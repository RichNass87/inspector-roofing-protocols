from __future__ import annotations

import base64
import hashlib
import json
import unittest
from unittest import mock

from cleanuprtx import gsc
from cleanuprtx.auth import _pkce_pair
from cleanuprtx.http import HttpError, _json_code
from cleanuprtx.probe import ProbeRow, parse_robots
from cleanuprtx.http import Fetched

INSPECT_FIXTURE = {"inspectionResult": {
    "inspectionResultLink": "https://search.google.com/search-console/inspect?resource_id=x",
    "indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed",
                          "robotsTxtState": "ALLOWED", "indexingState": "INDEXING_ALLOWED",
                          "lastCrawlTime": "2026-09-01T03:00:00Z", "pageFetchState": "ACCESS_FORBIDDEN",
                          "googleCanonical": "https://x/a/", "userCanonical": "https://x/a/", "crawledAs": "MOBILE"},
    "richResultsResult": {"verdict": "FAIL", "detectedItems": [
        {"richResultType": "Profile page", "items": [{"name": "ProfilePage", "issues": [
            {"issueMessage": 'Invalid object type for field "<parent_node>"', "severity": "ERROR"}]}]}]}}}


class TestInspectionParsing(unittest.TestCase):
    def test_shape(self):
        insp = gsc.parse_inspection("https://x/a/", INSPECT_FIXTURE)
        self.assertTrue(insp.is_indexed)
        self.assertEqual(insp.page_fetch_state, "ACCESS_FORBIDDEN")
        self.assertTrue(insp.fetch_blocked)
        self.assertEqual(insp.crawled_as, "MOBILE")
        self.assertEqual(len(insp.rich_result_issues), 1)
        self.assertIn("<parent_node>", str(insp.rich_result_issues[0]))
        self.assertTrue(insp.result_link.startswith("https://search.google.com"))

    def test_empty_is_safe(self):
        insp = gsc.parse_inspection("u", {})
        self.assertEqual(insp.verdict, "")
        self.assertFalse(insp.fetch_blocked)


class TestScopeEnforcement(unittest.TestCase):
    def _token(self, payload):
        c = gsc.SearchConsoleClient()
        with mock.patch.object(gsc, "read_secret", lambda *a, **k: "s"), \
             mock.patch.object(gsc, "request_json", lambda *a, **k: payload):
            return c._access_token()

    def test_readonly_accepted_and_scope_requested(self):
        seen = {}
        def fake(url, method="GET", form=None, **kw):
            seen.update(form or {})
            return {"access_token": "t", "expires_in": 3600, "scope": gsc.READONLY_SCOPE}
        c = gsc.SearchConsoleClient()
        with mock.patch.object(gsc, "read_secret", lambda *a, **k: "s"), mock.patch.object(gsc, "request_json", fake):
            self.assertEqual(c._access_token(), "t")
        self.assertEqual(seen["scope"], gsc.READONLY_SCOPE)

    def test_write_scope_rejected(self):
        with self.assertRaises(gsc.ScopeError):
            self._token({"access_token": "t", "scope": gsc.WRITE_SCOPE})

    def test_invalid_grant_explains_reauth(self):
        c = gsc.SearchConsoleClient()
        def fake(*a, **k):
            raise HttpError(400, gsc.TOKEN_URL, json.dumps({"error": "invalid_grant"}))
        with mock.patch.object(gsc, "read_secret", lambda *a, **k: "s"), mock.patch.object(gsc, "request_json", fake), \
             self.assertRaises(HttpError) as cm:
            c._access_token()
        self.assertIn("auth google", str(cm.exception))


class TestSafeInspect(unittest.TestCase):
    def _with(self, exc):
        c = gsc.SearchConsoleClient()
        c._token, c._expires_at = "t", 9e12
        def fake(*a, **k):
            raise exc
        with mock.patch.object(gsc, "request_json", fake):
            return c.safe_inspect("https://x/", "sc-domain:x")

    def test_quota_is_flagged_fatal(self):
        insp = self._with(HttpError(429, "u", json.dumps({"error": {"message": "Quota exceeded"}})))
        self.assertTrue(insp.error.startswith("QUOTA"))
        self.assertIn("Quota exceeded", insp.error)

    def test_permission(self):
        self.assertTrue(self._with(HttpError(403, "u", "{}")).error.startswith("PERMISSION"))


class TestPkce(unittest.TestCase):
    def test_challenge_is_s256_of_verifier(self):
        pair = _pkce_pair()
        expect = base64.urlsafe_b64encode(hashlib.sha256(pair["verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(pair["challenge"], expect)
        self.assertGreaterEqual(len(pair["verifier"]), 43)


class TestRobots(unittest.TestCase):
    def test_sitemaps_and_googlebot_block(self):
        info = parse_robots("User-agent: *\nDisallow: /wp-admin/\n\nUser-agent: Googlebot\nDisallow: /\n\nSitemap: https://x/sitemap_index.xml\n")
        self.assertEqual(info.sitemaps, ["https://x/sitemap_index.xml"])
        self.assertTrue(info.googlebot_disallow_all)
        self.assertIn("User-agent: Googlebot", info.blocks[0])

    def test_benign(self):
        info = parse_robots("User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n")
        self.assertFalse(info.googlebot_disallow_all)

    def test_row_flags_ua_filtering(self):
        row = ProbeRow(url="https://x/")
        row.results = {"browser": Fetched("https://x/", 200, "https://x/"), "googlebot": Fetched("https://x/", 403, "https://x/", {"server": "cloudflare"})}
        self.assertTrue(row.filtered_by_ua)
        self.assertEqual(row.notable_headers(), {"server": "cloudflare"})
        row.results["browser"] = Fetched("https://x/", 403, "https://x/")
        self.assertFalse(row.filtered_by_ua, "both blocked is not UA filtering")


class TestHttpHelpers(unittest.TestCase):
    def test_json_code_extraction(self):
        self.assertEqual(_json_code(json.dumps({"code": "rest_not_logged_in"})), "rest_not_logged_in")
        self.assertEqual(_json_code(json.dumps({"error": "invalid_grant"})), "invalid_grant")
        self.assertEqual(_json_code(json.dumps({"error": {"status": "PERMISSION_DENIED"}})), "PERMISSION_DENIED")
        self.assertEqual(_json_code("not json"), "")

    def test_non_https_refused_before_sending(self):
        from cleanuprtx.http import request_json
        with self.assertRaises(HttpError):
            request_json("http://inspector-roofing.com/wp-json/", headers={"Authorization": "x"})
