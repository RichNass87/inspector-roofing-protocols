"""Site and credential configuration.

Keychain service names are declared here so 'cleanuprtx doctor' can report
exactly which items it expects without ever reading their values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# --- Keychain item names -------------------------------------------------
# Create these once with, for example:
#   security add-generic-password -s cleanuprtx-wp-app-password \
#       -a richard@inspector-roofing.com -w
KC_WP_APP_PASSWORD = "cleanuprtx-wp-app-password"
KC_GOOGLE_CLIENT_ID = "cleanuprtx-google-client-id"
KC_GOOGLE_CLIENT_SECRET = "cleanuprtx-google-client-secret"
KC_GOOGLE_REFRESH_TOKEN = "cleanuprtx-google-refresh-token"

WP_ACCOUNT = "richard@inspector-roofing.com"


@dataclass(frozen=True)
class Site:
    """One site under management."""

    slug: str
    wp_base_url: str
    gsc_properties: List[str] = field(default_factory=list)
    breakdance: bool = False


SITES = {
    "inspector-roofing": Site(
        slug="inspector-roofing",
        wp_base_url="https://inspector-roofing.com",
        # Both a domain property and a URL-prefix property exist for this site.
        # They report overlapping issues in different wording.
        gsc_properties=[
            "sc-domain:inspector-roofing.com",
            "https://inspector-roofing.com/",
        ],
        breakdance=True,
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

# Canonical entity IDs. A node using any other @id for the same real-world
# entity fragments the authority graph.
CANONICAL_PERSON_ID = "https://inspector-roofing.com/richard-nasser/#person"
CANONICAL_ORG_ID = "https://inspector-roofing.com/#organization"
