"""Google Search Console client.

Uses an installed-app OAuth refresh token held in the Keychain. Access tokens
are minted per run and kept in memory only. The read-only scope is requested
on every refresh and verified in Google's response before the token is used.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from . import config
from .http import HttpError, request_json
from .keychain import read_secret

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SITEMAPS_URL = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"

# URL Inspection quota per property: 2,000/day, 600/minute.
INSPECT_MIN_INTERVAL = 0.11
# Google's crawler was refused: the 403-to-Googlebot signature.
BLOCKED_FETCH_STATES = {"ACCESS_FORBIDDEN", "ACCESS_DENIED", "BLOCKED_ROBOTS_TXT", "BLOCKED_4XX"}
# Google could not use the page for another reason.
PROBLEM_FETCH_STATES = {"SERVER_ERROR", "NOT_FOUND", "SOFT_404", "REDIRECT_ERROR", "INTERNAL_CRAWL_ERROR"}


class ScopeError(HttpError):
    """The stored refresh token grants more than read-only access."""

    def __init__(self, message: str) -> None:
        super().__init__(0, TOKEN_URL, message)


@dataclass
class RichResultIssue:
    rich_result_type: str
    item_name: str
    message: str
    severity: str

    def __str__(self) -> str:
        return f"{self.rich_result_type} / {self.item_name}: {self.message} [{self.severity}]"


@dataclass
class Inspection:
    """Result of one URL inspection."""

    url: str
    verdict: str = ""
    coverage_state: str = ""
    robots_state: str = ""
    indexing_state: str = ""
    page_fetch_state: str = ""
    canonical: str = ""
    user_canonical: str = ""
    last_crawl: str = ""
    crawled_as: str = ""
    result_link: str = ""
    rich_results_verdict: str = ""
    rich_result_issues: List[RichResultIssue] = field(default_factory=list)
    error: str = ""

    @property
    def is_indexed(self) -> bool:
        return self.verdict == "PASS"

    @property
    def fetch_blocked(self) -> bool:
        return self.page_fetch_state in BLOCKED_FETCH_STATES

    @property
    def fetch_problem(self) -> bool:
        return self.page_fetch_state in PROBLEM_FETCH_STATES


def parse_inspection(url: str, data: Dict[str, Any]) -> Inspection:
    """Map the v1 inspectionResult shape onto Inspection."""
    result = (data or {}).get("inspectionResult", {}) or {}
    idx = result.get("indexStatusResult", {}) or {}
    rich = result.get("richResultsResult", {}) or {}

    issues: List[RichResultIssue] = []
    for detected in rich.get("detectedItems", []) or []:
        rtype = detected.get("richResultType", "unknown")
        for item in detected.get("items", []) or []:
            for issue in item.get("issues", []) or []:
                issues.append(RichResultIssue(
                    rich_result_type=rtype,
                    item_name=item.get("name", ""),
                    message=issue.get("issueMessage", "unspecified"),
                    severity=issue.get("severity", "UNKNOWN"),
                ))

    return Inspection(
        url=url,
        verdict=idx.get("verdict", ""),
        coverage_state=idx.get("coverageState", ""),
        robots_state=idx.get("robotsTxtState", ""),
        indexing_state=idx.get("indexingState", ""),
        page_fetch_state=idx.get("pageFetchState", ""),
        canonical=idx.get("googleCanonical", ""),
        user_canonical=idx.get("userCanonical", ""),
        last_crawl=idx.get("lastCrawlTime", ""),
        crawled_as=idx.get("crawledAs", ""),
        result_link=result.get("inspectionResultLink", ""),
        rich_results_verdict=rich.get("verdict", ""),
        rich_result_issues=issues,
    )


class SearchConsoleClient:
    """Read-only Search Console access."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        self._last_inspect: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        try:
            payload = request_json(
                TOKEN_URL,
                method="POST",
                form={
                    "client_id": read_secret(config.KC_GOOGLE_CLIENT_ID),
                    "client_secret": read_secret(config.KC_GOOGLE_CLIENT_SECRET),
                    "refresh_token": read_secret(config.KC_GOOGLE_REFRESH_TOKEN),
                    "grant_type": "refresh_token",
                    "scope": READONLY_SCOPE,
                },
                retry_status=set(),
            ) or {}
        except HttpError as exc:
            if exc.code == "invalid_grant":
                raise HttpError(
                    exc.status, TOKEN_URL,
                    "Google rejected the refresh token (invalid_grant). It was revoked or "
                    "expired - Google expires tokens after 7 days while the OAuth consent "
                    "screen is in 'Testing'. Run 'cleanuprtx auth google' to sign in again.",
                    code="invalid_grant",
                ) from exc
            raise

        # Google echoes the granted scope when it is narrower than or equal to the
        # request; an absent field means "as requested" and is accepted.
        granted = set((payload.get("scope") or "").split())
        if granted and granted != {READONLY_SCOPE}:
            raise ScopeError(
                f"the stored Google token grants {sorted(granted)}; cleanuprtx requires "
                f"exactly {READONLY_SCOPE}. Run 'auth google' to re-consent with read-only access."
            )

        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def list_properties(self) -> List[Dict[str, Any]]:
        """Every property this token can read: [{siteUrl, permissionLevel}]."""
        data = request_json(SITES_URL, headers=self._headers())
        return (data or {}).get("siteEntry", [])

    def list_sitemaps(self, property_id: str) -> List[Dict[str, Any]]:
        data = request_json(
            SITEMAPS_URL.format(site=quote(property_id, safe="")), headers=self._headers()
        )
        return (data or {}).get("sitemap", [])

    def list_pages(self, property_id: str, days: int = 90, limit: int = 25000) -> List[Dict[str, Any]]:
        """Pages with any impressions in the window: [{page, clicks, impressions}]."""
        import datetime as dt
        end = dt.date.today() - dt.timedelta(days=2)   # GSC data lags ~2 days
        start = end - dt.timedelta(days=days)
        data = request_json(
            ANALYTICS_URL.format(site=quote(property_id, safe="")),
            method="POST", headers=self._headers(),
            body={"startDate": start.isoformat(), "endDate": end.isoformat(),
                  "dimensions": ["page"], "rowLimit": limit},
        ) or {}
        return [
            {"page": row["keys"][0], "clicks": row.get("clicks", 0),
             "impressions": row.get("impressions", 0)}
            for row in data.get("rows", []) if row.get("keys")
        ]

    def inspect(self, url: str, property_id: str) -> Inspection:
        """Ask Google what it currently knows about one URL. Raises HttpError."""
        wait = INSPECT_MIN_INTERVAL - (time.time() - self._last_inspect)
        if wait > 0:
            time.sleep(wait)
        self._last_inspect = time.time()
        data = request_json(
            INSPECT_URL, method="POST", headers=self._headers(),
            body={"inspectionUrl": url, "siteUrl": property_id, "languageCode": "en-US"},
            retry_status={500, 502, 503, 504},   # never retry a quota 429
        )
        return parse_inspection(url, data or {})

    def safe_inspect(self, url: str, property_id: str) -> Inspection:
        """inspect(), returning the error on the Inspection instead of raising.

        A 429 is fatal for the run - the caller should stop rather than burn
        the rest of the daily quota on refusals.
        """
        try:
            return self.inspect(url, property_id)
        except HttpError as exc:
            insp = Inspection(url=url)
            if exc.status == 429:
                insp.error = "QUOTA: Google refused with 429; stop for today. " + _google_message(exc)
            elif exc.status == 403:
                msg = _google_message(exc)
                if "has not been used" in msg or "is disabled" in msg or "not enabled" in msg.lower():
                    insp.error = ("API: the Search Console API is not enabled in the Google Cloud "
                                  "project that owns the OAuth client. " + msg)
                else:
                    insp.error = ("PERMISSION: this Google account cannot inspect URLs on "
                                  f"{property_id} (needs Owner or Full user, and the URL must "
                                  "belong to the property). " + msg)
            elif exc.status == 400:
                insp.error = "REQUEST: " + _google_message(exc)
            else:
                insp.error = f"{exc}"
            return insp


def _google_message(exc: HttpError) -> str:
    import json as _json
    try:
        return (_json.loads(exc.body).get("error") or {}).get("message", "") or exc.body
    except (ValueError, AttributeError):
        return exc.body
