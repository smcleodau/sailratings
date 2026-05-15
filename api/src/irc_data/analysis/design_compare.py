"""Engine 5: Cross-Design Comparison.

Compare design classes on multiple dimensions:
- TCC distribution (mean, spread, range)
- TCC/LOA ratio — rating efficiency
- Fleet RAI distribution
- Fleet activity (races per boat, growing/shrinking)
- Modification potential (TCC variation within class)
- Country distribution
- ORC polar comparison (if available)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass
class DesignProfile:
    """Comprehensive profile for a single design class."""
    design: str
    n_boats: int
    # TCC stats
    tcc_mean: float | None = None
    tcc_median: float | None = None
    tcc_std: float | None = None
    tcc_min: float | None = None
    tcc_max: float | None = None
    tcc_spread: float | None = None  # max - min
    # Rating efficiency
    avg_loa: float | None = None
    tcc_per_foot: float | None = None  # TCC/LOA ratio
    # Performance
    n_with_races: int = 0
    mean_rai: float | None = None
    median_rai: float | None = None
    # Activity
    total_race_results: int = 0
    avg_races_per_boat: float = 0.0
    # Modification potential
    modification_potential: str = ""  # "high", "moderate", "low"
    # Country distribution
    countries: dict[str, int] = field(default_factory=dict)
    n_countries: int = 0
    # ORC data
    has_orc: bool = False
    orc_avg_gph: float | None = None

    def to_dict(self) -> dict:
        result = {
            "design": self.design,
            "n_boats": self.n_boats,
            "tcc": {
                "mean": round(self.tcc_mean, 4) if self.tcc_mean else None,
                "median": round(self.tcc_median, 4) if self.tcc_median else None,
                "std": round(self.tcc_std, 5) if self.tcc_std else None,
                "min": round(self.tcc_min, 4) if self.tcc_min else None,
                "max": round(self.tcc_max, 4) if self.tcc_max else None,
                "spread": round(self.tcc_spread, 4) if self.tcc_spread else None,
            },
            "rating_efficiency": {
                "avg_loa": round(self.avg_loa, 2) if self.avg_loa else None,
                "tcc_per_foot": round(self.tcc_per_foot, 5) if self.tcc_per_foot else None,
            },
            "performance": {
                "n_with_races": self.n_with_races,
                "mean_rai": round(self.mean_rai, 2) if self.mean_rai is not None else None,
                "median_rai": round(self.median_rai, 2) if self.median_rai is not None else None,
            },
            "activity": {
                "total_race_results": self.total_race_results,
                "avg_races_per_boat": round(self.avg_races_per_boat, 1),
            },
            "modification_potential": self.modification_potential,
            "countries": self.countries,
            "n_countries": self.n_countries,
        }
        if self.has_orc:
            result["orc"] = {
                "avg_gph": round(self.orc_avg_gph, 2) if self.orc_avg_gph else None,
            }
        return result


def _build_design_profile(engine: Engine, design: str) -> DesignProfile | None:
    """Build a comprehensive profile for a single design class."""
    # Core TCC and dimensional stats
    query = text("""
        SELECT
            b.id AS boat_id,
            t.tcc,
            t.lh,
            b.loa,
            b.country,
            COALESCE(b.design_canonical, b.design) AS design_name
        FROM boats b
        JOIN LATERAL (
            SELECT tcc, lh FROM tcc_snapshots
            WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
    """)

    with engine.connect() as conn:
        rows = conn.execute(query, {"design": design}).fetchall()

    if not rows:
        return None

    data = [dict(r._mapping) for r in rows]
    boat_ids = [d["boat_id"] for d in data]
    n = len(data)

    tcc_vals = np.array([float(d["tcc"]) for d in data])

    profile = DesignProfile(
        design=design,
        n_boats=n,
        tcc_mean=float(np.mean(tcc_vals)),
        tcc_median=float(np.median(tcc_vals)),
        tcc_std=float(np.std(tcc_vals, ddof=1)) if n > 1 else 0.0,
        tcc_min=float(np.min(tcc_vals)),
        tcc_max=float(np.max(tcc_vals)),
        tcc_spread=float(np.max(tcc_vals) - np.min(tcc_vals)),
    )

    # LOA / rating efficiency
    loa_vals = [float(d["loa"]) for d in data if d.get("loa") is not None]
    if not loa_vals:
        # Fall back to lh
        loa_vals = [float(d["lh"]) for d in data if d.get("lh") is not None]

    if loa_vals:
        avg_loa = float(np.mean(loa_vals))
        profile.avg_loa = avg_loa
        if avg_loa > 0:
            # Convert metres to feet for TCC/foot ratio
            avg_loa_ft = avg_loa * 3.28084
            profile.tcc_per_foot = profile.tcc_mean / avg_loa_ft if profile.tcc_mean else None

    # Country distribution
    countries: dict[str, int] = {}
    for d in data:
        c = d.get("country")
        if c:
            countries[c] = countries.get(c, 0) + 1
    profile.countries = dict(sorted(countries.items(), key=lambda x: x[1], reverse=True))
    profile.n_countries = len(countries)

    # Performance stats (from MV or live)
    _add_performance_stats(engine, profile, boat_ids)

    # Race activity
    _add_activity_stats(engine, profile, boat_ids)

    # Modification potential
    if profile.tcc_std and profile.tcc_mean:
        cv = profile.tcc_std / profile.tcc_mean
        if cv < 0.005:
            profile.modification_potential = "low (very one-design-like)"
        elif cv < 0.015:
            profile.modification_potential = "moderate"
        else:
            profile.modification_potential = "high (significant within-class variation)"

    # ORC
    _add_orc_stats(engine, profile, boat_ids)

    return profile


def _add_performance_stats(engine: Engine, profile: DesignProfile, boat_ids: list[int]) -> None:
    """Add fleet RAI distribution from performance summary MV."""
    if not boat_ids:
        return

    placeholders = ", ".join(f":id_{i}" for i in range(len(boat_ids)))
    params = {f"id_{i}": bid for i, bid in enumerate(boat_ids)}

    try:
        query = text(f"""
            SELECT boat_id, avg_finish_pct, finished_races
            FROM mv_boat_performance_summary
            WHERE boat_id IN ({placeholders})
              AND finished_races >= 3
        """)
        with engine.connect() as conn:
            rows = conn.execute(query, params).fetchall()
    except Exception:
        # MV may not exist
        return

    if not rows:
        return

    rai_values = []
    for r in rows:
        if r.avg_finish_pct is not None:
            rai = (0.5 - float(r.avg_finish_pct)) * 100
            rai_values.append(rai)

    profile.n_with_races = len(rows)
    if rai_values:
        arr = np.array(rai_values)
        profile.mean_rai = float(np.mean(arr))
        profile.median_rai = float(np.median(arr))


def _add_activity_stats(engine: Engine, profile: DesignProfile, boat_ids: list[int]) -> None:
    """Add race activity statistics."""
    if not boat_ids:
        return

    placeholders = ", ".join(f":id_{i}" for i in range(len(boat_ids)))
    params = {f"id_{i}": bid for i, bid in enumerate(boat_ids)}

    query = text(f"""
        SELECT COUNT(*) AS total, COUNT(DISTINCT boat_id) AS boats_with_races
        FROM race_results
        WHERE boat_id IN ({placeholders})
    """)

    with engine.connect() as conn:
        row = conn.execute(query, params).first()

    if row:
        profile.total_race_results = row.total or 0
        boats_with = row.boats_with_races or 0
        if boats_with > 0:
            profile.avg_races_per_boat = profile.total_race_results / boats_with


def _add_orc_stats(engine: Engine, profile: DesignProfile, boat_ids: list[int]) -> None:
    """Add ORC polar comparison data if available."""
    if not boat_ids:
        return

    placeholders = ", ".join(f":id_{i}" for i in range(len(boat_ids)))
    params = {f"id_{i}": bid for i, bid in enumerate(boat_ids)}

    query = text(f"""
        SELECT AVG(gph) AS avg_gph, COUNT(*) AS n_orc
        FROM orc_certificates oc
        WHERE oc.boat_id IN ({placeholders})
          AND oc.gph IS NOT NULL
          AND oc.snapshot_date = (
              SELECT MAX(snapshot_date) FROM orc_certificates
              WHERE boat_id = oc.boat_id
          )
    """)

    with engine.connect() as conn:
        row = conn.execute(query, params).first()

    if row and row.n_orc and row.n_orc > 0:
        profile.has_orc = True
        profile.orc_avg_gph = float(row.avg_gph) if row.avg_gph else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_designs(engine: Engine, design_names: list[str]) -> dict:
    """Compare two or more design classes across all dimensions.

    Returns structured comparison with side-by-side metrics.
    """
    profiles = []
    for name in design_names:
        p = _build_design_profile(engine, name)
        if p:
            profiles.append(p)

    if not profiles:
        return {"designs": design_names, "error": "No designs found", "profiles": []}

    # Build comparison highlights
    highlights = []

    if len(profiles) >= 2:
        # TCC comparison
        by_tcc = sorted(profiles, key=lambda p: p.tcc_mean or 0)
        fastest = by_tcc[0]
        slowest = by_tcc[-1]
        highlights.append(
            f"{fastest.design} rates faster (TCC {fastest.tcc_mean:.4f}) "
            f"vs {slowest.design} ({slowest.tcc_mean:.4f}), "
            f"delta {(slowest.tcc_mean or 0) - (fastest.tcc_mean or 0):.4f}."
        )

        # Spread comparison
        tightest = min(profiles, key=lambda p: p.tcc_spread or 999)
        widest = max(profiles, key=lambda p: p.tcc_spread or 0)
        if tightest.design != widest.design:
            highlights.append(
                f"{tightest.design} has tighter TCC spread ({tightest.tcc_spread:.4f}) "
                f"— more one-design-like. {widest.design} has wider spread "
                f"({widest.tcc_spread:.4f}) — more variation in individual setup."
            )

        # Fleet size
        biggest = max(profiles, key=lambda p: p.n_boats)
        highlights.append(
            f"{biggest.design} has the largest fleet ({biggest.n_boats} boats)."
        )

        # Performance
        perf_profiles = [p for p in profiles if p.mean_rai is not None]
        if len(perf_profiles) >= 2:
            best_perf = max(perf_profiles, key=lambda p: p.mean_rai or -999)
            highlights.append(
                f"{best_perf.design} boats tend to outperform their rating "
                f"(mean RAI {best_perf.mean_rai:+.1f})."
            )

    return {
        "designs": design_names,
        "profiles": [p.to_dict() for p in profiles],
        "highlights": highlights,
    }


def list_comparable_designs(engine: Engine, design: str, limit: int = 10) -> list[dict]:
    """Find designs similar to the given one (by LOA/TCC range).

    Useful for "what else could I race?" queries.
    """
    # Get this design's profile
    profile = _build_design_profile(engine, design)
    if not profile or not profile.tcc_mean:
        return []

    tcc_target = profile.tcc_mean
    loa_target = profile.avg_loa

    # Find designs with similar TCC
    query = text("""
        SELECT
            COALESCE(b.design_canonical, b.design) AS design_name,
            COUNT(*) AS fleet_size,
            AVG(t.tcc)::numeric(8,4) AS avg_tcc,
            STDDEV(t.tcc)::numeric(8,5) AS std_tcc,
            AVG(t.lh)::numeric(6,2) AS avg_lh
        FROM boats b
        JOIN LATERAL (
            SELECT tcc, lh FROM tcc_snapshots
            WHERE boat_id = b.id ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
          AND COALESCE(b.design_canonical, b.design) != :design
          AND t.tcc BETWEEN :tcc_low AND :tcc_high
        GROUP BY COALESCE(b.design_canonical, b.design)
        HAVING COUNT(*) >= 3
        ORDER BY ABS(AVG(t.tcc) - :tcc_target)
        LIMIT :limit
    """)

    tcc_range = 0.1  # ±0.1 TCC
    with engine.connect() as conn:
        rows = conn.execute(query, {
            "design": design,
            "tcc_low": tcc_target - tcc_range,
            "tcc_high": tcc_target + tcc_range,
            "tcc_target": tcc_target,
            "limit": limit,
        }).fetchall()

    return [
        {
            "design": r.design_name,
            "fleet_size": r.fleet_size,
            "avg_tcc": float(r.avg_tcc) if r.avg_tcc else None,
            "tcc_delta": round(float(r.avg_tcc) - tcc_target, 4) if r.avg_tcc else None,
            "avg_lh": float(r.avg_lh) if r.avg_lh else None,
        }
        for r in rows
    ]
