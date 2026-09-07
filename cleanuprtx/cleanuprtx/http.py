"""Minimal HTTPS client built on the standard library.

Two distinct paths:

* request_json  - authenticated API calls (WordPress REST, Google). Refuses
                  every redirect, so a credential can never be forwarded to a
                  host the caller did not name.
* fetch_text    - unauthenticated front-end GETs with a browser User-Agent,
                  following redirects like a browser would. Never carries an
                  Authorization header.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set, Tuple

USER_AGENT = "cleanuprtx/0.2 (+https://inspector-roofing.com)"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
RETRY_STATUS: Set[int] = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60

CERT_HELP = (
    "Python cannot verify the server's TLS certificate. On macOS with the "
    "python.org installer run 'Install Certificates.command' from the Python "
    "folder in /Applications; with Homebrew Python, 'brew reinstall ca-certificates'."
)


class HttpError(RuntimeError):
    """A request failed after exhausting retries.

    status is 0 when no HTTP response was received (DNS, TLS, timeout).
    """

    def __init__(self, status: int, url: str, body: str, code: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body[:600]
        self.code = code or _json_code(body)
        label = f"HTTP {status}" if status else "could not reach"
        super().__init__(f"{label} {url}: {self.body}")


def _json_code(body: str) -> str:
    """WordPress and Google both put a machine-readable code in error JSON."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return ""
    if isinstance(data, dict):
        if isinstance(data.get("code"), str):
            return data["code"]
        err = data.get("error")
        if isinstance(err, dict) and isinstance(err.get("status"), str):
            return err["status"]
        if isinstance(err, str):
            return err
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """API endpoints never legitimately redirect. Following one would forward
    the Authorization header to whatever host the Location names, and would
    silently turn a POST into a GET."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


_API_OPENER = urllib.request.build_opener(_NoRedirect)
_BROWSER_OPENER = urllib.request.build_opener()


def _classify_urlerror(exc: urllib.error.URLError) -> Tuple[str, bool]:
    """Return (message, retryable)."""
    reason = exc.reason
    if isinstance(reason, ssl.SSLCertVerificationError):
        return CERT_HELP, False
    if isinstance(reason, ssl.SSLError):
        return f"TLS error: {reason}", False
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timed out", True
    if isinstance(reason, socket.gaierror):
        return f"DNS lookup failed: {reason}", False
    return str(reason), True


def request_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
    form: Optional[Dict[str, str]] = None,
    timeout: int = 45,
    retries: int = 3,
    retry_status: Optional[Set[int]] = None,
) -> Any:
    """Perform an API request and decode the JSON response.

    Retries transient failures with exponential backoff, honouring Retry-After.
    Raises HttpError on a non-retryable status, on any redirect, or once
    retries are exhausted.
    """
    return request_json_with_headers(
        url, method, headers, body, form, timeout, retries, retry_status
    )[0]


def request_json_with_headers(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
    form: Optional[Dict[str, str]] = None,
    timeout: int = 45,
    retries: int = 3,
    retry_status: Optional[Set[int]] = None,
) -> Tuple[Any, Dict[str, str]]:
    """request_json, also returning lower-cased response headers."""
    if not url.lower().startswith("https://"):
        raise HttpError(0, url, "refusing to send credentials over a non-HTTPS URL")

    retry_on = RETRY_STATUS if retry_status is None else retry_status
    payload: Optional[bytes] = None
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        all_headers.update(headers)

    if form is not None:
        payload = urllib.parse.urlencode(form).encode("utf-8")
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        payload = json.dumps(body).encode("utf-8")
        all_headers["Content-Type"] = "application/json"

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=payload, headers=all_headers, method=method)
        try:
            with _API_OPENER.open(req, timeout=timeout) as resp:
                try:
                    raw = resp.read().decode("utf-8", "replace")
                except (OSError, http.client.HTTPException) as exc:
                    if attempt < retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise HttpError(0, url, f"connection dropped while reading: {exc}") from exc
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                if not raw.strip():
                    return None, resp_headers
                try:
                    return json.loads(raw), resp_headers
                except json.JSONDecodeError as exc:
                    raise HttpError(
                        resp.status, url,
                        f"expected JSON but got {resp.headers.get('Content-Type', 'unknown')}: "
                        f"{raw[:200]!r}",
                    ) from exc
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                target = exc.headers.get("Location", "<no Location header>") if exc.headers else "?"
                raise HttpError(
                    exc.code, url,
                    f"unexpected redirect to {target}; refusing to follow with credentials. "
                    "Check the site's base URL (scheme, www, trailing slash) or a host/WAF rule.",
                ) from exc
            try:
                detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
            except (OSError, http.client.HTTPException):
                detail = ""
            if exc.code in retry_on and attempt < retries:
                time.sleep(_backoff(exc.headers, attempt))
                continue
            raise HttpError(exc.code, url, detail) from exc
        except urllib.error.URLError as exc:
            message, retryable = _classify_urlerror(exc)
            if retryable and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise HttpError(0, url, message) from exc
        except (socket.timeout, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise HttpError(0, url, "timed out") from exc
        except (OSError, http.client.HTTPException) as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise HttpError(0, url, f"connection dropped: {exc}") from exc

    raise HttpError(0, url, "retries exhausted")  # pragma: no cover


def _backoff(headers: Any, attempt: int) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after:
        try:
            return min(float(retry_after), MAX_RETRY_AFTER)
        except ValueError:
            pass
    return float(2 ** attempt)


@dataclass
class Fetched:
    """Result of an unauthenticated front-end GET."""

    url: str
    status: int
    final_url: str
    headers: Dict[str, str] = field(default_factory=dict)
    text: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def fetch_text(
    url: str,
    timeout: int = 30,
    user_agent: str = BROWSER_UA,
    accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
) -> Fetched:
    """GET a public URL as a browser would. Never sends credentials.

    Returns status 0 when the host could not be reached. Never raises on an
    HTTP error, since a 403 or 404 is itself a result the caller wants.
    """
    req = urllib.request.Request(
        url, method="GET",
        headers={"User-Agent": user_agent, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"},
    )
    try:
        with _BROWSER_OPENER.open(req, timeout=timeout) as resp:
            body = resp.read()
            return Fetched(
                url=url, status=resp.status, final_url=resp.geturl(),
                headers={k.lower(): v for k, v in resp.headers.items()},
                text=_decode(body, resp.headers.get_content_charset()),
            )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() if exc.fp else b""
        except (OSError, http.client.HTTPException):
            body = b""   # a 403 whose body was cut is still a 403
        return Fetched(
            url=url, status=exc.code, final_url=exc.geturl() or url,
            headers={k.lower(): v for k, v in (exc.headers or {}).items()},
            text=_decode(body, None),
        )
    except (OSError, http.client.HTTPException, ValueError):
        # URLError, socket.timeout, ConnectionResetError are OSError subclasses;
        # IncompleteRead is an HTTPException. Covers a drop during resp.read().
        return Fetched(url=url, status=0, final_url=url)


def _decode(body: bytes, charset: Optional[str]) -> str:
    for enc in (charset, "utf-8", "latin-1"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


def head_status(url: str, timeout: int = 30, user_agent: str = BROWSER_UA) -> int:
    """HTTP status for a URL, or 0 if unreachable. Kept for callers that only
    need the number."""
    return fetch_text(url, timeout=timeout, user_agent=user_agent).status
