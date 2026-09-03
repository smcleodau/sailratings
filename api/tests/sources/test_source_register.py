"""Tests for the governed Data Source Register (DP-01-01).

Covers SPEC-012 §2 (source register), §2.3 (enforcement invariant) and the
acceptance criterion that every collection job references an approved source
record and policy decision — and that unknown legal status blocks collection
beyond discovery metadata.

ORM tests use an in-memory SQLite engine so no Postgres or Alembic state is
required.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from irc_data.sources.models import (
    ADAPTER_STATUSES,
    CATEGORIES,
    LEGAL_STATUSES,
    NOTION_LICENSE_TO_LICENSING,
    NOTION_STATUS_TO_ADAPTER_STATUS,
    PRIORITY_TIERS,
    DataSourceRecordV1,
)
from irc_data.sources.registry import (
    CURRENT_POLICY_VERSION,
    DataSource,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_approved,
    assert_policy_current,
    can_collect,
    can_discover,
    get_source,
    get_source_record,
    list_sources,
    resolve_and_assert_approved,
    seed_sources,
)
from irc_data.sources.seed_data import CANONICAL_SEED_SOURCES, SEED_SOURCES

#: The canonical register this checkout carried before OPS-02-14 (30 Notion
#: entries + rhkyc / wayback-irc / yotbot).  OPS-02-14 adds the Solent
#: coverage sources (jog, warsash-spring-series, hamble-winter-series).
_CANONICAL_COUNT = len(CANONICAL_SEED_SOURCES)  # 33
_SOLENT_COUNT = len(SEED_SOURCES) - _CANONICAL_COUNT  # 3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine with the ``data_sources`` table."""
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    DataSource.__table__.create(eng, checkfirst=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def seeded_engine(engine):
    """Engine pre-populated with all 30 seed entries."""
    seed_sources(engine)
    return engine


def _make_record(**overrides) -> DataSourceRecordV1:
    """Build a minimal valid record, overridable per-test."""
    base = dict(
        slug="test-source",
        display_name="Test Source",
        base_url="https://example.com",
        category="results",
        legal_status="approved",
        policy_version=CURRENT_POLICY_VERSION,
        owner="data-platform",
        access_method="html_scrape",
        cadence="nightly",
    )
    base.update(overrides)
    return DataSourceRecordV1(**base)


def _insert(engine, record: DataSourceRecordV1) -> None:
    seed_sources(engine, [record])


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_all_seed_entries_validate(self):
        for entry in SEED_SOURCES:
            record = DataSourceRecordV1.model_validate(entry.model_dump())
            assert record.slug

    def test_required_fields_enforced(self):
        # slug, display_name, base_url, category are required.
        with pytest.raises(Exception):
            DataSourceRecordV1(
                display_name="x", base_url="https://x", category="results"
            )
        with pytest.raises(Exception):
            DataSourceRecordV1(slug="x", base_url="https://x", category="results")

    def test_policy_version_matches_current(self):
        for entry in SEED_SOURCES:
            assert entry.policy_version == CURRENT_POLICY_VERSION


# ---------------------------------------------------------------------------
# Seed counts / uniqueness / distribution
# ---------------------------------------------------------------------------


class TestSeedCount:
    def test_canonical_register_size(self):
        # The canonical register this checkout carried before OPS-02-14.
        assert _CANONICAL_COUNT == 33

    def test_solent_sources_added(self):
        # OPS-02-14 adds exactly three Solent coverage sources.
        assert _SOLENT_COUNT == 3
        assert len(SEED_SOURCES) == _CANONICAL_COUNT + 3


class TestSeedUniqueness:
    def test_all_slugs_unique(self):
        slugs = [s.slug for s in SEED_SOURCES]
        assert len(slugs) == len(set(slugs))


class TestLegalStatusDistribution:
    def test_approved_at_least_9(self):
        n = sum(1 for s in SEED_SOURCES if s.legal_status == "approved")
        assert n >= 9

    def test_hold_at_least_2(self):
        n = sum(1 for s in SEED_SOURCES if s.legal_status == "hold")
        assert n >= 2

    def test_unknown_at_least_1(self):
        n = sum(1 for s in SEED_SOURCES if s.legal_status == "unknown")
        assert n >= 1

    def test_all_statuses_valid(self):
        for s in SEED_SOURCES:
            assert s.legal_status in LEGAL_STATUSES
            assert s.category in CATEGORIES


# ---------------------------------------------------------------------------
# Enforcement invariants (SPEC-012 §2.3, §3.1)
# ---------------------------------------------------------------------------


class TestEnforcementInvariants:
    def test_approved_source_passes(self):
        rec = _make_record(legal_status="approved", enabled=True)
        assert_approved(rec)  # must not raise
        assert can_collect(rec) is True

    def test_hold_source_raises(self):
        rec = _make_record(legal_status="hold")
        with pytest.raises(SourceNotApprovedError):
            assert_approved(rec)
        assert can_collect(rec) is False

    def test_unknown_source_raises(self):
        rec = _make_record(legal_status="unknown")
        with pytest.raises(SourceNotApprovedError):
            assert_approved(rec)
        assert can_collect(rec) is False

    def test_blocked_source_raises(self):
        rec = _make_record(legal_status="blocked")
        with pytest.raises(SourceNotApprovedError):
            assert_approved(rec)
        assert can_collect(rec) is False

    def test_disabled_source_raises(self):
        rec = _make_record(legal_status="approved", enabled=False)
        with pytest.raises(SourceNotApprovedError):
            assert_approved(rec)
        assert can_collect(rec) is False

    def test_policy_version_mismatch_raises(self):
        rec = _make_record(policy_version="interim-v999")
        with pytest.raises(PolicyVersionMismatchError):
            assert_policy_current(rec)

    def test_policy_version_current_passes(self):
        rec = _make_record(policy_version=CURRENT_POLICY_VERSION)
        assert_policy_current(rec)  # must not raise


# ---------------------------------------------------------------------------
# Discovery gating
# ---------------------------------------------------------------------------


class TestDiscoveryGating:
    def test_approved_can_discover(self):
        assert can_discover(_make_record(legal_status="approved")) is True

    def test_hold_can_discover(self):
        assert can_discover(_make_record(legal_status="hold")) is True

    def test_unknown_can_discover(self):
        assert can_discover(_make_record(legal_status="unknown")) is True

    def test_blocked_cannot_discover(self):
        assert can_discover(_make_record(legal_status="blocked")) is False

    def test_disabled_cannot_discover(self):
        rec = _make_record(legal_status="approved", enabled=False)
        assert can_discover(rec) is False

    def test_approved_disabled_cannot_collect(self):
        rec = _make_record(legal_status="approved", enabled=False)
        assert can_collect(rec) is False

    def test_hold_disabled_cannot_discover(self):
        rec = _make_record(legal_status="hold", enabled=False)
        assert can_discover(rec) is False


# ---------------------------------------------------------------------------
# One-call resolve + assert entry point
# ---------------------------------------------------------------------------


class TestResolveAndAssert:
    def test_approved_source_resolves(self, seeded_engine):
        rec = resolve_and_assert_approved(seeded_engine, "sailsys")
        assert isinstance(rec, DataSourceRecordV1)
        assert rec.slug == "sailsys"
        assert rec.legal_status == "approved"

    def test_hold_source_raises_on_content(self, seeded_engine):
        with pytest.raises(SourceNotApprovedError):
            resolve_and_assert_approved(seeded_engine, "clubspot")

    def test_unknown_source_raises_on_content(self, seeded_engine):
        # Unknown legal status blocks content collection (beyond discovery).
        with pytest.raises(SourceNotApprovedError):
            resolve_and_assert_approved(seeded_engine, "crf")

    def test_unknown_source_allows_discovery(self, seeded_engine):
        rec = get_source_record(seeded_engine, "crf")
        assert can_discover(rec) is True
        assert can_collect(rec) is False

    def test_missing_source_raises(self, seeded_engine):
        with pytest.raises(SourceNotApprovedError):
            resolve_and_assert_approved(seeded_engine, "does-not-exist")

    def test_hold_source_allows_discovery(self, seeded_engine):
        rec = get_source_record(seeded_engine, "kwindoo")
        assert can_discover(rec) is True


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class TestOrmModel:
    def test_table_created(self, engine):
        assert DataSource.__table__.name == "data_sources"

    def test_insert_and_query(self, engine):
        _insert(engine, _make_record(slug="sailsys", display_name="SailSys"))
        row = get_source(engine, "sailsys")
        assert row.display_name == "SailSys"
        assert row.legal_status == "approved"

    def test_get_source(self, engine):
        _insert(engine, _make_record(slug="orc", category="ratings"))
        rec = get_source(engine, "orc")
        assert rec.category == "ratings"

    def test_get_source_not_found(self, engine):
        with pytest.raises(SourceNotApprovedError):
            get_source(engine, "nope")

    def test_get_source_record(self, engine):
        _insert(engine, _make_record(slug="orc"))
        rec = get_source_record(engine, "orc")
        assert isinstance(rec, DataSourceRecordV1)
        assert rec.slug == "orc"

    def test_list_sources(self, engine):
        _insert(engine, _make_record(slug="a", category="results"))
        _insert(engine, _make_record(slug="b", category="ratings"))
        _insert(engine, _make_record(slug="c", category="results"))
        all_sources = list_sources(engine)
        assert len(all_sources) == 3

    def test_list_sources_filtered(self, engine):
        _insert(engine, _make_record(slug="a", category="results"))
        _insert(engine, _make_record(slug="b", category="ratings"))
        results = list_sources(engine, category="results")
        assert {r.slug for r in results} == {"a"}
        ratings = list_sources(engine, category="ratings")
        assert {r.slug for r in ratings} == {"b"}


# ---------------------------------------------------------------------------
# Seed scope-field coverage (breadth/value/legality/health visible)
# ---------------------------------------------------------------------------


class TestSeedScopeFields:
    _REQUIRED = (
        "slug",
        "display_name",
        "base_url",
        "category",
        "owner",
        "geography",
        "access_method",
        "cadence",
        "format",
        "legal_status",
        "policy_version",
        "change_detection",
        "priority",
        "adapter_status",
    )

    def test_all_required_fields_present(self):
        for s in SEED_SOURCES:
            dumped = s.model_dump()
            for field in self._REQUIRED:
                assert field in dumped, f"{s.slug} missing {field}"
                assert dumped[field] is not None, f"{s.slug}.{field} is None"

    def test_robots_status_field_exists(self):
        for s in SEED_SOURCES:
            assert hasattr(s, "robots_status")
            assert s.robots_status in (
                "allowed",
                "disallowed",
                "unchecked",
                "no_robots",
            )

    def test_owner_field_present(self):
        for s in SEED_SOURCES:
            assert s.owner, f"{s.slug} has empty owner"


# ---------------------------------------------------------------------------
# Seed function idempotency
# ---------------------------------------------------------------------------


class TestSeedSources:
    def test_seed_sources_count(self, engine):
        # Full register (canonical + OPS-02-14 Solent sources).
        count = seed_sources(engine)
        assert count == len(SEED_SOURCES)

    def test_seed_sources_idempotent(self, engine):
        first = seed_sources(engine)
        second = seed_sources(engine)
        assert first == len(SEED_SOURCES)
        assert second == len(SEED_SOURCES)
        # Seeding twice must not duplicate rows.
        rows = list_sources(engine)
        assert len(rows) == len(SEED_SOURCES)
        assert len({r.slug for r in rows}) == len(SEED_SOURCES)


# ---------------------------------------------------------------------------
# DP-01-01 — Notion Data Source Register carry-over (tiers preserved)
# ---------------------------------------------------------------------------


class TestNotionTierPreservation:
    """The register is seeded from the canonical Notion Data Source Register
    (3a937ffe-f467-813e-874a-ee0ef0760341); tiers must be carried over."""

    #: Canonical (Source Name, Priority Tier, Current Status, License Status,
    #: Ingestion Method) tuples read from the Notion register 2026-09-02.
    _NOTION_REGISTER = {
        "CRF (Classic Rule Formula)": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "CSA (Caribbean Sailing Association)": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "MOCRA": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "CBH (Class Based Handicap)": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "Portsmouth Yardstick": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Bulk Download"),
        "ORR (Offshore Racing Rule)": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "PHRF (North America)": ("Tier 1: Core Identifiers", "Unexplored", "Public Domain", "Web Scraping"),
        "IRC Certificates": ("Tier 1: Core Identifiers", "Prototyped", "Grey Area", "PDF Parsing"),
        "IRC TCC Listings": ("Tier 1: Core Identifiers", "Active", "Public Domain", "Web Scraping"),
        "ORC (Offshore Racing Congress)": ("Tier 1: Core Identifiers", "Active", "TOS Restricted", "Web Scraping"),
        "SailSys": ("Tier 2: Major Race Platforms", "Active", "Licensed/API", "API"),
        "TopYacht": ("Tier 2: Major Race Platforms", "Active", "TOS Restricted", "Web Scraping"),
        "Manage2Sail": ("Tier 2: Major Race Platforms", "Unexplored", "Public Domain", "API"),
        "ClubSpot": ("Tier 2: Major Race Platforms", "Unexplored", "TOS Restricted", "Web Scraping"),
        "Yacht Scoring": ("Tier 2: Major Race Platforms", "Unexplored", "Public Domain", "Web Scraping"),
        "Sydney Hobart": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "RORC": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "ISORA": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Cowes Week": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "SailRaceHQ": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Regatta Toolbox": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Kwindoo": ("Tier 3: Niche/Local Events", "Unexplored", "TOS Restricted", "API"),
        "ORC Scorer": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "St. Pete Scorer": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Railmeets": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Regatta Management Solutions": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "HalSail": ("Tier 3: Niche/Local Events", "Unexplored", "Public Domain", "Web Scraping"),
        "Sailwave": ("Tier 3: Niche/Local Events", "Prototyped", "Public Domain", "Web Scraping"),
        "Sailing News": ("Tier 4: News & Enrichment", "Prototyped", "Public Domain", "Web Scraping"),
        "RacingRulesOfSailing.org": ("Tier 4: News & Enrichment", "Unexplored", "Public Domain", "Web Scraping"),
    }

    _METHOD = {
        "API": "rest_api",
        "Web Scraping": "html_scrape",
        "PDF Parsing": "pdf_download",
        "Bulk Download": "file_download",
    }

    def test_every_notion_source_present_by_display_name(self):
        # Every Notion-register source is present in the full register.
        # (SEED_SOURCES is a superset: it also carries platform additions like
        # OPS-02-14's Solent sources, so we assert containment, not equality.)
        seed_names = {s.display_name for s in SEED_SOURCES}
        assert set(self._NOTION_REGISTER) <= seed_names

    def test_tier_carried_over_for_all_30(self):
        by_name = {s.display_name: s for s in SEED_SOURCES}
        for name, (tier, status, license_, _method) in self._NOTION_REGISTER.items():
            rec = by_name[name]
            assert rec.tier == tier, f"{name}: tier {rec.tier!r} != Notion {tier!r}"
            assert rec.notion_status == status, (
                f"{name}: notion_status {rec.notion_status!r} != Notion {status!r}"
            )
            assert rec.notion_license == license_, (
                f"{name}: notion_license {rec.notion_license!r} != Notion {license_!r}"
            )

    def test_adapter_status_carried_over_from_notion_current_status(self):
        by_name = {s.display_name: s for s in SEED_SOURCES}
        for name, (_tier, status, _lic, _method) in self._NOTION_REGISTER.items():
            rec = by_name[name]
            assert rec.adapter_status == NOTION_STATUS_TO_ADAPTER_STATUS[status], (
                f"{name}: adapter_status {rec.adapter_status!r} != "
                f"{NOTION_STATUS_TO_ADAPTER_STATUS[status]!r} (Notion {status!r})"
            )

    def test_access_method_carried_over_from_notion_ingestion_method(self):
        by_name = {s.display_name: s for s in SEED_SOURCES}
        for name, (_tier, _status, _lic, method) in self._NOTION_REGISTER.items():
            rec = by_name[name]
            assert rec.access_method == self._METHOD[method], (
                f"{name}: access_method {rec.access_method!r} != "
                f"{self._METHOD[method]!r} (Notion {method!r})"
            )

    def test_tier_vocabulary_valid(self):
        # Scoped to the canonical Notion-derived register: OPS-02-14's Solent
        # sources are platform additions and carry a tier too, but the
        # licensing derivation below is only meaningful for Notion rows.
        for s in SEED_SOURCES:
            if s.tier is not None:
                assert s.tier in PRIORITY_TIERS
            assert s.adapter_status in ADAPTER_STATUSES

    def test_licensing_derived_from_notion_license(self):
        for s in SEED_SOURCES:
            if s.notion_license is None:
                continue  # platform additions (no Notion lineage)
            expected = NOTION_LICENSE_TO_LICENSING[s.notion_license]
            assert s.licensing == expected, (
                f"{s.slug}: licensing {s.licensing!r} != derived {expected!r}"
            )


class TestNotionTierDistribution:
    """Distribution carried over from the Notion register (4/3/23, 10/5/13/2).

    These assertions describe the *canonical* Notion register.  They are
    evaluated against :data:`CANONICAL_SEED_SOURCES` (the register before
    OPS-02-14) so the OPS-02-14 Solent additions don't skew the counts.
    """

    def test_adapter_status_distribution(self):
        from collections import Counter

        # Canonical register in this checkout: 5 active (the 4 Notion-active
        # plus rhkyc), 3 prototyped, remaining unexplored/planned.
        dist = Counter(s.adapter_status for s in CANONICAL_SEED_SOURCES)
        assert dist["active"] == 5       # SailSys, TopYacht, IRC TCC, ORC, RHKYC
        assert dist["prototyped"] == 3   # Sailing News, Sailwave, IRC Certs
        assert dist["unexplored"] == 23
        assert sum(dist.values()) == _CANONICAL_COUNT

    def test_tier_distribution(self):
        from collections import Counter

        dist = Counter(s.tier for s in CANONICAL_SEED_SOURCES if s.tier is not None)
        assert dist["Tier 1: Core Identifiers"] == 10
        assert dist["Tier 2: Major Race Platforms"] == 5
        assert dist["Tier 3: Niche/Local Events"] == 13
        assert dist["Tier 4: News & Enrichment"] == 2

    def test_active_sources_are_exactly_notion_active(self):
        active = {s.slug for s in CANONICAL_SEED_SOURCES if s.adapter_status == "active"}
        assert active == {"sailsys", "topyacht", "irc-tcc", "orc", "rhkyc"}

    def test_prototyped_sources_are_exactly_notion_prototyped(self):
        proto = {s.slug for s in SEED_SOURCES if s.adapter_status == "prototyped"}
        assert proto == {"sailing-news", "sailwave", "irc-certs"}

    def test_unexplored_with_no_adapter_are_unknown_legal_status(self):
        """Notion entries with no adapter and no ruling must be 'unknown' so
        that content collection is blocked (discovery metadata only).

        17 of the canonical entries have no platform adapter: the
        unknown-rights sources plus the two ``hold`` sources (ClubSpot,
        Kwindoo) plus ``wayback-irc``.  (Canonical register only — OPS-02-14's
        Solent sources all carry an adapter_class.)
        """
        no_adapter = [s for s in CANONICAL_SEED_SOURCES if s.adapter_class is None]
        assert len(no_adapter) == 17
        for s in no_adapter:
            # wayback-irc is a planned platform addition (not Notion
            # "Unexplored") and is approved for Wayback API reads.
            assert s.adapter_status in ("unexplored", "planned")
            assert s.legal_status in ("unknown", "hold", "approved")
            if s.legal_status != "approved":
                assert can_collect(s) is False
            assert can_discover(s) is True

    def test_unexplored_hold_sources_have_no_adapter(self):
        holds = {s.slug: s for s in SEED_SOURCES if s.legal_status == "hold"}
        assert set(holds) == {"clubspot", "kwindoo"}
        for s in holds.values():
            assert s.adapter_class is None
            assert s.adapter_status == "unexplored"

    def test_hold_sources_still_block_collection(self, seeded_engine):
        for slug in ("clubspot", "kwindoo"):
            with pytest.raises(SourceNotApprovedError):
                resolve_and_assert_approved(seeded_engine, slug)
            rec = get_source_record(seeded_engine, slug)
            assert rec.tier is not None  # tier preserved even on hold
            assert can_discover(rec) is True


class TestTierOrmRoundTrip:
    """The tier columns persist through the ORM (SQLite round-trip)."""

    def test_tier_survives_seed_and_read(self, seeded_engine):
        rec = get_source_record(seeded_engine, "sailsys")
        assert rec.tier == "Tier 2: Major Race Platforms"
        assert rec.notion_status == "Active"
        assert rec.notion_license == "Licensed/API"
        assert rec.adapter_status == "active"

    def test_unexplored_tier_survives_seed_and_read(self, seeded_engine):
        rec = get_source_record(seeded_engine, "phrf")
        assert rec.tier == "Tier 1: Core Identifiers"
        assert rec.notion_status == "Unexplored"
        assert rec.adapter_status == "unexplored"
        assert rec.legal_status == "unknown"


class TestSolentCoverageSources:
    """OPS-02-14 — UK/Solent coverage: the Solent sources are registered with
    policy checks so the schedule registry + watchdog pick them up."""

    SOLENT_SLUGS = ("jog", "warsash-spring-series", "hamble-winter-series")

    def test_solent_sources_present(self):
        slugs = {s.slug for s in SEED_SOURCES}
        for slug in self.SOLENT_SLUGS:
            assert slug in slugs, f"{slug} not registered"

    def test_solent_sources_are_approved_and_enabled(self):
        by_slug = {s.slug: s for s in SEED_SOURCES}
        for slug in self.SOLENT_SLUGS:
            rec = by_slug[slug]
            assert rec.legal_status == "approved", f"{slug} not approved"
            assert rec.enabled, f"{slug} disabled"
            assert rec.geography == "GB"
            assert rec.category == "results"

    def test_solent_sources_carry_scheduling_policy(self):
        # Active sources must carry the OPS-01-01 scheduling fields.
        by_slug = {s.slug: s for s in SEED_SOURCES}
        for slug in self.SOLENT_SLUGS:
            rec = by_slug[slug]
            assert rec.cadence_class == "daily_results"
            assert rec.staleness_budget_hours is not None
            assert rec.nightly_window_start is not None
            assert rec.nightly_window_end is not None
            assert rec.retry_policy is not None
            assert rec.cooldown_hours is not None
            assert rec.kill_switch_ack_hours is not None

    def test_solent_sources_pass_register_validation(self):
        from irc_data.sources.registry import validate_scheduling

        failures = validate_scheduling(
            sources=[s for s in SEED_SOURCES if s.slug in self.SOLENT_SLUGS]
        )
        assert failures == {}, failures

    def test_solent_sources_seed_and_read(self, seeded_engine):
        for slug in self.SOLENT_SLUGS:
            rec = get_source_record(seeded_engine, slug)
            assert rec.legal_status == "approved"
            # Policy gate passes (approved + enabled + current policy).
            assert resolve_and_assert_approved(seeded_engine, slug).slug == slug

    def test_full_register_still_validates_with_solent_sources(self):
        from irc_data.sources.registry import validate_scheduling

        failures = validate_scheduling(include_inactive=True)
        assert failures == {}, failures
