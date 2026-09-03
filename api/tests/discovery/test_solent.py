"""Tests for the OPS-02-14 Solent coverage pipeline.

Covers:
  * the Solent source registry (slugs, policy-checked status),
  * the SOURCE-POLICY gate (discovery vs content collection),
  * the JOG per-race HTML parser (against a real captured fixture page),
  * the 3NF import path (event / entry / result) on an in-memory DB,
  * the Warsash URL expander.

No network or paid-provider calls — the JOG parser is exercised against a
recorded fixture and the DB work is SQLite in-memory.
"""

from __future__ import annotations

import datetime as dt
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from irc_data.discovery.solent import (
    SOLENT_SOURCE_SLUGS,
    SOURCE_HAMBLE,
    SOURCE_JOG,
    SOURCE_WARSASH,
    HalSailResultsSource,
    JOGSource,
    _assert_collectable,
    _assert_discoverable,
    parse_jog_race_html,
)
from irc_data.discovery.url_expanders import expand_for_source
from irc_data.sources.policy import SourceNotApprovedError
from irc_data.sources.registry import (
    DataSource,
    register_source,
    resolve_and_assert_approved,
    seed_sources,
)
from irc_data.sources.gate import SourceRecord, LegalStatus
from irc_data.sources.seed_data import SEED_SOURCES

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
JOG_FIXTURE = FIXTURES / "jog_race_page.html"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestSolentRegistry:
    def test_solent_slugs_are_the_expected_set(self):
        assert SOLENT_SOURCE_SLUGS == (
            "jog",
            "warsash-spring-series",
            "hamble-winter-series",
        )

    def test_solent_sources_in_seed_register(self):
        slugs = {s.slug for s in SEED_SOURCES}
        for slug in SOLENT_SOURCE_SLUGS:
            assert slug in slugs

    def test_solent_sources_approved_with_scheduling(self):
        by_slug = {s.slug: s for s in SEED_SOURCES}
        for slug in SOLENT_SOURCE_SLUGS:
            rec = by_slug[slug]
            assert rec.legal_status == "approved"
            assert rec.enabled
            assert rec.cadence_class == "daily_results"
            assert rec.staleness_budget_hours is not None

    def test_solent_sources_seed_into_db(self):
        from sqlalchemy import create_engine

        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        DataSource.__table__.create(eng, checkfirst=True)
        try:
            seed_sources(eng, [s for s in SEED_SOURCES if s.slug in SOLENT_SOURCE_SLUGS])
            for slug in SOLENT_SOURCE_SLUGS:
                rec = resolve_and_assert_approved(eng, slug)
                assert rec.slug == slug
        finally:
            eng.dispose()


# ---------------------------------------------------------------------------
# Policy gate
# ---------------------------------------------------------------------------


class TestPolicyGate:
    """The content / discovery gates consult the data_sources register.

    To avoid leaking state into the shared in-memory registry overlay (which
    would pollute other tests that assert the canonical seed count), these
    tests seed a throwaway in-memory SQLite ``data_sources`` table and pass
    the engine through.
    """

    def _db(self, slugs):
        from sqlalchemy import create_engine

        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        DataSource.__table__.create(eng, checkfirst=True)
        seed_sources(eng, [s for s in SEED_SOURCES if s.slug in slugs])
        return eng

    def test_approved_solent_source_is_collectable(self):
        eng = self._db(SOLENT_SOURCE_SLUGS)
        try:
            _assert_collectable(eng, SOURCE_JOG)  # must not raise
        finally:
            eng.dispose()

    def test_unknown_source_not_collectable(self):
        eng = self._db(SOLENT_SOURCE_SLUGS)
        try:
            with pytest.raises(SourceNotApprovedError):
                _assert_collectable(eng, "not-a-real-source")
        finally:
            eng.dispose()

    def test_hold_source_is_discoverable_but_not_collectable(self):
        eng = self._db(("clubspot",))  # clubspot is legal_status='hold'
        try:
            _assert_discoverable("clubspot", eng)  # discovery metadata OK
            with pytest.raises(SourceNotApprovedError):
                _assert_collectable(eng, "clubspot")  # but not content
        finally:
            eng.dispose()

    def test_blocked_source_not_discoverable(self):
        # Seed a blocked source row directly.
        from irc_data.sources.models import DataSourceRecordV1
        from sqlalchemy import create_engine as _ce

        eng = _ce("sqlite+pysqlite:///:memory:", future=True)
        DataSource.__table__.create(eng, checkfirst=True)
        blocked = DataSourceRecordV1(
            slug="blocked-x", display_name="X", base_url="https://x",
            category="results", legal_status="blocked", enabled=True,
        )
        seed_sources(eng, [blocked])
        try:
            with pytest.raises(SourceNotApprovedError):
                _assert_discoverable("blocked-x", eng)
        finally:
            eng.dispose()


# ---------------------------------------------------------------------------
# JOG parser (against a real captured race page)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not JOG_FIXTURE.exists(), reason="JOG fixture not captured")
class TestJogParser:
    def _rows(self):
        html = JOG_FIXTURE.read_text(encoding="utf-8", errors="replace")
        return parse_jog_race_html(
            html,
            source_url="https://myjog.jog.org.uk/raceresults/07eb9ff4-c029-4470-bd9d-763128312693",
            event_name="JOG Cowes-Alderney",
            event_date=date(2026, 3, 21),
        )

    def test_parses_all_boats(self):
        rows = self._rows()
        assert len(rows) == 25

    def test_every_row_has_irc_tcc(self):
        rows = self._rows()
        assert all(r.rating_value is not None for r in rows)
        assert all(r.rating_type == "irc_tcc" for r in rows)

    def test_first_row_is_winner(self):
        rows = self._rows()
        assert rows[0].boat_name == "DAWN TREADER"
        assert rows[0].sail_number == "GBR6712R"
        assert rows[0].place == 1
        assert rows[0].rating_value == Decimal("1.091")

    def test_places_are_sequential(self):
        rows = self._rows()
        assert [r.place for r in rows] == list(range(1, len(rows) + 1))

    def test_class_name_detected(self):
        rows = self._rows()
        assert all(r.class_name and "IRC" in r.class_name for r in rows)


class TestJogParserEdgeCases:
    def test_empty_page_yields_no_rows(self):
        rows = parse_jog_race_html("<html><body>no boats</body></html>",
                                   source_url="x", event_name="x")
        assert rows == []

    def test_rows_carry_source_and_event(self):
        rows = parse_jog_race_html(
            JOG_FIXTURE.read_text(encoding="utf-8", errors="replace"),
            source_url="https://x", event_name="E", event_date=date(2025, 4, 1),
        )
        assert all(r.source_url == "https://x" for r in rows)
        assert all(r.event_name == "E" for r in rows)
        assert all(r.event_date == date(2025, 4, 1) for r in rows)


# ---------------------------------------------------------------------------
# URL expander
# ---------------------------------------------------------------------------


class TestWarsashExpander:
    def test_expander_registered(self):
        urls = expand_for_source("warsash-spring-series", "https://x", 2026)
        assert any("black-group-results" in u for u in urls)
        assert any("white-group-results" in u for u in urls)

    def test_unknown_source_falls_back_to_seed(self):
        assert expand_for_source("no-such", "https://seed", None) == ["https://seed"]


# ---------------------------------------------------------------------------
# 3NF import path (in-memory SQLite)
# ---------------------------------------------------------------------------


class TestImportPath:
    """The import must create events + event_entries and link results."""

    def _engine(self):
        from sqlalchemy import create_engine, text

        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        with eng.begin() as c:
            c.execute(text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "start_date DATE, end_date DATE, venue TEXT, organiser TEXT)"
            ))
            c.execute(text(
                "CREATE TABLE event_entries (id INTEGER PRIMARY KEY, event_id INTEGER, "
                "boat_id INTEGER, sail_number TEXT, boat_name TEXT, tcc NUMERIC)"
            ))
            c.execute(text(
                "CREATE TABLE race_results (id INTEGER PRIMARY KEY, "
                "event_entry_id INTEGER NOT NULL, boat_id INTEGER, event_name TEXT, "
                "event_date DATE, race_name TEXT, event_series TEXT, organizing_club TEXT, "
                "event_type TEXT, source TEXT, source_url TEXT, rating_type TEXT, "
                "rating_value NUMERIC, tcc_at_race NUMERIC, place INTEGER, fleet_size INTEGER, "
                "class_name TEXT, class_place INTEGER, class_fleet_size INTEGER, status TEXT, "
                "raw_data TEXT, transport TEXT)"
            ))
            c.execute(text(
                "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT, sail_number TEXT, "
                "design_canonical TEXT)"
            ))
            c.execute(text(
                "CREATE TABLE ingestion_log (id INTEGER PRIMARY KEY, source TEXT, "
                "started_at TEXT, status TEXT)"
            ))
        return eng

    def test_import_creates_event_entry_and_result(self, monkeypatch):
        from irc_data.discovery import solent as solent_mod
        from irc_data.scrapers.result_base import NormalizedResult

        eng = self._engine()
        # Stub out boat matching + ingestion logging (they need the full
        # boats / tcc_snapshots / ingestion_log schema, which the stub DB
        # doesn't carry).  ``_import_normalized`` imports them lazily inside
        # the function, so patch the source modules.
        import irc_data.db.operations as ops
        import irc_data.scrapers.result_import as ri

        monkeypatch.setattr(ops, "log_ingestion_start", lambda e, s, metadata=None: 1)
        monkeypatch.setattr(ops, "log_ingestion_end", lambda e, i, **k: None)
        monkeypatch.setattr(ops, "find_boat_by_sail_number", lambda e, s: None)
        monkeypatch.setattr(ri, "_find_boat_by_name", lambda e, n, t=None: None)

        rows = [
            NormalizedResult(
                boat_name="JAGO", sail_number="GBR9779T", event_name="JOG Test",
                event_date=date(2025, 4, 20), organizing_club="JOG", place=1,
                fleet_size=10, class_name="IRC", rating_type="irc_tcc",
                rating_value=Decimal("1.002"), source_url="https://x",
                raw_data={"boat_name": "JAGO", "sail_number": "GBR9779T"},
            )
        ]
        stats = solent_mod._import_normalized(eng, rows, SOURCE_JOG)
        assert stats["imported"] == 1

        from sqlalchemy import text

        with eng.connect() as c:
            ev = c.execute(text("SELECT count(*) FROM events")).scalar()
            en = c.execute(text("SELECT count(*) FROM event_entries")).scalar()
            rr = c.execute(text(
                "SELECT place, source, boat_id FROM race_results"
            )).fetchone()
        assert ev == 1 and en == 1
        assert rr[0] == 1 and rr[1] == "jog"
        eng.dispose()
