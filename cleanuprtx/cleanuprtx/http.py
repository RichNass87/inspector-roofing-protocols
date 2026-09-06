"""Minimal JSON-over-HTTPS client built on the standard library.

No third-party dependencies, so cleanuprtx runs on a stock Python 3.9+ with
nothing to install.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

USER_AGENT = "cleanuprtx/0.1 (+https://inspector-roofing.com)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    """A request failed after exhausting retries."""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body[:500]
        super().__init__(f"HTTP {status} from {url}: {self.body}")


def request_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[Any] = None,
    form: Optional[Dict[str, str]] = None,
    timeout: int = 45,
    retries: int = 3,
) -> Any:
    """Perform an HTTPS request and decode the JSON response.

    Retries transient failures with exponential backoff. Raises HttpError on a
    non-retryable status or once retries are exhausted.
    """
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

    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=payload, headers=all_headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code in RETRY_STATUS and attempt < retries:
                last_error = exc
                time.sleep(2 ** attempt)
                continue
            raise HttpError(exc.code, url, detail) from exc
        except urllib.error.URLError as exc:
            if attempt < retries:
                last_error = exc
                time.sleep(2 ** attempt)
                continue
            raise HttpError(0, url, str(exc.reason)) from exc

    raise HttpError(0, url, str(last_error))  # pragma: no cover


def head_status(url: str, timeout: int = 30, user_agent: str = USER_AGENT) -> int:
    """Return the HTTP status for a URL, or 0 if the host is unreachable.

    Used to reproduce the 403-to-Googlebot reports, so the user agent is
    caller-supplied.
    """
    req = urllib.request.Request(
        url, method="GET", headers={"User-Agent": user_agent}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError:
        return 0
