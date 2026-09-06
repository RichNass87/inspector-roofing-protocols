"""Terminal and JSON output."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Iterable, List

from .audit import CRITICAL, NOTICE, WARNING, AuditResult

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
GREEN = "\033[32m"

SEVERITY_STYLE = {CRITICAL: RED, WARNING: YELLOW, NOTICE: BLUE}
SEVERITY_LABEL = {CRITICAL: "CRITICAL", WARNING: "WARNING", NOTICE: "NOTICE"}


def _color(enabled: bool, code: str) -> str:
    return code if enabled else ""


def print_audit(result: AuditResult, site: str, color: bool = True) -> None:
    c = lambda code: _color(color, code)

    print(f"\n{c(BOLD)}Audit - {site}{c(RESET)}")
    print(f"{c(DIM)}{'-' * 60}{c(RESET)}")
    print(f"  Pages scanned      {result.pages_scanned}")
    print(f"  Pages with JSON-LD {result.pages_with_jsonld}")
    print(f"  Findings           {len(result.findings)}")

    for severity in (CRITICAL, WARNING, NOTICE):
        group = result.by_severity(severity)
        if not group:
            continue
        style = c(SEVERITY_STYLE[severity])
        print(f"\n{style}{c(BOLD)}{SEVERITY_LABEL[severity]}{c(RESET)} "
              f"{c(DIM)}({len(group)}){c(RESET)}")
        for finding in group[:40]:
            flag = f" {c(DIM)}[breakdance]{c(RESET)}" if finding.breakdance_owned else ""
            print(f"  {style}*{c(RESET)} {finding.message}{flag}")
            print(f"    {c(DIM)}{finding.page_title[:60]} - {finding.page_url}{c(RESET)}")
            if finding.detail:
                print(f"    {c(DIM)}{finding.detail}{c(RESET)}")
        if len(group) > 40:
            print(f"  {c(DIM)}... and {len(group) - 40} more{c(RESET)}")

    fixable = result.auto_fixable
    print(f"\n{c(GREEN)}{len(fixable)}{c(RESET)} of {len(result.findings)} "
          f"findings can be repaired automatically.")
    blocked = [f for f in result.findings if f.fix and f.breakdance_owned]
    if blocked:
        print(f"{c(DIM)}{len(blocked)} more have a known fix but live on "
              f"Breakdance-owned pages and must be edited in the builder.{c(RESET)}")


def print_repairs(repairs: List[Any], color: bool = True) -> None:
    c = lambda code: _color(color, code)
    if not repairs:
        print(f"{c(DIM)}No repairs in this state.{c(RESET)}")
        return
    for repair in repairs:
        print(f"  {c(BOLD)}{repair.repair_id}{c(RESET)}  {repair.message}")
        print(f"    {c(DIM)}{repair.page_title[:60]}{c(RESET)}")
        print(f"    {c(DIM)}{repair.page_url}{c(RESET)}")
        print(f"    {c(DIM)}set {json.dumps(repair.fix)}{c(RESET)}")


def write_json(result: AuditResult, path: str) -> None:
    payload = {
        "pages_scanned": result.pages_scanned,
        "pages_with_jsonld": result.pages_with_jsonld,
        "findings": [f.to_dict() for f in result.findings],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
