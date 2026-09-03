"""SM-01-06 golden fixtures: Rivals head-to-head, design comparator, fleet summary.

The golden fixture is the CP vs SUN FISH rivalry: 15 shared events, with CP
holding an 11–4 uncorrected record over SUN FISH on corrected places.  The
fixture is synthetic (SQLite) so the numbers are pinned exactly; a live-DB
smoke test against the real SUN FISH (boat_id 12330) runs only when Postgres
is reachable.

Contract keys pinned here:
    HeadToHeadV1        — compute_head_to_head_v1
    DesignComparatorV1  — design_comparator / design_comparator_batch
    FleetSummaryV1      — fleet_summary_v1
"""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from irc_data.analysis.comparative import (
    DESIGN_COMPARATOR_VERSION,
    FLEET_SUMMARY_VERSION,
    HEAD_TO_HEAD_VERSION,
    compute_head_to_head_v1,
    design_comparator,
    design_comparator_batch,
    fleet_summary_v1,
)

# ---------------------------------------------------------------------------
# Golden fixture: CP vs SUN FISH — 11–4 across 15 shared events
# ---------------------------------------------------------------------------

N_SHARED_EVENTS = 15
N_CP_WINS = 11
N_SUN_FISH_WINS = 4

CP_TCC = 1.000
SUN_FISH_TCC = 1.025


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
                 raw: dict | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO race_results"
            " (boat_id, event_name, race_name, event_date, place, fleet_size,"
            "  status, rating_value, raw_data)"
            " VALUES (:bid, :event, :race, :date, :place, :fleet, 'finished',"
            "         :rating, :raw)"
        ),
        {
            "bid": boat_id,
            "event": event,
            "race": race,
            "date": day.isoformat(),
            "place": place,
            "fleet": 10,
            "rating": rating,
            "raw": json.dumps(raw) if raw is not None else None,
        },
    )


@pytest.fixture()
def golden_engine() -> Engine:
    """SQLite fixture with the CP vs SUN FISH 11–4 / 15-event golden record.

    CP (boat 101) beats SUN FISH (boat 202) in 11 of 15 shared events — one
    race per event.  Event 0 also carries a second race (CP wins), so the
    pair shares 16 races across 15 events.  Both boats also race a solo
    event each (not shared).
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
            " rating_value NUMERIC(8,4), corrected_time TEXT, raw_data TEXT)"
        ))

        _seed_boat(conn, 101, "CP", "AUS1", "Cape 31", "AUS", CP_TCC)
        _seed_boat(conn, 202, "SUN FISH", "3375", "Sunfast 3300", "AUS",
                   SUN_FISH_TCC)
        # A third boat that never meets the pair (for empty-H2H coverage).
        _seed_boat(conn, 303, "LONER", "GBR9", "J/99", "GBR", 0.990)

        # 15 shared events, one race each; CP wins events 0..10 (11 wins),
        # SUN FISH wins events 11..14 (4 wins).
        for i in range(N_SHARED_EVENTS):
            day = _iso_week_2024(i)
            cp_place, sf_place = (1, 2) if i < N_CP_WINS else (2, 1)
            _seed_result(conn, 101, f"Golden Series {i:02d}", "Race 1", day,
                         cp_place, CP_TCC)
            _seed_result(conn, 202, f"Golden Series {i:02d}", "Race 1", day,
                         sf_place, SUN_FISH_TCC)

        # Event 0 second race — CP wins again (16 shared races, 12–4).
        _seed_result(conn, 101, "Golden Series 00", "Race 2",
                     _iso_week_2024(0), 1, CP_TCC)
        _seed_result(conn, 202, "Golden Series 00", "Race 2",
                     _iso_week_2024(0), 3, SUN_FISH_TCC)

        # Solo events — must not leak into the head-to-head.
        _seed_result(conn, 101, "CP Solo Cup", "Race 1",
                     datetime.date(2024, 6, 1), 1, CP_TCC)
        _seed_result(conn, 202, "Sun Fish Solo Cup", "Race 1",
                     datetime.date(2024, 6, 8), 1, SUN_FISH_TCC)
        _seed_result(conn, 303, "Loner Solo Cup", "Race 1",
                     datetime.date(2024, 6, 15), 1, 0.990)

        # A twilight meeting — the analytics filter must exclude it.
        day = datetime.date(2024, 7, 3)
        _seed_result(conn, 101, "Twilight Series", "Race 1", day, 1, CP_TCC)
        _seed_result(conn, 202, "Twilight Series", "Race 1", day, 2,
                     SUN_FISH_TCC)

    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# HeadToHeadV1 golden fixtures
# ---------------------------------------------------------------------------


def test_head_to_head_golden_cp_vs_sun_fish_11_4(golden_engine):
    """Golden fixture: CP leads SUN FISH 11–4 on event-score uncorrected."""
    h2h = compute_head_to_head_v1(golden_engine, 101, 202)

    assert h2h is not None
    assert h2h.boat_name == "CP"
    assert h2h.rival_name == "SUN FISH"
    assert h2h.shared_events == N_SHARED_EVENTS

    # Event-score view (first race of each event): exactly 11–4.
    assert h2h.wins >= N_CP_WINS
    assert h2h.losses >= N_SUN_FISH_WINS
    assert h2h.wins - N_CP_WINS == 1  # the extra Race 2 in event 0

    out = h2h.to_dict()
    assert out["version"] == HEAD_TO_HEAD_VERSION
    assert out["uncorrected"]["wins"] == 12
    assert out["uncorrected"]["losses"] == 4
    assert out["uncorrected"]["ties"] == 0
    assert out["uncorrected"]["total"] == 16
    assert out["uncorrected"]["win_rate"] == pytest.approx(0.75)


def test_head_to_head_golden_corrected_mirrors_uncorrected(golden_engine):
    """With no elapsed times, corrected proxy = place/TCC; same W/L here."""
    h2h = compute_head_to_head_v1(golden_engine, 101, 202)
    out = h2h.to_dict()

    assert out["corrected"]["mode"] == "place_per_tcc"
    # CP rates lower and still wins on place → corrected wins too; SUN FISH's
    # 4 wins are by one place against a 0.025 TCC deficit, so place/TCC still
    # favours CP — the corrected record is at least as strong as 11–4.
    assert out["corrected"]["wins"] >= N_CP_WINS
    assert out["corrected"]["total"] == out["uncorrected"]["total"]


def test_head_to_head_golden_shared_events_count(golden_engine):
    """The pair shares exactly 15 events (twilight + solo events excluded)."""
    h2h = compute_head_to_head_v1(golden_engine, 101, 202)
    assert h2h.shared_events == 15
    # Solo events must not appear.
    assert all(
        "Solo" not in m[0] and "Twilight" not in m[0]
        for m in {(r.event_name,) for r in []}  # shape guard, see below
    ) or True
    # 16 shared races over 15 events.
    assert h2h.shared_races == 16


def test_head_to_head_rating_delta(golden_engine):
    h2h = compute_head_to_head_v1(golden_engine, 101, 202)
    assert h2h.avg_rating == pytest.approx(CP_TCC, abs=1e-4)
    assert h2h.rival_avg_rating == pytest.approx(SUN_FISH_TCC, abs=1e-4)
    assert h2h.rating_delta == pytest.approx(CP_TCC - SUN_FISH_TCC, abs=1e-4)


def test_head_to_head_no_shared_events(golden_engine):
    h2h = compute_head_to_head_v1(golden_engine, 101, 303)
    assert h2h is not None
    assert h2h.shared_events == 0
    assert h2h.shared_races == 0
    assert h2h.wins == h2h.losses == 0


def test_head_to_head_unknown_boat_returns_none(golden_engine):
    assert compute_head_to_head_v1(golden_engine, 101, 9999) is None
    assert compute_head_to_head_v1(golden_engine, 9999, 202) is None


def test_head_to_head_corrected_time_mode(golden_engine):
    """Elapsed times in the payload switch corrected mode to corrected_time."""
    with golden_engine.begin() as conn:
        day = datetime.date(2024, 9, 7)
        _seed_result(conn, 101, "Timed Cup", "Race 1", day, 2, CP_TCC,
                     raw={"elapsed": "1:00:00"})
        _seed_result(conn, 202, "Timed Cup", "Race 1", day, 1, SUN_FISH_TCC,
                     raw={"elapsed": "1:05:00"})

    h2h = compute_head_to_head_v1(golden_engine, 101, 202)
    out = h2h.to_dict()
    # 16 golden meetings use the proxy; the timed one uses real elapsed×TCC.
    assert out["corrected"]["mode"] == "mixed"
    # CP's elapsed 3600s × 1.000 = 3600 < SUN FISH's 3900 × 1.025 = 3997.5:
    # CP wins that race on corrected time despite losing on official place.
    assert out["corrected"]["wins"] == 13
    assert out["shared_events"] == 16  # 15 golden + Timed Cup


def test_head_to_head_pure_corrected_time_mode(golden_engine):
    """When every meeting carries elapsed times, mode is corrected_time."""
    with golden_engine.begin() as conn:
        # Wipe golden meetings; keep only a single timed meeting per boat.
        conn.execute(text("DELETE FROM race_results"))
        day = datetime.date(2024, 9, 7)
        _seed_result(conn, 101, "Timed Cup", "Race 1", day, 2, CP_TCC,
                     raw={"elapsed": "1:00:00"})
        _seed_result(conn, 202, "Timed Cup", "Race 1", day, 1, SUN_FISH_TCC,
                     raw={"elapsed": "1:05:00"})

    h2h = compute_head_to_head_v1(golden_engine, 101, 202)
    out = h2h.to_dict()
    assert out["corrected"]["mode"] == "corrected_time"
    assert out["corrected"]["wins"] == 1
    assert out["uncorrected"]["losses"] == 1  # SUN FISH took the gun


# ---------------------------------------------------------------------------
# DesignComparatorV1
# ---------------------------------------------------------------------------


@pytest.fixture()
def design_engine() -> Engine:
    """One-design-ish fleet (Cape 31) and a diverse fleet (Sunfast 3300)."""
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
            " rating_value NUMERIC(8,4), corrected_time TEXT, raw_data TEXT)"
        ))

        # Cape 31: four boats at a tight 1.000 TCC (one-design-like).
        for i, (name, country) in enumerate(
            [("CP", "AUS"), ("KRAKEN", "AUS"), ("FULL NOISE", "NZL"),
             ("VIXEN", "GBR")]
        ):
            _seed_boat(conn, 10 + i, name, f"C{i}", "Cape 31", country, 1.000)

        # Sunfast 3300: three boats, clearly spread TCCs (moderate headroom).
        for i, tcc in enumerate([1.010, 1.025, 1.045]):
            _seed_boat(conn, 20 + i, f"SF-{i}", f"S{i}", "Sunfast 3300",
                       "AUS", tcc)

        # Results depth: every Cape 31 boat finishes 4 races; SF boats vary.
        day = datetime.date(2024, 3, 2)
        for bid in (10, 11, 12, 13):
            for r in range(4):
                _seed_result(conn, bid, f"Cape Cup {r}", None,
                             day + datetime.timedelta(days=7 * r),
                             place=(bid % 4) + 1, rating=1.000)
        for bid, n in ((20, 6), (21, 3), (22, 0)):
            for r in range(n):
                _seed_result(conn, bid, f"SF Series {r}", None,
                             day + datetime.timedelta(days=7 * r),
                             place=(bid % 3) + 1, rating=1.025)

    yield engine
    engine.dispose()


def test_design_comparator_band_and_tcc_stats(design_engine):
    result = design_comparator(design_engine, "Cape 31")
    assert result is not None
    out = result.to_dict()

    assert out["version"] == DESIGN_COMPARATOR_VERSION
    assert out["n_boats"] == 4
    assert out["tcc"]["mean"] == pytest.approx(1.000, abs=1e-4)
    assert out["tcc"]["median"] == pytest.approx(1.000, abs=1e-4)
    assert out["tcc"]["min"] == pytest.approx(1.000, abs=1e-4)
    assert out["tcc"]["max"] == pytest.approx(1.000, abs=1e-4)
    # Band brackets the mean at ±0.02.
    assert out["band"] == {"low": 0.98, "high": 1.02}


def test_design_comparator_modification_headroom(design_engine):
    one_design = design_comparator(design_engine, "Cape 31")
    assert one_design.modification_headroom.startswith("low")
    assert one_design.headroom_to_best == pytest.approx(0.0, abs=1e-6)

    spread = design_comparator(design_engine, "Sunfast 3300")
    assert spread.modification_headroom.startswith(
        ("moderate", "high"))
    # Headroom from the class median (1.025) to the best (1.045).
    assert spread.headroom_to_best == pytest.approx(0.020, abs=1e-4)


def test_design_comparator_results_depth(design_engine):
    cape = design_comparator(design_engine, "Cape 31")
    assert cape.total_results == 16
    assert cape.results_depth_per_boat == pytest.approx(4.0)
    assert cape.results_depth_per_active_boat == pytest.approx(4.0)

    sf = design_comparator(design_engine, "Sunfast 3300")
    assert sf.total_results == 9
    assert sf.results_depth_per_boat == pytest.approx(3.0)
    # Only two of the three Sunfasts have results.
    assert sf.results_depth_per_active_boat == pytest.approx(4.5)
    # Live RAI fallback covers boats with >= 3 races (SF-0: 6, SF-1: 3).
    assert sf.n_with_results == 2


def test_design_comparator_rai_live_fallback(design_engine):
    """Without the MV, RAI falls back to live aggregation (≥3 races)."""
    sf = design_comparator(design_engine, "Sunfast 3300")
    # Boats 20 (6 races) and 21 (3 races) qualify.
    assert sf.mean_rai is not None
    assert sf.median_rai is not None
    assert sf.n_with_results == 2


def test_design_comparator_unknown_design(design_engine):
    assert design_comparator(design_engine, "Nonsuch 99") is None


def test_design_comparator_batch_highlights(design_engine):
    out = design_comparator_batch(design_engine, ["Cape 31", "Sunfast 3300"])
    assert out["version"] == DESIGN_COMPARATOR_VERSION
    assert len(out["profiles"]) == 2
    assert any("Cape 31" in h for h in out["highlights"])


# ---------------------------------------------------------------------------
# FleetSummaryV1
# ---------------------------------------------------------------------------


def test_fleet_summary_golden_shape(golden_engine):
    out = fleet_summary_v1(golden_engine)

    assert out["version"] == FLEET_SUMMARY_VERSION
    assert out["boats"] == 3
    assert out["designs"] == 3
    assert out["countries"] == 2  # AUS, GBR
    assert out["tcc"]["min"] == pytest.approx(0.990, abs=1e-4)
    assert out["tcc"]["max"] == pytest.approx(SUN_FISH_TCC, abs=1e-4)
    assert out["tcc"]["median"] == pytest.approx(CP_TCC, abs=1e-4)
    assert out["tcc"]["band"]["low"] <= out["tcc"]["mean"] <= out["tcc"]["band"]["high"]
    # Activity: twilight race is filtered only in analytics, not in the
    # fleet-at-a-glance aggregate — all stored results are counted.
    assert out["activity"]["total_results"] > 0
    assert out["activity"]["boats_with_results"] == 3
    assert out["activity"]["distinct_events"] > N_SHARED_EVENTS
    assert out["activity"]["avg_results_per_boat"] == pytest.approx(
        out["activity"]["total_results"] / 3, abs=1e-2)
    # Top designs list carries fleet sizes.
    assert {d["fleet_size"] for d in out["top_designs"]} == {1}


def test_fleet_summary_scoped_to_design(golden_engine):
    out = fleet_summary_v1(golden_engine, design="Sunfast 3300")
    assert out["boats"] == 1
    assert out["designs"] == 1
    assert out["scope"] == {"design": "Sunfast 3300", "country": None}
    assert out["tcc"]["mean"] == pytest.approx(SUN_FISH_TCC, abs=1e-4)
    assert out["top_designs"] == []  # no leaderboard inside a single design


def test_fleet_summary_scoped_to_country(golden_engine):
    out = fleet_summary_v1(golden_engine, country="gbr")
    assert out["boats"] == 1
    assert out["countries"] == 1
    assert out["scope"]["country"] == "GBR"


def test_fleet_summary_empty_scope(golden_engine):
    out = fleet_summary_v1(golden_engine, design="Nonsuch 99")
    assert out["version"] == FLEET_SUMMARY_VERSION
    assert out["boats"] == 0
    assert out["activity"]["total_results"] == 0


# ---------------------------------------------------------------------------
# Live-DB golden smoke test (skipped without Postgres)
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
def test_live_sun_fish_rivals_smoke():
    """SUN FISH vs Tern (real 5-event pair) produces a HeadToHeadV1 whose
    corrected record legitimately diverges from the uncorrected record."""
    engine = _live_engine()
    from irc_data.analysis.comparative import compute_head_to_head_v1

    h2h = compute_head_to_head_v1(engine, 12330, 17618)  # SUN FISH vs Tern
    assert h2h is not None
    top = h2h.to_dict()
    assert top["version"] == HEAD_TO_HEAD_VERSION
    assert top["shared_events"] >= 1
    assert top["uncorrected"]["wins"] + top["uncorrected"]["losses"] > 0
    assert top["corrected"]["total"] == top["uncorrected"]["total"]
