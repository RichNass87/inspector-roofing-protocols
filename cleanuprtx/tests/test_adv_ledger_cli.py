"""Adversarial tests for the approval ledger (approvals.py) and the CLI flows
around it (cli.py: audit --propose, pending, approve/reject, apply, hand-edits,
and the argument parser).

Every assertion is a behaviour the README or a docstring promises:

* README / Ledger: "Repair IDs are content-hashed over site, type, page, rule,
  the node's @id and the patch, so re-running an audit never renumbers a
  pending decision"; "Repairs whose target vanished are marked stale; failed
  applies stay in the queue and are retried on the next apply"; the file is
  "0600, directory 0700, written atomically".
* approvals.py: "An audit proposes repairs, each repair is approved
  individually by ID, and only approved repairs are ever applied";
  Ledger.propose: "Existing decisions are kept"; Ledger.load: an unreadable
  ledger is quarantined with a LedgerError that names the fix.
* cli.cmd_apply: "Stage approved repairs ... Never publishes"; the output
  after --propose reports how many repairs are NEW.
* README / Use: `hand-edits audit.json` lists "Everything that must be fixed in
  Rank Math / the builder, grouped by screen" from a `--json` audit.
* argparse help: `--limit ... 0 = all`; `--types` is "comma-separated REST
  bases"; `--no-color` is accepted before and after the subcommand.

No network, no Keychain, no subprocess. WordPressClient is replaced at the
cli import site by an in-memory stub; the ledger lives in a temp directory.
"""

from __future__ import annotations

import functools
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from cleanuprtx import cli, report
from cleanuprtx.approvals import (APPLIED, APPROVED, FAILED, PENDING, REJECTED, STALE, Ledger,
                                  LedgerError, Repair, make_repair_id)
from cleanuprtx.audit import audit_pages
from cleanuprtx.jsonld import find_blocks
from cleanuprtx.wordpress import Content
from tests.helpers import PERSON, SITE, body_page, findings, ld, page

PP_ID = "https://inspector-roofing.com/richard-nasser/#pp"
PP = {"@context": "https://schema.org", "@type": "ProfilePage", "@id": PP_ID, "name": "R"}
RANK_MATH_ORG = {"@context": "https://schema.org", "@graph": [
    {"@type": "Organization", "@id": "https://inspector-roofing.com/#organization",
     "name": "Inspector Roofing and Restoration"}]}


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


def with_plugin_block_first(content: Content, plugin_doc: Any = RANK_MATH_ORG) -> Content:
    """Same page, but the SEO plugin now emits its own block ahead of the body
    block on the live page: every body block index shifts by one."""
    body_blocks = "".join(ld(b.document) for b in find_blocks(content.front_html))
    content.front_html = ("<html><head><title>T</title>" + ld(plugin_doc, "rank-math-schema")
                          + body_blocks + "</head><body></body></html>")
    return content


def first_repairable(content: Content, rule: Optional[str] = None):
    fs = [f for f in findings(audit_pages([content], SITE), rule) if f.is_auto_fixable]
    assert fs, "fixture must produce a repairable finding"
    return fs[0]


class StubWordPress:
    """In-memory stand-in for WordPressClient as cmd_apply and cmd_audit use it.

    Holds one live page per (kind, id). Records every stage_block_repair call
    and answers ("autosave", edit_link) without any HTTP.
    """

    def __init__(self, pages: Optional[List[Content]] = None, stage_error: Optional[Exception] = None):
        self.pages: Dict[Any, Content] = {(p.kind, p.id): p for p in (pages or [])}
        self.staged: List[Dict[str, Any]] = []
        self.stage_error = stage_error
        self.iter_kinds: List[Any] = []
        self.constructed_for: List[str] = []

    # factory used in place of the WordPressClient class
    def __call__(self, site):
        self.constructed_for.append(site.slug)
        return self

    # --- what cmd_audit needs -------------------------------------------
    def iter_all(self, kinds=None, progress=None, warn=None):
        self.iter_kinds.append(kinds)
        for p in self.pages.values():
            yield p

    def load_front_html(self, content, delay=0.0):
        pass   # fixtures already carry front_html

    def load_content_raw(self, content):
        pass   # fixtures already carry content_raw

    # --- what cmd_apply needs -------------------------------------------
    def get_content(self, kind, content_id):
        return self.pages.get((kind, content_id))

    def stage_block_repair(self, content, front_block, target, patch):
        return self.stage_content(content, self.prepare_block_repair(content, front_block, target, patch))

    # v0.2.1: cmd_apply folds every repair on a page into one write.
    def prepare_block_repair(self, content, front_block, target, patch, working_raw=None, raw_block_index=None):
        self.staged.append({"id": content.id, "block": front_block.index, "target": target.to_dict(), "patch": patch})
        if self.stage_error:
            raise self.stage_error
        return (working_raw or content.content_raw or "") + "<!-- patched -->"

    def stage_content(self, content, patched_raw):
        self.writes = getattr(self, "writes", []) + [{"id": content.id, "content": patched_raw}]
        return "autosave", content.edit_link


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "home" / ".cleanuprtx" / "approvals.json"

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self) -> Ledger:
        return Ledger(self.ledger_path)

    def propose(self, content: Content, approve: bool = False, rule: Optional[str] = None, site: str = "inspector-roofing") -> str:
        f = first_repairable(content, rule)
        led = self.ledger()
        r = led.propose(site, f)
        if approve:
            led.decide(r.repair_id, True)
        led.save()
        return r.repair_id

    def run_cli(self, argv, wp: Optional[StubWordPress] = None):
        wp = wp or StubWordPress()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "Ledger", functools.partial(Ledger, self.ledger_path)), \
             mock.patch.object(cli, "WordPressClient", wp), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Ledger: identity across re-audits
# ---------------------------------------------------------------------------

class TestProposeIdentity(Harness):
    def test_reproposing_after_block_shift_keeps_id_and_approval_updates_index(self):
        """README: re-running an audit never renumbers a pending decision.
        The node has an @id; a Rank Math block appearing ahead of it on the
        live page must change block_index on the SAME repair, not create one."""
        content = body_page(PP)
        rid = self.propose(content, approve=True)
        shifted = with_plugin_block_first(body_page(PP))
        f = first_repairable(shifted, "profile-parent-node")
        self.assertEqual(f.block_index, 1)
        led = self.ledger()
        r = led.propose("inspector-roofing", f)
        led.save()
        self.assertEqual(r.repair_id, rid)
        self.assertEqual(r.state, APPROVED, "existing decisions are kept")
        self.assertEqual(r.block_index, 1, "block index follows the live page")
        self.assertEqual(r.target["block"], 1)
        self.assertEqual(len(self.ledger().repairs), 1)

    def test_reproposing_after_block_shift_for_node_without_id_does_not_renumber(self):
        """Same promise for a node that has no @id - the shape of a hand-pasted
        ProfilePage. The README promises the hash is over site, type, page, rule,
        node identity and patch; a plugin block appearing first on the live page
        changes none of those, so the approved repair must be found again rather
        than a second pending copy created."""
        doc = {"@context": "https://schema.org", "@type": "ProfilePage", "name": "R"}
        content = body_page(doc)
        rid = self.propose(content, approve=True)
        shifted = with_plugin_block_first(body_page(doc))
        f = first_repairable(shifted, "profile-parent-node")
        self.assertEqual(f.block_index, 1)
        led = self.ledger()
        r = led.propose("inspector-roofing", f)
        self.assertEqual(r.repair_id, rid, "a block shift renumbered an approved decision")
        self.assertEqual(r.state, APPROVED)
        self.assertEqual(len(led.repairs), 1, "no duplicate pending copy")

    def test_rejected_decision_survives_reproposal_and_is_not_pending(self):
        content = body_page(PP)
        rid = self.propose(content)
        led = self.ledger()
        led.decide(rid, False)
        led.save()
        led = self.ledger()
        r = led.propose("inspector-roofing", first_repairable(body_page(PP)))
        self.assertEqual(r.state, REJECTED)
        self.assertEqual(led.in_state(PENDING), [])

    def test_same_defect_on_two_sites_never_collides(self):
        f = first_repairable(body_page(PP))
        led = self.ledger()
        a = led.propose("inspector-roofing", f).repair_id
        b = led.propose("pnagolfcarts", f).repair_id
        self.assertNotEqual(a, b)
        self.assertEqual({r.site for r in led.in_state(PENDING)}, {"inspector-roofing", "pnagolfcarts"})
        self.assertEqual([r.repair_id for r in led.in_state(PENDING, "pnagolfcarts")], [b])

    def test_make_repair_id_is_order_insensitive_over_patch_keys(self):
        p1 = {"op": "set", "key": "k", "value": 1, "expect": None}
        p2 = {"expect": None, "value": 1, "key": "k", "op": "set"}
        self.assertEqual(make_repair_id("s", "pages", 1, "r", "n", p1), make_repair_id("s", "pages", 1, "r", "n", p2))


# ---------------------------------------------------------------------------
# Ledger: state machine
# ---------------------------------------------------------------------------

class TestStateMachine(Harness):
    def test_failed_is_in_the_approved_queue_and_only_there(self):
        rid = self.propose(body_page(PP), approve=True)
        led = self.ledger()
        led.mark_failed(rid, "503")
        led.mark_failed(rid, "503 again")
        r = led.repairs[rid]
        self.assertEqual(r.state, FAILED)
        self.assertEqual(r.attempts, 2)
        self.assertEqual(r.result, "503 again")
        self.assertEqual([x.repair_id for x in led.in_state(APPROVED)], [rid], "retried on the next apply")
        self.assertEqual([x.repair_id for x in led.in_state(FAILED)], [rid])
        self.assertEqual(led.in_state(PENDING), [])
        self.assertEqual(led.in_state(APPLIED), [])
        self.assertEqual(led.in_state(APPROVED, "pnagolfcarts"), [], "site filter applies to the failed queue too")

    def test_failed_repair_can_still_be_rejected_and_leaves_the_queue(self):
        rid = self.propose(body_page(PP), approve=True)
        led = self.ledger()
        led.mark_failed(rid, "boom")
        led.decide(rid, False)
        self.assertEqual(led.repairs[rid].state, REJECTED)
        self.assertEqual(led.in_state(APPROVED), [])

    def test_approval_can_be_withdrawn_but_rejected_and_applied_are_final(self):
        """TERMINAL = {applied, rejected, stale}: an approval may still be turned
        into a rejection before apply, but nothing comes back from a terminal state."""
        rid = self.propose(body_page(PP))
        led = self.ledger()
        led.decide(rid, True)
        led.decide(rid, False)
        self.assertEqual(led.repairs[rid].state, REJECTED)
        for verdict in (True, False):
            with self.assertRaises(ValueError):
                led.decide(rid, verdict)
        self.assertEqual(led.in_state(APPROVED), [])

        rid2 = self.propose(body_page(PP, pid=42), approve=True)
        led = self.ledger()
        led.mark_applied(rid2, "autosave: link")
        for verdict in (True, False):
            with self.assertRaises(ValueError):
                led.decide(rid2, verdict)
        self.assertEqual(led.in_state(APPROVED), [], "applied is terminal, never re-queued")

    def test_stale_is_terminal_and_out_of_every_queue(self):
        rid = self.propose(body_page(PP), approve=True)
        led = self.ledger()
        led.mark_stale(rid, "gone")
        self.assertEqual(led.in_state(APPROVED), [])
        self.assertEqual(led.in_state(PENDING), [])
        self.assertEqual([x.repair_id for x in led.in_state(STALE)], [rid])
        with self.assertRaises(ValueError):
            led.decide(rid, True)


# ---------------------------------------------------------------------------
# Ledger: persistence
# ---------------------------------------------------------------------------

class TestPersistence(Harness):
    def test_two_saves_in_a_row_leave_one_valid_0600_file_and_no_temp_files(self):
        rid = self.propose(body_page(PP))
        led = self.ledger()
        led.decide(rid, True)
        led.save()
        led.save()
        self.assertEqual(stat.S_IMODE(os.stat(self.ledger_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.ledger_path.parent).st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in self.ledger_path.parent.iterdir()), ["approvals.json"])
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(data["repairs"][rid]["state"], APPROVED)
        self.assertIn("updated_at", data)

    def test_two_instances_saving_leaves_a_loadable_file(self):
        """Two processes racing: last writer wins, but the file on disk is
        always a complete ledger, never a torn one."""
        rid = self.propose(body_page(PP))
        a, b = self.ledger(), self.ledger()
        a.decide(rid, True)
        b.decide(rid, False)
        a.save()
        b.save()
        led = self.ledger()
        self.assertIn(led.repairs[rid].state, (APPROVED, REJECTED))
        self.assertEqual(stat.S_IMODE(os.stat(self.ledger_path).st_mode), 0o600)
        self.assertFalse(list(self.ledger_path.parent.glob(".approvals-*")))

    def test_failed_write_keeps_previous_ledger_intact_and_cleans_temp(self):
        """Docstring: 'Writes are atomic.' A crash while serialising must leave
        the previous file untouched and no temp file behind."""
        rid = self.propose(body_page(PP))
        before = self.ledger_path.read_bytes()
        led = self.ledger()
        led.decide(rid, True)
        with mock.patch("cleanuprtx.approvals.json.dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                led.save()
        self.assertEqual(self.ledger_path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.ledger_path.parent.iterdir()), ["approvals.json"])
        self.assertEqual(self.ledger().repairs[rid].state, PENDING)

    def test_valid_json_that_is_not_a_ledger_is_quarantined_with_ledger_error(self):
        """Ledger.load promises: an unreadable ledger is moved to .corrupt and a
        LedgerError names the rebuild command. A JSON array is not a ledger."""
        self.ledger_path.parent.mkdir(parents=True)
        for text in ("[]", '"string"', "42"):
            self.ledger_path.write_text(text, encoding="utf-8")
            with self.assertRaises(LedgerError, msg=text) as cm:
                Ledger(self.ledger_path)
            self.assertIn("audit --propose", str(cm.exception))
            self.assertTrue(self.ledger_path.with_suffix(".corrupt").exists(), text)
            self.ledger_path.with_suffix(".corrupt").unlink()

    def test_row_without_target_field_is_skipped_but_others_load(self):
        rid = self.propose(body_page(PP))
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        broken = dict(data["repairs"][rid])
        del broken["target"]
        broken["repair_id"] = "deadbeef"
        data["repairs"]["deadbeef"] = broken
        self.ledger_path.write_text(json.dumps(data), encoding="utf-8")
        led = self.ledger()
        self.assertIn(rid, led.repairs)
        self.assertNotIn("deadbeef", led.repairs)

    def test_pending_and_dry_run_never_create_the_ledger_file(self):
        """Read-only commands must not write ~/.cleanuprtx."""
        rc, out, _ = self.run_cli(["pending", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("0 repair(s) awaiting", out)
        rc, out, _ = self.run_cli(["apply", "--dry-run", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("No approved repairs", out)
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse(self.ledger_path.parent.exists())


# ---------------------------------------------------------------------------
# Ledger rows with a thin target
# ---------------------------------------------------------------------------

class TestThinTarget(Harness):
    def _row_without_node_id(self):
        doc = {"@context": "https://schema.org", "@type": "ProfilePage", "name": "R"}
        content = body_page(doc)
        rid = self.propose(content, approve=True)
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        row = data["repairs"][rid]
        self.assertEqual(row["target"]["node_id"], "")
        del row["target"]["node_id"]          # a row written by an older version
        self.ledger_path.write_text(json.dumps(data), encoding="utf-8")
        return rid, content

    def test_pending_lists_a_row_whose_target_lacks_node_id(self):
        rid, _ = self._row_without_node_id()
        led = self.ledger()
        led.repairs[rid].state = PENDING   # put it back in front of the owner
        led.save()
        rc, out, _ = self.run_cli(["pending", "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("1 repair(s) awaiting", out)
        self.assertIn(rid, out)
        self.assertIn("ProfilePage", out)

    def test_apply_resolves_a_row_without_node_id_by_path_and_type(self):
        rid, content = self._row_without_node_id()
        wp = StubWordPress([content])
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(wp.staged), 1)
        self.assertEqual(wp.staged[0]["target"]["node_types"], ["ProfilePage"])
        self.assertEqual(wp.staged[0]["target"]["node_id"], "")
        self.assertEqual(self.ledger().repairs[rid].state, APPLIED)


# ---------------------------------------------------------------------------
# cmd_apply: idempotence, retries, filters
# ---------------------------------------------------------------------------

class TestApplyFlow(Harness):
    def test_apply_twice_stages_once(self):
        """'only approved repairs are ever applied' - and an applied repair is
        no longer approved. A second `apply` must not touch WordPress at all."""
        content = body_page(PP)
        rid = self.propose(content, approve=True)
        wp = StubWordPress([content])
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(wp.staged), 1)
        self.assertEqual(self.ledger().repairs[rid].state, APPLIED)
        self.assertIn("post.php?post=41&action=edit", out)

        wp2 = StubWordPress([content])
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp2)
        self.assertEqual(rc, 0)
        self.assertEqual(wp2.staged, [], "double apply")
        self.assertEqual(wp2.constructed_for, [], "no client is even built")
        self.assertIn("No approved repairs", out)
        self.assertEqual(self.ledger().repairs[rid].state, APPLIED)
        self.assertEqual(self.ledger().repairs[rid].attempts, 0)

    def test_reaudit_after_apply_does_not_requeue_the_applied_repair(self):
        """The live page still shows the defect until the owner restores the
        autosave and publishes. Re-running audit --propose in between must not
        turn the applied repair back into a pending one."""
        content = body_page(PP)
        rid = self.propose(content, approve=True)
        wp = StubWordPress([content])
        self.run_cli(["apply", "--no-color"], wp)
        rc, out, _ = self.run_cli(["audit", "--no-color", "--propose"], StubWordPress([body_page(PP)]))
        self.assertEqual(rc, 0, out)
        self.assertIn("0 new repair(s) proposed (0 pending)", out)
        led = self.ledger()
        self.assertEqual(led.repairs[rid].state, APPLIED)
        self.assertEqual(len(led.repairs), 1)

    def test_failed_repair_is_retried_and_then_applied_with_attempts_kept(self):
        content = body_page(PP)
        rid = self.propose(content, approve=True)
        from cleanuprtx.http import HttpError
        wp = StubWordPress([content], stage_error=HttpError(503, "u", "{}"))
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", out)
        r = self.ledger().repairs[rid]
        self.assertEqual((r.state, r.attempts), (FAILED, 1))

        rc, out, _ = self.run_cli(["pending", "--no-color"])
        self.assertIn("0 repair(s) awaiting", out)
        self.assertIn("1 approved repair(s) failed on the last apply", out)
        self.assertIn(rid, out)

        wp = StubWordPress([content])
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(wp.staged), 1)
        r = self.ledger().repairs[rid]
        self.assertEqual((r.state, r.attempts), (APPLIED, 1))
        self.assertTrue(r.result.startswith("autosave: "))

    def test_site_filter_on_pending_and_dry_run(self):
        content = body_page(PP)
        rid_ir = self.propose(content, site="inspector-roofing")
        rid_pna = self.propose(content, site="pnagolfcarts")
        led = self.ledger()
        led.decide(rid_pna, True)
        led.save()

        rc, out, _ = self.run_cli(["pending", "--site", "inspector-roofing", "--no-color"])
        self.assertIn("1 repair(s) awaiting", out)
        self.assertIn(rid_ir, out)
        self.assertNotIn(rid_pna, out)

        rc, out, _ = self.run_cli(["pending", "--site", "pnagolfcarts", "--no-color"])
        self.assertIn("0 repair(s) awaiting", out)
        self.assertNotIn(rid_ir, out)

        rc, out, _ = self.run_cli(["pending", "--no-color"])
        self.assertIn("1 repair(s) awaiting", out)

        wp = StubWordPress([content])
        rc, out, _ = self.run_cli(["apply", "--dry-run", "--site", "inspector-roofing", "--no-color"], wp)
        self.assertEqual(rc, 0)
        self.assertIn("No approved repairs", out)
        self.assertEqual(wp.constructed_for, [])

        rc, out, _ = self.run_cli(["apply", "--dry-run", "--site", "pnagolfcarts", "--no-color"], wp)
        self.assertEqual(rc, 0)
        self.assertIn("1 approved repair(s)", out)
        self.assertIn("Dry run - nothing will be written", out)
        self.assertIn(rid_pna, out)
        self.assertEqual(wp.staged, [])
        self.assertEqual(wp.constructed_for, [], "dry run must not build a client")
        states = {k: r.state for k, r in self.ledger().repairs.items()}
        self.assertEqual(states, {rid_ir: PENDING, rid_pna: APPROVED})

    def test_vanished_target_is_stale_and_other_repairs_still_run(self):
        gone = body_page(PP, pid=7)     # same defect, different page id -> different repair id
        keep = body_page(PP, pid=41)
        rid_gone = self.propose(gone, approve=True)
        rid_keep = self.propose(keep, approve=True)
        self.assertNotEqual(rid_gone, rid_keep)
        wp = StubWordPress([keep])
        rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        self.assertEqual(rc, 1, "skipped repairs are reported in the exit code")
        led = self.ledger()
        self.assertEqual(led.repairs[rid_gone].state, STALE)
        self.assertEqual(led.repairs[rid_keep].state, APPLIED)
        self.assertEqual([s["id"] for s in wp.staged], [41])
        self.assertIn("1 staged, 1 skipped, 0 failed", out)

    def test_repair_for_a_site_no_longer_configured_does_not_abort_the_run(self):
        """README: 'Repairs whose target vanished are marked stale'. A site
        removed from config.py is the most vanished a target can be; the other
        sites' repairs must still be staged and the command must not traceback."""
        content = body_page(PP)
        rid_ir = self.propose(content, approve=True)
        rid_old = self.propose(content, approve=True, site="retired-site")
        led = self.ledger()
        # make the retired-site row iterate first
        led.repairs = {rid_old: led.repairs[rid_old], rid_ir: led.repairs[rid_ir]}
        led.save()
        wp = StubWordPress([content])
        try:
            rc, out, _ = self.run_cli(["apply", "--no-color"], wp)
        except KeyError as exc:
            self.fail(f"apply aborted with KeyError {exc}")
        led = self.ledger()
        self.assertEqual(led.repairs[rid_ir].state, APPLIED)
        self.assertNotEqual(led.repairs[rid_old].state, APPLIED)
        self.assertIn(led.repairs[rid_old].state, (STALE, FAILED))


# ---------------------------------------------------------------------------
# audit --propose counting
# ---------------------------------------------------------------------------

class TestAuditPropose(Harness):
    def test_first_run_proposes_second_run_reports_zero_new(self):
        wp = StubWordPress([body_page(PP)])
        rc, out, _ = self.run_cli(["audit", "--no-color", "--propose"], wp)
        self.assertEqual(rc, 0, out)
        self.assertIn("1 new repair(s) proposed (1 pending)", out)
        self.assertEqual(len(self.ledger().in_state(PENDING)), 1)

        rc, out, _ = self.run_cli(["audit", "--no-color", "--propose"], StubWordPress([body_page(PP)]))
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.ledger().in_state(PENDING)), 1)
        self.assertIn("0 new repair(s) proposed (1 pending)", out,
                      "an unchanged pending repair was reported as new again")

    def test_propose_keeps_approval_across_block_shift_via_cli(self):
        wp = StubWordPress([body_page(PP)])
        self.run_cli(["audit", "--no-color", "--propose"], wp)
        (rid,) = self.ledger().repairs
        led = self.ledger()
        led.decide(rid, True)
        led.save()
        rc, out, _ = self.run_cli(["audit", "--no-color", "--propose"], StubWordPress([with_plugin_block_first(body_page(PP))]))
        self.assertEqual(rc, 0, out)
        self.assertIn("0 new repair(s) proposed (0 pending)", out)
        led = self.ledger()
        self.assertEqual(list(led.repairs), [rid])
        self.assertEqual(led.repairs[rid].state, APPROVED)
        self.assertEqual(led.repairs[rid].block_index, 1)

    def test_types_with_spaces_are_trimmed_before_inventory(self):
        wp = StubWordPress([])
        rc, out, err = self.run_cli(["audit", "--no-color", "--types", "pages, posts"], wp)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(wp.iter_kinds, [["pages", "posts"]])

    def test_audit_without_types_asks_for_every_kind(self):
        wp = StubWordPress([])
        rc, out, err = self.run_cli(["audit", "--no-color"], wp)
        self.assertEqual(rc, 0)
        self.assertEqual(wp.iter_kinds, [None])


# ---------------------------------------------------------------------------
# hand-edits round trip through report.write_json
# ---------------------------------------------------------------------------

class TestHandEditsRoundTrip(Harness):
    def _write_audit(self, pages: List[Content]) -> str:
        result = audit_pages(pages, SITE)
        path = os.path.join(self.tmp.name, "audit.json")
        report.write_json(result, path)
        return path

    def test_plugin_findings_are_listed_by_screen_and_repairable_ones_are_not(self):
        repairable = body_page(dict(PP, dateModified="yesterday"), pid=41)
        plugin = page(graph({"@type": "ImageObject", "@id": "https://inspector-roofing.com/#img", "creator": "Bob"}),
                      plugin_cls="rank-math-schema", pid=42, link="https://inspector-roofing.com/gallery/")
        stale = page(None, status="draft", pid=43, title="", body="", link="https://inspector-roofing.com/?p=43")
        path = self._write_audit([repairable, plugin, stale])
        data = json.load(open(path, encoding="utf-8"))
        rules = sorted(f["rule"] for f in data["findings"])
        self.assertIn("invalid-datetime", rules)
        self.assertIn("object-field-type", rules)
        self.assertIn("stale-draft", rules)
        self.assertTrue(any(f["target"] is None for f in data["findings"]), "stale-draft has no target")

        rc, out, _ = self.run_cli(["hand-edits", path, "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("Rank Math > edit the page > Schema tab", out)
        self.assertIn('Field "creator" is not a Person or Organization object', out)
        self.assertIn("https://inspector-roofing.com/gallery/", out)
        self.assertIn('suggested value: {"@type": "Person", "name": "Bob"}', out)
        self.assertNotIn("Invalid datetime", out, "a repairable finding is not a hand edit")
        self.assertNotIn("Draft looks abandoned", out, "stale drafts are not hand edits")

    def test_audit_with_nothing_to_hand_edit(self):
        path = self._write_audit([body_page(dict(PP, mainEntity={"@id": PERSON}))])
        rc, out, _ = self.run_cli(["hand-edits", path, "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("Nothing needs a hand edit.", out)

    def test_html_audit_json_round_trips_into_hand_edits(self):
        """--html audits have no post_content; every finding is a hand edit."""
        html_path = os.path.join(self.tmp.name, "page.html")
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write("<html><head><title>Static</title>" + ld(graph({"@type": "ProfilePage", "@id": PP_ID}))
                     + "</head><body></body></html>")
        json_path = os.path.join(self.tmp.name, "static.json")
        rc, out, _ = self.run_cli(["audit", "--no-color", "--html", html_path, "--json", json_path])
        self.assertEqual(rc, 0, out)
        self.assertIn("0 finding(s) can be staged", out)
        rc, out, _ = self.run_cli(["hand-edits", json_path, "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("ProfilePage is missing mainEntity", out)
        self.assertIn("generated by the theme or page builder", out)


# ---------------------------------------------------------------------------
# approve / reject flows
# ---------------------------------------------------------------------------

class TestDecideFlow(Harness):
    def test_unknown_and_terminal_ids_are_reported_and_good_ids_still_decided(self):
        a = self.propose(body_page(PP, pid=41))
        b = self.propose(body_page(PP, pid=42), approve=True)
        led = self.ledger()
        led.mark_applied(b, "autosave: x")
        led.save()
        rc, out, _ = self.run_cli(["approve", "nope1234", b, a, "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("No repair with ID 'nope1234'", out)
        self.assertIn(f"Repair {b} is applied and cannot be changed", out)
        self.assertIn(f"Approved {a}", out)
        self.assertIn("apply --dry-run", out)
        led = self.ledger()
        self.assertEqual(led.repairs[a].state, APPROVED)
        self.assertEqual(led.repairs[b].state, APPLIED)

    def test_corrupt_ledger_exits_4_with_the_rebuild_hint(self):
        self.ledger_path.parent.mkdir(parents=True)
        self.ledger_path.write_text("{oops", encoding="utf-8")
        rc, out, err = self.run_cli(["pending", "--no-color"])
        self.assertEqual(rc, 4)
        self.assertIn("audit --propose", err)
        self.assertTrue(self.ledger_path.with_suffix(".corrupt").exists())
        self.assertFalse(self.ledger_path.exists())


# ---------------------------------------------------------------------------
# parser edge cases
# ---------------------------------------------------------------------------

class TestParser(unittest.TestCase):
    def test_no_color_in_every_position_including_both(self):
        p = cli.build_parser()
        self.assertTrue(p.parse_args(["--no-color", "apply", "--no-color"]).no_color)
        self.assertTrue(p.parse_args(["approve", "abc", "--no-color"]).no_color)
        self.assertTrue(p.parse_args(["approve", "--no-color", "abc"]).no_color)
        self.assertEqual(p.parse_args(["approve", "--no-color", "abc"]).repair_ids, ["abc"])
        self.assertTrue(p.parse_args(["hand-edits", "a.json", "--no-color"]).no_color)
        self.assertFalse(p.parse_args(["hand-edits", "a.json"]).no_color)
        self.assertTrue(p.parse_args(["--no-color", "hand-edits", "a.json"]).no_color)

    def test_limit_zero_parses_and_means_all(self):
        p = cli.build_parser()
        self.assertEqual(p.parse_args(["audit", "--limit", "0"]).limit, 0)
        self.assertEqual(p.parse_args(["audit"]).limit, 40)
        many = graph(*[{"@type": "ImageObject", "@id": f"https://x/#i{i}", "creator": "Bob"} for i in range(45)])
        result = audit_pages([page(many, raw=None)], SITE)
        self.assertEqual(len(result.by_severity("warning")), 45)
        buf = io.StringIO()
        report.print_audit(result, "t", color=False, limit=0, out=buf)
        out = buf.getvalue()
        self.assertEqual(out.count('Field "creator"'), 45)
        self.assertNotIn("more; use --limit 0", out)
        buf = io.StringIO()
        report.print_audit(result, "t", color=False, limit=40, out=buf)
        self.assertEqual(buf.getvalue().count('Field "creator"'), 40)
        self.assertIn("... 5 more; use --limit 0", buf.getvalue())

    def test_types_string_is_kept_verbatim_by_the_parser(self):
        p = cli.build_parser()
        self.assertEqual(p.parse_args(["audit", "--types", "pages, posts"]).types, "pages, posts")
        self.assertIsNone(p.parse_args(["audit"]).types)

    def test_missing_subcommand_and_bad_choices_exit_2(self):
        p = cli.build_parser()
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            p.parse_args([])
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            p.parse_args(["auth", "github"])
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            p.parse_args(["approve"])   # needs at least one ID
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            p.parse_args(["audit", "--site", "all"])   # audit is one site at a time
        self.assertEqual(cm.exception.code, 2)

    def test_apply_and_pending_default_to_all_sites_audit_to_the_primary(self):
        p = cli.build_parser()
        self.assertEqual(p.parse_args(["apply"]).site, "all")
        self.assertFalse(p.parse_args(["apply"]).dry_run)
        self.assertTrue(p.parse_args(["apply", "--dry-run"]).dry_run)
        self.assertEqual(p.parse_args(["pending"]).site, "all")
        self.assertEqual(p.parse_args(["audit"]).site, "inspector-roofing")
        self.assertEqual(p.parse_args(["audit", "--url", "https://a/", "--url", "https://b/"]).url,
                         ["https://a/", "https://b/"])


if __name__ == "__main__":
    unittest.main()
