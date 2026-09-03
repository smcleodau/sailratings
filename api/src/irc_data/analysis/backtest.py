"""SM-01-08 — Model backtesting and golden fixtures.

Two public entry points:

``golden_fixtures``
    Snapshot harness. Seeds a throwaway, self-contained PostgreSQL
    database from a checked-in JSON dataset (under
    ``tests/report/golden/<boat>/dataset.json``), rebuilds the
    ReportFactsV1 bundle with the *current* model code, and compares every
    figure against the checked-in golden bundle within stated tolerances.
    Any model/builder change that moves a report figure breaks the
    comparison — which is exactly what CI gates on.

``backtest_rai``
    Held-out-season evaluation of the Rating Advantage Index. For each
    season the RAI engine is re-run with that season hidden; the gap
    between the full-history RAI and the hold-one-season-out RAI
    (in-sample stability) plus the correlation between prior-season RAI
    and next-season finish percentile (out-of-sample predictive value)
    are the headline numbers. A deterministic holdout split of the Tier-C
    fleet model reports held-out MAE/R² for the rating model itself.

The module is library-shaped (pure functions returning plain dicts /
dataclasses) so it can be driven from pytest, from the CLI
(``api/scripts/run_model_backtest.py``), or from CI.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.analysis import regression as _reg
from irc_data.analysis.filters import BASIC_IRC_FILTER
from irc_data.analysis.performance import _compute_expected_pct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tolerances ("stated tolerance" from the acceptance criteria)
# ---------------------------------------------------------------------------

# Absolute tolerance applied to every float in a ReportFactsV1 bundle when
# comparing a rebuilt bundle against its golden fixture. TCC figures are
# quoted to 3dp in the product; 5e-3 ignores last-bit float jitter while
# catching genuine model movement.
DEFAULT_ABS_TOL = 5e-3
# Relative tolerance, applied to whichever is larger (|golden|, abs floor).
DEFAULT_REL_TOL = 1e-3

# Acceptance thresholds for the RAI held-out-season backtest.
RAI_STABILITY_TOL = 7.5        # max |full-history RAI − hold-one-season-out RAI|
RAI_MIN_RACES_AFTER_HOLDOUT = 5
RATING_MODEL_HOLDOUT_MAE_MAX = 0.040   # Tier-C held-out MAE ceiling
RATING_MODEL_HOLDOUT_R2_MIN = 0.80     # Tier-C held-out R² floor
HOLDOUT_SEED = 42
HOLDOUT_FRACTION = 0.2


# ---------------------------------------------------------------------------
# Golden-fixture harness
# ---------------------------------------------------------------------------


@dataclass
class FixtureBoat:
    """Registry entry for one golden-fixture boat."""
    slug: str             # filesystem slug, e.g. 'chilli_pepper'
    boat_name: str        # canonical display name, e.g. 'CHILLI PEPPER'
    sail_number: str
    design: str           # design class asserted on the rebuilt bundle
    description: str = ""


# The three boats named by the SM-01-08 acceptance criteria. Their design
# reports (ReportFactsV1 bundles) are checked in under
# tests/report/golden/<slug>/.
GOLDEN_BOATS: tuple[FixtureBoat, ...] = (
    FixtureBoat(
        slug="chilli_pepper",
        boat_name="CHILLI PEPPER",
        sail_number="GBR1663R",
        design="Sunfast 3300",
        description="Tier-A design-class fixture (has a parsed IRC certificate).",
    ),
    FixtureBoat(
        slug="diablo_j",
        boat_name="DIABLO-J",
        sail_number="GBR9205R",
        design="J/92",
        description=(
            "Long-history fixture (2007-2022 racing). Design class is absent "
            "in the production extract, so the fixture restores the J/92 "
            "class context the design report is generated against."
        ),
    ),
    FixtureBoat(
        slug="kestrel",
        boat_name="KESTREL",
        sail_number="GBR6779R",
        design="Sunfast 3300",
        description="Modern-era fixture (2021-2026 racing, constant TCC).",
    ),
)

GOLDEN_FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "tests" / "report" / "golden"

# Dataset tables copied into the scratch database, in load order.
# (table, fixture key, required)
_TABLES: tuple[tuple[str, str, bool], ...] = (
    ("boats", "boats", True),
    ("tcc_snapshots", "tcc_snapshots", True),
    ("irc_certificates", "irc_certificates", False),
    ("race_results", "race_results", False),
    ("boat_identities", "boat_identities", False),
)


@dataclass
class FigureDiff:
    path: str
    golden: Any
    actual: Any
    abs_diff: float | None = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "golden": self.golden,
            "actual": self.actual,
            "abs_diff": self.abs_diff,
        }


@dataclass
class GoldenComparison:
    boat_slug: str
    passed: bool
    figures_checked: int
    violations: list[FigureDiff] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "boat_slug": self.boat_slug,
            "passed": self.passed,
            "figures_checked": self.figures_checked,
            "violations": [v.to_dict() for v in self.violations],
        }


def golden_dataset_path(slug: str) -> Path:
    return GOLDEN_FIXTURES_ROOT / slug / "dataset.json"


def golden_bundle_path(slug: str) -> Path:
    return GOLDEN_FIXTURES_ROOT / slug / "golden_report_facts_v1.json"


# ── Scratch database ─────────────────────────────────────────────────────

# DDL mirrors the columns the report/analysis SQL actually reads. Keeping
# the scratch schema minimal is deliberate: the fixture proves the models,
# not the migrations (migrations have their own DP-03-05 suite).
_SCRATCH_DDL = """
CREATE TABLE boats (
    id integer PRIMARY KEY,
    boat_name text,
    sail_number text,
    cert_number text,
    design text,
    country text,
    year_built integer,
    hull_id text,
    builder text,
    designer text,
    design_canonical text,
    loa numeric,
    lwl numeric,
    beam_max numeric,
    displacement_kg numeric
);
CREATE TABLE tcc_snapshots (
    id serial PRIMARY KEY,
    boat_id integer NOT NULL,
    snapshot_date date,
    cert_year integer,
    tcc numeric,
    non_spi_tcc numeric,
    lh numeric,
    beam numeric,
    draft numeric,
    headsails integer,
    spinnakers integer,
    crew integer,
    dlr numeric,
    source text
);
CREATE TABLE irc_certificates (
    id serial PRIMARY KEY,
    boat_id integer NOT NULL,
    cert_number text,
    issue_date date,
    source text,
    source_url text,
    lh numeric,
    beam numeric,
    draft numeric,
    displacement_kg numeric,
    p numeric,
    e numeric,
    j numeric,
    stl numeric,
    muw numeric,
    mhw numeric,
    hlu numeric,
    hlp numeric,
    sym_slu numeric,
    sym_sf numeric,
    headsails integer,
    spinnakers integer,
    raw_data jsonb
);
CREATE TABLE race_results (
    id serial PRIMARY KEY,
    boat_id integer NOT NULL,
    event_name text,
    race_name text,
    event_date date,
    race_number integer,
    race_date_specific date,
    place integer,
    fleet_size integer,
    class_name text,
    status text,
    rating_value numeric,
    corrected_time text,
    elapsed_time text,
    organizing_club text,
    source text,
    source_url text,
    raw_data jsonb
);
CREATE TABLE boat_identities (
    id serial PRIMARY KEY,
    boat_id integer NOT NULL,
    boat_name text,
    sail_number text,
    owner text,
    flag text,
    source text,
    observed_date date
);
-- Present-but-empty: the optimisation builder probes this table for ORC
-- context. Fixture boats have no ORC data, but the table must exist.
CREATE TABLE orc_certificates (
    id serial PRIMARY KEY,
    boat_id integer,
    snapshot_date date,
    gph numeric,
    osn text,
    cdl numeric,
    triple_low numeric,
    triple_med numeric,
    triple_high numeric,
    loa numeric,
    displacement numeric,
    draft numeric,
    sail_area_upwind numeric,
    sail_area_downwind numeric,
    class_name text,
    ref_no text
);
"""


def create_scratch_db(admin_engine: Engine, name: str) -> str:
    """Create (dropping first if necessary) a throwaway database owned by
    the current user. Returns the database name."""
    safe = "".join(c for c in name if c.isalnum() or c == "_")
    if not safe:
        raise ValueError(f"unsafe scratch db name {name!r}")
    with admin_engine.connect() as conn:
        conn.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :db AND pid <> pg_backend_pid()"
        ), {"db": safe})
        conn.execute(text(f'DROP DATABASE IF EXISTS "{safe}"'))
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    return safe


def drop_scratch_db(admin_engine: Engine, name: str) -> None:
    safe = "".join(c for c in name if c.isalnum() or c == "_")
    if not safe:
        return
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ), {"db": safe})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{safe}"'))
    except Exception as e:  # teardown must never fail the suite
        logger.warning("drop_scratch_db(%s) failed: %s", safe, e)


def load_fixture_dataset(engine: Engine, dataset: dict) -> None:
    """Create the scratch schema and bulk-load a fixture dataset dict."""
    with engine.begin() as conn:
        for stmt in _SCRATCH_DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    # jsonb columns need an explicit cast from the JSON string we bind.
    jsonb_cols = {
        "irc_certificates": {"raw_data"},
        "race_results": {"raw_data"},
    }

    for table, key, required in _TABLES:
        rows = dataset.get(key) or []
        if required and not rows:
            raise ValueError(f"fixture dataset missing required key {key!r}")
        if not rows:
            continue
        cols = list(rows[0].keys())
        jsonb = jsonb_cols.get(table, set())
        col_sql = ", ".join(cols)
        ph_sql = ", ".join(f"CAST(:{c} AS jsonb)" if c in jsonb else f":{c}" for c in cols)
        bind_rows = [
            {
                c: (json.dumps(r[c]) if c in jsonb and r.get(c) is not None else r.get(c))
                for c in cols
            }
            for r in rows
        ]
        with engine.begin() as conn:
            conn.execute(text(f"INSERT INTO {table} ({col_sql}) VALUES ({ph_sql})"), bind_rows)


# ── Bundle comparison ────────────────────────────────────────────────────


def _iter_figures(obj: Any, prefix: str = ""):
    """Yield (path, value) for every leaf in a bundle-shaped object."""
    if isinstance(obj, dict):
        for k in sorted(obj.keys()):
            yield from _iter_figures(obj[k], f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_figures(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def compare_bundles(
    golden: dict,
    actual: dict,
    *,
    abs_tol: float = DEFAULT_ABS_TOL,
    rel_tol: float = DEFAULT_REL_TOL,
) -> list[FigureDiff]:
    """Compare two ReportFactsV1 bundles figure-by-figure.

    Every leaf must match: strings/dates exactly, numbers within
    ``max(abs_tol, rel_tol * |golden|)``. ``facts_sha256`` itself is
    skipped (it is *derived* from the figures; comparing leaves is
    strictly stronger and gives actionable diffs).
    """
    diffs: list[FigureDiff] = []
    golden_fig = dict(_iter_figures(golden))
    actual_fig = dict(_iter_figures(actual))

    for path in sorted(set(golden_fig) | set(actual_fig)):
        if path.endswith("facts_sha256") or path == "facts_sha256":
            continue
        if path not in golden_fig:
            diffs.append(FigureDiff(path=path, golden="<missing>", actual=actual_fig[path]))
            continue
        if path not in actual_fig:
            diffs.append(FigureDiff(path=path, golden=golden_fig[path], actual="<missing>"))
            continue
        g, a = golden_fig[path], actual_fig[path]
        if _is_num(g) and _is_num(a):
            tol = max(abs_tol, rel_tol * abs(float(g)))
            d = abs(float(a) - float(g))
            if d > tol:
                diffs.append(FigureDiff(path=path, golden=g, actual=a, abs_diff=d))
        elif g != a:
            diffs.append(FigureDiff(path=path, golden=g, actual=a))
    return diffs


# ---------------------------------------------------------------------------
# RAI held-out-season backtest
# ---------------------------------------------------------------------------


def _season_of(dt: Any) -> int | None:
    if dt is None:
        return None
    if hasattr(dt, "year"):
        return int(dt.year)
    return int(str(dt)[:4])


def _fetch_rai_races(engine: Engine, boat_id: int) -> list[dict]:
    """The exact race set compute_rai() consumes (BASIC_IRC_FILTER applied),
    returned as plain dicts so holdout subsets can be replayed in-process."""
    query = text(f"""
        SELECT
            r.event_name,
            r.race_name,
            r.event_date,
            r.place,
            r.fleet_size,
            r.rating_value,
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
        ORDER BY r.event_date ASC NULLS LAST, r.id ASC
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query, {"boat_id": boat_id}).fetchall()]


def _rai_from_races(engine: Engine, boat_id: int, races: list[dict]) -> float | None:
    """Mean per-race RAI over an explicit race subset. Same per-race math
    as ``performance.compute_rai`` (expected percentile from the TCC field
    of the event's other finishers, fallback 0.5)."""
    rai_values: list[float] = []
    for race in races:
        actual_pct = race["place"] / race["fleet_size"]
        expected_pct = _compute_expected_pct(
            engine,
            boat_id,
            race["event_name"],
            race["race_name"],
            race["event_date"],
            race["rating_value"],
        )
        if expected_pct is None:
            expected_pct = 0.5
        rai_values.append((expected_pct - actual_pct) * 100)
    if not rai_values:
        return None
    return float(np.mean(rai_values))


def backtest_rai_held_out_seasons(
    engine: Engine,
    boat_id: int,
    *,
    min_season_races: int = 3,
) -> dict:
    """Backtest RAI predictive value on held-out seasons for one boat.

    For every season with >= ``min_season_races`` races:

    * ``rai_held_out`` — RAI recomputed with that season hidden
      (stability of the in-sample estimate);
    * ``predictive_corr`` (fleet of seasons) — Spearman correlation
      between RAI computed over all *prior* seasons and the held-out
      season's own RAI (does yesterday's RAI predict tomorrow's racing?).
    """
    races = _fetch_rai_races(engine, boat_id)
    if not races:
        return {"boat_id": boat_id, "seasons": [], "error": "no eligible races"}

    boat_name = races[0]["boat_name"]
    seasons: dict[int, list[dict]] = {}
    for r in races:
        s = _season_of(r.get("event_date"))
        if s is not None:
            seasons.setdefault(s, []).append(r)

    full_rai = _rai_from_races(engine, boat_id, races)

    season_rows: list[dict] = []
    stability_gaps: list[float] = []
    prior_x: list[float] = []
    heldout_y: list[float] = []

    for season in sorted(seasons):
        held = seasons[season]
        if len(held) < min_season_races:
            continue
        rest = [r for s, rs in seasons.items() if s != season for r in rs]
        rai_held_out = _rai_from_races(engine, boat_id, rest)
        rai_season = _rai_from_races(engine, boat_id, held)

        # Predictive check: RAI from seasons *before* this one vs how the
        # boat actually rated this season.
        prior = [r for s, rs in seasons.items() if s < season for r in rs]
        rai_prior = (
            _rai_from_races(engine, boat_id, prior)
            if len(prior) >= RAI_MIN_RACES_AFTER_HOLDOUT
            else None
        )

        gap = (
            abs((full_rai or 0.0) - rai_held_out)
            if rai_held_out is not None and full_rai is not None
            else None
        )
        if gap is not None:
            stability_gaps.append(gap)
        if rai_prior is not None and rai_season is not None:
            prior_x.append(rai_prior)
            heldout_y.append(rai_season)

        season_rows.append({
            "season": season,
            "n_races": len(held),
            "rai_full_history": round(full_rai, 3) if full_rai is not None else None,
            "rai_held_out": round(rai_held_out, 3) if rai_held_out is not None else None,
            "abs_stability_gap": round(gap, 3) if gap is not None else None,
            "rai_prior_seasons": round(rai_prior, 3) if rai_prior is not None else None,
            "rai_this_season": round(rai_season, 3) if rai_season is not None else None,
        })

    predictive_corr = None
    if len(prior_x) >= 3:
        from scipy import stats as scipy_stats

        rho = scipy_stats.spearmanr(prior_x, heldout_y)
        predictive_corr = float(rho.statistic) if rho.statistic == rho.statistic else None  # NaN guard

    return {
        "boat_id": boat_id,
        "boat_name": boat_name,
        "n_races": len(races),
        "n_seasons_tested": len(season_rows),
        "rai_full_history": round(full_rai, 3) if full_rai is not None else None,
        "max_stability_gap": round(max(stability_gaps), 3) if stability_gaps else None,
        "mean_stability_gap": (
            round(float(np.mean(stability_gaps)), 3) if stability_gaps else None
        ),
        "predictive_spearman": (
            round(predictive_corr, 3) if predictive_corr is not None else None
        ),
        "seasons": season_rows,
    }


# ---------------------------------------------------------------------------
# Rating model held-out evaluation (Tier-C fleet model)
# ---------------------------------------------------------------------------


def backtest_rating_model_holdout(
    engine: Engine,
    *,
    seed: int = HOLDOUT_SEED,
    holdout_fraction: float = HOLDOUT_FRACTION,
) -> dict:
    """Deterministic holdout evaluation of the fleet-wide Tier-C model.

    80/20 split with a fixed seed; refits the same RidgeCV pipeline used
    in production and reports held-out MAE / R². This is the number CI
    watches for silent rating-model regressions.
    """
    from sklearn.linear_model import RidgeCV
    from sklearn.preprocessing import StandardScaler

    data = _reg._fetch_tier_c_data(engine)
    X, y, used_features, _ = _reg._prepare_matrix(data, _reg.TIER_C_FEATURES)
    if len(y) < _reg.MIN_BOATS_FOR_REGRESSION * 2:
        return {"error": "insufficient data for holdout", "n_boats": int(len(y))}

    rng = np.random.RandomState(seed)
    perm = rng.permutation(len(y))
    cut = int(len(y) * (1.0 - holdout_fraction))
    tr, te = perm[:cut], perm[cut:]

    scaler = StandardScaler().fit(X[tr])
    model = RidgeCV(alphas=_reg.RIDGE_ALPHAS, cv=5, scoring="r2")
    model.fit(scaler.transform(X[tr]), y[tr])
    pred = model.predict(scaler.transform(X[te]))

    mae = float(np.mean(np.abs(pred - y[te])))
    ss_res = float(np.sum((y[te] - pred) ** 2))
    ss_tot = float(np.sum((y[te] - np.mean(y[te])) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return {
        "model_tier": "C",
        "features": used_features,
        "n_train": int(len(tr)),
        "n_holdout": int(len(te)),
        "holdout_mae": round(mae, 6),
        "holdout_r2": round(r2, 4),
        "alpha": round(float(model.alpha_), 4),
        "seed": seed,
        "holdout_fraction": holdout_fraction,
    }


# ---------------------------------------------------------------------------
# Full evaluation report (the artifact Stuart reviews once)
# ---------------------------------------------------------------------------


def run_full_evaluation(
    engine: Engine,
    golden_results: list[GoldenComparison] | None = None,
) -> dict:
    """Run every backtest and assemble the SM-01-08 evaluation report."""
    from irc_data.api.services.report.facts_bundle import SCHEMA_VERSION

    report: dict[str, Any] = {
        "eval_version": SCHEMA_VERSION,
        "golden_fixtures": (
            [g.to_dict() for g in golden_results] if golden_results is not None else None
        ),
        "rai_backtests": {},
        "rating_model_holdout": None,
        "thresholds": {
            "golden_abs_tol": DEFAULT_ABS_TOL,
            "golden_rel_tol": DEFAULT_REL_TOL,
            "rai_stability_tol": RAI_STABILITY_TOL,
            "rating_model_holdout_mae_max": RATING_MODEL_HOLDOUT_MAE_MAX,
            "rating_model_holdout_r2_min": RATING_MODEL_HOLDOUT_R2_MIN,
        },
        "verdict": {},
    }

    for fb in GOLDEN_BOATS:
        try:
            report["rai_backtests"][fb.slug] = backtest_rai_held_out_seasons(engine, fb_boat_id(engine, fb))
        except Exception as e:
            logger.warning("RAI backtest failed for %s: %s", fb.slug, e)
            report["rai_backtests"][fb.slug] = {"error": str(e)}

    try:
        report["rating_model_holdout"] = backtest_rating_model_holdout(engine)
    except Exception as e:
        logger.warning("rating-model holdout failed: %s", e)
        report["rating_model_holdout"] = {"error": str(e)}

    # Verdicts
    verdict: dict[str, Any] = {}
    if golden_results is not None:
        verdict["golden_fixtures_pass"] = all(g.passed for g in golden_results)
    verdict["rai"] = {
        slug: {
            "stability_pass": (
                (rb.get("max_stability_gap") or 0.0) <= RAI_STABILITY_TOL
                if isinstance(rb, dict) and "error" not in rb
                else False
            ),
            "seasons_tested": rb.get("n_seasons_tested") if isinstance(rb, dict) else 0,
            "predictive_spearman": rb.get("predictive_spearman") if isinstance(rb, dict) else None,
        }
        for slug, rb in report["rai_backtests"].items()
    }
    ho = report["rating_model_holdout"] or {}
    verdict["rating_model"] = {
        "holdout_mae": ho.get("holdout_mae"),
        "holdout_mae_pass": (
            ho.get("holdout_mae") is not None
            and ho["holdout_mae"] <= RATING_MODEL_HOLDOUT_MAE_MAX
        ),
        "holdout_r2": ho.get("holdout_r2"),
        "holdout_r2_pass": (
            ho.get("holdout_r2") is not None
            and ho["holdout_r2"] >= RATING_MODEL_HOLDOUT_R2_MIN
        ),
    }
    report["verdict"] = verdict
    return report


def fb_boat_id(engine: Engine, fixture: FixtureBoat) -> int:
    """Resolve the scratch-DB boat id for a fixture boat."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM boats WHERE boat_name = :n AND sail_number = :s"),
            {"n": fixture.boat_name, "s": fixture.sail_number},
        ).first()
    if not row:
        raise ValueError(f"fixture boat {fixture.boat_name!r} not seeded")
    return int(row.id)
