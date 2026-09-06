"""Google Search Console client.

Uses an installed-app OAuth refresh token held in the Keychain. Access tokens
are minted per run and kept in memory only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import config
from .http import HttpError, request_json
from .keychain import read_secret

TOKEN_URL = "https://oauth2.googleapis.com/token"
SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


@dataclass
class Inspection:
    """Result of one URL inspection."""

    url: str
    verdict: str
    coverage_state: str
    robots_state: str
    canonical: str
    last_crawl: str
    rich_result_issues: List[str]

    @property
    def is_indexed(self) -> bool:
        return self.verdict == "PASS"


class SearchConsoleClient:
    """Read-only Search Console access.

    cleanuprtx deliberately requests the readonly scope: it reports what Google
    sees but never submits validation requests or removals on your behalf.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        payload = request_json(
            TOKEN_URL,
            method="POST",
            form={
                "client_id": read_secret(config.KC_GOOGLE_CLIENT_ID),
                "client_secret": read_secret(config.KC_GOOGLE_CLIENT_SECRET),
                "refresh_token": read_secret(config.KC_GOOGLE_REFRESH_TOKEN),
                "grant_type": "refresh_token",
            },
        )
        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def list_properties(self) -> List[Dict[str, Any]]:
        """Every property this token can read."""
        data = request_json(SITES_URL, headers=self._headers())
        return (data or {}).get("siteEntry", [])

    def inspect(self, url: str, property_id: str) -> Inspection:
        """Ask Google what it currently knows about one URL."""
        data = request_json(
            INSPECT_URL,
            method="POST",
            headers=self._headers(),
            body={
                "inspectionUrl": url,
                "siteUrl": property_id,
                "languageCode": "en-US",
            },
        )
        result = (data or {}).get("inspectionResult", {})
        index_status = result.get("indexStatusResult", {})

        issues: List[str] = []
        for block in result.get("richResultsResult", {}).get("detectedItems", []):
            for item in block.get("items", []):
                for issue in item.get("issues", []):
                    issues.append(
                        f"{block.get('richResultType', 'unknown')}: "
                        f"{issue.get('issueMessage', 'unspecified')} "
                        f"[{issue.get('severity', 'UNKNOWN')}]"
                    )

        return Inspection(
            url=url,
            verdict=index_status.get("verdict", "UNKNOWN"),
            coverage_state=index_status.get("coverageState", ""),
            robots_state=index_status.get("robotsTxtState", ""),
            canonical=index_status.get("googleCanonical", ""),
            last_crawl=index_status.get("lastCrawlTime", ""),
            rich_result_issues=issues,
        )

    def safe_inspect(self, url: str, property_id: str) -> Optional[Inspection]:
        """inspect(), returning None instead of raising on a per-URL failure.

        The inspection endpoint is quota-limited; one refusal should not end a
        whole audit run.
        """
        try:
            return self.inspect(url, property_id)
        except HttpError:
            return None
