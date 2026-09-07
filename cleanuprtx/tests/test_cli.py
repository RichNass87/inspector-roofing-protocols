from __future__ import annotations

import io
import pathlib
import unittest
from contextlib import redirect_stdout

from cleanuprtx.cli import build_parser, main

ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestParser(unittest.TestCase):
    def test_no_color_accepted_before_and_after_subcommand(self):
        p = build_parser()
        self.assertTrue(p.parse_args(["--no-color", "pending"]).no_color)
        self.assertTrue(p.parse_args(["pending", "--no-color"]).no_color)
        self.assertFalse(p.parse_args(["pending"]).no_color)

    def test_site_is_validated_everywhere(self):
        p = build_parser()
        for cmd in (["audit", "--site", "nope"], ["pending", "--site", "nope"], ["apply", "--site", "nope"]):
            with self.assertRaises(SystemExit):
                p.parse_args(cmd)
        self.assertEqual(p.parse_args(["pending"]).site, "all")
        self.assertEqual(p.parse_args(["audit"]).site, "inspector-roofing")


class TestStaticAudit(unittest.TestCase):
    def test_html_mode_audits_repo_page(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["audit", "--no-color", "--html", str(ROOT / "docs" / "index.html")])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Pages with JSON-LD   1", out)
        self.assertNotIn("CRITICAL", out)
