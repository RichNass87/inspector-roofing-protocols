"""WordPress REST API client.

Authenticates with an application password read from the Keychain. All write
paths create or update drafts only - nothing in this module can publish.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from . import config
from .http import HttpError, request_json
from .keychain import read_secret

PER_PAGE = 100


@dataclass
class Content:
    """One WordPress page or post."""

    id: int
    kind: str
    slug: str
    link: str
    title: str
    status: str
    modified_gmt: str
    rendered_html: str
    raw: Dict[str, Any]

    @property
    def is_breakdance(self) -> bool:
        """True if Breakdance owns this page's layout.

        Breakdance stores its canvas in post meta, not post_content, so the
        REST content field is a rendered artifact. Rewriting it would be
        discarded on the next Breakdance save.
        """
        meta = self.raw.get("meta") or {}
        if any(str(k).startswith("_breakdance") for k in meta):
            return True
        return "breakdance" in (self.rendered_html[:4000].lower())


class WordPressClient:
    """Read and draft-write access to one WordPress site."""

    def __init__(self, site: config.Site, account: str = config.WP_ACCOUNT) -> None:
        self.site = site
        self._account = account
        self._auth: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        if self._auth is None:
            secret = read_secret(config.KC_WP_APP_PASSWORD, self._account)
            token = base64.b64encode(
                f"{self._account}:{secret}".encode("utf-8")
            ).decode("ascii")
            self._auth = f"Basic {token}"
        return {"Authorization": self._auth}

    def _url(self, path: str) -> str:
        return f"{self.site.wp_base_url.rstrip('/')}/wp-json/wp/v2/{path.lstrip('/')}"

    def verify(self) -> str:
        """Confirm the credential works. Returns the authenticated user's name."""
        me = request_json(
            f"{self.site.wp_base_url.rstrip('/')}/wp-json/wp/v2/users/me",
            headers=self._headers(),
        )
        return me.get("name", "unknown")

    def iter_content(self, kind: str = "pages") -> Iterator[Content]:
        """Yield every page or post, following pagination."""
        page = 1
        while True:
            try:
                batch = request_json(
                    self._url(f"{kind}?per_page={PER_PAGE}&page={page}"
                              "&status=publish,draft,pending,private"
                              "&_fields=id,slug,link,title,status,modified_gmt,content,meta"),
                    headers=self._headers(),
                )
            except HttpError as exc:
                # WordPress returns 400 once the page number runs past the end.
                if exc.status == 400:
                    return
                raise

            if not batch:
                return

            for item in batch:
                yield Content(
                    id=item["id"],
                    kind=kind,
                    slug=item.get("slug", ""),
                    link=item.get("link", ""),
                    title=(item.get("title") or {}).get("rendered", ""),
                    status=item.get("status", ""),
                    modified_gmt=item.get("modified_gmt", ""),
                    rendered_html=(item.get("content") or {}).get("rendered", ""),
                    raw=item,
                )

            if len(batch) < PER_PAGE:
                return
            page += 1

    def fetch_all(self) -> List[Content]:
        items = list(self.iter_content("pages"))
        items.extend(self.iter_content("posts"))
        return items

    def create_draft_revision(self, content: Content, fields: Dict[str, Any]) -> str:
        """Apply approved field changes, forcing draft status.

        Refuses outright if the caller tries to set a published status. This is
        the only write path in cleanuprtx.
        """
        if str(fields.get("status", "draft")) != "draft":
            raise ValueError(
                "cleanuprtx only writes drafts. Publishing is done by hand in "
                "WordPress after review."
            )
        payload = dict(fields)
        payload["status"] = "draft"
        result = request_json(
            self._url(f"{content.kind}/{content.id}"),
            method="POST",
            headers=self._headers(),
            body=payload,
        )
        return result.get("link", content.link)
