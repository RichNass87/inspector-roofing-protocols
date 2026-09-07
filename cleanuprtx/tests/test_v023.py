"""Coverage for the v0.2.3 (round-4) fixes: the carry lifecycle across restore
and hand edits, identity across twins, and rule corrections."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleanuprtx import cli, wordpress
from cleanuprtx.approvals import APPLIED, APPROVED, FAILED, PENDING, REJECTED, STALE, Ledger, make_repair_id
from cleanuprtx.audit import audit_pages
from cleanuprtx.jsonld import find_blocks
from cleanuprtx.schema_types import is_org_type
from tests.helpers import PERSON, SITE, body_page, findings, ld, page
from tests.test_adv_writepath import ApplyHarness, Site41

PP_ID = "https://inspector-roofing.com/richard-nasser/#pp"
OLD = "https://inspector-roofing.com/#author"
WP_ID = "https://inspector-roofing.com/richard-nasser/#webpage"


class Site41M(Site41):
    """Site41 with a configurable modified_gmt (a later save of the page)."""

    def __init__(self, front_html, raw, status="publish", modified="2026-08-01T12:00:00"):
        super().__init__(front_html, raw, status=status)
        self.modified = modified

    def request_json(self, url, method="GET", headers=None, body=None, form=None, **kw):
        out = super().request_json(url, method, headers, body, form, **kw)
        if method == "GET" and isinstance(out, dict) and "modified_gmt" in out:
            out["modified_gmt"] = self.modified
        return out


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class TestAbsorbAfterRestore(ApplyHarness):
    """The documented flow: apply, restore the autosave in wp-admin, publish.
    The next run must recognise the repair as absorbed, not wedge the page."""

    def _stage_then_publish(self, doc, rule):
        content = body_page(doc)
        ids = self.propose_all(content, rule=rule)
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        published_raw = site.writes[-1]["body"]["content"]     # the owner restored + published it
        return ids, published_raw

    def test_rename_absorbed_after_publish_and_new_repair_stages(self):
        doc = graph({"@type": "Person", "@id": OLD, "name": "Richard Nasser"},
                    {"@type": "WebPage", "@id": WP_ID, "dateModified": "yesterday"})
        (rid_a,), published = self._stage_then_publish(doc, "entity-fragmentation")
        live_doc = find_blocks(published)[0].document
        live = page(live_doc, raw=published, modified="2026-09-02T10:00:00")   # a later save
        (rid_b,) = self.propose_all(live, rule="invalid-datetime")
        site = Site41M(live.front_html, published, modified="2026-09-02T10:00:00")
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        led = self.ledger()
        self.assertIn("absorbed", led.repairs[rid_a].result)
        self.assertEqual(led.repairs[rid_b].state, APPLIED)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"]
        self.assertEqual(g[0]["@id"], PERSON)
        self.assertEqual(g[1]["dateModified"], "2026-09-02T10:00:00+00:00")

    def test_idless_node_absorbed_after_publish(self):
        doc = graph({"@type": "ImageObject", "contentUrl": "https://x/a.jpg", "creator": "Bob", "dateModified": "bad"})
        (rid_a,), published = self._stage_then_publish(doc, "object-field-type")
        live_doc = find_blocks(published)[0].document
        live = page(live_doc, raw=published, modified="2026-09-02T10:00:00")
        # No datetime repair for a non-page node; nothing else to propose. Apply must still absorb A.
        led = self.ledger()
        led.repairs[rid_a].state = APPLIED
        led.save()
        # Approve a fresh repair on another block of the same page to trigger apply.
        extra = graph({"@type": "ProfilePage", "@id": PP_ID})
        raw2 = published + "<!-- wp:html -->" + ld(extra) + "<!-- /wp:html -->"
        live2 = page(live_doc, raw=raw2, extra_front=ld(extra), modified="2026-09-02T10:00:00")
        (rid_b,) = self.propose_all(live2, rule="profile-parent-node")
        site = Site41M(live2.front_html, raw2, modified="2026-09-02T10:00:00")
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertIn("absorbed", self.ledger().repairs[rid_a].result)
        self.assertEqual(self.ledger().repairs[rid_b].state, APPLIED)

    def test_discarded_autosave_is_recarried_with_a_fresh_patch(self):
        """Owner discarded the autosave: the defect is still live, so the earlier repair is
        re-carried - with the patch the audit builds NOW (a derived date follows the page)."""
        doc = graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad", "name": "x"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="invalid-datetime")
        self.run_apply(Site41(content.front_html, content.content_raw))
        self.assertEqual(self.ledger().repairs[rid_a].patch["value"], "2026-08-01T12:00:00+00:00")
        # Page saved again by hand without fixing the date (and with a new defect).
        doc2 = graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad", "name": "x", "author": "Bob"})
        later = body_page(doc2, modified="2026-09-03T09:00:00")
        (rid_b,) = self.propose_all(later, rule="object-field-type")
        site = Site41M(later.front_html, later.content_raw, modified="2026-09-03T09:00:00")
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        g = find_blocks(site.writes[-1]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(g["dateModified"], "2026-09-03T09:00:00+00:00", "carried with the current derived value")
        self.assertEqual(g["author"]["name"], "Bob")


class TestCrossRunOrder(ApplyHarness):
    def test_carried_rename_then_new_set_on_same_node(self):
        doc = graph({"@type": "RoofingContractor", "@id": "https://inspector-roofing.com/#org-old",
                     "name": "Inspector Roofing and Restoration", "founder": "Richard Nasser"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="entity-fragmentation")
        self.run_apply(Site41(content.front_html, content.content_raw))
        (rid_b,) = self.propose_all(content, rule="object-field-type")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        node = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(node["@id"], SITE.canonical_org_id)
        self.assertEqual(node["founder"]["@id"], PERSON)
        self.assertEqual({self.ledger().repairs[i].state for i in (rid_a, rid_b)}, {APPLIED})


class TestSavedSinceStaging(ApplyHarness):
    def test_carry_that_no_longer_fits_after_a_save_is_retired_and_others_proceed(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="profile-parent-node")
        self.run_apply(Site41(content.front_html, content.content_raw))
        # Owner set mainEntity to someone else by hand and saved; the date is still wrong.
        doc2 = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday",
                      "mainEntity": {"@type": "Person", "name": "Colten Smith"}})
        later = body_page(doc2, modified="2026-09-04T09:00:00")
        (rid_b,) = self.propose_all(later, rule="invalid-datetime")
        site = Site41M(later.front_html, later.content_raw, modified="2026-09-04T09:00:00")
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        led = self.ledger()
        self.assertIn("absorbed", led.repairs[rid_a].result, "the audit no longer reports the parent-node defect")
        self.assertEqual(led.repairs[rid_b].state, APPLIED)
        self.assertEqual(len(site.writes), 1)


class TestTwins(ApplyHarness):
    def test_second_of_two_identical_body_blocks_is_repairable(self):
        a = {"@type": "ImageObject", "contentUrl": "https://x/a.jpg", "creator": "Bob"}
        raw = "<!-- wp:html -->" + ld(a) + "<!-- /wp:html --><!-- wp:html -->" + ld(a) + "<!-- /wp:html -->"
        p = page(None, raw=raw, extra_front=ld(a) + ld(a))
        fs = findings(audit_pages([p], SITE), "object-field-type")
        self.assertEqual([f.target.raw_block for f in fs], [0, 1])
        led = self.ledger()
        ra, rb = (led.propose("inspector-roofing", f) for f in fs)
        self.assertNotEqual(ra.repair_id, rb.repair_id)
        led.decide(rb.repair_id, True)
        led.save()
        site = Site41(p.front_html, raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        blocks = find_blocks(site.writes[0]["body"]["content"])
        self.assertEqual(blocks[0].document["creator"], "Bob")
        self.assertEqual(blocks[1].document["creator"]["name"], "Bob")


class TestLedgerLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _finding(self):
        return findings(audit_pages([body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))], SITE))[0]

    def test_applied_and_absorbed_row_reopens_on_regression(self):
        led = Ledger(self.path)
        r = led.propose("s", self._finding())
        led.mark_applied(r.repair_id, "already correct: x")
        self.assertIs(led.propose("s", self._finding()), r)
        self.assertEqual(r.state, PENDING)
        self.assertIn("reported again", r.result)

    def test_unrestored_autosave_row_is_kept_but_patch_refreshed(self):
        led = Ledger(self.path)
        f = findings(audit_pages([body_page(graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad"}))], SITE))[0]
        r = led.propose("s", f)
        led.mark_applied(r.repair_id, "autosave: https://x/edit", "2026-08-01T12:00:00")
        f2 = findings(audit_pages([body_page(graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad"}), modified="2026-09-01T00:00:00")], SITE))[0]
        self.assertIs(led.propose("s", f2), r)
        self.assertEqual(r.state, APPLIED)
        self.assertEqual(r.patch["value"], "2026-09-01T00:00:00+00:00")

    def test_rejection_reopens_only_when_the_defect_changed(self):
        led = Ledger(self.path)
        f = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": "Bob"}))], SITE))[0]
        r = led.propose("s", f)
        led.decide(r.repair_id, False)
        self.assertEqual(led.propose("s", f).state, REJECTED)
        f2 = findings(audit_pages([body_page(graph({"@type": "ImageObject", "creator": "Alice"}))], SITE))[0]
        self.assertEqual(led.propose("s", f2).state, PENDING)

    def test_rename_identity_includes_the_target(self):
        a = make_repair_id("s", "pages", 1, "entity-fragmentation", "https://x/#n", {"op": "rename_id", "old": "https://x/#n", "new": PERSON})
        b = make_repair_id("s", "pages", 1, "entity-fragmentation", "https://x/#n", {"op": "rename_id", "old": "https://x/#n", "new": SITE.canonical_org_id})
        self.assertNotEqual(a, b)


class TestRetireGating(unittest.TestCase):
    def test_failed_post_content_read_does_not_retire(self):
        from cleanuprtx.audit import scan_source
        p = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        p.content_raw = None    # the context=edit read failed
        self.assertTrue(scan_source(p)[0])
        self.assertTrue(cli._needs_post_content(p))
        audited = {(q.kind, q.id) for q in [p] if scan_source(q)[0] and (q.content_raw is not None or not cli._needs_post_content(q))}
        self.assertEqual(audited, set())


class TestRuleCorrections(unittest.TestCase):
    def test_service_and_station_types_are_not_organizations(self):
        for t in ("GovernmentService", "BroadcastService", "TrainStation", "ProductGroup", "SportsTeam"):
            expected = t == "SportsTeam"
            self.assertEqual(is_org_type(t), expected, t)
        r = audit_pages([body_page(graph({"@type": "ImageObject", "creator": {"@type": "BroadcastService", "name": "x"}}))], SITE)
        self.assertEqual(len(findings(r, "object-field-type")), 1)

    def test_empty_type_is_repaired_not_noop(self):
        for empty in (None, "", []):
            f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@type": empty, "name": "Colten"}}))], SITE), "profile-parent-node")
            self.assertEqual(f[0].patch["value"]["@type"], "Person", repr(empty))
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": []}))], SITE), "profile-parent-node")
        self.assertIsNone(f[0].patch)

    def test_page_node_in_top_level_array_and_www_variant(self):
        doc = [{"@context": "https://schema.org", "@type": "WebPage", "url": "https://www.inspector-roofing.com/richard-nasser/", "dateModified": "bad"}]
        f = findings(audit_pages([body_page(doc)], SITE), "invalid-datetime")
        self.assertEqual(f[0].patch["value"], "2026-08-01T12:00:00+00:00")

    def test_tz_aware_modified_gmt_does_not_crash_stale_draft(self):
        p = page(None, status="draft", modified="2024-01-01T12:00:00+00:00", created="2024-01-01T12:00:00+00:00")
        r = audit_pages([p], SITE)
        self.assertEqual(len(findings(r, "stale-draft")), 1)
