"""Natural-key matching for race_results (re-scrape dedup).

Unit tests cover the pure matcher in ``irc_data.db.result_identity``; the
integration tests run ``upsert_race_result`` against a throwaway Postgres
schema (skipped when no database is reachable).
"""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import create_engine, text

from irc_data.db.result_identity import (
    ResultIdentity,
    canonical_source_url,
    match_strength,
    normalize_key_name,
    pick_existing,
)

# ---------------------------------------------------------------------------
# Pure matcher
# ---------------------------------------------------------------------------


def _rid(id=None, name="Panther", sail="GBR 6690R", place=1, boat_id=None,
         url="https://example.org/r1", status="finished"):
    return ResultIdentity.build(id=id, boat_name=name, sail_number=sail, place=place,
                                status=status, boat_id=boat_id, source_url=url)


def test_normalisation_keeps_unicode_digits_apart():
    assert normalize_key_name("State O'Shassis") == "STATEOSHASSIS"
    assert normalize_key_name("ABRACADABRA ²") != normalize_key_name("ABRACADABRA")


def test_canonical_url_known_site_moves():
    assert canonical_source_url(
        "https://www.rorc.org/files/raceresults/2022/balticrace-one01.html"
    ) == canonical_source_url("https://www.rorc.org/raceresults/2022/balticrace-one01.html")
    old = ("https://www.cowesweek.co.uk/web/code/php/main_c.php?map=cw26&ui=cw4&style=std"
           "&override=&section=racing&page=points2025&resultrequest=5")
    new = ("https://www.cowesweek.co.uk/web/code/php/main_c.php?map=cw-res&ui=cw-res&style=std"
           "&override=&section=regatta&page=points2025&resultrequest=5")
    assert canonical_source_url(old) == canonical_source_url(new)
    assert canonical_source_url(old) != canonical_source_url(old.replace("=5", "=10"))
    # Distinct RORC season pages stay distinct.
    assert canonical_source_url("https://www.rorc.org/raceresults/2022/zero15.html") != \
        canonical_source_url("https://www.rorc.org/raceresults/2022/zero09.html")


def test_strong_match_ignores_boat_id_and_place():
    old = _rid(id=1, boat_id=444, place=24)
    new = _rid(boat_id=20201, place=31)
    assert match_strength(new, old) == "strong"


def test_renamed_boat_same_sail_same_place_is_weak_match():
    old = _rid(id=1, name="MADRUGADA", sail="GBR942R", place=6, boat_id=1887)
    new = _rid(name="BULLDOG", sail="GBR942R", place=6, boat_id=1887)
    assert match_strength(new, old) == "weak"


def test_corrected_sail_same_name_same_place_is_weak_match():
    old = _rid(id=1, name="BRAVEHEART", sail="USA52", place=1, boat_id=444)
    new = _rid(name="BRAVEHEART", sail="GBR17N", place=1, boat_id=20201)
    assert match_strength(new, old) == "weak"


def test_same_name_different_boat_different_place_does_not_match():
    old = _rid(id=1, name="Panther", sail="GBR1", place=3, boat_id=10)
    new = _rid(name="Panther", sail="GBR2", place=7, boat_id=11)
    assert match_strength(new, old) is None
    # ... even when the existing row was never matched to a boat.
    assert match_strength(new, _rid(id=1, name="Panther", sail="GBR1", place=3)) is None


def test_no_sail_same_name_different_matched_boats_do_not_match():
    old = _rid(id=1, name="MOANA", sail=None, place=50, boat_id=5526)
    new = _rid(name="MOANA", sail=None, place=71, boat_id=20832)
    assert match_strength(new, old) is None


def test_no_sail_standing_moved_and_newly_matched_matches():
    # ISORA overall standing: Amoret has no sail, place moved, now matched.
    old = _rid(id=1, name="Amoret", sail=None, place=24, boat_id=None)
    new = _rid(name="Amoret", sail=None, place=31, boat_id=56281)
    assert match_strength(new, old) == "weak"


def test_different_page_never_matches():
    old = _rid(id=1, url="https://rhkyc.example/SBR2021-22JasmieOverall.html")
    new = _rid(url="https://rhkyc.example/SBR2021-22OrangePekoeOverall.html")
    assert match_strength(new, old) is None


def test_pick_prefers_strong_then_newest():
    weak = _rid(id=5, name="BULLDOG", sail="GBR942R", place=6)
    strong_old = _rid(id=2, name="MADRUGADA", sail="GBR942R", place=9)
    strong_new = _rid(id=3, name="MADRUGADA", sail="GBR942R", place=9)
    new = _rid(name="MADRUGADA", sail="GBR942R", place=6)
    assert pick_existing(new, [weak, strong_old, strong_new]).id == 3
    assert pick_existing(_rid(name="Other", sail="X1"), [weak]) is None


# ---------------------------------------------------------------------------
# upsert_race_result against a throwaway Postgres schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE boats (id serial PRIMARY KEY, boat_name text, sail_number text);
CREATE TABLE events (id serial PRIMARY KEY, name text, start_date date, end_date date,
    organiser text, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now());
CREATE TABLE event_entries (id serial PRIMARY KEY, event_id int REFERENCES events(id),
    boat_id int REFERENCES boats(id), sail_number text, boat_name text, tcc numeric(5,3),
    design text, created_at timestamptz DEFAULT now());
CREATE TABLE race_results (
    id serial PRIMARY KEY, boat_id int REFERENCES boats(id), event_name text NOT NULL,
    event_date date, source_url text, tcc_at_race numeric(6,4), place int, division text,
    elapsed_time interval, corrected_time interval, raw_data jsonb, event_series text,
    organizing_club text, event_type text, race_name text, race_date_specific date,
    race_number int, course_distance_nm numeric(8,2), fleet_size int, class_name text,
    class_place int, class_fleet_size int, status text DEFAULT 'finished', rating_type text,
    rating_value numeric(8,4), time_behind_winner interval, source text,
    created_at timestamptz DEFAULT now(), transport varchar(32),
    event_entry_id int NOT NULL REFERENCES event_entries(id));
INSERT INTO boats (id, boat_name, sail_number) VALUES
    (1, 'Panther', 'GBR6690R'), (2, 'Panther', 'GBR6690R'), (3, 'Jezebel', 'GBR8911R');
"""


@pytest.fixture()
def pg_engine():
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://irc:irc@localhost:5433/irc_data")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"test_rr_identity_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url)
    try:
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
    except Exception as exc:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"PostgreSQL not available for integration test: {exc}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as conn:
            for stmt in _DDL.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def _save(engine, boat_id, name, sail, place, race_name="Race 1", race_date=None,
          event="2026 Test Regatta", url="https://example.org/results/1"):
    from irc_data.db.operations import upsert_race_result

    return upsert_race_result(
        engine, boat_id=boat_id, event_name=event, event_date=date(2026, 6, 1),
        race_name=race_name, race_date_specific=race_date, source="isora",
        source_url=url, place=place, class_name="IRC Class 1", status="finished",
        raw_data={"boat_name": name, "sail_number": sail},
    )


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT r.id, r.boat_id, r.place, ee.boat_id AS entry_boat, r.race_name, "
            "r.raw_data->>'boat_name' AS boat_name "
            "FROM race_results r JOIN event_entries ee ON ee.id = r.event_entry_id ORDER BY r.id"
        )).fetchall()


def test_rescrape_with_changed_boat_match_updates_existing_row(pg_engine):
    first = _save(pg_engine, 1, "Panther", "GBR 6690R", 12)
    second = _save(pg_engine, 2, "PANTHER", "GBR6690R", 11)
    assert second == first
    rows = _rows(pg_engine)
    assert len(rows) == 1
    assert rows[0].boat_id == 2 and rows[0].entry_boat == 2 and rows[0].place == 11


def test_rescrape_without_boat_match_keeps_existing_link(pg_engine):
    first = _save(pg_engine, 1, "Panther", "GBR6690R", 12)
    assert _save(pg_engine, None, "Panther", "GBR6690R", 12) == first
    rows = _rows(pg_engine)
    assert len(rows) == 1 and rows[0].boat_id == 1 and rows[0].entry_boat == 1


def test_genuinely_new_boat_inserts(pg_engine):
    a = _save(pg_engine, 1, "Panther", "GBR6690R", 1)
    b = _save(pg_engine, 3, "Jezebel", "GBR8911R", 2)
    # Same name, different sail and place: a different boat, not a rename.
    c = _save(pg_engine, None, "Panther", "IRL 99", 3)
    assert len({a, b, c}) == 3
    assert len(_rows(pg_engine)) == 3


def test_different_races_do_not_collide(pg_engine):
    a = _save(pg_engine, 1, "Panther", "GBR6690R", 1, race_name="Race 1")
    b = _save(pg_engine, 1, "Panther", "GBR6690R", 1, race_name="Race 2")
    # Two races on one day distinguished by race date / number only.
    c = _save(pg_engine, 1, "Panther", "GBR6690R", 1, race_name=None,
              race_date=date(2026, 6, 1))
    d = _save(pg_engine, 1, "Panther", "GBR6690R", 1, race_name=None,
              race_date=date(2026, 6, 2))
    # Same event/race name on another results page (e.g. another sub-series).
    e = _save(pg_engine, 1, "Panther", "GBR6690R", 1, url="https://example.org/results/2")
    assert len({a, b, c, d, e}) == 5
    assert len(_rows(pg_engine)) == 5


def test_renamed_boat_on_rescrape_updates(pg_engine):
    first = _save(pg_engine, 3, "MADRUGADA", "GBR942R", 6)
    assert _save(pg_engine, 3, "BULLDOG", "GBR942R", 6) == first
    rows = _rows(pg_engine)
    assert len(rows) == 1 and rows[0].boat_name == "BULLDOG"
