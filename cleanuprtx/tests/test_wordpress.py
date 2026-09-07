"""Write-path tests. request_json is stubbed; every outgoing call is recorded."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from cleanuprtx import wordpress
from cleanuprtx.http import HttpError
from cleanuprtx.jsonld import PatchError, Target, find_blocks
from tests.helpers import PERSON, SITE, body_page, ld, page

PP = {"@type": "ProfilePage", "@id": "https://inspector-roofing.com/richard-nasser/#pp", "name": "R"}
PATCH = {"op": "set", "key": "mainEntity", "value": {"@id": PERSON}}


class Recorder:
    """Stand-in for http.request_json that records calls and answers."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, url, method="GET", headers=None, body=None, form=None, **kw):
        self.calls.append({"url": url, "method": method, "body": body, "headers": headers or {}})
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        # Default: echo the content back like WordPress does.
        if body and "content" in body:
            return {"id": 1, "status": "draft" if "/autosaves" not in url else None,
                    "content": {"raw": body["content"]}}
        return {}


def client():
    c = wordpress.WordPressClient(SITE)
    c._auth = "Basic dGVzdA=="   # never touch the Keychain in tests
    return c


def front_block(p):
    return find_blocks(p.front_html)[0]


def target():
    return Target(block=0, node_id=PP["@id"], node_types=["ProfilePage"])


class TestStageBlockRepair(unittest.TestCase):
    def test_published_page_goes_to_autosaves_with_only_content(self):
        rec = Recorder()
        p = body_page(PP)
        with mock.patch.object(wordpress, "request_json", rec):
            mode, link = client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(mode, "autosave")
        self.assertEqual(len(rec.calls), 1)
        call = rec.calls[0]
        self.assertTrue(call["url"].endswith("/wp/v2/pages/41/autosaves"))
        self.assertEqual(call["method"], "POST")
        self.assertEqual(set(call["body"]), {"content"}, "only content may ever be sent")
        self.assertNotIn("status", json.dumps(call["body"]).lower().split('"content"')[0])
        patched = find_blocks(call["body"]["content"])[0].document
        self.assertEqual(patched["mainEntity"], {"@id": PERSON})
        self.assertIn("<!-- wp:html -->", call["body"]["content"], "surrounding bytes preserved")
        self.assertIn("post.php?post=41&action=edit", link)

    def test_private_and_pending_also_use_autosaves(self):
        for status in ("private", "pending", "future"):
            rec = Recorder()
            p = body_page(PP, status=status)
            p.front_html = "<html><head>" + ld(PP) + "</head></html>"   # status != publish -> no front html by default
            with mock.patch.object(wordpress, "request_json", rec):
                mode, _ = client().stage_block_repair(p, front_block(p), target(), PATCH)
            self.assertEqual(mode, "autosave", status)
            self.assertTrue(rec.calls[0]["url"].endswith("/autosaves"), status)

    def test_draft_page_is_updated_in_place_without_status(self):
        rec = Recorder()
        p = body_page(PP, status="draft")
        p.front_html = "<html><head>" + ld(PP) + "</head></html>"
        with mock.patch.object(wordpress, "request_json", rec):
            mode, _ = client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(mode, "draft")
        self.assertTrue(rec.calls[0]["url"].endswith("/wp/v2/pages/41"))
        self.assertEqual(set(rec.calls[0]["body"]), {"content"})

    def test_refuses_media_and_author_and_breakdance_templates(self):
        for kind, ptype in (("media", "attachment"), ("author", "author"), ("breakdance_header", "breakdance_header")):
            rec = Recorder()
            p = body_page(PP, kind=kind)
            p.post_type = ptype
            with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
                client().stage_block_repair(p, front_block(p), target(), PATCH)
            self.assertEqual(rec.calls, [], kind)

    def test_refuses_trash_and_auto_draft(self):
        for status in ("trash", "auto-draft"):
            p = body_page(PP)
            p.status = status
            with mock.patch.object(wordpress, "request_json", Recorder()), self.assertRaises(PatchError):
                client().stage_block_repair(p, front_block(p), target(), PATCH)

    def test_refuses_when_post_content_is_empty(self):
        rec = Recorder()
        p = page(PP, raw="")
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError) as cm:
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertIn("page builder", str(cm.exception))
        self.assertEqual(rec.calls, [])

    def test_refuses_when_block_is_not_in_post_content(self):
        rec = Recorder()
        p = page(PP, raw="<p>hand written text, schema comes from RankMath</p>")
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError) as cm:
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertIn("SEO plugin", str(cm.exception))
        self.assertEqual(rec.calls, [])

    def test_refuses_when_node_changed_since_audit(self):
        rec = Recorder()
        p = body_page(PP)
        stale = {"op": "set", "key": "name", "value": "Q", "expect": "was-something-else"}
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), stale)
        self.assertEqual(rec.calls, [])

    def test_fetches_post_content_lazily_via_context_edit(self):
        raw_html = "<!-- wp:html -->" + ld(PP) + "<!-- /wp:html -->"
        rec = Recorder([{"id": 41, "status": "publish", "content": {"raw": raw_html}}])
        p = page(PP, raw=None)
        with mock.patch.object(wordpress, "request_json", rec):
            mode, _ = client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertIn("context=edit", rec.calls[0]["url"])
        self.assertEqual(rec.calls[0]["method"], "GET")
        self.assertEqual(mode, "autosave")

    def test_server_echo_mismatch_is_an_error(self):
        rec = Recorder([{"id": 1, "content": {"raw": "<p>something else entirely</p>"}}])
        p = body_page(PP)
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)

    def test_rename_id_patch_end_to_end(self):
        doc = {"@graph": [{"@type": "Person", "@id": "https://inspector-roofing.com/#old", "name": "Richard Nasser"},
                          {"@type": "Article", "author": {"@id": "https://inspector-roofing.com/#old"}}]}
        rec = Recorder()
        p = body_page(doc)
        t = Target(block=0, node_id="https://inspector-roofing.com/#old", node_types=["Person"])
        with mock.patch.object(wordpress, "request_json", rec):
            client().stage_block_repair(p, front_block(p), t, {"op": "rename_id", "old": "https://inspector-roofing.com/#old", "new": PERSON})
        out = find_blocks(rec.calls[0]["body"]["content"])[0].document
        self.assertEqual(out["@graph"][0]["@id"], PERSON)
        self.assertEqual(out["@graph"][1]["author"]["@id"], PERSON)


class TestVerifyDiagnosis(unittest.TestCase):
    def _fail(self, code, status=401, probe_code=None):
        responses = [HttpError(status, "u", json.dumps({"code": code, "message": "m"}))]
        if probe_code is not None:
            responses.append(HttpError(401, "u", json.dumps({"code": probe_code})))
        rec = Recorder(responses)
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(wordpress.AuthDiagnosis) as cm:
            client().verify()
        return str(cm.exception)

    def test_stripped_header(self):
        msg = self._fail("rest_not_logged_in", probe_code="rest_not_logged_in")
        self.assertIn("SetEnvIf", msg)

    def test_header_seen_but_rejected(self):
        msg = self._fail("rest_not_logged_in", probe_code="incorrect_password")
        self.assertIn("did not accept", msg)

    def test_app_passwords_disabled(self):
        self.assertIn("Wordfence", self._fail("application_passwords_disabled"))

    def test_bad_credential(self):
        self.assertIn("auth wordpress", self._fail("incorrect_password"))

    def test_edge_403(self):
        msg = self._fail("", status=403)
        self.assertIn("WAF", msg)

    def test_success_reports_caps(self):
        rec = Recorder([{"name": "Richard", "roles": ["administrator"], "capabilities": {"edit_pages": True}}])
        with mock.patch.object(wordpress, "request_json", rec):
            who = client().verify()
        self.assertEqual(who["name"], "Richard")
        self.assertIn("context=edit", rec.calls[0]["url"])


class TestPagination(unittest.TestCase):
    def test_offset_pagination_and_end_detection(self):
        items = [{"id": i, "type": "page", "title": {"rendered": f"p{i}"}, "content": {"rendered": ""}} for i in range(7)]
        seq = [(items[0:3], {"x-wp-total": "7"}), (items[3:6], {"x-wp-total": "7"}), (items[6:7], {"x-wp-total": "7"})]
        calls = []

        def fake(url, **kw):
            calls.append(url)
            return seq.pop(0)

        with mock.patch.object(wordpress, "request_json_with_headers", fake):
            got = list(client().iter_content("pages", per_page=3))
        self.assertEqual([c.id for c in got], list(range(7)))
        self.assertIn("offset=0", calls[0]); self.assertIn("offset=3", calls[1]); self.assertIn("offset=6", calls[2])
        self.assertEqual(len(calls), 3)

    def test_halves_batch_on_timeout(self):
        attempts = []

        def fake(url, **kw):
            attempts.append(url)
            if "per_page=20" in url:
                raise HttpError(504, url, "gateway timeout")
            return ([{"id": 1, "type": "page", "content": {"rendered": ""}}], {"x-wp-total": "1"})

        with mock.patch.object(wordpress, "request_json_with_headers", fake):
            got = list(client().iter_content("pages", per_page=20))
        self.assertEqual(len(got), 1)
        self.assertIn("per_page=10", attempts[-1])

    def test_invalid_page_number_is_end_not_error(self):
        def fake(url, **kw):
            raise HttpError(400, url, json.dumps({"code": "rest_post_invalid_page_number"}))
        with mock.patch.object(wordpress, "request_json_with_headers", fake):
            self.assertEqual(list(client().iter_content("pages")), [])

    def test_other_400_is_raised(self):
        def fake(url, **kw):
            raise HttpError(400, url, json.dumps({"code": "rest_invalid_param"}))
        with mock.patch.object(wordpress, "request_json_with_headers", fake), self.assertRaises(HttpError):
            list(client().iter_content("pages"))


class TestBreakdanceDetection(unittest.TestCase):
    def test_site_flag_marks_owned_and_front_signals_escalate(self):
        self.assertTrue(page(None, breakdance=True).is_breakdance)
        p = page(None, breakdance=False, extra_front='<link href="/wp-content/uploads/breakdance/css/post-41.css">')
        self.assertTrue(p.is_breakdance)
        self.assertFalse(page(None, breakdance=False).is_breakdance)

    def test_breakdance_ownership_does_not_block_body_block_repair(self):
        """The guard is structural: a block provably in post_content is ours even on a Breakdance site."""
        rec = Recorder()
        p = body_page(PP, breakdance=True)
        with mock.patch.object(wordpress, "request_json", rec):
            mode, _ = client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(mode, "autosave")
