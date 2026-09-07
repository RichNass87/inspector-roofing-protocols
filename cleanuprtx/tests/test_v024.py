"""Coverage for the v0.2.4 (round-5) fixes: staged repairs that lose their
autosave, withdrawal, identity across id changes, the absorb gate, and rule
robustness."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleanuprtx import cli, wordpress
from cleanuprtx.approvals import (APPLIED, APPROVED, FAILED, PENDING, REJECTED, STALE, Ledger,
                                  make_repair_id, patch_identity)
from cleanuprtx.audit import audit_pages, default_hand_edit
from cleanuprtx.jsonld import AlreadyApplied, PatchError, find_blocks
from cleanuprtx.schema_types import is_org_type
from tests.helpers import PERSON, SITE, body_page, findings, ld, page
from tests.test_adv_writepath import ApplyHarness, Site41, LINK
from tests.test_v023 import Site41M, graph

PP_ID = "https://inspector-roofing.com/richard-nasser/#pp"
OLD = "https://inspector-roofing.com/#author"
WP_ID = "https://inspector-roofing.com/richard-nasser/#webpage"
LATER = "2026-09-04T09:00:00"


class TestSupersededAutosave(ApplyHarness):
    """A staged repair whose page was saved again (WordPress drops the autosave)
    while the defect stayed must come back for a decision, not sit APPLIED forever."""

    def _stage_date_fix(self):
        doc = graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad", "name": "x"})
        content = body_page(doc)
        (rid,) = self.propose_all(content, rule="invalid-datetime")
        rc, out = self.run_apply(Site41(content.front_html, content.content_raw))
        self.assertEqual(rc, 0, out)
        return doc, rid

    def test_propose_reopens_after_a_later_save_and_keeps_when_unchanged(self):
        doc, rid = self._stage_date_fix()
        led = self.ledger()
        f_same = findings(audit_pages([body_page(doc)], SITE), "invalid-datetime")[0]
        led.propose("inspector-roofing", f_same, modified_gmt="2026-08-01T12:00:00")
        self.assertEqual(led.repairs[rid].state, APPLIED, "autosave still offered: keep it")
        f_later = findings(audit_pages([body_page(doc, modified=LATER)], SITE), "invalid-datetime")[0]
        r = led.propose("inspector-roofing", f_later, modified_gmt=LATER)
        self.assertIs(r, led.repairs[rid])
        self.assertEqual(r.state, PENDING)
        self.assertIn("superseded", r.result)
        self.assertEqual(r.staged_modified_gmt, "")
        self.assertEqual(r.patch["value"], LATER + "+00:00")
        self.assertEqual([x.repair_id for x in led.in_state(PENDING)], [rid], "it is listed again")

    def test_full_loop_stage_save_reopen_approve_apply(self):
        doc, rid = self._stage_date_fix()
        later = body_page(doc, modified=LATER)
        led = self.ledger()
        f = findings(audit_pages([later], SITE), "invalid-datetime")[0]
        led.propose("inspector-roofing", f, modified_gmt=LATER)
        led.decide(rid, True)
        led.save()
        site = Site41M(later.front_html, later.content_raw, modified=LATER)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(g["dateModified"], LATER + "+00:00")
        self.assertEqual(self.ledger().repairs[rid].state, APPLIED)

    def test_withdrawn_staged_repair_is_not_carried(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="profile-parent-node")
        self.run_apply(Site41(content.front_html, content.content_raw))
        led = self.ledger()
        led.decide(rid_a, False)                       # the owner discarded it on purpose
        self.assertEqual(led.repairs[rid_a].state, REJECTED)
        led.save()
        (rid_b,) = self.propose_all(content, rule="invalid-datetime")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        g = find_blocks(site.writes[-1]["body"]["content"])[0].document["@graph"][0]
        self.assertNotIn("mainEntity", g, "withdrawn: not written again without a fresh approval")
        self.assertEqual(self.ledger().repairs[rid_a].state, REJECTED)
        # ...and the rejection holds on the next audit.
        led = self.ledger()
        f = findings(audit_pages([content], SITE), "profile-parent-node")[0]
        self.assertEqual(led.propose("inspector-roofing", f).state, REJECTED)


class TestAbsorbGate(ApplyHarness):
    """Only a page saved since staging can have absorbed a repair; a defect the
    rules still report is never absorbed, whatever its repair id today."""

    def _stage_rename(self):
        doc = graph({"@type": "Person", "@id": OLD, "name": "Richard Nasser"},
                    {"@type": "WebPage", "@id": WP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="entity-fragmentation")
        rc, out = self.run_apply(Site41(content.front_html, content.content_raw))
        self.assertEqual(rc, 0, out)
        return doc, content, rid_a

    def test_withdrawn_patch_blocks_the_page_when_unchanged(self):
        doc, content, rid_a = self._stage_rename()
        # Rank Math now emits a reference to the old @id on the live page (no save).
        plug = ld({"@type": "Article", "author": {"@id": OLD}}, cls="rank-math-schema")
        live = page(doc, raw_doc=doc, extra_front=plug)
        (rid_b,) = self.propose_all(live, rule="invalid-datetime")
        site = Site41(live.front_html, live.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [], "the earlier autosave must not be replaced without the rename")
        led = self.ledger()
        self.assertTrue(led.repairs[rid_a].result.startswith("autosave:"))
        self.assertEqual(led.repairs[rid_b].state, FAILED)
        self.assertIn("can no longer be applied", led.repairs[rid_b].result)

    def test_withdrawn_patch_after_a_save_retires_the_carry_and_others_proceed(self):
        doc, content, rid_a = self._stage_rename()
        plug = ld({"@type": "Article", "author": {"@id": OLD}}, cls="rank-math-schema")
        live = page(doc, raw_doc=doc, extra_front=plug, modified=LATER)
        (rid_b,) = self.propose_all(live, rule="invalid-datetime")
        site = Site41M(live.front_html, live.content_raw, modified=LATER)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        led = self.ledger()
        self.assertEqual(led.repairs[rid_a].state, STALE)
        self.assertIn("no longer fits after a later save", led.repairs[rid_a].result)
        self.assertEqual(led.repairs[rid_b].state, APPLIED)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"]
        self.assertEqual(g[0]["@id"], OLD, "the rename is not written around the plugin's reference")
        self.assertEqual(g[1]["dateModified"], LATER + "+00:00")

    def test_v022_rename_id_is_migrated_and_carried_not_absorbed(self):
        doc, content, rid_a = self._stage_rename()
        led = self.ledger()
        row = led.repairs[rid_a]
        # Re-key the row the way v0.2.2 hashed it (old @id only).
        import hashlib
        from cleanuprtx.jsonld import Target
        old_style = hashlib.sha256(json.dumps(
            ["inspector-roofing", "pages", 41, row.rule, Target.from_dict(row.target).key(),
             {"op": "rename_id", "old": row.patch["old"]}], sort_keys=True).encode()).hexdigest()[:8]
        self.assertNotEqual(old_style, rid_a)
        data = json.loads(self.ledger_path.read_text())
        data["repairs"] = {old_style: dict(data["repairs"][rid_a], repair_id=old_style)}
        self.ledger_path.write_text(json.dumps(data))
        led = self.ledger()
        self.assertEqual(list(led.repairs), [rid_a], "loaded under the current id")
        self.assertEqual(led.repairs[rid_a].repair_id, rid_a)
        (rid_b,) = self.propose_all(content, rule="invalid-datetime")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"]
        self.assertEqual(g[0]["@id"], PERSON, "the staged rename rides the new autosave")
        self.assertNotIn("absorbed", self.ledger().repairs[rid_a].result)

    def test_migration_keeps_the_staged_row_over_a_pending_duplicate(self):
        doc, content, rid_a = self._stage_rename()
        import hashlib
        from cleanuprtx.jsonld import Target
        data = json.loads(self.ledger_path.read_text())
        row = data["repairs"][rid_a]
        old_style = hashlib.sha256(json.dumps(
            ["inspector-roofing", "pages", 41, row["rule"], Target.from_dict(row["target"]).key(),
             {"op": "rename_id", "old": row["patch"]["old"]}], sort_keys=True).encode()).hexdigest()[:8]
        pending = dict(row, repair_id=rid_a, state=PENDING, result="", applied_at="")
        data["repairs"] = {old_style: dict(row, repair_id=old_style), rid_a: pending}
        self.ledger_path.write_text(json.dumps(data))
        led = self.ledger()
        self.assertEqual(list(led.repairs), [rid_a])
        self.assertEqual(led.repairs[rid_a].state, APPLIED)
        self.assertTrue(led.repairs[rid_a].result.startswith("autosave:"))

    def test_idless_node_carried_when_a_block_is_inserted_ahead(self):
        img = {"@type": "ImageObject", "contentUrl": "https://x/a.jpg", "creator": "Bob"}
        p = page(None, raw=f"<!-- wp:html -->{ld(img)}<!-- /wp:html -->", extra_front=ld(img))
        (rid_a,) = self.propose_all(p, rule="object-field-type")
        rc, out = self.run_apply(Site41(p.front_html, p.content_raw))
        self.assertEqual(rc, 0, out)
        # Owner inserted a block ahead of it and saved: the same defect now hashes differently.
        pp = graph({"@type": "ProfilePage", "@id": PP_ID})
        raw2 = f"<!-- wp:html -->{ld(pp)}<!-- /wp:html --><!-- wp:html -->{ld(img)}<!-- /wp:html -->"
        p2 = page(None, raw=raw2, extra_front=ld(pp) + ld(img), modified=LATER)
        fs = findings(audit_pages([p2], SITE), "object-field-type")
        new_id = make_repair_id("inspector-roofing", "pages", 41, fs[0].rule, fs[0].target.key(), fs[0].patch)
        self.assertNotEqual(new_id, rid_a)
        (rid_b,) = self.propose_all(p2, rule="profile-parent-node")
        site = Site41M(p2.front_html, raw2, modified=LATER)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        blocks = find_blocks(site.writes[0]["body"]["content"])
        self.assertEqual(blocks[1].document["creator"]["name"], "Bob", "carried by fingerprint")
        self.assertEqual(self.ledger().repairs[rid_a].state, APPLIED)
        self.assertTrue(self.ledger().repairs[rid_a].result.startswith("autosave:"))

    def test_unchanged_page_with_unmatched_carry_is_verified_not_absorbed(self):
        doc, content, rid_a = self._stage_rename()
        led = self.ledger()
        led.repairs[rid_a].rule = "some-retired-rule"      # id no longer computed by any rule
        led.save()
        (rid_b,) = self.propose_all(content, rule="invalid-datetime")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"]
        self.assertEqual(g[0]["@id"], PERSON, "the stored patch was carried and verified")
        (row,) = [r for r in self.ledger().repairs.values() if r.rule == "some-retired-rule"]
        self.assertNotIn("absorbed", row.result)
        self.assertTrue(row.result.startswith("autosave:"))

    def test_retire_path_after_a_later_save_when_the_patch_no_longer_fits(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        (rid_a,) = self.propose_all(content, rule="profile-parent-node")
        self.run_apply(Site41(content.front_html, content.content_raw))
        later = body_page(doc, modified=LATER)
        (rid_b,) = self.propose_all(later, rule="invalid-datetime")
        real = wordpress.WordPressClient.prepare_block_repair

        def misfit(self_, c, fb, t, patch, working_raw=None, raw_block_index=None):
            if patch.get("key") == "mainEntity":
                raise PatchError("node shape changed")
            return real(self_, c, fb, t, patch, working_raw, raw_block_index)

        site = Site41M(later.front_html, later.content_raw, modified=LATER)
        with mock.patch.object(wordpress.WordPressClient, "prepare_block_repair", misfit):
            rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        led = self.ledger()
        self.assertEqual(led.repairs[rid_a].state, STALE)
        self.assertIn("no longer fits after a later save", led.repairs[rid_a].result)
        self.assertIn("marked stale", out)
        self.assertEqual(led.repairs[rid_b].state, APPLIED)
        g = find_blocks(site.writes[0]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(g["dateModified"], LATER + "+00:00")
        self.assertNotIn("mainEntity", g)


class TestAlreadyAppliedRecheck(ApplyHarness):
    def test_value_produced_by_the_fold_rides_the_write(self):
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        rid_a, rid_b = self.propose_all(content)
        real = wordpress.WordPressClient.prepare_block_repair
        seen = []

        def fold_made_it(self_, c, fb, t, patch, working_raw=None, raw_block_index=None):
            seen.append((patch.get("key"), working_raw is not None))
            if patch.get("key") == "dateModified" and working_raw is not None:
                raise AlreadyApplied("already there")     # judged against the fold, not the page
            return real(self_, c, fb, t, patch, working_raw, raw_block_index)

        site = Site41(content.front_html, content.content_raw, post_error=PatchError("write refused"))
        with mock.patch.object(wordpress.WordPressClient, "prepare_block_repair", fold_made_it):
            rc, out = self.run_apply(site)
        led = self.ledger()
        self.assertIn(("dateModified", False), seen, "re-checked against the original post_content")
        self.assertEqual({led.repairs[rid_a].state, led.repairs[rid_b].state}, {FAILED},
                         "neither row may claim to be on the page when the write failed")


class TestLedgerAbsorbAtAuditTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _stage(self):
        led = Ledger(self.path)
        f = findings(audit_pages([body_page(graph({"@type": "WebPage", "@id": WP_ID, "dateModified": "bad"}))], SITE))[0]
        r = led.propose("s", f)
        led.mark_applied(r.repair_id, "autosave: https://x/edit", "2026-08-01T12:00:00")
        return led, r, f

    def test_fixed_page_after_a_save_marks_the_row_absorbed_then_regression_reopens(self):
        led, r, f = self._stage()
        done = led.absorb_unreported("s", {("pages", 41)}, set(), set(), {("pages", 41): LATER})
        self.assertEqual(done, [r.repair_id])
        self.assertEqual(r.state, APPLIED)
        self.assertIn("absorbed", r.result)
        self.assertEqual(r.staged_modified_gmt, "")
        self.assertIs(led.propose("s", f, modified_gmt="2026-09-05T00:00:00"), r)
        self.assertEqual(r.state, PENDING)
        self.assertIn("reported again", r.result)

    def test_unsaved_page_is_left_alone_and_still_reported_defect_is_retired(self):
        led, r, f = self._stage()
        self.assertEqual(led.absorb_unreported("s", {("pages", 41)}, set(), set(), {("pages", 41): "2026-08-01T12:00:00"}), [])
        self.assertTrue(r.result.startswith("autosave:"))
        from cleanuprtx.approvals import defect_keys
        still = defect_keys(f.rule, f.target, None)      # reported, but the patch was withdrawn
        led.absorb_unreported("s", {("pages", 41)}, set(), still, {("pages", 41): LATER})
        self.assertEqual(r.state, STALE)
        self.assertIn("still reported", r.result)

    def test_rejected_row_with_a_null_patch_does_not_crash_propose(self):
        led = Ledger(self.path)
        f = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))], SITE))[0]
        r = led.propose("s", f)
        led.decide(r.repair_id, False)
        r.patch = None
        led.save()
        led = Ledger(self.path)
        self.assertEqual(led.propose("s", f).state, REJECTED, "same defect: the rejection stands, no crash")


class TestRules(unittest.TestCase):
    def test_plugin_person_with_list_url_is_a_notice_not_a_crash(self):
        arch = "https://inspector-roofing.com/author/richard/"
        p = page(graph({"@type": "Person", "@id": arch, "name": "Richard Nasser", "url": [arch]}),
                 plugin_cls="rank-math-schema", raw="")
        fs = findings(audit_pages([p], SITE), "entity-fragmentation")
        self.assertEqual([f.severity for f in fs], ["notice"])
        p = page(graph({"@type": "Person", "@id": arch, "name": "Richard Nasser", "url": 5}),
                 plugin_cls="rank-math-schema", raw="")
        fs = findings(audit_pages([p], SITE), "entity-fragmentation")
        self.assertEqual([f.severity for f in fs], ["warning"])

    def test_organisation_is_not_a_schema_org_type(self):
        self.assertFalse(is_org_type("Organisation"))
        p = body_page({"@type": "ImageObject", "contentUrl": "https://x/a.jpg",
                       "creator": {"@type": "Organisation", "name": "x"}})
        self.assertEqual(len(findings(audit_pages([p], SITE), "object-field-type")), 1)

    def test_wrong_non_string_type_on_main_entity_is_report_only(self):
        for bad in (0, False, {"x": 1}):
            p = body_page(graph({"@type": "ProfilePage", "@id": PP_ID,
                                 "mainEntity": {"@type": bad, "name": "Richard Nasser"}}))
            fs = findings(audit_pages([p], SITE), "profile-parent-node")
            self.assertEqual(len(fs), 1, bad)
            self.assertIsNone(fs[0].patch, bad)
        p = body_page(graph({"@type": "ProfilePage", "@id": PP_ID, "mainEntity": {"@type": "", "name": "Richard Nasser"}}))
        self.assertIsNotNone(findings(audit_pages([p], SITE), "profile-parent-node")[0].patch)

    def test_page_node_in_array_of_graphs_and_protocol_relative_url(self):
        p = body_page([graph({"@type": "WebPage", "url": LINK, "dateModified": "bad"})])
        f = findings(audit_pages([p], SITE), "invalid-datetime")[0]
        self.assertEqual(f.patch["value"], "2026-08-01T12:00:00+00:00")
        p = body_page([graph({"@type": "WebPage", "url": "https://elsewhere/x/", "dateModified": "bad"})])
        self.assertIsNone(findings(audit_pages([p], SITE), "invalid-datetime")[0].patch)
        p = body_page({"@type": "WebPage", "url": "//inspector-roofing.com/richard-nasser/", "dateModified": "bad"})
        self.assertEqual(findings(audit_pages([p], SITE), "invalid-datetime")[0].patch["derived"], "modified_gmt")

    def test_rank_math_entity_hand_edit_is_honest_about_ids(self):
        text = default_hand_edit("RankMath", "entity-fragmentation")
        self.assertIn("/#organization", text)
        self.assertIn("Schema tab", text)
        self.assertNotIn("Local SEO (Person/Organization name", text)


class TestFrontCacheCompact(unittest.TestCase):
    def test_last_line_wins_and_only_kept_links_survive(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cli, "CACHE_DIR", Path(tmp)):
            c = cli.FrontCache("s")
            lines = [{"link": "https://a/", "modified_gmt": "1", "html": "old", "at": 1},
                     {"link": "https://b/", "modified_gmt": "1", "html": "b", "at": 1},
                     {"link": "https://a/", "modified_gmt": "2", "html": "new", "at": 2}]
            c.path.parent.mkdir(parents=True, exist_ok=True)
            c.path.write_text("garbage\n" + "".join(json.dumps(x) + "\n" for x in lines))
            c.compact({"https://a/"})
            kept = [json.loads(l) for l in c.path.read_text().splitlines()]
            self.assertEqual(kept, [lines[2]])
            self.assertEqual(c.data, {}, "compaction never loads pages into memory")

    def test_type_filtered_audit_does_not_compact(self):
        src = open(cli.__file__, encoding="utf-8").read()
        guard = src.split("cache.compact(")[0][-200:]
        self.assertIn("kinds is None", guard, "compact only after a complete, unrestricted inventory")


if __name__ == "__main__":
    unittest.main()
