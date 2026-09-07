"""Coverage for the v0.2.1 verification fixes."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from cleanuprtx import keychain, wordpress
from cleanuprtx.audit import audit_pages
from cleanuprtx.http import Fetched, HttpError
from cleanuprtx.jsonld import AlreadyApplied, Target, apply_patch, find_blocks
from tests.helpers import PERSON, SITE, body_page, findings, ld, page


def graph(*nodes):
    return {"@context": "https://schema.org", "@graph": list(nodes)}


class TestMediaInherit(unittest.TestCase):
    def test_attachment_pages_are_fetched_and_audited(self):
        """REST media items report status 'inherit'; their public attachment page is
        where Rank Math emits ImageObject - the Image Metadata report's source."""
        item = {"id": 9, "type": "attachment", "slug": "roof", "link": "https://inspector-roofing.com/roof/",
                "title": {"rendered": "roof"}, "status": "inherit", "content": {"rendered": ""}}
        c = wordpress._to_content(item, "media", SITE)
        self.assertTrue(c.is_public)
        self.assertFalse(c.writable)
        live = "<html><head>" + ld(graph({"@type": "ImageObject", "creator": "Bob"}), "rank-math-schema") + "</head></html>"
        with mock.patch.object(wordpress, "fetch_text", lambda url, **k: Fetched(url, 200, url, {}, live)):
            wordpress.WordPressClient(SITE).load_front_html(c)
        self.assertIsNotNone(c.front_html)
        f = findings(audit_pages([c], SITE), "object-field-type")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].plugin, "RankMath")
        self.assertFalse(f[0].is_auto_fixable)


class TestRedirects(unittest.TestCase):
    def test_redirected_permalink_is_recorded_not_audited(self):
        c = page(None)
        with mock.patch.object(wordpress, "fetch_text",
                               lambda url, **k: Fetched(url, 200, "https://inspector-roofing.com/new-home/", {}, "<html></html>")):
            wordpress.WordPressClient(SITE).load_front_html(c)
        self.assertIsNone(c.front_html)
        self.assertEqual(c.front_redirected_to, "https://inspector-roofing.com/new-home/")
        r = audit_pages([c], SITE)
        self.assertEqual(r.pages_redirected, [f"{c.link} -> https://inspector-roofing.com/new-home/"])

    def test_www_and_trailing_slash_are_not_redirects(self):
        c = page(None)
        with mock.patch.object(wordpress, "fetch_text",
                               lambda url, **k: Fetched(url, 200, "https://www.inspector-roofing.com/richard-nasser", {}, "<html></html>")):
            wordpress.WordPressClient(SITE).load_front_html(c)
        self.assertEqual(c.front_redirected_to, "")
        self.assertIsNotNone(c.front_html)


class TestInventoryTolerance(unittest.TestCase):
    def test_forbidden_cpt_is_skipped_with_a_warning(self):
        client = wordpress.WordPressClient(SITE)
        client._auth = "Basic x"
        calls, warnings = [], []

        def fake(url, **kw):
            calls.append(url)
            if "breakdance_header" in url:
                raise HttpError(403, url, json.dumps({"code": "rest_forbidden"}))
            if "users?who=authors" in url:
                return []
            return ([{"id": 1, "type": "page", "content": {"rendered": ""}, "status": "publish"}], {"x-wp-total": "1"})

        with mock.patch.object(wordpress, "request_json_with_headers", fake), \
             mock.patch.object(wordpress, "request_json", lambda url, **kw: fake(url)):
            got = list(client.iter_all(kinds=["breakdance_header", "pages"], warn=warnings.append))
        self.assertEqual([c.id for c in got], [1])
        self.assertTrue(warnings and "breakdance_header" in warnings[0])


class TestAlreadyFixed(unittest.TestCase):
    def test_patch_already_present_raises_already_applied(self):
        doc = {"@type": "ProfilePage", "@id": "https://x/#pp", "mainEntity": {"@id": PERSON}}
        t = Target(block=0, node_id="https://x/#pp", node_types=["ProfilePage"])
        with self.assertRaises(AlreadyApplied):
            apply_patch(doc, t, {"op": "set", "key": "mainEntity", "value": {"@id": PERSON}})

    def test_rename_already_done_raises_already_applied(self):
        doc = {"@graph": [{"@type": "Person", "@id": PERSON, "name": "R"}]}
        t = Target(block=0, node_id="https://x/#old", node_types=["Person"])
        with self.assertRaises(AlreadyApplied):
            apply_patch(doc, t, {"op": "rename_id", "old": "https://x/#old", "new": PERSON})


class TestCrossBlockRenameGate(unittest.TestCase):
    def test_reference_in_a_non_defining_block_makes_the_finding_report_only(self):
        author = {"@type": "Person", "@id": "https://inspector-roofing.com/#author", "name": "Richard Nasser"}
        front = "<html><head>" + ld(graph(author)) + ld(graph({"@type": "Article", "author": {"@id": author["@id"]}}), "rank-math-schema") + "</head></html>"
        p = body_page(graph(author))
        p.front_html = front
        f = findings(audit_pages([p], SITE), "entity-fragmentation")
        self.assertEqual(len(f), 1)
        self.assertIsNone(f[0].patch)
        self.assertIn("dangling", f[0].detail)


class TestKeychainQuoting(unittest.TestCase):
    def test_store_secret_passes_value_on_stdin_never_argv(self):
        seen = {}

        def fake_run(cmd, input=None, capture_output=True, text=True, timeout=60):
            seen["cmd"], seen["stdin"] = cmd, input
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(keychain.subprocess, "run", fake_run):
            keychain.store_secret("svc", 'pa"ss\\word $x', "acct")
        self.assertEqual(seen["cmd"], [keychain.SECURITY, "-i"])
        self.assertNotIn("pa", " ".join(seen["cmd"]))
        self.assertEqual(seen["stdin"], 'add-generic-password -U -s "svc" -a "acct" -w "pa\\"ss\\\\word $x"\n')

    def test_store_secret_refuses_newlines_and_empty(self):
        with self.assertRaises(keychain.KeychainError):
            keychain.store_secret("svc", "a\nb")
        with self.assertRaises(keychain.KeychainError):
            keychain.store_secret("svc", "")

    def test_read_secret_distinguishes_missing_from_denied(self):
        def missing(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 44, "", "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.")
        with mock.patch.object(keychain.subprocess, "run", missing), self.assertRaises(keychain.KeychainError) as cm:
            keychain.read_secret("svc")
        self.assertIn("No Keychain item", str(cm.exception))
        def denied(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 36, "", "User interaction is not allowed.")
        with mock.patch.object(keychain.subprocess, "run", denied), self.assertRaises(keychain.KeychainError) as cm:
            keychain.read_secret("svc")
        self.assertIn("refused", str(cm.exception))

    def test_item_exists_never_asks_for_the_password(self):
        seen = []
        with mock.patch.object(keychain.subprocess, "run",
                               lambda cmd, **kw: (seen.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1]):
            self.assertTrue(keychain.item_exists("svc", "acct"))
        self.assertNotIn("-w", seen[0])


class TestSplitBlockSafety(unittest.TestCase):
    def test_serialised_block_never_contains_a_closing_script(self):
        from cleanuprtx.jsonld import serialise_block
        text = serialise_block({"description": "see </script> and <!-- x -->"}, "{}")
        self.assertNotIn("</script>", text)
        self.assertNotIn("<!--", text)
        self.assertEqual(json.loads(text)["description"], "see </script> and <!-- x -->")
