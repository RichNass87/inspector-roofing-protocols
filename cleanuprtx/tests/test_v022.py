"""Coverage for the v0.2.2 (round-3) fixes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleanuprtx import cli, wordpress
from cleanuprtx.approvals import APPLIED, APPROVED, FAILED, PENDING, STALE, Ledger, make_repair_id
from cleanuprtx.audit import audit_pages
from cleanuprtx.http import HttpError
from cleanuprtx.jsonld import Target, find_blocks
from cleanuprtx.wordpress import OwnershipLost
from tests.helpers import PERSON, SITE, body_page, findings, ld, page
from tests.test_adv_writepath import ApplyHarness, Site41, LINK

PP_ID = "https://inspector-roofing.com/richard-nasser/#pp"
OLD = "https://inspector-roofing.com/#author"


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class TestReCarryAcrossRuns(ApplyHarness):
    """WordPress keeps one autosave per page per user across time. A second run
    must carry the first run's staged repairs into its single write."""

    def test_second_run_carries_first_runs_repair(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="profile-parent-node")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        # Later: the owner approves the second repair without restoring the first.
        (rid_b,) = self.propose_all(content, rule="invalid-datetime")
        site2 = Site41(content.front_html, content.content_raw)   # post_content unchanged by an autosave
        rc, out = self.run_apply(site2)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site2.writes), 1)
        final = find_blocks(site2.writes[-1]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(final["mainEntity"]["@id"], PERSON, "run-1 repair carried into run-2 autosave")
        self.assertEqual(final["dateModified"], "2026-08-01T12:00:00+00:00")
        led = self.ledger()
        self.assertEqual({led.repairs[rid_a].state, led.repairs[rid_b].state}, {APPLIED})
        self.assertIn("carried", out)

    def test_carry_that_cannot_be_reapplied_blocks_the_page(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="profile-parent-node")
        self.run_apply(Site41(content.front_html, content.content_raw))
        (rid_b,) = self.propose_all(content, rule="invalid-datetime")
        # post_content was rewritten by hand: the ProfilePage node is gone.
        changed = body_page(graph({"@type": "Article", "@id": "https://x/#a", "dateModified": "yesterday"}))
        site = Site41(changed.front_html, changed.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [], "never stage a page without a repair it already holds")
        led = self.ledger()
        self.assertEqual(led.repairs[rid_a].state, APPLIED, "the earlier row keeps its state")
        self.assertEqual(led.repairs[rid_b].state, FAILED)
        self.assertIn("cannot be re-carried", led.repairs[rid_b].result)


class TestFoldOrder(ApplyHarness):
    def test_rename_before_set_in_ledger_order_still_folds_both(self):
        doc = graph({"@type": "Person", "@id": OLD, "name": "Richard Nasser", "dateModified": "yesterday"},
                    {"@type": "ProfilePage", "@id": PP_ID, "mainEntity": {"@id": OLD}})
        content = body_page(doc)
        ids = self.propose_all(content)
        led = self.ledger()
        # Force ledger order: rename first.
        rows = {k: v for k, v in led.repairs.items()}
        rename = [k for k, v in rows.items() if v.patch["op"] == "rename_id"]
        others = [k for k in rows if k not in rename]
        led.repairs = {k: rows[k] for k in rename + others}
        led.save()
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"]
        self.assertEqual(g[0]["@id"], PERSON)
        self.assertEqual(g[1]["mainEntity"], {"@id": PERSON}, "reference followed the rename")
        self.assertEqual({self.ledger().repairs[i].state for i in ids} - {APPLIED}, set())


class TestDraftPath(ApplyHarness):
    def test_include_drafts_repairs_reach_the_draft_write_path(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID})
        draft = page(doc, raw_doc=doc, status="draft")
        ids = self.propose_all(draft)
        self.assertEqual(len(ids), 1)
        site = Site41("", draft.content_raw, status="draft")
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        self.assertTrue(site.writes[0]["url"].endswith("/wp/v2/pages/41"))
        self.assertEqual(set(site.writes[0]["body"]), {"content"})
        self.assertEqual(self.ledger().repairs[ids[0]].state, APPLIED)


class TestIdlessResolution(ApplyHarness):
    def test_rejected_twin_is_never_written_after_a_shift(self):
        """Two id-less ImageObjects, same path shape, same defect. Reject the first,
        approve the second, then a plugin block appears ahead on the live page."""
        a = {"@type": "ImageObject", "contentUrl": "https://x/a.jpg", "creator": "Bob"}
        b = {"@type": "ImageObject", "contentUrl": "https://x/b.jpg", "creator": "Bob"}
        raw = "<!-- wp:html -->" + ld(a) + "<!-- /wp:html --><!-- wp:html -->" + ld(b) + "<!-- /wp:html -->"
        p = page(None, raw=raw, extra_front=ld(a) + ld(b))
        fs = findings(audit_pages([p], SITE), "object-field-type")
        self.assertEqual(len(fs), 2)
        led = self.ledger()
        ra, rb = (led.propose("inspector-roofing", f) for f in fs)
        led.decide(ra.repair_id, False)
        led.decide(rb.repair_id, True)
        led.save()
        rm = {"@type": "Organization", "@id": "https://inspector-roofing.com/#organization", "name": "IR"}
        live = "<html><head>" + ld(rm, "rank-math-schema") + ld(a) + ld(b) + "</head></html>"
        site = Site41(live, raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        blocks = find_blocks(site.writes[0]["body"]["content"])
        self.assertEqual(blocks[0].document["creator"], "Bob", "the rejected twin is untouched")
        self.assertEqual(blocks[1].document["creator"]["name"], "Bob")


class TestOwnershipLostIsStale(ApplyHarness):
    def test_block_gone_from_post_content_marks_stale_not_failed(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID})
        content = body_page(doc)
        (rid,) = self.propose_all(content)
        site = Site41(content.front_html, "<p>layout moved to the builder</p>")
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [])
        self.assertEqual(self.ledger().repairs[rid].state, STALE)


class TestLedgerDecisions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_repair_id_ignores_derived_values(self):
        a = make_repair_id("s", "pages", 1, "invalid-datetime", "https://x/#n", {"op": "set", "key": "dateModified", "value": "2026-08-01T00:00:00+00:00"})
        b = make_repair_id("s", "pages", 1, "invalid-datetime", "https://x/#n", {"op": "set", "key": "dateModified", "value": "2026-09-01T00:00:00+00:00"})
        self.assertEqual(a, b)

    def test_rejection_survives_and_changed_patch_reopens_approval(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        f = findings(audit_pages([body_page(doc)], SITE), "invalid-datetime")[0]
        led = Ledger(self.path)
        r = led.propose("s", f)
        led.decide(r.repair_id, True)
        # The page was saved again: same defect, new derived value.
        f2 = findings(audit_pages([body_page(doc, modified="2026-09-05T08:00:00")], SITE), "invalid-datetime")[0]
        r2 = led.propose("s", f2)
        self.assertIs(r2, r)
        self.assertEqual(r.state, PENDING)
        self.assertIn("re-approve", r.result)
        led.decide(r.repair_id, False)
        self.assertEqual(led.propose("s", f2).state, "rejected")

    def test_retire_unreported_only_for_audited_pages(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))], SITE))[0]
        led = Ledger(self.path)
        r = led.propose("s", f)
        retired = led.retire_unreported("s", audited_pages={("pages", 41)}, reported_ids=set())
        self.assertEqual(retired, [r.repair_id])
        self.assertEqual(r.state, STALE)
        led2 = Ledger(self.path)
        r2 = led2.propose("s", f)
        self.assertEqual(led2.retire_unreported("s", audited_pages={("pages", 99)}, reported_ids=set()), [],
                         "a page that was not audited says nothing about its rows")

    def test_lock_rereads_from_disk(self):
        led_a = Ledger(self.path)
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))], SITE))[0]
        led_b = Ledger(self.path)
        rb = led_b.propose("s", f)
        led_b.decide(rb.repair_id, False)
        led_b.save()
        with led_a.lock():
            self.assertEqual(led_a.repairs[rb.repair_id].state, "rejected")


class TestResumeCache(unittest.TestCase):
    def test_failed_fetches_are_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "CACHE_DIR", Path(tmp)):
            cache = cli.FrontCache("x")
            p = page(None)
            p.front_html, p.front_status = None, 503
            cache.put(p)
            cache.flush()
            self.assertIsNone(cli.FrontCache("x").get(p))
            ok = page(None)
            ok.front_html, ok.front_status = "<html>", 200
            cache = cli.FrontCache("x"); cache.put(ok); cache.flush()
            self.assertEqual(cli.FrontCache("x").get(ok)["html"], "<html>")


class TestRules(unittest.TestCase):
    def test_organization_subtypes_are_entities(self):
        for t in ("GeneralContractor", "Plumber", "MedicalBusiness", "NewsMediaOrganization", "SportsTeam", "HVACBusiness"):
            r = audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@type": t, "name": "Acme"}}))], SITE)
            self.assertEqual(findings(r, "profile-parent-node"), [], t)
            r = audit_pages([body_page(graph({"@type": "Article", "publisher": {"@type": t, "name": "Acme"}}))], SITE)
            self.assertEqual(findings(r, "object-field-type"), [], t)

    def test_dict_mainentity_keeps_the_named_identity(self):
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"name": "Colten Smith"}}))], SITE), "profile-parent-node")
        self.assertEqual(f[0].patch["value"], {"@type": "Person", "name": "Colten Smith"})
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@id": "https://x/#ghost", "name": "Colten Smith"}}))], SITE), "profile-parent-node")
        self.assertEqual(f[0].patch["value"], {"@type": "Person", "@id": "https://x/#ghost", "name": "Colten Smith"},
                         "typing a named node makes it a definition; the identity is kept")
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@id": "https://x/#ghost"}}))], SITE), "profile-parent-node")
        self.assertIsNone(f[0].patch, "a dangling @id with no name is the author's to fix")
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "mainEntity": {"@type": "WebPage", "name": "X"}}))], SITE), "profile-parent-node")
        self.assertIsNone(f[0].patch, "a wrong @type is never silently retyped")

    def test_nested_dates_are_never_patched_with_the_pages_dates(self):
        doc = graph({"@type": "LocalBusiness", "name": "IR", "review": [{"@type": "Review", "datePublished": "May 3, 2019"}]})
        f = findings(audit_pages([body_page(doc)], SITE), "invalid-datetime")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch)
        self.assertIn("nested", f[0].hand_edit)
        f = findings(audit_pages([body_page(graph({"@type": "WebPage", "dateModified": "bad"}))], SITE), "invalid-datetime")
        self.assertEqual(f[0].patch["derived"], "modified_gmt")

    def test_body_references_follow_a_rename_and_plugin_references_gate_it(self):
        author = {"@type": "Person", "@id": OLD, "name": "Richard Nasser"}
        raw = "<!-- wp:html -->" + ld(graph(author)) + "<!-- /wp:html --><!-- wp:html -->" + ld(graph({"@type": "Article", "author": {"@id": OLD}})) + "<!-- /wp:html -->"
        p = page(None, raw=raw, extra_front=ld(graph(author)) + ld(graph({"@type": "Article", "author": {"@id": OLD}})))
        f = findings(audit_pages([p], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertIsNotNone(f[0].patch, "a body reference follows the rename")
        self.assertIn("renamed with it", f[0].detail)

    def test_plugin_author_pattern_is_a_notice_with_honest_hand_edit(self):
        node = {"@type": "Person", "@id": "https://inspector-roofing.com/author/richard/", "url": "https://inspector-roofing.com/author/richard/", "name": "Richard Nasser"}
        p = page(graph(node), raw="<p>x</p>", plugin_cls="rank-math-schema")
        f = findings(audit_pages([p], SITE), "entity-fragmentation")
        self.assertEqual(f[0].severity, "notice")
        self.assertIn("author archive", f[0].hand_edit)


class TestInventoryMidWalk(unittest.TestCase):
    def test_refusal_after_items_arrived_propagates(self):
        client = wordpress.WordPressClient(SITE)
        client._auth = "Basic x"
        warnings = []

        def fake(url, **kw):
            if "offset=0&" in url:
                return ([{"id": i, "type": "page", "content": {"rendered": ""}, "status": "publish"} for i in range(25)], {"x-wp-total": "900"})
            raise HttpError(403, url, json.dumps({"code": "rest_forbidden"}))

        got = []
        with mock.patch.object(wordpress, "request_json_with_headers", fake), self.assertRaises(HttpError):
            for c in client.iter_all(kinds=["pages"], warn=warnings.append):
                got.append(c)
        self.assertEqual(len(got), 25)
        self.assertEqual(warnings, [])
