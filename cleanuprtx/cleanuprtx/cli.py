"""cleanuprtx command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from . import __version__, config, report
from .approvals import APPROVED, PENDING, Ledger, LedgerError
from .audit import AuditResult, audit_pages
from .gsc import ScopeError, SearchConsoleClient
from .http import HttpError, fetch_text
from .jsonld import PatchError, find_blocks, page_title
from .keychain import KeychainError, item_exists
from .wordpress import AuthDiagnosis, Content, WordPressClient

SEO_PLUGINS = {"seo-by-rank-math": "Rank Math", "seo-by-rank-math-pro": "Rank Math Pro",
               "wordpress-seo": "Yoast SEO", "wordpress-seo-premium": "Yoast SEO Premium",
               "all-in-one-seo-pack": "All in One SEO", "wp-seopress": "SEOPress"}
WAF_PLUGINS = {"wordfence": "Wordfence", "sucuri-scanner": "Sucuri", "wp-cerber": "WP Cerber",
               "all-in-one-wp-security-and-firewall": "AIOS", "cloudflare": "Cloudflare",
               "better-wp-security": "Solid Security"}
NS_HINTS = {"rankmath/v1": "Rank Math", "yoast/v1": "Yoast SEO", "breakdance/v1": "Breakdance",
            "wordfence/v1": "Wordfence", "aioseo/v1": "All in One SEO"}


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
    if offset < 0 or total == -1 and offset == 0:
        return
    tot = f"/{total}" if total >= 0 else ""
    print(f"\r  {kind:<22} {offset}{tot} items (batch {per_page})   ", end="", file=sys.stderr, flush=True)


# --- doctor -------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"cleanuprtx {__version__}\n")
    problems = 0

    print("Keychain items")
    for slug, site in sorted(config.SITES.items()):
        ok = item_exists(site.wp_keychain_service, site.wp_account)
        problems += 0 if ok else 1
        print(f"  [{'ok     ' if ok else 'MISSING'}] WordPress app password - {slug}")
        if not ok:
            print(f"            cleanuprtx auth wordpress --site {slug}")
    for service, label in ((config.KC_GOOGLE_CLIENT_ID, "Google OAuth client ID"),
                           (config.KC_GOOGLE_CLIENT_SECRET, "Google OAuth client secret"),
                           (config.KC_GOOGLE_REFRESH_TOKEN, "Google refresh token")):
        ok = item_exists(service)
        problems += 0 if ok else 1
        print(f"  [{'ok     ' if ok else 'MISSING'}] {label}")
    if not item_exists(config.KC_GOOGLE_REFRESH_TOKEN):
        print("            cleanuprtx auth google-client   then   cleanuprtx auth google")

    sites = [config.SITES[args.site]] if args.site != "all" else list(config.SITES.values())
    for site in sites:
        print(f"\nWordPress - {site.slug} ({site.wp_base_url})")
        client = WordPressClient(site)
        try:
            who = client.verify()
            caps = who["capabilities"]
            print(f"  [ok     ] authenticated as {who['name']} ({', '.join(who['roles']) or 'no roles'})")
            need = {"edit_pages", "edit_posts", "edit_published_pages", "edit_others_pages"}
            missing = sorted(c for c in need if not caps.get(c))
            if missing:
                problems += 1
                print(f"  [WARN   ] missing capabilities: {', '.join(missing)} - staging repairs on "
                      "published pages will fail")
        except (KeychainError, AuthDiagnosis, HttpError) as exc:
            problems += 1
            print(f"  [FAILED ] {exc}")
            continue

        info = client.discover()
        plugins = {p["slug"]: p for p in info.get("plugins", [])}
        seo = [f"{SEO_PLUGINS[s]} {plugins[s]['version']}" for s in SEO_PLUGINS if s in plugins]
        waf = [WAF_PLUGINS[s] for s in WAF_PLUGINS if s in plugins]
        if not seo:
            seo = [NS_HINTS[n] for n in info.get("namespaces", []) if n in NS_HINTS
                   and NS_HINTS[n] in ("Rank Math", "Yoast SEO", "All in One SEO")]
        builder = "breakdance" in plugins or "breakdance/v1" in info.get("namespaces", [])
        print(f"  SEO plugin   {', '.join(seo) or 'none detected (schema comes from theme/hand-written blocks)'}")
        print(f"  Page builder {'Breakdance ' + plugins.get('breakdance', {}).get('version', '') if builder else 'none detected'}")
        if builder != site.breakdance:
            print(f"  [WARN   ] config says breakdance={site.breakdance} but the site "
                  f"{'has' if builder else 'does not have'} Breakdance")
        if waf:
            print(f"  Security     {', '.join(waf)}  " + ("<- likely source of Googlebot 403s" if site.slug != "inspector-roofing" else ""))
        if info.get("plugins_error"):
            print(f"  {'':12} plugin list unavailable ({info['plugins_error']})")

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
            print(f"Stored. Verify with:  cleanuprtx doctor --site {args.site}")
        elif args.target == "google-client":
            auth.store_google_client()
            print("Stored. Now run:  cleanuprtx auth google")
        elif args.target == "google":
            scope = auth.google_login(open_browser=not args.no_browser)
            print(f"\nConnected with scope: {scope}\nVerify with:  cleanuprtx doctor")
    except (auth.AuthError, KeychainError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    return 0


# --- audit --------------------------------------------------------------

def _static_page(url: str, html: str, status: int) -> Content:
    return Content(id=0, kind="url", post_type="url", slug="", link=url, title=page_title(html),
                   status="publish", date_gmt="", modified_gmt="", body_rendered="",
                   front_html=html or None, front_status=status, content_raw=None)


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
    kinds = [k.strip() for k in args.types.split(",")] if args.types else None

    print(f"Inventory from {site.wp_base_url} ...", file=sys.stderr)
    pages = []
    partial = ""
    try:
        for page in client.iter_all(kinds=kinds, progress=_progress):
            pages.append(page)
    except HttpError as exc:
        partial = str(exc)
    except KeyboardInterrupt:
        partial = "interrupted"
    print(file=sys.stderr)
    if partial:
        print(f"  inventory stopped early: {partial}\n  auditing the {len(pages)} items fetched so far",
              file=sys.stderr)

    publishable = [p for p in pages if p.status == "publish" and p.link]
    print(f"Fetching {len(publishable)} live pages as Googlebot sees them ...", file=sys.stderr)
    for i, page in enumerate(publishable, 1):
        client.load_front_html(page, delay=args.delay)
        if i % 25 == 0 or i == len(publishable):
            print(f"\r  {i}/{len(publishable)}   ", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)

    # post_content is only needed for pages that carry JSON-LD, to classify ownership.
    with_ld = [p for p in publishable if p.front_html and find_blocks(p.front_html) and p.kind not in ("media", "author")]
    if with_ld:
        print(f"Reading post_content for {len(with_ld)} pages with JSON-LD ...", file=sys.stderr)
        for page in with_ld:
            try:
                client.load_content_raw(page)
            except HttpError:
                page.content_raw = None

    result = audit_pages(pages, site=site, stale_days=args.stale_days)
    report.print_audit(result, site.slug, color=color, limit=args.limit)

    if args.json:
        report.write_json(result, args.json)
        print(f"\nWrote {args.json}")

    if args.propose:
        ledger = Ledger()
        added = 0
        for finding in result.auto_fixable:
            r = ledger.propose(site.slug, finding)
            if r and r.state == PENDING and not r.decided_at:
                added += 1
        ledger.save()
        print(f"\n{added} new repair(s) proposed ({len(ledger.in_state(PENDING, site.slug))} pending). "
              "Review with:  cleanuprtx pending")
    return 1 if partial else 0


def cmd_hand_edits(args: argparse.Namespace) -> int:
    """Re-read a saved --json audit and print the hand-edit list."""
    with open(args.json_path, encoding="utf-8") as handle:
        data = json.load(handle)
    from .audit import Finding
    from .jsonld import Target
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
        print("\nApprove with:  cleanuprtx approve <id> [<id> ...]")
        print("Reject with:   cleanuprtx reject <id> [<id> ...]")
    failed = ledger.in_state("failed", _sites_arg(args.site))
    if failed:
        print(f"\n{len(failed)} approved repair(s) failed on the last apply:")
        report.print_repairs(failed, color=report.use_color(args.no_color))
    return 0


def _decide(ids: List[str], approved: bool) -> int:
    ledger = Ledger()
    verb = "Approved" if approved else "Rejected"
    for repair_id in ids:
        try:
            r = ledger.decide(repair_id, approved)
        except (KeyError, ValueError) as exc:
            print(f"  {exc}")
            continue
        print(f"  {verb} {r.repair_id}: {r.message}")
    ledger.save()
    if approved:
        print("\nApply with:  cleanuprtx apply --dry-run   then   cleanuprtx apply")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, True)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, False)


def cmd_apply(args: argparse.Namespace) -> int:
    """Stage approved repairs as autosave revisions / draft updates. Never publishes."""
    color = report.use_color(args.no_color)
    ledger = Ledger()
    approved = ledger.in_state(APPROVED, _sites_arg(args.site))
    if not approved:
        print("No approved repairs to apply.")
        return 0

    print(f"{len(approved)} approved repair(s).")
    if args.dry_run:
        print("Dry run - nothing will be written.\n")
        report.print_repairs(approved, color=color)
        return 0

    clients: Dict[str, WordPressClient] = {}
    applied = skipped = failed = 0
    try:
        for r in approved:
            client = clients.setdefault(r.site, WordPressClient(config.SITES[r.site]))
            try:
                page = client.get_content(r.kind, r.page_id)
                if page is None:
                    ledger.mark_stale(r.repair_id, "target no longer exists")
                    print(f"  {r.repair_id}: {r.kind}/{r.page_id} no longer exists - marked stale")
                    skipped += 1
                    continue
                client.load_front_html(page)
                blocks = find_blocks(page.front_html or "")
                front_block = next((b for b in blocks if b.index == r.block_index), None)
                if front_block is None or front_block.document is None:
                    # Block index may have shifted; find by node id.
                    from .jsonld import Target, locate
                    target = Target.from_dict(r.target)
                    front_block = None
                    for b in blocks:
                        try:
                            if b.document is not None:
                                locate(b.document, target)
                                front_block = b
                                break
                        except PatchError:
                            continue
                if front_block is None:
                    ledger.mark_stale(r.repair_id, "JSON-LD block no longer on the live page")
                    print(f"  {r.repair_id}: block no longer present - marked stale")
                    skipped += 1
                    continue
                from .jsonld import Target
                mode, link = client.stage_block_repair(page, front_block, Target.from_dict(r.target), r.patch)
                ledger.mark_applied(r.repair_id, f"{mode}: {link}")
                applied += 1
                what = "autosave revision staged" if mode == "autosave" else "draft updated"
                print(f"  {r.repair_id}: {what} - review at {link}")
            except (HttpError, PatchError, ValueError, KeychainError, AuthDiagnosis) as exc:
                ledger.mark_failed(r.repair_id, str(exc))
                failed += 1
                print(f"  {r.repair_id}: FAILED - {exc}")
    finally:
        ledger.save()

    print(f"\n{applied} staged, {skipped} skipped, {failed} failed.")
    if applied:
        print("Staged changes are autosave revisions on published pages: open each edit link, "
              "review the 'autosave' notice, restore it, and publish by hand.")
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
        urls.extend(r["page"] for r in rows)
        print(f"{len(rows)} URL(s) with impressions in the last {args.days} days")
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
          f"Confirm with:  cleanuprtx indexing --site {site.slug} <url>   and read the 'fetch' line.")
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
    p.add_argument("--delay", type=float, default=0.25, help="seconds between front-end fetches")
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
    p.add_argument("--from-search-console", action="store_true", help="inspect every URL with impressions")
    p.add_argument("--days", type=int, default=90)
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
