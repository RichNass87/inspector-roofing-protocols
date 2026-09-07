"""WordPress REST API client.

Authenticates with an application password read from the Keychain. The single
write path stages a corrected post_content as an autosave revision (published
items) or updates the draft itself (draft items). It never sends a status
field, never publishes, never deletes, and never touches post meta.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from . import config
from .http import HttpError, fetch_text, request_json, request_json_with_headers
from .jsonld import Block, PatchError, Target, apply_patch, find_blocks, match_block_in_raw, splice_block
from .keychain import read_secret

DEFAULT_PER_PAGE = 25
MIN_PER_PAGE = 5
ITEM_FIELDS = "id,slug,link,title,status,type,date_gmt,modified_gmt,content"

# Post types that exist but should never be audited or written.
SKIP_TYPES = {"wp_block", "wp_template", "wp_template_part", "wp_navigation",
              "wp_global_styles", "wp_font_family", "wp_font_face", "nav_menu_item"}
# Kinds whose content the tool reads for the audit but must never write.
READ_ONLY_KINDS = {"media", "author"}
WRITABLE_STATUSES = {"publish", "draft", "pending", "private", "future"}

STRIPPED_AUTH = "rest_not_logged_in"
APP_PW_DISABLED = "application_passwords_disabled"
BAD_CREDENTIAL = {"incorrect_password", "invalid_username", "invalid_email",
                  "rest_forbidden", "rest_cannot_view"}


class AuthDiagnosis(RuntimeError):
    """Authentication failed for a reason the owner can act on."""


@dataclass
class Content:
    """One WordPress item: page, post, attachment, author archive, or CPT."""

    id: int
    kind: str                  # REST base: pages, posts, media, author, breakdance_header...
    post_type: str
    slug: str
    link: str
    title: str
    status: str
    date_gmt: str
    modified_gmt: str
    body_rendered: str         # content.rendered - the post body only
    raw: Dict[str, Any] = field(default_factory=dict)
    site_breakdance: bool = False
    front_html: Optional[str] = None   # what Googlebot sees; None = not fetched
    front_status: int = 0
    content_raw: Optional[str] = None  # content.raw via context=edit; None = not fetched

    @property
    def is_breakdance(self) -> bool:
        """Breakdance owns the layout of this item.

        On a Breakdance site every page is rendered from the canvas in post
        meta. Per-page markers on the front end can only confirm that, never
        clear it. This property changes how drafts are judged and what the
        report says; the write guard is separate and structural.
        """
        if self.post_type.startswith("breakdance_"):
            return True
        if self.site_breakdance:
            return True
        html = (self.front_html or "")[:20000]
        return f"/uploads/breakdance/css/post-{self.id}.css" in html or 'class="breakdance' in html

    @property
    def writable(self) -> bool:
        return (self.kind not in READ_ONLY_KINDS
                and not self.post_type.startswith("breakdance_")
                and self.status in WRITABLE_STATUSES)

    @property
    def edit_link(self) -> str:
        base = self.link.split("/wp-json")[0]
        # link is a front-end URL; derive the admin URL from its origin.
        from urllib.parse import urlparse
        p = urlparse(self.link)
        return f"{p.scheme}://{p.netloc}/wp-admin/post.php?post={self.id}&action=edit"


def _to_content(item: Dict[str, Any], kind: str, site: config.Site) -> Content:
    title = item.get("title")
    if isinstance(title, dict):
        title = title.get("rendered", "")
    content = item.get("content") or {}
    return Content(
        id=int(item["id"]),
        kind=kind,
        post_type=str(item.get("type") or kind),
        slug=item.get("slug", "") or "",
        link=item.get("link", "") or "",
        title=str(title or ""),
        status=item.get("status", "") or "",
        date_gmt=item.get("date_gmt") or "",
        modified_gmt=item.get("modified_gmt") or "",
        body_rendered=(content.get("rendered") if isinstance(content, dict) else "") or "",
        raw=item,
        site_breakdance=site.breakdance,
    )


class WordPressClient:
    """Read and draft-write access to one WordPress site."""

    def __init__(self, site: config.Site) -> None:
        self.site = site
        self._auth: Optional[str] = None

    # --- plumbing --------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        if self._auth is None:
            secret = read_secret(self.site.wp_keychain_service, self.site.wp_account)
            token = base64.b64encode(
                f"{self.site.wp_account}:{secret}".encode("utf-8")
            ).decode("ascii")
            self._auth = f"Basic {token}"
        return {"Authorization": self._auth}

    def _root(self) -> str:
        return self.site.wp_base_url.rstrip("/") + "/wp-json"

    def _url(self, path: str) -> str:
        return f"{self._root()}/wp/v2/{path.lstrip('/')}"

    # --- discovery -------------------------------------------------------

    def rest_index(self) -> Dict[str, Any]:
        """Unauthenticated GET /wp-json/. Reveals namespaces (plugins)."""
        return request_json(self._root() + "/") or {}

    def verify(self) -> Dict[str, Any]:
        """Confirm the credential works and report who we are.

        Returns {"name", "capabilities", "roles"}. Raises AuthDiagnosis with an
        owner-actionable message for the three common first-run failures.
        """
        try:
            me = request_json(
                self._url("users/me?context=edit&_fields=id,name,slug,roles,capabilities"),
                headers=self._headers(),
            ) or {}
        except HttpError as exc:
            raise AuthDiagnosis(self._diagnose_auth(exc)) from exc
        return {
            "name": me.get("name", "unknown"),
            "roles": me.get("roles", []),
            "capabilities": me.get("capabilities", {}) or {},
        }

    def _diagnose_auth(self, exc: HttpError) -> str:
        if exc.code == APP_PW_DISABLED:
            return ("WordPress says application passwords are disabled. A security "
                    "plugin usually does this (Wordfence > All Options > 'Disable "
                    "WordPress application passwords'). Turn that off for this tool.")
        if exc.code in BAD_CREDENTIAL:
            return (f"WordPress rejected the credential for {self.site.wp_account}. "
                    f"Re-create the application password (Users > Profile > Application "
                    f"Passwords) and run 'cleanuprtx auth wordpress --site {self.site.slug}'.")
        if exc.code == STRIPPED_AUTH:
            # Distinguish "header never arrived" from "header arrived, rejected".
            probe = self._probe_bad_password()
            if probe == STRIPPED_AUTH:
                return ("WordPress never received the Authorization header - the host "
                        "strips it before PHP. Add to .htaccess:\n"
                        "    SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1\n"
                        "or enable Authorization pass-through in the hosting panel.")
            return (f"WordPress saw the header but did not accept the credential for "
                    f"{self.site.wp_account}. Check the username and re-create the "
                    f"application password.")
        if exc.status == 403 and not exc.code:
            return ("The REST API is blocked before WordPress (403 with no WordPress "
                    "error code) - a WAF or hosting rule. Allow /wp-json/ for your IP.")
        if exc.status in (301, 302, 307, 308):
            return str(exc)
        return f"{exc}"

    def _probe_bad_password(self) -> str:
        token = base64.b64encode(f"{self.site.wp_account}:cleanuprtx-probe".encode()).decode()
        try:
            request_json(self._url("users/me"), headers={"Authorization": f"Basic {token}"})
        except HttpError as exc:
            return exc.code
        return ""

    def discover(self) -> Dict[str, Any]:
        """What is installed: SEO plugin, builder, WAF. Read-only."""
        result: Dict[str, Any] = {"namespaces": [], "plugins": [], "plugins_error": ""}
        try:
            result["namespaces"] = list((self.rest_index()).get("namespaces", []))
        except HttpError as exc:
            result["namespaces_error"] = str(exc)
        try:
            plugins = request_json(
                self._url("plugins?status=active&_fields=plugin,name,version,status"),
                headers=self._headers(),
            ) or []
            result["plugins"] = [
                {"slug": (p.get("plugin") or "").split("/")[0], "name": p.get("name", ""),
                 "version": p.get("version", "")} for p in plugins
            ]
        except HttpError as exc:
            result["plugins_error"] = ("not an administrator" if exc.status in (401, 403)
                                       else str(exc))
        return result

    def list_types(self) -> Dict[str, str]:
        """{post_type: rest_base} for every REST-exposed type."""
        types = request_json(self._url("types?context=view"), headers=self._headers()) or {}
        out: Dict[str, str] = {}
        for slug, info in types.items():
            base = info.get("rest_base") if isinstance(info, dict) else None
            if base and slug not in SKIP_TYPES:
                out[slug] = base
        return out

    # --- reading ---------------------------------------------------------

    def iter_content(
        self,
        kind: str,
        per_page: int = DEFAULT_PER_PAGE,
        progress: Optional[Callable[[str, int, int, int], None]] = None,
        statuses: str = "publish,draft,pending,private,future",
    ) -> Iterator[Content]:
        """Yield every item of one kind, adapting batch size on slow hosts."""
        offset = 0
        total = -1
        status_q = "" if kind == "media" else f"&status={statuses}"
        # media returns 'inherit' status items; status filter is not accepted.
        while True:
            url = self._url(f"{kind}?per_page={per_page}&offset={offset}{status_q}"
                            f"&_fields={ITEM_FIELDS}&context=view")
            try:
                batch, headers = request_json_with_headers(
                    url, headers=self._headers(), timeout=90
                )
            except HttpError as exc:
                if exc.status == 400 and exc.code == "rest_post_invalid_page_number":
                    return
                if exc.status in (0, 429, 500, 502, 503, 504) and per_page > MIN_PER_PAGE:
                    per_page = max(MIN_PER_PAGE, per_page // 2)
                    if progress:
                        progress(kind, offset, -1, per_page)
                    continue
                raise
            if total < 0:
                try:
                    total = int(headers.get("x-wp-total", "-1"))
                except ValueError:
                    total = -1
            if not batch:
                return
            for item in batch:
                yield _to_content(item, kind, self.site)
            offset += len(batch)
            if progress:
                progress(kind, offset, total, per_page)
            if len(batch) < per_page or (0 <= total <= offset):
                return

    def iter_authors(self) -> Iterator[Content]:
        """Author archives, read-only. RankMath emits ProfilePage schema here."""
        users = request_json(
            self._url("users?who=authors&per_page=100&_fields=id,slug,link,name"),
            headers=self._headers(),
        ) or []
        for u in users:
            yield Content(
                id=int(u["id"]), kind="author", post_type="author", slug=u.get("slug", ""),
                link=u.get("link", ""), title=u.get("name", ""), status="publish",
                date_gmt="", modified_gmt="", body_rendered="", raw=u,
                site_breakdance=self.site.breakdance,
            )

    def iter_all(
        self,
        kinds: Optional[List[str]] = None,
        progress: Optional[Callable[[str, int, int, int], None]] = None,
    ) -> Iterator[Content]:
        """Every auditable item across all post types plus author archives."""
        if kinds is None:
            try:
                types = self.list_types()
            except HttpError:
                types = {"page": "pages", "post": "posts", "attachment": "media"}
            kinds = sorted(set(types.values()))
            # Pages and posts first: they are what the owner cares about most.
            kinds.sort(key=lambda k: (k not in ("pages", "posts"), k))
        for kind in kinds:
            if kind == "author":
                yield from self.iter_authors()
                continue
            yield from self.iter_content(kind, progress=progress)
        if kinds and "author" not in kinds and any(k in ("pages", "posts") for k in kinds):
            try:
                yield from self.iter_authors()
            except HttpError:
                pass

    def get_content(self, kind: str, content_id: int) -> Optional[Content]:
        """One fresh item, or None if it no longer exists."""
        try:
            item = request_json(
                self._url(f"{kind}/{content_id}?_fields={ITEM_FIELDS}&context=view"),
                headers=self._headers(),
            )
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise
        return _to_content(item, kind, self.site)

    def load_front_html(self, content: Content, delay: float = 0.0) -> None:
        """Fetch what Googlebot sees. Unauthenticated; only public items."""
        if content.status != "publish" or not content.link:
            content.front_html = None
            return
        if delay:
            time.sleep(delay)
        fetched = fetch_text(content.link)
        content.front_status = fetched.status
        content.front_html = fetched.text if fetched.ok else None

    def load_content_raw(self, content: Content) -> None:
        """Fetch post_content as stored (context=edit). Requires edit capability."""
        if content.kind in READ_ONLY_KINDS:
            content.content_raw = None
            return
        item = request_json(
            self._url(f"{content.kind}/{content.id}?context=edit&_fields=id,status,content"),
            headers=self._headers(),
        ) or {}
        raw = (item.get("content") or {}).get("raw")
        content.content_raw = raw if isinstance(raw, str) else ""
        if item.get("status"):
            content.status = item["status"]

    # --- writing ---------------------------------------------------------

    def stage_block_repair(
        self, content: Content, front_block: Block, target: Target, patch: Dict[str, Any]
    ) -> Tuple[str, str]:
        """Apply one patch to one JSON-LD block and stage the result.

        Returns (mode, review_url) where mode is 'autosave' or 'draft'.

        Guards, in order:
          1. the item must be a writable kind and status (never media/author/
             Breakdance templates, never trash/auto-draft);
          2. post_content must be fetchable and non-empty;
          3. the exact block must exist in post_content - the structural
             proof that this JSON-LD is ours to edit and not the SEO
             plugin's or the page builder's;
          4. the node must still match what the audit saw.
        Only then is {"content": patched} sent. No other key, ever.
        """
        if not content.writable:
            raise PatchError(
                f"{content.kind}/{content.id} is {content.status or 'read-only'}; "
                "cleanuprtx does not write to this kind of item."
            )
        if content.content_raw is None:
            self.load_content_raw(content)
        if not content.content_raw:
            raise PatchError(
                "post_content is empty - the layout lives in the page builder. "
                "Edit the schema in the builder or SEO plugin by hand."
            )
        raw_block = match_block_in_raw(front_block, content.content_raw)
        if raw_block is None:
            raise PatchError(
                "this JSON-LD block is not in post_content; it is generated by the "
                "SEO plugin or theme and must be edited there."
            )
        if raw_block.document is None:
            raise PatchError(f"post_content block does not parse: {raw_block.parse_error}")

        patched_doc = apply_patch(raw_block.document, target, patch)
        patched_raw = splice_block(content.content_raw, raw_block, patched_doc)
        if patched_raw == content.content_raw:
            raise PatchError("patch produced no change")

        body = {"content": patched_raw}
        assert set(body) == {"content"}, "write body must contain only content"

        if content.status == "draft":
            result = request_json(
                self._url(f"{content.kind}/{content.id}"),
                method="POST", headers=self._headers(), body=body,
            ) or {}
            mode = "draft"
        else:
            result = request_json(
                self._url(f"{content.kind}/{content.id}/autosaves"),
                method="POST", headers=self._headers(), body=body,
            ) or {}
            mode = "autosave"

        # Confirm the server stored what we sent; a 200 alone proves nothing.
        stored = (result.get("content") or {})
        stored_raw = stored.get("raw") if isinstance(stored, dict) else None
        if isinstance(stored_raw, str):
            from .jsonld import normalise_block_text as _n
            if _n(stored_raw) != _n(patched_raw):
                raise PatchError("server response does not reflect the patched content")
        if mode == "draft" and result.get("status") not in (None, "draft"):
            raise PatchError(f"unexpected status after draft update: {result.get('status')}")

        return mode, content.edit_link
