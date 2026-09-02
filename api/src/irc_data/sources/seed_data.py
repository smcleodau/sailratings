"""Seed entries for the governed Data Source Register (DP-01-01).

Exactly 30 entries covering the sources SailRatings knows about. Legal status
distribution is intentional and load-bearing for the register tests:

* ``approved``   — at least 9 (the interim-v0 approved set plus operational sources)
* ``hold``       — at least 2 (ClubSpot, Kwindoo: discovery metadata only)
* ``unknown``    — at least 1 (rights not yet reviewed)

See SPEC-012 §2.2 and docs/SOURCE-POLICY.md §2–§3 (v1.0).
"""

from __future__ import annotations

from irc_data.sources.models import DataSourceRecordV1
from irc_data.sources.registry import CURRENT_POLICY_VERSION
from irc_data.sources.scheduling import (
    CADENCE_CLASS_DEFAULTS,
    CadenceClass,
    SCHEDULING_POLICY,
    classify_cadence,
)

_P = CURRENT_POLICY_VERSION  # shorthand

# ---------------------------------------------------------------------------
# Approved — content capture enabled (interim-v0 §2.1 + operational sources)
# ---------------------------------------------------------------------------
_APPROVED: list[DataSourceRecordV1] = [
    DataSourceRecordV1(
        slug="sailsys",
        display_name="SailSys",
        base_url="https://app.sailsys.com.au",
        category="results",
        geography="AU",
        access_method="rest_api",
        legal_status="approved",
        cadence="30min",
        format="json",
        identifiers=["sail_number", "boat_name"],
        change_detection="content_hash",
        priority=1,
        adapter_class="irc_data.scrapers.sailsys.SailSysScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=2.0,
        notes="Australian race management; publicly published results.",
    ),
    DataSourceRecordV1(
        slug="topyacht",
        display_name="TopYacht",
        base_url="https://topyacht.net.au/results",
        category="results",
        geography="AU",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=2,
        adapter_class="irc_data.scrapers.topyacht.TopYachtScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=30.0,
    ),
    DataSourceRecordV1(
        slug="irc-tcc",
        display_name="IRC TCC Listings",
        base_url="https://ircrating.org/irc-racing/online-tcc-listings/",
        category="ratings",
        geography="GLOBAL",
        access_method="csv_download",
        legal_status="approved",
        cadence="daily",
        format="csv",
        identifiers=["sail_number", "cert_number"],
        priority=1,
        adapter_class="irc_data.scrapers.tcc_listing.TCCListingScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_admin",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=30.0,
    ),
    DataSourceRecordV1(
        slug="orc",
        display_name="ORC",
        base_url="https://data.orc.org/public/WPub.dll",
        category="ratings",
        geography="GLOBAL",
        access_method="rest_api",
        legal_status="approved",
        cadence="daily",
        format="json",
        identifiers=["sail_number", "cert_number"],
        priority=1,
        adapter_class="irc_data.scrapers.orc.ORCScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_admin",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=30.0,
    ),
    DataSourceRecordV1(
        slug="yachtscoring",
        display_name="Yacht Scoring",
        base_url="https://www.yachtscoring.com",
        category="results",
        geography="US",
        access_method="rest_api",
        legal_status="approved",
        cadence="nightly",
        format="json",
        identifiers=["sail_number", "boat_name"],
        priority=2,
        adapter_class="irc_data.scrapers.yachtscoring.YachtScoringScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
    ),
    DataSourceRecordV1(
        slug="manage2sail",
        display_name="Manage2Sail",
        base_url="https://www.manage2sail.com",
        category="results",
        geography="EU",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.manage2sail.Manage2SailScraper",
        adapter_status="planned",
        robots_status="unchecked",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
    ),
    DataSourceRecordV1(
        slug="sailwave",
        display_name="Sailwave",
        base_url="https://www.sailwave.com/results",
        category="results",
        geography="GLOBAL",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.sailwave.SailwaveScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
    ),
    DataSourceRecordV1(
        slug="sailing-news",
        display_name="Sailing News Feeds",
        base_url="https://www.sailing.org/news",
        category="news",
        geography="GLOBAL",
        access_method="rss",
        legal_status="approved",
        cadence="hourly",
        format="xml",
        identifiers=["url"],
        change_detection="etag",
        priority=3,
        adapter_class="irc_data.scrapers.news.NewsScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="rss_syndication",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=6.0,
    ),
    DataSourceRecordV1(
        slug="irc-certs",
        display_name="IRC Certificate PDFs",
        base_url="https://ircrating.org/boat-data-for-valid-irc-certificates/",
        category="certificates",
        geography="GLOBAL",
        access_method="pdf_download",
        legal_status="approved",
        cadence="weekly",
        format="pdf",
        identifiers=["cert_number", "sail_number"],
        change_detection="content_hash",
        priority=1,
        adapter_class="irc_data.scrapers.certificate_bulk.CertificateBulkScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_admin",
        # Scheduling policy (OPS-01-01)
        cadence_class="weekly_certificates",
        staleness_budget_hours=192.0,
        notes="Approved interim-v0 2026-08-30; derived data only in public API.",
    ),
    DataSourceRecordV1(
        slug="sailracehq",
        display_name="SailRaceHQ",
        base_url="https://sailracehq.com/results",
        category="results",
        geography="US",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.sailracehq.SailRaceHQScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=48.0,
    ),
    DataSourceRecordV1(
        slug="rorc",
        display_name="RORC",
        base_url="https://www.rorc.org",
        category="results",
        geography="GB",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=2,
        adapter_class="irc_data.scrapers.rorc.RORCScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="manual",
        staleness_budget_hours=87600.0,
    ),
    DataSourceRecordV1(
        slug="isora",
        display_name="ISORA",
        base_url="https://www.isora.org",
        category="results",
        geography="IE",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.isora.ISORAScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="daily_results",
        staleness_budget_hours=192.0,
    ),
    DataSourceRecordV1(
        slug="cowesweek",
        display_name="Cowes Week",
        base_url="https://www.cowesweek.co.uk",
        category="results",
        geography="GB",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=3,
        adapter_class="irc_data.scrapers.cowesweek.CowesWeekScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01)
        cadence_class="annual_identifiers",
        staleness_budget_hours=8880.0,
    ),
    DataSourceRecordV1(
        slug="sydney-hobart",
        display_name="Sydney Hobart",
        base_url="https://bwps.cycaracing.com/standings",
        category="results",
        geography="AU",
        access_method="html_scrape",
        legal_status="approved",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=2,
        adapter_class="irc_data.scrapers.sydneyhobart.SydneyHobartScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_results",
        # Scheduling policy (OPS-01-01) — annual December event
        cadence_class="annual_identifiers",
        staleness_budget_hours=8880.0,
    ),
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
        cadence="manual",
        format="json",
        identifiers=["url", "timestamp"],
        change_detection="none",
        priority=4,
        adapter_class="irc_data.scrapers.wayback.WaybackScraper",
        adapter_status="active",
        robots_status="allowed",
        licensing="public_archive",
    ),
    DataSourceRecordV1(
        slug="firecrawl-discovery",
        display_name="Firecrawl Discovery",
        base_url="https://www.firecrawl.dev",
        category="results",
        geography="GLOBAL",
        access_method="rest_api",
        legal_status="approved",
        cadence="nightly",
        format="json",
        identifiers=["url"],
        change_detection="content_hash",
        priority=3,
        adapter_class="irc_data.discovery.firecrawl_client.FirecrawlClient",
        adapter_status="beta",
        robots_status="allowed",
        licensing="api_service",
    ),
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
]

# ---------------------------------------------------------------------------
# Hold — discovery metadata only, no content capture (§2.2)
# ---------------------------------------------------------------------------
_HOLD: list[DataSourceRecordV1] = [
    DataSourceRecordV1(
        slug="clubspot",
        display_name="ClubSpot",
        base_url="https://www.clubspot.com.au",
        category="results",
        geography="EU",
        access_method="html_scrape",
        legal_status="hold",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=4,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unreviewed",
        notes="Rights ruling pending; ToS review incomplete. Discovery only.",
    ),
    DataSourceRecordV1(
        slug="kwindoo",
        display_name="Kwindoo",
        base_url="https://www.kwindoo.com",
        category="results",
        geography="US",
        access_method="html_scrape",
        legal_status="hold",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=4,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unreviewed",
        notes="Rights ruling pending; ToS review incomplete. Discovery only.",
    ),
]

# ---------------------------------------------------------------------------
# Unknown — rights not yet reviewed; discovery metadata only (§2.3)
# ---------------------------------------------------------------------------
_UNKNOWN: list[DataSourceRecordV1] = [
    DataSourceRecordV1(
        slug="yachtscoring-eu",
        display_name="Yacht Scoring (EU)",
        base_url="https://www.yachtscoring.eu",
        category="results",
        geography="EU",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=4,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="regatta-central",
        display_name="Regatta Central",
        base_url="https://www.regattacentral.com",
        category="results",
        geography="US",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=4,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="race-officer",
        display_name="Race Officer",
        base_url="https://www.raceofficer.com",
        category="results",
        geography="GB",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=4,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="sailscore",
        display_name="SailScore",
        base_url="https://www.sailscore.com",
        category="results",
        geography="US",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="racehub",
        display_name="RaceHub",
        base_url="https://www.racehub.com",
        category="results",
        geography="GLOBAL",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="vela-race",
        display_name="Vela Race",
        base_url="https://www.velarace.com",
        category="results",
        geography="EU",
        access_method="rest_api",
        legal_status="unknown",
        cadence="nightly",
        format="json",
        identifiers=["sail_number", "boat_name"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="sailingregatta",
        display_name="SailingRegatta",
        base_url="https://www.sailingregatta.org",
        category="events",
        geography="GLOBAL",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["event_name", "url"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="yacht-club-results",
        display_name="Yacht Club Results",
        base_url="https://www.yachtclubresults.com",
        category="results",
        geography="GLOBAL",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["sail_number", "boat_name"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="sail-event-manager",
        display_name="Sail Event Manager",
        base_url="https://www.saileventmanager.com",
        category="events",
        geography="GLOBAL",
        access_method="html_scrape",
        legal_status="unknown",
        cadence="nightly",
        format="html",
        identifiers=["event_name", "url"],
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
    DataSourceRecordV1(
        slug="offshore-racing",
        display_name="Offshore Racing",
        base_url="https://www.offshoreracing.org",
        category="news",
        geography="GLOBAL",
        access_method="rss",
        legal_status="unknown",
        cadence="hourly",
        format="xml",
        identifiers=["url"],
        change_detection="etag",
        priority=5,
        adapter_status="planned",
        robots_status="unchecked",
        licensing="unknown",
    ),
]

#: All seed entries (30). Order is stable for display.
SEED_SOURCES: list[DataSourceRecordV1] = _APPROVED + _HOLD + _UNKNOWN

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
    print("=" * 60)
    for i, s in enumerate(SEED_SOURCES, 1):
        print(
            f"{i:2d}. {s.slug:<{width}} | {s.category:<12} | "
            f"{s.legal_status:<10} | {s.geography:<6} | {s.access_method}"
        )


if __name__ == "__main__":
    _print_register()
