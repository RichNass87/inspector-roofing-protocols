from __future__ import annotations

import json
import unittest

from cleanuprtx.jsonld import (PatchError, Target, apply_patch, find_blocks, locate,
                               match_block_in_raw, normalise_block_text, splice_block)


class TestBlocks(unittest.TestCase):
    def test_finds_blocks_with_class_and_span(self):
        html = ('<head><script type="application/ld+json" class="rank-math-schema">{"a":1}</script>'
                '<script type="text/javascript">{"b":2}</script>'
                '<script class="x" type=\'application/ld+json\'>{"c":3}</script></head>')
        blocks = find_blocks(html)
        self.assertEqual([b.document for b in blocks], [{"a": 1}, {"c": 3}])
        self.assertEqual(blocks[0].plugin, "RankMath")
        self.assertEqual(blocks[1].plugin, "")
        self.assertEqual(html[blocks[0].start:blocks[0].end], '{"a":1}')

    def test_malformed_block_records_error(self):
        blocks = find_blocks('<script type="application/ld+json">{nope}</script>')
        self.assertIsNone(blocks[0].document)
        self.assertTrue(blocks[0].parse_error)

    def test_match_in_raw_is_whitespace_insensitive(self):
        front = find_blocks('<script type="application/ld+json">{"a": 1, "b": [1, 2]}</script>')[0]
        raw = '<!-- wp:html -->\n<script type="application/ld+json">\n{"a":1,"b":[1,2]}\n</script>\n<!-- /wp:html -->'
        self.assertIsNotNone(match_block_in_raw(front, raw))
        self.assertIsNone(match_block_in_raw(front, '<script type="application/ld+json">{"a":2}</script>'))
        self.assertIsNone(match_block_in_raw(front, ""))

    def test_normalise(self):
        self.assertEqual(normalise_block_text(' {"a":\n 1} '), '{"a":1}')


class TestPatch(unittest.TestCase):
    def setUp(self):
        self.doc = {"@context": "https://schema.org", "@graph": [
            {"@type": "ProfilePage", "@id": "https://x/#pp", "name": "P"},
            {"@type": "Person", "@id": "https://x/#old", "name": "R"},
            {"@type": "Article", "author": {"@id": "https://x/#old"}},
        ]}

    def test_set_by_node_id(self):
        t = Target(block=0, node_id="https://x/#pp", node_types=["ProfilePage"])
        out = apply_patch(self.doc, t, {"op": "set", "key": "mainEntity", "value": {"@id": "https://x/#old"}})
        self.assertEqual(out["@graph"][0]["mainEntity"], {"@id": "https://x/#old"})
        self.assertNotIn("mainEntity", self.doc["@graph"][0], "input must not be mutated")

    def test_set_checks_expect(self):
        t = Target(block=0, node_id="https://x/#pp")
        with self.assertRaises(PatchError):
            apply_patch(self.doc, t, {"op": "set", "key": "name", "value": "Q", "expect": "changed"})

    def test_rename_rewrites_references(self):
        t = Target(block=0, node_id="https://x/#old", node_types=["Person"])
        out = apply_patch(self.doc, t, {"op": "rename_id", "old": "https://x/#old", "new": "https://x/#new"})
        self.assertEqual(out["@graph"][1]["@id"], "https://x/#new")
        self.assertEqual(out["@graph"][2]["author"], {"@id": "https://x/#new"})

    def test_locate_by_path_with_type_check(self):
        t = Target(block=0, path=["@graph", 2], node_types=["Article"])
        _, node = locate(self.doc, t)
        self.assertEqual(node["@type"], "Article")
        with self.assertRaises(PatchError):
            locate(self.doc, Target(block=0, path=["@graph", 2], node_types=["Person"]))
        with self.assertRaises(PatchError):
            locate(self.doc, Target(block=0, node_id="https://x/#missing"))

    def test_splice_preserves_every_other_byte(self):
        raw = '<!-- wp:html -->\n<script type="application/ld+json">{"a": 1}</script>\n<!-- /wp:html --><p>x</p>'
        block = find_blocks(raw)[0]
        out = splice_block(raw, block, {"a": 2})
        self.assertTrue(out.startswith('<!-- wp:html -->\n<script type="application/ld+json">'))
        self.assertTrue(out.endswith('</script>\n<!-- /wp:html --><p>x</p>'))
        self.assertEqual(json.loads(find_blocks(out)[0].text), {"a": 2})
