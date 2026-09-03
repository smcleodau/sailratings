"""SM-01-07: Race-prep brief inputs (entries, rivals, forecast, condition fit).

One output contract:

- ``RacePrepFactsV1`` — the structured fact pack for the race-prep brief.
  For an upcoming event with an entry list (``events`` / ``event_entries``,
  populated from entry-list sources in the source register) and a focal boat,
  it emits:

    * ``event``         — identity, dates, venue, days until start
    * ``fleet``         — fleet size, TCC distribution, design/country mix
    * ``rivals``        — entered boats that are known rivals of the focal
                          boat (shared-race history per SM-01-06), each with
                          the TCC rating delta and an embedded HeadToHeadV1
    * ``course``        — course type plus historical race-distance stats for
                          this event from prior editions in ``race_results``
    * ``forecast``      — forecast-ingestion seam.  The provider decision is
                          still pending, so by default this is a structured
                          ``provider_pending`` stub; a ``forecast_provider``
                          callable can be injected without changing the
                          contract shape.
    * ``condition_fit`` — the focal boat's RAI (SM-01-03 definition:
                          expected-vs-actual finish percentile × 100) split
                          over the condition dimensions present in
                          ``race_results`` (fleet size, course distance,
                          field strength), plus a structured fit signal.
                          Wind/sea-state splits unlock once the forecast /
                          observed-weather provider lands.

Everything is computed off the canonical tables with the shared analytics
race filter so the numbers line up with the other analytics engines.
Structured facts only — AI-01 turns this pack into the tactical prose read.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis.filters import BASIC_IRC_FILTER
from irc_data.analysis.performance import _compute_expected_pct

logger = logging.getLogger(__name__)

# Contract identifier — pinned so API responses and tests can assert on it.
RACE_PREP_FACTS_VERSION = "RacePrepFactsV1"

# Minimum shared races for an entered boat to count as a rival of the focal
# boat.  Matches the SM-01-06 rivals-v1 default.
DEFAULT_MIN_RIVAL_MEETINGS = 2

# Minimum races per split bucket before the bucket is reported as meaningful
# and eligible for the condition-fit signal (SM-01 binding invariant: below
# minimum-N the model returns 'not meaningful', never a fabricated table).
MIN_SPLIT_RACES = 3

# RAI-delta thresholds for the condition-fit signal strength.
_SIGNAL_MODERATE_DELTA = 5.0
_SIGNAL_STRONG_DELTA = 10.0

# Condition split boundaries (data dimensions available in race_results).
# Bounds use .5 cut-lines so boundary values classify identically whether the
# column arrives as a Decimal (Postgres NUMERIC) or a float/int (SQLite).
_FLEET_SIZE_BANDS = (
    ("small_fleet", None, 7.5),      # fleet_size < 8
    ("medium_fleet", 7.5, 20.5),     # 8 <= fleet_size <= 20
    ("large_fleet", 20.5, None),     # fleet_size > 20
)
_DISTANCE_BANDS_NM = (
    ("short_course", None, 10.5),    # <= 10nm — windward/leeward-style
    ("medium_course", 10.5, 30.5),   # 11–30nm
    ("long_course", 30.5, None),     # > 30nm — offshore-style
)

# A forecast provider takes the structured event facts and returns a
# structured forecast summary dict, or None when it has no forecast for the
# event.  The concrete provider choice is still an open decision; this seam
# keeps the contract stable when it lands.
ForecastProvider = Callable[[dict[str, Any]], dict[str, Any] | None]


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


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
            return value
        except ValueError:
            return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return None


def _to_date(value: Any) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _round(value: float | None, ndigits: int = 4) -> float | None:
    return round(value, ndigits) if value is not None else None


def _band_for(value: float | None, bands: tuple[tuple[str, Any, Any], ...]) -> str | None:
    if value is None:
        return None
    for name, low, high in bands:
        if (low is None or value >= low) and (high is None or value <= high):
            return name
    return None


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _load_event(engine: Engine, event_id: int) -> dict[str, Any] | None:
    query = text("""
        SELECT id, name, start_date, end_date, venue, course_type, organiser
        FROM events
        WHERE id = :event_id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"event_id": event_id}).first()
    if row is None:
        return None
    r = row._mapping
    return {
        "event_id": r["id"],
        "name": r["name"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "venue": r["venue"],
        "course_type": r["course_type"],
        "organiser": r["organiser"],
    }


def _event_facts(event: dict[str, Any], as_of: datetime.date) -> dict[str, Any]:
    start = _to_date(event["start_date"])
    days_until = (start - as_of).days if start is not None else None
    return {
        "event_id": event["event_id"],
        "name": event["name"],
        "start_date": _to_iso(event["start_date"]),
        "end_date": _to_iso(event["end_date"]),
        "venue": event["venue"],
        "organiser": event["organiser"],
        "as_of": as_of.isoformat(),
        "days_until_start": days_until,
        "is_upcoming": days_until is not None and days_until >= 0,
    }


def _load_entries(engine: Engine, event_id: int) -> list[dict[str, Any]]:
    query = text("""
        SELECT
            ee.id          AS entry_id,
            ee.boat_id,
            COALESCE(b.boat_name, ee.boat_name)   AS boat_name,
            COALESCE(b.sail_number, ee.sail_number) AS sail_number,
            COALESCE(b.design_canonical, b.design, ee.design) AS design,
            b.country,
            ee.tcc         AS entry_tcc,
            (
                SELECT t.tcc FROM tcc_snapshots t
                WHERE t.boat_id = ee.boat_id
                ORDER BY t.snapshot_date DESC
                LIMIT 1
            )              AS current_tcc
        FROM event_entries ee
        LEFT JOIN boats b ON b.id = ee.boat_id
        WHERE ee.event_id = :event_id
        ORDER BY ee.id
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query, {"event_id": event_id})]


def _fleet_facts(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Fleet size, rating spread and design/country mix across the entries."""
    tccs = [
        t for t in
        (_to_float(e["current_tcc"]) if e["current_tcc"] is not None else _to_float(e["entry_tcc"])
         for e in entries)
        if t is not None
    ]
    arr = np.array(tccs) if tccs else None

    design_counts: dict[str, int] = {}
    country_counts: dict[str, int] = {}
    resolved = 0
    for e in entries:
        if e["boat_id"] is not None:
            resolved += 1
        if e.get("design"):
            design_counts[e["design"]] = design_counts.get(e["design"], 0) + 1
        if e.get("country"):
            country_counts[e["country"]] = country_counts.get(e["country"], 0) + 1

    designs = [
        {"design": name, "entries": n}
        for name, n in sorted(design_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {
        "size": len(entries),
        "matched_boats": resolved,
        "tcc": {
            "mean": _round(float(np.mean(arr))) if arr is not None else None,
            "median": _round(float(np.median(arr))) if arr is not None else None,
            "min": _round(float(np.min(arr))) if arr is not None else None,
            "max": _round(float(np.max(arr))) if arr is not None else None,
            "spread": (
                _round(float(np.max(arr) - np.min(arr))) if arr is not None else None
            ),
        },
        "designs": designs,
        "distinct_designs": len(design_counts),
        "countries": dict(sorted(country_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _course_facts(engine: Engine, event: dict[str, Any]) -> dict[str, Any]:
    """Course summary: declared course type plus this event's historical
    race-distance profile (same event name, prior editions)."""
    distances: list[float] = []
    race_count = 0
    editions = 0
    if event.get("name"):
        query = text("""
            SELECT DISTINCT event_date, race_name, race_number, course_distance_nm
            FROM race_results
            WHERE event_name = :event_name
              AND course_distance_nm IS NOT NULL
        """)
        edition_query = text("""
            SELECT COUNT(DISTINCT event_date) AS n
            FROM race_results
            WHERE event_name = :event_name
        """)
        with engine.connect() as conn:
            rows = conn.execute(query, {"event_name": event["name"]}).fetchall()
            editions = int(
                conn.execute(edition_query, {"event_name": event["name"]}).scalar() or 0
            )
        seen: set[tuple] = set()
        for r in rows:
            key = (r.event_date, r.race_name, r.race_number)
            if key in seen:
                continue
            seen.add(key)
            d = _to_float(r.course_distance_nm)
            if d is not None:
                distances.append(d)
                race_count += 1

    arr = np.array(distances) if distances else None
    return {
        "course_type": event.get("course_type"),
        "historical_editions": editions,
        "historical_races_with_distance": race_count,
        "distance_nm": {
            "mean": _round(float(np.mean(arr)), 2) if arr is not None else None,
            "min": _round(float(np.min(arr)), 2) if arr is not None else None,
            "max": _round(float(np.max(arr)), 2) if arr is not None else None,
        },
    }


def _forecast_facts(
    event: dict[str, Any],
    forecast_provider: ForecastProvider | None,
) -> dict[str, Any]:
    """Forecast-ingestion seam.

    The provider decision is pending, so with no provider injected this is a
    structured placeholder.  An injected provider keeps the same shape.
    """
    if forecast_provider is None:
        return {
            "provider": None,
            "status": "provider_pending",
            "issued_at": None,
            "summary": None,
        }
    try:
        summary = forecast_provider(_event_facts(event, datetime.date.today()))
    except Exception:  # pragma: no cover - defensive around external providers
        logger.exception("forecast provider failed for event %s", event.get("event_id"))
        summary = None
    provider_name = getattr(forecast_provider, "provider_name", None) or getattr(
        forecast_provider, "__name__", forecast_provider.__class__.__name__
    )
    if summary is None:
        return {
            "provider": provider_name,
            "status": "unavailable",
            "issued_at": None,
            "summary": None,
        }
    return {
        "provider": provider_name,
        "status": "ok",
        "issued_at": datetime.date.today().isoformat(),
        "summary": summary,
    }


def _load_boat(engine: Engine, boat_id: int) -> dict[str, Any] | None:
    query = text("""
        SELECT b.id, b.boat_name, b.sail_number,
               COALESCE(b.design_canonical, b.design) AS design,
               b.country,
               (
                   SELECT t.tcc FROM tcc_snapshots t
                   WHERE t.boat_id = b.id
                   ORDER BY t.snapshot_date DESC
                   LIMIT 1
               ) AS current_tcc
        FROM boats b
        WHERE b.id = :boat_id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"boat_id": boat_id}).first()
    if row is None:
        return None
    return dict(row._mapping)


def _rival_facts(
    engine: Engine,
    entries: list[dict[str, Any]],
    boat: dict[str, Any],
    my_entry: dict[str, Any] | None,
    min_meetings: int,
) -> list[dict[str, Any]]:
    """Entered boats that are known rivals of the focal boat.

    A rival is an entered boat (resolved to a canonical ``boats`` row) with at
    least ``min_meetings`` shared finished races.  Each rival carries the TCC
    rating delta against the focal boat and the full SM-01-06 HeadToHeadV1.
    """
    from irc_data.analysis.comparative import compute_head_to_head_v1

    my_tcc = _to_float(boat.get("current_tcc"))
    if my_tcc is None and my_entry is not None:
        my_tcc = _to_float(my_entry.get("entry_tcc"))

    rivals: list[dict[str, Any]] = []
    for entry in entries:
        rival_id = entry.get("boat_id")
        if rival_id is None or rival_id == boat["id"]:
            continue

        h2h = compute_head_to_head_v1(engine, boat["id"], rival_id)
        if h2h is None or h2h.shared_races < min_meetings:
            continue

        rival_tcc = _to_float(entry.get("current_tcc"))
        if rival_tcc is None:
            rival_tcc = _to_float(entry.get("entry_tcc"))

        delta = None
        if my_tcc is not None and rival_tcc is not None:
            # Positive delta: rival rates higher (faster) than the focal boat.
            delta = round(rival_tcc - my_tcc, 4)

        rivals.append(
            {
                "boat_id": rival_id,
                "boat_name": entry.get("boat_name"),
                "sail_number": entry.get("sail_number"),
                "design": entry.get("design"),
                "tcc": _round(rival_tcc),
                "rating_delta": delta,
                "head_to_head": h2h.to_dict(),
            }
        )

    rivals.sort(
        key=lambda r: (
            -(r["head_to_head"]["shared_races"]),
            r["boat_name"] or "",
        )
    )
    return rivals


# ---------------------------------------------------------------------------
# Condition fit — RAI splits
# ---------------------------------------------------------------------------


def _per_race_rai(engine: Engine, boat_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Per-race RAI rows for the focal boat (SM-01-03 definition).

    Returns ``(boat_row, races)`` where each race carries the fields needed
    for condition splits.  ``None`` when the boat is unknown.
    """
    boat = _load_boat(engine, boat_id)
    if boat is None:
        return None

    query = text(f"""
        SELECT
            r.event_name,
            r.race_name,
            r.event_date,
            r.place,
            r.fleet_size,
            r.rating_value,
            r.course_distance_nm
        FROM race_results r
        WHERE r.boat_id = :boat_id
          AND r.status = 'finished'
          AND r.place IS NOT NULL
          AND r.fleet_size IS NOT NULL
          AND r.fleet_size > 1
          {BASIC_IRC_FILTER}
        ORDER BY r.event_date DESC NULLS LAST
    """)
    # Mean rating of the rest of the field for every race the boat started,
    # fetched in one grouped pass (used for the field-strength split).
    field_query = text("""
        SELECT event_name, race_name, event_date,
               AVG(rating_value) AS mean_rating
        FROM race_results
        WHERE status = 'finished'
          AND rating_value IS NOT NULL
          AND boat_id != :boat_id
        GROUP BY event_name, race_name, event_date
    """)

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(query, {"boat_id": boat_id})]
        field_means = {
            (r.event_name, r.race_name or "", _to_iso(r.event_date)): _to_float(r.mean_rating)
            for r in conn.execute(field_query, {"boat_id": boat_id})
        }

    races: list[dict[str, Any]] = []
    for row in rows:
        rating = _to_float(row["rating_value"])
        if rating is None:
            continue
        actual_pct = row["place"] / row["fleet_size"]
        expected_pct = _compute_expected_pct(
            engine,
            boat_id,
            row["event_name"],
            row["race_name"],
            row["event_date"],
            row["rating_value"],
        )
        if expected_pct is None:
            # Same fallback as compute_rai: mid-fleet expectation.
            expected_pct = 0.5

        field_mean = field_means.get(
            (row["event_name"], row["race_name"] or "", _to_iso(row["event_date"]))
        )

        races.append(
            {
                "event_name": row["event_name"],
                "race_name": row["race_name"],
                "event_date": _to_iso(row["event_date"]),
                "place": row["place"],
                "fleet_size": row["fleet_size"],
                "rating_value": rating,
                "course_distance_nm": _to_float(row["course_distance_nm"]),
                "finish_pct": actual_pct,
                "expected_pct": expected_pct,
                "rai": (expected_pct - actual_pct) * 100,
                "field_mean_rating": field_mean,
            }
        )

    return boat, races


def _bucket_stats(races: list[dict[str, Any]]) -> dict[str, Any]:
    if not races:
        return {"rai": None, "n_races": 0, "meaningful": False, "avg_finish_pct": None}
    arr = np.array([r["rai"] for r in races])
    n = int(len(arr))
    return {
        "rai": _round(float(np.mean(arr)), 2),
        "n_races": n,
        "meaningful": n >= MIN_SPLIT_RACES,
        "avg_finish_pct": _round(float(np.mean([r["finish_pct"] for r in races])), 3),
    }


def _split_family(
    races: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], str | None],
    bucket_names: tuple[str, ...],
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name in bucket_names}
    unclassified = 0
    for race in races:
        name = key_fn(race)
        if name is None:
            unclassified += 1
            continue
        buckets.setdefault(name, []).append(race)
    return {
        "buckets": {name: _bucket_stats(rs) for name, rs in buckets.items()},
        "unclassified_races": unclassified,
    }


def _condition_signal(splits: dict[str, Any]) -> dict[str, Any]:
    """Structured condition-fit signal from the RAI splits.

    Picks the split family with the largest RAI gap between two meaningful
    buckets (both ≥ MIN_SPLIT_RACES).  Below minimum-N everywhere the signal
    is ``insufficient_data`` rather than a fabricated read (SM-01 invariant).
    """
    best: dict[str, Any] | None = None
    for family, data in splits.items():
        buckets = data.get("buckets", {})
        meaningful = [
            (name, b) for name, b in buckets.items() if b.get("meaningful")
        ]
        for i in range(len(meaningful)):
            for j in range(i + 1, len(meaningful)):
                name_a, a = meaningful[i]
                name_b, b = meaningful[j]
                delta = a["rai"] - b["rai"]
                if best is None or abs(delta) > abs(best["rai_delta"]):
                    best = {
                        "family": family,
                        "preferred_bucket": name_a if delta > 0 else name_b,
                        "other_bucket": name_b if delta > 0 else name_a,
                        "rai_delta": round(abs(delta), 2),
                    }

    if best is None:
        return {
            "status": "insufficient_data",
            "family": None,
            "preferred_bucket": None,
            "other_bucket": None,
            "rai_delta": None,
            "strength": "none",
        }

    if best["rai_delta"] >= _SIGNAL_STRONG_DELTA:
        strength = "strong"
    elif best["rai_delta"] >= _SIGNAL_MODERATE_DELTA:
        strength = "moderate"
    else:
        strength = "neutral"

    return {
        "status": "ok",
        "family": best["family"],
        "preferred_bucket": best["preferred_bucket"],
        "other_bucket": best["other_bucket"],
        "rai_delta": best["rai_delta"],
        "strength": strength,
    }


def _condition_fit(engine: Engine, boat_id: int) -> dict[str, Any] | None:
    """Condition-fit section: overall RAI plus RAI splits and a fit signal."""
    result = _per_race_rai(engine, boat_id)
    if result is None:
        return None
    _boat, races = result

    overall = _bucket_stats(races)

    splits = {
        "fleet_size": _split_family(
            races,
            lambda r: _band_for(
                float(r["fleet_size"]) if r["fleet_size"] is not None else None,
                _FLEET_SIZE_BANDS,
            ),
            tuple(name for name, _lo, _hi in _FLEET_SIZE_BANDS),
        ),
        "course_distance": _split_family(
            races,
            lambda r: _band_for(r["course_distance_nm"], _DISTANCE_BANDS_NM),
            tuple(name for name, _lo, _hi in _DISTANCE_BANDS_NM),
        ),
        "field_strength": _split_family(
            races,
            lambda r: (
                None
                if r["field_mean_rating"] is None
                else (
                    "stronger_field"
                    if r["field_mean_rating"] > r["rating_value"]
                    else "weaker_field"
                )
            ),
            ("stronger_field", "weaker_field"),
        ),
    }

    return {
        "definition": "RAI = (expected_finish_pct - actual_finish_pct) x 100; "
                      "positive = finishing better than TCC predicts",
        "min_split_races": MIN_SPLIT_RACES,
        "overall_rai": overall["rai"],
        "n_races": overall["n_races"],
        "avg_finish_pct": overall["avg_finish_pct"],
        "splits": splits,
        "signal": _condition_signal(splits),
    }


# ---------------------------------------------------------------------------
# RacePrepFactsV1 entry point
# ---------------------------------------------------------------------------


def race_prep_facts(
    engine: Engine,
    event_id: int,
    boat_id: int,
    *,
    as_of: datetime.date | None = None,
    forecast_provider: ForecastProvider | None = None,
    min_meetings: int = DEFAULT_MIN_RIVAL_MEETINGS,
) -> dict[str, Any] | None:
    """RacePrepFactsV1 for one upcoming event and focal boat.

    Returns ``None`` when the event or the boat is unknown.  The boat does
    not need to be entered in the event (the brief is often requested before
    entering); rivals/condition-fit are computed from its racing history
    regardless.
    """
    as_of = as_of or datetime.date.today()

    event = _load_event(engine, event_id)
    if event is None:
        return None
    boat = _load_boat(engine, boat_id)
    if boat is None:
        return None

    entries = _load_entries(engine, event_id)
    my_entry = next((e for e in entries if e.get("boat_id") == boat_id), None)

    condition_fit = _condition_fit(engine, boat_id)

    my_tcc = _to_float(boat.get("current_tcc"))
    if my_tcc is None and my_entry is not None:
        my_tcc = _to_float(my_entry.get("entry_tcc"))

    return {
        "version": RACE_PREP_FACTS_VERSION,
        "event": _event_facts(event, as_of),
        "focal_boat": {
            "boat_id": boat["id"],
            "boat_name": boat["boat_name"],
            "sail_number": boat["sail_number"],
            "design": boat["design"],
            "country": boat["country"],
            "tcc": _round(my_tcc),
            "entered": my_entry is not None,
        },
        "fleet": _fleet_facts(entries),
        "rivals": _rival_facts(engine, entries, boat, my_entry, min_meetings),
        "course": _course_facts(engine, event),
        "forecast": _forecast_facts(event, forecast_provider),
        "condition_fit": condition_fit,
    }
