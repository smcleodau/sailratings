"""Policy fixtures for DP-01-02 verification.

These fixtures exercise every case required by the acceptance criteria:

* **public HTML** — a freely accessible results page
* **API** — a JSON API endpoint with proper Content-Type
* **PDF** — a publicly accessible IRC certificate PDF
* **login wall** — an authenticated/gated page without authorisation
* **disallow** — a URL blocked by robots.txt
* **unclear** — a source with no rights ruling

Each fixture returns a ``SourceRecord`` and optionally a ``RobotsRules``
object and a URL, so tests can feed them directly to ``CollectionGate``.
"""

from __future__ import annotations

from irc_data.sources.gate import SourceRecord
from irc_data.sources.policy import (
    CollectionPolicyDecisionV1,
    LegalStatus,
    SourceClass,
)
from irc_data.sources.robots import RobotsRules, parse_robots_txt


# ---------------------------------------------------------------------------
# Active policy
# ---------------------------------------------------------------------------

POLICY = CollectionPolicyDecisionV1()


# ---------------------------------------------------------------------------
# Source fixtures (one per classification case)
# ---------------------------------------------------------------------------


def public_html_source() -> SourceRecord:
    """A publicly accessible HTML results page (SailSys)."""
    return SourceRecord(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://app.sailsys.com.au",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.APPROVED,
        enabled=True,
        robots_disallow=[],
    )


def api_source() -> SourceRecord:
    """A JSON API source (ORC data API)."""
    return SourceRecord(
        slug="orc",
        display_name="ORC",
        base_url="https://data.orc.org",
        category="ratings",
        policy_version="v1.0",
        legal_status=LegalStatus.APPROVED,
        enabled=True,
        robots_disallow=[],
    )


def pdf_source() -> SourceRecord:
    """A publicly accessible PDF source (IRC certificates)."""
    return SourceRecord(
        slug="irc-certs",
        display_name="IRC Certificate PDFs",
        base_url="https://ircrating.org/pdfdirectory",
        category="certificates",
        policy_version="v1.0",
        legal_status=LegalStatus.APPROVED,
        enabled=True,
        robots_disallow=[],
    )


def login_wall_source() -> SourceRecord:
    """An authenticated source that requires login — no authorisation held.

    This exercises the ``authenticated`` source class.  Collection is
    prohibited because we do not have written authorisation.
    """
    return SourceRecord(
        slug="private-regatta",
        display_name="Private Regatta Portal",
        base_url="https://regatta-private.example.com",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.BLOCKED,  # no auth authorisation
        enabled=False,
        robots_disallow=["/admin", "/private"],
        notes="Login wall — no written authorisation; collection prohibited",
    )


def disallow_source() -> SourceRecord:
    """A source whose robots.txt disallows the target path."""
    return SourceRecord(
        slug="sailwave",
        display_name="Sailwave",
        base_url="https://www.sailwave.com",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.APPROVED,
        enabled=True,
        robots_disallow=["/private"],
    )


def unclear_source() -> SourceRecord:
    """A source with unclear rights status (ClubSpot — on hold)."""
    return SourceRecord(
        slug="clubspot",
        display_name="ClubSpot",
        base_url="https://clubspot.com",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.HOLD,
        enabled=True,
        robots_disallow=[],
        notes="Rights ruling pending; ToS review incomplete",
    )


def disabled_source() -> SourceRecord:
    """A source that was emergency-disabled (enabled=False)."""
    return SourceRecord(
        slug="kwindoo",
        display_name="Kwindoo",
        base_url="https://www.kwindoo.com",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.HOLD,
        enabled=False,  # kill switch active
        robots_disallow=[],
        notes="Emergency disabled — takedown request received",
    )


def stale_policy_source() -> SourceRecord:
    """A source referencing an outdated policy version."""
    return SourceRecord(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://app.sailsys.com.au",
        category="results",
        policy_version="interim-v0",  # ← superseded by v1.0 (mismatch)
        legal_status=LegalStatus.APPROVED,
        enabled=True,
    )


def quarantined_source() -> SourceRecord:
    """A source that is quarantined due to a structure-change incident."""
    from datetime import datetime, timedelta, timezone

    return SourceRecord(
        slug="topyacht",
        display_name="TopYacht",
        base_url="https://www.topyacht.net.au",
        category="results",
        policy_version="v1.0",
        legal_status=LegalStatus.APPROVED,
        enabled=True,
        quarantine_until=datetime.now(timezone.utc) + timedelta(hours=24),
        notes="Quarantined — structure change detected",
    )


# ---------------------------------------------------------------------------
# Robots.txt fixtures
# ---------------------------------------------------------------------------


def public_html_robots() -> RobotsRules:
    """Robots.txt that allows the results path."""
    return parse_robots_txt(
        """
User-agent: *
Allow: /results/
Disallow: /admin/
"""
    )


def disallow_robots() -> RobotsRules:
    """Robots.txt that disallows the private path."""
    return parse_robots_txt(
        """
User-agent: *
Disallow: /private
Disallow: /admin
"""
    )


def all_disallowed_robots() -> RobotsRules:
    """Robots.txt that disallows everything."""
    return parse_robots_txt(
        """
User-agent: *
Disallow: /
"""
    )


def empty_robots() -> RobotsRules:
    """A 404 robots response — no rules, everything allowed."""
    return parse_robots_txt("")


# ---------------------------------------------------------------------------
# URL fixtures (one per case)
# ---------------------------------------------------------------------------

PUBLIC_HTML_URL = "https://app.sailsys.com.au/results/club/37/series/5204"
API_URL = "https://data.orc.org/public/WPub.dll/ERT/JSON"
PDF_URL = "https://ircrating.org/pdfdirectory/cert/GBR12345.pdf"
LOGIN_WALL_URL = "https://regatta-private.example.com/admin/results"
DISALLOW_URL = "https://www.sailwave.com/private/results.html"
UNCLEAR_URL = "https://clubspot.com/results/123"


# ---------------------------------------------------------------------------
# All fixtures bundled for parametrised tests
# ---------------------------------------------------------------------------

ALL_FIXTURES = [
    ("public_html", public_html_source, public_html_robots, PUBLIC_HTML_URL, True),
    ("api", api_source, None, API_URL, True),
    ("pdf", pdf_source, None, PDF_URL, True),
    ("login_wall", login_wall_source, None, LOGIN_WALL_URL, False),
    ("disallow", disallow_source, disallow_robots, DISALLOW_URL, False),
    ("unclear", unclear_source, None, UNCLEAR_URL, False),
    ("disabled", disabled_source, None, UNCLEAR_URL, False),
    ("stale_policy", stale_policy_source, None, PUBLIC_HTML_URL, False),
    ("quarantined", quarantined_source, None, "https://www.topyacht.net.au/results", False),
]
