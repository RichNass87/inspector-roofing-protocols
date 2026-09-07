"""JSON-LD extraction, addressing and patching.

Pure functions, no network. Used by the audit to read what Google sees and by
the writer to splice a corrected block back into post_content without
disturbing any other byte of it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

LD_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*)\btype\s*=\s*[\"']application/ld\+json[\"'](?P<attrs2>[^>]*)>"
    r"(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
CLASS_RE = re.compile(r"\bclass\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A JSON string literal (escapes included) or a run of whitespace between tokens.
_STRING_OR_WS_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\s+')

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
    """Locate every live JSON-LD block with its exact character span.

    Blocks inside HTML comments are skipped: no browser or crawler parses
    them, so they are neither audited nor eligible as write targets.
    """
    html = html or ""
    comments = [(c.start(), c.end()) for c in COMMENT_RE.finditer(html)]
    blocks: List[Block] = []
    for m in LD_SCRIPT_RE.finditer(html):
        if any(cs <= m.start() < ce for cs, ce in comments):
            continue
        attrs = (m.group("attrs") or "") + " " + (m.group("attrs2") or "")
        cls = CLASS_RE.search(attrs)
        body = m.group("body")
        block = Block(
            index=len(blocks), text=body, start=m.start("body"), end=m.end("body"),
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
    post_content. WordPress may reflow whitespace between tokens but never
    inside a string literal, so whitespace inside strings is kept."""
    return _STRING_OR_WS_RE.sub(lambda m: m.group(0) if m.group(0)[0] == '"' else "", text or "")


def iter_nodes(document: Any, path: Tuple[Any, ...] = ()) -> Iterator[Tuple[Tuple[Any, ...], Dict[str, Any]]]:
    """Walk a JSON-LD document yielding (path, node) for every object node.

    @context is skipped: its value is a URL, a list, or a term-definition map,
    never a node.
    """
    if isinstance(document, dict):
        yield path, document
        for key, value in document.items():
            if key == "@context":
                continue
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
    """Map @id -> first defining node (one that carries a @type)."""
    out: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("@id")
        if isinstance(node_id, str) and node.get("@type") and node_id not in out:
            out[node_id] = node
    return out


def types_by_id(nodes: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """Map @id -> union of @type over every definition. In JSON-LD two nodes
    with one @id are one node; Google merges their types."""
    out: Dict[str, Set[str]] = {}
    for node in nodes:
        node_id = node.get("@id")
        if isinstance(node_id, str) and node.get("@type"):
            out.setdefault(node_id, set()).update(node_types(node))
    return out


def get_at(document: Any, path: Tuple[Any, ...]) -> Any:
    cur = document
    for step in path:
        cur = cur[step]
    return cur


def node_fingerprint(node: Dict[str, Any]) -> str:
    """Short content hash of a node, used to identify id-less nodes stably."""
    canon = json.dumps(node, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


@dataclass
class Target:
    """Address of one node inside one block."""

    block: int
    path: List[Any] = field(default_factory=list)
    node_id: str = ""
    node_types: List[str] = field(default_factory=list)
    raw_block: int = -1        # index of the owning block in post_content, if any
    shared_id: bool = False    # the same @id is defined in more than one block
    fingerprint: str = ""      # content hash for id-less nodes

    def key(self) -> str:
        """Stable identity for hashing repairs.

        An @id is stable across renders. Where the same @id is defined in
        several blocks the block in post_content disambiguates. Id-less nodes
        are keyed by their block in post_content plus path and content, so a
        plugin block appearing ahead of them on the live page does not
        renumber their repairs.
        """
        path = "/".join(map(str, self.path))
        if self.node_id and not self.shared_id:
            return self.node_id
        block = f"raw:{self.raw_block}" if self.raw_block >= 0 else f"live:{self.block}"
        if self.node_id:
            return f"{block}:{self.node_id}"
        return f"{block}:{path}:{self.fingerprint}"

    def to_dict(self) -> Dict[str, Any]:
        return {"block": self.block, "path": list(self.path), "node_id": self.node_id,
                "node_types": list(self.node_types), "raw_block": self.raw_block,
                "shared_id": self.shared_id, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Target":
        return cls(block=int(data.get("block", 0)), path=list(data.get("path", [])),
                   node_id=str(data.get("node_id", "") or ""),
                   node_types=list(data.get("node_types", [])),
                   raw_block=int(data.get("raw_block", -1)),
                   shared_id=bool(data.get("shared_id", False)),
                   fingerprint=str(data.get("fingerprint", "") or ""))


class PatchError(ValueError):
    """The patch could not be applied safely."""


def locate(document: Any, target: Target) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Find the target node in a (possibly re-fetched) document.

    Match by @id AND audited type first, then by path with a type check.
    Raises PatchError if the document no longer contains the node the audit
    saw.
    """
    if target.node_id:
        wanted = set(target.node_types)
        candidates = [
            (path, node) for path, node in iter_nodes(document)
            if node.get("@id") == target.node_id and node.get("@type")
            and (not wanted or set(node_types(node)) & wanted)
        ]
        if not candidates:
            raise PatchError(
                f"node {target.node_id} with @type {sorted(wanted) or 'any'} "
                "is no longer present in the block"
            )
        audited = tuple(target.path)
        for path, node in candidates:
            if path == audited:
                return path, node
        return candidates[0]
    try:
        node = get_at(document, tuple(target.path))
    except (KeyError, IndexError, TypeError):
        raise PatchError("node path no longer resolves; content changed since audit")
    if not isinstance(node, dict):
        raise PatchError("node path no longer points at an object")
    if target.node_types and not (set(node_types(node)) & set(target.node_types)):
        raise PatchError("node at path has a different @type than at audit time")
    return tuple(target.path), node


class AlreadyApplied(PatchError):
    """The node already carries the patched value; nothing to write."""


def apply_patch(document: Any, target: Target, patch: Dict[str, Any]) -> Any:
    """Return a patched deep copy of the document.

    patch forms:
      {"op": "set", "key": k, "value": v, "expect": <current value or absent>}
      {"op": "rename_id", "old": iri, "new": iri}   - node @id and every
                                                      {"@id": old} reference
    Raises AlreadyApplied when the document already holds the target value.
    """
    doc = json.loads(json.dumps(document))
    op = patch.get("op")
    if op == "set":
        _, node = locate(doc, target)
        key = patch["key"]
        if node.get(key) == patch["value"]:
            raise AlreadyApplied(f"{key} already has the repaired value")
        if "expect" in patch and node.get(key) != patch["expect"]:
            raise PatchError(
                f"{key} changed since audit (expected {patch['expect']!r}, "
                f"found {node.get(key)!r})"
            )
        node[key] = json.loads(json.dumps(patch["value"]))
        return doc
    if op == "rename_id":
        old, new = patch["old"], patch["new"]
        if not any(n.get("@id") == old for _, n in iter_nodes(doc)):
            if any(n.get("@id") == new and n.get("@type") for _, n in iter_nodes(doc)):
                raise AlreadyApplied("@id already renamed")
            raise PatchError(f"no node with @id {old!r} in the block")
        _, node = locate(doc, target)
        if node.get("@id") != old:
            raise PatchError(f"node @id is {node.get('@id')!r}, expected {old!r}")
        for _, n in iter_nodes(doc):
            if n.get("@id") == old:
                n["@id"] = new
        return doc
    raise PatchError(f"unknown patch op {op!r}")


def serialise_block(document: Any, like: str) -> str:
    """JSON text for a block, safe inside <script>: "</" would end the element
    early, so it is written as "<\\/" (valid JSON, standard JSON-LD-in-HTML)."""
    indent = 2 if "\n" in (like or "").strip() else None
    text = json.dumps(document, ensure_ascii=False, indent=indent)
    return text.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def splice_block(raw_html: str, block: Block, new_document: Any) -> str:
    """Replace one block's JSON text inside raw_html, leaving every other byte
    of the document exactly as it was, and prove the result still parses as
    the same number of blocks with the intended document in place."""
    new_text = serialise_block(new_document, block.text)
    out = raw_html[: block.start] + new_text + raw_html[block.end:]
    after = find_blocks(out)
    if len(after) != len(find_blocks(raw_html)) or after[block.index].document != new_document:
        raise PatchError("spliced block would not survive as one JSON-LD block")
    return out


def rename_references(raw_html: str, old: str, new: str, skip_index: int) -> str:
    """Rewrite every node whose @id == old in every block of raw_html except
    skip_index (already patched). Blocks that do not mention old stay
    byte-for-byte untouched."""
    out = raw_html
    for block in sorted(find_blocks(raw_html), key=lambda b: b.start, reverse=True):
        if block.index == skip_index or block.document is None:
            continue
        doc = json.loads(json.dumps(block.document))
        hits = 0
        for _, n in iter_nodes(doc):
            if n.get("@id") == old:
                n["@id"] = new
                hits += 1
        if hits:
            out = splice_block(out, block, doc)
    return out


def references_outside(blocks: List[Block], old: str, defining_block: int) -> List[int]:
    """Indexes of blocks (other than defining_block) that reference old
    without defining it themselves. A rename there would dangle."""
    out = []
    for b in blocks:
        if b.index == defining_block or b.document is None:
            continue
        refs = defines = False
        for _, n in iter_nodes(b.document):
            if n.get("@id") == old:
                if n.get("@type"):
                    defines = True
                else:
                    refs = True
        if refs and not defines:
            out.append(b.index)
    return out


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
    raw_blocks = find_blocks(raw_html)
    candidates = [b for b in raw_blocks if normalise_block_text(b.text) == want]
    if candidates:
        exact = [b for b in candidates if b.text == front_block.text]
        return (exact or candidates)[0]
    # Semantic fallback: same parsed document (JSON escaping may differ).
    if front_block.document is not None:
        for b in raw_blocks:
            if b.document == front_block.document:
                return b
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
