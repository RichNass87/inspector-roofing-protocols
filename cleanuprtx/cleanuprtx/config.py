"""Site and credential configuration.

Keychain service names are declared here so 'cleanuprtx doctor' can report
exactly which items it expects without ever reading their values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

# --- Keychain item names -------------------------------------------------
# WordPress application passwords are minted per install, so each site has
# its own item. 'cleanuprtx auth wordpress --site <slug>' creates them.
WP_KEYCHAIN_PREFIX = "cleanuprtx-wp-app-password"
KC_GOOGLE_CLIENT_ID = "cleanuprtx-google-client-id"
KC_GOOGLE_CLIENT_SECRET = "cleanuprtx-google-client-secret"
KC_GOOGLE_REFRESH_TOKEN = "cleanuprtx-google-refresh-token"

DEFAULT_WP_ACCOUNT = "richard@inspector-roofing.com"


@dataclass(frozen=True)
class Site:
    """One site under management."""

    slug: str
    wp_base_url: str
    wp_account: str = DEFAULT_WP_ACCOUNT
    gsc_properties: List[str] = field(default_factory=list)
    # Breakdance stores its canvas in post meta, not post_content. The flag
    # changes how stale drafts are judged and adds a caution to the report;
    # the write guard itself is structural (see wordpress.py) and does not
    # depend on it.
    breakdance: bool = False
    # Canonical entity IDs for this site. Only set where a single real-world
    # person/organization is meant to be represented by exactly one node.
    canonical_person_id: Optional[str] = None
    canonical_person_name: Optional[str] = None
    canonical_person_aliases: List[str] = field(default_factory=list)
    canonical_org_id: Optional[str] = None
    canonical_org_name: Optional[str] = None

    @property
    def wp_keychain_service(self) -> str:
        return f"{WP_KEYCHAIN_PREFIX}-{self.slug}"

    @property
    def host(self) -> str:
        host = urlparse(self.wp_base_url).hostname or ""
        return host[4:] if host.startswith("www.") else host


SITES = {
    "inspector-roofing": Site(
        slug="inspector-roofing",
        wp_base_url="https://inspector-roofing.com",
        # Both a domain property and a URL-prefix property exist for this site.
        # They report overlapping issues in different wording. The domain
        # property also covers standards.inspector-roofing.com.
        gsc_properties=[
            "sc-domain:inspector-roofing.com",
            "https://inspector-roofing.com/",
        ],
        breakdance=True,
        canonical_person_id="https://inspector-roofing.com/richard-nasser/#person",
        canonical_person_name="Richard Amir Nasser",
        canonical_person_aliases=["Richard Nasser", "Richard A. Nasser"],
        canonical_org_id="https://inspector-roofing.com/#organization",
        canonical_org_name="Inspector Roofing and Restoration",
    ),
    "positive-outcomes": Site(
        slug="positive-outcomes",
        wp_base_url="https://positive-outcomes.com",
        gsc_properties=["sc-domain:positive-outcomes.com"],
    ),
    "pnagolfcarts": Site(
        slug="pnagolfcarts",
        wp_base_url="https://pnagolfcarts.com",
        gsc_properties=["sc-domain:pnagolfcarts.com"],
    ),
}

DEFAULT_SITE = "inspector-roofing"

# Kept for callers that want the primary site's IDs without a Site in hand.
CANONICAL_PERSON_ID = SITES[DEFAULT_SITE].canonical_person_id
CANONICAL_ORG_ID = SITES[DEFAULT_SITE].canonical_org_id
