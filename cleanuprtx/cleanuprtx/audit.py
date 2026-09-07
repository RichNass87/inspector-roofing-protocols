"""Findings engine.

Each rule targets a specific defect Google flags and produces a Finding that
says what is wrong, where the JSON-LD comes from, and - only when the repair
is mechanical AND the block lives in post_content - the exact patch.

Rules read what Googlebot reads: the live front-end HTML. Rules never mutate.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import config
from .jsonld import (Block, Target, find_blocks, index_by_id, iter_nodes, match_block_in_raw,
                     node_fingerprint, node_types, references_outside, types_by_id)

CRITICAL = "critical"
WARNING = "warning"
NOTICE = "notice"

SOURCE_BODY = "body"       # block is in post_content - repairable
SOURCE_PLUGIN = "plugin"   # RankMath / Yoast / etc. generated it
SOURCE_HEAD = "head"       # in the document but not in post_content; theme or builder
SOURCE_UNKNOWN = "unknown" # post_content not fetched (no edit rights, read-only kind)

PROFILE_TYPES = {"ProfilePage"}
PERSON_TYPES = {"Person"}
ORG_TYPES = {"Organization", "LocalBusiness", "HomeAndConstructionBusiness",
             "RoofingContractor", "ProfessionalService", "Corporation", "NGO",
             "EducationalOrganization", "GovernmentOrganization", "OnlineBusiness"}
PROFILE_ENTITY_TYPES = PERSON_TYPES | ORG_TYPES

# Fields whose value must be a Person/Organization node (or a reference to one).
# mainEntity is deliberately absent: its schema.org range is Thing (FAQPage ->
# Question, WebPage -> Article) and Google constrains it only on ProfilePage,
# which rule_profile_parent_node handles.
OBJECT_FIELDS: Dict[str, Set[str]] = {
    "creator": PERSON_TYPES | ORG_TYPES,
    "author": PERSON_TYPES | ORG_TYPES,
    "publisher": ORG_TYPES | PERSON_TYPES,
    "founder": PERSON_TYPES,
    "copyrightHolder": PERSON_TYPES | ORG_TYPES,
}
DATE_FIELDS = ("dateModified", "datePublished", "dateCreated", "uploadDate")

# schema.org Date / DateTime, including reduced precision (YYYY, YYYY-MM).
_ISO_PARTS = re.compile(
    r"^(\d{4})(?:-(\d{2})(?:-(\d{2})"
    r"(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
    r"(?:Z|([+-])(\d{2}):?(\d{2}))?)?)?)?$"
)
_HAS_ZONE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def is_iso_datetime(value: str) -> bool:
    """Lexically ISO 8601 AND a real calendar moment."""
    m = _ISO_PARTS.match((value or "").strip())
    if not m:
        return False
    y, mo, d, hh, mm, ss, _sign, oh, om = m.groups()
    try:
        _dt.date(int(y), int(mo or 1), int(d or 1))
    except ValueError:
        return False
    if int(y) == 0:
        return False
    if hh is not None and not (int(hh) < 24 and int(mm) < 60 and (ss is None or int(ss) < 61)):
        return False
    if oh is not None and not (int(oh) <= 14 and int(om) < 60):
        return False
    return True


def _date_literals(value: Any) -> List[Any]:
    """Unwrap JSON-LD arrays and {"@value": ...} typed literals."""
    if isinstance(value, list):
        return [x for v in value for x in _date_literals(v)]
    if isinstance(value, dict) and "@value" in value:
        return [value["@value"]]
    return [value]


def _iso_utc(wp_gmt: str) -> Optional[str]:
    """WordPress *_gmt ('2026-08-01T12:00:00') -> schema.org DateTime with zone."""
    if not wp_gmt or wp_gmt.startswith("0000"):
        return None
    if not is_iso_datetime(wp_gmt):
        return None
    return wp_gmt if _HAS_ZONE.search(wp_gmt) else wp_gmt + "+00:00"


def _norm_name(s: str) -> str:
    return " ".join((s or "").split()).casefold()


@dataclass
class Finding:
    """One defect on one page."""

    rule: str
    severity: str
    kind: str
    page_id: int
    page_url: str
    page_title: str
    message: str
    detail: str = ""
    source: str = SOURCE_UNKNOWN
    plugin: str = ""
    block_index: int = -1
    target: Optional[Target] = None
    patch: Optional[Dict[str, Any]] = None
    hand_edit: str = ""          # where to fix it when the tool cannot
    breakdance_owned: bool = False
    reasons: List[str] = field(default_factory=list)

    @property
    def is_auto_fixable(self) -> bool:
        """Mechanical patch AND the block is provably in post_content."""
        return (self.patch is not None and self.target is not None
                and self.source == SOURCE_BODY)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule, "severity": self.severity, "kind": self.kind,
            "page_id": self.page_id, "page_url": self.page_url, "page_title": self.page_title,
            "message": self.message, "detail": self.detail, "source": self.source,
            "plugin": self.plugin, "block_index": self.block_index,
            "target": self.target.to_dict() if self.target else None,
            "patch": self.patch, "hand_edit": self.hand_edit, "reasons": list(self.reasons),
            "auto_fixable": self.is_auto_fixable, "breakdance_owned": self.breakdance_owned,
        }


@dataclass
class PageContext:
    """Everything the rules need about one page, computed once."""

    page: Any
    site: Optional[config.Site]
    blocks: List[Block]
    block_sources: Dict[int, Tuple[str, str]]   # index -> (source, plugin)
    raw_index: Dict[int, int]                   # live block index -> post_content block index
    nodes: List[Tuple[int, Tuple[Any, ...], Dict[str, Any]]]  # (block, path, node)
    by_id: Dict[str, Dict[str, Any]]
    types_by_id: Dict[str, Set[str]]
    shared_ids: Set[str]

    def finding(self, rule: str, severity: str, message: str, *, block: int = -1,
                path: Tuple[Any, ...] = (), node: Optional[Dict[str, Any]] = None,
                detail: str = "", patch: Optional[Dict[str, Any]] = None,
                hand_edit: str = "", reasons: Optional[List[str]] = None) -> Finding:
        source, plugin = self.block_sources.get(block, (SOURCE_UNKNOWN, ""))
        target = None
        if node is not None and block >= 0:
            node_id = node.get("@id") if isinstance(node.get("@id"), str) and node.get("@type") else ""
            target = Target(block=block, path=list(path), node_id=node_id or "",
                            node_types=node_types(node), raw_block=self.raw_index.get(block, -1),
                            shared_id=bool(node_id) and node_id in self.shared_ids,
                            fingerprint="" if node_id else node_fingerprint(node))
        if not hand_edit and source != SOURCE_BODY:
            hand_edit = default_hand_edit(plugin, rule, source)
        return Finding(
            rule=rule, severity=severity, kind=self.page.kind, page_id=self.page.id,
            page_url=self.page.link, page_title=self.page.title or "(untitled)",
            message=message, detail=detail, source=source, plugin=plugin,
            block_index=block, target=target, patch=patch, hand_edit=hand_edit,
            breakdance_owned=bool(getattr(self.page, "is_breakdance", False)),
            reasons=list(reasons or []),
        )


def default_hand_edit(plugin: str, rule: str, source: str = "") -> str:
    if plugin == "RankMath":
        if rule == "profile-parent-node":
            return ("Rank Math > Titles & Meta > Authors (enable author archives / Person "
                    "schema), or the page's Schema tab: set ProfilePage mainEntity.")
        if rule == "entity-fragmentation":
            return ("Rank Math > Titles & Meta > Local SEO (Person/Organization name and "
                    "URL); author @ids derive from the author archive URL.")
        return "Rank Math > edit the page > Schema tab (Schema Generator)."
    if plugin == "Yoast SEO":
        return "Yoast SEO > Search Appearance (Organization/Person), or the page's Schema tab."
    if plugin:
        return f"{plugin} settings for this page."
    if source == SOURCE_UNKNOWN:
        return ("post_content was not read for this item, so this block is either generated by the "
                "theme or page builder or sits in the editor; check where it is defined.")
    return ("Not found in post_content as served: generated by the theme or page builder, "
            "a synced pattern, or altered by a content filter. Edit it where it is defined.")


# --- Rules ---------------------------------------------------------------

def _resolve(ctx: PageContext, value: Any) -> Tuple[Set[str], str]:
    """Types of a referenced/inline object, and a reason if it cannot resolve."""
    if isinstance(value, dict):
        if value.get("@type"):
            return set(node_types(value)), ""
        ref = value.get("@id")
        if isinstance(ref, str):
            if ref not in ctx.by_id:
                return set(), f"@id {ref} is not defined on this page"
            return set(ctx.types_by_id.get(ref, ())), ""
        return set(), "object has no @type and no @id"
    return set(), "not an object"


def _canonical_match(site: Optional[config.Site], value: str) -> Optional[Tuple[str, str, str]]:
    """(canonical_id, name, kind) if value names or links the canonical person
    or organization; kind is 'Person' or 'Organization'."""
    if not site:
        return None
    v = value.strip()
    for cid, cname, kind, aliases in (
        (site.canonical_person_id, site.canonical_person_name, "Person", site.canonical_person_aliases),
        (site.canonical_org_id, site.canonical_org_name, "Organization", []),
    ):
        if not cid:
            continue
        base = cid.split("#")[0].rstrip("/")
        names = {_norm_name(n) for n in ([cname] if cname else []) + list(aliases)}
        if (v.rstrip("/").casefold() == base.casefold() or v.casefold() == cid.casefold()
                or (names and _norm_name(v) in names)):
            return cid, cname or "", kind
    return None


def _object_fix(ctx: PageContext, key: str, value: str) -> Optional[Dict[str, Any]]:
    if value in ctx.by_id:
        return {"@id": value}
    canon = _canonical_match(ctx.site, value)
    if canon:
        cid, cname, kind = canon
        return {"@id": cid} if cid in ctx.by_id else {"@type": kind, "@id": cid, "name": cname}
    if re.match(r"^https?://", value.strip()):
        return None   # a URL we do not recognise; do not invent an identity
    return {"@type": "Organization" if key == "publisher" else "Person", "name": value}


def _profile_fix(ctx: PageContext, main_value: Any) -> Optional[Dict[str, Any]]:
    """Repair for a ProfilePage whose mainEntity is missing or wrong.

    Keeps the identity the author named when mainEntity is a string. Otherwise
    the canonical person is used only on the canonical profile page itself,
    or when that node is already defined on the page.
    """
    site = ctx.site
    canonical = site.canonical_person_id if site else None
    if isinstance(main_value, str) and main_value.strip():
        candidate = _object_fix(ctx, "mainEntity", main_value)
        if candidate is not None and _resolve(ctx, candidate)[0] & PROFILE_ENTITY_TYPES:
            return {"op": "set", "key": "mainEntity", "expect": main_value, "value": candidate}
        return None
    if canonical and canonical in ctx.by_id:
        return {"op": "set", "key": "mainEntity", "value": {"@id": canonical}, "expect": main_value}
    if canonical and site and site.canonical_person_name:
        page_base = (ctx.page.link or "").split("#")[0].rstrip("/")
        canonical_base = canonical.split("#")[0].rstrip("/")
        if page_base and page_base.casefold() == canonical_base.casefold():
            return {"op": "set", "key": "mainEntity", "expect": main_value,
                    "value": {"@type": "Person", "@id": canonical,
                              "name": site.canonical_person_name, "url": canonical_base + "/"}}
    return None


def rule_profile_parent_node(ctx: PageContext) -> List[Finding]:
    """ProfilePage.mainEntity must resolve to a named Person or Organization.

    Google: 'Invalid object type for field "<parent_node>"' (critical) when
    present but wrong; 'Missing field "mainEntity"' when absent.
    """
    out: List[Finding] = []
    for block, path, node in ctx.nodes:
        if not (set(node_types(node)) & PROFILE_TYPES):
            continue
        main = node.get("mainEntity")
        if main is None:
            out.append(ctx.finding(
                "profile-parent-node", CRITICAL, "ProfilePage is missing mainEntity",
                block=block, path=path, node=node,
                detail="A ProfilePage must name the Person or Organization it profiles.",
                patch=_profile_fix(ctx, None)))
            continue
        items = main if isinstance(main, list) else [main]
        ok = False
        reasons: List[str] = []
        for item in items:
            if isinstance(item, str):
                reasons.append(f"mainEntity is the string {item!r}, not an object")
                continue
            types, why = _resolve(ctx, item)
            if types & PROFILE_ENTITY_TYPES:
                ok = True
                break
            reasons.append(why or f"mainEntity @type is {sorted(types)}, not Person/Organization")
        if ok:
            continue
        out.append(ctx.finding(
            "profile-parent-node", CRITICAL, "ProfilePage mainEntity is not a Person or Organization",
            block=block, path=path, node=node, detail="; ".join(reasons),
            patch=_profile_fix(ctx, main)))
    return out


def rule_object_fields(ctx: PageContext) -> List[Finding]:
    """creator/author/publisher/... must be Person/Organization objects.

    Google: 'Invalid object type for field "creator"'. A bare string - name or
    URL - is a text literal in the schema.org context, never a reference.
    """
    out: List[Finding] = []
    for block, path, node in ctx.nodes:
        for key, allowed in OBJECT_FIELDS.items():
            if key not in node:
                continue
            value = node[key]
            items = value if isinstance(value, list) else [value]
            problems: List[str] = []
            converted: List[Any] = []
            fixable = True
            for item in items:
                if isinstance(item, str):
                    problems.append(f"{key} is the string {item!r}")
                    fix = _object_fix(ctx, key, item)
                    if fix is None:
                        fixable = False
                    converted.append(fix)
                    continue
                types, why = _resolve(ctx, item)
                if isinstance(item, dict) and types & allowed:
                    converted.append(item)
                    continue
                expected = sorted(allowed & {"Person", "Organization"}) + ["..."]
                problems.append(why or f"{key} @type is {sorted(types)}, expected {expected}")
                fixable = False
                converted.append(item)
            if not problems:
                continue
            patch = None
            if fixable and all(c is not None for c in converted):
                new_value = converted if isinstance(value, list) else converted[0]
                patch = {"op": "set", "key": key, "value": new_value, "expect": value}
            out.append(ctx.finding(
                "object-field-type", WARNING, f'Field "{key}" is not a Person or Organization object',
                block=block, path=path, node=node,
                detail=f"{'/'.join(node_types(node)) or 'node'}: " + "; ".join(problems),
                patch=patch))
    return out


def rule_datetime_values(ctx: PageContext) -> List[Finding]:
    """Date fields must be real ISO 8601 moments. Google: 'Invalid datetime value'."""
    out: List[Finding] = []
    page = ctx.page
    for block, path, node in ctx.nodes:
        for key in DATE_FIELDS:
            if key not in node:
                continue
            for literal in _date_literals(node[key]):
                if isinstance(literal, str) and is_iso_datetime(literal):
                    continue
                reason = ("not ISO 8601" if isinstance(literal, str)
                          else f"a JSON {type(literal).__name__}, not a date string")
                replacement = None
                if key == "dateModified":
                    replacement = _iso_utc(getattr(page, "modified_gmt", ""))
                elif key == "datePublished":
                    replacement = _iso_utc(getattr(page, "date_gmt", ""))
                patch = None
                if replacement and not isinstance(node[key], list):
                    patch = {"op": "set", "key": key, "value": replacement, "expect": node[key]}
                out.append(ctx.finding(
                    "invalid-datetime", WARNING, f'Invalid datetime value for "{key}"',
                    block=block, path=path, node=node,
                    detail=f"Found {literal!r}: {reason}.", patch=patch))
                break
    return out


def _names(node: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for key in ("name", "alternateName", "legalName"):
        v = node.get(key)
        for s in (v if isinstance(v, list) else [v]):
            if isinstance(s, str):
                out.add(_norm_name(s))
    return out


def _urls(node: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for key in ("url", "sameAs", "@id"):
        v = node.get(key)
        for s in (v if isinstance(v, list) else [v]):
            if isinstance(s, str):
                out.add(s.split("#")[0].rstrip("/").casefold())
    return out


def rule_entity_fragmentation(ctx: PageContext) -> List[Finding]:
    """The canonical person/organization must use exactly one @id.

    Identity-gated: only nodes that name or link to the canonical entity are
    considered. Other people and organizations are left alone. A rename is
    offered only when no other block references the old @id without defining
    it, so no reference is left dangling.
    """
    site = ctx.site
    if not site or not (site.canonical_person_id or site.canonical_org_id):
        return []
    out: List[Finding] = []
    person_names = {_norm_name(n) for n in ([site.canonical_person_name] if site.canonical_person_name else [])
                    + list(site.canonical_person_aliases)}
    person_url = (site.canonical_person_id or "").split("#")[0].rstrip("/").casefold()
    org_names = {_norm_name(site.canonical_org_name)} if site.canonical_org_name else set()
    org_url = (site.canonical_org_id or "").split("#")[0].rstrip("/").casefold()

    def emit(block: int, path: Tuple[Any, ...], node: Dict[str, Any], node_id: str, new_id: str, what: str) -> None:
        dangling = references_outside(ctx.blocks, node_id, block)
        patch: Optional[Dict[str, Any]] = {"op": "rename_id", "old": node_id, "new": new_id}
        detail = f"Found {node_id}, expected {new_id}."
        if dangling:
            patch = None
            detail += (f" Referenced from block(s) {dangling} that do not define it; renaming here "
                       "would leave those references dangling, so this must be changed by hand in "
                       "every block at once.")
        out.append(ctx.finding(
            "entity-fragmentation", WARNING, f"Canonical {what} uses a non-canonical @id",
            block=block, path=path, node=node, detail=detail, patch=patch))

    for block, path, node in ctx.nodes:
        node_id = node.get("@id")
        if not isinstance(node_id, str) or not node.get("@type"):
            continue
        types = set(node_types(node))
        if site.canonical_person_id and types & PERSON_TYPES and node_id != site.canonical_person_id:
            if _names(node) & person_names or (person_url and person_url in _urls(node)):
                emit(block, path, node, node_id, site.canonical_person_id, "person")
        if site.canonical_org_id and types & ORG_TYPES and node_id != site.canonical_org_id:
            if _names(node) & org_names or (org_url and org_url in _urls(node) and _names(node) & org_names):
                emit(block, path, node, node_id, site.canonical_org_id, "organization")
    return out


def rule_stale_draft(ctx: PageContext, stale_days: int = 180) -> List[Finding]:
    """Drafts that look abandoned. Breakdance-aware: body length means nothing
    when the layout lives in post meta, so use title and age there."""
    page = ctx.page
    if page.status != "draft" or page.kind in ("media", "author"):
        return []
    title = (page.title or "").strip()
    reasons: List[str] = []
    if not title or title.lower() in ("auto draft", "(untitled)", "untitled"):
        reasons.append("untitled")
    age = None
    if page.modified_gmt:
        try:
            modified = _dt.datetime.fromisoformat(page.modified_gmt.replace("Z", ""))
            age = (_dt.datetime.utcnow() - modified).days
        except ValueError:
            pass
    if age is not None and age > stale_days:
        reasons.append(f"last edited {age} days ago")
        if page.date_gmt and page.modified_gmt[:16] == page.date_gmt[:16]:
            reasons.append("never edited after creation")
    if not getattr(page, "is_breakdance", False):
        text = re.sub(r"<[^>]+>", "", page.body_rendered or "").strip()
        if len(text) < 200:
            reasons.append(f"{len(text)} characters of body text")
    if not reasons:
        return []
    return [ctx.finding("stale-draft", NOTICE, "Draft looks abandoned",
                        detail="; ".join(reasons), reasons=reasons)]


def rule_unparseable_block(ctx: PageContext) -> List[Finding]:
    out = []
    for b in ctx.blocks:
        if b.parse_error:
            out.append(ctx.finding("invalid-jsonld", CRITICAL, "JSON-LD block does not parse",
                                   block=b.index, detail=b.parse_error))
    return out


RULES = [
    rule_unparseable_block,
    rule_profile_parent_node,
    rule_object_fields,
    rule_datetime_values,
    rule_entity_fragmentation,
    rule_stale_draft,
]


@dataclass
class AuditResult:
    findings: List[Finding] = field(default_factory=list)
    pages_scanned: int = 0
    pages_fetched: int = 0
    pages_with_jsonld: int = 0
    pages_unreachable: List[str] = field(default_factory=list)
    pages_redirected: List[str] = field(default_factory=list)
    plugins_seen: Dict[str, int] = field(default_factory=dict)

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def auto_fixable(self) -> List[Finding]:
        return [f for f in self.findings if f.is_auto_fixable]

    @property
    def hand_edits(self) -> List[Finding]:
        return [f for f in self.findings if not f.is_auto_fixable and f.rule != "stale-draft"]


def classify_blocks(page: Any, blocks: List[Block]) -> Tuple[Dict[int, Tuple[str, str]], Dict[int, int]]:
    """Decide, per block, whether post_content owns it. Returns (sources, raw_index)."""
    raw = getattr(page, "content_raw", None)
    sources: Dict[int, Tuple[str, str]] = {}
    raw_index: Dict[int, int] = {}
    for b in blocks:
        if b.plugin:
            sources[b.index] = (SOURCE_PLUGIN, b.plugin)
        elif raw is None:
            sources[b.index] = (SOURCE_UNKNOWN, "")
        else:
            match = match_block_in_raw(b, raw)
            if match is None:
                sources[b.index] = (SOURCE_HEAD, "")
            else:
                sources[b.index] = (SOURCE_BODY, "")
                raw_index[b.index] = match.index
    return sources, raw_index


def scan_source(page: Any) -> Tuple[str, bool]:
    """What HTML the rules read for this page, and whether it is the live page.

    Published items: the live front-end document. Unpublished items whose
    post_content was read: post_content itself (every block there is body-owned
    by definition).
    """
    if getattr(page, "front_html", None):
        return page.front_html, True
    raw = getattr(page, "content_raw", None)
    if raw and getattr(page, "status", "") != "publish":
        return raw, False
    return "", True


def build_context(page: Any, site: Optional[config.Site]) -> PageContext:
    html, live = scan_source(page)
    blocks = find_blocks(html)
    if live:
        sources, raw_index = classify_blocks(page, blocks)
    else:
        sources = {b.index: (SOURCE_PLUGIN, b.plugin) if b.plugin else (SOURCE_BODY, "") for b in blocks}
        raw_index = {b.index: b.index for b in blocks if not b.plugin}
    nodes: List[Tuple[int, Tuple[Any, ...], Dict[str, Any]]] = []
    for b in blocks:
        if b.document is not None:
            for path, node in iter_nodes(b.document):
                nodes.append((b.index, path, node))
    flat = [n for _, _, n in nodes]
    defs: Dict[str, Set[int]] = {}
    for block, _, node in nodes:
        nid = node.get("@id")
        if isinstance(nid, str) and node.get("@type"):
            defs.setdefault(nid, set()).add(block)
    return PageContext(
        page=page, site=site, blocks=blocks, block_sources=sources, raw_index=raw_index,
        nodes=nodes, by_id=index_by_id(flat), types_by_id=types_by_id(flat),
        shared_ids={i for i, bs in defs.items() if len(bs) > 1},
    )


def audit_pages(pages: Iterable[Any], site: Optional[config.Site] = None,
                stale_days: int = 180) -> AuditResult:
    """Run every rule over every page."""
    result = AuditResult()
    for page in pages:
        result.pages_scanned += 1
        redirected = getattr(page, "front_redirected_to", "")
        if redirected:
            result.pages_redirected.append(f"{page.link} -> {redirected}")
            continue
        if getattr(page, "front_html", None):
            result.pages_fetched += 1
        elif (getattr(page, "status", "") in ("publish", "inherit")
              and getattr(page, "front_status", 0) not in (0, 200) and getattr(page, "link", "")):
            result.pages_unreachable.append(f"{page.link} ({page.front_status})")
        ctx = build_context(page, site)
        if ctx.blocks:
            result.pages_with_jsonld += 1
            for _, plugin in ctx.block_sources.values():
                if plugin:
                    result.plugins_seen[plugin] = result.plugins_seen.get(plugin, 0) + 1
        for rule in RULES:
            if rule is rule_stale_draft:
                result.findings.extend(rule(ctx, stale_days))
            else:
                result.findings.extend(rule(ctx))
    return result
