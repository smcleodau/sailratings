"""IN-01-03 - ConfidenceAssessmentV1 (SPEC-25 §3).

``assess(inputs)`` is a pure function: the same :class:`ConfidenceInputs` always
give the same assessment. Five factors with fixed weights (SPEC-25 §3.1):

========================  ======  =========================================
factor                    weight  value (0..1)
========================  ======  =========================================
coverage                  0.25    seasons with results / 3, capped at 1
source_agreement          0.20    fraction of TCC readings within 0.001 of the latest
identity_certainty        0.20    1 - (boat in a PENDING dupe cluster)
recency                   0.20    1 - days since latest fact / 365, floored at 0
model_evidence            0.15    0.5 * N-score + 0.5 * R²-score (METHODOLOGY.md thresholds)
========================  ======  =========================================

Bands: ``high`` >= 0.75, ``medium`` >= 0.45, else ``low``. Every factor value
below 0.5 adds a named limitation. ``inputs_for_result`` gathers the inputs for
an answer; anything it cannot read is ``None`` and scores 0 (never guessed).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.schemas.intelligence import ConfidenceAssessmentV1, ConfidenceFactor
from irc_data.api.services.intelligence import evidence as ev

# --- SPEC-25 §3.1: fixed weights (Human Gate: Stuart approves) -------------
WEIGHTS: dict[str, float] = {
    "coverage": 0.25,
    "source_agreement": 0.20,
    "identity_certainty": 0.20,
    "recency": 0.20,
    "model_evidence": 0.15,
}

# --- SPEC-25 §3.2: band thresholds ------------------------------------------
HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.45
LIMITATION_BELOW = 0.5

# --- factor parameters --------------------------------------------------------
COVERAGE_SEASONS = 3          # seasons with results for full coverage (SPEC-25 §1.4)
TCC_AGREEMENT_TOLERANCE = 0.001
SINGLE_SOURCE_VALUE = 0.5     # one TCC reading cannot be cross-checked
RECENCY_DAYS = 365
# docs/model/METHODOLOGY.md (class regression thresholds)
MODEL_MIN_N = 5               # MIN_BOATS: below this no regression is published
MODEL_FULL_N = 15             # MIN_BOATS_FULL_CV: below this "limited data"
MODEL_MIN_R2 = 0.3            # Tier A in-sample R² floor


@dataclass(frozen=True)
class ConfidenceInputs:
    """Everything ``assess`` needs. ``None`` means "could not be read"."""

    seasons_with_results: int | None = None
    # TCC readings of the boat's latest certificate year, oldest -> latest.
    tcc_readings: Sequence[float] | None = None
    pending_dupe_cluster: bool | None = None
    days_since_latest_fact: float | None = None
    model_r_squared: float | None = None
    model_n: int | None = None


def band_for(score: float) -> str:
    if score >= HIGH_THRESHOLD:
        return "high"
    if score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _coverage(i: ConfidenceInputs) -> tuple[float, str]:
    if i.seasons_with_results is None:
        return 0.0, "season coverage could not be read"
    n = max(0, i.seasons_with_results)
    return _clip(n / COVERAGE_SEASONS), f"{n} season(s) with results of {COVERAGE_SEASONS} needed"


def _agreement(i: ConfidenceInputs) -> tuple[float, str]:
    readings = list(i.tcc_readings or [])
    if i.tcc_readings is None or not readings:
        return 0.0, "no TCC reading available"
    if len(readings) == 1:
        return SINGLE_SOURCE_VALUE, "single TCC reading, not cross-checked"
    latest = readings[-1]
    agree = sum(1 for r in readings if abs(r - latest) <= TCC_AGREEMENT_TOLERANCE + 1e-9)
    return agree / len(readings), (
        f"{agree} of {len(readings)} TCC readings within {TCC_AGREEMENT_TOLERANCE} of the latest"
    )


def _identity(i: ConfidenceInputs) -> tuple[float, str]:
    if i.pending_dupe_cluster is None:
        return 0.0, "identity status not assessed for this query"
    if i.pending_dupe_cluster:
        return 0.0, "boat is in a pending duplicate-review cluster"
    return 1.0, "no pending duplicate cluster"


def _recency(i: ConfidenceInputs) -> tuple[float, str]:
    if i.days_since_latest_fact is None:
        return 0.0, "no dated fact available"
    days = max(0.0, float(i.days_since_latest_fact))
    return _clip(1.0 - days / RECENCY_DAYS), f"latest fact {int(days)} day(s) old against {RECENCY_DAYS}"


def _model(i: ConfidenceInputs) -> tuple[float, str]:
    if i.model_n is None and i.model_r_squared is None:
        return 0.0, "no class model behind this answer"
    n = i.model_n or 0
    n_score = 0.0 if n < MODEL_MIN_N else (0.5 if n < MODEL_FULL_N else 1.0)
    r2 = i.model_r_squared
    r2_score = 1.0 if (r2 is not None and r2 >= MODEL_MIN_R2) else 0.0
    r2_txt = "n/a" if r2 is None else f"{r2:.2f}"
    return 0.5 * n_score + 0.5 * r2_score, (
        f"class model N={n} (min {MODEL_MIN_N}, full {MODEL_FULL_N}), R²={r2_txt} (min {MODEL_MIN_R2})"
    )


_FACTORS = (
    ("coverage", _coverage, "Thin coverage"),
    ("source_agreement", _agreement, "Sources disagree or are uncorroborated"),
    ("identity_certainty", _identity, "Identity uncertain"),
    ("recency", _recency, "Stale data"),
    ("model_evidence", _model, "Weak model evidence"),
)


def assess(inputs: ConfidenceInputs) -> ConfidenceAssessmentV1:
    """Deterministic confidence for ``inputs`` (SPEC-25 §3)."""
    factors: list[ConfidenceFactor] = []
    limitations: list[str] = []
    score = 0.0
    for name, fn, label in _FACTORS:
        value, note = fn(inputs)
        value = round(value, 6)
        factors.append(ConfidenceFactor(name=name, weight=WEIGHTS[name], value=value, note=note))
        score += WEIGHTS[name] * value
        if value < LIMITATION_BELOW:
            limitations.append(f"{label} ({name}={value:.2f}): {note}")
    score = round(_clip(score), 6)
    return ConfidenceAssessmentV1(
        score=score, band=band_for(score), factors=factors, limitations=limitations
    )


# ---------------------------------------------------------------------------
# Gathering inputs for an answer
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, sql: str, params: dict[str, Any]) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _boat_id(data: dict[str, Any]) -> int | None:
    raw = data.get("id")
    if raw is None and isinstance(data.get("boat"), dict):
        raw = data["boat"].get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _model_from(data: dict[str, Any]) -> tuple[float | None, int | None]:
    engines = data.get("engines") if isinstance(data.get("engines"), dict) else {}
    for key in ("design_model", "fleet_wide_model"):
        m = engines.get(key)
        if isinstance(m, dict) and "_error" not in m:
            r2 = m.get("r_squared_cv")
            if r2 is None:
                r2 = m.get("r_squared")
            return (float(r2) if r2 is not None else None), m.get("n_boats")
    return None, None


def inputs_for_result(engine: Engine, result: Any, now: datetime | None = None) -> ConfidenceInputs:
    """Read the confidence inputs for a ``QueryResult``.

    Boat-scoped answers (``boat_profile`` / ``report_facts``) read seasons, TCC
    readings and duplicate status from the database; every database read is
    best-effort (a missing table or row leaves the input ``None``, except that
    a database without ``dupe_review_queue`` means no pending cluster).
    """
    now = now or datetime.now(timezone.utc)
    data = result.data if isinstance(result.data, dict) else {}
    boat_id = _boat_id(data)
    latest = ev.to_datetime(result.freshness_at)
    seasons = readings = pending = None

    if boat_id is not None:
        p = {"i": boat_id}
        try:
            seasons = int(_scalar(
                engine,
                "SELECT count(DISTINCT substr(CAST(event_date AS TEXT), 1, 4)) "
                "FROM race_results WHERE boat_id = :i AND event_date IS NOT NULL", p) or 0)
            last_race = _scalar(engine, "SELECT max(event_date) FROM race_results WHERE boat_id = :i", p)
            latest = ev.latest(latest, last_race)
        except Exception:
            pass
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT tcc, snapshot_date, cert_year FROM tcc_snapshots "
                    "WHERE boat_id = :i ORDER BY snapshot_date"), p).fetchall()
            if rows:
                year = rows[-1][2]
                use = [r for r in rows if year is None or r[2] == year]
                readings = [float(r[0]) for r in use]
            else:
                readings = []
            latest = ev.latest(latest, rows[-1][1] if rows else None)
        except Exception:
            pass
        try:
            pending = bool(_scalar(
                engine,
                "SELECT count(*) FROM dupe_review_queue WHERE boat_id = :i AND verdict = 'PENDING'", p))
        except Exception:
            pending = False  # no review queue -> no pending cluster (cf. quarantine, §2.3)

    r2, n = _model_from(data)
    days = (now - latest).total_seconds() / 86400.0 if latest else None
    return ConfidenceInputs(
        seasons_with_results=seasons,
        tcc_readings=readings,
        pending_dupe_cluster=pending,
        days_since_latest_fact=days,
        model_r_squared=r2,
        model_n=n,
    )


def assess_result(engine: Engine, result: Any) -> ConfidenceAssessmentV1:
    return assess(inputs_for_result(engine, result))
