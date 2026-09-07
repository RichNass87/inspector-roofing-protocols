"""Shared fixtures. No network, no Keychain."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from cleanuprtx import config
from cleanuprtx.wordpress import Content

SITE = config.SITES["inspector-roofing"]
PERSON = SITE.canonical_person_id
ORG = SITE.canonical_org_id


def ld(doc: Any, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f'<script type="application/ld+json"{c}>{json.dumps(doc)}</script>'


def page(front_doc: Any = None, *, raw_doc: Any = None, plugin_cls: str = "", status: str = "publish",
         kind: str = "pages", pid: int = 41, title: str = "Richard Amir Nasser",
         link: str = "https://inspector-roofing.com/richard-nasser/", raw: Optional[str] = "",
         modified: str = "2026-08-01T12:00:00", created: str = "2026-07-01T09:00:00",
         body: str = "", breakdance: bool = False, extra_front: str = "") -> Content:
    """A Content whose live page carries front_doc.

    raw_doc: JSON-LD present in post_content (source=body). None = post_content
    has no block. raw=None = post_content not fetched (unknown).
    """
    front = "<html><head><title>T</title>" + (ld(front_doc, plugin_cls) if front_doc is not None else "") \
            + extra_front + "</head><body>" + body + "</body></html>"
    if raw is not None:
        raw_html = raw
        if raw_doc is not None:
            raw_html = f"<!-- wp:html -->\n{ld(raw_doc)}\n<!-- /wp:html -->\n<p>{body}</p>"
    else:
        raw_html = None
    return Content(
        id=pid, kind=kind, post_type="page" if kind == "pages" else kind, slug="richard-nasser",
        link=link, title=title, status=status, date_gmt=created, modified_gmt=modified,
        body_rendered=body, raw={}, site_breakdance=breakdance,
        front_html=front if status == "publish" else None, front_status=200 if status == "publish" else 0,
        content_raw=raw_html,
    )


def body_page(doc: Any, **kw) -> Content:
    """Front and post_content carry the same block -> repairable."""
    return page(doc, raw_doc=doc, **kw)


def findings(result, rule: Optional[str] = None) -> List[Any]:
    return [f for f in result.findings if rule is None or f.rule == rule]
