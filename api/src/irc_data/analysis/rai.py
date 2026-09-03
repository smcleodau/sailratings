"""SM-01-03: Racing Advantage Index (RAI) with confidence intervals.

Goal: *"Is this boat out-performing her rating?"* as a calibrated index.

For every race, a boat's **advantage observation** is::

    A_race = (expected_finish_pct − actual_finish_pct) × 100

* ``actual_finish_pct = place / fleet_size`` on **corrected** results (the
  official published placing already reflects corrected time; the shared
  ``BASIC_IRC_FILTER`` excludes twilight/non-IRC/non-spinnaker pollution).
* ``expected_finish_pct = TCC rank among the race field / field size`` —
  the percentile the boat *should* finish at if the rating were perfectly
  fair.  In IRC, a **lower** TCC means the boat is owed time by the fleet,
  so the *lowest*-rated boat is expected to win: ``rank = 1 + #(field TCC
  strictly below boat TCC)``.  The field is the **distinct ratings in the
  race** (one per identity, not one per result row), so the expectation is
  insensitive to duplicate or wrongly-merged result rows — an
  identity-merge error then shows up *only* in the boat's own actual
  finishes, which is exactly what the merge-sensitivity verification
  measures.

Positive RAI ⇒ consistently beating the rating.  The per-boat index is the
mean of the per-race observations with a **bootstrap-t 95 % confidence
interval** (percentile-bootstrap fallback when the standard error is ~0),
plus a **condition split by true-wind-speed band** wherever a source payload
carries wind data, and **class baselines** (mean/median RAI per design
class).

Contract properties (enforced, not conventional):

1. **Reproducible per dataset version.**  The computation is a pure
   function of the input rows + the versioned config
   (``RAIRulesetConfigV1``).  Every output carries
   ``dataset_fingerprint`` — a stable hash over the sorted input row
   identities — and ``config_fingerprint`` — a stable hash over the config
   — so two runs on the same dataset version are bit-identical and two runs
   on different versions are provably different.
2. **Minimum-race threshold enforced.**  Boats with fewer than
   ``min_races`` usable races return ``status="insufficient_data"`` with
   ``rai=None``; they are *excluded* from class baselines.  Bands with fewer
   than ``min_band_races`` races are reported with
   ``status="insufficient_data"`` — never silently pooled.
3. **Identity-resolution sensitive by construction.**  RAI is computed per
   *resolved* ``boat_id`` (SM-01-01 / DP-04-04 output); the per-race
   contribution list is preserved on the result so a merge/split audit can
   attribute every point to a source race.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

import numpy as np
from scipy import stats as scipy_stats
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Contract identifiers / defaults
# ---------------------------------------------------------------------------

RAI_SCHEMA_VERSION = "RAIComputationV1"
RAI_CONFIG_SCHEMA = "rai-config-v1"

DEFAULT_MIN_RACES = 5
"""Minimum usable races before a per-boat RAI is reported at all."""

DEFAULT_MIN_BAND_RACES = 3
"""Minimum races inside one TWS band before that band split is reported."""

DEFAULT_BOOTSTRAP_RESAMPLES = 2000
"""Bootstrap resamples for the confidence interval (fixed seed ⇒ reproducible)."""

BOOTSTRAP_SEED = 0x5A11
"""Fixed RNG seed — the CI is a deterministic function of the input data."""

DEFAULT_CONFIDENCE_LEVEL = 0.95

# TWS band edges in knots: [lo, hi) — hi=None means unbounded above.
# Band ids are stable strings; changing the edges ships rai-config-v2.
TWS_BANDS: tuple[tuple[str, str, float | None, float | None], ...] = (
    ("light", "0–8 kn", 0.0, 8.0),
    ("medium", "8–14 kn", 8.0, 14.0),
    ("fresh", "14–20 kn", 14.0, 20.0),
    ("heavy", "20+ kn", 20.0, None),
)

# raw_data keys scanned for a true-wind-speed reading, in priority order.
# Source payloads are not standardised, so the extractor is deliberately
# liberal; absence is preserved (the race simply has no band split).
_TWS_KEYS = (
    "tws",
    "tws_kt",
    "tws_kts",
    "tws_knots",
    "true_wind_speed",
    "wind_speed",
    "wind_speed_kt",
    "wind_kts",
    "wind",
    "wind_kt",
)

# Status values on the output contract.
STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient_data"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RAIRulesetConfigV1:
    """Versioned configuration for the RAI computation.

    The config is part of the output contract: every ``RAIResultV1`` carries
    the config's fingerprint, so a run is fully described by
    ``(dataset_fingerprint, config_fingerprint)``.
    """

    schema: str = RAI_CONFIG_SCHEMA
    min_races: int = DEFAULT_MIN_RACES
    min_band_races: int = DEFAULT_MIN_BAND_RACES
    bootstrap_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES
    bootstrap_seed: int = BOOTSTRAP_SEED
    confidence_level: float = DEFAULT_CONFIDENCE_LEVEL
    tws_bands: tuple[tuple[str, str, float | None, float | None], ...] = TWS_BANDS

    def fingerprint(self) -> str:
        """Stable content hash of the config (16 hex chars)."""
        payload = {
            "schema": self.schema,
            "min_races": self.min_races,
            "min_band_races": self.min_band_races,
            "bootstrap_resamples": self.bootstrap_resamples,
            "bootstrap_seed": self.bootstrap_seed,
            "confidence_level": self.confidence_level,
            "tws_bands": [list(b) for b in self.tws_bands],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]


DEFAULT_CONFIG = RAIRulesetConfigV1()


# ---------------------------------------------------------------------------
# Input layer (pure — no database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RaceObservation:
    """One race's RAI input.  ``raw`` is the source payload (may carry TWS)."""

    boat_id: int
    event_name: str
    race_name: str | None
    event_date: str | None
    place: int
    fleet_size: int
    rating_value: float
    raw: dict | None = None

    def observation_key(self) -> str:
        """Stable identity of this observation for fingerprinting."""
        return "|".join(
            [
                str(self.boat_id),
                self.event_name or "",
                self.race_name or "",
                self.event_date or "",
                str(self.place),
                str(self.fleet_size),
                f"{self.rating_value:.4f}",
            ]
        )


@dataclass(frozen=True)
class BoatInfo:
    boat_id: int
    boat_name: str = ""
    sail_number: str = ""
    design: str | None = None


@dataclass(frozen=True)
class FleetRatingSet:
    """The rating field of one race — used to compute expected percentiles."""

    event_name: str
    race_name: str | None
    event_date: str | None
    ratings: tuple[float, ...]

    def field_key(self) -> tuple[str, str | None, str | None]:
        return (self.event_name, self.race_name, self.event_date)


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BandSplitV1:
    """RAI inside one TWS band (a *condition split*)."""

    band: str
    band_label: str
    n_races: int
    rai: float | None  # None when below min_band_races
    ci_lower: float | None
    ci_upper: float | None
    status: str

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "band_label": self.band_label,
            "n_races": self.n_races,
            "rai": round(self.rai, 2) if self.rai is not None else None,
            "ci_lower": round(self.ci_lower, 2) if self.ci_lower is not None else None,
            "ci_upper": round(self.ci_upper, 2) if self.ci_upper is not None else None,
            "status": self.status,
        }


@dataclass(frozen=True)
class RAIResultV1:
    """Per-boat Racing Advantage Index with confidence interval."""

    schema: str
    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None
    status: str  # "ok" | "insufficient_data"
    rai: float | None
    ci_lower: float | None
    ci_upper: float | None
    ci_method: str
    confidence_level: float
    n_races: int
    n_scored: int  # races with a usable expected percentile
    avg_finish_pct: float | None
    avg_expected_pct: float | None
    wins: int
    podiums: int
    meets_min_races: bool
    min_races_required: int
    condition_splits: tuple[BandSplitV1, ...]
    n_wind_observed: int  # races whose payload carried TWS
    dataset_fingerprint: str
    config_fingerprint: str
    race_contributions: tuple[dict, ...]
    interpretation: str

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "boat_id": self.boat_id,
            "boat_name": self.boat_name,
            "sail_number": self.sail_number,
            "design": self.design,
            "status": self.status,
            "rai": round(self.rai, 2) if self.rai is not None else None,
            "ci_lower": round(self.ci_lower, 2) if self.ci_lower is not None else None,
            "ci_upper": round(self.ci_upper, 2) if self.ci_upper is not None else None,
            "ci_method": self.ci_method,
            "confidence_level": self.confidence_level,
            "n_races": self.n_races,
            "n_scored": self.n_scored,
            "avg_finish_pct": round(self.avg_finish_pct, 4)
            if self.avg_finish_pct is not None
            else None,
            "avg_expected_pct": round(self.avg_expected_pct, 4)
            if self.avg_expected_pct is not None
            else None,
            "wins": self.wins,
            "podiums": self.podiums,
            "meets_min_races": self.meets_min_races,
            "min_races_required": self.min_races_required,
            "condition_splits": [s.to_dict() for s in self.condition_splits],
            "n_wind_observed": self.n_wind_observed,
            "dataset_fingerprint": self.dataset_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "race_contributions": [dict(c) for c in self.race_contributions],
            "interpretation": self.interpretation,
        }


@dataclass(frozen=True)
class ClassBaselineV1:
    """Class (design) baseline RAI across threshold-passing boats."""

    schema: str
    design: str
    n_boats: int  # boats meeting the min-race threshold
    n_boats_total: int  # boats with any usable race
    mean_rai: float | None
    median_rai: float | None
    std_rai: float | None
    p25_rai: float | None
    p75_rai: float | None
    min_races_required: int
    dataset_fingerprint: str
    config_fingerprint: str

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "design": self.design,
            "n_boats": self.n_boats,
            "n_boats_total": self.n_boats_total,
            "mean_rai": round(self.mean_rai, 2) if self.mean_rai is not None else None,
            "median_rai": round(self.median_rai, 2) if self.median_rai is not None else None,
            "std_rai": round(self.std_rai, 2) if self.std_rai is not None else None,
            "p25_rai": round(self.p25_rai, 2) if self.p25_rai is not None else None,
            "p75_rai": round(self.p75_rai, 2) if self.p75_rai is not None else None,
            "min_races_required": self.min_races_required,
            "dataset_fingerprint": self.dataset_fingerprint,
            "config_fingerprint": self.config_fingerprint,
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _to_float(value) -> float | None:
    """Best-effort numeric coercion (Decimal / numeric strings included)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower()
        for suffix in ("knots", "knot", "kts", "kt", "kn"):
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                break
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract_tws(raw: dict | None) -> float | None:
    """Extract a true-wind-speed reading (knots) from a source payload.

    Returns ``None`` when no recognised key carries a parseable value —
    absence is preserved, never imputed.  Values ≤ 0 or > 60 kn are treated
    as sensor noise and discarded.
    """
    if not raw or not isinstance(raw, dict):
        return None
    for key in _TWS_KEYS:
        if key in raw:
            val = _to_float(raw.get(key))
            if val is not None and 0.0 < val <= 60.0:
                return val
    return None


def tws_band_for(
    tws: float | None,
    bands: tuple[tuple[str, str, float | None, float | None], ...] = TWS_BANDS,
) -> str | None:
    """Map a TWS reading onto a band id; ``None`` when no wind data exists."""
    if tws is None:
        return None
    for band_id, _label, lo, hi in bands:
        lo_ok = lo is None or tws >= lo
        hi_ok = hi is None or tws < hi
        if lo_ok and hi_ok:
            return band_id
    return None


def expected_percentile(ratings: tuple[float, ...], rating: float) -> float | None:
    """Expected finish percentile = TCC rank (ascending) / field size.

    Lower TCC ⇒ owed time by the fleet ⇒ expected to finish nearer the front
    (lower percentile).  Ties share the best rank (a conservative
    expectation).  Single-boat fields carry no information (``None``).
    """
    n = len(ratings)
    if n < 2:
        return None
    rank = sum(1 for r in ratings if r < rating) + 1
    return rank / n


def dataset_fingerprint(observations: tuple[RaceObservation, ...]) -> str:
    """Stable hash over the sorted observation identities (16 hex chars).

    This *is* the dataset version for the computation: same rows in ⇒ same
    fingerprint out; any row added/removed/changed ⇒ different fingerprint.
    """
    keys = sorted(obs.observation_key() for obs in observations)
    digest = hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()
    return digest[:16]


def _bootstrap_ci(
    values: np.ndarray,
    *,
    confidence_level: float,
    resamples: int,
    seed: int,
) -> tuple[float, float, str]:
    """95 % CI for the mean: bootstrap-t, percentile fallback, or degenerate.

    * ``n < 2``                → degenerate interval at the point estimate.
    * ``se ≈ 0``               → percentile bootstrap (bootstrap-t is
      undefined on zero variance).
    * degenerate resample      → ``(mean, mean)`` (zero-width CI).
    """
    n = len(values)
    mean = float(np.mean(values))
    if n < 2:
        return mean, mean, "degenerate"

    se = float(scipy_stats.sem(values))
    if not math.isfinite(se) or se < 1e-12:
        method = "bootstrap-percentile"
    else:
        method = "bootstrap-t"

    rng = np.random.default_rng(seed)
    alpha = 1.0 - confidence_level
    lo_q, hi_q = 100.0 * (alpha / 2.0), 100.0 * (1.0 - alpha / 2.0)

    if method == "bootstrap-t":
        t_stats: list[float] = []
        for _ in range(resamples):
            sample = values[rng.integers(0, n, n)]
            s_mean = float(np.mean(sample))
            s_se = float(scipy_stats.sem(sample))
            if math.isfinite(s_se) and s_se >= 1e-12:
                t_stats.append((s_mean - mean) / s_se)
        if len(t_stats) < 10:
            return mean, mean, "degenerate"
        t_lo, t_hi = np.percentile(np.array(t_stats), [lo_q, hi_q])
        return (
            float(mean - t_hi * se),
            float(mean - t_lo * se),
            method,
        )

    # Percentile bootstrap on the mean.
    means = np.empty(resamples)
    for i in range(resamples):
        means[i] = float(np.mean(values[rng.integers(0, n, n)]))
    lo, hi = np.percentile(means, [lo_q, hi_q])
    return float(lo), float(hi), method


def _interpret(rai: float, ci_lower: float, ci_upper: float) -> str:
    if ci_lower > 0:
        return (
            f"Out-performing her rating (RAI {rai:+.1f}, CI wholly above zero): "
            "consistently finishes better than TCC predicts."
        )
    if ci_upper < 0:
        return (
            f"Under-performing her rating (RAI {rai:+.1f}, CI wholly below zero): "
            "consistently finishes worse than TCC predicts."
        )
    return (
        f"Racing to her rating within noise (RAI {rai:+.1f}, CI spans zero): "
        "no statistically clear over/under-performance."
    )


# ---------------------------------------------------------------------------
# Core computation (pure)
# ---------------------------------------------------------------------------


def compute_rai_from_observations(
    observations: list[RaceObservation] | tuple[RaceObservation, ...],
    field_ratings: dict[tuple[str, str | None, str | None], tuple[float, ...]],
    *,
    info: BoatInfo | None = None,
    config: RAIRulesetConfigV1 = DEFAULT_CONFIG,
) -> RAIResultV1:
    """Compute the per-boat RAI from race observations — pure function.

    Parameters
    ----------
    observations:
        The boat's usable race results (already filtered to finished,
        placed, fleet_size > 1, IRC-rated — see the DB bridge).
    field_ratings:
        ``(event_name, race_name, event_date) → ratings`` of every scored
        boat in that race, used for the expected-percentile computation.
    info:
        Boat display metadata (name/sail/design).
    config:
        Versioned ruleset; the output carries its fingerprint.
    """
    observations = tuple(observations)
    info = info or BoatInfo(boat_id=observations[0].boat_id if observations else 0)
    ds_fp = dataset_fingerprint(observations)
    cfg_fp = config.fingerprint()

    wins = sum(1 for o in observations if o.place == 1)
    podiums = sum(1 for o in observations if o.place <= 3)

    advantages: list[float] = []
    finish_pcts: list[float] = []
    expected_pcts: list[float] = []
    contributions: list[dict] = []
    band_values: dict[str, list[float]] = {}
    n_wind_observed = 0

    for obs in observations:
        actual_pct = obs.place / obs.fleet_size
        finish_pcts.append(actual_pct)

        key = (obs.event_name, obs.race_name, obs.event_date)
        ratings = field_ratings.get(key, ())
        exp_pct = expected_percentile(ratings, obs.rating_value)

        tws = extract_tws(obs.raw)
        band = tws_band_for(tws, config.tws_bands)
        if tws is not None:
            n_wind_observed += 1

        if exp_pct is None:
            contributions.append(
                {
                    "event_name": obs.event_name,
                    "race_name": obs.race_name,
                    "event_date": obs.event_date,
                    "place": obs.place,
                    "fleet_size": obs.fleet_size,
                    "rating_value": obs.rating_value,
                    "actual_pct": round(actual_pct, 6),
                    "expected_pct": None,
                    "advantage": None,
                    "scored": False,
                    "tws": tws,
                    "tws_band": band,
                }
            )
            continue

        adv = (exp_pct - actual_pct) * 100.0
        advantages.append(adv)
        expected_pcts.append(exp_pct)
        if band is not None:
            band_values.setdefault(band, []).append(adv)

        contributions.append(
            {
                "event_name": obs.event_name,
                "race_name": obs.race_name,
                "event_date": obs.event_date,
                "place": obs.place,
                "fleet_size": obs.fleet_size,
                "rating_value": obs.rating_value,
                "actual_pct": round(actual_pct, 6),
                "expected_pct": round(exp_pct, 6),
                "advantage": round(adv, 4),
                "scored": True,
                "tws": tws,
                "tws_band": band,
            }
        )

    n_races = len(observations)
    n_scored = len(advantages)
    meets_threshold = n_scored >= config.min_races

    # ---- condition splits (computed regardless of the overall threshold) --
    splits: list[BandSplitV1] = []
    for band_id, band_label, _lo, _hi in config.tws_bands:
        vals = band_values.get(band_id, [])
        n_band = len(vals)
        if n_band == 0:
            splits.append(
                BandSplitV1(band_id, band_label, 0, None, None, None, STATUS_INSUFFICIENT)
            )
            continue
        if n_band < config.min_band_races:
            splits.append(
                BandSplitV1(
                    band_id,
                    band_label,
                    n_band,
                    None,
                    None,
                    None,
                    STATUS_INSUFFICIENT,
                )
            )
            continue
        arr = np.array(vals)
        lo, hi, _method = _bootstrap_ci(
            arr,
            confidence_level=config.confidence_level,
            resamples=config.bootstrap_resamples,
            seed=config.bootstrap_seed,
        )
        splits.append(
            BandSplitV1(band_id, band_label, n_band, float(np.mean(arr)), lo, hi, STATUS_OK)
        )

    # ---- headline RAI ------------------------------------------------------
    if not meets_threshold:
        return RAIResultV1(
            schema=RAI_SCHEMA_VERSION,
            boat_id=info.boat_id,
            boat_name=info.boat_name,
            sail_number=info.sail_number,
            design=info.design,
            status=STATUS_INSUFFICIENT,
            rai=None,
            ci_lower=None,
            ci_upper=None,
            ci_method="none",
            confidence_level=config.confidence_level,
            n_races=n_races,
            n_scored=n_scored,
            avg_finish_pct=float(np.mean(finish_pcts)) if finish_pcts else None,
            avg_expected_pct=float(np.mean(expected_pcts)) if expected_pcts else None,
            wins=wins,
            podiums=podiums,
            meets_min_races=False,
            min_races_required=config.min_races,
            condition_splits=tuple(splits),
            n_wind_observed=n_wind_observed,
            dataset_fingerprint=ds_fp,
            config_fingerprint=cfg_fp,
            race_contributions=tuple(contributions),
            interpretation=(
                f"Insufficient data: {n_scored} scored races, "
                f"minimum {config.min_races} required."
            ),
        )

    arr = np.array(advantages)
    mean_rai = float(np.mean(arr))
    ci_lower, ci_upper, ci_method = _bootstrap_ci(
        arr,
        confidence_level=config.confidence_level,
        resamples=config.bootstrap_resamples,
        seed=config.bootstrap_seed,
    )

    return RAIResultV1(
        schema=RAI_SCHEMA_VERSION,
        boat_id=info.boat_id,
        boat_name=info.boat_name,
        sail_number=info.sail_number,
        design=info.design,
        status=STATUS_OK,
        rai=mean_rai,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        ci_method=ci_method,
        confidence_level=config.confidence_level,
        n_races=n_races,
        n_scored=n_scored,
        avg_finish_pct=float(np.mean(finish_pcts)),
        avg_expected_pct=float(np.mean(expected_pcts)),
        wins=wins,
        podiums=podiums,
        meets_min_races=True,
        min_races_required=config.min_races,
        condition_splits=tuple(splits),
        n_wind_observed=n_wind_observed,
        dataset_fingerprint=ds_fp,
        config_fingerprint=cfg_fp,
        race_contributions=tuple(contributions),
        interpretation=_interpret(mean_rai, ci_lower, ci_upper),
    )


def class_baseline_from_results(
    design: str,
    results: list[RAIResultV1] | tuple[RAIResultV1, ...],
    *,
    config: RAIRulesetConfigV1 = DEFAULT_CONFIG,
    dataset_fingerprint_: str | None = None,
) -> ClassBaselineV1:
    """Aggregate per-boat RAIs into a class baseline — pure function.

    Only boats that *meet the minimum-race threshold* (``status == "ok"``)
    enter the baseline; under-threshold boats are counted in
    ``n_boats_total`` but excluded from every statistic.
    """
    results = tuple(results)
    qualifying = [r.rai for r in results if r.status == STATUS_OK and r.rai is not None]
    n_total = len(results)
    n_qual = len(qualifying)

    if dataset_fingerprint_ is not None:
        ds_fp = dataset_fingerprint_
    elif results:
        # Combine member fingerprints deterministically.
        ds_fp = hashlib.sha256(
            "|".join(sorted(r.dataset_fingerprint for r in results)).encode("utf-8")
        ).hexdigest()[:16]
    else:
        ds_fp = hashlib.sha256(f"empty:{design}".encode("utf-8")).hexdigest()[:16]

    if not qualifying:
        return ClassBaselineV1(
            schema="ClassBaselineV1",
            design=design,
            n_boats=0,
            n_boats_total=n_total,
            mean_rai=None,
            median_rai=None,
            std_rai=None,
            p25_rai=None,
            p75_rai=None,
            min_races_required=config.min_races,
            dataset_fingerprint=ds_fp,
            config_fingerprint=config.fingerprint(),
        )

    arr = np.array(qualifying)
    return ClassBaselineV1(
        schema="ClassBaselineV1",
        design=design,
        n_boats=n_qual,
        n_boats_total=n_total,
        mean_rai=float(np.mean(arr)),
        median_rai=float(median(qualifying)),
        std_rai=float(np.std(arr, ddof=1)) if n_qual > 1 else 0.0,
        p25_rai=float(np.percentile(arr, 25)),
        p75_rai=float(np.percentile(arr, 75)),
        min_races_required=config.min_races,
        dataset_fingerprint=ds_fp,
        config_fingerprint=config.fingerprint(),
    )


# ---------------------------------------------------------------------------
# DB bridge — corrected race results linked to resolved boat identities
# ---------------------------------------------------------------------------


def _iso_date(value) -> str | None:
    """Normalise a DB date value to an ISO string (SQLite returns strings)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _fetch_boat_observations(engine: Engine, boat_id: int) -> tuple[BoatInfo, list[RaceObservation]]:
    """Pull the boat's corrected results with the shared analytics filter."""
    from irc_data.analysis.filters import BASIC_IRC_FILTER

    query = text(f"""
        SELECT
            r.event_name,
            r.race_name,
            r.event_date,
            r.place,
            r.fleet_size,
            r.rating_value,
            r.raw_data,
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
        ORDER BY r.event_date NULLS LAST, r.event_name, r.race_name
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"boat_id": boat_id}).fetchall()

    if not rows:
        return BoatInfo(boat_id=boat_id), []

    info = BoatInfo(
        boat_id=boat_id,
        boat_name=rows[0].boat_name or "",
        sail_number=rows[0].sail_number or "",
        design=rows[0].design,
    )
    observations = [
        RaceObservation(
            boat_id=boat_id,
            event_name=r.event_name,
            race_name=r.race_name,
            event_date=_iso_date(r.event_date),
            place=int(r.place),
            fleet_size=int(r.fleet_size),
            rating_value=float(r.rating_value),
            raw=r.raw_data if isinstance(r.raw_data, dict) else _parse_raw(r.raw_data),
        )
        for r in rows
    ]
    return info, observations


def _parse_raw(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _fetch_field_ratings(
    engine: Engine,
    race_keys: set[tuple[str, str | None, str | None]],
) -> dict[tuple[str, str | None, str | None], tuple[float, ...]]:
    """Fetch the rating field for each race the boat sailed.

    The field is the set of **distinct ratings** among scored (finished,
    placed) rows — one rating per resolved identity — so duplicate result
    rows (e.g. a wrongly-merged identity) cannot bias the expected
    percentile.
    """
    from irc_data.analysis.filters import BASIC_IRC_FILTER

    if not race_keys:
        return {}

    query = text(f"""
        SELECT r.event_name, r.race_name, r.event_date, r.rating_value
        FROM race_results r
        WHERE r.event_name = :event
          AND r.status = 'finished'
          AND r.place IS NOT NULL
          AND COALESCE(r.race_name, '') = COALESCE(:race_name, '')
          AND r.event_date IS NOT DISTINCT FROM :event_date
          {BASIC_IRC_FILTER}
    """)

    fields: dict[tuple[str, str | None, str | None], set[float]] = {}
    with engine.connect() as conn:
        for event, race_name, event_date in race_keys:
            rows = conn.execute(
                query,
                {"event": event, "race_name": race_name, "event_date": event_date},
            ).fetchall()
            key = (event, race_name, event_date)
            # Distinct ratings: one per identity, so duplicate result rows
            # (e.g. from an identity-merge error) cannot distort the field.
            fields[key] = {float(row.rating_value) for row in rows}
    return {k: tuple(sorted(v)) for k, v in fields.items()}


def compute_rai_v1(
    engine: Engine,
    boat_id: int,
    *,
    config: RAIRulesetConfigV1 = DEFAULT_CONFIG,
) -> RAIResultV1:
    """DB bridge: RAI for one resolved boat identity from corrected results."""
    info, observations = _fetch_boat_observations(engine, boat_id)
    race_keys = {(o.event_name, o.race_name, o.event_date) for o in observations}
    fields = _fetch_field_ratings(engine, race_keys)
    return compute_rai_from_observations(observations, fields, info=info, config=config)


def class_baseline_v1(
    engine: Engine,
    design: str,
    *,
    config: RAIRulesetConfigV1 = DEFAULT_CONFIG,
) -> ClassBaselineV1:
    """DB bridge: class mean RAI for a design, over threshold-passing boats."""
    boats_query = text("""
        SELECT DISTINCT b.id
        FROM boats b
        JOIN race_results r ON r.boat_id = b.id
        WHERE COALESCE(b.design_canonical, b.design) = :design
    """)
    with engine.connect() as conn:
        boat_ids = [row.id for row in conn.execute(boats_query, {"design": design}).fetchall()]

    results = [compute_rai_v1(engine, bid, config=config) for bid in sorted(boat_ids)]
    return class_baseline_from_results(design, results, config=config)
