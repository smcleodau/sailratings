"""Seed entries for the governed Data Source Register (DP-01-01).

Exactly 30 entries mirroring the canonical Notion Data Source Register
(``3a937ffe-f467-813e-874a-ee0ef0760341``), preserving its Priority Tier,
Current Status, License Status, Ingestion Method and Update Frequency.

Tier distribution (carried over from Notion):

* Tier 1: Core Identifiers     — 10 entries
* Tier 2: Major Race Platforms —  5 entries
* Tier 3: Niche/Local Events   — 13 entries
* Tier 4: News & Enrichment    —  2 entries

Adapter status distribution (Notion "Current Status" carried over):

* ``active``      —  4 (SailSys, TopYacht, IRC TCC Listings, ORC)
* ``prototyped``  —  3 (Sailing News, Sailwave, IRC Certificates)
* ``unexplored``  — 23 (all remaining entries)

Legal status distribution is intentional and load-bearing for the register
tests (SPEC-012 §2.2–§2.3, docs/SOURCE-POLICY.md §2–§3 v1.0):

* ``approved``   — at least 9 (the interim-v0 approved set plus operational sources)
* ``hold``       — at least 2 (ClubSpot, Kwindoo: discovery metadata only)
* ``unknown``    — at least 1 (rights not yet reviewed; discovery only)

Entries that exist in the Notion register but have no platform adapter
(e.g. CRF, MOCRA, PHRF, HalSail) are seeded with ``legal_status="unknown"``
so that, per the enforcement invariant, no content collection can run
against them until a rights ruling is recorded — discovery metadata only.
"""

from __future__ import annotations

from irc_data.sources.models import (
    NOTION_LICENSE_TO_LICENSING,
    NOTION_STATUS_TO_ADAPTER_STATUS,
    DataSourceRecordV1,
)
from irc_data.sources.registry import CURRENT_POLICY_VERSION
from irc_data.sources.scheduling import (
    CADENCE_CLASS_DEFAULTS,
    CadenceClass,
    SCHEDULING_POLICY,
    classify_cadence,
)

_P = CURRENT_POLICY_VERSION  # shorthand

# Re-exported so callers/tests can assert the carry-over mapping.
__all__ = [
    "SEED_SOURCES",
    "NOTION_STATUS_TO_ADAPTER_STATUS",
    "NOTION_LICENSE_TO_LICENSING",
    "TIER1",
    "TIER2",
    "TIER3",
    "TIER4",
]

#: Notion register "Priority Tier" labels (exact strings, preserved).
TIER1 = "Tier 1: Core Identifiers"
TIER2 = "Tier 2: Major Race Platforms"
TIER3 = "Tier 3: Niche/Local Events"
TIER4 = "Tier 4: News & Enrichment"

#: Notion "Ingestion Method" → register ``access_method``.
_METHOD: dict[str, str] = {
    "API": "rest_api",
    "Web Scraping": "html_scrape",
    "PDF Parsing": "pdf_download",
    "Bulk Download": "file_download",
}

#: Notion "Update Frequency" → register ``cadence``.
_FREQ: dict[str, str] = {
    "Real-time/Webhook": "hourly",
    "Daily": "nightly",
    "Weekly": "weekly",
    "Monthly": "monthly",
    "Annually": "annual",
    "Static/One-off": "one_off",
}

#: Notion "Entities Produced" → register ``identifiers``.
_ENTITY_IDS: dict[str, list[str]] = {
    "Boat": ["sail_number", "boat_name"],
    "Certificate": ["cert_number", "sail_number"],
    "RaceResult": ["event_slug", "sail_number"],
    "Event": ["event_slug"],
    "EventEntry": ["event_slug", "sail_number"],
    "News": ["url"],
}


def _ident(*entities: str) -> list[str]:
    """Merge the identifier lists for the given Notion entities (ordered,
    de-duplicated)."""
    out: list[str] = []
    for e in entities:
        for ident in _ENTITY_IDS[e]:
            if ident not in out:
                out.append(ident)
    return out

# ---------------------------------------------------------------------------
# Tier 1: Core Identifiers (Notion Priority Tier, preserved)
#
# Rating/rule authorities and certificate systems: the identifier backbone.
# Notion Current Status carried over: 4 entries Unexplored (MOCRA, CBH,
# Portsmouth Yardstick, ORR — plus PHRF, CRF, CSA), 1 Prototyped
# (IRC Certificates), 2 Active (IRC TCC Listings, ORC).
# ---------------------------------------------------------------------------
_TIER1: list[DataSourceRecordV1] = [
    # -- Active --------------------------------------------------------------
    DataSourceRecordV1(
        slug="irc-tcc",
        display_name="IRC TCC Listings",
        base_url="https://ircrating.org/irc-racing/online-tcc-listings/",
        category="ratings",
        geography="GLOBAL",
        tier=TIER1,
        notion_status="Active",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="csv",
        identifiers=_ident("Boat"),
        priority=1,
        adapter_class="irc_data.scrapers.tcc_listing.TCCListingScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Active"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        notes="Notion: Active / Public Domain / Web Scraping / Daily.",
    ),
    DataSourceRecordV1(
        slug="orc",
        display_name="ORC (Offshore Racing Congress)",
        base_url="https://data.orc.org/public/WPub.dll",
        category="ratings",
        geography="GLOBAL",
        tier=TIER1,
        notion_status="Active",
        notion_license="TOS Restricted",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="json",
        identifiers=_ident("Boat", "Certificate"),
        priority=1,
        adapter_class="irc_data.scrapers.orc.ORCScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Active"],
        legal_status="approved",
        terms_status="reviewed",
        robots_status="allowed",
        licensing="tos_restricted",
        notes="Notion: Active / TOS Restricted / Web Scraping / Daily. "
              "Accessed via public JSON endpoint; ToS noted, ruling recorded.",
    ),
    # -- Prototyped ------------------------------------------------------------
    DataSourceRecordV1(
        slug="irc-certs",
        display_name="IRC Certificates",
        base_url="https://ircrating.org/boat-data-for-valid-irc-certificates/",
        category="certificates",
        geography="GLOBAL",
        tier=TIER1,
        notion_status="Prototyped",
        notion_license="Grey Area",
        access_method=_METHOD["PDF Parsing"],
        cadence=_FREQ["Daily"],
        format="pdf",
        identifiers=_ident("Boat", "Certificate"),
        change_detection="content_hash",
        priority=1,
        adapter_class="irc_data.scrapers.certificate_bulk.CertificateBulkScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Prototyped"],
        legal_status="approved",
        terms_status="reviewed",
        robots_status="allowed",
        licensing="grey_area",
        notes="Notion: Prototyped / Grey Area / PDF Parsing / Daily. "
              "Approved v1.0 2026-09-02 (originally interim-v0 2026-08-30); "
              "derived data only in public API.",
    ),
    # -- Unexplored ------------------------------------------------------------
    DataSourceRecordV1(
        slug="crf",
        display_name="CRF (Classic Rule Formula)",
        base_url="https://www.classicrating.com",
        category="ratings",
        geography="US",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Boat", "Certificate"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="csa",
        display_name="CSA (Caribbean Sailing Association)",
        base_url="https://www.caribbean-sailing.com",
        category="ratings",
        geography="CARICOM",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Boat", "Certificate"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="mocra",
        display_name="MOCRA",
        base_url="https://www.mocra.org",
        category="ratings",
        geography="GB",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Monthly"],
        format="html",
        identifiers=_ident("Boat", "Certificate"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Monthly. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="cbh",
        display_name="CBH (Class Based Handicap)",
        base_url="https://www.rya.org.uk/racing/technical/handicap-systems",
        category="ratings",
        geography="GB",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Boat"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="portsmouth-yardstick",
        display_name="Portsmouth Yardstick",
        base_url="https://www.rya.org.uk/racing/technical/handicap-systems/portsmouth-yardstick",
        category="ratings",
        geography="GB",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Bulk Download"],
        cadence=_FREQ["Annually"],
        format="csv",
        identifiers=_ident("Boat"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Bulk Download / Annually. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="orr",
        display_name="ORR (Offshore Racing Rule)",
        base_url="https://www.offshoreracingrule.org",
        category="ratings",
        geography="US",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Monthly"],
        format="html",
        identifiers=_ident("Boat", "Certificate"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Monthly. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="phrf",
        display_name="PHRF (North America)",
        base_url="https://www.phrfne.org",
        category="ratings",
        geography="US",
        tier=TIER1,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Monthly"],
        format="html",
        identifiers=_ident("Boat", "Certificate"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Monthly. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
]

# ---------------------------------------------------------------------------
# Tier 2: Major Race Platforms (Notion Priority Tier, preserved)
#
# High-volume race management / results platforms.
# Notion Current Status carried over: 2 Active (SailSys, TopYacht),
# 3 Unexplored (Manage2Sail, ClubSpot, Yacht Scoring).
# ---------------------------------------------------------------------------
_TIER2: list[DataSourceRecordV1] = [
    # -- Active --------------------------------------------------------------
    DataSourceRecordV1(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://app.sailsys.com.au",
        category="results",
        geography="AU",
        tier=TIER2,
        notion_status="Active",
        notion_license="Licensed/API",
        access_method=_METHOD["API"],
        legal_status="approved",
        cadence="30min",  # live ops cadence (Supersedes Notion "Daily")
        format="json",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        change_detection="content_hash",
        priority=1,
        adapter_class="irc_data.scrapers.sailsys.SailSysScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Active"],
        terms_status="reviewed",
        robots_status="allowed",
        licensing="licensed_api",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=2.0,
        notes="Notion: Active / Licensed/API / API / Daily. "
              "Australian race management; publicly published results.",
    ),
    DataSourceRecordV1(
        slug="topyacht",
        display_name="TopYacht",
        base_url="https://topyacht.net.au/results",
        category="results",
        geography="AU",
        tier=TIER2,
        notion_status="Active",
        notion_license="TOS Restricted",
        access_method=_METHOD["Web Scraping"],
        legal_status="approved",
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=2,
        adapter_class="irc_data.scrapers.topyacht.TopYachtScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Active"],
        terms_status="reviewed",
        robots_status="allowed",
        licensing="tos_restricted",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=30.0,
        notes="Notion: Active / TOS Restricted / Web Scraping / Daily. "
              "Public results pages; ToS ruling recorded (v1.0).",
    ),
    # -- Unexplored ------------------------------------------------------------
    DataSourceRecordV1(
        slug="manage2sail",
        display_name="Manage2Sail",
        base_url="https://www.manage2sail.com",
        category="results",
        geography="EU",
        tier=TIER2,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["API"],
        legal_status="approved",
        cadence=_FREQ["Daily"],
        format="json",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.manage2sail.Manage2SailScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        robots_status="unchecked",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
        notes="Notion: Unexplored / Public Domain / API / Daily. "
              "DP-00-03 raw capture landed; adapter not yet registered.",
    ),
    DataSourceRecordV1(
        slug="clubspot",
        display_name="ClubSpot",
        base_url="https://www.clubspot.com.au",
        category="results",
        geography="EU",
        tier=TIER2,
        notion_status="Unexplored",
        notion_license="TOS Restricted",
        access_method=_METHOD["Web Scraping"],
        legal_status="hold",
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        robots_status="unchecked",
        licensing="tos_restricted",
        terms_status="reviewed",
        notes="Notion: Unexplored / TOS Restricted / Web Scraping / Daily. "
              "Rights ruling pending; ToS review incomplete. Discovery only.",
    ),
    DataSourceRecordV1(
        slug="yachtscoring",
        display_name="Yacht Scoring",
        base_url="https://www.yachtscoring.com",
        category="results",
        geography="US",
        tier=TIER2,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        legal_status="approved",
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=2,
        adapter_class="irc_data.scrapers.yachtscoring.YachtScoringScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "DP-00-03 raw capture landed; adapter not yet registered.",
    ),
]
# ---------------------------------------------------------------------------
# Tier 3: Niche/Local Events (Notion Priority Tier, preserved)
#
# Club / regional race platforms and event-specific scrapers.
# Notion Current Status carried over: 1 Prototyped (Sailwave),
# 12 Unexplored.
# ---------------------------------------------------------------------------
_TIER3: list[DataSourceRecordV1] = [
    # -- Prototyped ----------------------------------------------------------
    DataSourceRecordV1(
        slug="sailwave",
        display_name="Sailwave",
        base_url="https://www.sailwave.com/results",
        category="results",
        geography="GLOBAL",
        tier=TIER3,
        notion_status="Prototyped",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Weekly"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.sailwave.SailwaveScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Prototyped"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
        notes="Notion: Prototyped / Public Domain / Web Scraping / Weekly. "
              "Results files publicly linked from club sites.",
    ),
    # -- Unexplored ----------------------------------------------------------
    DataSourceRecordV1(
        slug="sydney-hobart",
        display_name="Sydney Hobart",
        base_url="https://bwps.cycaracing.com/standings",
        category="results",
        geography="AU",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=2,
        adapter_class="irc_data.scrapers.sydneyhobart.SydneyHobartScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="annual_identifiers",
        staleness_budget_hours=8880.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "Event-specific scraper exists (bespoke); register tier carried over.",
    ),
    DataSourceRecordV1(
        slug="rorc",
        display_name="RORC",
        base_url="https://www.rorc.org",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=2,
        adapter_class="irc_data.scrapers.rorc.RORCScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="manual",
        staleness_budget_hours=87600.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "Event-specific scraper exists (bespoke); register tier carried over.",
    ),
    DataSourceRecordV1(
        slug="isora",
        display_name="ISORA",
        base_url="https://www.isora.org",
        category="results",
        geography="IE",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.isora.ISORAScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=192.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "Event-specific scraper exists (bespoke); register tier carried over.",
    ),
    DataSourceRecordV1(
        slug="cowesweek",
        display_name="Cowes Week",
        base_url="https://www.cowesweek.co.uk",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Annually"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.cowesweek.CowesWeekScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="annual_identifiers",
        staleness_budget_hours=8880.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Annually. "
              "Event-specific scraper exists (bespoke); register tier carried over.",
    ),
    DataSourceRecordV1(
        slug="sailracehq",
        display_name="SailRaceHQ",
        base_url="https://sailracehq.com/results",
        category="results",
        geography="US",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.sailracehq.SailRaceHQScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "Generic HTML/file parsing scraper exists.",
    ),
    # OPS-01-01 new sources (not in HEAD Notion register; appended after existing list)
    DataSourceRecordV1(
        slug="rhkyc",
        display_name="RHKYC",
        base_url="https://www.rhkyc.org.hk",
        category="results",
        geography="HK",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.rhkyc.RHKYCScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01) — weekly Wed 10:00 UTC ops cadence
        cadence_class="daily_results",
        staleness_budget_hours=192.0,
    ),
    DataSourceRecordV1(
        slug="wayback-irc",
        display_name="Wayback Machine — IRC",
        base_url="https://web.archive.org/web",
        category="ratings",
        geography="GLOBAL",
        access_method="rest_api",
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
    ),
    DataSourceRecordV1(
        slug="regatta-toolbox",
        display_name="Regatta Toolbox",
        base_url="https://www.regattatoolbox.com",
        category="results",
        geography="GLOBAL",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    # OPS-01-01 new source (not in HEAD Notion register; appended)
    DataSourceRecordV1(
        slug="yotbot",
        display_name="Yotbot",
        base_url="https://www.yotbot.com.au",
        category="results",
        geography="AU",
        access_method="rest_api",
        legal_status="approved",
        cadence="nightly",
        format="json",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.yotbot.YotbotScraper",
        adapter_status="planned",
        robots_status="unchecked",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
    ),
    DataSourceRecordV1(
        slug="kwindoo",
        display_name="Kwindoo",
        base_url="https://www.kwindoo.com",
        category="results",
        geography="US",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="TOS Restricted",
        access_method=_METHOD["API"],
        legal_status="hold",
        cadence=_FREQ["Real-time/Webhook"],
        format="json",
        identifiers=_ident("Event", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        robots_status="unchecked",
        licensing="tos_restricted",
        terms_status="reviewed",
        notes="Notion: Unexplored / TOS Restricted / API / Real-time/Webhook. "
              "Rights ruling pending; ToS review incomplete. Discovery only.",
    ),
    DataSourceRecordV1(
        slug="orc-scorer",
        display_name="ORC Scorer",
        base_url="https://www.orcscorer.com",
        category="results",
        geography="GLOBAL",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Static/One-off"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=5,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Static/One-off. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="st-pete-scorer",
        display_name="St. Pete Scorer",
        base_url="https://www.stpetescorer.com",
        category="results",
        geography="US",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Static/One-off"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=5,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Static/One-off. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="railmeets",
        display_name="Railmeets",
        base_url="https://www.railmeets.com",
        category="results",
        geography="US",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="regatta-management-solutions",
        display_name="Regatta Management Solutions",
        base_url="https://www.regattamanagementsolutions.com",
        category="results",
        geography="GLOBAL",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
    DataSourceRecordV1(
        slug="halsail",
        display_name="HalSail",
        base_url="https://www.halsail.com",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=4,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
]

# ---------------------------------------------------------------------------
# Tier 4: News & Enrichment (Notion Priority Tier, preserved)
#
# News, rules and enrichment sources.
# Notion Current Status carried over: 1 Prototyped (Sailing News),
# 1 Unexplored (RacingRulesOfSailing.org).
# ---------------------------------------------------------------------------
_TIER4: list[DataSourceRecordV1] = [
    # -- Prototyped ----------------------------------------------------------
    DataSourceRecordV1(
        slug="sailing-news",
        display_name="Sailing News",
        base_url="https://www.sailing.org/news",
        category="news",
        geography="GLOBAL",
        tier=TIER4,
        notion_status="Prototyped",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence="hourly",  # live ops cadence (Supersedes Notion "Daily")
        format="xml",
        identifiers=_ident("News", "Boat"),
        change_detection="etag",
        priority=3,
        adapter_class="irc_data.scrapers.raw_capture.capture_news_feeds",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Prototyped"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        notes="Notion: Prototyped / Public Domain / Web Scraping / Daily. "
              "OPS-02-06: moved off Firecrawl — RSS/Atom raw capture "
              "(sailingscuttlebutt, SailWeb, Sail-World) + Gemini mention "
              "extraction. Zero Firecrawl credits on news domains.",
    ),
    # -- Unexplored ----------------------------------------------------------
    DataSourceRecordV1(
        slug="racing-rules-of-sailing",
        display_name="RacingRulesOfSailing.org",
        base_url="https://www.racingrulesofsailing.org",
        category="events",
        geography="GLOBAL",
        tier=TIER4,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event"),
        priority=5,
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="unknown",
        robots_status="unchecked",
        licensing="public_domain",
        notes="Notion: Unexplored / Public Domain / Web Scraping / Daily. "
              "No adapter yet; rights not yet reviewed — discovery only.",
    ),
]

# ---------------------------------------------------------------------------
# OPS-02-14 — UK / Solent coverage sources
#
# The boats that pay are Solent boats (Sun Fast 3300, J/109 fleets), not just
# Sydney.  These entries register the UK / Solent results platforms whose
# pages the discovery pipeline (``irc_data.discovery.solent``) finds and the
# ingestion pipeline imports into ``race_results``.  Each carries the
# scheduling-policy fields so the register stays valid and the watchdog /
# schedule registry can pick them up without a schema change.
#
# Legal status / rights review (``docs/SOURCE-POLICY.md`` §2–§3):
#   * ``jog`` / ``warsash-spring-series`` / ``hamble-winter-series`` publish
#     their full race results publicly (no login), so they are ``approved``
#     for content collection.
#   * ``halsail`` stays ``unknown`` (HalSail is a scoring-platform host used
#     by HRSC / Hamble; the club results pages it serves are registered via
#     the concrete ``hamble-winter-series`` entry and discovered per-event).
# ---------------------------------------------------------------------------
_SOLENT: list[DataSourceRecordV1] = [
    DataSourceRecordV1(
        slug="jog",
        display_name="JOG (Junior Offshore Group)",
        base_url="https://myjog.jog.org.uk/results",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Daily"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.discovery.solent.JOGSource",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
        notes="OPS-02-14 Solent coverage. JOG publishes full IRC race results "
              "publicly at myjog.jog.org.uk (server-rendered, per-race "
              "/raceresults/<uuid> pages keyed by ?year=). Covers Solent "
              "cross-channel + coastal races.",
    ),
    DataSourceRecordV1(
        slug="warsash-spring-series",
        display_name="Warsash Spring Series / Spring Championships",
        base_url="https://warsashsc.org.uk/springseries/black-group-results/",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Weekly"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.scrapers.sailwave.SailwaveScraper",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=192.0,
        notes="OPS-02-14 Solent coverage. Warsash SC Spring Series / Spring "
              "Championships (Solent, Hamble). Results published as public "
              "Sailwave files on sailwave.com/results/warsashsc — imported "
              "via the sailwave source + per-source expander.",
    ),
    DataSourceRecordV1(
        slug="hamble-winter-series",
        display_name="Hamble Winter Series (HRSC)",
        base_url="https://www.hamblewinterseries.com",
        category="results",
        geography="GB",
        tier=TIER3,
        notion_status="Unexplored",
        notion_license="Public Domain",
        access_method=_METHOD["Web Scraping"],
        cadence=_FREQ["Weekly"],
        format="html",
        identifiers=_ident("Event", "EventEntry", "RaceResult"),
        priority=3,
        adapter_class="irc_data.discovery.solent.HalSailResultsSource",
        adapter_status=NOTION_STATUS_TO_ADAPTER_STATUS["Unexplored"],
        legal_status="approved",
        robots_status="allowed",
        licensing="public_domain",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=192.0,
        notes="OPS-02-14 Solent coverage. HRSC Hamble Winter Series results "
              "are published publicly via HalSail (halsail.com/Result/Club/"
              "3560 and per-event /Result/Event/<id>). JS-rendered — collected "
              "through the discovery pipeline (Firecrawl).",
    ),
]

#: Canonical register before OPS-02-14 (33 entries: the 30 Notion register
#: entries plus 3 platform additions already present in this checkout —
#: ``rhkyc``, ``wayback-irc``, ``yotbot``).  Order is stable for display.
CANONICAL_SEED_SOURCES: list[DataSourceRecordV1] = _TIER1 + _TIER2 + _TIER3 + _TIER4

#: All seed entries, including the OPS-02-14 Solent coverage sources (JOG,
#: Warsash Spring Series, Hamble Winter Series / HRSC).  Tests that assert the
#: canonical register use :data:`CANONICAL_SEED_SOURCES`; everything that
#: seeds or validates the full register uses this superset.
SEED_SOURCES: list[DataSourceRecordV1] = CANONICAL_SEED_SOURCES + _SOLENT

# Stamp every seed with the current policy version (they were all governed
# under interim-v0). This makes the policy decision each record references
# explicit rather than relying on the schema default.
for _s in SEED_SOURCES:
    if not _s.policy_version:
        _s.policy_version = _P

# ---------------------------------------------------------------------------
# Scheduling policy stamping (OPS-01-01 / docs/SCHEDULING-POLICY.md sched-v1.0)
# ---------------------------------------------------------------------------
# Every register row must carry explicit scheduling values so that "how
# often, how late is too late" is visible per source.  Explicit per-source
# values above win; anything unset is filled from the cadence-class design
# defaults plus the collection-policy nightly window (01:00–06:00) and the
# global cooldown (4 h) / takedown-ack (4 h) constants.  After stamping,
# the full register (including hold/unknown rows) passes
# ``irc_data.sources.registry.validate_scheduling(include_inactive=True)``,
# so a source can be re-activated without a schema/config change.
for _s in SEED_SOURCES:
    _cc = CadenceClass(_s.cadence_class) if _s.cadence_class else classify_cadence(_s.cadence)
    _d = CADENCE_CLASS_DEFAULTS[_cc]
    if not _s.cadence_class:
        _s.cadence_class = _cc.value
    if _s.staleness_budget_hours is None:
        _s.staleness_budget_hours = float(_d["staleness_budget_hours"])
    if _s.nightly_window_start is None:
        _s.nightly_window_start = SCHEDULING_POLICY.nightly_window[0]
    if _s.nightly_window_end is None:
        _s.nightly_window_end = SCHEDULING_POLICY.nightly_window[1]
    if _s.retry_policy is None:
        _s.retry_policy = {
            "max_attempts": int(_d["retry_max_attempts"]),
            "backoff_seconds": list(_d["retry_backoff_seconds"]),
        }
    if _s.cooldown_hours is None:
        _s.cooldown_hours = float(_d["cooldown_hours"])
    if _s.kill_switch_ack_hours is None:
        _s.kill_switch_ack_hours = SCHEDULING_POLICY.kill_switch.ack_window_hours


def _print_register() -> None:
    width = max(len(s.slug) for s in SEED_SOURCES)
    print(f"DP-01-01 Source Register: {len(SEED_SOURCES)} Seed Entries")
    print("=" * 100)
    for i, s in enumerate(SEED_SOURCES, 1):
        print(
            f"{i:2d}. {s.slug:<{width}} | {(s.tier or '-'):<30} | "
            f"{s.adapter_status:<10} | {s.legal_status:<8} | "
            f"{s.access_method:<11} | {s.cadence:<7} | {s.category}"
        )


if __name__ == "__main__":
    _print_register()
