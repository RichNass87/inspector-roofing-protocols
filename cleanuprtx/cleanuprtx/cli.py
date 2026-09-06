"""cleanuprtx command line interface."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__, config, report
from .approvals import APPLIED, APPROVED, PENDING, Ledger
from .audit import audit_pages
from .gsc import SearchConsoleClient
from .http import HttpError
from .keychain import KeychainError, has_secret
from .wordpress import WordPressClient

GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


def _site(name: str) -> config.Site:
    if name not in config.SITES:
        raise SystemExit(
            f"Unknown site {name!r}. Known: {', '.join(sorted(config.SITES))}"
        )
    return config.SITES[name]


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report which credentials are present, without reading their values."""
    print(f"cleanuprtx {__version__}\n")
    print("Keychain items")
    checks = [
        (config.KC_WP_APP_PASSWORD, config.WP_ACCOUNT, "WordPress application password"),
        (config.KC_GOOGLE_CLIENT_ID, None, "Google OAuth client ID"),
        (config.KC_GOOGLE_CLIENT_SECRET, None, "Google OAuth client secret"),
        (config.KC_GOOGLE_REFRESH_TOKEN, None, "Google OAuth refresh token"),
    ]
    missing = 0
    for service, account, label in checks:
        ok = has_secret(service, account)
        missing += 0 if ok else 1
        mark = "ok     " if ok else "MISSING"
        print(f"  [{mark}] {label}")
        print(f"            service: {service}")

    if missing:
        print(f"\n{missing} item(s) missing. Add each one with:")
        print("  security add-generic-password -s <service> -a "
              f"{config.WP_ACCOUNT} -w")
        print("You will be prompted for the value; it is not echoed.")
    else:
        print("\nAll credentials present.")

    print("\nLive checks")
    site = _site(args.site)
    try:
        who = WordPressClient(site).verify()
        print(f"  [ok     ] WordPress authenticated as {who}")
    except (KeychainError, HttpError) as exc:
        print(f"  [FAILED ] WordPress: {exc}")

    try:
        props = SearchConsoleClient().list_properties()
        print(f"  [ok     ] Search Console reachable, "
              f"{len(props)} propert{'y' if len(props) == 1 else 'ies'}")
        for prop in props:
            print(f"            {prop.get('siteUrl')} "
                  f"({prop.get('permissionLevel', 'unknown')})")
    except (KeychainError, HttpError) as exc:
        print(f"  [FAILED ] Search Console: {exc}")

    return 1 if missing else 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Scan the site and report findings. Read-only."""
    site = _site(args.site)
    client = WordPressClient(site)

    print(f"Fetching content from {site.wp_base_url} ...", file=sys.stderr)
    pages = client.fetch_all()
    result = audit_pages(pages)

    report.print_audit(result, site.slug, color=not args.no_color)

    if args.json:
        report.write_json(result, args.json)
        print(f"\nWrote {args.json}")

    if args.propose:
        ledger = Ledger()
        added = 0
        for finding in result.auto_fixable:
            repair = ledger.propose(site.slug, finding)
            if repair and repair.state == PENDING:
                added += 1
        ledger.save()
        print(f"\n{added} repair(s) proposed. Review with:  cleanuprtx pending")

    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    ledger = Ledger()
    repairs = ledger.in_state(PENDING, args.site if args.site != "all" else None)
    print(f"\n{len(repairs)} repair(s) awaiting your decision\n")
    report.print_repairs(repairs, color=not args.no_color)
    if repairs:
        print("\nApprove with:  cleanuprtx approve <id> [<id> ...]")
        print("Reject with:   cleanuprtx reject <id> [<id> ...]")
    return 0


def _decide(ids: List[str], approved: bool) -> int:
    ledger = Ledger()
    verb = "Approved" if approved else "Rejected"
    for repair_id in ids:
        try:
            repair = ledger.decide(repair_id, approved)
        except (KeyError, ValueError) as exc:
            print(f"  {exc}")
            continue
        print(f"  {verb} {repair.repair_id}: {repair.message}")
    ledger.save()
    if approved:
        print("\nApply with:  cleanuprtx apply")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, True)


def cmd_reject(args: argparse.Namespace) -> int:
    return _decide(args.repair_ids, False)


def cmd_apply(args: argparse.Namespace) -> int:
    """Write approved repairs as WordPress drafts. Never publishes."""
    ledger = Ledger()
    approved = ledger.in_state(APPROVED, args.site if args.site != "all" else None)
    if not approved:
        print("No approved repairs to apply.")
        return 0

    print(f"{len(approved)} approved repair(s).")
    if args.dry_run:
        print("Dry run - nothing will be written.\n")
        report.print_repairs(approved, color=not args.no_color)
        return 0

    for repair in approved:
        site = config.SITES[repair.site]
        client = WordPressClient(site)
        page = next(
            (p for p in client.iter_content("pages") if p.id == repair.page_id),
            None,
        )
        if page is None:
            print(f"  {repair.repair_id}: page {repair.page_id} not found, skipping")
            continue
        try:
            link = client.create_draft_revision(page, repair.fix)
            ledger.mark_applied(repair.repair_id, link)
            print(f"  {repair.repair_id}: draft saved - {link}")
        except (HttpError, ValueError) as exc:
            print(f"  {repair.repair_id}: FAILED - {exc}")

    ledger.save()
    print("\nDrafts saved. Review and publish by hand in WordPress.")
    return 0


def cmd_indexing(args: argparse.Namespace) -> int:
    """Ask Google what it currently knows about a set of URLs."""
    site = _site(args.site)
    if not site.gsc_properties:
        print(f"No Search Console property configured for {site.slug}.")
        return 1

    prop = args.property or site.gsc_properties[0]
    gsc = SearchConsoleClient()
    urls = args.urls or [site.wp_base_url + "/"]

    print(f"Inspecting {len(urls)} URL(s) against {prop}\n")
    for url in urls:
        inspection = gsc.safe_inspect(url, prop)
        if inspection is None:
            print(f"  {url}\n    inspection unavailable (quota or permission)")
            continue
        print(f"  {url}")
        print(f"    verdict   {inspection.verdict}")
        print(f"    coverage  {inspection.coverage_state}")
        if inspection.canonical and inspection.canonical != url:
            print(f"    canonical {inspection.canonical}")
        for issue in inspection.rich_result_issues:
            print(f"    issue     {issue}")
    return 0


def cmd_forbidden(args: argparse.Namespace) -> int:
    """Reproduce the 403-to-Googlebot reports by fetching as Googlebot.

    Compares a normal browser user agent against Googlebot's. A 200 for one and
    a 403 for the other means a server or firewall rule is filtering by user
    agent, not a WordPress setting.
    """
    from .http import head_status

    site = _site(args.site)
    targets = args.urls or [
        site.wp_base_url + "/",
        site.wp_base_url + "/robots.txt",
        site.wp_base_url + "/sitemap_index.xml",
    ]

    print(f"Comparing browser and Googlebot responses for {site.slug}\n")
    print(f"  {'URL':<50} {'browser':>8} {'googlebot':>10}")
    for url in targets:
        browser = head_status(url)
        bot = head_status(url, user_agent=GOOGLEBOT_UA)
        flag = "  <-- filtered by user agent" if browser == 200 and bot in (401, 403) else ""
        print(f"  {url[:50]:<50} {browser:>8} {bot:>10}{flag}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleanuprtx",
        description="Website and Search Console cleanup for Inspector Roofing. "
                    "Credentials are read from the macOS Keychain at run time.",
    )
    parser.add_argument("--version", action="version", version=f"cleanuprtx {__version__}")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI color")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_site(p: argparse.ArgumentParser, allow_all: bool = False) -> None:
        p.add_argument(
            "--site",
            default=config.DEFAULT_SITE,
            help=f"site to operate on (default: {config.DEFAULT_SITE})",
        )

    p = sub.add_parser("doctor", help="check credentials and connectivity")
    add_site(p)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("audit", help="scan the site for defects (read-only)")
    add_site(p)
    p.add_argument("--json", metavar="PATH", help="also write findings as JSON")
    p.add_argument("--propose", action="store_true",
                   help="record auto-fixable findings as pending repairs")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("pending", help="list repairs awaiting a decision")
    p.add_argument("--site", default="all")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("approve", help="approve one or more repairs by ID")
    p.add_argument("repair_ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="reject one or more repairs by ID")
    p.add_argument("repair_ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("apply", help="write approved repairs as drafts")
    p.add_argument("--site", default="all")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be written and stop")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("indexing", help="inspect URLs in Search Console")
    add_site(p)
    p.add_argument("--property", help="override the Search Console property")
    p.add_argument("urls", nargs="*", metavar="URL")
    p.set_defaults(func=cmd_indexing)

    p = sub.add_parser("forbidden", help="test whether Googlebot is being blocked")
    add_site(p)
    p.add_argument("urls", nargs="*", metavar="URL")
    p.set_defaults(func=cmd_forbidden)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, "no_color"):
        args.no_color = False
    try:
        return args.func(args)
    except KeychainError as exc:
        print(f"\nCredential error: {exc}", file=sys.stderr)
        return 2
    except HttpError as exc:
        print(f"\nRequest failed: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
