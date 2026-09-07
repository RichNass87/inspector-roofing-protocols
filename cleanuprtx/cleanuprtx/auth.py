"""Credential setup.

Both flows put the secret in the Keychain and nowhere else.

* google   - installed-app OAuth with PKCE on a loopback redirect. Requests
             the read-only Search Console scope only.
* wordpress - prompts for an application password without echo.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from typing import Dict, Optional

from . import config
from .gsc import AUTH_URL, READONLY_SCOPE, TOKEN_URL
from .http import HttpError, request_json
from .keychain import KeychainError, read_secret, store_secret

LOOPBACK_TIMEOUT = 300  # seconds to wait for the browser round trip


class AuthError(RuntimeError):
    pass


def _pkce_pair() -> Dict[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return {"verifier": verifier, "challenge": challenge}


class _Callback(http.server.BaseHTTPRequestHandler):
    """Receives exactly one redirect from Google."""

    expected_state = ""
    received: Dict[str, str] = {}

    def log_message(self, *args) -> None:  # noqa: D401 - silence stderr
        """Never log: the request line carries ?code=."""

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.urlparse(self.path).query
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        if params.get("state") != self.expected_state:
            self._respond(400, "State mismatch. Close this tab and run the command again.")
            return
        if "error" in params:
            _Callback.received = {"error": params["error"]}
            self._respond(200, f"Google reported: {params['error']}. You can close this tab.")
            return
        _Callback.received = {"code": params.get("code", "")}
        self._respond(200, "cleanuprtx is connected to Search Console. You can close this tab.")

    def _respond(self, status: int, text: str) -> None:
        body = f"<!doctype html><title>cleanuprtx</title><p style='font-family:system-ui;margin:3em'>{text}</p>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def google_login(open_browser: bool = True, out=sys.stdout) -> str:
    """Run the consent flow and store the refresh token. Returns granted scope."""
    try:
        client_id = read_secret(config.KC_GOOGLE_CLIENT_ID)
        client_secret = read_secret(config.KC_GOOGLE_CLIENT_SECRET)
    except KeychainError as exc:
        raise AuthError(
            f"{exc}\n\nCreate the OAuth client first:\n"
            "  1. console.cloud.google.com > APIs & Services > Library > enable "
            "'Google Search Console API'\n"
            "  2. Credentials > Create credentials > OAuth client ID > Desktop app\n"
            "  3. cleanuprtx auth google-client   (stores the ID and secret)\n"
        ) from exc

    pkce = _pkce_pair()
    state = secrets.token_urlsafe(16)
    _Callback.expected_state = state
    _Callback.received = {}

    server = http.server.HTTPServer(("127.0.0.1", 0), _Callback)
    server.timeout = LOOPBACK_TIMEOUT
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": pkce["challenge"],
        "code_challenge_method": "S256",
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print("Opening Google sign-in in your browser. Sign in with the account that owns", file=out)
    print("the Search Console properties, and allow read-only access.", file=out)
    print(f"\nIf the browser does not open, paste this URL into it:\n{url}\n", file=out)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    server.handle_request()   # blocks for one request or until timeout
    server.server_close()

    if not _Callback.received:
        raise AuthError("No response from the browser within 5 minutes. Run the command again.")
    if "error" in _Callback.received:
        raise AuthError(f"Google refused: {_Callback.received['error']}")
    code = _Callback.received.get("code", "")
    if not code:
        raise AuthError("Google returned no authorization code.")

    try:
        token = request_json(
            TOKEN_URL, method="POST",
            form={
                "code": code, "client_id": client_id, "client_secret": client_secret,
                "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                "code_verifier": pkce["verifier"],
            },
            retry_status=set(),
        ) or {}
    except HttpError as exc:
        raise AuthError(f"Token exchange failed: {exc}") from exc

    refresh = token.get("refresh_token")
    if not refresh:
        raise AuthError(
            "Google did not return a refresh token. This happens when a previous grant "
            "for this app still exists. Revoke it at myaccount.google.com/permissions and "
            "run 'cleanuprtx auth google' again."
        )
    granted = token.get("scope", "")
    if READONLY_SCOPE not in granted.split() or "auth/webmasters " in granted + " ":
        raise AuthError(f"Google granted {granted!r}; expected only {READONLY_SCOPE}. Not stored.")

    store_secret(config.KC_GOOGLE_REFRESH_TOKEN, refresh)
    return granted


def store_google_client(out=sys.stdout) -> None:
    """Prompt for and store the OAuth client ID and secret."""
    print("From Google Cloud Console > Credentials > your Desktop app client:", file=out)
    client_id = getpass.getpass("  Client ID: ").strip()
    client_secret = getpass.getpass("  Client secret (hidden): ").strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise AuthError("That does not look like a Google OAuth client ID.")
    store_secret(config.KC_GOOGLE_CLIENT_ID, client_id)
    store_secret(config.KC_GOOGLE_CLIENT_SECRET, client_secret)


def store_wordpress_password(site: config.Site, out=sys.stdout) -> None:
    """Prompt for and store a WordPress application password for one site."""
    print(f"WordPress application password for {site.wp_base_url}", file=out)
    print(f"  (Users > Profile > Application Passwords, logged in as {site.wp_account})", file=out)
    value = getpass.getpass("  Application password (hidden): ").strip()
    # WordPress shows them as 'xxxx xxxx xxxx xxxx xxxx xxxx'; spaces are optional.
    if len(value.replace(" ", "")) < 20:
        raise AuthError("That is too short to be a WordPress application password.")
    store_secret(site.wp_keychain_service, value, site.wp_account)
