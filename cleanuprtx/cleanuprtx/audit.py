"""Findings engine.

Each rule targets a specific defect and produces a Finding describing what is
wrong, why it matters, and - where the repair is mechanical - the exact field
change that would fix it. Rules never mutate anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional

from . import config

CRITICAL = "critical"
WARNING = "warning"
NOTICE = "notice"

# Schema.org date-time: a date, optionally with a time and offset.
ISO_DATE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:\d{2})?)?$"
)

# Types Google treats as profile pages.
PROFILE_TYPES = {"ProfilePage"}
# Fields that must hold an object or @id reference, never a bare string.
OBJECT_FIELDS = {"creator", "author", "publisher", "mainEntity", "founder"}


@dataclass
class Finding:
    """One defect on one page."""

    rule: str
    severity: str
    page_id: int
    page_url: str
    page_title: str
    message: str
    detail: str = ""
    fix: Optional[Dict[str, Any]] = None
    breakdance_owned: bool = False

    @property
    def is_auto_fixable(self) -> bool:
        """Only mechanical repairs on pages Breakdance does not own."""
        return self.fix is not None and not self.breakdance_owned

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "message": self.message,
            "detail": self.detail,
            "auto_fixable": self.is_auto_fixable,
            "breakdance_owned": self.breakdance_owned,
        }


class _LdExtractor(HTMLParser):
    """Pull application/ld+json blocks out of rendered HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: List[str] = []
        self._capturing = False

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        if tag.lower() != "script":
            return
        attrs_map = {k.lower(): (v or "").lower() for k, v in attrs}
        if attrs_map.get("type") == "application/ld+json":
            self._capturing = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script":
            self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing and data.strip():
            self.blocks.append(data.strip())


def extract_jsonld(html: str) -> List[Any]:
    """Return every parseable JSON-LD document in the page."""
    parser = _LdExtractor()
    try:
        parser.feed(html)
    except Exception:
        return []
    documents = []
    for block in parser.blocks:
        try:
            documents.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return documents


def iter_nodes(document: Any) -> Iterable[Dict[str, Any]]:
    """Walk a JSON-LD document, yielding every object node."""
    if isinstance(document, dict):
        yield document
        for value in document.values():
            yield from iter_nodes(value)
    elif isinstance(document, list):
        for item in document:
            yield from iter_nodes(item)


def node_types(node: Dict[str, Any]) -> List[str]:
    raw = node.get("@type", [])
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


# --- Rules ---------------------------------------------------------------


def rule_profile_parent_node(page: Any, nodes: List[Dict[str, Any]]) -> List[Finding]:
    """ProfilePage.mainEntity must be a Person or Organization object.

    Matches the critical Search Console error reported as
    'Invalid object type for field "<parent_node>"'.
    """
    findings = []
    for node in nodes:
        if not (set(node_types(node)) & PROFILE_TYPES):
            continue
        main = node.get("mainEntity")
        if main is None:
            findings.append(Finding(
                rule="profile-parent-node",
                severity=CRITICAL,
                page_id=page.id,
                page_url=page.link,
                page_title=page.title,
                message="ProfilePage has no mainEntity",
                detail=(
                    "Google reports this as 'Invalid object type for field "
                    "\"<parent_node>\"'. A ProfilePage must point at the Person "
                    "or Organization it profiles."
                ),
                fix={"mainEntity": {"@id": config.CANONICAL_PERSON_ID}},
                breakdance_owned=page.is_breakdance,
            ))
        elif isinstance(main, str):
            findings.append(Finding(
                rule="profile-parent-node",
                severity=CRITICAL,
                page_id=page.id,
                page_url=page.link,
                page_title=page.title,
                message="ProfilePage mainEntity is a string, not an object",
                detail=f"Found {main!r}. Schema.org requires a node or @id reference.",
                fix={"mainEntity": {"@id": main}},
                breakdance_owned=page.is_breakdance,
            ))
    return findings


def rule_object_fields(page: Any, nodes: List[Dict[str, Any]]) -> List[Finding]:
    """creator, author, publisher and friends must be objects, not strings.

    Matches 'Invalid object type for field "creator"' on Image Metadata.
    """
    findings = []
    for node in nodes:
        for key in OBJECT_FIELDS:
            value = node.get(key)
            if isinstance(value, str) and not value.startswith("http"):
                findings.append(Finding(
                    rule="object-field-type",
                    severity=WARNING,
                    page_id=page.id,
                    page_url=page.link,
                    page_title=page.title,
                    message=f'Field "{key}" is a bare string',
                    detail=(
                        f"{'/'.join(node_types(node)) or 'node'} sets {key}={value!r}. "
                        "Google reports this as an invalid object type."
                    ),
                    fix={key: {"@type": "Person", "name": value}},
                    breakdance_owned=page.is_breakdance,
                ))
    return findings


def rule_datetime_values(page: Any, nodes: List[Dict[str, Any]]) -> List[Finding]:
    """dateModified and datePublished must be ISO 8601."""
    findings = []
    for node in nodes:
        for key in ("dateModified", "datePublished", "dateCreated"):
            value = node.get(key)
            if isinstance(value, str) and not ISO_DATE.match(value.strip()):
                findings.append(Finding(
                    rule="invalid-datetime",
                    severity=WARNING,
                    page_id=page.id,
                    page_url=page.link,
                    page_title=page.title,
                    message=f'Invalid datetime value for "{key}"',
                    detail=f"Found {value!r}, which is not ISO 8601.",
                    fix={key: page.modified_gmt[:10]} if page.modified_gmt else None,
                    breakdance_owned=page.is_breakdance,
                ))
    return findings


def rule_entity_fragmentation(page: Any, nodes: List[Dict[str, Any]]) -> List[Finding]:
    """A Person or Organization node using a non-canonical @id splits the graph."""
    findings = []
    for node in nodes:
        types = set(node_types(node))
        node_id = node.get("@id")
        if not isinstance(node_id, str):
            continue

        if "Person" in types and node_id != config.CANONICAL_PERSON_ID:
            if "inspector-roofing.com" in node_id:
                findings.append(Finding(
                    rule="entity-fragmentation",
                    severity=WARNING,
                    page_id=page.id,
                    page_url=page.link,
                    page_title=page.title,
                    message="Person node uses a non-canonical @id",
                    detail=(
                        f"Found {node_id}, expected {config.CANONICAL_PERSON_ID}. "
                        "A second @id makes Google treat this as a different person."
                    ),
                    fix={"@id": config.CANONICAL_PERSON_ID},
                    breakdance_owned=page.is_breakdance,
                ))
        if "Organization" in types and node_id != config.CANONICAL_ORG_ID:
            if "inspector-roofing.com" in node_id:
                findings.append(Finding(
                    rule="entity-fragmentation",
                    severity=WARNING,
                    page_id=page.id,
                    page_url=page.link,
                    page_title=page.title,
                    message="Organization node uses a non-canonical @id",
                    detail=(
                        f"Found {node_id}, expected {config.CANONICAL_ORG_ID}."
                    ),
                    fix={"@id": config.CANONICAL_ORG_ID},
                    breakdance_owned=page.is_breakdance,
                ))
    return findings


def rule_empty_draft(page: Any, nodes: List[Dict[str, Any]]) -> List[Finding]:
    """Flag drafts with no body - the 729 stale drafts Colten inventoried."""
    if page.status != "draft":
        return []
    text = re.sub(r"<[^>]+>", "", page.rendered_html).strip()
    if len(text) > 200:
        return []
    return [Finding(
        rule="empty-draft",
        severity=NOTICE,
        page_id=page.id,
        page_url=page.link,
        page_title=page.title or "(untitled)",
        message="Draft has little or no content",
        detail=f"{len(text)} characters of body text. Likely abandoned.",
        fix=None,
        breakdance_owned=page.is_breakdance,
    )]


RULES = [
    rule_profile_parent_node,
    rule_object_fields,
    rule_datetime_values,
    rule_entity_fragmentation,
    rule_empty_draft,
]


@dataclass
class AuditResult:
    findings: List[Finding] = field(default_factory=list)
    pages_scanned: int = 0
    pages_with_jsonld: int = 0

    def by_severity(self, severity: str) -> List[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def auto_fixable(self) -> List[Finding]:
        return [f for f in self.findings if f.is_auto_fixable]


def audit_pages(pages: Iterable[Any]) -> AuditResult:
    """Run every rule over every page."""
    result = AuditResult()
    for page in pages:
        result.pages_scanned += 1
        documents = extract_jsonld(page.rendered_html)
        if documents:
            result.pages_with_jsonld += 1
        nodes = [n for doc in documents for n in iter_nodes(doc)]
        for rule in RULES:
            result.findings.extend(rule(page, nodes))
    return result
