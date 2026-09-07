"""Adversarial tests for http.py, gsc.py, auth.py, probe.py and keychain.py.

Every assertion is a behaviour the README or a docstring promises:

* keychain: "never placed on a command line where 'ps' could see them";
  store_secret "passed to 'security' through stdin in interactive mode";
  read_secret "raises KeychainError with a message naming the item, never
  its value"; item_exists "the password is never decrypted".
* http.request_json: "Refuses every redirect, so a credential can never be
  forwarded"; "Retries transient failures with exponential backoff,
  honouring Retry-After"; fetch_text "Never sends credentials", "Returns
  status 0 when the host could not be reached. Never raises on an HTTP
  error".
* gsc: "The read-only scope is requested on every refresh and verified in
  Google's response"; README: "verified to carry, exactly the
  webmasters.readonly scope"; inspect: "never retry a quota 429";
  safe_inspect: "A 429 is fatal for the run".
* auth: "installed-app OAuth with PKCE on a loopback redirect. Requests the
  read-only Search Console scope only"; _Callback "Receives exactly one
  redirect from Google", "Never log: the request line carries ?code=";
  README: "no secret ever printed".
* probe: README: "`forbidden` reads robots.txt, follows its Sitemap: lines"
  and cmd_forbidden prints "robots.txt BLOCKS Googlebot" only when it does.

No network, no Keychain, no real subprocess. Every transport is a fake
opener installed on cleanuprtx.http, subprocess.run is patched on
cleanuprtx.keychain, and the OAuth callback handler is driven with a fake
socket.

NOTE: unittest's discovery regex ([_a-z]\\w*\\.py) skips a module whose file
name contains a hyphen, so `python3 -m unittest discover -s tests -t .` will
not load this file. Run it explicitly:
    python3 -m unittest tests/test_adv_http-gsc-auth.py
"""

from __future__ import annotations

import base64
import contextlib
import email.message
import hashlib
import http.server
import io
import json
import socket
import ssl
import subprocess
import sys
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from cleanuprtx import auth, config, gsc, http as chttp, keychain
from cleanuprtx.auth import AuthError, _Callback
from cleanuprtx.http import HttpError, _classify_urlerror
from cleanuprtx.probe import parse_robots
from tests.helpers import SITE  # noqa: F401  (shared fixtures; no network, no Keychain)

READONLY = gsc.READONLY_SCOPE
WRITE = gsc.WRITE_SCOPE
SITEVERIFY = "https://www.googleapis.com/auth/siteverification"


# ---------------------------------------------------------------------------
# helpers: keychain
# ---------------------------------------------------------------------------

def security_tokenize(line: str):
    """Reference tokenizer for what `security -i` reads on stdin: words split
    on whitespace, double quotes group a word, and inside quotes a backslash
    escapes the next character (the two escapes _quote emits: \\\\ and \\")."""
    out, cur, have, inq, i = [], [], False, False, 0
    while i < len(line):
        c = line[i]
        if inq:
            if c == "\\" and i + 1 < len(line):
                cur.append(line[i + 1])
                i += 2
                continue
            if c == '"':
                inq = False
            else:
                cur.append(c)
        elif c.isspace():
            if have:
                out.append("".join(cur))
                cur, have = [], False
        elif c == '"':
            inq, have = True, True
        else:
            cur.append(c)
            have = True
        i += 1
    if have:
        out.append("".join(cur))
    return out


def fake_security(rc=0, stdout="", stderr="", echo_input=False, raise_exc=None):
    """A subprocess.run stand-in that records every call and never executes."""
    calls = []

    def run(cmd, input=None, capture_output=None, text=None, timeout=None):
        calls.append({"cmd": list(cmd), "input": input, "text": text, "timeout": timeout})
        if raise_exc is not None:
            raise raise_exc
        err = stderr + (input or "" if echo_input else "")
        return subprocess.CompletedProcess(list(cmd), rc, stdout=stdout, stderr=err)

    return calls, run


NASTY_SECRETS = [
    'quote"inside',
    'back\\slash\\\\double',
    '$HOME `id` $(whoami) ;rm -rf /',
    "xxxx xxxx xxxx xxxx xxxx xxxx",           # WordPress application password shape
    "GOCSPX-abc_DEF-123",                      # Google client secret shape
    "1//0gAbC-xYz_refresh",                    # Google refresh token shape
    "pässwörd – ünïcødé 屋根",
    "it's #not a comment | pipe > redirect",
    ' leading and trailing spaces ',
    '\\"tricky\\" \\\\ end\\',
]


# ---------------------------------------------------------------------------
# helpers: http
# ---------------------------------------------------------------------------

class FakeResp:
    """What urllib's opener returns, minus the socket."""

    def __init__(self, status=200, body=b"", content_type="application/json", charset=None,
                 url="https://api.example/x", read_error=None, extra_headers=None):
        self.status = status
        self._body = body
        self.url = url
        self._read_error = read_error
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type + (f"; charset={charset}" if charset else "")
        for k, v in (extra_headers or {}).items():
            self.headers[k] = v

    def read(self):
        if self._read_error is not None:
            raise self._read_error
        return self._body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Scripted stand-in for http._API_OPENER / http._BROWSER_OPENER."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def http_error(code, url="https://api.example/x", headers=None, body=b""):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError(url, code, f"status {code}", hdrs, io.BytesIO(body))


@contextlib.contextmanager
def api_transport(script):
    """Install a scripted opener for request_json and record every sleep."""
    opener = FakeOpener(script)
    sleeps = []
    with mock.patch.object(chttp, "_API_OPENER", opener), \
         mock.patch.object(chttp.time, "sleep", lambda s: sleeps.append(s)):
        yield opener, sleeps


@contextlib.contextmanager
def browser_transport(script):
    opener = FakeOpener(script)
    with mock.patch.object(chttp, "_BROWSER_OPENER", opener):
        yield opener


# ---------------------------------------------------------------------------
# helpers: auth (loopback callback driven with a fake socket)
# ---------------------------------------------------------------------------

class FakeSocket:
    """Enough of a socket for StreamRequestHandler: rfile from makefile(),
    wfile is a _SocketWriter that calls sendall()."""

    def __init__(self, request_bytes: bytes):
        self._in = io.BytesIO(request_bytes)
        self.out = bytearray()

    def makefile(self, mode, bufsize=-1):
        return self._in

    def sendall(self, data):
        self.out.extend(data)


def drive_callback(query: str) -> bytes:
    """Deliver GET /?<query> to _Callback exactly as a browser redirect would.
    Returns the raw HTTP response bytes."""
    sock = FakeSocket(f"GET /?{query} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode("ascii"))
    _Callback(sock, ("127.0.0.1", 50000), None)
    return bytes(sock.out)


def make_fake_server(port: int, redirect_query):
    """An http.server.HTTPServer stand-in whose handle_request() is 'the
    browser came back': it drives the real _Callback with redirect_query(state)."""

    class FakeServer:
        def __init__(self, address, handler):
            self.handler = handler
            self.server_address = (address[0], port)
            self.timeout = None
            self.closed = False

        def handle_request(self):
            drive_callback(redirect_query(self.handler.expected_state))

        def server_close(self):
            self.closed = True

    return FakeServer


def run_google_login(token_response, redirect_query=None, port=43111):
    """google_login with every side effect stubbed.

    Returns dict(result=<scope or AuthError>, exchanges=[form...], stored=[(service, value)],
    auth_url=<the URL printed for the user>, out=<everything printed>).
    """
    if redirect_query is None:
        redirect_query = lambda state: f"state={urllib.parse.quote(state)}&code=4%2Fthe-code&scope=x"
    secrets = {config.KC_GOOGLE_CLIENT_ID: "12345.apps.googleusercontent.com",
               config.KC_GOOGLE_CLIENT_SECRET: "GOCSPX-hush-hush"}
    exchanges, stored = [], []

    def fake_request_json(url, method="GET", headers=None, body=None, form=None, **kw):
        exchanges.append({"url": url, "method": method, "form": dict(form or {}), "kw": kw})
        return token_response

    out = io.StringIO()
    result = None
    with mock.patch.object(auth, "read_secret", lambda service, account=None: secrets[service]), \
         mock.patch.object(auth, "request_json", fake_request_json), \
         mock.patch.object(auth, "store_secret", lambda service, value, account=None: stored.append((service, value, account))), \
         mock.patch.object(http.server, "HTTPServer", make_fake_server(port, redirect_query)):
        try:
            result = auth.google_login(open_browser=False, out=out)
        except AuthError as exc:
            result = exc
    text = out.getvalue()
    auth_url = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith(gsc.AUTH_URL)), "")
    return {"result": result, "exchanges": exchanges, "stored": stored, "auth_url": auth_url, "out": text,
            "secret": secrets[config.KC_GOOGLE_CLIENT_SECRET]}


def query_params(url: str):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


# ===========================================================================
# keychain
# ===========================================================================

class TestKeychainStore(unittest.TestCase):
    def test_secret_never_in_argv_and_stdin_line_round_trips_every_nasty_value(self):
        """Docstring: 'passed to security through stdin in interactive mode, so it
        never appears in this process's argument list'. The stdin line must
        tokenize back to exactly the arguments add-generic-password expects."""
        for value in NASTY_SECRETS:
            calls, run = fake_security()
            with mock.patch.object(keychain.subprocess, "run", run):
                keychain.store_secret("cleanuprtx-wp-app-password-x", value, "richard@inspector-roofing.com")
            self.assertEqual(len(calls), 1, repr(value))
            call = calls[0]
            self.assertEqual(call["cmd"], [keychain.SECURITY, "-i"], repr(value))
            self.assertTrue(all(value not in arg for arg in call["cmd"]), "secret leaked into argv")
            self.assertTrue(call["text"], "stdin must be text so the secret is not re-encoded by us")
            self.assertTrue(call["input"].endswith("\n"), "interactive mode needs the line terminated")
            self.assertEqual(call["input"].count("\n"), 1, "exactly one command line")
            tokens = security_tokenize(call["input"][:-1])
            self.assertEqual(tokens, ["add-generic-password", "-U", "-s", "cleanuprtx-wp-app-password-x",
                                      "-a", "richard@inspector-roofing.com", "-w", value], repr(value))

    def test_store_without_account_omits_dash_a(self):
        calls, run = fake_security()
        with mock.patch.object(keychain.subprocess, "run", run):
            keychain.store_secret(config.KC_GOOGLE_REFRESH_TOKEN, "1//0refresh")
        tokens = security_tokenize(calls[0]["input"].rstrip("\n"))
        self.assertEqual(tokens, ["add-generic-password", "-U", "-s", config.KC_GOOGLE_REFRESH_TOKEN, "-w", "1//0refresh"])
        self.assertNotIn("-a", tokens)

    def test_store_refuses_empty_and_line_break_secrets_without_running_security(self):
        for bad in ("", "abc\ndef", "abc\rdef", "\n"):
            calls, run = fake_security()
            with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError, msg=repr(bad)):
                keychain.store_secret("svc", bad)
            self.assertEqual(calls, [], repr(bad))

    def test_store_failure_names_the_item_and_never_echoes_the_secret(self):
        """'Do not include stderr: in interactive mode it can echo the command.'"""
        value = "GOCSPX-super-secret-value"
        calls, run = fake_security(rc=1, stderr="security: SecKeychainItemCreateFromContent: ", echo_input=True)
        with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError) as cm:
            keychain.store_secret("cleanuprtx-google-client-secret", value)
        msg = str(cm.exception)
        self.assertIn("cleanuprtx-google-client-secret", msg)
        self.assertNotIn(value, msg)
        self.assertNotIn("SecKeychainItemCreateFromContent", msg, "stderr must not be forwarded")

    def test_timeout_becomes_a_keychain_error_about_unlocking(self):
        calls, run = fake_security(raise_exc=subprocess.TimeoutExpired([keychain.SECURITY, "-i"], 60))
        with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError) as cm:
            keychain.store_secret("svc", "value")
        self.assertIn("Unlock", str(cm.exception))
        self.assertNotIn("value", str(cm.exception))


class TestKeychainRead(unittest.TestCase):
    def test_read_uses_dash_w_and_account_and_returns_stripped_value(self):
        calls, run = fake_security(stdout="xxxx xxxx xxxx xxxx xxxx xxxx\n")
        with mock.patch.object(keychain.subprocess, "run", run):
            got = keychain.read_secret("svc", "acct")
        self.assertEqual(got, "xxxx xxxx xxxx xxxx xxxx xxxx", "internal spaces of an app password survive")
        self.assertEqual(calls[0]["cmd"], [keychain.SECURITY, "find-generic-password", "-s", "svc", "-a", "acct", "-w"])
        self.assertIsNone(calls[0]["input"])

    def test_missing_item_message_names_item_and_next_step_not_value(self):
        for rc, err in ((44, "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain."),
                        (1, "The specified item could not be found in the keychain.")):
            calls, run = fake_security(rc=rc, stderr=err)
            with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError) as cm:
                keychain.read_secret("cleanuprtx-wp-app-password-pnagolfcarts", "richard@inspector-roofing.com")
            msg = str(cm.exception)
            self.assertIn("cleanuprtx-wp-app-password-pnagolfcarts", msg)
            self.assertIn("richard@inspector-roofing.com", msg)
            self.assertIn("doctor", msg)

    def test_locked_or_denied_is_distinguished_from_missing(self):
        calls, run = fake_security(rc=51, stderr="security: SecKeychainItemCopyContent: User interaction is not allowed.")
        with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError) as cm:
            keychain.read_secret("svc")
        self.assertIn("locked", str(cm.exception).lower())
        self.assertNotIn("auth", str(cm.exception).split("Unlock")[0].lower().replace("cleanuprtx", ""),
                         "a locked keychain must not be reported as a missing item")

    def test_present_but_empty_is_an_error(self):
        calls, run = fake_security(rc=0, stdout="\n")
        with mock.patch.object(keychain.subprocess, "run", run), self.assertRaises(keychain.KeychainError):
            keychain.read_secret("svc")

    def test_item_exists_never_asks_for_the_password(self):
        """'Reads attributes only; the password is never decrypted'."""
        for account in (None, "acct"):
            calls, run = fake_security(rc=0)
            with mock.patch.object(keychain.subprocess, "run", run):
                self.assertTrue(keychain.item_exists("svc", account))
            self.assertNotIn("-w", calls[0]["cmd"])
            self.assertNotIn("-g", calls[0]["cmd"])
        calls, run = fake_security(rc=44)
        with mock.patch.object(keychain.subprocess, "run", run):
            self.assertFalse(keychain.item_exists("svc"))


# ===========================================================================
# http
# ===========================================================================

class TestRequestJsonRedirects(unittest.TestCase):
    def test_redirect_handler_never_builds_a_follow_up_request(self):
        """_NoRedirect: 'Following one would forward the Authorization header'."""
        handler = chttp._NoRedirect()
        req = urllib.request.Request("https://inspector-roofing.com/wp-json/wp/v2/users/me",
                                     headers={"Authorization": "Basic abc"})
        hdrs = email.message.Message()
        hdrs["Location"] = "https://evil.example/wp-json/wp/v2/users/me"
        for code in (301, 302, 303, 307, 308):
            with self.assertRaises(urllib.error.HTTPError, msg=code) as cm:
                handler.redirect_request(req, io.BytesIO(b""), code, "moved", hdrs, hdrs["Location"])
            self.assertEqual(cm.exception.code, code)

    def test_request_json_refuses_redirect_without_retry_and_names_the_target(self):
        for code in (301, 302, 307, 308):
            with api_transport([http_error(code, headers={"Location": "https://www.inspector-roofing.com/wp-json/"})]) as (opener, sleeps):
                with self.assertRaises(HttpError) as cm:
                    chttp.request_json("https://inspector-roofing.com/wp-json/wp/v2/pages",
                                       headers={"Authorization": "Basic abc"})
            self.assertEqual(cm.exception.status, code)
            self.assertIn("www.inspector-roofing.com", str(cm.exception))
            self.assertIn("refusing", str(cm.exception))
            self.assertEqual(len(opener.requests), 1, "a redirect must not be retried")
            self.assertEqual(sleeps, [])
            self.assertEqual(opener.requests[0].full_url, "https://inspector-roofing.com/wp-json/wp/v2/pages",
                             "the credential went only to the host the caller named")

    def test_redirect_without_location_header_is_still_refused(self):
        with api_transport([http_error(302)]) as (opener, sleeps):
            with self.assertRaises(HttpError) as cm:
                chttp.request_json("https://x.example/api")
        self.assertEqual(cm.exception.status, 302)
        self.assertIn("refusing", str(cm.exception))
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(sleeps, [])


class TestRequestJsonRetries(unittest.TestCase):
    def test_retry_after_is_honoured_and_capped_then_succeeds(self):
        """Docstring: 'exponential backoff, honouring Retry-After'; MAX_RETRY_AFTER = 60."""
        script = [http_error(503, headers={"Retry-After": "600"}),
                  http_error(429, headers={"Retry-After": "7"}),
                  FakeResp(body=b'{"ok": true}')]
        with api_transport(script) as (opener, sleeps):
            got = chttp.request_json("https://x.example/api", retries=3)
        self.assertEqual(got, {"ok": True})
        self.assertEqual(sleeps, [60.0, 7.0])
        self.assertEqual(len(opener.requests), 3)

    def test_unparseable_retry_after_falls_back_to_exponential_backoff(self):
        script = [http_error(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                  http_error(503), FakeResp(body=b"[]")]
        with api_transport(script) as (opener, sleeps):
            self.assertEqual(chttp.request_json("https://x.example/api"), [])
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_status_outside_retry_status_is_raised_immediately(self):
        """gsc.inspect relies on this: 'never retry a quota 429'."""
        with api_transport([http_error(429, body=b'{"error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}}')]) as (opener, sleeps):
            with self.assertRaises(HttpError) as cm:
                chttp.request_json("https://x.example/api", retry_status={500, 502, 503, 504})
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.code, "RESOURCE_EXHAUSTED")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(sleeps, [])

    def test_empty_retry_status_means_no_retry_even_on_503(self):
        with api_transport([http_error(503)]) as (opener, sleeps):
            with self.assertRaises(HttpError):
                chttp.request_json("https://x.example/api", retry_status=set())
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(sleeps, [])

    def test_retries_exhausted_reports_last_status_and_body(self):
        # Three distinct error objects: urllib reads each response body once.
        with api_transport([http_error(502, body=b"bad gateway") for _ in range(3)]) as (opener, sleeps):
            with self.assertRaises(HttpError) as cm:
                chttp.request_json("https://x.example/api", retries=2)
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("bad gateway", cm.exception.body)
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_timeouts_are_retried_then_reported_as_status_zero(self):
        script = [urllib.error.URLError(socket.timeout("timed out")), TimeoutError("read timed out"),
                  urllib.error.URLError(TimeoutError())]
        with api_transport(script) as (opener, sleeps):
            with self.assertRaises(HttpError) as cm:
                chttp.request_json("https://x.example/api", retries=2)
        self.assertEqual(cm.exception.status, 0)
        self.assertIn("timed out", str(cm.exception))
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(sleeps, [1, 2])

    def test_dns_and_certificate_failures_are_not_retried(self):
        for reason, expect in ((socket.gaierror(8, "nodename nor servname provided"), "DNS lookup failed"),
                               (ssl.SSLCertVerificationError(1, "certificate verify failed"), "Install Certificates.command")):
            with api_transport([urllib.error.URLError(reason)]) as (opener, sleeps):
                with self.assertRaises(HttpError) as cm:
                    chttp.request_json("https://x.example/api")
            self.assertEqual(cm.exception.status, 0)
            self.assertIn(expect, str(cm.exception))
            self.assertEqual(len(opener.requests), 1, repr(reason))
            self.assertEqual(sleeps, [], repr(reason))

    def test_classify_urlerror_branches(self):
        cases = [
            (ssl.SSLCertVerificationError(1, "certificate verify failed"), chttp.CERT_HELP, False),
            (ssl.SSLError(1, "wrong version number"), "TLS error", False),
            (ssl.SSLEOFError(8, "EOF occurred in violation of protocol"), "TLS error", False),
            (socket.timeout("t"), "timed out", True),
            (TimeoutError(), "timed out", True),
            (socket.gaierror(-2, "Name or service not known"), "DNS lookup failed", False),
            (ConnectionRefusedError(111, "Connection refused"), "Connection refused", True),
            ("plain string reason", "plain string reason", True),
        ]
        for reason, expect_text, expect_retry in cases:
            message, retryable = _classify_urlerror(urllib.error.URLError(reason))
            self.assertIn(expect_text, message, repr(reason))
            self.assertEqual(retryable, expect_retry, repr(reason))


class TestRequestJsonBodies(unittest.TestCase):
    def test_non_json_200_is_an_http_error_naming_the_content_type(self):
        """A WAF challenge page or a login form answered 200 must not be mistaken for data."""
        with api_transport([FakeResp(body=b"<html>Just a moment...</html>", content_type="text/html")]) as (opener, sleeps):
            with self.assertRaises(HttpError) as cm:
                chttp.request_json("https://x.example/wp-json/")
        self.assertEqual(cm.exception.status, 200)
        self.assertIn("text/html", str(cm.exception))
        self.assertEqual(sleeps, [], "a bad body is not a transient failure")

    def test_empty_body_is_none_and_headers_are_lowercased(self):
        with api_transport([FakeResp(body=b"  \n", extra_headers={"X-WP-Total": "7"})]) as (opener, sleeps):
            data, headers = chttp.request_json_with_headers("https://x.example/api")
        self.assertIsNone(data)
        self.assertEqual(headers["x-wp-total"], "7")

    def test_form_and_json_bodies_set_content_type_and_default_headers(self):
        with api_transport([FakeResp(body=b"{}"), FakeResp(body=b"{}")]) as (opener, sleeps):
            chttp.request_json("https://x.example/token", method="POST", form={"a": "b c", "d": "e&f"})
            chttp.request_json("https://x.example/api", method="POST", body={"content": "x"},
                               headers={"Authorization": "Bearer t"})
        form_req, json_req = opener.requests
        self.assertEqual(form_req.data, b"a=b+c&d=e%26f")
        self.assertEqual(form_req.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(form_req.get_method(), "POST")
        self.assertEqual(json.loads(json_req.data), {"content": "x"})
        self.assertEqual(json_req.get_header("Content-type"), "application/json")
        self.assertEqual(json_req.get_header("Authorization"), "Bearer t")
        self.assertEqual(json_req.get_header("User-agent"), chttp.USER_AGENT)


class TestFetchText(unittest.TestCase):
    def test_never_sends_credentials_and_uses_the_requested_user_agent(self):
        """'Never carries an Authorization header.'"""
        with browser_transport([FakeResp(body=b"<html></html>", content_type="text/html")]) as opener:
            got = chttp.fetch_text("https://pnagolfcarts.com/", user_agent="Googlebot-Image/1.0")
        req = opener.requests[0]
        self.assertFalse(req.has_header("Authorization"))
        self.assertNotIn("authorization", {k.lower() for k in req.headers})
        self.assertEqual(req.get_header("User-agent"), "Googlebot-Image/1.0")
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(got.ok)
        self.assertEqual(got.text, "<html></html>")

    def test_unknown_charset_falls_back_to_utf8_then_latin1(self):
        cases = [
            ("x-bogus-charset", "café — ünïcødé".encode("utf-8"), "café — ünïcødé"),
            (None, "caf\xe9".encode("latin-1"), "caf\xe9"),
            ("utf-8", b"caf\xe9 broken utf8", "caf\xe9 broken utf8"),
            ("utf-8", "屋根".encode("utf-8"), "屋根"),
        ]
        for charset, body, expect in cases:
            with browser_transport([FakeResp(body=body, content_type="text/html", charset=charset)]):
                got = chttp.fetch_text("https://x.example/")
            self.assertEqual(got.status, 200, repr(charset))
            self.assertEqual(got.text, expect, repr(charset))

    def test_http_error_is_a_result_with_headers_and_body_not_an_exception(self):
        """'a 403 or 404 is itself a result the caller wants' - and the WAF headers
        the README says forbidden prints (server / cf-ray) must survive."""
        err = http_error(403, url="https://pnagolfcarts.com/", body=b"<html>blocked</html>",
                         headers={"Server": "cloudflare", "CF-RAY": "8a1b-IAD", "CF-Mitigated": "challenge"})
        with browser_transport([err]):
            got = chttp.fetch_text("https://pnagolfcarts.com/", user_agent="Googlebot/2.1")
        self.assertEqual(got.status, 403)
        self.assertFalse(got.ok)
        self.assertEqual(got.final_url, "https://pnagolfcarts.com/")
        self.assertEqual(got.headers["server"], "cloudflare")
        self.assertEqual(got.headers["cf-ray"], "8a1b-IAD")
        self.assertEqual(got.headers["cf-mitigated"], "challenge")
        self.assertIn("blocked", got.text)

    def test_http_error_without_body_or_headers_is_safe(self):
        err = urllib.error.HTTPError("https://x.example/", 404, "Not Found", None, None)
        with browser_transport([err]):
            got = chttp.fetch_text("https://x.example/")
        self.assertEqual(got.status, 404)
        self.assertEqual(got.headers, {})
        self.assertEqual(got.text, "")

    def test_unreachable_host_is_status_zero_not_an_exception(self):
        for exc in (urllib.error.URLError(socket.gaierror(8, "x")), urllib.error.URLError(ConnectionRefusedError()),
                    socket.timeout("t"), TimeoutError(), ValueError("unknown url type")):
            with browser_transport([exc]):
                got = chttp.fetch_text("https://x.example/")
            self.assertEqual(got.status, 0, repr(exc))
            self.assertEqual(got.final_url, "https://x.example/")

    def test_connection_reset_while_reading_the_body_is_status_zero_not_a_crash(self):
        """'Returns status 0 when the host could not be reached. Never raises on an
        HTTP error.' A WAF that accepts the connection and then drops it mid-body
        (the pattern Cloudflare/Wordfence show a fake Googlebot) must be a result
        row in `forbidden`, not a traceback that ends the whole probe."""
        for exc in (ConnectionResetError(104, "Connection reset by peer"),
                    __import__("http.client").client.IncompleteRead(b"<html>")):
            with browser_transport([FakeResp(body=b"", content_type="text/html", read_error=exc)]):
                try:
                    got = chttp.fetch_text("https://pnagolfcarts.com/")
                except Exception as raised:  # noqa: BLE001 - the point of the test
                    self.fail(f"fetch_text raised {raised!r} for {exc!r}")
            self.assertEqual(got.status, 0, repr(exc))

    def test_follows_redirects_and_reports_final_url(self):
        resp = FakeResp(body=b"<html></html>", content_type="text/html", url="https://www.pnagolfcarts.com/")
        with browser_transport([resp]):
            got = chttp.fetch_text("https://pnagolfcarts.com/")
        self.assertEqual(got.url, "https://pnagolfcarts.com/")
        self.assertEqual(got.final_url, "https://www.pnagolfcarts.com/")


# ===========================================================================
# gsc
# ===========================================================================

def token_client(payload, seen=None):
    """A SearchConsoleClient whose token endpoint answers `payload`."""
    c = gsc.SearchConsoleClient()

    def fake(url, method="GET", form=None, **kw):
        if seen is not None:
            seen.append({"url": url, "method": method, "form": dict(form or {}), "kw": kw})
        return payload

    return c, fake


class TestScopeVerification(unittest.TestCase):
    def _mint(self, payload):
        seen = []
        c, fake = token_client(payload, seen)
        with mock.patch.object(gsc, "read_secret", lambda *a, **k: "s"), mock.patch.object(gsc, "request_json", fake):
            return c._access_token(), seen

    def test_missing_scope_field_does_not_raise(self):
        """Google may omit 'scope' from a refresh response; that is not a scope violation."""
        token, seen = self._mint({"access_token": "ya29.x", "expires_in": 3599, "token_type": "Bearer"})
        self.assertEqual(token, "ya29.x")
        self.assertEqual(seen[0]["form"]["scope"], READONLY, "read-only scope requested on every refresh")
        self.assertEqual(seen[0]["form"]["grant_type"], "refresh_token")
        self.assertEqual(seen[0]["kw"].get("retry_status"), set(), "token endpoint errors are never retried")

    def test_write_scope_in_any_position_or_alongside_readonly_is_refused(self):
        for scope in (WRITE, f"{WRITE} {READONLY}", f"{READONLY} {WRITE}", f"openid {WRITE}"):
            with self.assertRaises(gsc.ScopeError, msg=repr(scope)) as cm:
                self._mint({"access_token": "t", "scope": scope})
            self.assertIn("auth google", str(cm.exception))
            self.assertEqual(cm.exception.status, 0)

    def test_scope_field_present_but_readonly_absent_is_refused(self):
        with self.assertRaises(gsc.ScopeError):
            self._mint({"access_token": "t", "scope": "openid email"})

    def test_readonly_plus_an_unrelated_write_capable_scope_is_refused(self):
        """README: 'verified to carry, exactly the webmasters.readonly scope'; the
        ScopeError text itself says 'requires exactly'. siteverification lets a
        token add and remove Search Console owners - that is not read-only."""
        with self.assertRaises(gsc.ScopeError):
            self._mint({"access_token": "t", "scope": f"{READONLY} {SITEVERIFY}"})

    def test_access_token_is_cached_in_memory_for_the_run(self):
        seen = []
        c, fake = token_client({"access_token": "t", "expires_in": 3600, "scope": READONLY}, seen)
        with mock.patch.object(gsc, "read_secret", lambda *a, **k: "s"), mock.patch.object(gsc, "request_json", fake):
            self.assertEqual(c._access_token(), "t")
            self.assertEqual(c._headers(), {"Authorization": "Bearer t"})
        self.assertEqual(len(seen), 1)


class TestParseInspection(unittest.TestCase):
    def test_sparse_and_extra_fields_are_tolerated(self):
        data = {"inspectionResult": {
            "indexStatusResult": {"verdict": "NEUTRAL", "coverageState": "Discovered - currently not indexed"},
            "richResultsResult": {"verdict": "PASS", "detectedItems": [
                {"richResultType": "Breadcrumbs"},                       # no items
                {"richResultType": "Image metadata", "items": [
                    {"name": "ImageObject"},                              # no issues
                    {"issues": [{}]},                                     # issue with no fields
                    {"name": "ImageObject", "issues": None},
                ]},
                {"items": [{"name": "X", "issues": [{"issueMessage": 'Invalid object type for field "creator"', "severity": "WARNING"}]}]},
            ]},
            "ampResult": {"verdict": "NEUTRAL"},
            "mobileUsabilityResult": {"verdict": "VERDICT_UNSPECIFIED"},
            "futureField": {"anything": [1, 2]},
        }}
        insp = gsc.parse_inspection("https://inspector-roofing.com/richard-nasser/", data)
        self.assertFalse(insp.is_indexed)
        self.assertEqual(insp.coverage_state, "Discovered - currently not indexed")
        self.assertEqual(insp.page_fetch_state, "")
        self.assertFalse(insp.fetch_blocked)
        self.assertEqual(insp.rich_results_verdict, "PASS")
        self.assertEqual(len(insp.rich_result_issues), 2)
        blank, creator = insp.rich_result_issues
        self.assertEqual((blank.rich_result_type, blank.item_name, blank.message, blank.severity),
                         ("Image metadata", "", "unspecified", "UNKNOWN"))
        self.assertEqual(creator.rich_result_type, "unknown")
        self.assertIn('"creator"', str(creator))
        self.assertEqual(insp.error, "")

    def test_null_and_missing_containers(self):
        for data in ({}, {"inspectionResult": None}, {"inspectionResult": {"indexStatusResult": None, "richResultsResult": None}},
                     {"inspectionResult": {"richResultsResult": {"detectedItems": None}}}):
            insp = gsc.parse_inspection("u", data)
            self.assertEqual(insp.url, "u")
            self.assertEqual(insp.rich_result_issues, [], repr(data))
            self.assertFalse(insp.fetch_blocked)

    def test_every_blocked_fetch_state_is_recognised(self):
        for state in sorted(gsc.BLOCKED_FETCH_STATES):
            insp = gsc.parse_inspection("u", {"inspectionResult": {"indexStatusResult": {"pageFetchState": state}}})
            self.assertTrue(insp.fetch_blocked, state)
        ok = gsc.parse_inspection("u", {"inspectionResult": {"indexStatusResult": {"pageFetchState": "SUCCESSFUL"}}})
        self.assertFalse(ok.fetch_blocked)


class TestInspect(unittest.TestCase):
    def _client(self):
        c = gsc.SearchConsoleClient()
        c._token, c._expires_at = "t", 9e12
        return c

    def test_inspect_never_retries_429_and_sends_the_documented_body(self):
        seen = []

        def fake(url, method="GET", headers=None, body=None, **kw):
            seen.append({"url": url, "method": method, "headers": headers, "body": body, "kw": kw})
            return {"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}}

        with mock.patch.object(gsc, "request_json", fake), mock.patch.object(gsc.time, "sleep", lambda s: None):
            insp = self._client().inspect("https://pnagolfcarts.com/", "sc-domain:pnagolfcarts.com")
        self.assertTrue(insp.is_indexed)
        call = seen[0]
        self.assertEqual(call["url"], gsc.INSPECT_URL)
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["body"], {"inspectionUrl": "https://pnagolfcarts.com/", "siteUrl": "sc-domain:pnagolfcarts.com",
                                        "languageCode": "en-US"})
        self.assertEqual(call["headers"], {"Authorization": "Bearer t"})
        retry = call["kw"].get("retry_status")
        self.assertIsNotNone(retry, "inspect must override the default retry set")
        self.assertNotIn(429, retry, "'never retry a quota 429'")

    def test_safe_inspect_classifies_every_failure_without_raising(self):
        cases = [
            (HttpError(429, "u", json.dumps({"error": {"code": 429, "message": "Quota exceeded for quota metric 'URL inspection'"}})),
             "QUOTA", "Quota exceeded"),
            (HttpError(429, "u", json.dumps({"error": "rateLimitExceeded"})), "QUOTA", "rateLimitExceeded"),
            (HttpError(429, "u", "<html>429 Too Many Requests</html>"), "QUOTA", "429 Too Many"),
            (HttpError(403, "u", json.dumps({"error": {"message": "User does not have sufficient permission for site"}})),
             "PERMISSION", "sc-domain:pnagolfcarts.com"),
            (HttpError(400, "u", json.dumps({"error": {"message": "URL is not part of the property"}})),
             "REQUEST: URL is not part of the property", ""),
            (HttpError(500, "u", "backend error"), "HTTP 500", "backend error"),
            (HttpError(0, "u", "timed out"), "could not reach", "timed out"),
            (gsc.ScopeError("bad scope"), "could not reach", "bad scope"),
        ]
        for exc, prefix, needle in cases:
            def fake(*a, _exc=exc, **k):
                raise _exc
            with mock.patch.object(gsc, "request_json", fake), mock.patch.object(gsc.time, "sleep", lambda s: None):
                insp = self._client().safe_inspect("https://pnagolfcarts.com/", "sc-domain:pnagolfcarts.com")
            self.assertEqual(insp.url, "https://pnagolfcarts.com/")
            self.assertTrue(insp.error.startswith(prefix), (exc.status, insp.error))
            self.assertIn(needle, insp.error, (exc.status, insp.error))
            self.assertFalse(insp.is_indexed)
            self.assertFalse(insp.fetch_blocked)


# ===========================================================================
# auth
# ===========================================================================

class TestCallbackHandler(unittest.TestCase):
    def setUp(self):
        _Callback.expected_state = "expected-state-123"
        _Callback.received = {}

    def tearDown(self):
        _Callback.expected_state = ""
        _Callback.received = {}

    def _quiet(self, query):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            raw = drive_callback(query)
        return raw, err.getvalue()

    def test_state_mismatch_is_400_and_the_code_is_not_accepted(self):
        for query in ("state=forged&code=4%2Fstolen", "code=4%2Fno-state-at-all", "state=&code=x",
                      "state=expected-state-1234&code=x", "state=forged&error=access_denied"):
            _Callback.received = {}
            raw, logged = self._quiet(query)
            self.assertTrue(raw.startswith(b"HTTP/1.0 400"), (query, raw[:40]))
            self.assertEqual(_Callback.received, {}, query)
            self.assertIn(b"State mismatch", raw)
            self.assertEqual(logged, "", "the request line carries ?code= and must never be logged")

    def test_error_param_with_valid_state_is_recorded_as_error_not_code(self):
        raw, logged = self._quiet("state=expected-state-123&error=access_denied&code=4%2Fshould-be-ignored")
        self.assertTrue(raw.startswith(b"HTTP/1.0 200"))
        self.assertEqual(_Callback.received, {"error": "access_denied"})
        self.assertIn(b"access_denied", raw)
        self.assertEqual(logged, "")

    def test_valid_state_and_code_are_recorded_and_nothing_is_logged(self):
        raw, logged = self._quiet("state=expected-state-123&code=4%2F0AbCdEf-gH&scope=" + urllib.parse.quote(READONLY))
        self.assertTrue(raw.startswith(b"HTTP/1.0 200"))
        self.assertEqual(_Callback.received, {"code": "4/0AbCdEf-gH"})
        self.assertIn(b"Content-Type: text/html; charset=utf-8", raw)
        self.assertIn(b"connected to Search Console", raw)
        self.assertNotIn(b"4/0AbCdEf-gH", raw, "the code must not be reflected into the page")
        self.assertEqual(logged, "")

    def test_repeated_state_parameter_uses_the_first_value_only(self):
        raw, _ = self._quiet("state=forged&state=expected-state-123&code=x")
        self.assertTrue(raw.startswith(b"HTTP/1.0 400"))
        self.assertEqual(_Callback.received, {})


class TestGoogleLogin(unittest.TestCase):
    def tearDown(self):
        _Callback.expected_state = ""
        _Callback.received = {}

    def test_readonly_grant_is_stored_and_the_flow_is_pkce_on_loopback(self):
        run = run_google_login({"access_token": "ya29.a", "refresh_token": "1//0refresh-XYZ", "scope": READONLY,
                                "token_type": "Bearer", "expires_in": 3599})
        self.assertEqual(run["result"], READONLY)
        self.assertEqual(run["stored"], [(config.KC_GOOGLE_REFRESH_TOKEN, "1//0refresh-XYZ", None)])
        # Authorization URL: read-only scope only, offline, PKCE S256, loopback redirect.
        self.assertTrue(run["auth_url"], run["out"])
        params = query_params(run["auth_url"])
        self.assertEqual(params["scope"], READONLY, "Requests the read-only Search Console scope only")
        self.assertEqual(params["response_type"], "code")
        self.assertEqual(params["access_type"], "offline")
        self.assertEqual(params["prompt"], "consent")
        self.assertEqual(params["code_challenge_method"], "S256")
        self.assertEqual(params["redirect_uri"], "http://127.0.0.1:43111/")
        self.assertEqual(params["client_id"], "12345.apps.googleusercontent.com")
        self.assertGreaterEqual(len(params["state"]), 16)
        # Token exchange: same redirect_uri, the delivered code, a verifier that hashes to the challenge.
        self.assertEqual(len(run["exchanges"]), 1)
        ex = run["exchanges"][0]
        self.assertEqual(ex["url"], gsc.TOKEN_URL)
        self.assertEqual(ex["method"], "POST")
        form = ex["form"]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code"], "4/the-code")
        self.assertEqual(form["redirect_uri"], params["redirect_uri"])
        self.assertEqual(form["client_secret"], run["secret"], "the secret travels only in the POST form")
        digest = hashlib.sha256(form["code_verifier"].encode("ascii")).digest()
        self.assertEqual(base64.urlsafe_b64encode(digest).rstrip(b"=").decode(), params["code_challenge"])
        self.assertEqual(ex["kw"].get("retry_status"), set())
        # README: no secret ever printed.
        self.assertNotIn(run["secret"], run["out"])
        self.assertNotIn("1//0refresh-XYZ", run["out"])
        self.assertNotIn("4/the-code", run["out"])

    def test_write_scope_grant_is_refused_and_not_stored(self):
        for scope in (WRITE, f"{READONLY} {WRITE}", f"{WRITE} {READONLY}", f"openid {WRITE} email"):
            run = run_google_login({"access_token": "t", "refresh_token": "1//0r", "scope": scope})
            self.assertIsInstance(run["result"], AuthError, repr(scope))
            self.assertIn("Not stored", str(run["result"]), repr(scope))
            self.assertEqual(run["stored"], [], repr(scope))

    def test_readonly_plus_unrelated_write_capable_scope_is_refused(self):
        """AuthError text: 'expected only {READONLY_SCOPE}'; README: 'exactly the
        webmasters.readonly scope'. A grant that also carries siteverification
        is not read-only and must not be stored."""
        run = run_google_login({"access_token": "t", "refresh_token": "1//0r", "scope": f"{READONLY} {SITEVERIFY}"})
        self.assertIsInstance(run["result"], AuthError, run["result"])
        self.assertEqual(run["stored"], [])

    def test_missing_refresh_token_is_explained_and_nothing_is_stored(self):
        run = run_google_login({"access_token": "t", "scope": READONLY})
        self.assertIsInstance(run["result"], AuthError)
        self.assertIn("myaccount.google.com/permissions", str(run["result"]))
        self.assertEqual(run["stored"], [])

    def test_user_denied_consent_aborts_before_any_token_exchange(self):
        run = run_google_login({"access_token": "t", "refresh_token": "r", "scope": READONLY},
                               redirect_query=lambda state: f"state={urllib.parse.quote(state)}&error=access_denied")
        self.assertIsInstance(run["result"], AuthError)
        self.assertIn("access_denied", str(run["result"]))
        self.assertEqual(run["exchanges"], [], "no code, no exchange")
        self.assertEqual(run["stored"], [])

    def test_forged_redirect_with_wrong_state_never_reaches_the_token_endpoint(self):
        run = run_google_login({"access_token": "t", "refresh_token": "r", "scope": READONLY},
                               redirect_query=lambda state: "state=forged&code=4%2Fattacker")
        self.assertIsInstance(run["result"], AuthError)
        self.assertEqual(run["exchanges"], [], "a code delivered with the wrong state must never be exchanged")
        self.assertEqual(run["stored"], [])


class TestCredentialPrompts(unittest.TestCase):
    def test_google_client_id_is_validated_before_anything_is_stored(self):
        stored = []
        answers = iter(["not-a-google-client-id", "GOCSPX-secret"])
        with mock.patch.object(auth.getpass, "getpass", lambda prompt="": next(answers)), \
             mock.patch.object(auth, "store_secret", lambda *a, **k: stored.append(a)), \
             self.assertRaises(AuthError):
            auth.store_google_client(out=io.StringIO())
        self.assertEqual(stored, [], "neither half of the pair may be stored when the ID is wrong")

    def test_google_client_pair_is_stored_under_the_documented_items(self):
        stored = []
        answers = iter(["  12345-abc.apps.googleusercontent.com \n", " GOCSPX-secret "])
        with mock.patch.object(auth.getpass, "getpass", lambda prompt="": next(answers)), \
             mock.patch.object(auth, "store_secret", lambda service, value, account=None: stored.append((service, value, account))):
            auth.store_google_client(out=io.StringIO())
        self.assertEqual(stored, [(config.KC_GOOGLE_CLIENT_ID, "12345-abc.apps.googleusercontent.com", None),
                                  (config.KC_GOOGLE_CLIENT_SECRET, "GOCSPX-secret", None)])

    def test_wordpress_password_too_short_is_refused_and_valid_one_is_stored_per_site(self):
        site = config.SITES["pnagolfcarts"]
        stored = []
        with mock.patch.object(auth.getpass, "getpass", lambda prompt="": "abcd efgh ijkl"), \
             mock.patch.object(auth, "store_secret", lambda *a, **k: stored.append(a)), \
             self.assertRaises(AuthError):
            auth.store_wordpress_password(site, out=io.StringIO())
        self.assertEqual(stored, [])
        out = io.StringIO()
        with mock.patch.object(auth.getpass, "getpass", lambda prompt="": "AbCd EfGh IjKl MnOp QrSt UvWx"), \
             mock.patch.object(auth, "store_secret", lambda service, value, account=None: stored.append((service, value, account))):
            auth.store_wordpress_password(site, out=out)
        self.assertEqual(stored, [("cleanuprtx-wp-app-password-pnagolfcarts", "AbCd EfGh IjKl MnOp QrSt UvWx",
                                   "richard@inspector-roofing.com")])
        self.assertNotIn("AbCd EfGh", out.getvalue(), "the password is never printed")


# ===========================================================================
# probe.parse_robots
# ===========================================================================

class TestParseRobots(unittest.TestCase):
    def test_crlf_comments_mixed_case_and_no_space_after_colon(self):
        text = ("# generated by a Windows host\r\n"
                "User-Agent: *\r\n"
                "Disallow: /wp-admin/   # keep admin private\r\n"
                "Allow: /wp-admin/admin-ajax.php\r\n"
                "\r\n"
                "user-agent: GOOGLEBOT\r\n"
                "DISALLOW:/  # blocked while staging\r\n"
                "\r\n"
                "SITEMAP: https://pnagolfcarts.com/sitemap_index.xml   \r\n"
                "sitemap: https://pnagolfcarts.com/wp-sitemap.xml # second\r\n")
        info = parse_robots(text)
        self.assertEqual(info.sitemaps, ["https://pnagolfcarts.com/sitemap_index.xml", "https://pnagolfcarts.com/wp-sitemap.xml"])
        self.assertTrue(info.googlebot_disallow_all)
        self.assertEqual(len(info.blocks), 1)
        self.assertIn("GOOGLEBOT", info.blocks[0])
        self.assertNotIn("\r", info.blocks[0])
        self.assertNotIn("#", info.blocks[0], "comments are stripped from the shown block")

    def test_multi_agent_group_and_partial_disallow_are_handled(self):
        info = parse_robots("User-agent: Bingbot\nUser-agent: Googlebot\nDisallow: /\n")
        self.assertTrue(info.googlebot_disallow_all)
        benign = parse_robots("User-agent: *\nDisallow: /wp-admin/\nDisallow: /private/\nDisallow: /\t\n")
        self.assertTrue(benign.googlebot_disallow_all, "'Disallow: /' followed by a tab is still a full block")
        self.assertFalse(parse_robots("User-agent: *\nDisallow: /x/\nDisallow: /y\n").googlebot_disallow_all)
        self.assertFalse(parse_robots("User-agent: Googlebot\nDisallow:\n").googlebot_disallow_all, "empty Disallow allows all")

    def test_googlebot_image_only_block_is_shown_but_is_not_a_googlebot_block(self):
        info = parse_robots("User-agent: Googlebot-Image\nDisallow: /\n\nUser-agent: *\nDisallow: /wp-admin/\n")
        self.assertFalse(info.googlebot_disallow_all)
        self.assertEqual(len(info.blocks), 1)
        self.assertIn("Googlebot-Image", info.blocks[0])

    def test_empty_and_non_robots_text(self):
        self.assertEqual(parse_robots("").sitemaps, [])
        self.assertFalse(parse_robots("<html>404</html>").googlebot_disallow_all)
        self.assertFalse(parse_robots(None).googlebot_disallow_all)  # type: ignore[arg-type]

    def test_specific_googlebot_group_overrides_the_wildcard_group(self):
        """RFC 9309 / Google: a crawler obeys only the most specific group that
        names it. With 'User-agent: Googlebot / Allow: /' present, Googlebot is
        NOT blocked, so cmd_forbidden must not print 'robots.txt BLOCKS Googlebot'
        and send the owner after a rule Google never applies."""
        text = ("User-agent: *\nDisallow: /\n\n"
                "User-agent: Googlebot\nAllow: /\n\n"
                "Sitemap: https://positive-outcomes.com/sitemap.xml\n")
        info = parse_robots(text)
        self.assertEqual(info.sitemaps, ["https://positive-outcomes.com/sitemap.xml"])
        self.assertFalse(info.googlebot_disallow_all, info.blocks)
        # The mirror image really is a Googlebot block.
        self.assertTrue(parse_robots("User-agent: *\nAllow: /\n\nUser-agent: Googlebot\nDisallow: /\n").googlebot_disallow_all)

    def test_utf8_bom_does_not_hide_the_first_group(self):
        """A robots.txt saved by a Windows editor starts with U+FEFF. Google's
        parser ignores the BOM and applies the group; so must the diagnosis."""
        info = parse_robots("﻿User-agent: *\r\nDisallow: /\r\nSitemap: https://x/s.xml\r\n")
        self.assertTrue(info.googlebot_disallow_all)
        self.assertEqual(info.sitemaps, ["https://x/s.xml"])


if __name__ == "__main__":
    unittest.main()
