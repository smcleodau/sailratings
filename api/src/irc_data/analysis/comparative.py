"""SM-01-06: Comparative metrics for Rivals, Design Comparator, Fleet Intelligence.

Three output contracts:

- ``HeadToHeadV1`` — win/loss records between two boats across shared events,
  computed both ways:
    * uncorrected  — order of ``place`` (official published result)
    * corrected    — order of TCC-rated performance (elapsed × TCC).  In the
      database, elapsed times are not yet extracted from raw payloads, so the
      corrected proxy is ``place / rating_value`` (lower = sailed better
      relative to rating).  Where a source payload does carry per-race
      elapsed/corrected times (``raw_data`` keys ``elapsed``/``finish_time``
      and ``irc_corrected``/``phs_corrected``), the real corrected time is
      preferred.

- ``DesignComparatorV1`` — design-class comparator metrics: TCC band
  (min–max), mean/median RAI across the class, results depth (races per boat,
  races per active boat), and modification headroom (within-class TCC spread
  plus headroom to the class best).

- ``FleetSummaryV1`` — fleet-at-a-glance aggregates: boats, designs,
  countries, TCC distribution, results depth and racing activity, optionally
  scoped to a design and/or country.

All three are computed straight off the canonical tables
(``boats`` / ``race_results`` / ``tcc_snapshots`` /
``mv_boat_performance_summary``) with the shared analytics race filter so the
numbers line up with the other analytics engines.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.filters import BASIC_IRC_FILTER

logger = logging.getLogger(__name__)

# Contract identifiers — pinned so API responses and tests can assert on them.
HEAD_TO_HEAD_VERSION = "HeadToHeadV1"
DESIGN_COMPARATOR_VERSION = "DesignComparatorV1"
FLEET_SUMMARY_VERSION = "FleetSummaryV1"

# Corrected-time extraction keys (source payloads are not standardised).
_ELAPSED_KEYS = ("elapsed", "elapsed_time", "finish_time")
_CORRECTED_KEYS = ("irc_corrected", "corrected", "corrected_time", "phs_corrected")

# Modification-headroom thresholds on within-class TCC coefficient of variation.
_HEADROOM_LOW_CV = 0.005
_HEADROOM_MODERATE_CV = 0.015


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time_to_seconds(raw: Any) -> float | None:
    """Parse ``HH:MM:SS`` (or ``H:MM:SS.fff``) payloads into seconds."""
    if raw is None:
        return None
    if isinstance(raw, datetime.timedelta):
        return raw.total_seconds()
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(s)
    except (TypeError, ValueError):
        return None


def _raw_seconds(raw_data: Any, keys: tuple[str, ...]) -> float | None:
    """Pull a time value out of a race-result raw payload, if present."""
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw_data, dict):
        return None
    for key in keys:
        val = _parse_time_to_seconds(raw_data.get(key))
        if val is not None:
            return val
    return None


def _tcc_band(mean: float | None, width: float = 0.02) -> dict | None:
    """Rating band (mean ± width, clamped at 0) a design races in."""
    if mean is None:
        return None
    return {"low": round(max(mean - width, 0.0), 4), "high": round(mean + width, 4)}


# ---------------------------------------------------------------------------
# Head-to-Head (Rivals surface)
# ---------------------------------------------------------------------------


@dataclass
class HeadToHeadResult:
    """HeadToHeadV1 — corrected and uncorrected records between two boats."""

    boat_id: int
    boat_name: str
    rival_boat_id: int
    rival_name: str
    shared_events: int
    shared_races: int
    wins: int
    losses: int
    ties: int
    corrected_wins: int
    corrected_losses: int
    corrected_ties: int
    corrected_races: int
    corrected_mode: str
    avg_rating: float | None
    rival_avg_rating: float | None
    rating_delta: float | None

    def to_dict(self) -> dict:
        total = self.wins + self.losses + self.ties
        return {
            "version": HEAD_TO_HEAD_VERSION,
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "rival_boat_id": self.rival_boat_id,
            "rival_name": self.rival_name,
            "shared_events": self.shared_events,
            "shared_races": self.shared_races,
            "uncorrected": {
                "wins": self.wins,
                "losses": self.losses,
                "ties": self.ties,
                "total": total,
                "win_rate": round(self.wins / total, 4) if total else None,
            },
            "corrected": {
                "wins": self.corrected_wins,
                "losses": self.corrected_losses,
                "ties": self.corrected_ties,
                "total": self.corrected_races,
                "win_rate": (
                    round(self.corrected_wins / self.corrected_races, 4)
                    if self.corrected_races
                    else None
                ),
                "mode": self.corrected_mode,
            },
            "avg_rating": round(self.avg_rating, 4) if self.avg_rating is not None else None,
            "rival_avg_rating": (
                round(self.rival_avg_rating, 4) if self.rival_avg_rating is not None else None
            ),
            "rating_delta": (
                round(self.rating_delta, 4) if self.rating_delta is not None else None
            ),
        }


def _meeting_key(row: Any) -> tuple:
    """Identity of one race both boats started in."""
    event_date = row["event_date"]
    if hasattr(event_date, "isoformat"):
        event_date = event_date.isoformat()
    return (
        row["event_name"],
        row["race_name"] or "",
        event_date or "",
        row["race_number"] if row["race_number"] is not None else "",
    )


def _corrected_outcome(row: Any) -> tuple[str | None, bool]:
    """Corrected-order outcome for one meeting.

    Returns ``(outcome, used_real_times)`` where outcome is ``'win'`` /
    ``'loss'`` / ``'tie'`` from the focal boat's perspective, or ``None`` when
    the corrected comparison can't be made for this meeting.
    """
    my_elapsed = _raw_seconds(row["raw_data"], _ELAPSED_KEYS)
    rival_elapsed = _raw_seconds(row["rival_raw_data"], _ELAPSED_KEYS)
    my_rating = _to_float(row["rating_value"])
    rival_rating = _to_float(row["rival_rating_value"])

    if (
        my_elapsed is not None
        and rival_elapsed is not None
        and my_rating
        and rival_rating
    ):
        my_corr = my_elapsed * my_rating
        rival_corr = rival_elapsed * rival_rating
        used_real = True
    else:
        # Fallback proxy: official place per unit of TCC.  A boat that beats a
        # higher-rated rival outright has clearly beaten it on corrected time;
        # this ratio generalises that comparison across all meetings.
        my_place = row["place"]
        rival_place = row["rival_place"]
        if not my_place or not rival_place or not my_rating or not rival_rating:
            return None, False
        my_corr = my_place / my_rating
        rival_corr = rival_place / rival_rating
        used_real = False

    if abs(my_corr - rival_corr) < 1e-9:
        return "tie", used_real
    return ("win" if my_corr < rival_corr else "loss"), used_real


def compute_head_to_head_v1(
    engine: Engine, boat_id: int, rival_boat_id: int
) -> HeadToHeadResult | None:
    """HeadToHeadV1 for one boat pair.

    Returns ``None`` when either boat is unknown; otherwise returns a record
    even when the pair has no shared races (``shared_races == 0``).
    """

    query = text(f"""
        SELECT
            b.boat_name        AS boat_name,
            rb.boat_name       AS rival_name,
            r.event_name,
            r.race_name,
            r.event_date,
            r.race_number,
            r.place,
            r.rating_value,
            r.corrected_time,
            r.raw_data,
            rr.place           AS rival_place,
            rr.rating_value    AS rival_rating_value,
            rr.corrected_time  AS rival_corrected_time,
            rr.raw_data        AS rival_raw_data
        FROM boats b
        CROSS JOIN boats rb
        LEFT JOIN race_results r
            ON r.boat_id = b.id
            AND r.status = 'finished'
            AND r.place IS NOT NULL
            AND r.rating_value IS NOT NULL
        LEFT JOIN race_results rr
            ON rr.boat_id = rb.id
            AND rr.event_name = r.event_name
            AND COALESCE(rr.race_name, '') = COALESCE(r.race_name, '')
            AND rr.event_date IS NOT DISTINCT FROM r.event_date
            AND rr.race_number IS NOT DISTINCT FROM r.race_number
            AND rr.status = 'finished'
            AND rr.place IS NOT NULL
        WHERE b.id = :boat_id
          AND rb.id = :rival_id
          {BASIC_IRC_FILTER}
    """)

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(
            query, {"boat_id": boat_id, "rival_id": rival_boat_id}
        )]

    if not rows:
        return None

    # Boat names survive on the seed row even when the pair never met.
    boat_name = rows[0]["boat_name"]
    rival_name = rows[0]["rival_name"]

    # The LEFT JOIN seed row has no race data when the pair never met; rows
    # without a rival finish are races the rival did not share.
    rows = [
        r for r in rows
        if r["event_name"] is not None and r["rival_place"] is not None
    ]

    meetings: dict[tuple, Any] = {}
    for row in rows:
        meetings.setdefault(_meeting_key(row), row)
    rows = list(meetings.values())

    wins = losses = ties = 0
    c_wins = c_losses = c_ties = 0
    c_races = 0
    real_races = 0
    my_ratings: list[float] = []
    rival_ratings: list[float] = []

    for row in rows:
        if row["place"] < row["rival_place"]:
            wins += 1
        elif row["place"] > row["rival_place"]:
            losses += 1
        else:
            ties += 1

        outcome, used_real = _corrected_outcome(row)
        if used_real and outcome is not None:
            real_races += 1
        if outcome is not None:
            c_races += 1
            if outcome == "win":
                c_wins += 1
            elif outcome == "loss":
                c_losses += 1
            else:
                c_ties += 1

        my_rating = _to_float(row["rating_value"])
        rival_rating = _to_float(row["rival_rating_value"])
        if my_rating:
            my_ratings.append(my_rating)
        if rival_rating:
            rival_ratings.append(rival_rating)

    avg_rating = float(np.mean(my_ratings)) if my_ratings else None
    rival_avg = float(np.mean(rival_ratings)) if rival_ratings else None

    return HeadToHeadResult(
        boat_id=boat_id,
        boat_name=boat_name,
        rival_boat_id=rival_boat_id,
        rival_name=rival_name,
        shared_events=len({r["event_name"] for r in rows}),
        shared_races=len(rows),
        wins=wins,
        losses=losses,
        ties=ties,
        corrected_wins=c_wins,
        corrected_losses=c_losses,
        corrected_ties=c_ties,
        corrected_races=c_races,
        corrected_mode=(
            "corrected_time" if real_races == c_races and c_races
            else "mixed" if real_races
            else "place_per_tcc"
        ),
        avg_rating=avg_rating,
        rival_avg_rating=rival_avg,
        rating_delta=(avg_rating - rival_avg)
        if avg_rating is not None and rival_avg is not None
        else None,
    )


def compute_rivals_v1(
    engine: Engine, boat_id: int, min_meetings: int = 2
) -> list[HeadToHeadResult]:
    """HeadToHeadV1 records for every rival sharing ``min_meetings`` races."""

    rivals_query = text("""
        SELECT DISTINCT r2.boat_id AS rival_id
        FROM race_results r1
        JOIN race_results r2
            ON r2.event_name = r1.event_name
            AND COALESCE(r2.race_name, '') = COALESCE(r1.race_name, '')
            AND r2.event_date IS NOT DISTINCT FROM r1.event_date
        WHERE r1.boat_id = :boat_id
          AND r2.boat_id != :boat_id
    """)

    with engine.connect() as conn:
        rival_ids = [r.rival_id for r in conn.execute(rivals_query, {"boat_id": boat_id})]

    records = []
    for rival_id in rival_ids:
        record = compute_head_to_head_v1(engine, boat_id, rival_id)
        if record and record.shared_races >= min_meetings:
            records.append(record)

    records.sort(key=lambda r: (-r.shared_races, r.rival_name))
    return records


# ---------------------------------------------------------------------------
# Design Comparator
# ---------------------------------------------------------------------------


@dataclass
class DesignComparatorResult:
    """DesignComparatorV1 — comparator metrics for one design class."""

    design: str
    n_boats: int
    band: dict | None
    tcc_mean: float | None
    tcc_median: float | None
    tcc_std: float | None
    tcc_min: float | None
    tcc_max: float | None
    mean_rai: float | None
    median_rai: float | None
    n_with_results: int
    total_results: int
    results_depth_per_boat: float
    results_depth_per_active_boat: float
    headroom_to_best: float | None
    modification_headroom: str
    n_countries: int = 0
    countries: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": DESIGN_COMPARATOR_VERSION,
            "design": self.design,
            "n_boats": self.n_boats,
            "band": self.band,
            "tcc": {
                "mean": round(self.tcc_mean, 4) if self.tcc_mean is not None else None,
                "median": round(self.tcc_median, 4) if self.tcc_median is not None else None,
                "std": round(self.tcc_std, 5) if self.tcc_std is not None else None,
                "min": round(self.tcc_min, 4) if self.tcc_min is not None else None,
                "max": round(self.tcc_max, 4) if self.tcc_max is not None else None,
            },
            "rai": {
                "mean": round(self.mean_rai, 2) if self.mean_rai is not None else None,
                "median": round(self.median_rai, 2) if self.median_rai is not None else None,
                "n_with_results": self.n_with_results,
            },
            "results_depth": {
                "total_results": self.total_results,
                "per_boat": round(self.results_depth_per_boat, 2),
                "per_active_boat": round(self.results_depth_per_active_boat, 2),
            },
            "modification": {
                "headroom_to_best": (
                    round(self.headroom_to_best, 4)
                    if self.headroom_to_best is not None
                    else None
                ),
                "headroom_class": self.modification_headroom,
            },
            "n_countries": self.n_countries,
            "countries": self.countries,
        }


def design_comparator(engine: Engine, design: str) -> DesignComparatorResult | None:
    """DesignComparatorV1 metrics for a single design class."""

    base_query = text("""
        SELECT b.id AS boat_id, b.country, t.tcc
        FROM boats b
        JOIN tcc_snapshots t
            ON t.boat_id = b.id
            AND t.snapshot_date = (
                SELECT MAX(snapshot_date) FROM tcc_snapshots
                WHERE boat_id = b.id
            )
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
    """)

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(base_query, {"design": design})]

    if not rows:
        return None

    # One row per boat (latest snapshot only).
    by_boat: dict[int, dict] = {}
    for r in rows:
        by_boat.setdefault(r["boat_id"], r)
    rows = list(by_boat.values())

    boat_ids = [r["boat_id"] for r in rows]
    tcc_vals = np.array([float(r["tcc"]) for r in rows])
    n = len(rows)

    tcc_mean = float(np.mean(tcc_vals))
    tcc_median = float(np.median(tcc_vals))
    tcc_std = float(np.std(tcc_vals, ddof=1)) if n > 1 else 0.0
    tcc_min = float(np.min(tcc_vals))
    tcc_max = float(np.max(tcc_vals))

    countries: dict[str, int] = {}
    for r in rows:
        c = r.get("country")
        if c:
            countries[c] = countries.get(c, 0) + 1

    placeholders = ", ".join(f":id_{i}" for i in range(len(boat_ids)))
    params = {f"id_{i}": bid for i, bid in enumerate(boat_ids)}

    # Results depth
    activity_query = text(f"""
        SELECT COUNT(*) AS total_results,
               COUNT(DISTINCT boat_id) AS n_active
        FROM race_results
        WHERE boat_id IN ({placeholders})
    """)
    with engine.connect() as conn:
        activity = conn.execute(activity_query, params).first()
    total_results = int(activity.total_results or 0) if activity else 0
    n_active = int(activity.n_active or 0) if activity else 0

    # RAI distribution (MV first, live fallback)
    mean_rai: float | None = None
    median_rai: float | None = None
    n_with_results = 0
    rai_values: list[float] = []

    try:
        mv_query = text(f"""
            SELECT boat_id, avg_finish_pct, finished_races
            FROM mv_boat_performance_summary
            WHERE boat_id IN ({placeholders})
              AND finished_races >= 3
        """)
        with engine.connect() as conn:
            mv_rows = conn.execute(mv_query, params).fetchall()
        for r in mv_rows:
            if r.avg_finish_pct is not None:
                rai_values.append((0.5 - float(r.avg_finish_pct)) * 100)
        n_with_results = len(mv_rows)
    except Exception:
        logger.debug("mv_boat_performance_summary unavailable; falling back to live RAI")
        live_query = text(f"""
            SELECT boat_id,
                   AVG(CAST(place AS float) / NULLIF(fleet_size, 0)) AS avg_finish_pct
            FROM race_results
            WHERE boat_id IN ({placeholders})
              AND status = 'finished'
              AND place IS NOT NULL
              AND fleet_size > 1
            GROUP BY boat_id
            HAVING COUNT(*) >= 3
        """)
        with engine.connect() as conn:
            live_rows = conn.execute(live_query, params).fetchall()
        for r in live_rows:
            if r.avg_finish_pct is not None:
                rai_values.append((0.5 - float(r.avg_finish_pct)) * 100)
        n_with_results = len(live_rows)

    if rai_values:
        arr = np.array(rai_values)
        mean_rai = float(np.mean(arr))
        median_rai = float(np.median(arr))

    # Modification headroom: spread of TCC within the class.  A tight class is
    # one-design-like (little to gain from tweaking); a wide class leaves room.
    cv = tcc_std / tcc_mean if tcc_mean else 0.0
    if cv < _HEADROOM_LOW_CV:
        headroom_class = "low (one-design-like)"
    elif cv < _HEADROOM_MODERATE_CV:
        headroom_class = "moderate"
    else:
        headroom_class = "high (significant within-class variation)"

    return DesignComparatorResult(
        design=design,
        n_boats=n,
        band=_tcc_band(tcc_mean),
        tcc_mean=tcc_mean,
        tcc_median=tcc_median,
        tcc_std=tcc_std,
        tcc_min=tcc_min,
        tcc_max=tcc_max,
        mean_rai=mean_rai,
        median_rai=median_rai,
        n_with_results=n_with_results,
        total_results=total_results,
        results_depth_per_boat=total_results / n if n else 0.0,
        results_depth_per_active_boat=total_results / n_active if n_active else 0.0,
        headroom_to_best=(tcc_max - tcc_median) if n else None,
        modification_headroom=headroom_class,
        n_countries=len(countries),
        countries=dict(sorted(countries.items(), key=lambda kv: kv[1], reverse=True)),
    )


def design_comparator_batch(engine: Engine, designs: list[str]) -> dict:
    """DesignComparatorV1 for several classes, plus cross-class highlights."""

    profiles = []
    for name in designs:
        profile = design_comparator(engine, name)
        if profile:
            profiles.append(profile)

    highlights: list[str] = []
    if len(profiles) >= 2:
        by_tcc = sorted(profiles, key=lambda p: p.tcc_mean or 0)
        fastest, slowest = by_tcc[0], by_tcc[-1]
        if fastest.tcc_mean and slowest.tcc_mean:
            highlights.append(
                f"{fastest.design} rates faster (TCC {fastest.tcc_mean:.4f}) vs "
                f"{slowest.design} ({slowest.tcc_mean:.4f}); "
                f"delta {slowest.tcc_mean - fastest.tcc_mean:.4f}."
            )
        with_rai = [p for p in profiles if p.mean_rai is not None]
        if with_rai:
            best = max(with_rai, key=lambda p: p.mean_rai or -999)
            highlights.append(
                f"{best.design} boats outperform their rating most "
                f"(mean RAI {best.mean_rai:+.1f})."
            )
        deepest = max(profiles, key=lambda p: p.results_depth_per_active_boat)
        highlights.append(
            f"{deepest.design} has the deepest results base "
            f"({deepest.results_depth_per_active_boat:.1f} results per active boat)."
        )

    return {
        "version": DESIGN_COMPARATOR_VERSION,
        "designs": designs,
        "profiles": [p.to_dict() for p in profiles],
        "highlights": highlights,
    }


# ---------------------------------------------------------------------------
# Fleet Summary
# ---------------------------------------------------------------------------


def fleet_summary_v1(
    engine: Engine,
    design: str | None = None,
    country: str | None = None,
) -> dict:
    """FleetSummaryV1 — fleet-at-a-glance aggregates.

    Optionally scoped to a design class and/or country code.
    """

    scope = {"design": design, "country": country.upper() if country else None}

    # Latest-snapshot TCC per boat in scope (portable correlated subquery).
    where = ["t.tcc IS NOT NULL"]
    params: dict[str, Any] = {}
    if design:
        where.append("COALESCE(b.design_canonical, b.design) = :design")
        params["design"] = design
    if country:
        where.append("UPPER(b.country) = :country")
        params["country"] = country.upper()
    where_sql = " AND ".join(where)

    tcc_query = text(f"""
        SELECT b.id AS boat_id, b.country,
               COALESCE(b.design_canonical, b.design) AS design_name,
               t.tcc
        FROM boats b
        JOIN tcc_snapshots t
            ON t.boat_id = b.id
            AND t.snapshot_date = (
                SELECT MAX(snapshot_date) FROM tcc_snapshots
                WHERE boat_id = b.id
            )
        WHERE {where_sql}
    """)

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(tcc_query, params)]

    # One row per boat.
    by_boat: dict[int, dict] = {}
    for r in rows:
        by_boat.setdefault(r["boat_id"], r)
    rows = list(by_boat.values())

    tcc_vals = np.array([float(r["tcc"]) for r in rows]) if rows else None
    boats = len(rows)

    designs = {r["design_name"] for r in rows if r.get("design_name")}
    countries = {r["country"] for r in rows if r.get("country")}

    top_designs: list[dict] = []
    if not design and boats:
        counts: dict[str, int] = {}
        for r in rows:
            name = r.get("design_name")
            if name:
                counts[name] = counts.get(name, 0) + 1
        top_designs = [
            {"design": name, "fleet_size": size}
            for name, size in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ]

    empty = {
        "version": FLEET_SUMMARY_VERSION,
        "scope": scope,
        "boats": 0,
        "designs": 0,
        "countries": 0,
        "tcc": {"mean": None, "median": None, "min": None, "max": None, "band": None},
        "activity": {
            "boats_with_results": 0,
            "total_results": 0,
            "distinct_events": 0,
            "avg_results_per_boat": None,
        },
        "rai": {"mean": None, "median": None, "n_with_results": 0},
        "top_designs": top_designs,
    }
    if not boats:
        return empty

    boat_ids = [r["boat_id"] for r in rows]
    placeholders = ", ".join(f":id_{i}" for i in range(len(boat_ids)))
    id_params = {f"id_{i}": bid for i, bid in enumerate(boat_ids)}

    activity_query = text(f"""
        SELECT
            COUNT(*)                     AS total_results,
            COUNT(DISTINCT r.boat_id)    AS boats_with_results,
            COUNT(DISTINCT r.event_name) AS distinct_events
        FROM race_results r
        WHERE r.boat_id IN ({placeholders})
    """)

    with engine.connect() as conn:
        activity = dict(conn.execute(activity_query, id_params).first()._mapping)

    total_results = int(activity.get("total_results") or 0)
    boats_with_results = int(activity.get("boats_with_results") or 0)
    distinct_events = int(activity.get("distinct_events") or 0)

    # Fleet RAI from the performance MV (best-effort; absent on test DBs).
    rai_mean = rai_median = None
    n_with_results = 0
    try:
        rai_query = text(f"""
            SELECT boat_id, avg_finish_pct
            FROM mv_boat_performance_summary
            WHERE boat_id IN ({placeholders})
              AND finished_races >= 3
              AND avg_finish_pct IS NOT NULL
        """)
        with engine.connect() as conn:
            rai_vals = [
                (0.5 - float(r.avg_finish_pct)) * 100
                for r in conn.execute(rai_query, id_params)
            ]
        if rai_vals:
            arr = np.array(rai_vals)
            rai_mean = float(np.mean(arr))
            rai_median = float(np.median(arr))
            n_with_results = len(rai_vals)
    except Exception:
        logger.debug("mv_boat_performance_summary unavailable for fleet summary")

    tcc_mean = float(np.mean(tcc_vals)) if tcc_vals is not None else None

    return {
        "version": FLEET_SUMMARY_VERSION,
        "scope": scope,
        "boats": boats,
        "designs": len(designs),
        "countries": len(countries),
        "tcc": {
            "mean": round(tcc_mean, 4) if tcc_mean is not None else None,
            "median": round(float(np.median(tcc_vals)), 4) if tcc_vals is not None else None,
            "min": round(float(np.min(tcc_vals)), 4) if tcc_vals is not None else None,
            "max": round(float(np.max(tcc_vals)), 4) if tcc_vals is not None else None,
            "band": _tcc_band(tcc_mean),
        },
        "activity": {
            "boats_with_results": boats_with_results,
            "total_results": total_results,
            "distinct_events": distinct_events,
            "avg_results_per_boat": round(total_results / boats, 2) if boats else None,
        },
        "rai": {
            "mean": round(rai_mean, 2) if rai_mean is not None else None,
            "median": round(rai_median, 2) if rai_median is not None else None,
            "n_with_results": n_with_results,
        },
        "top_designs": top_designs,
    }
