"""SM-01-07 golden fixtures: Race-prep brief inputs (RacePrepFactsV1).

The golden fixture is the upcoming "Golden Regatta 2025" (event id 7) with
four entries — the focal boat CP, its golden rival SUN FISH (15 shared
events, CP leads 11–4), LONER (one shared race — below the rival threshold),
and NEWCOMER (no racing history at all).  The fixture is synthetic (SQLite)
so every fact is pinned exactly; a live-DB smoke test runs only when
Postgres is reachable.

Contract keys pinned here:
    RacePrepFactsV1 — race_prep_facts
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.analysis.race_prep import (
    DEFAULT_MIN_RIVAL_MEETINGS,
    MIN_SPLIT_RACES,
    RACE_PREP_FACTS_VERSION,
    race_prep_facts,
)

# ---------------------------------------------------------------------------
# Golden fixture: Golden Regatta 2025, focal boat CP
# ---------------------------------------------------------------------------

EVENT_ID = 7
EVENT_NAME = "Golden Regatta 2025"
EVENT_START = datetime.date(2025, 6, 7)
AS_OF = datetime.date(2025, 5, 28)  # 10 days before the start

CP_ID = 101
SUN_FISH_ID = 202
LONER_ID = 303
NEWCOMER_ID = 404
DABBLE_ID = 505

CP_TCC = 1.000
SUN_FISH_TCC = 1.025
LONER_TCC = 0.990
NEWCOMER_TCC = 1.010
DABBLE_TCC = 1.015

N_SHARED_EVENTS = 15
N_CP_WINS = 11
N_SUN_FISH_WINS = 4

# Pinned condition-fit values (see fixture derivation notes below):
# short course — CP is lowest rated and wins: RAI +90 per race
SHORT_RAI = 90.0
# long course — CP is highest rated (expected 1/3) and finishes last of 3:
# RAI = (1/3 - 1.0) x 100 per race
LONG_RAI = (1 / 3 - 1.0) * 100
# field-strength races — expected falls back to 0.5 (no rated opposition)
FIELD_STRONG_RAI = 10.0   # place 2 of 5: (0.5 - 0.4) x 100
FIELD_WEAK_RAI = -30.0    # place 4 of 5: (0.5 - 0.8) x 100
# past editions of the regatta — CP is the only rated starter
EDITION_RAI = 20.0        # place 3 of 10


def _iso_week_2024(index: int) -> datetime.date:
    """``index`` distinct Saturdays in 2024 (Jan 6 2024 was a Saturday)."""
    return datetime.date(2024, 1, 6) + datetime.timedelta(days=7 * index)


def _seed_boat(conn, boat_id: int, name: str, sail: str, design: str,
               country: str, tcc: float) -> None:
    conn.execute(
        text(
            "INSERT INTO boats (id, boat_name, sail_number, design, country)"
            " VALUES (:id, :name, :sail, :design, :country)"
        ),
        {"id": boat_id, "name": name, "sail": sail, "design": design,
         "country": country},
    )
    conn.execute(
        text(
            "INSERT INTO tcc_snapshots (boat_id, snapshot_date, tcc)"
            " VALUES (:id, :date, :tcc)"
        ),
        {"id": boat_id, "date": "2024-01-01", "tcc": tcc},
    )


def _seed_result(conn, boat_id: int, event: str, race: str | None,
                 day: datetime.date, place: int, rating: float,
                 fleet: int = 10, distance: float | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO race_results"
            " (boat_id, event_name, race_name, event_date, place, fleet_size,"
            "  status, rating_value, course_distance_nm)"
            " VALUES (:bid, :event, :race, :date, :place, :fleet, 'finished',"
            "         :rating, :distance)"
        ),
        {
            "bid": boat_id,
            "event": event,
            "race": race,
            "date": day.isoformat(),
            "place": place,
            "fleet": fleet,
            "rating": rating,
            "distance": distance,
        },
    )


def _seed_entry(conn, entry_id: int, event_id: int, boat_id: int | None,
                name: str, sail: str, tcc: float, design: str) -> None:
    conn.execute(
        text(
            "INSERT INTO event_entries"
            " (id, event_id, boat_id, sail_number, boat_name, tcc, design)"
            " VALUES (:id, :event, :boat, :sail, :name, :tcc, :design)"
        ),
        {"id": entry_id, "event": event_id, "boat": boat_id, "sail": sail,
         "name": name, "tcc": tcc, "design": design},
    )


@pytest.fixture()
def golden_engine() -> Engine:
    """SQLite fixture for the Golden Regatta 2025 race-prep brief.

    Entries: CP (focal), SUN FISH (golden rival), LONER (below the rival
    threshold), NEWCOMER (no history).  CP's race history is seeded so the
    condition-fit RAI splits pin exactly (see module constants).
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT,"
            " sail_number TEXT, design TEXT, design_canonical TEXT,"
            " country TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE tcc_snapshots (id INTEGER PRIMARY KEY,"
            " boat_id INTEGER, snapshot_date DATE, tcc NUMERIC(6,4))"
        ))
        conn.execute(text(
            "CREATE TABLE race_results (id INTEGER PRIMARY KEY,"
            " boat_id INTEGER, event_name TEXT, race_name TEXT,"
            " event_date DATE, race_number INTEGER, place INTEGER,"
            " fleet_size INTEGER, status TEXT DEFAULT 'finished',"
            " rating_value NUMERIC(8,4), course_distance_nm NUMERIC(8,2),"
            " corrected_time TEXT, raw_data TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT,"
            " start_date DATE, end_date DATE, venue TEXT, course_type TEXT,"
            " organiser TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE event_entries (id INTEGER PRIMARY KEY,"
            " event_id INTEGER, boat_id INTEGER, sail_number TEXT,"
            " boat_name TEXT, tcc NUMERIC(5,3), design TEXT)"
        ))

        # -- boats + current ratings --------------------------------------
        _seed_boat(conn, CP_ID, "CP", "AUS1", "Cape 31", "AUS", CP_TCC)
        _seed_boat(conn, SUN_FISH_ID, "SUN FISH", "3375", "Sunfast 3300",
                   "AUS", SUN_FISH_TCC)
        _seed_boat(conn, LONER_ID, "LONER", "GBR9", "J/99", "GBR", LONER_TCC)
        _seed_boat(conn, NEWCOMER_ID, "NEWCOMER", "FRA5", "J/99", "FRA",
                   NEWCOMER_TCC)
        # DABBLE is only used to keep LONER's race history below the
        # condition-fit minimum-N threshold (never enters, never meets CP).
        _seed_boat(conn, DABBLE_ID, "DABBLE", "GBR10", "J/99", "GBR",
                   DABBLE_TCC)

        # -- the upcoming event + its entry list --------------------------
        conn.execute(
            text(
                "INSERT INTO events (id, name, start_date, end_date, venue,"
                " course_type, organiser)"
                " VALUES (:id, :name, :start, :end, :venue, :course, :org)"
            ),
            {"id": EVENT_ID, "name": EVENT_NAME, "start": EVENT_START.isoformat(),
             "end": "2025-06-08", "venue": "Golden Bay", "course": "inshore",
             "org": "Golden Yacht Club"},
        )
        _seed_entry(conn, 1, EVENT_ID, CP_ID, "CP", "AUS1", CP_TCC, "Cape 31")
        _seed_entry(conn, 2, EVENT_ID, SUN_FISH_ID, "SUN FISH", "3375",
                    SUN_FISH_TCC, "Sunfast 3300")
        _seed_entry(conn, 3, EVENT_ID, LONER_ID, "LONER", "GBR9", LONER_TCC,
                    "J/99")
        _seed_entry(conn, 4, EVENT_ID, NEWCOMER_ID, "NEWCOMER", "FRA5",
                    NEWCOMER_TCC, "J/99")

        # -- rival history: CP vs SUN FISH, 15 events, 11–4 ----------------
        # DABBLE (unrated) pads the field but never counts for expected
        # finish percentiles or field-strength means.
        for i in range(N_SHARED_EVENTS):
            day = _iso_week_2024(i)
            cp_place, sf_place = (1, 2) if i < N_CP_WINS else (2, 1)
            event = f"Golden Series {i:02d}"
            _seed_result(conn, CP_ID, event, "Race 1", day,
                         cp_place, CP_TCC, fleet=4)
            _seed_result(conn, SUN_FISH_ID, event, "Race 1",
                         day, sf_place, SUN_FISH_TCC, fleet=4)
            _seed_result(conn, DABBLE_ID, event, "Race 1", day, 3, None,
                         fleet=4)
            _seed_result(conn, DABBLE_ID, event, "Race 1", day, 4, None,
                         fleet=4)

        # LONER shares exactly one race with CP — below min_meetings.
        _seed_result(conn, CP_ID, "One-Off Cup", "Race 1",
                     datetime.date(2024, 9, 7), 1, CP_TCC, fleet=10)
        _seed_result(conn, LONER_ID, "One-Off Cup", "Race 1",
                     datetime.date(2024, 9, 7), 5, LONER_TCC, fleet=10)
        # LONER's other results are thin on purpose: 2 races, both below the
        # condition-fit minimum-N threshold in every split.
        for i in range(2):
            day = datetime.date(2024, 10, 5) + datetime.timedelta(days=7 * i)
            event = f"Loner Solo {i}"
            _seed_result(conn, LONER_ID, event, "Race 1", day, 1, LONER_TCC,
                         fleet=5)
            _seed_result(conn, DABBLE_ID, event, "Race 1", day, 2, None,
                         fleet=5)

        # -- course-summary history: three past editions, distances 6/8/10 --
        # CP is the only rated starter -> expected falls back to 0.5,
        # place 3 of 10 pins RAI at +20 per edition.
        for year, dist in ((2022, 6.0), (2023, 8.0), (2024, 10.0)):
            _seed_result(conn, CP_ID, EVENT_NAME, "Race 1",
                         datetime.date(year, 6, 3), 3, CP_TCC, fleet=10,
                         distance=dist)

        # -- condition-fit history ------------------------------------------
        # Short course (<10nm): CP lowest rated in a 3-boat field, wins.
        #   expected = 3/3 = 1.0, actual = 1/10 -> RAI = +90 per race.
        for i in range(MIN_SPLIT_RACES):
            day = datetime.date(2024, 3, 2) + datetime.timedelta(days=7 * i)
            event = f"Short Bash {i}"
            _seed_result(conn, CP_ID, event, "Race 1", day, 1, CP_TCC,
                         fleet=10, distance=8.0)
            _seed_result(conn, SUN_FISH_ID, event, "Race 1", day, 2,
                         SUN_FISH_TCC, fleet=10, distance=8.0)
            _seed_result(conn, DABBLE_ID, event, "Race 1", day, 3, 1.020,
                         fleet=10, distance=8.0)

        # Long course (>30nm): CP highest rated in a 3-boat rated field,
        # finishes last of the 10-boat fleet.
        #   expected = 1/3, actual = 10/10 -> RAI = (1/3 - 1) x 100 per race.
        for i in range(MIN_SPLIT_RACES):
            day = datetime.date(2024, 4, 6) + datetime.timedelta(days=7 * i)
            event = f"Offshore Miler {i}"
            _seed_result(conn, CP_ID, event, "Race 1", day, 10, CP_TCC,
                         fleet=10, distance=40.0)
            _seed_result(conn, SUN_FISH_ID, event, "Race 1", day, 1, 0.950,
                         fleet=10, distance=40.0)
            _seed_result(conn, DABBLE_ID, event, "Race 1", day, 2, 0.970,
                         fleet=10, distance=40.0)

        # Field strength: CP rated above the rest of the field (stronger)
        # and below it (weaker).  Single rated starter -> expected falls
        # back to 0.5, so RAI pins to (0.5 - place/fleet) x 100.
        for i in range(MIN_SPLIT_RACES):
            day = datetime.date(2024, 5, 4) + datetime.timedelta(days=7 * i)
            event = f"Hotshots Meeting {i}"
            _seed_result(conn, CP_ID, event, "Race 1", day, 2, CP_TCC,
                         fleet=5)
            for j in range(4):
                _seed_result(conn, DABBLE_ID, event, "Race 1", day,
                             j + 1, None, fleet=5)
        for i in range(MIN_SPLIT_RACES):
            day = datetime.date(2024, 6, 1) + datetime.timedelta(days=7 * i)
            event = f"Open Handicap {i}"
            _seed_result(conn, CP_ID, event, "Race 1", day, 4, CP_TCC,
                         fleet=5)
            for j in range(4):
                _seed_result(conn, DABBLE_ID, event, "Race 1", day,
                             j + 1, None, fleet=5)

    yield engine
    engine.dispose()


def _golden_facts(engine: Engine) -> dict:
    facts = race_prep_facts(engine, EVENT_ID, CP_ID, as_of=AS_OF)
    assert facts is not None
    return facts


# ---------------------------------------------------------------------------
# Golden fixtures — top-level shape
# ---------------------------------------------------------------------------


def test_race_prep_golden_version_and_shape(golden_engine):
    facts = _golden_facts(golden_engine)

    assert facts["version"] == RACE_PREP_FACTS_VERSION
    assert set(facts) == {
        "version", "event", "focal_boat", "fleet", "rivals",
        "course", "forecast", "condition_fit",
    }


def test_race_prep_golden_event_facts(golden_engine):
    facts = _golden_facts(golden_engine)
    event = facts["event"]

    assert event["event_id"] == EVENT_ID
    assert event["name"] == EVENT_NAME
    assert event["start_date"] == EVENT_START.isoformat()
    assert event["venue"] == "Golden Bay"
    assert event["organiser"] == "Golden Yacht Club"
    assert event["days_until_start"] == 10
    assert event["is_upcoming"] is True


def test_race_prep_golden_focal_boat(golden_engine):
    facts = _golden_facts(golden_engine)
    boat = facts["focal_boat"]

    assert boat["boat_id"] == CP_ID
    assert boat["boat_name"] == "CP"
    assert boat["design"] == "Cape 31"
    assert boat["tcc"] == pytest.approx(CP_TCC)
    assert boat["entered"] is True


def test_race_prep_golden_fleet_size_and_tcc(golden_engine):
    facts = _golden_facts(golden_engine)
    fleet = facts["fleet"]

    assert fleet["size"] == 4
    assert fleet["matched_boats"] == 4
    tcc = fleet["tcc"]
    assert tcc["mean"] == pytest.approx(
        (CP_TCC + SUN_FISH_TCC + LONER_TCC + NEWCOMER_TCC) / 4, abs=1e-4
    )
    assert tcc["min"] == pytest.approx(LONER_TCC)
    assert tcc["max"] == pytest.approx(SUN_FISH_TCC)
    assert tcc["spread"] == pytest.approx(SUN_FISH_TCC - LONER_TCC, abs=1e-4)
    assert fleet["distinct_designs"] == 3
    designs = {d["design"]: d["entries"] for d in fleet["designs"]}
    assert designs == {"Cape 31": 1, "Sunfast 3300": 1, "J/99": 2}
    assert fleet["countries"] == {"AUS": 2, "GBR": 1, "FRA": 1}


# ---------------------------------------------------------------------------
# Golden fixtures — rivals and rating deltas
# ---------------------------------------------------------------------------


def test_race_prep_golden_rivals_entered(golden_engine):
    """Of the entries, only SUN FISH clears the shared-race rival bar."""
    facts = _golden_facts(golden_engine)
    rivals = facts["rivals"]

    assert [r["boat_id"] for r in rivals] == [SUN_FISH_ID]
    rival = rivals[0]
    assert rival["boat_name"] == "SUN FISH"
    assert rival["tcc"] == pytest.approx(SUN_FISH_TCC)
    # Positive delta: SUN FISH rates higher (faster) than CP.
    assert rival["rating_delta"] == pytest.approx(SUN_FISH_TCC - CP_TCC)

    # The embedded record is the full SM-01-06 HeadToHeadV1, covering every
    # shared race (the 15 golden series plus the short/long fixtures).
    h2h = rival["head_to_head"]
    assert h2h["version"] == "HeadToHeadV1"
    assert h2h["shared_events"] == 21
    assert h2h["uncorrected"]["wins"] == N_CP_WINS + 3
    assert h2h["uncorrected"]["losses"] == N_SUN_FISH_WINS + 3


def test_race_prep_rival_h2h_matches_sm_01_06_engine(golden_engine):
    """The embedded record is exactly what compute_head_to_head_v1 emits."""
    from irc_data.analysis.comparative import compute_head_to_head_v1

    facts = _golden_facts(golden_engine)
    rival = facts["rivals"][0]
    direct = compute_head_to_head_v1(golden_engine, CP_ID, SUN_FISH_ID)
    assert rival["head_to_head"] == direct.to_dict()


def test_race_prep_min_meetings_threshold(golden_engine):
    """At min_meetings=1 LONER (one shared race) becomes a rival too."""
    facts = race_prep_facts(golden_engine, EVENT_ID, CP_ID,
                            as_of=AS_OF, min_meetings=1)
    rival_ids = {r["boat_id"] for r in facts["rivals"]}
    assert rival_ids == {SUN_FISH_ID, LONER_ID}
    # NEWCOMER has no shared races — never a rival regardless of threshold.
    assert NEWCOMER_ID not in rival_ids

    loner = next(r for r in facts["rivals"] if r["boat_id"] == LONER_ID)
    assert loner["rating_delta"] == pytest.approx(LONER_TCC - CP_TCC)


def test_race_prep_rival_delta_falls_back_to_entry_tcc(golden_engine):
    """A focal boat with no snapshots still yields deltas via entry TCC."""
    with golden_engine.begin() as conn:
        conn.execute(text("DELETE FROM tcc_snapshots WHERE boat_id = :id"),
                     {"id": CP_ID})
    facts = _golden_facts(golden_engine)
    rival = facts["rivals"][0]
    assert rival["rating_delta"] == pytest.approx(SUN_FISH_TCC - CP_TCC)
    assert facts["focal_boat"]["tcc"] == pytest.approx(CP_TCC)


# ---------------------------------------------------------------------------
# Golden fixtures — course summary and forecast seam
# ---------------------------------------------------------------------------


def test_race_prep_golden_course_summary(golden_engine):
    facts = _golden_facts(golden_engine)
    course = facts["course"]

    assert course["course_type"] == "inshore"
    assert course["historical_editions"] == 3
    assert course["historical_races_with_distance"] == 3
    dist = course["distance_nm"]
    assert dist["mean"] == pytest.approx(8.0)
    assert dist["min"] == pytest.approx(6.0)
    assert dist["max"] == pytest.approx(10.0)


def test_race_prep_forecast_provider_pending_by_default(golden_engine):
    """Provider decision pending -> structured placeholder, never prose."""
    facts = _golden_facts(golden_engine)
    forecast = facts["forecast"]

    assert forecast["status"] == "provider_pending"
    assert forecast["provider"] is None
    assert forecast["summary"] is None


def test_race_prep_forecast_provider_injected(golden_engine):
    """The ingestion seam accepts a provider without changing the shape."""
    summary = {
        "wind_speed_kt": {"mean": 12.0, "min": 8.0, "max": 16.0},
        "wind_direction_deg": 45.0,
    }

    def fake_provider(event_facts: dict) -> dict:
        assert event_facts["event_id"] == EVENT_ID
        return summary

    fake_provider.provider_name = "fixture-met"

    facts = race_prep_facts(golden_engine, EVENT_ID, CP_ID, as_of=AS_OF,
                            forecast_provider=fake_provider)
    forecast = facts["forecast"]
    assert forecast["status"] == "ok"
    assert forecast["provider"] == "fixture-met"
    assert forecast["summary"] == summary


def test_race_prep_forecast_provider_empty(golden_engine):
    def empty_provider(event_facts: dict):
        return None

    facts = race_prep_facts(golden_engine, EVENT_ID, CP_ID, as_of=AS_OF,
                            forecast_provider=empty_provider)
    assert facts["forecast"]["status"] == "unavailable"
    assert facts["forecast"]["summary"] is None


# ---------------------------------------------------------------------------
# Golden fixtures — condition fit from RAI splits
# ---------------------------------------------------------------------------


def test_race_prep_golden_condition_fit_overall(golden_engine):
    facts = _golden_facts(golden_engine)
    fit = facts["condition_fit"]

    # 15 series + 1 one-off + 3 editions + 3 short + 3 long + 3 + 3 = 31.
    assert fit["n_races"] == 31
    assert fit["min_split_races"] == MIN_SPLIT_RACES
    # (11 x 75) + (4 x 50) + 40 + (3 x 20) + (3 x 90) + (3 x -66.67)
    #   + (3 x 10) + (3 x -30) = 1135 -> 36.61
    assert fit["overall_rai"] == pytest.approx(
        (11 * 75.0 + 4 * 50.0 + 40.0 + 3 * EDITION_RAI + 3 * SHORT_RAI
         + 3 * LONG_RAI + 3 * FIELD_STRONG_RAI + 3 * FIELD_WEAK_RAI) / 31,
        abs=0.01,
    )


def test_race_prep_golden_condition_fit_distance_split(golden_engine):
    facts = _golden_facts(golden_engine)
    split = facts["condition_fit"]["splits"]["course_distance"]
    buckets = split["buckets"]

    # 6/8/10nm editions + 8nm sprints all sit under the 10.5nm short-course
    # cut-line; the 40nm milers clear the 30.5nm long-course line.
    assert buckets["short_course"]["n_races"] == 3 + 3  # sprints + editions
    assert buckets["short_course"]["rai"] == pytest.approx(
        (3 * SHORT_RAI + 3 * EDITION_RAI) / 6, abs=0.01
    )
    assert buckets["short_course"]["meaningful"] is True

    assert buckets["medium_course"]["n_races"] == 0
    assert buckets["medium_course"]["rai"] is None

    assert buckets["long_course"]["n_races"] == 3
    assert buckets["long_course"]["rai"] == pytest.approx(LONG_RAI, abs=0.01)

    # Series / one-off / field-strength races carry no distance.
    assert split["unclassified_races"] == 22


def test_race_prep_golden_condition_fit_field_strength_split(golden_engine):
    facts = _golden_facts(golden_engine)
    split = facts["condition_fit"]["splits"]["field_strength"]
    buckets = split["buckets"]

    # Series races (SUN FISH rates higher than CP) + short-course sprints.
    assert buckets["stronger_field"]["n_races"] == 15 + 3
    assert buckets["stronger_field"]["rai"] == pytest.approx(
        (11 * 75.0 + 4 * 50.0 + 3 * SHORT_RAI) / 18, abs=0.01
    )
    # Long-course milers (CP top rated) plus the one-off cup.
    assert buckets["weaker_field"]["n_races"] == 3 + 1
    assert buckets["weaker_field"]["rai"] == pytest.approx(
        (3 * LONG_RAI + 40.0) / 4, abs=0.01
    )
    # Editions / Hotshots / Open Handicap: no rated opposition.
    assert split["unclassified_races"] == 9


def test_race_prep_golden_condition_fit_signal(golden_engine):
    """The structured signal prefers short-course racing (largest RAI gap)."""
    facts = _golden_facts(golden_engine)
    signal = facts["condition_fit"]["signal"]

    assert signal["status"] == "ok"
    assert signal["family"] == "course_distance"
    assert signal["preferred_bucket"] == "short_course"
    assert signal["other_bucket"] == "long_course"
    short_rai = (3 * SHORT_RAI + 3 * EDITION_RAI) / 6
    assert signal["rai_delta"] == pytest.approx(short_rai - LONG_RAI, abs=0.01)
    assert signal["strength"] == "strong"


def test_race_prep_condition_fit_insufficient_data(golden_engine):
    """Below minimum-N everywhere the signal degrades, never fabricates."""
    facts = race_prep_facts(golden_engine, EVENT_ID, LONER_ID, as_of=AS_OF)
    fit = facts["condition_fit"]
    signal = fit["signal"]

    assert signal["status"] == "insufficient_data"
    assert signal["strength"] == "none"
    assert signal["family"] is None
    assert fit["n_races"] == 3
    # LONER's split buckets are still reported with their (thin) numbers.
    buckets = fit["splits"]["fleet_size"]["buckets"]
    total = sum(b["n_races"] for b in buckets.values())
    assert total == fit["n_races"]
    assert all(not b["meaningful"] for b in buckets.values())


def test_race_prep_no_history_condition_fit_and_rivals(golden_engine):
    """NEWCOMER: no races -> empty condition fit, empty rivals, still facts."""
    facts = race_prep_facts(golden_engine, EVENT_ID, NEWCOMER_ID, as_of=AS_OF)

    assert facts is not None
    assert facts["rivals"] == []
    fit = facts["condition_fit"]
    assert fit["n_races"] == 0
    assert fit["overall_rai"] is None
    assert fit["signal"]["status"] == "insufficient_data"
    # Fleet/course/forecast sections are independent of the focal history.
    assert facts["fleet"]["size"] == 4
    assert facts["course"]["historical_editions"] == 3


# ---------------------------------------------------------------------------
# Not-found behaviour
# ---------------------------------------------------------------------------


def test_race_prep_unknown_event_returns_none(golden_engine):
    assert race_prep_facts(golden_engine, 9999, CP_ID, as_of=AS_OF) is None


def test_race_prep_unknown_boat_returns_none(golden_engine):
    assert race_prep_facts(golden_engine, EVENT_ID, 9999, as_of=AS_OF) is None


def test_race_prep_event_without_entries(golden_engine):
    """An upcoming event with no entries: empty fleet, no rivals."""
    with golden_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO events (id, name, start_date, venue)"
                " VALUES (99, 'Empty Regatta 2025', '2025-07-01', 'Nowhere')"
            )
        )
    facts = race_prep_facts(golden_engine, 99, CP_ID, as_of=AS_OF)

    assert facts is not None
    assert facts["fleet"]["size"] == 0
    assert facts["fleet"]["tcc"]["mean"] is None
    assert facts["rivals"] == []
    assert facts["course"]["historical_editions"] == 0
    assert facts["focal_boat"]["entered"] is False


def test_race_prep_default_min_meetings_matches_sm_01_06():
    assert DEFAULT_MIN_RIVAL_MEETINGS == 2


# ---------------------------------------------------------------------------
# Live-DB smoke test (skipped without Postgres)
# ---------------------------------------------------------------------------


def _live_engine() -> Engine | None:
    try:
        from irc_data.db.connection import get_engine

        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


@pytest.mark.skipif(_live_engine() is None, reason="live Postgres unavailable")
def test_live_race_prep_smoke():
    """Any live event with entries produces a well-formed RacePrepFactsV1."""
    engine = _live_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT e.id, ee.boat_id FROM events e"
            " JOIN event_entries ee ON ee.event_id = e.id"
            " WHERE ee.boat_id IS NOT NULL"
            " ORDER BY e.id DESC LIMIT 1"
        )).first()
    if row is None:
        pytest.skip("no live events with matched entries")

    facts = race_prep_facts(engine, row.id, row.boat_id)
    assert facts is not None
    assert facts["version"] == RACE_PREP_FACTS_VERSION
    assert facts["fleet"]["size"] >= 1
    assert facts["forecast"]["status"] in {
        "provider_pending", "ok", "unavailable",
    }
    assert facts["condition_fit"]["signal"]["status"] in {
        "ok", "insufficient_data",
    }
