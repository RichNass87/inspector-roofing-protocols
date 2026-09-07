"""cleanuprtx command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, config, report
from datetime import datetime, timezone

from .approvals import (APPLIED, APPROVED, PENDING, Ledger, LedgerError, defect_keys, is_staged,
                        make_repair_id, saved_since_staging)
from .audit import AuditResult, audit_pages, scan_source
from .gsc import ScopeError, SearchConsoleClient
from .http import HttpError, fetch_text
from .invocation import prog
from .jsonld import (AlreadyApplied, PatchError, Target, find_blocks, locate, match_block_in_raw,
                     node_fingerprint, page_title)
from .keychain import KeychainError, item_exists
from .wordpress import AUDIT_CAPS, WRITE_CAPS, AuthDiagnosis, Content, OwnershipLost, WordPressClient

SEO_PLUGINS = {"seo-by-rank-math": "Rank Math", "seo-by-rank-math-pro": "Rank Math Pro",
               "wordpress-seo": "Yoast SEO", "wordpress-seo-premium": "Yoast SEO Premium",
               "all-in-one-seo-pack": "All in One SEO", "wp-seopress": "SEOPress"}
WAF_PLUGINS = {"wordfence": "Wordfence", "sucuri-scanner": "Sucuri", "wp-cerber": "WP Cerber",
               "all-in-one-wp-security-and-firewall": "AIOS", "cloudflare": "Cloudflare",
               "better-wp-security": "Solid Security"}
NS_HINTS = {"rankmath/v1": "Rank Math", "yoast/v1": "Yoast SEO", "breakdance/v1": "Breakdance",
            "wordfence/v1": "Wordfence", "aioseo/v1": "All in One SEO"}
CACHE_DIR = Path.home() / ".cleanuprtx" / "cache"


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value}")
    return value


def _site(name: str) -> config.Site:
    if name not in config.SITES:
        raise SystemExit(f"Unknown site {name!r}. Known: {', '.join(sorted(config.SITES))}")
    return config.SITES[name]


def _sites_arg(name: str) -> Optional[str]:
    if name == "all":
        return None
    _site(name)
    return name


def _progress(kind: str, offset: int, total: int, per_page: int) -> None:
    if offset < 0:
        return
    tot = f"/{total}" if total >= 0 else ""
    print(f"\r  {kind:<22} {offset}{tot} items (batch {per_page})   ", end="", file=sys.stderr, flush=True)


def _warn(msg: str) -> None:
    print(f"\n  {msg}", file=sys.stderr)


# --- front-end cache ------------------------------------------------------

class FrontCache:
    """Live-page HTML, append-only, one JSON line per fetch, keyed by
    (link, modified_gmt). An interrupted audit resumes where it stopped.
    Read only under --resume; every put is O(one page); compaction streams."""

    def __init__(self, site_slug: str) -> None:
        self.path = CACHE_DIR / f"{site_slug}.jsonl"
        self.data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._fh = None

    def load(self) -> None:
        self._loaded = True
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("link"):
                        self.data[entry["link"]] = entry          # last line wins
        except OSError:
            pass

    def get(self, page: Content, max_age_s: float = 24 * 3600) -> Optional[Dict[str, Any]]:
        """Reused only within max_age (plugin or theme changes alter the head
        schema without touching modified_gmt) and only when it carries a
        result: a failed fetch is always retried - that is what --resume is for."""
        import time as _time
        if not self._loaded:
            self.load()
        entry = self.data.get(page.link)
        if not entry or entry.get("modified_gmt") != page.modified_gmt:
            return None
        if _time.time() - float(entry.get("at", 0)) >= max_age_s:
            return None
        if entry.get("html") is None and not entry.get("redirected_to"):
            return None
        return entry

    def put(self, page: Content) -> None:
        import time as _time
        entry = {"link": page.link, "modified_gmt": page.modified_gmt, "status": page.front_status,
                 "html": page.front_html, "redirected_to": page.front_redirected_to, "at": _time.time()}
        try:
            if self._fh is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.path.parent.chmod(0o700)
                except OSError:
                    pass
                fd = os.open(str(self.path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                self._fh = os.fdopen(fd, "a", encoding="utf-8")
            self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._fh.flush()
        except OSError:
            pass

    def flush(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def compact(self, keep_links: set) -> None:
        """After a complete, unrestricted audit, rewrite the file with one line
        per page that still exists (the last line written for it wins), so it
        does not grow by a site snapshot per run. Streams the file by byte
        offset, twice: no page's HTML is held in memory."""
        self.flush()
        try:
            last: Dict[str, Tuple[int, int]] = {}      # link -> (offset, length) of its last line
            with open(self.path, "rb") as fh:
                offset = 0
                for raw in fh:
                    n = len(raw)
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        entry = None
                    if isinstance(entry, dict) and entry.get("link") in keep_links:
                        last[entry["link"]] = (offset, n)
                    offset += n
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with open(self.path, "rb") as src, os.fdopen(fd, "wb") as dst:
                for offset, n in sorted(last.values()):
                    src.seek(offset)
                    dst.write(src.read(n))
            os.replace(tmp, self.path)
        except OSError:
            pass


# --- doctor -------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"cleanuprtx {__version__}\n")
    problems = 0

    sites = [config.SITES[args.site]] if args.site != "all" else list(config.SITES.values())
    print("Keychain items")
    for site in sorted(sites, key=lambda x: x.slug):
        ok = item_exists(site.wp_keychain_service, site.wp_account)
        problems += 0 if ok else 1
        print(f"  [{'ok     ' if ok else 'MISSING'}] WordPress app password - {site.slug} (login {site.wp_account})")
        if not ok:
            print(f"            {prog()} auth wordpress --site {site.slug}")
    for service, label in ((config.KC_GOOGLE_CLIENT_ID, "Google OAuth client ID"),
                           (config.KC_GOOGLE_CLIENT_SECRET, "Google OAuth client secret"),
                           (config.KC_GOOGLE_REFRESH_TOKEN, "Google refresh token")):
        ok = item_exists(service)
        problems += 0 if ok else 1
        print(f"  [{'ok     ' if ok else 'MISSING'}] {label}")
    if not item_exists(config.KC_GOOGLE_REFRESH_TOKEN):
        print(f"            {prog()} auth google-client   then   {prog()} auth google")

    for site in sites:
        print(f"\nWordPress - {site.slug} ({site.wp_base_url}, as {site.wp_account})")
        client = WordPressClient(site)
        try:
            who = client.verify()
            caps = who["capabilities"]
            print(f"  [ok     ] authenticated as {who['name']} ({', '.join(who['roles']) or 'no roles'})")
            missing_audit = sorted(c for c in AUDIT_CAPS if not caps.get(c))
            missing_write = sorted(c for c in WRITE_CAPS if not caps.get(c))
            if missing_audit:
                problems += 1
                print(f"  [WARN   ] missing: {', '.join(missing_audit)} - the inventory will miss "
                      "other authors' or private items")
            if missing_write:
                problems += 1
                print(f"  [WARN   ] missing: {', '.join(missing_write)} - staging repairs will fail"
                      + (" (unfiltered_html: WordPress strips <script> blocks on save)"
                         if "unfiltered_html" in missing_write else ""))
        except (KeychainError, AuthDiagnosis, HttpError) as exc:
            problems += 1
            print(f"  [FAILED ] {exc}")
            continue

        info = client.discover()
        plugins = {p["slug"]: p for p in info.get("plugins", [])}
        namespaces = info.get("namespaces", [])
        refused = info.get("plugins_error", "")
        seo_names = [SEO_PLUGINS[s] for s in SEO_PLUGINS if s in plugins]
        seo = [f"{SEO_PLUGINS[s]} {plugins[s]['version']}" for s in SEO_PLUGINS if s in plugins]
        if not seo_names:
            seo_names = [NS_HINTS[n] for n in namespaces if n in NS_HINTS
                         and NS_HINTS[n] in ("Rank Math", "Yoast SEO", "All in One SEO")]
            seo = list(seo_names)
        waf = [WAF_PLUGINS[s] for s in WAF_PLUGINS if s in plugins]
        if not waf and "wordfence/v1" in namespaces:
            waf = ["Wordfence"]
        builder_seen = "breakdance" in plugins or "breakdance/v1" in namespaces
        unknown = " (plugin list unavailable: " + refused + ")" if refused else ""
        print(f"  SEO plugin   {', '.join(seo) or ('unknown' + unknown if refused else 'none detected (schema comes from theme/hand-written blocks)')}")
        if builder_seen:
            print(f"  Page builder Breakdance {plugins.get('breakdance', {}).get('version', '')}".rstrip())
        elif refused:
            print(f"  Page builder unknown{unknown}")
        else:
            print("  Page builder none detected")
        if not refused and builder_seen != site.breakdance:
            print(f"  [WARN   ] config says breakdance={site.breakdance} but the site "
                  f"{'has' if builder_seen else 'does not have'} Breakdance")
        if waf:
            print(f"  Security     {', '.join(waf)}  " + ("<- likely source of Googlebot 403s" if site.slug != "inspector-roofing" else ""))
        elif refused:
            print(f"  Security     unknown{unknown}")
        if seo_names and seo_names[0] in ("Rank Math", "Rank Math Pro", "Yoast SEO", "Yoast SEO Premium"):
            print(f"  {'':12} note: schema generated by {seo_names[0]} is report-only; "
                  "the audit names the plugin screen to fix it in")

    print("\nGoogle Search Console")
    try:
        props = {p.get("siteUrl"): p.get("permissionLevel", "?") for p in SearchConsoleClient().list_properties()}
        print(f"  [ok     ] token valid, {len(props)} propert{'y' if len(props) == 1 else 'ies'} visible")
        for site in sites:
            for prop in site.gsc_properties:
                if prop not in props:
                    problems += 1
                    print(f"  [WARN   ] {prop} is not in this Google account - 'indexing' will 403")
                elif props[prop] in ("siteUnverifiedUser", "siteRestrictedUser"):
                    problems += 1
                    print(f"  [WARN   ] {prop}: {props[prop]} - URL Inspection refused")
                else:
                    print(f"  [ok     ] {prop} ({props[prop]})")
        extra = sorted(set(props) - {p for s in config.SITES.values() for p in s.gsc_properties})
        if extra:
            print(f"  {'':10} also visible: {', '.join(extra)}")
    except (KeychainError, ScopeError, HttpError) as exc:
        problems += 1
        print(f"  [FAILED ] {exc}")

    print(f"\n{'All checks passed.' if not problems else str(problems) + ' problem(s) found.'}")
    return 1 if problems else 0


# --- auth ---------------------------------------------------------------

def cmd_auth(args: argparse.Namespace) -> int:
    from . import auth
    try:
        if args.target == "wordpress":
            auth.store_wordpress_password(_site(args.site))
            print(f"Stored. Verify with:  {prog()} doctor --site {args.site}")
        elif args.target == "google-client":
            auth.store_google_client()
            print(f"Stored. Now run:  {prog()} auth google")
        elif args.target == "google":
            scope = auth.google_login(open_browser=not args.no_browser)
            print(f"\nConnected with scope: {scope}\nVerify with:  {prog()} doctor")
    except (auth.AuthError, KeychainError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    return 0


# --- audit --------------------------------------------------------------

def _static_page(url: str, html: str, status: int) -> Content:
    return Content(id=0, kind="url", post_type="url", slug="", link=url, title=page_title(html),
                   status="publish", date_gmt="", modified_gmt="", body_rendered="",
                   front_html=html or None, front_status=status, content_raw=None)


def _needs_post_content(page: Content) -> bool:
    """Only pages with at least one block that is not plugin-classed can hold
    a body-owned block worth classifying. Skipping the rest saves an
    authenticated request per Rank Math page."""
    if page.kind in ("media", "author"):
        return False
    return any(not b.plugin for b in find_blocks(page.front_html or ""))


def cmd_audit(args: argparse.Namespace) -> int:
    color = report.use_color(args.no_color)

    if args.url or args.html:
        pages: List[Content] = []
        for url in args.url or []:
            fetched = fetch_text(url)
            pages.append(_static_page(url, fetched.text if fetched.ok else "", fetched.status))
        for path in args.html or []:
            with open(path, encoding="utf-8") as handle:
                pages.append(_static_page(f"file://{path}", handle.read(), 200))
        site = config.SITES.get(args.site) if args.site in config.SITES else None
        result = audit_pages(pages, site=site, stale_days=args.stale_days)
        report.print_audit(result, args.site if site else "urls", color=color, limit=args.limit)
        if args.show_schema:
            for page in pages:
                for b in find_blocks(page.front_html or ""):
                    print(f"\n--- {page.link} block {b.index} {b.css_class}".rstrip())
                    print(json.dumps(b.document, indent=2) if b.document is not None else b.text)
        if args.json:
            report.write_json(result, args.json)
        return 0

    site = _site(args.site)
    client = WordPressClient(site)
    kinds = [k.strip() for k in args.types.split(",") if k.strip()] if args.types else None
    cache = FrontCache(site.slug)   # always written, so an interrupted run can resume
    if args.propose:
        Ledger()                    # surface a corrupt ledger before a long run

    print(f"Inventory from {site.wp_base_url} ...", file=sys.stderr)
    pages = []
    partial = ""
    try:
        for page in client.iter_all(kinds=kinds, progress=_progress, warn=_warn):
            pages.append(page)
    except HttpError as exc:
        partial = str(exc)
    except KeyboardInterrupt:
        # Nothing has been fetched or cached yet, so there is nothing to keep.
        print(f"\n  interrupted during inventory after {len(pages)} items; nothing fetched yet",
              file=sys.stderr)
        return 130
    print(file=sys.stderr)
    if partial:
        print(f"  inventory stopped early: {partial}\n  auditing the {len(pages)} items fetched so far",
              file=sys.stderr)

    public = [p for p in pages if p.is_public]
    print(f"Fetching {len(public)} live pages as Googlebot sees them ...", file=sys.stderr)
    fetched = 0
    try:
        for i, page in enumerate(public, 1):
            hit = cache.get(page) if args.resume else None
            if hit:
                page.front_status = hit.get("status", 0)
                page.front_html = hit.get("html")
                page.front_redirected_to = hit.get("redirected_to", "")
            else:
                client.load_front_html(page, delay=args.delay)
                if cache:
                    cache.put(page)
            fetched = i
            if i % 25 == 0 or i == len(public):
                print(f"\r  {i}/{len(public)}   ", end="", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        partial = partial or "interrupted while fetching live pages"
        print(f"\n  {fetched}/{len(public)} fetched; auditing what was fetched", file=sys.stderr)
    finally:
        if cache:
            cache.flush()
    print(file=sys.stderr)

    # post_content is read only where a body-owned block could exist.
    need_raw = [p for p in public if p.front_html and _needs_post_content(p)]
    if args.include_drafts:
        need_raw += [p for p in pages if not p.is_public and p.writable]
    raw_failed = 0
    if need_raw:
        print(f"Reading post_content for {len(need_raw)} items ...", file=sys.stderr)
        try:
            for page in need_raw:
                try:
                    client.load_content_raw(page)
                except HttpError as exc:
                    page.content_raw = None
                    raw_failed += 1
                    print(f"  post_content unreadable for {page.kind}/{page.id}: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            partial = partial or "interrupted while reading post_content"

    result = audit_pages(pages, site=site, stale_days=args.stale_days)
    report.print_audit(result, site.slug, color=color, limit=args.limit)
    if not partial and kinds is None:      # compact can only keep the links it is told about
        cache.compact({p.link for p in public})

    if args.json:
        report.write_json(result, args.json)
        print(f"\nWrote {args.json}")

    if args.propose:
        ledger = Ledger()
        with ledger.lock():            # re-reads under the lock: an apply may have saved since
            before = set(ledger.repairs)
            reported = set()
            modified = {(p.kind, p.id): p.modified_gmt for p in pages}
            for finding in result.auto_fixable:
                r = ledger.propose(site.slug, finding, modified_gmt=modified.get((finding.kind, finding.page_id), ""))
                if r is not None:
                    reported.add(r.repair_id)
            added = len(set(ledger.repairs) - before)
            # A page counts as audited for retirement only when its scan source was
            # read AND post_content was read where a body block could exist; a
            # failed context=edit read says nothing about the rows on that page.
            audited = {(p.kind, p.id) for p in pages
                       if scan_source(p)[0] and (p.content_raw is not None or not _needs_post_content(p))}
            retired = ledger.retire_unreported(site.slug, audited, reported) if not partial else []
            if not partial:
                # Staged repairs whose page was saved since and whose defect is gone
                # were restored or fixed by hand: record that, so a regression
                # re-proposes them instead of hiding behind the old autosave row.
                defects: set = set()
                for f in result.findings:
                    defects |= defect_keys(f.rule, f.target, f.patch)
                ledger.absorb_unreported(site.slug, audited, reported, defects, modified)
            ledger.save()
        print(f"\n{added} new repair(s) proposed ({len(ledger.in_state(PENDING, site.slug))} pending"
              + (f", {len(retired)} retired as no longer reported" if retired else "") + "). "
              f"Review with:  {prog()} pending")
    return 1 if partial else 0


def cmd_hand_edits(args: argparse.Namespace) -> int:
    """Re-read a saved --json audit and print the hand-edit list."""
    with open(args.json_path, encoding="utf-8") as handle:
        data = json.load(handle)
    from .audit import Finding
    result = AuditResult()
    for f in data.get("findings", []):
        t = f.get("target")
        result.findings.append(Finding(
            rule=f["rule"], severity=f["severity"], kind=f.get("kind", "pages"), page_id=f["page_id"],
            page_url=f["page_url"], page_title=f["page_title"], message=f["message"],
            detail=f.get("detail", ""), source=f.get("source", "unknown"), plugin=f.get("plugin", ""),
            block_index=f.get("block_index", -1), target=Target.from_dict(t) if t else None,
            patch=f.get("patch"), hand_edit=f.get("hand_edit", ""),
            breakdance_owned=f.get("breakdance_owned", False)))
    report.print_hand_edits(result, report.use_color(args.no_color))
    return 0


# --- approvals ----------------------------------------------------------

def cmd_pending(args: argparse.Namespace) -> int:
    ledger = Ledger()
    repairs = ledger.in_state(PENDING, _sites_arg(args.site))
    print(f"\n{len(repairs)} repair(s) awaiting your decision\n")
    report.print_repairs(repairs, color=report.use_color(args.no_color))
    if repairs:
        print(f"\nApprove with:  {prog()} approve <id> [<id> ...]")
        print(f"Reject with:   {prog()} reject <id> [<id> ...]")
    failed = ledger.in_state("failed", _sites_arg(args.site))
    if failed:
        print(f"\n{len(failed)} approved repair(s) failed on the last apply:")
        report.print_repairs(failed, color=report.use_color(args.no_color))
    return 0


def _decide(ids: List[str], approved: bool) -> int:
    ledger = Ledger()
    verb = "Approved" if approved else "Rejected"
    with ledger.lock():                # re-reads the ledger once held
        for repair_id in ids:
            try:
                r = ledger.decide(repair_id, approved)
            except (KeyError, ValueError) as exc:
                print(f"  {exc}")
                continue
            print(f"  {verb} {r.repair_id}: {r.message}")
        ledger.save()
    if approved:
        print(f"\nApply with:  {prog()} apply --dry-run   then   {prog()} apply")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, True)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, False)


def _live_to_raw(blocks: List[Any], raw: Optional[str]) -> Dict[int, int]:
    """Map live blocks to post_content blocks one-to-one, in order, exactly as
    the audit does - identical twins each get their own raw block."""
    mapping: Dict[int, int] = {}
    if not raw:
        return mapping
    claimed: set = set()
    for b in blocks:
        m = match_block_in_raw(b, raw, exclude=claimed)
        if m is not None:
            claimed.add(m.index)
            mapping[b.index] = m.index
    return mapping


def _resolve_front_block(page: Content, r: Any) -> Tuple[Optional[Any], Target, Optional[int]]:
    """Find the block holding the repair's node in the document the audit read.

    Never by position alone: plugins emit blocks ahead of the body block and
    shift indices. For id-less nodes the stored fingerprint must match, and
    where post_content is known the candidate must map to the same
    post_content block the repair was keyed on. Returns the live block, the
    target, and the post_content index the block maps to.
    """
    target = Target.from_dict(r.target)
    html = page.front_html if page.is_public else page.content_raw
    blocks = [b for b in find_blocks(html or "") if not b.plugin]
    raw_map = _live_to_raw(blocks, page.content_raw)

    def holds(b: Any) -> bool:
        if b.document is None:
            return False
        try:
            _, node = locate(b.document, target)
        except PatchError:
            return False
        if not target.node_id and target.fingerprint:
            return node_fingerprint(node) == target.fingerprint
        return True

    candidates = [b for b in blocks if holds(b)]
    if not candidates:
        return None, target, None
    if target.raw_block >= 0 and page.content_raw:
        owned = [b for b in candidates if raw_map.get(b.index) == target.raw_block]
        if not owned:
            return None, target, None
        candidates = owned
    at_index = next((b for b in candidates if b.index == r.block_index), None)
    chosen = at_index or candidates[0]
    return chosen, target, raw_map.get(chosen.index)


def _saved_since_staging(page: Content, r: Any) -> bool:
    """An autosave never changes the parent's modified_gmt, so any change since
    staging is a later save - after which WordPress no longer offers the older
    autosave and the staged repair may no longer fit."""
    return saved_since_staging(r, page.modified_gmt)


def cmd_apply(args: argparse.Namespace) -> int:
    """Stage approved repairs, one write per page. Never publishes."""
    color = report.use_color(args.no_color)
    ledger = Ledger()
    site_filter = _sites_arg(args.site)
    approved = ledger.in_state(APPROVED, site_filter)
    if not approved:
        print("No approved repairs to apply.")
        return 0
    if args.dry_run:
        print(f"{len(approved)} approved repair(s).\nDry run - nothing will be written.\n")
        report.print_repairs(approved, color=color)
        return 0

    clients: Dict[str, WordPressClient] = {}
    applied = skipped = failed = 0
    with ledger.lock():                      # re-reads the ledger once held
        approved = ledger.in_state(APPROVED, site_filter)
        print(f"{len(approved)} approved repair(s).")
        # Group by page: WordPress keeps ONE autosave per post per user, so every
        # repair on a page - this run's and any staged earlier but not yet
        # restored - must fold into a single write.
        groups: "OrderedDict[Tuple[str, str, int], List[Any]]" = OrderedDict()
        for r in approved:
            groups.setdefault((r.site, r.kind, r.page_id), []).append(r)
        carried: Dict[Tuple[str, str, int], List[Any]] = {}
        for r in ledger.repairs.values():
            key = (r.site, r.kind, r.page_id)
            if key in groups and is_staged(r):
                carried.setdefault(key, []).append(r)
        try:
            for (site_slug, kind, page_id), repairs in groups.items():
                site = config.SITES.get(site_slug)
                if site is None:
                    for r in repairs:
                        ledger.mark_stale(r.repair_id, f"site {site_slug!r} is no longer configured")
                        print(f"  {r.repair_id}: site {site_slug!r} is not in config.py - marked stale")
                    skipped += len(repairs)
                    continue
                client = clients.setdefault(site_slug, WordPressClient(site))
                try:
                    page = client.get_content(kind, page_id)
                    if page is None:
                        for r in repairs:
                            ledger.mark_stale(r.repair_id, "target no longer exists")
                            print(f"  {r.repair_id}: {kind}/{page_id} no longer exists - marked stale")
                        skipped += len(repairs)
                        continue
                    if page.is_public:
                        client.load_front_html(page)
                        if page.front_html is None:
                            raise HttpError(page.front_status, page.link,
                                            "live page could not be fetched; repairs stay queued")
                        if page.writable:
                            client.load_content_raw(page)   # ownership mapping needs it; refreshes status
                    elif page.writable:
                        # Unpublished: the audit read post_content (--include-drafts),
                        # so the block is resolved there. Also refreshes status.
                        client.load_content_raw(page)
                        if not page.writable:
                            raise PatchError(f"{kind}/{page_id} is now '{page.status}'; not written")
                    else:
                        for r in repairs:
                            ledger.mark_stale(r.repair_id, f"{kind}/{page_id} is {page.status}; not written")
                            print(f"  {r.repair_id}: {kind}/{page_id} is {page.status} - marked stale")
                        skipped += len(repairs)
                        continue
                except (HttpError, PatchError, KeychainError, AuthDiagnosis) as exc:
                    for r in repairs:
                        ledger.mark_failed(r.repair_id, str(exc))
                        print(f"  {r.repair_id}: FAILED - {exc}")
                    failed += len(repairs)
                    continue

                # Which of the repairs staged earlier for this page are still
                # defects on it? Ask the rules. A carried row the audit no longer
                # reports has been absorbed (restored and published, or fixed by
                # hand); one it still reports must be re-carried, with the fresh
                # patch the audit just built.
                carries = carried.get((site_slug, kind, page_id), [])
                blocked = ""
                if carries:
                    fresh = audit_pages([page], site=site)
                    reported: Dict[str, Any] = {}
                    # An id-less node is keyed by its block in post_content plus path,
                    # so the same live defect hashes to a new id when a block or a
                    # sibling node is inserted ahead of it: index by the defect too.
                    by_defect: Dict[Any, Any] = {}
                    for f in fresh.auto_fixable:
                        reported[make_repair_id(site_slug, f.kind, f.page_id, f.rule, f.target.key(), f.patch)] = f
                        for k in defect_keys(f.rule, f.target, f.patch):
                            by_defect.setdefault(k, f)
                    # Defects still reported but no longer repairable here (patch
                    # withdrawn because a block this tool does not own now refers
                    # to the @id, or the block left post_content). Built from the
                    # non-fixable findings only, so a still-fixable sibling repair on
                    # the same node cannot match.
                    withdrawn: Dict[Any, Any] = {}
                    for f in fresh.findings:
                        if f.target is not None and not f.is_auto_fixable:
                            for k in defect_keys(f.rule, f.target, None):
                                withdrawn.setdefault(k, f)
                    still: List[Any] = []
                    for r in carries:
                        f = reported.get(r.repair_id)
                        if f is None:
                            for k in defect_keys(r.rule, r.target, r.patch):
                                f = by_defect.get(k)
                                if f is not None:
                                    break
                        if f is not None:
                            r.patch, r.target, r.block_index = f.patch, f.target.to_dict(), f.block_index
                            still.append(r)
                            continue
                        w = None
                        for k in defect_keys(r.rule, r.target, None):
                            w = withdrawn.get(k)
                            if w is not None:
                                break
                        saved = _saved_since_staging(page, r)
                        if w is not None:
                            why = (f"earlier staged repair {r.repair_id} is still reported but can no longer "
                                   f"be applied by this tool ({w.detail or w.hand_edit or w.message})")
                            if saved:
                                ledger.mark_stale(r.repair_id, f"no longer fits after a later save: {why}")
                                print(f"  {r.repair_id}: earlier staged repair no longer fits the page - marked stale")
                                continue
                            blocked = why            # never stage a page without a repair it already holds
                            break
                        if saved:
                            # The page was saved since staging and the rules no longer
                            # report the defect: restored and published, or fixed by hand.
                            ledger.mark_applied(r.repair_id, "absorbed: no longer reported on the page after a later save")
                            print(f"  {r.repair_id}: earlier staged repair is now on the page")
                            continue
                        # Not saved since staging, so nothing can have been absorbed: the
                        # id changed for another reason. Carry the stored patch as it is
                        # and let the resolution below verify it against the page.
                        still.append(r)
                    carries = still

                # A rename changes the @id a later set on the same node is located
                # by, and a set may write a reference that only a later rename
                # rewrites: fold every set (carried or new) before any rename.
                ordered = sorted(carries + repairs, key=lambda r: (r.patch or {}).get("op") == "rename_id")
                working: Optional[str] = None
                folded: List[Any] = []
                recarried: List[Any] = []
                for r in ([] if blocked else ordered):
                    is_carry = r.state == APPLIED
                    if not isinstance(r.target, dict) or not isinstance(r.patch, dict):
                        why = "malformed repair row (target/patch is not an object); re-run 'audit --propose'"
                        ledger.mark_stale(r.repair_id, why)
                        print(f"  {r.repair_id}: {why} - marked stale")
                        skipped += 1
                        if is_carry:
                            blocked = f"earlier staged repair {r.repair_id} is malformed"
                            break
                        continue
                    tgt = Target.from_dict(r.target)
                    if tgt.raw_block < 0 or (not tgt.node_id and not tgt.fingerprint):
                        why = "repair predates this version's identity checks; re-run 'audit --propose'"
                        ledger.mark_stale(r.repair_id, why)
                        print(f"  {r.repair_id}: {why} - marked stale")
                        skipped += 1
                        if is_carry:
                            blocked = f"earlier staged repair {r.repair_id} cannot be verified"
                            break
                        continue
                    try:
                        front_block, target, raw_hint = _resolve_front_block(page, r)
                        if front_block is None:
                            raise OwnershipLost("JSON-LD block no longer in the page's schema")
                        working = client.prepare_block_repair(page, front_block, target, r.patch, working, raw_hint)
                        (recarried if is_carry else folded).append(r)
                    except AlreadyApplied as exc:
                        if working is not None:
                            # Judged against the fold so far: is the value on the LIVE
                            # page, or did an earlier repair in this fold produce it? If
                            # the latter, the row must ride the write and share its fate.
                            try:
                                client.prepare_block_repair(page, front_block, target, r.patch, None, raw_hint)
                            except AlreadyApplied:
                                pass                                       # on the page itself
                            except PatchError:
                                (recarried if is_carry else folded).append(r)  # the fold produced it
                                continue
                            else:
                                (recarried if is_carry else folded).append(r)  # the fold produced it
                                continue
                        ledger.mark_applied(r.repair_id, f"already correct: {exc}")
                        if is_carry:
                            print(f"  {r.repair_id}: earlier staged repair is now on the page")
                        else:
                            print(f"  {r.repair_id}: already correct on the page - marked applied")
                            applied += 1
                    except (HttpError, PatchError, ValueError, KeychainError, AuthDiagnosis) as exc:
                        if is_carry:
                            if isinstance(exc, (OwnershipLost, PatchError)) and not isinstance(exc, HttpError) \
                                    and _saved_since_staging(page, r):
                                # The page was saved after staging: WordPress no longer
                                # offers that autosave and the repair no longer fits.
                                ledger.mark_stale(r.repair_id, f"earlier staged repair no longer fits after a later save ({exc})")
                                print(f"  {r.repair_id}: earlier staged repair no longer fits the page - marked stale")
                                continue
                            # Transient, or the page is unchanged and something else is
                            # wrong: never stage a page without a repair it already holds.
                            blocked = f"earlier staged repair {r.repair_id} cannot be re-carried ({exc})"
                            break
                        if isinstance(exc, OwnershipLost):
                            ledger.mark_stale(r.repair_id, str(exc))
                            print(f"  {r.repair_id}: {exc} - marked stale")
                            skipped += 1
                        else:
                            ledger.mark_failed(r.repair_id, str(exc))
                            print(f"  {r.repair_id}: FAILED - {exc}")
                            failed += 1
                if blocked:
                    for r in repairs:
                        if r.state in (APPROVED, "failed"):
                            ledger.mark_failed(r.repair_id, blocked)
                            print(f"  {r.repair_id}: FAILED - {blocked}")
                            failed += 1
                    continue
                if not folded or working is None:
                    continue
                try:
                    mode, link = client.stage_content(page, working)
                except (HttpError, PatchError, KeychainError, AuthDiagnosis) as exc:
                    for r in folded:
                        ledger.mark_failed(r.repair_id, str(exc))
                        print(f"  {r.repair_id}: FAILED - {exc}")
                    failed += len(folded)
                    continue
                what = "autosave revision staged" if mode == "autosave" else "draft updated"
                for r in folded:
                    ledger.mark_applied(r.repair_id, f"{mode}: {link}", page.modified_gmt)
                    print(f"  {r.repair_id}: {what} - review at {link}")
                for r in recarried:
                    ledger.mark_applied(r.repair_id, f"{mode}: {link}", page.modified_gmt)
                applied += len(folded)
                if recarried:
                    print(f"  ({len(recarried)} earlier staged repair(s) carried into the same {mode})")
        finally:
            ledger.save()

    print(f"\n{applied} staged, {skipped} skipped, {failed} failed.")
    if applied:
        print("Staged changes on published pages are autosave revisions: open each edit link, "
              "look for the 'autosave' notice, restore it, check the page, publish by hand.")
    return 1 if (failed or skipped) else 0


# --- diagnostics --------------------------------------------------------

def cmd_indexing(args: argparse.Namespace) -> int:
    site = _site(args.site)
    if not site.gsc_properties:
        print(f"No Search Console property configured for {site.slug}.")
        return 1
    prop = args.property or site.gsc_properties[0]
    others = [p for p in site.gsc_properties if p != prop]
    if others and not args.property:
        print(f"Using {prop}; also configured: {', '.join(others)} (pass --property to switch).\n")

    gsc = SearchConsoleClient()
    urls = list(args.urls)
    if args.from_search_console:
        rows = gsc.list_pages(prop, days=args.days)
        rows.sort(key=lambda r: (-r["impressions"], r["page"]))
        print(f"{len(rows)} URL(s) with impressions in the last {args.days} days; "
              f"inspecting the top {min(args.max, len(rows))} by impressions "
              f"(URL Inspection allows 2,000/day per property)")
        urls.extend(r["page"] for r in rows[: args.max])
    if not urls:
        urls = [site.wp_base_url + "/"]

    print(f"Inspecting {len(urls)} URL(s) against {prop}\n")
    blocked = 0
    for url in urls:
        insp = gsc.safe_inspect(url, prop)
        print(f"  {url}")
        if insp.error:
            print(f"    error     {insp.error}")
            if insp.error.startswith("QUOTA"):
                break
            continue
        print(f"    verdict   {insp.verdict}   {insp.coverage_state}")
        print(f"    fetch     {insp.page_fetch_state or '?'}   robots {insp.robots_state or '?'}   crawled as {insp.crawled_as or '?'}")
        if insp.canonical and insp.canonical != url:
            print(f"    canonical {insp.canonical}")
        if insp.last_crawl:
            print(f"    last crawl {insp.last_crawl}")
        for issue in insp.rich_result_issues:
            print(f"    issue     {issue}")
        if insp.fetch_blocked:
            blocked += 1
            print("    ^ Google's own crawler was refused - this confirms the Search Console 403 report")
        elif insp.fetch_problem:
            print(f"    ^ Google could not use this page ({insp.page_fetch_state})")
        if insp.result_link:
            print(f"    details   {insp.result_link}")
    if blocked:
        print(f"\n{blocked} URL(s) blocked at fetch time according to Google's own crawl.")
    return 0


def cmd_forbidden(args: argparse.Namespace) -> int:
    from .probe import AGENTS, probe
    site = _site(args.site)
    print(f"Fetching {site.wp_base_url} as a browser and as three Google crawlers ...\n")
    data = probe(site.wp_base_url, list(args.urls) or None)
    robots = data["robots"]
    rows = data["rows"]

    names = [n for n, _ in AGENTS]
    print(f"  {'URL':<52} " + " ".join(f"{n:>11}" for n in names))
    hits = 0
    for row in rows:
        cells = " ".join(f"{(row.results[n].status or '---'):>11}" for n in names)
        flag = "  <- filtered by user agent" if row.filtered_by_ua else ""
        hits += bool(row.filtered_by_ua)
        print(f"  {row.url[:52]:<52} {cells}{flag}")
        if row.redirected_to:
            print(f"  {'':52} browser redirected to {row.redirected_to}")
        hdrs = row.notable_headers()
        if hdrs:
            print(f"  {'':52} " + ", ".join(f"{k}: {v}" for k, v in hdrs.items()))

    print()
    if robots.status != 200:
        print(f"robots.txt: HTTP {robots.status or 'unreachable'}")
    elif robots.googlebot_disallow_all:
        print("robots.txt BLOCKS Googlebot with 'Disallow: /':")
        for b in robots.blocks:
            print("    " + b.replace("\n", "\n    "))
    else:
        print(f"robots.txt: ok, {len(robots.sitemaps)} sitemap(s) declared")

    if hits:
        print(f"\n{hits} URL(s) answer the browser but refuse a Google crawler. That is a server, "
              "WAF or security-plugin rule keyed on the User-Agent - not a WordPress setting.")
    print("\nNote: a Googlebot user agent from a non-Google IP is treated as a fake bot by "
          "Cloudflare and Wordfence and may be challenged even when the real Googlebot passes. "
          f"Confirm with:  {prog()} indexing --site {site.slug} <url>   and read the 'fetch' line.")
    return 0


# --- parser -------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI color")

    parser = argparse.ArgumentParser(
        prog="cleanuprtx",
        description="Website and Search Console cleanup for Inspector Roofing. "
                    "Credentials are read from the macOS Keychain at run time.",
    )
    # Root gets its own action (default False); subparsers share `common`, whose
    # SUPPRESS default means they only set the attribute when the flag is given.
    parser.add_argument("--no-color", action="store_true", default=False, help="disable ANSI color")
    parser.add_argument("--version", action="version", version=f"cleanuprtx {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    site_choices = sorted(config.SITES)

    def add_site(p: argparse.ArgumentParser, allow_all: bool = False, default: str = config.DEFAULT_SITE) -> None:
        p.add_argument("--site", default=default, choices=site_choices + (["all"] if allow_all else []),
                       help=f"site to operate on (default: {default})")

    p = sub.add_parser("doctor", parents=[common], help="check credentials, connectivity and the site stack")
    add_site(p, allow_all=True, default="all")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("auth", parents=[common], help="store a credential in the Keychain")
    p.add_argument("target", choices=["wordpress", "google-client", "google"])
    p.add_argument("--site", default=config.DEFAULT_SITE, choices=site_choices)
    p.add_argument("--no-browser", action="store_true", help="print the sign-in URL instead of opening it")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("audit", parents=[common], help="scan what Googlebot sees (read-only)")
    add_site(p)
    p.add_argument("--url", action="append", metavar="URL", help="audit a URL instead of the WordPress inventory")
    p.add_argument("--html", action="append", metavar="PATH", help="audit a local HTML file")
    p.add_argument("--types", help="comma-separated REST bases to inventory, e.g. pages,posts")
    p.add_argument("--include-drafts", action="store_true",
                   help="also read post_content of drafts/pending/private items (one request each)")
    p.add_argument("--delay", type=float, default=0.25, help="seconds between front-end fetches")
    p.add_argument("--resume", action="store_true",
                   help="reuse live pages fetched in the last 24h (after an interrupted run)")
    p.add_argument("--stale-days", type=int, default=180)
    p.add_argument("--limit", type=int, default=40, help="findings shown per severity; 0 = all")
    p.add_argument("--json", metavar="PATH", help="also write findings as JSON")
    p.add_argument("--show-schema", action="store_true", help="print extracted JSON-LD (--url/--html)")
    p.add_argument("--propose", action="store_true", help="record repairable findings as pending repairs")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("hand-edits", parents=[common], help="list what must be fixed in the SEO plugin, from a --json audit")
    p.add_argument("json_path", metavar="AUDIT.json")
    p.set_defaults(func=cmd_hand_edits)

    p = sub.add_parser("pending", parents=[common], help="list repairs awaiting a decision")
    add_site(p, allow_all=True, default="all")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("approve", parents=[common], help="approve one or more repairs by ID")
    p.add_argument("repair_ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", parents=[common], help="reject one or more repairs by ID")
    p.add_argument("repair_ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("apply", parents=[common], help="stage approved repairs as autosave revisions (never publishes)")
    add_site(p, allow_all=True, default="all")
    p.add_argument("--dry-run", action="store_true", help="show what would be written and stop")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("indexing", parents=[common], help="ask Search Console about URLs")
    add_site(p)
    p.add_argument("--property", help="override the Search Console property")
    p.add_argument("--from-search-console", action="store_true", help="inspect URLs with impressions, most first")
    p.add_argument("--max", type=_positive_int, default=200, help="cap for --from-search-console, 1-2000 (quota is 2,000/day)")
    p.add_argument("--days", type=_positive_int, default=90)
    p.add_argument("urls", nargs="*", metavar="URL")
    p.set_defaults(func=cmd_indexing)

    p = sub.add_parser("forbidden", parents=[common], help="test whether Google's crawlers are being blocked")
    add_site(p)
    p.add_argument("urls", nargs="*", metavar="URL")
    p.set_defaults(func=cmd_forbidden)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeychainError as exc:
        print(f"\nCredential error: {exc}", file=sys.stderr)
        return 2
    except AuthDiagnosis as exc:
        print(f"\nWordPress authentication: {exc}", file=sys.stderr)
        return 2
    except LedgerError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 4
    except HttpError as exc:
        print(f"\nRequest failed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
