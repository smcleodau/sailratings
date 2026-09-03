#!/usr/bin/env python3
"""End-to-end verification evidence for SM-01-03 — Racing Advantage Index
(RAI) with confidence intervals.

Walks the acceptance criteria through the shipped code
(``irc_data.analysis.rai``, contract ``RAIComputationV1``) and prints hard,
paste-able PASS/FAIL evidence for the issue board:

  1. **RAI + CI per boat from corrected results** — the golden HELD vs
     CHASER fixture (HELD, TCC 1.000, wins all 8 races) reproduces the
     pinned advantage of 0.0 with a zero-width CI; a varied series yields a
     bootstrap-t CI that brackets the mean.
  2. **Class mean RAI** — the J/99 baseline aggregates exactly the
     threshold-passing boats (mean of members), with under-threshold boats
     counted but excluded.
  3. **Condition splits by TWS band** — HELD's 4 light (6 kn) + 4 fresh
     (16 kn) races split into the pinned bands; bands without wind data (or
     below the band threshold) report ``insufficient_data``.
  4. **Minimum-race threshold enforced** — 4 scored races ⇒
     ``status="insufficient_data"``, ``rai=None``; exactly 5 ⇒ ``ok``.
  5. **Reproducible per dataset version** — a re-run is bit-identical
     (including the serialised contract); the dataset fingerprint changes
     iff a row is added or mutated; the config fingerprint changes iff the
     ruleset changes.
  6. **Sensitivity to identity-merge errors** — keying CHASER's second
     places to HELD's identity moves HELD's RAI by the pinned −25.0 and
     changes the dataset fingerprint; splitting the merge restores both.

No database or network required — the fixture is in-memory SQLite, matching
the existing SM-01-06 golden-fixture harness style.

Usage::

    PYTHONPATH=src python3 scripts/verify_sm_01_03.py
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from irc_data.analysis.rai import (  # noqa: E402
    DEFAULT_CONFIG,
    RAI_SCHEMA_VERSION,
    BoatInfo,
    RaceObservation,
    RAIRulesetConfigV1,
    class_baseline_v1,
    compute_rai_from_observations,
    compute_rai_v1,
    dataset_fingerprint,
)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")


# ---------------------------------------------------------------------------
# Golden fixture: HELD (TCC 1.000) beats CHASER (TCC 1.050) in 8/8 races
# ---------------------------------------------------------------------------

N_RACES = 8
HELD_ID = 301
CHASER_ID = 302
HELD_TCC = 1.000
CHASER_TCC = 1.050


def race_day(index: int) -> datetime.date:
    return datetime.date(2024, 1, 6) + datetime.timedelta(days=7 * index)


def seed_boat(conn, boat_id: int, name: str, sail: str, design: str) -> None:
    conn.execute(
        text(
            "INSERT INTO boats (id, boat_name, sail_number, design)"
            " VALUES (:id, :name, :sail, :design)"
        ),
        {"id": boat_id, "name": name, "sail": sail, "design": design},
    )


def seed_result(conn, boat_id, event, race, day, place, fleet, rating,
                raw=None, status="finished") -> None:
    conn.execute(
        text(
            "INSERT INTO race_results"
            " (boat_id, event_name, race_name, event_date, place, fleet_size,"
            "  status, rating_value, raw_data)"
            " VALUES (:bid, :event, :race, :date, :place, :fleet, :status,"
            "         :rating, :raw)"
        ),
        {
            "bid": boat_id, "event": event, "race": race,
            "date": day.isoformat() if day else None, "place": place,
            "fleet": fleet, "status": status, "rating": rating,
            "raw": json.dumps(raw) if raw is not None else None,
        },
    )


def seed_golden_series(conn) -> None:
    for i in range(N_RACES):
        day = race_day(i)
        tws = 6.0 if i < 4 else 16.0
        seed_result(conn, HELD_ID, f"Event{i}", None, day, 1, 2, HELD_TCC,
                    raw={"tws": tws})
        seed_result(conn, CHASER_ID, f"Event{i}", None, day, 2, 2, CHASER_TCC)


def build_engine() :
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT,"
            " sail_number TEXT, design TEXT, design_canonical TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE race_results (id INTEGER PRIMARY KEY,"
            " boat_id INTEGER, event_name TEXT, race_name TEXT,"
            " event_date DATE, race_number INTEGER, place INTEGER,"
            " fleet_size INTEGER, status TEXT DEFAULT 'finished',"
            " rating_value NUMERIC(8,4), corrected_time TEXT, raw_data TEXT)"
        ))
        seed_boat(conn, HELD_ID, "HELD", "GBR101", "J/99")
        seed_boat(conn, CHASER_ID, "CHASER", "GBR202", "J/99")
        seed_golden_series(conn)
    return engine


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    engine = build_engine()

    print("\n== 1. RAI with confidence interval per boat (corrected results) ==")
    held = compute_rai_v1(engine, HELD_ID)
    check("schema is RAIComputationV1", held.schema == RAI_SCHEMA_VERSION,
          held.schema)
    check("status ok / meets threshold", held.status == "ok" and held.meets_min_races)
    check("8 races all scored", held.n_races == N_RACES and held.n_scored == N_RACES)
    # HELD (lowest TCC ⇒ expected rank 1/2 = 0.5) wins every race (actual
    # 1/2 = 0.5) ⇒ every advantage observation is exactly 0.0.
    check("golden HELD RAI == 0.0", approx(held.rai, 0.0), f"rai={held.rai}")
    check("zero-width CI at the point estimate",
          approx(held.ci_lower, 0.0) and approx(held.ci_upper, 0.0),
          f"ci=[{held.ci_lower}, {held.ci_upper}] method={held.ci_method}")
    check("wins/podiums pinned", held.wins == N_RACES and held.podiums == N_RACES)

    chaser = compute_rai_v1(engine, CHASER_ID)
    # CHASER (highest TCC ⇒ expected rank 2/2 = 1.0) finishes 2nd every race
    # (actual 1.0) ⇒ she sails exactly *to* her rating: RAI 0, not negative.
    check("golden CHASER sails to her rating (RAI == 0.0)",
          approx(chaser.rai, 0.0), f"rai={chaser.rai}")

    # Non-degenerate CI: boat at rank 3/5 (expected 0.6) with places 1..5.
    varied_obs = [
        RaceObservation(boat_id=1, event_name="E", race_name=None,
                        event_date="2024-01-06", place=p, fleet_size=5,
                        rating_value=1.010)
        for p in (1, 2, 3, 4, 5)
    ]
    varied_fields = {("E", None, "2024-01-06"): (0.990, 1.000, 1.010, 1.020, 1.030)}
    varied = compute_rai_from_observations(varied_obs, varied_fields,
                                           info=BoatInfo(boat_id=1))
    check("varied series: bootstrap-t CI brackets the mean",
          varied.ci_method == "bootstrap-t"
          and varied.ci_lower < varied.rai < varied.ci_upper,
          f"ci=[{varied.ci_lower:.2f}, {varied.ci_upper:.2f}] mean={varied.rai:.2f}")
    check("per-race contributions preserved",
          len(held.race_contributions) == N_RACES
          and all(c["advantage"] == 0.0 for c in held.race_contributions))

    print("\n== 2. Class mean RAI ==")
    baseline = class_baseline_v1(engine, "J/99")
    check("baseline aggregates both qualifying boats", baseline.n_boats == 2,
          f"n_boats={baseline.n_boats} total={baseline.n_boats_total}")
    check("class mean == mean of members (0.0)", approx(baseline.mean_rai, 0.0),
          f"mean={baseline.mean_rai} median={baseline.median_rai}")

    with engine.begin() as conn:
        seed_boat(conn, 303, "NEWCOMER", "GBR303", "J/99")
        seed_result(conn, 303, "Solo", None, race_day(0), 1, 5, 1.010)
    baseline2 = class_baseline_v1(engine, "J/99")
    check("under-threshold boat counted but excluded from the mean",
          baseline2.n_boats == 2 and baseline2.n_boats_total == 3
          and approx(baseline2.mean_rai, 0.0),
          f"n_boats={baseline2.n_boats} total={baseline2.n_boats_total}")

    print("\n== 3. Condition splits by TWS band ==")
    splits = {s.band: s for s in held.condition_splits}
    check("wind observed on all 8 HELD races", held.n_wind_observed == N_RACES)
    check("light band: 4 races @ 6 kn, RAI 0.0, ok",
          splits["light"].status == "ok" and splits["light"].n_races == 4
          and approx(splits["light"].rai, 0.0),
          f"light={splits['light'].to_dict()}")
    check("fresh band: 4 races @ 16 kn, RAI 0.0, ok",
          splits["fresh"].status == "ok" and splits["fresh"].n_races == 4
          and approx(splits["fresh"].rai, 0.0))
    check("medium/heavy bands carry no wind data ⇒ insufficient_data",
          splits["medium"].status == "insufficient_data"
          and splits["medium"].n_races == 0
          and splits["medium"].rai is None
          and splits["heavy"].status == "insufficient_data")

    print("\n== 4. Minimum-race threshold ==")
    few_obs = [
        RaceObservation(boat_id=7, event_name=f"E{i}", race_name=None,
                        event_date=f"2024-03-{i + 1:02d}", place=1,
                        fleet_size=2, rating_value=1.0)
        for i in range(4)
    ]
    few_fields = {(f"E{i}", None, f"2024-03-{i + 1:02d}"): (1.0, 1.05)
                  for i in range(4)}
    few = compute_rai_from_observations(few_obs, few_fields,
                                        info=BoatInfo(boat_id=7))
    check("4 scored races ⇒ insufficient_data, rai=None",
          few.status == "insufficient_data" and few.rai is None
          and few.ci_lower is None and not few.meets_min_races,
          f"n_scored={few.n_scored} min={few.min_races_required}")
    five = compute_rai_from_observations(
        few_obs + [RaceObservation(boat_id=7, event_name="E4", race_name=None,
                                   event_date="2024-03-05", place=1,
                                   fleet_size=2, rating_value=1.0)],
        {**few_fields, ("E4", None, "2024-03-05"): (1.0, 1.05)},
        info=BoatInfo(boat_id=7),
    )
    check("exactly 5 scored races ⇒ ok", five.status == "ok" and five.rai is not None)

    print("\n== 5. Reproducibility per dataset version ==")
    rerun = compute_rai_v1(engine, HELD_ID)
    check("re-run is bit-identical (incl. serialised contract)",
          held.to_dict() == rerun.to_dict()
          and json.dumps(held.to_dict(), sort_keys=True)
          == json.dumps(rerun.to_dict(), sort_keys=True))
    check("result carries dataset + config fingerprints",
          bool(held.dataset_fingerprint)
          and held.config_fingerprint == DEFAULT_CONFIG.fingerprint(),
          f"ds={held.dataset_fingerprint} cfg={held.config_fingerprint}")

    pure_obs = [
        RaceObservation(boat_id=HELD_ID, event_name=f"Event{i}", race_name=None,
                        event_date=race_day(i).isoformat(), place=1,
                        fleet_size=2, rating_value=HELD_TCC,
                        raw={"tws": 6.0 if i < 4 else 16.0})
        for i in range(N_RACES)
    ]
    fp_a = dataset_fingerprint(tuple(pure_obs))
    fp_added = dataset_fingerprint(tuple(
        pure_obs + [RaceObservation(boat_id=HELD_ID, event_name="Extra",
                                    race_name=None, event_date="2024-12-01",
                                    place=1, fleet_size=2, rating_value=HELD_TCC)]
    ))
    check("dataset fingerprint stable on re-hash", dataset_fingerprint(tuple(pure_obs)) == fp_a,
          fp_a)
    check("row added ⇒ new dataset version", fp_added != fp_a, fp_added)
    check("config change ⇒ new config fingerprint",
          RAIRulesetConfigV1(min_races=3).fingerprint() != DEFAULT_CONFIG.fingerprint())

    print("\n== 6. Sensitivity to identity-merge errors ==")
    clean_fp = held.dataset_fingerprint
    with engine.begin() as conn:
        # Merge error: CHASER's 8 second places are also keyed to HELD
        # (at HELD's TCC: expected 1/2 = 0.5, actual 2/2 = 1.0 ⇒ A = −50).
        for i in range(N_RACES):
            seed_result(conn, HELD_ID, f"Event{i}", None, race_day(i),
                        2, 2, HELD_TCC, raw={"tws": 6.0 if i < 4 else 16.0})
    polluted = compute_rai_v1(engine, HELD_ID)
    check("merge error moves RAI by the pinned −25.0",
          polluted.n_scored == 2 * N_RACES and approx(polluted.rai, -25.0),
          f"n_scored={polluted.n_scored} rai={polluted.rai:.2f}"
          f" ci=[{polluted.ci_lower:.2f}, {polluted.ci_upper:.2f}]")
    check("merge error changes the dataset version",
          polluted.dataset_fingerprint != clean_fp,
          f"{clean_fp} → {polluted.dataset_fingerprint}")

    with engine.begin() as conn:
        conn.execute(text(
            "DELETE FROM race_results WHERE boat_id = :bid AND place = 2"
        ), {"bid": HELD_ID})
    restored = compute_rai_v1(engine, HELD_ID)
    check("split restores RAI and the dataset fingerprint",
          approx(restored.rai, 0.0) and restored.dataset_fingerprint == clean_fp,
          f"rai={restored.rai} fp={restored.dataset_fingerprint}")

    # ----------------------------------------------------------------------
    n_pass = sum(1 for _, ok, _ in RESULTS if ok)
    n_fail = len(RESULTS) - n_pass
    print(f"\n{n_pass}/{len(RESULTS)} checks passed", end="")
    if n_fail:
        print(f" — {n_fail} FAILED")
        print("RESULT: FAIL")
        return 1
    print()
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
