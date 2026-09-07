from __future__ import annotations

import unittest

from cleanuprtx import config
from cleanuprtx.audit import (CRITICAL, SOURCE_BODY, SOURCE_HEAD, SOURCE_PLUGIN, SOURCE_UNKNOWN,
                              audit_pages, is_iso_datetime)
from tests.helpers import ORG, PERSON, SITE, body_page, findings, page

PERSON_NODE = {"@type": "Person", "@id": PERSON, "name": "Richard Amir Nasser"}


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class TestSourceClassification(unittest.TestCase):
    def test_block_in_post_content_is_repairable(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage"}))], SITE), "profile-parent-node")
        self.assertEqual(f[0].source, SOURCE_BODY)
        self.assertTrue(f[0].is_auto_fixable)

    def test_plugin_block_is_never_repairable(self):
        f = findings(audit_pages([page(graph({"@type": "ProfilePage"}), raw_doc=graph({"@type": "ProfilePage"}),
                                        plugin_cls="rank-math-schema")], SITE), "profile-parent-node")
        self.assertEqual(f[0].source, SOURCE_PLUGIN)
        self.assertEqual(f[0].plugin, "RankMath")
        self.assertFalse(f[0].is_auto_fixable)
        self.assertIn("Rank Math", f[0].hand_edit)

    def test_head_block_not_in_post_content_is_report_only(self):
        f = findings(audit_pages([page(graph({"@type": "ProfilePage"}), raw="<p>no schema here</p>")], SITE))
        self.assertEqual(f[0].source, SOURCE_HEAD)
        self.assertFalse(f[0].is_auto_fixable)

    def test_unfetched_post_content_is_unknown(self):
        f = findings(audit_pages([page(graph({"@type": "ProfilePage"}), raw=None)], SITE))
        self.assertEqual(f[0].source, SOURCE_UNKNOWN)
        self.assertFalse(f[0].is_auto_fixable)

    def test_draft_pages_have_no_front_html_and_no_schema_findings(self):
        r = audit_pages([page(graph({"@type": "ProfilePage"}), status="draft")], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [])
        self.assertEqual(r.pages_fetched, 0)

    def test_counts_plugins(self):
        r = audit_pages([page(graph(PERSON_NODE), plugin_cls="yoast-schema-graph")], SITE)
        self.assertEqual(r.plugins_seen, {"Yoast SEO": 1})


class TestProfileParentNode(unittest.TestCase):
    def test_missing_is_critical_with_reference_fix_when_person_defined(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage"}, PERSON_NODE))], SITE), "profile-parent-node")
        self.assertEqual(f[0].severity, CRITICAL)
        self.assertEqual(f[0].patch["value"], {"@id": PERSON})

    def test_missing_on_canonical_page_embeds_full_node_when_undefined(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage"}))], SITE), "profile-parent-node")
        v = f[0].patch["value"]
        self.assertEqual(v["@type"], "Person")
        self.assertEqual(v["@id"], PERSON)
        self.assertEqual(v["name"], "Richard Amir Nasser")

    def test_string_name_becomes_person_node_not_iri(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": "Jane Doe"}),
                                            link="https://inspector-roofing.com/about/")], SITE))
        self.assertEqual(len(f), 1, "exactly one finding across all rules")
        self.assertEqual(f[0].rule, "profile-parent-node")
        self.assertEqual(f[0].patch["value"], {"@type": "Person", "name": "Jane Doe"})

    def test_wrong_type_is_the_invalid_object_type_case(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@type": "WebPage"}}))], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertIn("WebPage", f[0].detail)

    def test_unresolved_reference_is_flagged(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@id": "https://x/#ghost"}}))], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertIn("not defined", f[0].detail)

    def test_resolved_reference_passes(self):
        r = audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@id": PERSON}}, PERSON_NODE))], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [])

    def test_organization_subtype_passes(self):
        r = audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@type": "RoofingContractor", "name": "x"}}))], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [])

    def test_list_with_one_valid_passes(self):
        r = audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": ["x", {"@type": "Person", "name": "y"}]}))], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [])


class TestObjectFields(unittest.TestCase):
    def test_string_name_creator(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": "Richard Amir Nasser"}))], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["value"]["@id"], PERSON, "canonical name resolves to the canonical id")

    def test_unknown_name_becomes_person(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": "Some Photographer"}))], SITE), "object-field-type")
        self.assertEqual(f[0].patch["value"], {"@type": "Person", "name": "Some Photographer"})

    def test_publisher_name_becomes_organization(self):
        f = findings(audit_pages([body_page(graph({"@type": "Article", "publisher": "Acme"}))], SITE), "object-field-type")
        self.assertEqual(f[0].patch["value"]["@type"], "Organization")

    def test_url_string_is_flagged_and_unknown_url_has_no_patch(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": "https://example.com/someone"}))], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch)

    def test_url_string_matching_a_defined_node_becomes_reference(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": PERSON}, PERSON_NODE))], SITE), "object-field-type")
        self.assertEqual(f[0].patch["value"], {"@id": PERSON})

    def test_unresolved_id_reference_is_flagged(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": {"@id": "https://x/#ghost"}}))], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch)

    def test_wrong_type_object_is_flagged(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": {"@type": "WebPage"}}))], SITE), "object-field-type")
        self.assertEqual(len(f), 1)

    def test_valid_reference_and_inline_pass(self):
        r = audit_pages([body_page(graph({"@type": "ImageObject", "creator": {"@id": PERSON}},
                                         {"@type": "Article", "author": {"@type": "Person", "name": "x"}},
                                         {"@type": "Article", "publisher": {"@type": "RoofingContractor", "name": "y"}},
                                         PERSON_NODE))], SITE)
        self.assertEqual(findings(r, "object-field-type"), [])

    def test_list_of_strings_converts_whole_list_or_nothing(self):
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": ["A", "B"]}))], SITE), "object-field-type")
        self.assertEqual([v["name"] for v in f[0].patch["value"]], ["A", "B"])
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": ["A", "https://unknown/x"]}))], SITE), "object-field-type")
        self.assertIsNone(f[0].patch)

    def test_mainentity_is_only_checked_on_profilepage(self):
        """schema.org's mainEntity range is Thing; Google constrains it only on ProfilePage."""
        for doc in (graph({"@type": "WebPage", "mainEntity": "Richard"}),
                    graph({"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "Q?",
                                                                "acceptedAnswer": {"@type": "Answer", "text": "A."}}]}),
                    graph({"@type": "CollectionPage", "mainEntity": {"@type": "ItemList"}})):
            r = audit_pages([body_page(doc)], SITE)
            self.assertEqual(findings(r), [], doc)
        r = audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": "Richard"}))], SITE)
        self.assertEqual([f.rule for f in findings(r)], ["profile-parent-node"])


class TestDatetime(unittest.TestCase):
    def test_validator(self):
        for ok in ("2026", "2026-07", "2026-07-12", "2026-07-12T10:30:00Z", "2026-07-12T10:30:00+01:00",
                   "2026-07-12T10:30:00.123-05:00", "2026-07-12T10:30"):
            self.assertTrue(is_iso_datetime(ok), ok)
        for bad in ("July 12, 2026", "0000-00-00T00:00:00+00:00", "2026-13-45", "2026-02-30", "2026-07-12T25:61:00Z", "", "12/07/2026"):
            self.assertFalse(is_iso_datetime(bad), bad)

    def test_bad_string_gets_fix_from_modified_gmt_with_zone(self):
        f = findings(audit_pages([body_page(graph({"@type": "Article", "dateModified": "August 1, 2026"}))], SITE), "invalid-datetime")
        self.assertEqual(f[0].patch["value"], "2026-08-01T12:00:00+00:00")

    def test_false_and_number_are_flagged(self):
        for v in (False, 1690000000, None):
            f = findings(audit_pages([body_page(graph({"@type": "Article", "dateModified": v}))], SITE), "invalid-datetime")
            self.assertEqual(len(f), 1, repr(v))

    def test_datepublished_uses_date_gmt_and_datecreated_has_no_fix(self):
        f = findings(audit_pages([body_page(graph({"@type": "Article", "datePublished": "x", "dateCreated": "y"}))], SITE), "invalid-datetime")
        by = {x.detail[:9]: x for x in f}
        pub = next(x for x in f if "datePublished" in x.message)
        cre = next(x for x in f if "dateCreated" in x.message)
        self.assertEqual(pub.patch["value"], "2026-07-01T09:00:00+00:00")
        self.assertIsNone(cre.patch)

    def test_typed_literal_and_list_are_unwrapped(self):
        r = audit_pages([body_page(graph({"@type": "Article", "dateModified": {"@value": "2026-07-12"}}))], SITE)
        self.assertEqual(findings(r, "invalid-datetime"), [])
        f = findings(audit_pages([body_page(graph({"@type": "Article", "dateModified": ["bad"]}))], SITE), "invalid-datetime")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch, "no partial list rewrite")


class TestEntityFragmentation(unittest.TestCase):
    def test_canonical_person_by_alias_gets_rename(self):
        f = findings(audit_pages([body_page(graph({"@type": "Person", "@id": "https://inspector-roofing.com/#author", "name": "Richard Nasser"}))], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch, {"op": "rename_id", "old": "https://inspector-roofing.com/#author", "new": PERSON})

    def test_other_person_is_left_alone(self):
        r = audit_pages([body_page(graph({"@type": "Person", "@id": "https://inspector-roofing.com/author/colten/#author", "name": "Colten"}))], SITE)
        self.assertEqual(findings(r, "entity-fragmentation"), [])

    def test_match_by_url(self):
        f = findings(audit_pages([body_page(graph({"@type": "Person", "@id": "https://standards.inspector-roofing.com/#richard-nasser",
                                                   "name": "R. Nasser", "url": "https://inspector-roofing.com/richard-nasser/"}))], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)

    def test_canonical_org_by_name(self):
        f = findings(audit_pages([body_page(graph({"@type": "RoofingContractor", "@id": "https://inspector-roofing.com/#local", "name": "Inspector Roofing and Restoration"}))], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["new"], ORG)

    def test_site_without_canonical_ids_never_fires(self):
        r = audit_pages([body_page(graph({"@type": "Person", "@id": "https://pnagolfcarts.com/#x", "name": "Richard Nasser"}))], config.SITES["pnagolfcarts"])
        self.assertEqual(findings(r, "entity-fragmentation"), [])

    def test_canonical_node_passes(self):
        self.assertEqual(findings(audit_pages([body_page(graph(PERSON_NODE))], SITE), "entity-fragmentation"), [])


class TestStaleDraft(unittest.TestCase):
    def test_breakdance_titled_recent_draft_is_not_flagged(self):
        r = audit_pages([page(None, status="draft", breakdance=True, title="Storm season checklist",
                              created="2026-07-01T09:00:00", modified="2026-08-30T12:00:00")], SITE)
        self.assertEqual(findings(r, "stale-draft"), [])

    def test_breakdance_untitled_is_flagged_and_reasons_exported(self):
        r = audit_pages([page(None, status="draft", breakdance=True, title="", created="2026-07-01T09:00:00",
                              modified="2026-07-01T09:00:00")], SITE)
        f = findings(r, "stale-draft")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].reasons, ["untitled"])
        self.assertEqual(f[0].to_dict()["reasons"], ["untitled"])

    def test_never_edited_counts_only_when_old(self):
        """Floating drafts make date_gmt track modified_gmt on every save, so
        equality alone means nothing; it is a reason only past stale_days."""
        recent = page(None, status="draft", breakdance=True, title="Storm checklist",
                      created="2026-08-30T12:00:00", modified="2026-08-30T12:00:00")
        self.assertEqual(findings(audit_pages([recent], SITE), "stale-draft"), [])
        old = page(None, status="draft", breakdance=True, title="Storm checklist",
                   created="2024-01-01T12:00:00", modified="2024-01-01T12:00:00")
        f = findings(audit_pages([old], SITE), "stale-draft")
        self.assertEqual(len(f), 1)
        self.assertIn("never edited after creation", f[0].reasons)

    def test_non_breakdance_short_body_is_flagged(self):
        r = audit_pages([page(None, status="draft", body="<p>hi</p>", modified="2026-08-30T12:00:00")], SITE)
        self.assertEqual(len(findings(r, "stale-draft")), 1)


class TestRepoSchema(unittest.TestCase):
    """The repository's own GitHub Pages schema must stay clean."""

    def test_docs_index_has_no_critical_findings(self):
        import pathlib
        from cleanuprtx.wordpress import Content
        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in ("docs/index.html", "docs/ai-roof-damage-verification-protocol/index.html", "index.html"):
            html = (root / rel).read_text(encoding="utf-8")
            c = Content(id=0, kind="url", post_type="url", slug="", link="https://standards.inspector-roofing.com/" + rel,
                        title=rel, status="publish", date_gmt="", modified_gmt="", body_rendered="",
                        front_html=html, front_status=200)
            r = audit_pages([c], SITE)
            self.assertEqual(r.by_severity(CRITICAL), [], rel)
            self.assertEqual(findings(r, "entity-fragmentation"), [], rel)
