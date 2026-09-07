"""Adversarial write-path tests: WordPressClient.stage_block_repair and cmd_apply.

Every test asserts what the README and the docstrings promise:

* the only body ever sent is {"content": patched_post_content} (README, stage docstring);
* published/private/pending/future items go to /autosaves, drafts to /{kind}/{id};
* media, author archives and Breakdance templates are never written;
* a block is written only when the exact block exists in post_content
  ("byte for byte after whitespace"; "WordPress may reflow whitespace but not tokens");
* splice_block leaves "every other byte of the document exactly as it was";
* a 200 alone proves nothing - the server echo must reflect the patched content;
* rename_id renames the node's @id and every {"@id": old} reference in the block;
* cmd_apply marks a repair applied only when the repair is actually staged, marks
  vanished targets stale, keeps failed repairs in the queue, and tolerates a
  shifted block index ("Block index may have shifted; find by node id").

No network, no Keychain, no subprocess: http.request_json / http.fetch_text and
keychain.read_secret are stubbed at the cleanuprtx.wordpress import site.
"""

from __future__ import annotations

import functools
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cleanuprtx import cli, wordpress
from cleanuprtx.approvals import APPLIED, APPROVED, FAILED, STALE, Ledger
from cleanuprtx.audit import audit_pages
from cleanuprtx.http import Fetched, HttpError
from cleanuprtx.jsonld import PatchError, Target, find_blocks
from tests.helpers import PERSON, SITE, body_page, findings, ld, page

PP_ID = "https://inspector-roofing.com/richard-nasser/#pp"
PP = {"@type": "ProfilePage", "@id": PP_ID, "name": "R"}
PATCH = {"op": "set", "key": "mainEntity", "value": {"@id": PERSON}}
LINK = "https://inspector-roofing.com/richard-nasser/"


class Recorder:
    """Stand-in for http.request_json: records every call, answers in order."""

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
        if body and "content" in body:
            return {"id": 1, "status": "draft" if "/autosaves" not in url else None,
                    "content": {"raw": body["content"]}}
        return {}

    @property
    def writes(self):
        return [c for c in self.calls if c["method"] != "GET"]


def client():
    c = wordpress.WordPressClient(SITE)
    c._auth = "Basic dGVzdA=="
    return c


def front_block(p, index=0):
    return find_blocks(p.front_html)[index]


def target(node_id=PP_ID, types=("ProfilePage",)):
    return Target(block=0, node_id=node_id, node_types=list(types))


def raw_html(*scripts, prefix="<!-- wp:html -->\n", sep="\n<!-- /wp:html -->\n<!-- wp:html -->\n",
             suffix="\n<!-- /wp:html -->\n<!-- wp:paragraph -->\n<p>Roof &amp; gutter &nbsp;care — café été</p>\n<!-- /wp:paragraph -->"):
    return prefix + sep.join(scripts) + suffix


def stage(p, blk, tgt, patch, responses=None):
    rec = Recorder(responses)
    with mock.patch.object(wordpress, "request_json", rec):
        result = client().stage_block_repair(p, blk, tgt, patch)
    return rec, result


# ---------------------------------------------------------------------------
# stage_block_repair: body shape and destination
# ---------------------------------------------------------------------------

class TestBodyShape(unittest.TestCase):
    def test_body_is_exactly_content_and_carries_no_status_anywhere(self):
        p = body_page(PP)
        rec, (mode, _) = stage(p, front_block(p), target(), PATCH)
        self.assertEqual(len(rec.writes), 1)
        w = rec.writes[0]
        self.assertEqual(list(w["body"].keys()), ["content"])
        self.assertIsInstance(w["body"]["content"], str)
        self.assertNotIn("status", w["url"])
        self.assertNotIn("?", w["url"], "no query-string smuggling on the write URL")
        self.assertEqual(w["url"], "https://inspector-roofing.com/wp-json/wp/v2/pages/41/autosaves")

    def test_patch_key_named_status_only_lands_inside_the_jsonld(self):
        """A patch that sets a JSON-LD key called 'status' must not become a REST field."""
        p = body_page(PP)
        rec, _ = stage(p, front_block(p), target(), {"op": "set", "key": "status", "value": "publish"})
        w = rec.writes[0]
        self.assertEqual(set(w["body"]), {"content"})
        self.assertEqual(find_blocks(w["body"]["content"])[0].document["status"], "publish")

    def test_draft_posts_kind_updates_item_not_autosave_and_never_status(self):
        p = body_page(PP, status="draft", kind="posts", pid=7)
        p.post_type = "post"
        p.front_html = "<html><head>" + ld(PP) + "</head></html>"
        rec, (mode, link) = stage(p, front_block(p), target(), PATCH)
        self.assertEqual(mode, "draft")
        self.assertEqual(rec.writes[0]["url"], "https://inspector-roofing.com/wp-json/wp/v2/posts/7")
        self.assertEqual(set(rec.writes[0]["body"]), {"content"})
        self.assertIn("post=7&action=edit", link)

    def test_no_write_when_patch_changes_nothing(self):
        p = body_page(dict(PP, mainEntity={"@id": PERSON}))
        with self.assertRaises(PatchError):
            stage(p, front_block(p), target(), PATCH)


# ---------------------------------------------------------------------------
# stage_block_repair: read-only kinds and statuses
# ---------------------------------------------------------------------------

class TestReadOnlyTargets(unittest.TestCase):
    def test_every_breakdance_post_type_is_refused_even_under_a_pages_rest_base(self):
        for ptype in ("breakdance_header", "breakdance_footer", "breakdance_popup",
                      "breakdance_template", "breakdance_block"):
            p = body_page(PP)          # kind == "pages", status publish
            p.post_type = ptype
            rec = Recorder()
            with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
                client().stage_block_repair(p, front_block(p), target(), PATCH)
            self.assertEqual(rec.calls, [], ptype)

    def test_media_with_publish_status_is_still_refused(self):
        p = body_page(PP, kind="media")
        p.post_type = "attachment"
        p.status = "publish"
        rec = Recorder()
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(rec.calls, [])

    def test_inherit_and_unknown_statuses_are_refused_before_any_request(self):
        for status in ("inherit", "", "spam", "publish ", "Publish"):
            p = body_page(PP)
            p.status = status
            rec = Recorder()
            with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
                client().stage_block_repair(p, front_block(p), target(), PATCH)
            self.assertEqual(rec.calls, [], repr(status))

    def test_status_learned_from_context_edit_overrides_a_stale_writable_status(self):
        """content_raw is fetched lazily; if the edit context says the page is now
        trashed, the write must not proceed as if it were still published."""
        p = page(PP, raw=None)          # status publish, post_content unknown
        rec = Recorder([{"id": 41, "status": "trash", "content": {"raw": raw_html(ld(PP))}}])
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(rec.writes, [])


# ---------------------------------------------------------------------------
# stage_block_repair: ownership guard (block must be in post_content)
# ---------------------------------------------------------------------------

class TestOwnershipGuard(unittest.TestCase):
    def test_same_type_different_node_in_post_content_is_not_ownership(self):
        other = {"@type": "ProfilePage", "@id": PP_ID + "2", "name": "R"}
        p = page(PP, raw_doc=other)
        rec = Recorder()
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError) as cm:
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertIn("not in post_content", str(cm.exception))
        self.assertEqual(rec.calls, [])

    def test_block_text_present_only_inside_an_html_comment_is_still_matched_as_a_script(self):
        """A commented-out copy is not a live block; but if the only script is inside
        <!-- --> the regex still finds it. The promise is 'exact block in post_content':
        it IS there byte for byte, so this documents that the guard accepts it."""
        p = page(PP, raw="<!-- " + ld(PP) + " -->")
        rec, (mode, _) = stage(p, front_block(p), target(), PATCH)
        self.assertEqual(mode, "autosave")
        sent = rec.writes[0]["body"]["content"]
        self.assertTrue(sent.startswith("<!-- ") and sent.endswith(" -->"))

    def test_front_block_with_empty_text_is_refused(self):
        p = page(None, raw=raw_html(ld(PP)), extra_front='<script type="application/ld+json"></script>')
        rec = Recorder()
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(rec.calls, [])

    def test_whitespace_inside_a_string_is_a_different_token_not_a_reflow(self):
        """Docstring: 'WordPress may reflow whitespace but not tokens'. The live block says
        "Richard Nasser"; post_content only has "RichardNasser". That is a different
        string token, so post_content does NOT contain this block and it must be refused."""
        live = {"@type": "ProfilePage", "@id": PP_ID, "name": "Richard Nasser"}
        stored = {"@type": "ProfilePage", "@id": PP_ID, "name": "RichardNasser"}
        p = page(live, raw_doc=stored)
        self.assertNotEqual(front_block(p).document, find_blocks(p.content_raw)[0].document)
        rec = Recorder()
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertEqual(rec.calls, [], "a block whose string tokens differ is not ours")

    def test_colliding_blocks_the_one_actually_on_the_live_page_is_patched(self):
        """Two body blocks whose only difference is a space inside a string. The live
        page carries the second one. The write must patch the second and leave the
        first byte-identical."""
        a = {"@type": "ProfilePage", "@id": PP_ID, "name": "Richard Nasser"}
        b = {"@type": "ProfilePage", "@id": PP_ID, "name": "RichardNasser"}
        raw = raw_html(ld(a), ld(b))
        p = page(b, raw=raw)
        # Precondition of the scenario: the two blocks are identical once ALL whitespace
        # is stripped (this is what makes them a collision, not a library promise).
        self.assertEqual(re.sub(r"\s+", "", json.dumps(a)), re.sub(r"\s+", "", json.dumps(b)))
        rec, _ = stage(p, front_block(p), target(), PATCH)
        sent = rec.writes[0]["body"]["content"]
        blocks = find_blocks(sent)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, json.dumps(a), "block 0 was not on the live page; untouched")
        self.assertEqual(blocks[1].document["name"], "RichardNasser")
        self.assertEqual(blocks[1].document["mainEntity"], {"@id": PERSON})


# ---------------------------------------------------------------------------
# splice_block: every other byte preserved, block stays a valid block
# ---------------------------------------------------------------------------

class TestSpliceBytes(unittest.TestCase):
    def _assert_surroundings_preserved(self, original, sent, index):
        ob, sb = find_blocks(original), find_blocks(sent)
        self.assertEqual(len(ob), len(sb), "block count must not change")
        self.assertEqual(original[: ob[index].start], sent[: sb[index].start], "prefix bytes changed")
        self.assertEqual(original[ob[index].end:], sent[sb[index].end:], "suffix bytes changed")
        for i, (o, s) in enumerate(zip(ob, sb)):
            if i != index:
                self.assertEqual(o.text, s.text, f"block {i} must be byte-identical")

    def test_gutenberg_comments_entities_non_ascii_and_crlf_survive(self):
        para = "\r\n<!-- wp:paragraph {\"align\":\"left\"} -->\r\n<p>Roof &amp; gutter&nbsp;care — café “quotes”</p>\r\n<!-- /wp:paragraph -->"
        raw = "<!-- wp:html -->\r\n" + ld(PP) + "\r\n<!-- /wp:html -->" + para
        p = page(PP, raw=raw)
        rec, _ = stage(p, front_block(p), target(), PATCH)
        sent = rec.writes[0]["body"]["content"]
        self._assert_surroundings_preserved(raw, sent, 0)
        self.assertEqual(find_blocks(sent)[0].document["mainEntity"], {"@id": PERSON})

    def test_second_block_is_patched_and_first_is_untouched(self):
        org = {"@context": "https://schema.org", "@type": "Organization", "@id": "https://inspector-roofing.com/#organization",
               "name": "Inspector Roofing and Restoration", "telephone": "+1 555 0100"}
        raw = raw_html(ld(org), ld(PP))
        p = page(PP, raw=raw, extra_front=ld(org))   # front: PP is block 0, org is block 1
        rec, _ = stage(p, front_block(p, 0), target(), PATCH)
        sent = rec.writes[0]["body"]["content"]
        self._assert_surroundings_preserved(raw, sent, 1)
        self.assertEqual(find_blocks(sent)[1].document["mainEntity"], {"@id": PERSON})

    def test_non_ascii_and_entities_inside_the_block_round_trip(self):
        doc = dict(PP, description="Café — été &amp; <b>bold</b> 屋根")
        p = body_page(doc)
        rec, _ = stage(p, front_block(p), target(), PATCH)
        out = find_blocks(rec.writes[0]["body"]["content"])[0].document
        self.assertEqual(out["description"], doc["description"])
        self.assertNotIn("\\u", rec.writes[0]["body"]["content"], "ensure_ascii=False promised")

    def test_pretty_printed_block_keeps_its_indentation_style(self):
        raw = "<!-- wp:html -->\n<script type=\"application/ld+json\">\n" + json.dumps(PP, indent=2) + "\n</script>\n<!-- /wp:html -->"
        p = page(PP, raw=raw)
        rec, _ = stage(p, front_block(p), target(), PATCH)
        sent = rec.writes[0]["body"]["content"]
        b = find_blocks(sent)[0]
        self.assertIn("\n  \"mainEntity\"", b.text)
        self.assertTrue(sent.startswith("<!-- wp:html -->\n<script"))
        self.assertTrue(sent.endswith("</script>\n<!-- /wp:html -->"))

    def test_string_containing_closing_script_tag_stays_one_valid_block(self):
        """post_content legitimately escapes it as <\\/script>; the spliced block must
        still be exactly one parseable JSON-LD block, or the restored page shows raw
        JSON as visible text and Google sees invalid JSON-LD."""
        body_text = '{"@type": "ProfilePage", "@id": "%s", "name": "R", "description": "see the <\\/script> tag"}' % PP_ID
        script = '<script type="application/ld+json">' + body_text + "</script>"
        raw = raw_html(script)
        p = page(None, raw=raw, extra_front=script)
        blk = front_block(p)
        self.assertEqual(blk.document["description"], "see the </script> tag")
        rec, _ = stage(p, blk, target(), PATCH)
        sent = rec.writes[0]["body"]["content"]
        blocks = find_blocks(sent)
        self.assertEqual(len(blocks), 1, "the spliced block terminated early")
        self.assertEqual(blocks[0].parse_error, "", blocks[0].parse_error)
        self.assertEqual(blocks[0].document["description"], "see the </script> tag")
        self.assertEqual(blocks[0].document["mainEntity"], {"@id": PERSON})
        self.assertEqual(raw[raw.index("</script>") + 9:], sent[sent.rindex("</script>") + 9:])


# ---------------------------------------------------------------------------
# stage_block_repair: server echo verification
# ---------------------------------------------------------------------------

class TestEchoVerification(unittest.TestCase):
    def test_echo_of_the_original_unpatched_content_is_an_error(self):
        p = body_page(PP)
        rec = Recorder([{"id": 1, "content": {"raw": p.content_raw}}])
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)

    def test_echo_with_block_stripped_by_kses_is_an_error(self):
        """A user without unfiltered_html has <script> removed on save."""
        p = body_page(PP)
        stripped = re.sub(r"<script.*?</script>", "", p.content_raw, flags=re.S)
        rec = Recorder([{"id": 1, "content": {"raw": stripped}}])
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), target(), PATCH)

    def test_response_without_content_raw_is_not_proof_of_storage(self):
        """Docstring: 'a 200 alone proves nothing'. {"id": 1} is a 200 alone."""
        p = body_page(PP)
        shapes = ({"id": 1},
                  {"id": 1, "content": {"rendered": "<p>x</p>"}},
                  {"id": 1, "content": "string"},
                  {"code": "rest_cannot_edit", "message": "Sorry", "data": {"status": 401}})
        for resp in shapes:
            rec = Recorder([resp])
            with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError, msg=repr(resp)):
                client().stage_block_repair(p, front_block(p), target(), PATCH)

    def test_draft_echo_reporting_publish_is_an_error(self):
        p = body_page(PP, status="draft")
        p.front_html = "<html><head>" + ld(PP) + "</head></html>"
        rec = Recorder([None])   # placeholder replaced below
        rec.responses = []

        def echo_publish(url, method="GET", headers=None, body=None, **kw):
            rec.calls.append({"url": url, "method": method, "body": body, "headers": headers or {}})
            return {"id": 41, "status": "publish", "content": {"raw": body["content"]}}

        with mock.patch.object(wordpress, "request_json", echo_publish), self.assertRaises(PatchError) as cm:
            client().stage_block_repair(p, front_block(p), target(), PATCH)
        self.assertIn("publish", str(cm.exception))


# ---------------------------------------------------------------------------
# rename_id
# ---------------------------------------------------------------------------

class TestRenameId(unittest.TestCase):
    OLD = "https://inspector-roofing.com/#richard"

    def _graph(self):
        return {"@context": "https://schema.org", "@graph": [
            {"@type": "Person", "@id": self.OLD, "name": "Richard Nasser", "url": self.OLD,
             "sameAs": [self.OLD, "https://www.linkedin.com/in/richardnasser/"]},
            {"@type": "Article", "@id": "https://inspector-roofing.com/post/#article",
             "author": [{"@id": self.OLD}], "publisher": {"@id": "https://inspector-roofing.com/#organization"},
             "about": {"@id": self.OLD}},
        ]}

    def test_renames_id_and_nested_list_references_but_not_url_strings(self):
        doc = self._graph()
        p = body_page(doc)
        t = Target(block=0, node_id=self.OLD, node_types=["Person"])
        rec, _ = stage(p, front_block(p), t, {"op": "rename_id", "old": self.OLD, "new": PERSON})
        out = find_blocks(rec.writes[0]["body"]["content"])[0].document
        person, article = out["@graph"]
        self.assertEqual(person["@id"], PERSON)
        self.assertEqual(article["author"], [{"@id": PERSON}])
        self.assertEqual(article["about"], {"@id": PERSON})
        self.assertEqual(person["url"], self.OLD, "url is a string value, not an @id reference")
        self.assertEqual(person["sameAs"][0], self.OLD)
        self.assertEqual(article["publisher"], {"@id": "https://inspector-roofing.com/#organization"})
        self.assertNotIn(self.OLD, json.dumps([n.get("@id") for n in out["@graph"]] +
                                              [article["author"][0]["@id"], article["about"]["@id"]]))

    def test_reference_in_a_second_block_is_left_byte_identical(self):
        """README: rename 'every reference to it in the block'. The second block is
        another byte of the document and must not change (a separate finding
        addresses it)."""
        doc = self._graph()
        second = {"@type": "ImageObject", "@id": "https://inspector-roofing.com/#img", "creator": {"@id": self.OLD}}
        raw = raw_html(ld(doc), ld(second))
        p = page(doc, raw=raw, extra_front=ld(second))
        t = Target(block=0, node_id=self.OLD, node_types=["Person"])
        rec, _ = stage(p, front_block(p), t, {"op": "rename_id", "old": self.OLD, "new": PERSON})
        sent = rec.writes[0]["body"]["content"]
        blocks = find_blocks(sent)
        self.assertEqual(blocks[1].text, json.dumps(second))
        self.assertEqual(blocks[0].document["@graph"][0]["@id"], PERSON)

    def test_rename_when_node_id_no_longer_matches_is_refused_without_write(self):
        doc = self._graph()
        p = body_page(doc)
        t = Target(block=0, node_id=self.OLD, node_types=["Person"])
        rec = Recorder()
        with mock.patch.object(wordpress, "request_json", rec), self.assertRaises(PatchError):
            client().stage_block_repair(p, front_block(p), t, {"op": "rename_id", "old": self.OLD + "x", "new": PERSON})
        self.assertEqual(rec.calls, [])

    def test_rename_into_an_id_already_held_by_a_duplicate_node_merges_them(self):
        """The common shape on this site: a plugin-style Person node already carries
        the canonical @id and a hand-written duplicate does not. README: rename the
        node's @id and every reference. Both nodes end up under PERSON (JSON-LD
        merges them), the pre-existing canonical node is not otherwise touched."""
        doc = self._graph()
        canonical = {"@type": "Person", "@id": PERSON, "name": "Richard Amir Nasser", "jobTitle": "Inspector"}
        doc["@graph"].append(dict(canonical))
        p = body_page(doc)
        t = Target(block=0, node_id=self.OLD, node_types=["Person"])
        rec, _ = stage(p, front_block(p), t, {"op": "rename_id", "old": self.OLD, "new": PERSON})
        out = find_blocks(rec.writes[0]["body"]["content"])[0].document
        ids = [n.get("@id") for n in out["@graph"]]
        self.assertEqual(ids.count(PERSON), 2)
        self.assertNotIn(self.OLD, ids)
        self.assertEqual(out["@graph"][2], canonical)
        self.assertEqual(out["@graph"][1]["author"], [{"@id": PERSON}])


# ---------------------------------------------------------------------------
# cmd_apply end to end (ledger + client, everything stubbed)
# ---------------------------------------------------------------------------

def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class Site41:
    """Fake WordPress for page 41: routes GETs by URL, echoes POSTs."""

    def __init__(self, front_html, raw, status="publish", exists=True, post_error=None):
        self.front_html = front_html
        self.raw = raw
        self.status = status
        self.exists = exists
        self.post_error = post_error
        self.calls = []

    def request_json(self, url, method="GET", headers=None, body=None, form=None, **kw):
        self.calls.append({"url": url, "method": method, "body": body})
        if method == "GET":
            if not self.exists:
                raise HttpError(404, url, json.dumps({"code": "rest_post_invalid_id"}))
            if "context=edit" in url:
                return {"id": 41, "status": self.status, "content": {"raw": self.raw, "rendered": ""}}
            return {"id": 41, "type": "page", "slug": "richard-nasser", "link": LINK,
                    "title": {"rendered": "Richard Amir Nasser"}, "status": self.status,
                    "date_gmt": "2026-07-01T09:00:00", "modified_gmt": "2026-08-01T12:00:00",
                    "content": {"rendered": ""}}
        if self.post_error:
            raise self.post_error
        return {"id": 999, "status": None if "/autosaves" in url else self.status,
                "content": {"raw": body["content"], "rendered": ""}}

    def fetch_text(self, url, **kw):
        return Fetched(url=url, status=200, final_url=url, text=self.front_html)

    @property
    def writes(self):
        return [c for c in self.calls if c["method"] != "GET"]


class ApplyHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "approvals.json"

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self):
        return Ledger(self.ledger_path)

    def propose_all(self, content, rule=None):
        fs = findings(audit_pages([content], SITE), rule)
        led = self.ledger()
        ids = []
        for f in fs:
            r = led.propose("inspector-roofing", f)
            if r is not None:
                led.decide(r.repair_id, True)
                ids.append(r.repair_id)
        led.save()
        return ids

    def run_apply(self, site, argv=("apply", "--no-color")):
        with mock.patch.object(cli, "Ledger", functools.partial(Ledger, self.ledger_path)), \
             mock.patch.object(wordpress, "request_json", site.request_json), \
             mock.patch.object(wordpress, "fetch_text", site.fetch_text), \
             mock.patch.object(wordpress, "read_secret", lambda *a, **k: "not-a-real-secret"), \
             mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            rc = cli.main(list(argv))
        return rc, out.getvalue()


class TestCmdApply(ApplyHarness):
    def test_dry_run_writes_nothing_and_changes_no_state(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        self.assertTrue(ids)
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site, ("apply", "--dry-run", "--no-color"))
        self.assertEqual(rc, 0)
        self.assertEqual(site.calls, [], "dry run must not even read")
        self.assertEqual({r.state for r in self.ledger().repairs.values()}, {APPROVED})

    def test_single_repair_full_sequence_only_one_post_only_content(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        w = site.writes[0]
        self.assertEqual(w["url"], "https://inspector-roofing.com/wp-json/wp/v2/pages/41/autosaves")
        self.assertEqual(set(w["body"]), {"content"})
        self.assertEqual([c["method"] for c in site.calls], ["GET", "GET", "POST"])
        self.assertTrue(all("wp/v2/pages/41" in c["url"] for c in site.calls))
        self.assertEqual(self.ledger().repairs[ids[0]].state, APPLIED)
        self.assertIn("post.php?post=41&action=edit", out)
        self.assertNotIn("not-a-real-secret", out)

    def test_echo_mismatch_marks_failed_not_applied_and_keeps_it_queued(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw)
        real_post = site.request_json

        def lying_server(url, method="GET", **kw):
            if method == "POST":
                site.calls.append({"url": url, "method": method, "body": kw.get("body")})
                return {"id": 999, "content": {"raw": "<p>nothing like it</p>"}}
            return real_post(url, method=method, **kw)

        site.request_json = lying_server
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 1)
        led = self.ledger()
        r = led.repairs[ids[0]]
        self.assertEqual(r.state, FAILED)
        self.assertEqual(r.attempts, 1)
        self.assertIn(ids[0], [x.repair_id for x in led.in_state(APPROVED)], "failed stays in the queue")
        self.assertIn("FAILED", out)

    def test_http_error_on_write_is_failed_and_retried_not_applied(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw,
                      post_error=HttpError(403, "u", json.dumps({"code": "rest_cannot_edit"})))
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 1)
        self.assertEqual(self.ledger().repairs[ids[0]].state, FAILED)

    def test_vanished_page_is_stale_and_never_written(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw, exists=False)
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [])
        self.assertEqual(self.ledger().repairs[ids[0]].state, STALE)

    def test_page_unpublished_since_audit_is_not_written(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw, status="trash")
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [])
        self.assertNotEqual(self.ledger().repairs[ids[0]].state, APPLIED)

    def test_block_moved_to_plugin_since_audit_is_not_written(self):
        """At audit time the block was in post_content. Since then the owner moved the
        schema into Rank Math: the live block now carries the plugin class and
        post_content no longer has it. Nothing may be written."""
        doc = graph({"@type": "ProfilePage", "@id": PP_ID})
        content = body_page(doc)
        ids = self.propose_all(content)
        live = "<html><head>" + ld(doc, "rank-math-schema") + "</head><body></body></html>"
        site = Site41(live, "<!-- wp:paragraph --><p>text only</p><!-- /wp:paragraph -->")
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [])
        self.assertNotEqual(self.ledger().repairs[ids[0]].state, APPLIED)

    def test_ledger_row_for_media_kind_never_reaches_a_write(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        led = self.ledger()
        led.repairs[ids[0]].kind = "media"
        led.save()
        site = Site41(content.front_html, content.content_raw, status="inherit")
        rc, out = self.run_apply(site)
        self.assertEqual(site.writes, [])
        self.assertNotEqual(self.ledger().repairs[ids[0]].state, APPLIED)

    def test_shifted_block_index_is_resolved_by_node_id(self):
        """cmd_apply: 'Block index may have shifted; find by node id.' At audit time the
        body block was index 0. Now Rank Math emits its own block first, so the body
        block is index 1 and index 0 is a parseable Rank Math block."""
        doc = graph({"@type": "ProfilePage", "@id": PP_ID})
        content = body_page(doc)
        ids = self.propose_all(content)
        self.assertEqual(self.ledger().repairs[ids[0]].block_index, 0)
        rm = {"@context": "https://schema.org", "@graph": [{"@type": "Organization", "@id": "https://inspector-roofing.com/#organization", "name": "Inspector Roofing and Restoration"}]}
        live = "<html><head>" + ld(rm, "rank-math-schema") + ld(doc) + "</head><body></body></html>"
        site = Site41(live, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(site.writes), 1)
        sent = site.writes[0]["body"]["content"]
        self.assertEqual(find_blocks(sent)[0].document["@graph"][0]["mainEntity"]["@id"], PERSON)
        self.assertEqual(self.ledger().repairs[ids[0]].state, APPLIED)

    def test_two_repairs_on_one_page_are_both_present_in_the_staged_content(self):
        """WordPress keeps ONE autosave per post per user ('Store one autosave per
        author. If there is already an autosave, overwrite it.'). If the second
        repair is computed from the original post_content, the second POST erases
        the first repair while the ledger marks both APPLIED. The /richard-nasser/
        page has exactly this shape: parent_node and dateModified on one block."""
        doc = graph({"@type": "ProfilePage", "@id": PP_ID, "dateModified": "yesterday"})
        content = body_page(doc)
        ids = self.propose_all(content)
        self.assertEqual(len(ids), 2, "parent-node and datetime repairs on one block")
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site)
        self.assertEqual(rc, 0, out)
        states = {self.ledger().repairs[i].state for i in ids}
        self.assertEqual(states, {APPLIED})
        final = find_blocks(site.writes[-1]["body"]["content"])[0].document["@graph"][0]
        self.assertEqual(final.get("mainEntity", {}).get("@id"), PERSON,
                         "the last autosave must still carry the first repair")
        self.assertEqual(final["dateModified"], "2026-08-01T12:00:00+00:00")
        for w in site.writes:
            self.assertEqual(set(w["body"]), {"content"})
            self.assertTrue(w["url"].endswith("/pages/41/autosaves"))

    def test_site_filter_applies_only_that_site(self):
        content = body_page(graph({"@type": "ProfilePage", "@id": PP_ID}))
        ids = self.propose_all(content)
        site = Site41(content.front_html, content.content_raw)
        rc, out = self.run_apply(site, ("apply", "--site", "pnagolfcarts", "--no-color"))
        self.assertEqual(site.calls, [])
        self.assertEqual(self.ledger().repairs[ids[0]].state, APPROVED)
        self.assertIn("No approved repairs", out)


if __name__ == "__main__":
    unittest.main()
