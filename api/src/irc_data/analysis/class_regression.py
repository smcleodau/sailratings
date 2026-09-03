"""SM-01-02: Class regression engine (IRC + ORC).

The "what the data shows" table and the "where the points hide" teaser.

Scope
-----
Regression over the latest certificate per boat grouped by **design
class**, **per rating system from the start** — the two systems are
reported separately and are *never pooled*:

* **IRC** — target ``TCC``; levers are the certificate levers a buyer can
  actually change: headsail area (``hsa``), spinnaker area (``spa``),
  crew, draft, displacement, length (``lh``), declared headsail /
  spinnaker counts.  The IRC rule is *secret* — every coefficient is
  framed as "consistent with", never "caused by".
* **ORC** — targets ``GPH`` and the offshore triple number
  (``triple_low`` / ``triple_med`` / ``triple_high``); levers come from
  the ORC measurement set (``sail_area_upwind``, ``sail_area_downwind``,
  ``displacement``, ``draft``, ``stability_index``, ``dynamic_allowance``
  ...).  ORC ratings are VPP-derived, so fits are expected to be
  tighter — that is reported, separately, per system.

For every class the engine returns the ``what_the_data_shows`` table
(standardised β, raw β per display unit, R², N, class mean, smart-boat
cohort mean per lever) and the ``where_the_points_hide`` teaser
(per-boat position vs class mean and smart cohort).  Classes below the
minimum-boats / minimum-lever-variance thresholds are **withheld** — the
fixture class ``Cape 31`` is the canonical withheld example.

Reproducibility
---------------
Every run is stamped with a **dataset version**: the caller may pin an
explicit ``dataset_version`` (e.g. a promoted batch id from the DP-05-02
promotion seam), or the engine fingerprints the rows it actually
consumed (``sha256`` of per-row content hashes).  Re-running against the
same data with the same code reproduces the same dataset version and
R².  :func:`refresh_all` is the refresh entry point — run it whenever a
new dataset version is promoted.

Blocked by (landed): SM-01-01 (promoted certificate consumer view via
``irc_data.quality.gates.get_consumer_view``) and DP-04-04 (canonical
identity operations — class grouping keys off the resolved
``design_canonical``).

The module is DB-agnostic (SQLite in tests, Postgres in production) and
pure/offline by default: golden fixtures are injected row sets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Contract identifiers — pinned so API responses and tests can assert on them.
CLASS_REGRESSION_VERSION = "ClassRegressionV1"
BOAT_POSITION_VERSION = "BoatClassPositionV1"

RATING_IRC = "irc"
RATING_ORC = "orc"

# ---------------------------------------------------------------------------
# Thresholds — classes below these are withheld, never published
# ---------------------------------------------------------------------------

MIN_BOATS = 5            # minimum class size to publish a regression
MIN_FEATURE_VARIANCE = 1e-12  # constant lever carries no signal
# ORC ratings are VPP-derived → expect tighter fits; the flag surfaces that.
ORC_TIGHT_R2 = 0.8

# Canonical withheld-example fixture class ("Cape 31" below thresholds).
WITHHELD_FIXTURE_CLASS = "Cape 31"


# ---------------------------------------------------------------------------
# Lever (feature) definitions, per rating system
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeverSpec:
    """A regression lever: certificate column, display label, display unit."""

    field: str
    label: str
    unit: str          # "per 0.1m", "per m²", ...
    scale: float       # raw units per display unit (β_display = β_raw × scale)


# IRC — target TCC; levers the owner can declare/change on the certificate.
# The issue names headsail/spinnaker *area*, crew, draft and displacement.
# Declared sail *counts* (``headsails`` / ``spinnakers``) are parsed only on
# newer certificates — including them would complete-case away ~30% of every
# class, so they are deliberately left out of the lever set.
IRC_LEVERS: tuple[LeverSpec, ...] = (
    LeverSpec("hsa",          "Headsail area",        "per m²",      1.0),
    LeverSpec("spa",          "Spinnaker area",       "per m²",      1.0),
    LeverSpec("crew",         "Crew number",          "per person",  1.0),
    LeverSpec("draft",        "Draft",                "per 0.1m",    0.1),
    LeverSpec("displacement", "Displacement",         "per 100kg", 100.0),
    LeverSpec("lh",           "Hull length (LH)",     "per 0.1m",    0.1),
)
IRC_LEVER_FIELDS: tuple[str, ...] = tuple(l.field for l in IRC_LEVERS)

# ORC — targets GPH + triple number; levers from the ORC measurement set.
ORC_LEVERS: tuple[LeverSpec, ...] = (
    LeverSpec("sail_area_upwind",   "Upwind sail area",   "per m²",   1.0),
    LeverSpec("sail_area_downwind", "Downwind sail area", "per m²",   1.0),
    LeverSpec("displacement",       "Displacement",       "per 100kg", 100.0),
    LeverSpec("draft",              "Draft",              "per 0.1m",  0.1),
    LeverSpec("stability_index",    "Stability index",    "per unit",  1.0),
    LeverSpec("dynamic_allowance",  "Dynamic allowance",  "per 0.001", 0.001),
)
ORC_LEVER_FIELDS: tuple[str, ...] = tuple(l.field for l in ORC_LEVERS)

# ORC regression targets — GPH first, then the offshore triple number.
ORC_TARGETS: tuple[str, ...] = ("gph", "triple_low", "triple_med", "triple_high")

_LEVER_LOOKUP: dict[str, dict[str, LeverSpec]] = {
    RATING_IRC: {l.field: l for l in IRC_LEVERS},
    RATING_ORC: {l.field: l for l in ORC_LEVERS},
}


def levers_for(system: str) -> tuple[LeverSpec, ...]:
    if system == RATING_IRC:
        return IRC_LEVERS
    if system == RATING_ORC:
        return ORC_LEVERS
    raise ValueError(f"unknown rating system {system!r}")


# ---------------------------------------------------------------------------
# Dataset versioning
# ---------------------------------------------------------------------------


def _row_fingerprint(system: str, row: Mapping[str, Any]) -> str:
    """Content hash of one regression input row (stable across runs)."""
    if system == RATING_IRC:
        key_parts = [
            row.get("cert_number") or "",
            row.get("boat_name") or "",
            row.get("sail_number") or "",
        ]
        measure_keys = ("tcc",) + IRC_LEVER_FIELDS
    else:
        key_parts = [
            row.get("ref_no") or "",
            row.get("sail_number") or row.get("sail_no") or "",
            row.get("yacht_name") or row.get("boat_name") or "",
        ]
        measure_keys = ORC_TARGETS + ORC_LEVER_FIELDS

    payload = {k: _normalise_for_hash(row.get(k)) for k in measure_keys}
    payload["_id"] = "|".join(str(p) for p in key_parts)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _normalise_for_hash(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        # Round-trip through repr keeps float hashing stable across runs.
        return repr(round(value, 9))
    return value


def compute_dataset_version(
    system: str,
    design: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    explicit_version: str | None = None,
) -> str:
    """Fingerprint the (system, class, rows) dataset.

    An explicit ``dataset_version`` (e.g. a promoted batch id) always
    wins; otherwise the version is content-addressed from the input rows
    so the same data reproduces the same version.
    """
    if explicit_version:
        return explicit_version
    h = hashlib.sha256()
    h.update(f"{system}|{design}".encode("utf-8"))
    for fp in sorted(_row_fingerprint(system, r) for r in rows):
        h.update(fp.encode("utf-8"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


@dataclass
class LeverRow:
    """One row of the 'what the data shows' table."""

    field: str
    label: str
    unit: str
    std_beta: float        # standardised β (comparable across levers)
    beta_per_unit: float   # raw β per display unit
    class_mean: float
    smart_boat_mean: float | None
    rank: int = 0

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "label": self.label,
            "unit": self.unit,
            "std_beta": round(self.std_beta, 4),
            "beta_per_unit": round(self.beta_per_unit, 6),
            "class_mean": round(self.class_mean, 4),
            "smart_boat_mean": (
                round(self.smart_boat_mean, 4)
                if self.smart_boat_mean is not None
                else None
            ),
            "rank": self.rank,
        }


@dataclass
class BoatPosition:
    """'Where the points hide' teaser: one boat vs class & smart means."""

    boat_id: int | None
    boat_name: str | None
    sail_number: str | None
    target_value: float | None
    class_mean_target: float
    smart_boat_mean_target: float | None
    delta_vs_class_mean: float | None
    delta_vs_smart_mean: float | None
    z_score: float | None
    percentile: float | None

    def to_dict(self) -> dict:
        return {
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "target_value": (
                round(self.target_value, 4)
                if self.target_value is not None
                else None
            ),
            "class_mean_target": round(self.class_mean_target, 4),
            "smart_boat_mean_target": (
                round(self.smart_boat_mean_target, 4)
                if self.smart_boat_mean_target is not None
                else None
            ),
            "delta_vs_class_mean": (
                round(self.delta_vs_class_mean, 4)
                if self.delta_vs_class_mean is not None
                else None
            ),
            "delta_vs_smart_mean": (
                round(self.delta_vs_smart_mean, 4)
                if self.delta_vs_smart_mean is not None
                else None
            ),
            "z_score": round(self.z_score, 3) if self.z_score is not None else None,
            "percentile": (
                round(self.percentile, 1) if self.percentile is not None else None
            ),
        }


@dataclass
class ClassRegressionResult:
    """SM-01-02 output contract — one design class, one rating system."""

    version: str
    system: str                     # "irc" | "orc"
    design: str
    target: str                     # "tcc" | "gph" | "triple_low" | ...
    dataset_version: str
    n: int
    r_squared: float
    adjusted_r_squared: float
    class_mean_target: float
    smart_boat_mean_target: float | None
    levers: list[LeverRow] = field(default_factory=list)
    positions: list[BoatPosition] = field(default_factory=list)
    tight_fit: bool = False         # ORC VPP-derived fits are expected tight
    interpretation: str = ""
    generated_at: str = ""

    @property
    def withheld(self) -> bool:
        return False

    def what_the_data_shows(self) -> list[dict]:
        """The headline table, ranked by |standardised β|."""
        return [l.to_dict() for l in self.levers]

    def where_the_points_hide(self) -> list[dict]:
        """The teaser: per-boat position vs class & smart-boat means."""
        return [p.to_dict() for p in self.positions]

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "system": self.system,
            "design": self.design,
            "target": self.target,
            "dataset_version": self.dataset_version,
            "n": self.n,
            "r_squared": round(self.r_squared, 4),
            "adjusted_r_squared": round(self.adjusted_r_squared, 4),
            "class_mean_target": round(self.class_mean_target, 4),
            "smart_boat_mean_target": (
                round(self.smart_boat_mean_target, 4)
                if self.smart_boat_mean_target is not None
                else None
            ),
            "tight_fit": self.tight_fit,
            "what_the_data_shows": self.what_the_data_shows(),
            "where_the_points_hide": self.where_the_points_hide(),
            "interpretation": self.interpretation,
            "generated_at": self.generated_at,
        }


@dataclass
class WithheldClassResult:
    """Classes below the publish thresholds — withheld, never fitted."""

    version: str
    system: str
    design: str
    target: str
    dataset_version: str
    n: int
    withheld_reason: str
    generated_at: str = ""

    @property
    def withheld(self) -> bool:
        return True

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "system": self.system,
            "design": self.design,
            "target": self.target,
            "dataset_version": self.dataset_version,
            "n": self.n,
            "withheld": True,
            "withheld_reason": self.withheld_reason,
            "generated_at": self.generated_at,
        }


EngineOutcome = ClassRegressionResult | WithheldClassResult


# ---------------------------------------------------------------------------
# Pure regression core (no DB)
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _prepare_matrix(
    rows: Sequence[Mapping[str, Any]],
    lever_fields: Sequence[str],
    target: str,
) -> tuple[np.ndarray, np.ndarray, list[str], list[Mapping[str, Any]]]:
    """Build the design matrix from rows with a non-null target.

    Levers with zero variance in this class are dropped (a constant
    lever carries no signal).  Rows missing a surviving lever are dropped.
    Returns ``(X, y, used_fields, kept_rows)``.
    """
    with_target = [r for r in rows if _to_float(r.get(target)) is not None]
    if not with_target:
        return np.zeros((0, 0)), np.zeros(0), [], []

    # Drop constant levers first so they don't zero out the row set.
    used_fields: list[str] = []
    for f in lever_fields:
        vals = [_to_float(r.get(f)) for r in with_target]
        present = [v for v in vals if v is not None]
        if len(present) >= MIN_BOATS and float(np.std(present)) > MIN_FEATURE_VARIANCE:
            used_fields.append(f)

    kept: list[Mapping[str, Any]] = []
    X_rows: list[list[float]] = []
    y_vals: list[float] = []
    for r in with_target:
        vec: list[float] = []
        ok = True
        for f in used_fields:
            v = _to_float(r.get(f))
            if v is None:
                ok = False
                break
            vec.append(v)
        if not ok:
            continue
        kept.append(r)
        X_rows.append(vec)
        y_vals.append(float(_to_float(r.get(target))))

    X = np.array(X_rows, dtype=float) if X_rows else np.zeros((0, len(used_fields)))
    y = np.array(y_vals, dtype=float)

    # Re-check lever variance on the *kept* rows: after row filtering a
    # lever can collapse to a constant, which would divide-by-zero in the
    # standardiser.  Drop degenerate columns and rebuild.
    if len(y) and X.shape[1]:
        degenerate = [j for j in range(X.shape[1]) if X[:, j].std(ddof=1) <= MIN_FEATURE_VARIANCE]
        if degenerate:
            keep_idx = [j for j in range(X.shape[1]) if j not in degenerate]
            X = X[:, keep_idx] if keep_idx else np.zeros((len(y), 0))
            used_fields = [used_fields[j] for j in keep_idx]

    return X, y, used_fields, kept


def _fit_ols_standardised(
    X: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """Ordinary least squares with fully standardised coefficients.

    Both the levers *and* the target are standardised (sample means /
    standard deviations, ddof=1) before fitting, so the returned
    ``std_betas`` are the conventional standardised regression
    coefficients: a one-standard-deviation move in lever *j* is
    associated with a ``std_beta[j]`` standard-deviation move in the
    target.  This makes levers directly comparable across the table.

    Returns ``(std_betas, raw_betas, y_pred, r_squared, adjusted_r_squared)``
    where ``raw_betas`` are in raw target units per raw lever unit and
    ``y_pred`` is on the raw target scale.
    """
    n, k = X.shape
    x_mean = X.mean(axis=0)
    x_std = X.std(axis=0, ddof=1)
    # Numerical guard: a degenerate column should have been dropped by
    # ``_prepare_matrix``; never divide by a zero standard deviation.
    x_std = np.where(x_std <= MIN_FEATURE_VARIANCE, 1.0, x_std)
    y_mean = float(y.mean())
    y_std = float(y.std(ddof=1))

    Z = (X - x_mean) / x_std
    y_z = (y - y_mean) / y_std
    # Least squares with intercept: append a ones column.
    A = np.column_stack([np.ones(n), Z])
    coef, *_ = np.linalg.lstsq(A, y_z, rcond=None)
    std_betas = coef[1:]

    # Back to raw scale: raw β = std β · s_y / s_x.
    raw_betas = std_betas * y_std / x_std
    y_pred = y_mean + Z @ (std_betas * y_std)

    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y_mean) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if n - k - 1 > 0:
        adjusted = 1.0 - (1.0 - r_squared) * (n - 1) / (n - k - 1)
    else:
        adjusted = r_squared

    return std_betas, raw_betas, y_pred, r_squared, adjusted


def _smart_cohort(
    kept_rows: Sequence[Mapping[str, Any]], target: str
) -> list[Mapping[str, Any]]:
    """Smart-boat cohort = lowest-target quintile of the class (min 1)."""
    if not kept_rows:
        return []
    ordered = sorted(kept_rows, key=lambda r: float(_to_float(r.get(target))))
    n_top = max(1, int(math.ceil(len(ordered) * 0.2)))
    return ordered[:n_top]


def _means_for(
    rows: Sequence[Mapping[str, Any]], fields: Iterable[str]
) -> dict[str, float]:
    means: dict[str, float] = {}
    for f in fields:
        vals = [_to_float(r.get(f)) for r in rows]
        present = [v for v in vals if v is not None]
        if present:
            means[f] = float(np.mean(present))
    return means


def regress_class_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    system: str,
    design: str,
    target: str,
    dataset_version: str | None = None,
    boat_index: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> EngineOutcome:
    """Pure per-class regression over an injected row set.

    ``rows`` are the latest-promoted certificate rows for one design
    class, one rating system.  IRC rows carry ``tcc`` + the IRC lever
    fields; ORC rows carry the ORC targets + ORC lever fields.  Systems
    are never pooled — callers pass one system at a time.

    Classes below :data:`MIN_BOATS`, or whose target has no variance,
    return a :class:`WithheldClassResult`.
    """
    lever_specs = levers_for(system)
    lever_fields = [l.field for l in lever_specs]
    stamp = generated_at or datetime.utcnow().isoformat(timespec="seconds") + "Z"
    ds_version = compute_dataset_version(system, design, rows, explicit_version=dataset_version)

    def _withheld(reason: str) -> WithheldClassResult:
        return WithheldClassResult(
            version=CLASS_REGRESSION_VERSION,
            system=system,
            design=design,
            target=target,
            dataset_version=ds_version,
            n=len(rows),
            withheld_reason=reason,
            generated_at=stamp,
        )

    if len(rows) < MIN_BOATS:
        return _withheld(
            f"below threshold: N={len(rows)} < MIN_BOATS={MIN_BOATS}"
        )

    X, y, used_fields, kept = _prepare_matrix(rows, lever_fields, target)
    if len(y) < MIN_BOATS:
        return _withheld(
            f"below threshold: N={len(y)} boats with {target} + levers "
            f"< MIN_BOATS={MIN_BOATS}"
        )
    if not used_fields:
        return _withheld("no lever variance within class")
    if float(np.std(y, ddof=1)) <= MIN_FEATURE_VARIANCE:
        return _withheld(f"target {target} is constant within class")

    std_betas, raw_betas, _y_pred, r2, r2_adj = _fit_ols_standardised(X, y)

    class_means = _means_for(kept, used_fields)
    smart_rows = _smart_cohort(kept, target)
    smart_means = _means_for(smart_rows, used_fields)

    lookup = _LEVER_LOOKUP[system]
    order = sorted(
        range(len(used_fields)), key=lambda i: abs(std_betas[i]), reverse=True
    )
    levers: list[LeverRow] = []
    for rank, i in enumerate(order, start=1):
        f = used_fields[i]
        spec = lookup[f]
        levers.append(
            LeverRow(
                field=f,
                label=spec.label,
                unit=spec.unit,
                std_beta=float(std_betas[i]),
                beta_per_unit=float(raw_betas[i]) * spec.scale,
                class_mean=class_means.get(f, 0.0),
                smart_boat_mean=smart_means.get(f),
                rank=rank,
            )
        )

    # Per-boat position vs class mean and smart-boat cohort.
    y_mean = float(y.mean())
    y_std = float(y.std(ddof=1)) if len(y) > 1 else 0.0
    smart_target_mean = float(
        np.mean([_to_float(r.get(target)) for r in smart_rows])
    )
    positions: list[BoatPosition] = []
    for r in kept:
        tv = _to_float(r.get(target))
        below = sum(1 for v in y if v < tv)
        pct = 100.0 * below / len(y) if len(y) else None
        positions.append(
            BoatPosition(
                boat_id=_boat_id_for(r, boat_index),
                boat_name=r.get("boat_name") or r.get("yacht_name"),
                sail_number=r.get("sail_number") or r.get("sail_no"),
                target_value=tv,
                class_mean_target=y_mean,
                smart_boat_mean_target=smart_target_mean,
                delta_vs_class_mean=(tv - y_mean) if tv is not None else None,
                delta_vs_smart_mean=(
                    (tv - smart_target_mean) if tv is not None else None
                ),
                z_score=((tv - y_mean) / y_std) if y_std > 0 else None,
                percentile=pct,
            )
        )

    if system == RATING_IRC:
        interpretation = (
            f"Within-class association of certificate levers with TCC for "
            f"{design} (N={len(y)}, R²={r2:.2f}). The IRC rule is secret — "
            f"coefficients are 'consistent with' the published rule, not "
            f"caused by it."
        )
        tight = False
    else:
        interpretation = (
            f"Within-class association of ORC measurement levers with "
            f"{target.upper()} for {design} (N={len(y)}, R²={r2:.2f}). ORC "
            f"ratings are VPP-derived — expect tighter fits than IRC. "
            f"Reported separately; never pooled with IRC."
        )
        tight = r2 >= ORC_TIGHT_R2

    return ClassRegressionResult(
        version=CLASS_REGRESSION_VERSION,
        system=system,
        design=design,
        target=target,
        dataset_version=ds_version,
        n=len(y),
        r_squared=r2,
        adjusted_r_squared=r2_adj,
        class_mean_target=y_mean,
        smart_boat_mean_target=smart_target_mean,
        levers=levers,
        positions=positions,
        tight_fit=tight,
        interpretation=interpretation,
        generated_at=stamp,
    )


def _boat_id_for(
    row: Mapping[str, Any], boat_index: Mapping[str, Any] | None
) -> int | None:
    if row.get("boat_id") is not None:
        try:
            return int(row["boat_id"])
        except (TypeError, ValueError):
            return None
    if not boat_index:
        return None
    key = row.get("cert_number") or row.get("ref_no") or row.get("sail_number")
    found = boat_index.get(key) if key is not None else None
    return int(found) if found is not None else None


# ---------------------------------------------------------------------------
# DB row extraction (latest promoted certificate per boat, per class)
# ---------------------------------------------------------------------------


def _fetch_irc_rows(engine: Engine, design: str) -> list[dict]:
    """Latest IRC certificate + TCC per boat in the class."""
    query = text("""
        SELECT
            b.id AS boat_id, b.boat_name, b.sail_number,
            c.cert_number,
            t.tcc,
            c.hsa, c.spa, c.draft, c.displacement_kg AS displacement, c.lh,
            t.crew
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC LIMIT 1
        ) t ON true
        JOIN LATERAL (
            SELECT * FROM irc_certificates
            WHERE boat_id = b.id
            ORDER BY issue_date DESC NULLS LAST LIMIT 1
        ) c ON true
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND t.tcc IS NOT NULL
          AND c.id IS NOT NULL
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query, {"design": design})]


def _fetch_orc_rows(engine: Engine, design: str) -> list[dict]:
    """Latest ORC certificate per boat in the class."""
    query = text("""
        SELECT DISTINCT ON (o.boat_id)
            o.boat_id AS boat_id,
            b.boat_name, b.sail_number,
            o.ref_no, o.yacht_name, o.sail_no,
            o.gph, o.triple_low, o.triple_med, o.triple_high,
            o.sail_area_upwind, o.sail_area_downwind,
            o.displacement, o.draft, o.stability_index, o.dynamic_allowance
        FROM orc_certificates o
        JOIN boats b ON b.id = o.boat_id
        WHERE COALESCE(b.design_canonical, b.design) = :design
          AND o.gph IS NOT NULL
        ORDER BY o.boat_id, o.snapshot_date DESC
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(query, {"design": design})]


def list_classes(engine: Engine, system: str, min_boats: int = MIN_BOATS) -> list[str]:
    """Design classes with at least ``min_boats`` boats for the system."""
    if system == RATING_IRC:
        query = text("""
            SELECT COALESCE(b.design_canonical, b.design) AS d, COUNT(*) AS n
            FROM boats b
            JOIN tcc_snapshots t ON t.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
            GROUP BY 1 HAVING COUNT(*) >= :n ORDER BY 1
        """)
    else:
        query = text("""
            SELECT COALESCE(b.design_canonical, b.design) AS d,
                   COUNT(DISTINCT o.boat_id) AS n
            FROM boats b
            JOIN orc_certificates o ON o.boat_id = b.id
            WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
            GROUP BY 1 HAVING COUNT(DISTINCT o.boat_id) >= :n ORDER BY 1
        """)
    with engine.connect() as conn:
        return [r.d for r in conn.execute(query, {"n": min_boats})]


# ---------------------------------------------------------------------------
# Public engine API
# ---------------------------------------------------------------------------


def run_class_regression(
    engine: Engine,
    design: str,
    system: str,
    target: str | None = None,
    *,
    dataset_version: str | None = None,
) -> EngineOutcome:
    """Run the SM-01-02 class regression for one (design, system).

    * IRC → target ``tcc`` (only target).
    * ORC → target ``gph`` by default; ``triple_low`` / ``triple_med`` /
      ``triple_high`` selectable.  ORC results are always produced
      separately — never pooled with IRC.
    """
    if system == RATING_IRC:
        tgt = target or "tcc"
        if tgt != "tcc":
            raise ValueError(f"IRC regression target must be 'tcc', got {tgt!r}")
        rows = _fetch_irc_rows(engine, design)
    elif system == RATING_ORC:
        tgt = target or "gph"
        if tgt not in ORC_TARGETS:
            raise ValueError(
                f"ORC regression target must be one of {ORC_TARGETS}, got {tgt!r}"
            )
        rows = _fetch_orc_rows(engine, design)
    else:
        raise ValueError(f"unknown rating system {system!r}")

    return regress_class_rows(
        rows,
        system=system,
        design=design,
        target=tgt,
        dataset_version=dataset_version,
    )


def run_class_all_targets(
    engine: Engine, design: str, system: str, *, dataset_version: str | None = None
) -> list[EngineOutcome]:
    """IRC → one result (TCC).  ORC → GPH + each triple-number target."""
    if system == RATING_IRC:
        return [run_class_regression(engine, design, system, dataset_version=dataset_version)]
    return [
        run_class_regression(engine, design, system, target=t, dataset_version=dataset_version)
        for t in ORC_TARGETS
    ]


def get_boat_class_position(
    engine: Engine,
    boat_id: int,
    system: str,
    target: str | None = None,
    *,
    dataset_version: str | None = None,
) -> dict | None:
    """'Where the points hide' teaser for one boat.

    Returns the boat's position vs its class mean and smart-boat cohort
    for the given rating system.  Dual-rated boats resolve both systems —
    call once per system.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, boat_name, sail_number, "
                "COALESCE(design_canonical, design) AS design "
                "FROM boats WHERE id = :id"
            ),
            {"id": boat_id},
        ).first()
    if not row:
        return None

    result = run_class_regression(
        engine, row.design, system, target, dataset_version=dataset_version
    )
    if isinstance(result, WithheldClassResult):
        return {
            "version": BOAT_POSITION_VERSION,
            "boat_id": boat_id,
            "boat_name": row.boat_name,
            "design": row.design,
            "system": system,
            "withheld": True,
            "withheld_reason": result.withheld_reason,
        }

    pos = next((p for p in result.positions if p.boat_id == boat_id), None)
    if pos is None:
        return None
    out = result.to_dict()
    out["this_boat"] = pos.to_dict()
    return out


def refresh_all(
    engine: Engine,
    *,
    dataset_version: str | None = None,
    min_boats: int = MIN_BOATS,
) -> dict[str, list[dict]]:
    """Refresh the engine for a new dataset version.

    Iterates every eligible class in **both** rating systems (separately,
    never pooled) and returns ``{"irc": [...], "orc": [...]}`` result
    dicts.  Call whenever a new dataset version is promoted; stamping the
    same ``dataset_version`` on every run keeps the outputs reproducible.
    """
    out: dict[str, list[dict]] = {RATING_IRC: [], RATING_ORC: []}
    for system in (RATING_IRC, RATING_ORC):
        for design in list_classes(engine, system, min_boats=min_boats):
            try:
                for res in run_class_all_targets(
                    engine, design, system, dataset_version=dataset_version
                ):
                    out[system].append(res.to_dict())
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("class regression failed for %s/%s: %s", system, design, exc)
    return out


__all__ = [
    "BOAT_POSITION_VERSION",
    "CLASS_REGRESSION_VERSION",
    "IRC_LEVERS",
    "IRC_LEVER_FIELDS",
    "MIN_BOATS",
    "ORC_LEVERS",
    "ORC_LEVER_FIELDS",
    "ORC_TIGHT_R2",
    "ORC_TARGETS",
    "RATING_IRC",
    "RATING_ORC",
    "WITHHELD_FIXTURE_CLASS",
    "BoatPosition",
    "ClassRegressionResult",
    "LeverRow",
    "LeverSpec",
    "WithheldClassResult",
    "compute_dataset_version",
    "get_boat_class_position",
    "levers_for",
    "list_classes",
    "refresh_all",
    "regress_class_rows",
    "run_class_all_targets",
    "run_class_regression",
]
