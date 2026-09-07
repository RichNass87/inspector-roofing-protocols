"""Adversarial tests for the audit rules (audit.py) and JSON-LD helpers (jsonld.py).

Every assertion here is the behaviour the README and the module docstrings
promise, not what the code happens to do today:

* audit.py: "Each rule targets a specific defect Google flags" and "Rules read
  what Googlebot reads: the live front-end HTML."
* jsonld.iter_nodes: "yielding (path, node) for every object node".
* jsonld.locate: "Match by @id first, then by path with a type check. Raises
  PatchError if the document no longer contains the node the audit saw."
* README / Rules: object-field-type resolves "a reference that resolves to one
  on the same page"; "an unknown URL ... -> report only".
* README / Ledger: "two identical defects on different nodes never collide".
* README / Rules: "Only inspector-roofing has [canonical identities]; the other
  two sites are never asked to merge anyone."

No network, no Keychain, no subprocess.
"""

from __future__ import annotations

import json
import unittest

from cleanuprtx import config
from cleanuprtx.approvals import make_repair_id
from cleanuprtx.audit import CRITICAL, SOURCE_BODY, audit_pages
from cleanuprtx.jsonld import apply_patch
from tests.helpers import ORG, PERSON, SITE, body_page, findings, ld, page

PERSON_NODE = {"@type": "Person", "@id": PERSON, "name": "Richard Amir Nasser"}
CANONICAL_URL = "https://inspector-roofing.com/richard-nasser/"
PNA = config.SITES["pnagolfcarts"]


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


def two_body_blocks(doc1, doc2, **kw):
    """A page whose live HTML and post_content both carry two blocks."""
    raw = (f"<!-- wp:html -->\n{ld(doc1)}\n<!-- /wp:html -->\n"
           f"<!-- wp:html -->\n{ld(doc2)}\n<!-- /wp:html -->")
    return page(doc1, raw=raw, extra_front=ld(doc2), **kw)


# --- references across blocks ------------------------------------------

class TestCrossBlockReferences(unittest.TestCase):
    """object-field-type / profile-parent-node resolve references 'on the same
    page' - Google merges every ld+json block on a page into one graph."""

    def test_mainentity_reference_to_person_in_plugin_block_resolves(self):
        pp = graph({"@type": "ProfilePage", "mainEntity": {"@id": PERSON}})
        p = page(pp, raw_doc=pp, extra_front=ld(graph(PERSON_NODE), "rank-math-schema"))
        r = audit_pages([p], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [])
        self.assertEqual(findings(r, "object-field-type"), [])

    def test_missing_mainentity_repair_references_person_defined_in_other_block(self):
        pp = graph({"@type": "ProfilePage"})
        p = page(pp, raw_doc=pp, extra_front=ld(graph(PERSON_NODE), "rank-math-schema"))
        f = findings(audit_pages([p], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].source, SOURCE_BODY, "the ProfilePage block is ours")
        self.assertEqual(f[0].patch["value"], {"@id": PERSON},
                         "README: reference the canonical Person when that node exists on the page")

    def test_creator_url_matching_node_in_other_body_block_becomes_reference(self):
        img = graph({"@type": "ImageObject", "creator": PERSON})
        p = two_body_blocks(img, graph(PERSON_NODE))
        f = findings(audit_pages([p], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["value"], {"@id": PERSON})


# --- unusual but valid JSON-LD shapes -----------------------------------

class TestDocumentShapes(unittest.TestCase):
    def test_top_level_array_containing_graph_is_walked_and_patchable(self):
        doc = [graph({"@type": "ProfilePage", "@id": "https://inspector-roofing.com/richard-nasser/#pp"})]
        f = findings(audit_pages([body_page(doc)], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, CRITICAL)
        self.assertTrue(f[0].is_auto_fixable)
        out = apply_patch(doc, f[0].target, f[0].patch)
        self.assertEqual(out[0]["@graph"][0]["mainEntity"]["@id"], PERSON)
        self.assertNotIn("mainEntity", doc[0]["@graph"][0], "input must not be mutated")

    def test_context_array_with_vocab_object_is_clean(self):
        doc = {"@context": ["https://schema.org", {"@vocab": "https://schema.org/"}],
               "@type": "ProfilePage", "mainEntity": {"@type": "Person", "name": "x"}}
        r = audit_pages([body_page(doc)], SITE)
        self.assertEqual(findings(r), [])

    def test_context_term_definitions_are_not_nodes(self):
        """iter_nodes promises 'every object node'. A @context term map is not a
        node: 'creator': 'schema:creator' there is a term definition, not a
        creator field, and Google does not flag it."""
        doc = {"@context": ["https://schema.org",
                            {"author": {"@id": "schema:author", "@type": "@id"},
                             "creator": "schema:creator",
                             "dateModified": {"@id": "schema:dateModified", "@type": "xsd:dateTime"}}],
               "@type": "ProfilePage", "mainEntity": {"@type": "Person", "name": "x"}}
        r = audit_pages([body_page(doc)], SITE)
        self.assertEqual(findings(r), [], [f.detail for f in r.findings])

    def test_type_as_list_on_profilepage_and_person(self):
        pp = {"@type": ["WebPage", "ProfilePage"], "mainEntity": {"@id": PERSON}}
        person = {"@type": ["Person", "Thing"], "@id": PERSON, "name": "Richard Amir Nasser"}
        r = audit_pages([body_page(graph(pp, person))], SITE)
        self.assertEqual(findings(r), [])
        # and the ProfilePage is still recognised as one when it is broken
        f = findings(audit_pages([body_page(graph({"@type": ["WebPage", "ProfilePage"], "mainEntity": "Richard"}))], SITE))
        self.assertEqual([x.rule for x in f], ["profile-parent-node"], "checked once, by the profile rule only")

    def test_type_list_organization_subtype_is_merged_into_canonical_org(self):
        node = {"@type": ["LocalBusiness", "RoofingContractor"], "@id": "https://inspector-roofing.com/#local",
                "name": "Inspector Roofing and Restoration"}
        f = findings(audit_pages([body_page(graph(node))], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch, {"op": "rename_id", "old": node["@id"], "new": ORG})

    def test_commented_out_block_is_not_what_googlebot_sees(self):
        """audit.py: 'Rules read what Googlebot reads'. A script inside an HTML
        comment is not parsed by any browser or crawler."""
        p = page(None, raw="<p>no schema</p>",
                 extra_front="<!-- disabled: " + ld(graph({"@type": "ProfilePage"})) + " -->")
        r = audit_pages([p], SITE)
        self.assertEqual(findings(r), [])


# --- duplicate @ids -------------------------------------------------------

class TestDuplicateIds(unittest.TestCase):
    DUP = "https://inspector-roofing.com/richard-nasser/#dup"

    def test_reference_to_id_defined_twice_resolves_if_any_definition_is_a_person(self):
        """In JSON-LD two nodes with one @id are one node; a reference to it
        resolves to a Person if either definition says Person."""
        doc = graph({"@type": "ImageObject", "@id": self.DUP, "contentUrl": "https://x/a.jpg"},
                    {"@type": "Person", "@id": self.DUP, "name": "R"},
                    {"@type": "ProfilePage", "mainEntity": {"@id": self.DUP}})
        r = audit_pages([body_page(doc)], SITE)
        self.assertEqual(findings(r, "profile-parent-node"), [],
                         [f.detail for f in findings(r, "profile-parent-node")])

    def test_locate_by_id_applies_patch_to_node_with_audited_type(self):
        """locate() promises 'by @id first, then by path with a type check' and
        to raise if 'the document no longer contains the node the audit saw'.
        The audit saw a ProfilePage; the patch must land on it, never on a
        Person that happens to share the @id."""
        doc = graph({"@type": "Person", "@id": self.DUP, "name": "R"},
                    {"@type": "ProfilePage", "@id": self.DUP})
        f = findings(audit_pages([body_page(doc)], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].target.node_types, ["ProfilePage"])
        out = apply_patch(doc, f[0].target, f[0].patch)
        self.assertNotIn("mainEntity", out["@graph"][0], "a Person must never receive mainEntity")
        self.assertIn("mainEntity", out["@graph"][1])

    def test_same_id_in_two_body_blocks_yields_two_repair_ids(self):
        """README: 'two identical defects on different nodes never collide'.
        Two hand-pasted blocks each declaring the same non-canonical Person
        are two nodes, each needing its own rename."""
        author = {"@type": "Person", "@id": "https://inspector-roofing.com/#author", "name": "Richard Nasser"}
        d1 = graph(author)
        d2 = graph(author, {"@type": "Article", "author": {"@id": author["@id"]}})
        fs = findings(audit_pages([two_body_blocks(d1, d2)], SITE), "entity-fragmentation")
        self.assertEqual(sorted(f.block_index for f in fs), [0, 1])
        self.assertTrue(all(f.is_auto_fixable for f in fs))
        ids = {make_repair_id("inspector-roofing", f.kind, f.page_id, f.rule, f.target.key(), f.patch) for f in fs}
        self.assertEqual(len(ids), 2, "one repair per block, or the second block is never fixed")


# --- dates --------------------------------------------------------------

class TestDatetimeLiterals(unittest.TestCase):
    def test_typed_literal_false_is_flagged_and_fixed_from_modified_gmt(self):
        f = findings(audit_pages([body_page(graph({"@type": "Article", "dateModified": {"@value": False}}))], SITE),
                     "invalid-datetime")
        self.assertEqual(len(f), 1)
        self.assertIn("bool", f[0].detail)
        self.assertEqual(f[0].patch["value"], "2026-08-01T12:00:00+00:00")
        self.assertEqual(f[0].patch["expect"], {"@value": False}, "expect guards the exact literal seen")

    def test_typed_literal_with_xsd_type_and_valid_date_passes(self):
        doc = graph({"@type": "Article", "dateModified": {"@value": "2026-08-01T12:00:00+00:00", "@type": "xsd:dateTime"}})
        self.assertEqual(findings(audit_pages([body_page(doc)], SITE), "invalid-datetime"), [])

    def test_list_containing_typed_false_is_flagged_without_partial_rewrite(self):
        doc = graph({"@type": "Article", "dateModified": ["2026-08-01", {"@value": False}]})
        f = findings(audit_pages([body_page(doc)], SITE), "invalid-datetime")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch)


# --- creator lists ------------------------------------------------------

class TestCreatorLists(unittest.TestCase):
    def test_mixed_dict_and_string_converts_only_the_string(self):
        doc = graph({"@type": "ImageObject", "creator": [{"@type": "Person", "name": "A"}, "B"]})
        f = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["value"], [{"@type": "Person", "name": "A"}, {"@type": "Person", "name": "B"}])
        self.assertEqual(f[0].patch["expect"], [{"@type": "Person", "name": "A"}, "B"])

    def test_mixed_list_with_wrong_type_object_has_no_patch(self):
        doc = graph({"@type": "ImageObject", "creator": ["B", {"@type": "WebPage", "name": "A"}]})
        f = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch, "whole list or nothing")

    def test_mixed_reference_and_alias_both_point_at_canonical(self):
        doc = graph({"@type": "ImageObject", "creator": [{"@id": PERSON}, "Richard Nasser"]}, PERSON_NODE)
        f = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        self.assertEqual(f[0].patch["value"], [{"@id": PERSON}, {"@id": PERSON}])


# --- canonical identity matching ----------------------------------------

class TestCanonicalIdentityMatching(unittest.TestCase):
    def test_casefolded_alias_and_padding_resolve_to_canonical(self):
        for v in ("RICHARD NASSER", " richard a. nasser "):
            f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": v}))], SITE), "object-field-type")
            self.assertEqual(f[0].patch["value"]["@id"], PERSON, repr(v))

    def test_alias_with_nbsp_or_double_space_resolves_to_canonical(self):
        """WordPress editors routinely turn a space into &nbsp;. The canonical
        person's name in any whitespace form is still the canonical person;
        inventing a second anonymous Person for it fragments the entity the
        entity-fragmentation rule exists to merge."""
        for v in ("Richard Nasser", "Richard  Nasser"):
            f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": v}))], SITE), "object-field-type")
            self.assertEqual(len(f), 1, repr(v))
            self.assertEqual(f[0].patch["value"].get("@id"), PERSON, repr(v))

    def test_fragmentation_matches_name_with_nbsp_and_casing(self):
        node = {"@type": "Person", "@id": "https://inspector-roofing.com/#author", "name": "RICHARD NASSER"}
        f = findings(audit_pages([body_page(graph(node))], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["new"], PERSON)

    def test_canonical_person_url_with_and_without_trailing_slash(self):
        for v in (CANONICAL_URL, CANONICAL_URL.rstrip("/")):
            f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": v}))], SITE), "object-field-type")
            self.assertEqual(len(f), 1, v)
            self.assertEqual(f[0].patch["value"]["@id"], PERSON, v)
            self.assertEqual(f[0].patch["value"]["@type"], "Person", v)
        # when the Person is defined on the page the fix is a reference, not a copy
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": CANONICAL_URL}, PERSON_NODE))], SITE),
                     "object-field-type")
        self.assertEqual(f[0].patch["value"], {"@id": PERSON})

    def test_canonical_person_id_string_as_creator_is_not_an_unknown_url(self):
        """README: only 'an unknown URL' is report-only. The canonical @id from
        config.py is the best-known URL the tool has."""
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": PERSON}))], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertIsNotNone(f[0].patch)
        self.assertEqual(f[0].patch["value"]["@id"], PERSON)

    def test_profilepage_mainentity_canonical_url_string_on_another_page(self):
        """README: mainEntity -> the canonical Person. A ProfilePage at /about/
        naming Richard's URL as a string is about Richard."""
        doc = graph({"@type": "ProfilePage", "mainEntity": CANONICAL_URL})
        f = findings(audit_pages([body_page(doc, link="https://inspector-roofing.com/about/")], SITE), "profile-parent-node")
        self.assertEqual(len(f), 1)
        self.assertIsNotNone(f[0].patch)
        self.assertEqual(f[0].patch["value"]["@id"], PERSON)


# --- other sites never merge anyone -------------------------------------

class TestOtherSitesNeverMerge(unittest.TestCase):
    def test_pnagolfcarts_creator_with_canonical_name_stays_a_plain_person(self):
        doc = graph({"@type": "ImageObject", "creator": "Richard Amir Nasser"})
        f = findings(audit_pages([body_page(doc, link="https://pnagolfcarts.com/about/")], PNA), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].patch["value"], {"@type": "Person", "name": "Richard Amir Nasser"})
        self.assertNotIn(PERSON, json.dumps(f[0].to_dict()))

    def test_pnagolfcarts_profilepage_gets_no_invented_identity(self):
        for main in (None, CANONICAL_URL):
            node = {"@type": "ProfilePage"}
            if main is not None:
                node["mainEntity"] = main
            f = findings(audit_pages([body_page(graph(node), link="https://pnagolfcarts.com/about/")], PNA), "profile-parent-node")
            self.assertEqual(len(f), 1, repr(main))
            self.assertIsNone(f[0].patch, repr(main))
        r = audit_pages([body_page(graph({"@type": "Person", "@id": "https://pnagolfcarts.com/#x",
                                          "name": "Richard Amir Nasser", "url": CANONICAL_URL}),
                                    link="https://pnagolfcarts.com/about/")], PNA)
        self.assertEqual(findings(r, "entity-fragmentation"), [])

    def test_no_site_url_mode_never_invents_identity(self):
        doc = graph({"@type": "ProfilePage"}, {"@type": "ImageObject", "creator": "Richard Amir Nasser"})
        r = audit_pages([body_page(doc)], None)
        pp = findings(r, "profile-parent-node")
        self.assertEqual(len(pp), 1)
        self.assertIsNone(pp[0].patch)
        cr = findings(r, "object-field-type")
        self.assertEqual(cr[0].patch["value"], {"@type": "Person", "name": "Richard Amir Nasser"})
        self.assertEqual(findings(r, "entity-fragmentation"), [])


# --- nodes without @id ---------------------------------------------------

class TestIdentityWithoutIds(unittest.TestCase):
    def test_two_identical_imageobjects_without_id_have_distinct_targets_and_repair_ids(self):
        doc = graph({"@type": "ImageObject", "creator": "Bob"}, {"@type": "ImageObject", "creator": "Bob"})
        fs = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        self.assertEqual(len(fs), 2)
        self.assertEqual(len({f.target.key() for f in fs}), 2)
        ids = {make_repair_id("inspector-roofing", f.kind, f.page_id, f.rule, f.target.key(), f.patch) for f in fs}
        self.assertEqual(len(ids), 2)
        for f in fs:
            self.assertEqual(f.target.node_id, "")
            self.assertEqual(f.target.node_types, ["ImageObject"])

    def test_path_target_patches_only_its_own_node(self):
        doc = graph({"@type": "ImageObject", "creator": "Bob"}, {"@type": "ImageObject", "creator": "Bob"})
        fs = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        second = next(f for f in fs if f.target.path == ["@graph", 1])
        out = apply_patch(doc, second.target, second.patch)
        self.assertEqual(out["@graph"][0]["creator"], "Bob", "sibling untouched")
        self.assertEqual(out["@graph"][1]["creator"], {"@type": "Person", "name": "Bob"})


# --- mainEntity range on ordinary pages -----------------------------------

class TestMainEntityRange(unittest.TestCase):
    def test_faqpage_question_mainentity_is_not_flagged(self):
        """audit.py: 'Each rule targets a specific defect Google flags.'
        schema.org's mainEntity has range Thing; Google requires Person or
        Organization only on ProfilePage. FAQPage.mainEntity = Question[] is
        Google's own documented FAQ markup and must not appear as a warning
        or a hand edit."""
        doc = graph({"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": "Do you inspect metal roofs?",
             "acceptedAnswer": {"@type": "Answer", "text": "Yes."}}]})
        r = audit_pages([body_page(doc)], SITE)
        self.assertEqual(findings(r), [], [f.detail for f in r.findings])
        self.assertEqual(r.hand_edits, [])


if __name__ == "__main__":
    unittest.main()
