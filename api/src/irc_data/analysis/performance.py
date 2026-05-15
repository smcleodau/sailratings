"""Engine 3: Racing Performance Analytics.

Quantify whether a boat outperforms or underperforms its rating when racing.

3a — Rating Advantage Index (RAI): expected vs actual finish percentile
3b — Head-to-Head Records: win/loss against specific rivals
3c — Performance × Measurement Regression: which modifications help at racing
3d — Fleet Performance Benchmarking: best/worst performers in a class
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from scipy import stats as scipy_stats
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RAIResult:
    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None
    rai: float  # positive = outperforming rating
    n_races: int
    ci_lower: float
    ci_upper: float
    avg_finish_pct: float
    avg_expected_pct: float
    wins: int
    podiums: int
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "design": self.design,
            "rai": round(self.rai, 2),
            "n_races": self.n_races,
            "ci_lower": round(self.ci_lower, 2),
            "ci_upper": round(self.ci_upper, 2),
            "avg_finish_pct": round(self.avg_finish_pct, 3),
            "avg_expected_pct": round(self.avg_expected_pct, 3),
            "wins": self.wins,
            "podiums": self.podiums,
            "interpretation": self.interpretation,
        }


@dataclass
class RivalRecord:
    rival_boat_id: int
    rival_name: str
    rival_sail_number: str
    wins: int
    losses: int
    events_together: int

    def to_dict(self) -> dict:
        total = self.wins + self.losses
        return {
            "rival_boat_id": self.rival_boat_id,
            "rival_name": self.rival_name,
            "rival_sail_number": self.rival_sail_number,
            "wins": self.wins,
            "losses": self.losses,
            "total": total,
            "win_rate": round(self.wins / total, 2) if total > 0 else 0,
            "events_together": self.events_together,
        }


@dataclass
class SmartBoatProfile:
    """Profile of a top-performing boat."""
    boat_id: int
    boat_name: str
    sail_number: str
    rai: float
    n_races: int
    measurements: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "rai": round(self.rai, 2),
            "n_races": self.n_races,
            "measurements": {k: round(v, 3) for k, v in self.measurements.items()},
        }


# ---------------------------------------------------------------------------
# 3a — Rating Advantage Index (RAI)
# ---------------------------------------------------------------------------


def compute_rai(engine: Engine, boat_id: int) -> RAIResult | None:
    """Compute the Rating Advantage Index for a specific boat.

    RAI = (expected_finish_pct - actual_finish_pct) × 100
    Positive RAI = consistently finishing better than TCC predicts.
    """
    from irc_data.analysis.filters import BASIC_IRC_FILTER

    # Get this boat's race results (excluding twilight/non-IRC)
    query = text(f"""
        SELECT
            r.event_name,
            r.race_name,
            r.event_date,
            r.place,
            r.fleet_size,
            r.rating_value,
            r.status,
            b.boat_name,
            b.sail_number,
            COALESCE(b.design_canonical, b.design) AS design
        FROM race_results r
        JOIN boats b ON b.id = r.boat_id
        WHERE r.boat_id = :boat_id
          AND r.status = 'finished'
          AND r.place IS NOT NULL
          AND r.fleet_size IS NOT NULL
          AND r.fleet_size > 1
          {BASIC_IRC_FILTER}
        ORDER BY r.event_date DESC NULLS LAST
    """)

    with engine.connect() as conn:
        races = conn.execute(query, {"boat_id": boat_id}).fetchall()

    if not races:
        return None

    boat_name = races[0].boat_name
    sail_number = races[0].sail_number
    design = races[0].design

    rai_values = []
    finish_pcts = []
    expected_pcts = []
    wins = 0
    podiums = 0

    for race in races:
        actual_pct = race.place / race.fleet_size
        finish_pcts.append(actual_pct)

        if race.place == 1:
            wins += 1
        if race.place <= 3:
            podiums += 1

        # Expected finish = TCC percentile among boats in that event/race
        expected_pct = _compute_expected_pct(
            engine, boat_id, race.event_name, race.race_name, race.event_date,
            race.rating_value,
        )

        if expected_pct is not None:
            expected_pcts.append(expected_pct)
            rai = (expected_pct - actual_pct) * 100
            rai_values.append(rai)
        else:
            # Fallback: assume expected = 0.5 (middle of fleet)
            expected_pcts.append(0.5)
            rai = (0.5 - actual_pct) * 100
            rai_values.append(rai)

    if not rai_values:
        return None

    rai_arr = np.array(rai_values)
    mean_rai = float(np.mean(rai_arr))
    n = len(rai_arr)

    # 95% confidence interval
    if n >= 3:
        se = float(scipy_stats.sem(rai_arr))
        ci = scipy_stats.t.interval(0.95, n - 1, loc=mean_rai, scale=se)
        ci_lower, ci_upper = float(ci[0]), float(ci[1])
    else:
        ci_lower = ci_upper = mean_rai

    avg_finish = float(np.mean(finish_pcts))
    avg_expected = float(np.mean(expected_pcts))

    # Interpretation
    if mean_rai > 5:
        interp = (
            f"Strong positive RAI ({mean_rai:+.1f}): this boat consistently finishes better "
            f"than its TCC predicts. Well-sailed or measurement-optimised."
        )
    elif mean_rai > 0:
        interp = (
            f"Slightly positive RAI ({mean_rai:+.1f}): marginally outperforming rating."
        )
    elif mean_rai > -5:
        interp = (
            f"Slightly negative RAI ({mean_rai:+.1f}): finishing roughly as expected or "
            f"marginally underperforming."
        )
    else:
        interp = (
            f"Negative RAI ({mean_rai:+.1f}): consistently finishing worse than TCC predicts. "
            f"May indicate rating disadvantage or performance gap."
        )

    return RAIResult(
        boat_id=boat_id,
        boat_name=boat_name,
        sail_number=sail_number,
        design=design,
        rai=mean_rai,
        n_races=n,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        avg_finish_pct=avg_finish,
        avg_expected_pct=avg_expected,
        wins=wins,
        podiums=podiums,
        interpretation=interp,
    )


def _compute_expected_pct(
    engine: Engine,
    boat_id: int,
    event_name: str,
    race_name: str | None,
    event_date,
    rating_value,
) -> float | None:
    """Compute expected finish percentile based on TCC rank among event participants."""
    if rating_value is None:
        return None

    rating_val = float(rating_value) if isinstance(rating_value, Decimal) else float(rating_value)

    # Find all boats' ratings at this event/race
    query = text("""
        SELECT r.rating_value
        FROM race_results r
        WHERE r.event_name = :event
          AND r.status = 'finished'
          AND r.rating_value IS NOT NULL
          AND COALESCE(r.race_name, '') = COALESCE(:race_name, '')
          AND (r.event_date IS NOT DISTINCT FROM :event_date)
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {
            "event": event_name,
            "race_name": race_name,
            "event_date": event_date,
        }).fetchall()

    if len(rows) < 2:
        return None

    all_ratings = sorted([float(r.rating_value) for r in rows])
    n = len(all_ratings)

    # Higher TCC = faster = expected to finish better (lower percentile)
    # Rank by rating descending
    rank = sum(1 for r in all_ratings if r > rating_val) + 1
    expected_pct = rank / n

    return expected_pct


# ---------------------------------------------------------------------------
# 3b — Head-to-Head Records
# ---------------------------------------------------------------------------


def compute_head_to_head(engine: Engine, boat_id: int, min_meetings: int = 2) -> list[RivalRecord]:
    """Find boats that raced at the same events and compute win/loss records."""
    from irc_data.analysis.filters import BASIC_IRC_FILTER

    # Build filter for the CTE (uses 'r' alias convention but we need bare table here)
    twilight_filter = """
        AND LOWER(COALESCE(event_name, '')) NOT LIKE '%%twilight%%'
        AND LOWER(COALESCE(race_name, ''))  NOT LIKE '%%twilight%%'
        AND rating_value IS NOT NULL
    """

    query = text(f"""
        WITH my_races AS (
            SELECT event_name, race_name, event_date, place, fleet_size
            FROM race_results
            WHERE boat_id = :boat_id
              AND status = 'finished'
              AND place IS NOT NULL
              {twilight_filter}
        ),
        rival_races AS (
            SELECT
                r.boat_id AS rival_id,
                b.boat_name AS rival_name,
                b.sail_number AS rival_sail,
                r.event_name,
                r.race_name,
                r.place AS rival_place,
                m.place AS my_place
            FROM race_results r
            JOIN boats b ON b.id = r.boat_id
            JOIN my_races m ON m.event_name = r.event_name
                AND COALESCE(m.race_name, '') = COALESCE(r.race_name, '')
                AND COALESCE(m.event_date::text, '') = COALESCE(r.event_date::text, '')
            WHERE r.boat_id != :boat_id
              AND r.status = 'finished'
              AND r.place IS NOT NULL
        )
        SELECT
            rival_id,
            rival_name,
            rival_sail,
            SUM(CASE WHEN my_place < rival_place THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN my_place > rival_place THEN 1 ELSE 0 END) AS losses,
            COUNT(*) AS meetings,
            COUNT(DISTINCT event_name) AS events_together
        FROM rival_races
        GROUP BY rival_id, rival_name, rival_sail
        HAVING COUNT(*) >= :min_meetings
        ORDER BY COUNT(*) DESC, rival_name
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"boat_id": boat_id, "min_meetings": min_meetings}).fetchall()

    return [
        RivalRecord(
            rival_boat_id=r.rival_id,
            rival_name=r.rival_name,
            rival_sail_number=r.rival_sail,
            wins=r.wins,
            losses=r.losses,
            events_together=r.events_together,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 3c — Performance × Measurement Regression (placeholder for future)
# ---------------------------------------------------------------------------


def performance_measurement_regression(engine: Engine, design: str) -> dict | None:
    """Regress RAI against measurement deviations from class mean.

    Reveals which modifications help at racing beyond what TCC accounts for.
    """
    # Get all boats in this design with both race results and measurements
    query = text("""
        SELECT
            b.id AS boat_id,
            t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr,
            perf.avg_finish_pct, perf.finished_races
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        LEFT JOIN mv_boat_performance_summary perf ON perf.boat_id = b.id
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND perf.finished_races >= 3
          AND t.tcc IS NOT NULL
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"design": design}).fetchall()
    except Exception:
        # MV may not exist yet
        return None

    if len(rows) < 5:
        return None

    data = [dict(r._mapping) for r in rows]
    features = ["lh", "beam", "draft", "headsails", "spinnakers", "crew", "dlr"]

    # Compute RAI-like metric (lower avg_finish_pct = better)
    # Use (0.5 - avg_finish_pct) * 100 as proxy RAI
    for d in data:
        d["rai_proxy"] = (0.5 - float(d["avg_finish_pct"])) * 100

    # Compute class means
    means = {}
    for feat in features:
        vals = [float(d[feat]) for d in data if d.get(feat) is not None]
        if vals:
            means[feat] = np.mean(vals)

    # Compute deviations from mean
    correlations = {}
    for feat in features:
        if feat not in means:
            continue
        pairs = [
            (float(d[feat]) - means[feat], d["rai_proxy"])
            for d in data if d.get(feat) is not None
        ]
        if len(pairs) >= 5:
            x = np.array([p[0] for p in pairs])
            y = np.array([p[1] for p in pairs])
            if np.std(x) > 0 and np.std(y) > 0:
                corr = float(np.corrcoef(x, y)[0, 1])
                correlations[feat] = round(corr, 4)

    return {
        "design": design,
        "n_boats": len(data),
        "measurement_performance_correlations": correlations,
    }


# ---------------------------------------------------------------------------
# 3d — Fleet Performance Benchmarking / Smart Boats
# ---------------------------------------------------------------------------


def get_smart_boats(
    engine: Engine,
    design: str,
    top_pct: float = 0.1,
    min_races: int = 3,
) -> dict:
    """Identify top performers in a design class and their measurement profiles.

    "Smart boats" = top 10% by performance (lowest avg finish percentile).
    """
    query = text("""
        SELECT
            b.id AS boat_id,
            b.boat_name,
            b.sail_number,
            t.tcc, t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr,
            perf.finished_races,
            perf.wins,
            perf.podiums,
            perf.avg_finish_pct
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        LEFT JOIN mv_boat_performance_summary perf ON perf.boat_id = b.id
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
        ORDER BY perf.avg_finish_pct ASC NULLS LAST
    """)

    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"design": design}).fetchall()
    except Exception:
        # MV may not exist yet — try without it
        return _get_smart_boats_fallback(engine, design, top_pct)

    if not rows:
        return {"design": design, "n_total": 0, "smart_boats": [], "fleet_rai_distribution": {}}

    data = [dict(r._mapping) for r in rows]

    # Filter to boats with enough races
    with_races = [d for d in data if d.get("finished_races") and d["finished_races"] >= min_races]

    # Identify top performers
    n_top = max(1, int(len(with_races) * top_pct)) if with_races else 0
    top_boats = with_races[:n_top]

    # Compute class measurement averages
    measurement_fields = ["lh", "beam", "draft", "headsails", "spinnakers", "crew", "dlr"]
    class_means = {}
    for f in measurement_fields:
        vals = [float(d[f]) for d in data if d.get(f) is not None]
        if vals:
            class_means[f] = round(float(np.mean(vals)), 3)

    # Smart boat profiles
    smart_boats = []
    for b in top_boats:
        measurements = {}
        for f in measurement_fields:
            if b.get(f) is not None:
                measurements[f] = float(b[f])
        smart_boats.append(SmartBoatProfile(
            boat_id=b["boat_id"],
            boat_name=b["boat_name"],
            sail_number=b["sail_number"],
            rai=(0.5 - float(b["avg_finish_pct"])) * 100 if b.get("avg_finish_pct") else 0,
            n_races=b.get("finished_races", 0),
            measurements=measurements,
        ))

    # Smart boat average measurements
    smart_means = {}
    for f in measurement_fields:
        vals = [float(b.measurements.get(f, 0)) for b in smart_boats if f in b.measurements]
        if vals:
            smart_means[f] = round(float(np.mean(vals)), 3)

    # Fleet RAI distribution
    rai_values = [(0.5 - float(d["avg_finish_pct"])) * 100
                  for d in with_races if d.get("avg_finish_pct")]
    distribution = {}
    if rai_values:
        arr = np.array(rai_values)
        distribution = {
            "mean": round(float(np.mean(arr)), 2),
            "median": round(float(np.median(arr)), 2),
            "std": round(float(np.std(arr)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
        }

    return {
        "design": design,
        "n_total": len(data),
        "n_with_races": len(with_races),
        "n_smart": len(smart_boats),
        "class_means": class_means,
        "smart_boat_means": smart_means,
        "smart_boats": [s.to_dict() for s in smart_boats],
        "fleet_rai_distribution": distribution,
    }


def _get_smart_boats_fallback(engine: Engine, design: str, top_pct: float) -> dict:
    """Fallback when mv_boat_performance_summary doesn't exist."""
    query = text("""
        SELECT
            b.id AS boat_id,
            b.boat_name,
            b.sail_number,
            t.tcc, t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew, t.dlr,
            COUNT(r.id) FILTER (WHERE r.status = 'finished') AS finished_races,
            AVG(r.place::float / NULLIF(r.fleet_size, 0))
                FILTER (WHERE r.status = 'finished' AND r.place IS NOT NULL AND r.fleet_size > 0)
                AS avg_finish_pct
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        LEFT JOIN race_results r ON r.boat_id = b.id
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
        GROUP BY b.id, b.boat_name, b.sail_number, t.tcc, t.lh, t.beam, t.draft,
                 t.headsails, t.spinnakers, t.crew, t.dlr
        ORDER BY avg_finish_pct ASC NULLS LAST
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"design": design}).fetchall()

    data = [dict(r._mapping) for r in rows]
    n_total = len(data)

    return {
        "design": design,
        "n_total": n_total,
        "n_with_races": sum(1 for d in data if d.get("finished_races", 0) > 0),
        "smart_boats": [],
        "fleet_rai_distribution": {},
    }
