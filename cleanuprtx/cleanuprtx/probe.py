"""Crawler-access diagnostics for the 403-to-Googlebot reports.

Everything here is an unauthenticated GET. No credential is ever sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin

from .http import BROWSER_UA, Fetched, fetch_text

GOOGLEBOT_DESKTOP_UA = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
    "+http://www.google.com/bot.html) Chrome/128.0.0.0 Safari/537.36"
)
GOOGLEBOT_SMARTPHONE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
GOOGLEBOT_IMAGE_UA = "Googlebot-Image/1.0"

AGENTS = [
    ("browser", BROWSER_UA),
    ("googlebot", GOOGLEBOT_DESKTOP_UA),
    ("gbot-mobile", GOOGLEBOT_SMARTPHONE_UA),
    ("gbot-image", GOOGLEBOT_IMAGE_UA),
]
INTERESTING_HEADERS = ("server", "cf-ray", "cf-mitigated", "cf-cache-status",
                       "x-powered-by", "x-sucuri-id", "x-litespeed-cache", "x-wf-blocked")
DEFAULT_SITEMAPS = ("/wp-sitemap.xml", "/sitemap_index.xml", "/sitemap.xml")


@dataclass
class RobotsInfo:
    status: int
    sitemaps: List[str] = field(default_factory=list)
    googlebot_disallow_all: bool = False
    blocks: List[str] = field(default_factory=list)   # verbatim rule blocks worth showing


def parse_robots(text: str) -> RobotsInfo:
    """Parse robots.txt the way Google does: the most specific matching
    User-agent group wins, so a 'Googlebot' group overrides '*'."""
    info = RobotsInfo(status=200)
    current_agents: List[str] = []
    block_lines: List[str] = []
    groups: List[tuple] = []   # (agents, lines)

    def flush() -> None:
        if block_lines:
            groups.append(({a.lower() for a in current_agents}, list(block_lines)))

    text = (text or "").lstrip("\ufeff")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"(?i)sitemap:\s*(\S+)", line)
        if m:
            info.sitemaps.append(m.group(1))
            continue
        m = re.match(r"(?i)user-agent:\s*(.+)", line)
        if m:
            if block_lines:
                flush()
                block_lines = []
                current_agents = []
            current_agents.append(m.group(1).strip())
            block_lines.append(line)
            continue
        block_lines.append(line)
    flush()

    def disallows_all(lines: List[str]) -> bool:
        return any(re.match(r"(?i)disallow:\s*/\s*$", l) for l in lines)

    specific = [g for g in groups if g[0] & {"googlebot"}]
    wildcard = [g for g in groups if "*" in g[0]]
    effective = specific or wildcard
    for agents, lines in effective:
        if disallows_all(lines):
            info.googlebot_disallow_all = True
            info.blocks.append("\n".join(lines))
    for agents, lines in groups:
        if agents & {"googlebot-image"} and disallows_all(lines):
            info.blocks.append("\n".join(lines))
    return info


@dataclass
class ProbeRow:
    url: str
    results: Dict[str, Fetched] = field(default_factory=dict)

    @property
    def filtered_by_ua(self) -> bool:
        browser = self.results.get("browser")
        if not browser or browser.status != 200:
            return False
        return any(r.status in (401, 403, 429) for k, r in self.results.items() if k != "browser")

    @property
    def redirected_to(self) -> str:
        b = self.results.get("browser")
        return b.final_url if b and b.final_url.rstrip("/") != self.url.rstrip("/") else ""

    def notable_headers(self) -> Dict[str, str]:
        bot = self.results.get("googlebot")
        if not bot:
            return {}
        return {h: bot.headers[h] for h in INTERESTING_HEADERS if h in bot.headers}


def probe(base_url: str, urls: Optional[List[str]] = None, timeout: int = 20) -> Dict[str, object]:
    """Fetch each URL with each user agent. Returns {"robots", "rows"}."""
    base = base_url.rstrip("/") + "/"
    robots_fetched = fetch_text(urljoin(base, "/robots.txt"), timeout=timeout)
    robots = parse_robots(robots_fetched.text) if robots_fetched.ok else RobotsInfo(status=robots_fetched.status)

    targets = list(urls or [])
    if not targets:
        targets = [base, urljoin(base, "/robots.txt")]
        for sm in robots.sitemaps or [urljoin(base, p) for p in DEFAULT_SITEMAPS]:
            if sm not in targets:
                targets.append(sm)

    rows: List[ProbeRow] = []
    for url in targets:
        row = ProbeRow(url=url)
        for name, ua in AGENTS:
            row.results[name] = fetch_text(url, timeout=timeout, user_agent=ua)
        rows.append(row)
    return {"robots": robots, "rows": rows}
