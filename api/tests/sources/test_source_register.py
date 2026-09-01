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
    CATEGORIES,
    LEGAL_STATUSES,
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
from irc_data.sources.seed_data import SEED_SOURCES


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
    def test_exactly_30_seed_entries(self):
        assert len(SEED_SOURCES) == 30


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
            resolve_and_assert_approved(seeded_engine, "yachtscoring-eu")

    def test_unknown_source_allows_discovery(self, seeded_engine):
        rec = get_source_record(seeded_engine, "yachtscoring-eu")
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
        count = seed_sources(engine)
        assert count == 30

    def test_seed_sources_idempotent(self, engine):
        first = seed_sources(engine)
        second = seed_sources(engine)
        assert first == 30
        assert second == 30
        # Seeding twice must not duplicate rows.
        rows = list_sources(engine)
        assert len(rows) == 30
        assert len({r.slug for r in rows}) == 30
