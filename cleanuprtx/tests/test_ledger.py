from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from cleanuprtx.approvals import APPLIED, APPROVED, FAILED, PENDING, STALE, Ledger, LedgerError, make_repair_id
from cleanuprtx.audit import audit_pages
from tests.helpers import PERSON, SITE, body_page, findings, page


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class TestIds(unittest.TestCase):
    def test_same_defect_on_two_nodes_gets_two_ids(self):
        doc = graph({"@type": "ImageObject", "@id": "https://x/#i1", "creator": "Bob"},
                    {"@type": "ImageObject", "@id": "https://x/#i2", "creator": "Bob"})
        fs = findings(audit_pages([body_page(doc)], SITE), "object-field-type")
        self.assertEqual(len(fs), 2)
        with tempfile.TemporaryDirectory() as tmp:
            led = Ledger(Path(tmp) / "l.json")
            ids = {led.propose("s", f).repair_id for f in fs}
        self.assertEqual(len(ids), 2)

    def test_stable_across_runs(self):
        a = make_repair_id("s", "pages", 1, "r", "https://x/#n", {"op": "set", "key": "k", "value": 1})
        b = make_repair_id("s", "pages", 1, "r", "https://x/#n", {"op": "set", "key": "k", "value": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, make_repair_id("s", "posts", 1, "r", "https://x/#n", {"op": "set", "key": "k", "value": 1}))


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "approvals.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _repair(self):
        fs = findings(audit_pages([body_page(graph({"@type": "ProfilePage", "@id": "https://x/#pp"}))], SITE), "profile-parent-node")
        led = Ledger(self.path)
        r = led.propose("inspector-roofing", fs[0])
        led.save()
        return led, r

    def test_propose_skips_non_repairable(self):
        fs = findings(audit_pages([page(graph({"@type": "ProfilePage"}), plugin_cls="rank-math-schema")], SITE))
        led = Ledger(self.path)
        self.assertIsNone(led.propose("s", fs[0]))

    def test_saved_with_mode_600_and_dir_700(self):
        self._repair()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path.parent).st_mode), 0o700)
        self.assertFalse(list(self.path.parent.glob(".approvals-*")), "no temp files left behind")

    def test_round_trip_preserves_kind_target_patch(self):
        led, r = self._repair()
        led.decide(r.repair_id, True); led.save()
        again = Ledger(self.path)
        got = again.repairs[r.repair_id]
        self.assertEqual(got.state, APPROVED)
        self.assertEqual(got.kind, "pages")
        self.assertEqual(got.target["node_id"], "https://x/#pp")
        self.assertEqual(got.patch["key"], "mainEntity")
        self.assertEqual([x.repair_id for x in again.in_state(APPROVED)], [r.repair_id])

    def test_failed_repairs_are_retried_and_terminal_states_locked(self):
        led, r = self._repair()
        led.decide(r.repair_id, True)
        led.mark_failed(r.repair_id, "boom")
        self.assertEqual([x.repair_id for x in led.in_state(APPROVED)], [r.repair_id], "failed stays in the apply queue")
        led.mark_applied(r.repair_id, "ok")
        with self.assertRaises(ValueError):
            led.decide(r.repair_id, False)
        led2, r2 = Ledger(self.path), None
        led.mark_stale(r.repair_id, "gone")
        with self.assertRaises(ValueError):
            led.decide(r.repair_id, True)

    def test_corrupt_file_is_quarantined_with_clear_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(LedgerError) as cm:
            Ledger(self.path)
        self.assertTrue(self.path.with_suffix(".corrupt").exists())
        self.assertIn("audit --propose", str(cm.exception))

    def test_old_ledger_rows_with_unknown_fields_are_tolerated(self):
        self.path.parent.mkdir(parents=True)
        row = {"repair_id": "a", "site": "s", "kind": "pages", "rule": "r", "page_id": 1, "page_url": "u",
               "page_title": "t", "message": "m", "block_index": 0, "target": {}, "patch": {}, "bogus": 1}
        self.path.write_text(json.dumps({"repairs": {"a": row}}), encoding="utf-8")
        self.assertIn("a", Ledger(self.path).repairs)
