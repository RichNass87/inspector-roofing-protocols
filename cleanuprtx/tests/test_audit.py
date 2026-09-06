"""Rule tests. No network and no Keychain access."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from cleanuprtx import config
from cleanuprtx.approvals import Ledger, make_repair_id
from cleanuprtx.audit import (
    CRITICAL,
    audit_pages,
    extract_jsonld,
    iter_nodes,
)


@dataclass
class FakePage:
    """Stands in for wordpress.Content."""

    id: int = 1
    kind: str = "pages"
    slug: str = "richard-nasser"
    link: str = "https://inspector-roofing.com/richard-nasser/"
    title: str = "Richard Nasser"
    status: str = "publish"
    modified_gmt: str = "2026-08-01T12:00:00"
    rendered_html: str = ""
    is_breakdance: bool = False


def page_with(payload: dict, **kwargs) -> FakePage:
    html = (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></head><body>body text</body></html>"
    )
    return FakePage(rendered_html=html, **kwargs)


class TestExtraction(unittest.TestCase):
    def test_extracts_jsonld_block(self):
        page = page_with({"@type": "Person", "name": "Richard"})
        docs = extract_jsonld(page.rendered_html)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["name"], "Richard")

    def test_ignores_malformed_json(self):
        html = '<script type="application/ld+json">{not json}</script>'
        self.assertEqual(extract_jsonld(html), [])

    def test_ignores_other_script_types(self):
        html = '<script type="text/javascript">{"@type":"Person"}</script>'
        self.assertEqual(extract_jsonld(html), [])

    def test_walks_nested_graph(self):
        doc = {"@graph": [{"@type": "Person"}, {"@type": "Organization"}]}
        types = [n.get("@type") for n in iter_nodes(doc) if n.get("@type")]
        self.assertIn("Person", types)
        self.assertIn("Organization", types)


class TestProfileParentNode(unittest.TestCase):
    def test_missing_main_entity_is_critical(self):
        page = page_with({"@type": "ProfilePage", "name": "Profile"})
        result = audit_pages([page])
        findings = [f for f in result.findings if f.rule == "profile-parent-node"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, CRITICAL)
        self.assertEqual(
            findings[0].fix["mainEntity"]["@id"], config.CANONICAL_PERSON_ID
        )

    def test_string_main_entity_is_flagged(self):
        page = page_with({"@type": "ProfilePage", "mainEntity": "Richard Nasser"})
        findings = [
            f for f in audit_pages([page]).findings
            if f.rule == "profile-parent-node"
        ]
        self.assertEqual(len(findings), 1)

    def test_object_main_entity_passes(self):
        page = page_with({
            "@type": "ProfilePage",
            "mainEntity": {"@id": config.CANONICAL_PERSON_ID},
        })
        findings = [
            f for f in audit_pages([page]).findings
            if f.rule == "profile-parent-node"
        ]
        self.assertEqual(findings, [])


class TestObjectFields(unittest.TestCase):
    def test_string_creator_is_flagged(self):
        page = page_with({"@type": "ImageObject", "creator": "Richard Nasser"})
        findings = [
            f for f in audit_pages([page]).findings if f.rule == "object-field-type"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].fix["creator"]["name"], "Richard Nasser")

    def test_object_creator_passes(self):
        page = page_with({
            "@type": "ImageObject",
            "creator": {"@id": config.CANONICAL_PERSON_ID},
        })
        findings = [
            f for f in audit_pages([page]).findings if f.rule == "object-field-type"
        ]
        self.assertEqual(findings, [])


class TestDatetime(unittest.TestCase):
    def test_valid_dates_pass(self):
        for value in ("2026-07-12", "2026-07-12T10:30:00Z",
                      "2026-07-12T10:30:00+01:00"):
            page = page_with({"@type": "Article", "dateModified": value})
            findings = [
                f for f in audit_pages([page]).findings
                if f.rule == "invalid-datetime"
            ]
            self.assertEqual(findings, [], f"{value} should be valid")

    def test_invalid_date_is_flagged_and_gets_a_fix(self):
        page = page_with({"@type": "Article", "dateModified": "July 12, 2026"})
        findings = [
            f for f in audit_pages([page]).findings if f.rule == "invalid-datetime"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].fix["dateModified"], "2026-08-01")


class TestEntityFragmentation(unittest.TestCase):
    def test_non_canonical_person_id_is_flagged(self):
        page = page_with({
            "@type": "Person",
            "@id": "https://standards.inspector-roofing.com/#richard-nasser",
        })
        findings = [
            f for f in audit_pages([page]).findings
            if f.rule == "entity-fragmentation"
        ]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].fix["@id"], config.CANONICAL_PERSON_ID)

    def test_canonical_person_id_passes(self):
        page = page_with({"@type": "Person", "@id": config.CANONICAL_PERSON_ID})
        findings = [
            f for f in audit_pages([page]).findings
            if f.rule == "entity-fragmentation"
        ]
        self.assertEqual(findings, [])

    def test_third_party_ids_are_left_alone(self):
        page = page_with({"@type": "Person", "@id": "https://orcid.org/0009-0000"})
        findings = [
            f for f in audit_pages([page]).findings
            if f.rule == "entity-fragmentation"
        ]
        self.assertEqual(findings, [])


class TestBreakdanceGuard(unittest.TestCase):
    def test_breakdance_pages_are_not_auto_fixable(self):
        page = page_with(
            {"@type": "ProfilePage", "name": "x"}, is_breakdance=True
        )
        findings = audit_pages([page]).findings
        self.assertTrue(findings)
        self.assertTrue(all(not f.is_auto_fixable for f in findings))
        self.assertEqual(audit_pages([page]).auto_fixable, [])


class TestLedger(unittest.TestCase):
    def test_repair_id_is_stable(self):
        a = make_repair_id("s", 1, "rule", {"x": 1})
        b = make_repair_id("s", 1, "rule", {"x": 1})
        self.assertEqual(a, b)

    def test_repair_id_varies_with_input(self):
        a = make_repair_id("s", 1, "rule", {"x": 1})
        b = make_repair_id("s", 2, "rule", {"x": 1})
        self.assertNotEqual(a, b)

    def test_propose_skips_unfixable_findings(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "l.json")
            page = page_with({"@type": "ProfilePage"}, is_breakdance=True)
            for finding in audit_pages([page]).findings:
                self.assertIsNone(ledger.propose("site", finding))


if __name__ == "__main__":
    unittest.main()
