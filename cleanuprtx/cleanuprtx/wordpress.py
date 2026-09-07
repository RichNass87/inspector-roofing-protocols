"""WordPress REST API client.

Authenticates with an application password read from the Keychain. The single
write path stages a corrected post_content as an autosave revision (published
items) or updates the draft itself (draft items). It never sends a status
field, never publishes, never deletes, and never touches post meta.
"""

from __future__ import annotations

import base64
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

from . import config
from .http import HttpError, fetch_text, request_json, request_json_with_headers
from .jsonld import (AlreadyApplied, Block, PatchError, Target, apply_patch, find_blocks, iter_nodes,
                     match_block_in_raw, normalise_block_text, references_outside,
                     rename_references, splice_block)
from .keychain import read_secret

DEFAULT_PER_PAGE = 25
MIN_PER_PAGE = 5
ITEM_FIELDS = "id,slug,link,title,status,type,date_gmt,modified_gmt,content"
EDIT_FIELDS = "id,status,content,title,excerpt"

# Post types that exist but should never be audited or written.
SKIP_TYPES = {"wp_block", "wp_template", "wp_template_part", "wp_navigation",
              "wp_global_styles", "wp_font_family", "wp_font_face", "nav_menu_item"}
# Kinds whose content the tool reads for the audit but must never write.
READ_ONLY_KINDS = {"media", "author"}
WRITABLE_STATUSES = {"publish", "draft", "pending", "private", "future"}
PUBLIC_STATUSES = {"publish", "inherit"}   # attachments report 'inherit'

STRIPPED_AUTH = "rest_not_logged_in"
APP_PW_DISABLED = "application_passwords_disabled"
BAD_CREDENTIAL = {"incorrect_password", "invalid_username", "invalid_email",
                  "rest_forbidden", "rest_cannot_view"}

# Capabilities the audit and the write path exercise.
AUDIT_CAPS = ("edit_pages", "edit_posts", "edit_others_pages", "edit_others_posts",
              "edit_private_pages", "edit_private_posts")
WRITE_CAPS = ("edit_published_pages", "edit_published_posts", "unfiltered_html")


class AuthDiagnosis(RuntimeError):
    """Authentication failed for a reason the owner can act on."""


class OwnershipLost(PatchError):
    """post_content no longer owns the block; a later audit must re-propose it."""


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
    front_html: Optional[str] = None   # what Googlebot sees; None = not fetched / not public
    front_status: int = 0
    front_redirected_to: str = ""      # set when the permalink redirects elsewhere
    content_raw: Optional[str] = None  # content.raw via context=edit; None = not fetched
    title_raw: Optional[str] = None    # title.raw, only if the post type has one
    excerpt_raw: Optional[str] = None  # excerpt.raw, only if the post type has one
    edit_loaded: bool = False          # context=edit was read for this item

    @property
    def is_breakdance(self) -> bool:
        """Breakdance owns the layout of this item.

        On a Breakdance site every page is rendered from the canvas in post
        meta. Per-page markers on the front end can only confirm that, never
        clear it. This changes how drafts are judged and what the report says;
        the write guard is separate and structural.
        """
        if self.post_type.startswith("breakdance_"):
            return True
        if self.site_breakdance:
            return True
        html = (self.front_html or "")[:20000]
        return f"/uploads/breakdance/css/post-{self.id}.css" in html or 'class="breakdance' in html

    @property
    def is_public(self) -> bool:
        return self.status in PUBLIC_STATUSES and bool(self.link)

    @property
    def writable(self) -> bool:
        return (self.kind not in READ_ONLY_KINDS
                and not self.post_type.startswith("breakdance_")
                and self.status in WRITABLE_STATUSES)

    @property
    def edit_link(self) -> str:
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


def _same_url(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        return host + p.path.rstrip("/")
    return norm(a) == norm(b)


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

        Returns {"name", "roles", "capabilities"}. Raises AuthDiagnosis with an
        owner-actionable message for the common first-run failures.
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
                    f"Passwords) and run 'auth wordpress --site {self.site.slug}'.")
        if exc.code == STRIPPED_AUTH:
            # Distinguish "header never arrived" from "header arrived, rejected".
            probe = self._probe_bad_password()
            if probe == STRIPPED_AUTH:
                available = self._app_passwords_available()
                if available is False:
                    return ("Application passwords are unavailable on this install: WordPress only "
                            "offers them over HTTPS and when no plugin or filter disables them. "
                            "Check the site URL scheme and security-plugin settings.")
                return ("WordPress never received the Authorization header - the host strips it "
                        "before PHP. Add to .htaccess:\n"
                        "    SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1\n"
                        "or enable Authorization pass-through in the hosting panel.")
            return (f"WordPress saw the header but did not accept the credential for "
                    f"{self.site.wp_account}. Check the username and re-create the "
                    f"application password.")
        if exc.status == 403 and not exc.code:
            return ("The REST API is blocked before WordPress (403 with no WordPress "
                    "error code) - a WAF or hosting rule. Allow /wp-json/ for your IP.")
        return f"{exc}"

    def _app_passwords_available(self) -> Optional[bool]:
        """Core advertises application passwords in the REST index only when
        wp_is_application_passwords_available() is true. None if unknown."""
        try:
            auth = (self.rest_index().get("authentication") or {})
        except HttpError:
            return None
        return "application-passwords" in auth

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
        """Yield every item of one kind, adapting batch size on slow hosts.

        Ordered by id so an offset walk is stable while content changes.
        """
        offset = 0
        total = -1
        # media items carry status 'inherit' and reject the status filter.
        status_q = "" if kind == "media" else f"&status={statuses}"
        while True:
            url = self._url(f"{kind}?per_page={per_page}&offset={offset}{status_q}"
                            f"&orderby=id&order=asc&_fields={ITEM_FIELDS}&context=view")
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
        """Author archives, read-only. Rank Math emits ProfilePage schema here."""
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
        warn: Optional[Callable[[str], None]] = None,
    ) -> Iterator[Content]:
        """Every auditable item across all post types plus author archives.

        A post type that refuses its first listing request (403/404 on a
        plugin CPT) is reported through warn() and skipped. A refusal after
        items were received propagates, so the inventory is reported partial.
        """
        if kinds is None:
            try:
                types = self.list_types()
            except HttpError:
                types = {"page": "pages", "post": "posts", "attachment": "media"}
            kinds = sorted(set(types.values()))
            kinds.sort(key=lambda k: (k not in ("pages", "posts"), k))
        for kind in kinds:
            yielded = 0
            try:
                items = self.iter_authors() if kind == "author" else self.iter_content(kind, progress=progress)
                for item in items:
                    yielded += 1
                    yield item
            except HttpError as exc:
                # A post type that refuses its FIRST listing is skipped; a refusal
                # after items arrived (a WAF tripping mid-walk) aborts as partial.
                if yielded == 0 and exc.status in (401, 403, 404):
                    if warn:
                        warn(f"skipping {kind}: {exc}")
                    continue
                raise
        if kinds and "author" not in kinds and any(k in ("pages", "posts") for k in kinds):
            try:
                yield from self.iter_authors()
            except HttpError as exc:
                if warn:
                    warn(f"skipping author archives: {exc}")

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
        """Fetch what Googlebot sees. Unauthenticated; only public items.

        A permalink that redirects elsewhere is recorded and not audited:
        Google audits the target, and that target is its own item.
        """
        content.front_html = None
        content.front_redirected_to = ""
        if not content.is_public:
            return
        if delay:
            time.sleep(delay)
        fetched = fetch_text(content.link)
        content.front_status = fetched.status
        if fetched.ok and not _same_url(fetched.final_url, content.link):
            content.front_redirected_to = fetched.final_url
            return
        content.front_html = fetched.text if fetched.ok else None

    def load_content_raw(self, content: Content) -> None:
        """Fetch post_content, title and excerpt as stored (context=edit)."""
        if content.kind in READ_ONLY_KINDS:
            content.content_raw = None
            return
        item = request_json(
            self._url(f"{content.kind}/{content.id}?context=edit&_fields={EDIT_FIELDS}"),
            headers=self._headers(),
        ) or {}
        raw = (item.get("content") or {}).get("raw") if isinstance(item.get("content"), dict) else None
        content.content_raw = raw if isinstance(raw, str) else ""
        for key, attr in (("title", "title_raw"), ("excerpt", "excerpt_raw")):
            value = item.get(key)
            if isinstance(value, dict) and isinstance(value.get("raw"), str):
                setattr(content, attr, value["raw"])
        if item.get("status"):
            content.status = item["status"]
        content.edit_loaded = True

    # --- writing ---------------------------------------------------------

    def prepare_block_repair(
        self, content: Content, front_block: Block, target: Target, patch: Dict[str, Any],
        working_raw: Optional[str] = None,
    ) -> str:
        """Apply one patch and return the new working post_content. No write.

        Ownership is proven against the ORIGINAL post_content; the patch is
        applied to the block with the same ordinal in the working copy, so
        several repairs on one page can be folded into one write.

        Guards, in order:
          1. the item must be a writable kind and status (never media/author/
             Breakdance templates, never trash/auto-draft);
          2. post_content must be fetchable and non-empty;
          3. the exact block must exist in post_content - the structural
             proof that this JSON-LD is ours to edit and not the SEO
             plugin's or the page builder's;
          4. the node must still match what the audit saw.
        """
        if not content.writable:
            raise PatchError(
                f"{content.kind}/{content.id} is {content.status or 'read-only'}; "
                "cleanuprtx does not write to this kind of item."
            )
        if content.content_raw is None:
            self.load_content_raw(content)
            # The edit context refreshes status; the item may have been trashed.
            if not content.writable:
                raise PatchError(
                    f"{content.kind}/{content.id} is now {content.status or 'read-only'}; not written."
                )
        if not content.content_raw:
            raise OwnershipLost(
                "post_content is empty - the layout lives in the page builder. "
                "Edit the schema in the builder or SEO plugin by hand."
            )
        raw_block = match_block_in_raw(front_block, content.content_raw)
        if raw_block is None:
            raise OwnershipLost(
                "this JSON-LD block is not in post_content; it is generated by the "
                "SEO plugin or theme and must be edited there."
            )
        if target.raw_block >= 0 and raw_block.index != target.raw_block:
            raise OwnershipLost(
                f"block is post_content block {raw_block.index}; the audit keyed this repair "
                f"on block {target.raw_block}. post_content changed - re-run the audit."
            )
        if raw_block.document is None:
            raise PatchError(f"post_content block does not parse: {raw_block.parse_error}")

        if working_raw is None:
            working_raw = content.content_raw
        working_blocks = find_blocks(working_raw)
        if len(working_blocks) != len(find_blocks(content.content_raw)):
            raise PatchError("an earlier repair changed the number of JSON-LD blocks; not stacking")
        work_block = working_blocks[raw_block.index]
        if work_block.document is None:
            raise PatchError("working copy of the block no longer parses")

        patched_doc = apply_patch(work_block.document, target, patch)
        patched_raw = splice_block(working_raw, work_block, patched_doc)
        if patch.get("op") == "rename_id":
            old, new = patch["old"], patch["new"]
            # A reference in a block we do not own (plugin/theme, on the live
            # page but not in post_content) would be left dangling: refuse.
            if content.front_html:
                live_refs = references_outside(find_blocks(content.front_html), old, front_block.index)
                foreign = [i for i in live_refs
                           if match_block_in_raw(find_blocks(content.front_html)[i], content.content_raw) is None]
                if foreign:
                    raise PatchError(
                        f"@id {old} is referenced by a plugin/theme-generated block on the live "
                        "page; renaming it here would leave that reference dangling."
                    )
            # References in the other body blocks - as they stand in the working
            # copy, earlier repairs in this fold included - follow the rename.
            if references_outside(find_blocks(patched_raw), old, work_block.index):
                patched_raw = rename_references(patched_raw, old, new, work_block.index)
        elif patch.get("op") == "set":
            # Every reference the patch writes must resolve on the page as it
            # will be after the write (working copy plus plugin/theme blocks).
            defined = set()
            for blk in find_blocks(patched_raw) + find_blocks(content.front_html or ""):
                if blk.document is not None:
                    for _, n in iter_nodes(blk.document):
                        if isinstance(n.get("@id"), str) and n.get("@type"):
                            defined.add(n["@id"])
            for _, n in iter_nodes(patch.get("value")):
                ref = n.get("@id")
                if isinstance(ref, str) and not n.get("@type") and ref not in defined:
                    raise PatchError(f"the repair references @id {ref}, which is no longer defined on the page")
        if patched_raw == working_raw:
            raise AlreadyApplied("patch produced no change")
        return patched_raw

    def stage_content(self, content: Content, patched_raw: str) -> Tuple[str, str]:
        """Write a corrected post_content. Returns (mode, review_url).

        Published, private, pending and scheduled items get an autosave
        revision (the editor offers to restore it; the live item is untouched).
        Drafts are updated in place. The body carries content, and on the
        autosave path the item's own unchanged title/excerpt so the revision is
        complete - restoring an autosave with an empty title would blank the
        page. Never status, never meta.
        """
        if not content.writable:
            raise PatchError(f"{content.kind}/{content.id} is {content.status}; not written.")
        if patched_raw == content.content_raw:
            raise AlreadyApplied("nothing to write")

        if content.status == "draft":
            body: Dict[str, Any] = {"content": patched_raw}
            assert set(body) == {"content"}
            result = request_json(
                self._url(f"{content.kind}/{content.id}"),
                method="POST", headers=self._headers(), body=body,
            ) or {}
            mode = "draft"
        else:
            body = {"content": patched_raw}
            if content.title_raw is not None:
                body["title"] = content.title_raw
            if content.excerpt_raw is not None:
                body["excerpt"] = content.excerpt_raw
            assert set(body) <= {"content", "title", "excerpt"}
            assert "status" not in body and "meta" not in body
            result = request_json(
                self._url(f"{content.kind}/{content.id}/autosaves"),
                method="POST", headers=self._headers(), body=body,
            ) or {}
            mode = "autosave"

        # A 200 alone proves nothing: the server must echo the patched content.
        stored = result.get("content") if isinstance(result, dict) else None
        stored_raw = stored.get("raw") if isinstance(stored, dict) else None
        if not isinstance(stored_raw, str):
            raise PatchError("server response has no content.raw; cannot confirm the write was stored")
        if normalise_block_text(stored_raw) != normalise_block_text(patched_raw):
            # content_save_pre filters may touch bytes outside the blocks; the
            # blocks themselves must still be exactly what was sent.
            def shape(raw: str) -> List[Any]:
                return [b.document if b.document is not None else normalise_block_text(b.text)
                        for b in find_blocks(raw)]
            if shape(stored_raw) != shape(patched_raw):
                hint = ""
                if "<script" in patched_raw and "<script" not in stored_raw:
                    hint = " (the <script> block was stripped: this user probably lacks unfiltered_html)"
                raise PatchError("server response does not reflect the patched content" + hint)
        if mode == "draft" and result.get("status") not in (None, "draft"):
            raise PatchError(f"unexpected status after draft update: {result.get('status')}")
        return mode, content.edit_link

    def stage_block_repair(
        self, content: Content, front_block: Block, target: Target, patch: Dict[str, Any]
    ) -> Tuple[str, str]:
        """One repair, one write. See prepare_block_repair and stage_content."""
        return self.stage_content(content, self.prepare_block_repair(content, front_block, target, patch))
