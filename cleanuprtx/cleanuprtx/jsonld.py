"""JSON-LD extraction, addressing and patching.

Pure functions, no network. Used by the audit to read what Google sees and by
the writer to splice a corrected block back into post_content without
disturbing any other byte of it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Tuple

LD_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*)\btype\s*=\s*[\"']application/ld\+json[\"'](?P<attrs2>[^>]*)>"
    r"(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
CLASS_RE = re.compile(r"\bclass\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)

# Well-known markers plugins put on the blocks they generate.
PLUGIN_CLASSES = {
    "rank-math-schema": "RankMath",
    "rank-math-schema-pro": "RankMath",
    "yoast-schema-graph": "Yoast SEO",
    "aioseo-schema": "All in One SEO",
    "seopress-schema": "SEOPress",
}


@dataclass
class Block:
    """One <script type="application/ld+json"> as found in a document."""

    index: int
    text: str
    start: int
    end: int
    css_class: str = ""
    document: Any = None
    parse_error: str = ""

    @property
    def plugin(self) -> str:
        for cls in self.css_class.split():
            if cls in PLUGIN_CLASSES:
                return PLUGIN_CLASSES[cls]
        return ""


def find_blocks(html: str) -> List[Block]:
    """Locate every JSON-LD block with its exact character span."""
    blocks: List[Block] = []
    for i, m in enumerate(LD_SCRIPT_RE.finditer(html or "")):
        attrs = (m.group("attrs") or "") + " " + (m.group("attrs2") or "")
        cls = CLASS_RE.search(attrs)
        body = m.group("body")
        block = Block(
            index=i, text=body, start=m.start("body"), end=m.end("body"),
            css_class=cls.group(1) if cls else "",
        )
        try:
            block.document = json.loads(body)
        except json.JSONDecodeError as exc:
            block.parse_error = str(exc)
        blocks.append(block)
    return blocks


def normalise_block_text(text: str) -> str:
    """Whitespace-insensitive form used to match a front-end block to raw
    post_content. WordPress may reflow whitespace but not tokens."""
    return re.sub(r"\s+", "", text or "")


def iter_nodes(document: Any, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """Walk a JSON-LD document yielding (path, node) for every object node."""
    if isinstance(document, dict):
        yield path, document
        for key, value in document.items():
            yield from iter_nodes(value, path + (key,))
    elif isinstance(document, list):
        for i, item in enumerate(document):
            yield from iter_nodes(item, path + (i,))


def node_types(node: Dict[str, Any]) -> List[str]:
    raw = node.get("@type", [])
    if isinstance(raw, str):
        return [raw]
    return [t for t in raw if isinstance(t, str)] if isinstance(raw, list) else []


def index_by_id(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map @id -> node for nodes that carry a type (a bare {"@id": x} is a
    reference, not a definition)."""
    out: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("@id")
        if isinstance(node_id, str) and node.get("@type") and node_id not in out:
            out[node_id] = node
    return out


def get_at(document: Any, path: Tuple[Any, ...]) -> Any:
    cur = document
    for step in path:
        cur = cur[step]
    return cur


@dataclass
class Target:
    """Address of one node inside one block."""

    block: int
    path: List[Any] = field(default_factory=list)
    node_id: str = ""
    node_types: List[str] = field(default_factory=list)

    def key(self) -> str:
        """Stable identity for hashing repairs. Prefer @id (stable across
        renders); fall back to block + path."""
        return self.node_id or f"{self.block}:{'/'.join(map(str, self.path))}"

    def to_dict(self) -> Dict[str, Any]:
        return {"block": self.block, "path": list(self.path),
                "node_id": self.node_id, "node_types": list(self.node_types)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Target":
        return cls(block=int(data.get("block", 0)), path=list(data.get("path", [])),
                   node_id=str(data.get("node_id", "")),
                   node_types=list(data.get("node_types", [])))


class PatchError(ValueError):
    """The patch could not be applied safely."""


def locate(document: Any, target: Target) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Find the target node in a (possibly re-fetched) document.

    Match by @id first, then by path with a type check. Raises PatchError if
    the document no longer contains the node the audit saw.
    """
    if target.node_id:
        for path, node in iter_nodes(document):
            if node.get("@id") == target.node_id and node.get("@type"):
                return path, node
        raise PatchError(f"node {target.node_id} is no longer present in the block")
    try:
        node = get_at(document, tuple(target.path))
    except (KeyError, IndexError, TypeError):
        raise PatchError("node path no longer resolves; content changed since audit")
    if not isinstance(node, dict):
        raise PatchError("node path no longer points at an object")
    if target.node_types and not (set(node_types(node)) & set(target.node_types)):
        raise PatchError("node at path has a different @type than at audit time")
    return tuple(target.path), node


def apply_patch(document: Any, target: Target, patch: Dict[str, Any]) -> Any:
    """Return a patched deep copy of the document.

    patch forms:
      {"op": "set", "key": k, "value": v, "expect": <current value or absent>}
      {"op": "rename_id", "old": iri, "new": iri}   - node @id and every
                                                      {"@id": old} reference
    """
    doc = json.loads(json.dumps(document))
    op = patch.get("op")
    if op == "set":
        _, node = locate(doc, target)
        key = patch["key"]
        if "expect" in patch and node.get(key) != patch["expect"]:
            raise PatchError(
                f"{key} changed since audit (expected {patch['expect']!r}, "
                f"found {node.get(key)!r})"
            )
        node[key] = json.loads(json.dumps(patch["value"]))
        return doc
    if op == "rename_id":
        old, new = patch["old"], patch["new"]
        _, node = locate(doc, target)
        if node.get("@id") != old:
            raise PatchError(f"node @id is {node.get('@id')!r}, expected {old!r}")
        count = 0
        for _, n in iter_nodes(doc):
            if n.get("@id") == old:
                n["@id"] = new
                count += 1
        if count == 0:
            raise PatchError("rename found nothing to rename")
        return doc
    raise PatchError(f"unknown patch op {op!r}")


def splice_block(raw_html: str, block: Block, new_document: Any) -> str:
    """Replace one block's JSON text inside raw_html, leaving every other byte
    of the document exactly as it was."""
    indent = 2 if "\n" in block.text.strip() else None
    new_text = json.dumps(new_document, ensure_ascii=False, indent=indent)
    return raw_html[: block.start] + new_text + raw_html[block.end:]


def match_block_in_raw(front_block: Block, raw_html: str) -> Optional[Block]:
    """Find the block in post_content that produced front_block on the live
    page, or None if the plugin/theme generated it.

    This is the ownership test that gates every write: a block we cannot find
    in post_content is not ours to rewrite.
    """
    if not raw_html:
        return None
    want = normalise_block_text(front_block.text)
    if not want:
        return None
    for raw_block in find_blocks(raw_html):
        if normalise_block_text(raw_block.text) == want:
            return raw_block
    # Fall back to a semantic match: same parsed document.
    if front_block.document is not None:
        for raw_block in find_blocks(raw_html):
            if raw_block.document == front_block.document:
                return raw_block
    return None


class TitleParser(HTMLParser):
    """Pull <title> out of a document."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag.lower() == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def page_title(html: str) -> str:
    parser = TitleParser()
    try:
        parser.feed(html or "")
    except Exception:
        return ""
    return " ".join(parser.title.split())
