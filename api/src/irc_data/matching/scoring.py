"""Explainable pairwise match scoring (DP-04-03).

Goal
----

Rank identity evidence **without opaque magic**.  Given the DP-04-02
candidate set (every pair explained by ≥1 blocking rule), this module
computes a *score* per candidate pair that is:

* **reproducible** — the score is a pure function of the two input
  observations, the pair's blocking provenance, the versioned feature
  ruleset and the versioned threshold config; no clocks, no randomness,
  no hidden state;
* **explainable** — the score is a weighted sum of named, individually
  computed *features*.  Every scored pair carries the full
  :class:`FeatureContribution` vector (``weight × value = points``) plus
  the list of features that were **missing**, so a human (or the DP-04-05
  MatchCard) can see exactly where every point came from and what
  evidence was absent;
* **calibrated by entity type** — :class:`ThresholdConfig` is fit on
  *labelled* examples per entity type; the band between
  ``auto_reject_below`` and ``auto_merge_at_or_above`` is the
  **uncertain band** that DP-04-05 routes to human adjudication.

Score construction (``scorer-rules-v1``)
----------------------------------------

::

    score = Σ (feature_weight × feature_value)          ∈ [0, 1]

    feature_value ∈ [0, 1]  for present features
    feature_value = 0       for missing features (never imputed)

Because every weight is non-negative and every value lies in [0, 1], the
score is bounded by construction (``Σ weights = 1.0``) and every feature
can only *add* evidence — absence is *preserved* as missingness, never
silently converted into positive or negative evidence.

An optional ``model_score`` (e.g. from a learned deduplication model)
blends in via ``score = (1 − λ) · deterministic + λ · model`` with the
deterministic floor intact: even at ``λ = 1`` a confident learned model
cannot push an evidence-free pair into the auto-merge band
(``AUTO_MERGE_FLOOR`` is all-deterministic).

Why no opaque magic
-------------------

Every number a downstream consumer sees decomposes into auditable parts:

* the DP-04-02 ``rules_fired`` / ``matching_keys`` (why the pair exists),
* ``feature_contributions`` (which signals added how many points),
* ``missing_features`` (which signals were absent),
* ``model_score`` and ``model_weight`` when a learned score was blended,
* the ``thresholds`` snapshot the pair was routed with (per entity type).

Builds on: DP-04-02 (``CandidatePair`` — every scored pair is explained
by ≥1 blocking rule), DP-03-03 (normalised ``EntityObservation`` input).

**Code of record:** ``api/src/irc_data/matching/scoring.py``
(``SCHEMA_VERSION = "scorer-v1"``, features ``scorer-rules-v1``,
threshold config ``threshold-config-v1``).
**Verification:** ``api/tests/matching/test_scoring.py`` and the
holdout-evaluation harness ``api/scripts/verify_dp_04_03.py``.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from irc_data.matching.blocking import (
    CandidatePair,
    EntityObservation,
    _name_tokens,
    _observation_year_hi,
    _observation_year_lo,
    _soundex,
    normalize_design_key,
    normalize_name_key,
    normalize_registry_id,
    normalize_sail_tokens,
)

# ---------------------------------------------------------------------------
# Schema / ruleset versioning
# ---------------------------------------------------------------------------

#: Version tag for the scored-pair contract family (serialisation format).
SCHEMA_VERSION = "scorer-v1"

#: Identifier of the shipped deterministic feature ruleset.  Like the
#: blocking ruleset, changing any feature ships ``scorer-rules-v2``
#: alongside v1 so prior scores remain reproducible.
SCORER_RULESET_V1_ID = "scorer-rules-v1"

#: All known feature-ruleset ids, oldest first.
KNOWN_SCORER_RULESETS: tuple[str, ...] = (SCORER_RULESET_V1_ID,)

#: Identifier of the shipped threshold config *schema*.  The *values* are
#: calibrated per entity type (and carry the labelled-example fingerprint
#: they were fit on); the schema id pins what the fields mean.
THRESHOLD_CONFIG_V1_ID = "threshold-config-v1"

#: Entity types the scorer knows how to calibrate.  ``"boat"`` is the DP-03
#: default; unknown types resolve to :data:`DEFAULT_THRESHOLDS_V1`.
KNOWN_ENTITY_TYPES: tuple[str, ...] = ("boat",)

#: The deterministic floor that a fully-corroborated pair must reach on its
#: own before the auto-merge band is meaningful (documented for handoff;
#: enforced by the all-agreeing evidence case in the harness).
AUTO_MERGE_FLOOR = 0.90

#: Maximum score mass attributable to an optional blended ``model_score``
#: (hard cap, enforced on :class:`ScoringConfig`).
MAX_MODEL_WEIGHT = 0.5


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScoringError(ValueError):
    """Base class for scoring contract violations."""


class UnknownScorerRulesetError(ScoringError):
    """Raised when a caller requests a feature ruleset id we do not ship."""


class UnknownEntityTypeError(ScoringError):
    """Raised when a caller requests thresholds for an unshipped entity type."""


# ---------------------------------------------------------------------------
# Feature primitives (pure, deterministic, dependency-free)
# ---------------------------------------------------------------------------


def _levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein edit distance (DP over two rows)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def _norm_key(value: str | None) -> str:
    """Normalise a free-text code (country) to a comparable key."""
    if not value:
        return ""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _year_from(d: Any) -> int | None:
    return d.year if d is not None else None


def _year_signal(obs: EntityObservation) -> int | None:
    """Best single year for an observation: validity year else build year."""
    return (
        _year_from(obs.valid_from)
        or _year_from(obs.valid_to)
        or obs.year_built
    )


# ---------------------------------------------------------------------------
# Feature ruleset — scorer-rules-v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringFeature:
    """One named, weighted deterministic pairwise feature.

    ``fn(left, right, pair)`` returns a value in ``[0, 1]`` for a
    *present* feature, or ``None`` when the evidence the feature needs is
    missing on either side.  Returning ``None`` (rather than 0.0) is how
    **missingness is preserved**: the pair's ``missing_features`` list
    records the feature name so a reviewer can see it was absent, and the
    score contribution is 0 — never imputed.
    """

    feature_id: str
    name: str
    weight: float
    description: str
    fn: Any

    def __post_init__(self) -> None:
        if not (0.0 < float(self.weight) <= 1.0):
            raise ScoringError(
                f"feature {self.feature_id} weight must be in (0, 1], got {self.weight!r}"
            )

    def compute(
        self,
        left: EntityObservation,
        right: EntityObservation,
        pair: CandidatePair,
    ) -> float | None:
        return self.fn(left, right, pair)


def _strong_sail_tokens(obs: EntityObservation) -> set[str]:
    """Sail tokens excluding short bare numerics (ambiguous across countries)."""
    return {
        t for t in normalize_sail_tokens(obs.sail_number)
        if not (t.isdigit() and len(t) <= 3)
    }


def _f_sail_exact(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    if not left.sail_number or not right.sail_number:
        return None
    a = _strong_sail_tokens(left)
    b = _strong_sail_tokens(right)
    if not a or not b:
        return None
    return 1.0 if a & b else 0.0


def _f_registry_exact(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    a = normalize_registry_id(left.registry_id)
    b = normalize_registry_id(right.registry_id)
    if not a or not b:
        return None
    return 1.0 if a == b else 0.0


def _f_name_similarity(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    ka = normalize_name_key(left.name)
    kb = normalize_name_key(right.name)
    if not ka or not kb:
        return None
    if ka == kb:
        return 1.0
    dist = _levenshtein(ka, kb)
    return max(0.0, 1.0 - dist / max(len(ka), len(kb)))


def _f_name_token_jaccard(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    a = frozenset(_name_tokens(left.name))
    b = frozenset(_name_tokens(right.name))
    if not a or not b:
        return None
    return _jaccard(a, b)


def _f_design_exact(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    a = normalize_design_key(left.design)
    b = normalize_design_key(right.design)
    if not a or not b:
        return None
    return 1.0 if a.replace(" ", "") == b.replace(" ", "") else 0.0


def _f_country_match(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    a = _norm_key(left.country)
    b = _norm_key(right.country)
    if not a or not b:
        return None
    return 1.0 if a == b else 0.0


def _f_loa_closeness(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    if left.loa_m is None or right.loa_m is None:
        return None
    if left.loa_m <= 0 or right.loa_m <= 0:
        return None
    rel = abs(float(left.loa_m) - float(right.loa_m)) / max(
        float(left.loa_m), float(right.loa_m)
    )
    return max(0.0, 1.0 - rel / 0.20)


def _f_beam_closeness(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    if left.beam_m is None or right.beam_m is None:
        return None
    if left.beam_m <= 0 or right.beam_m <= 0:
        return None
    rel = abs(float(left.beam_m) - float(right.beam_m)) / max(
        float(left.beam_m), float(right.beam_m)
    )
    return max(0.0, 1.0 - rel / 0.20)


def _f_year_closeness(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    a = _year_signal(left)
    b = _year_signal(right)
    if a is None or b is None:
        return None
    return max(0.0, 1.0 - abs(a - b) / 10.0)


def _f_temporal_overlap(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    lo_a, hi_a = _observation_year_lo(left), _observation_year_hi(left)
    lo_b, hi_b = _observation_year_lo(right), _observation_year_hi(right)
    if None in (lo_a, hi_a, lo_b, hi_b):
        return None
    return 1.0 if max(lo_a, lo_b) <= min(hi_a, hi_b) else 0.0


def _f_blocking_corroboration(
    left: EntityObservation, right: EntityObservation, pair: CandidatePair
) -> float | None:
    """DP-04-02 provenance as a *feature*: pairs found by ≥2 independent
    blocking rules carry more evidence than single-rule pairs."""
    n = len(pair.rules_fired)
    if n >= 3:
        return 1.0
    if n == 2:
        return 0.5
    return 0.0


#: The shipped feature ruleset.  Weights are hand-set priors, deliberately
#: simple, and sum to exactly 1.0 so the deterministic score is bounded in
#: [0, 1] by construction.  They are *not* magic: the holdout evaluation
#: (§5 of docs/architecture/scoring.md) measures the achieved precision /
#: recall / calibration, and the per-entity-type thresholds are *fit* to
#: labelled data rather than guessed.
#:
#: ======  ===========================  ======  ===========================
#: id      name                         weight  signal
#: ======  ===========================  ======  ===========================
#: F01     ``sail_exact``               0.22    shared strong sail token
#: F02     ``registry_exact``           0.20    normalised registry id equal
#: F03     ``name_similarity``          0.14    edit-similarity of name keys
#: F04     ``design_exact``             0.08    design families equal
#: F05     ``country_match``            0.04    flag country equal
#: F06     ``loa_closeness``            0.06    |ΔLOA| within 20 % taper
#: F07     ``year_closeness``           0.05    |Δyear| within 10-y taper
#: F08     ``blocking_corroboration``   0.06    ≥2 blocking rules fired
#: F09     ``name_token_jaccard``       0.05    shared name tokens
#: F10     ``beam_closeness``           0.03    |Δbeam| within 20 % taper
#: F11     ``temporal_overlap``         0.07    validity eras overlap
#: ======  ===========================  ======  ===========================
def _mk(feature_id, name, weight, description, fn) -> ScoringFeature:
    return ScoringFeature(
        feature_id=feature_id, name=name, weight=weight,
        description=description, fn=fn,
    )


SCORER_RULESET_V1: tuple[ScoringFeature, ...] = (
    _mk("F01", "sail_exact", 0.22,
        "shared strong sail token (short bare numerics excluded)",
        _f_sail_exact),
    _mk("F02", "registry_exact", 0.20,
        "normalised registry / hull id equal",
        _f_registry_exact),
    _mk("F03", "name_similarity", 0.14,
        "1 − levenshtein/max_len over normalised name keys",
        _f_name_similarity),
    _mk("F04", "design_exact", 0.08,
        "design families equal (punctuation/spacing collapsed)",
        _f_design_exact),
    _mk("F05", "country_match", 0.04,
        "flag country equal",
        _f_country_match),
    _mk("F06", "loa_closeness", 0.06,
        "1 − rel|ΔLOA|/0.20 (linear taper to 0 at 20 % difference)",
        _f_loa_closeness),
    _mk("F07", "year_closeness", 0.05,
        "1 − |Δyear|/10 (linear taper to 0 at 10 years difference)",
        _f_year_closeness),
    _mk("F08", "blocking_corroboration", 0.06,
        "0 / 0.5 / 1.0 for 1 / 2 / ≥3 blocking rules fired",
        _f_blocking_corroboration),
    _mk("F09", "name_token_jaccard", 0.05,
        "jaccard over significant name tokens (stopwords removed)",
        _f_name_token_jaccard),
    _mk("F10", "beam_closeness", 0.03,
        "1 − rel|Δbeam|/0.20 (linear taper)",
        _f_beam_closeness),
    _mk("F11", "temporal_overlap", 0.07,
        "1 if validity/build-year eras overlap else 0",
        _f_temporal_overlap),
)

_FEATURE_WEIGHT_SUM = round(sum(f.weight for f in SCORER_RULESET_V1), 9)
if _FEATURE_WEIGHT_SUM != 1.0:  # pragma: no cover - import-time guard
    raise ScoringError(
        f"scorer-rules-v1 weights must sum to 1.0, got {_FEATURE_WEIGHT_SUM}"
    )


def get_scorer_ruleset(
    ruleset_id: str = SCORER_RULESET_V1_ID,
) -> tuple[ScoringFeature, ...]:
    """Fetch a shipped feature ruleset by id (versioned, immutable)."""
    if ruleset_id != SCORER_RULESET_V1_ID:
        raise UnknownScorerRulesetError(
            f"unknown scorer ruleset {ruleset_id!r}; known: {list(KNOWN_SCORER_RULESETS)}"
        )
    return SCORER_RULESET_V1


# ---------------------------------------------------------------------------
# Threshold config — calibrated per entity type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdConfig:
    """Per-entity-type decision band, calibrated on labelled examples.

    The band ``[auto_reject_below, auto_merge_at_or_above)`` is the
    **uncertain band**: DP-04-05 routes everything inside it (and
    everything high-impact regardless of score) to human adjudication.

    ``fit_pairs`` / ``fit_fingerprint`` record the labelled example set the
    thresholds were fit on, so a stored config is auditable back to data.
    """

    entity_type: str = "boat"
    auto_reject_below: float = 0.20
    auto_merge_at_or_above: float = 0.90
    config_id: str = THRESHOLD_CONFIG_V1_ID
    fit_pairs: int = 0
    fit_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.auto_reject_below) < float(self.auto_merge_at_or_above) <= 1.0):
            raise ScoringError(
                "require 0 <= auto_reject_below < auto_merge_at_or_above <= 1, "
                f"got [{self.auto_reject_below}, {self.auto_merge_at_or_above}]"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "entity_type": self.entity_type,
            "auto_reject_below": self.auto_reject_below,
            "auto_merge_at_or_above": self.auto_merge_at_or_above,
            "fit_pairs": self.fit_pairs,
            "fit_fingerprint": self.fit_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ThresholdConfig":
        return cls(
            entity_type=d.get("entity_type", "boat"),
            auto_reject_below=d["auto_reject_below"],
            auto_merge_at_or_above=d["auto_merge_at_or_above"],
            config_id=d.get("config_id", THRESHOLD_CONFIG_V1_ID),
            fit_pairs=d.get("fit_pairs", 0),
            fit_fingerprint=d.get("fit_fingerprint", ""),
        )


#: Shipped default thresholds (hand-set prior; the verification harness
#: re-fits them on the labelled calibration split and reports the result).
DEFAULT_THRESHOLDS_V1: dict[str, ThresholdConfig] = {
    "boat": ThresholdConfig(
        entity_type="boat",
        auto_reject_below=0.20,
        auto_merge_at_or_above=0.90,
    ),
}


def get_thresholds(entity_type: str = "boat") -> ThresholdConfig:
    """Fetch the shipped default thresholds for an entity type."""
    try:
        return DEFAULT_THRESHOLDS_V1[entity_type]
    except KeyError:
        raise UnknownEntityTypeError(
            f"unknown entity type {entity_type!r}; known: {list(DEFAULT_THRESHOLDS_V1)}"
        ) from None


# ---------------------------------------------------------------------------
# Output contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureContribution:
    """One feature's line item: ``weight × value = points`` (or missing)."""

    feature_id: str
    name: str
    weight: float
    value: float | None
    points: float
    missing: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "name": self.name,
            "weight": self.weight,
            "value": self.value,
            "points": self.points,
            "missing": self.missing,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScoredPairV1:
    """One scored candidate pair — the DP-04-03 handoff contract.

    Carries everything needed to *explain* the score: the full feature
    vector with per-feature contributions, the missing-feature list, the
    (optional) blended model score, and the threshold snapshot the pair
    was routed with.  ``pair`` is the DP-04-02 :class:`CandidatePair`, so
    every scored pair is auditable back to ≥1 blocking rule.
    """

    pair: CandidatePair
    entity_type: str
    deterministic_score: float
    model_score: float | None
    model_weight: float
    score: float
    feature_contributions: tuple[FeatureContribution, ...]
    missing_features: tuple[str, ...]
    thresholds: ThresholdConfig
    scorer_ruleset_id: str = SCORER_RULESET_V1_ID
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.score) <= 1.0):
            raise ScoringError(f"score must be in [0, 1], got {self.score!r}")
        if not (0.0 <= float(self.deterministic_score) <= 1.0):
            raise ScoringError(
                f"deterministic_score must be in [0, 1], got {self.deterministic_score!r}"
            )
        if self.model_score is not None and not (0.0 <= float(self.model_score) <= 1.0):
            raise ScoringError(
                f"model_score must be in [0, 1], got {self.model_score!r}"
            )
        if not (0.0 <= float(self.model_weight) <= 1.0):
            raise ScoringError(
                f"model_weight must be in [0, 1], got {self.model_weight!r}"
            )

    # -- explainability --------------------------------------------------

    @property
    def explanation(self) -> tuple[str, ...]:
        """Human-readable per-feature line items, most-points first."""
        lines = []
        for c in sorted(
            self.feature_contributions, key=lambda c: (-c.points, c.feature_id)
        ):
            if c.missing:
                lines.append(f"{c.name}: MISSING — {c.detail}")
            else:
                lines.append(
                    f"{c.name}: {c.value:.2f} × w{c.weight:.2f} = {c.points:.3f}"
                )
        return tuple(lines)

    # -- routing (mirrors the DP-04-05 adjudication bands) ---------------

    @property
    def routing_band(self) -> str:
        """``auto_reject`` | ``uncertain`` | ``auto_merge`` from thresholds."""
        if self.score < self.thresholds.auto_reject_below:
            return "auto_reject"
        if self.score >= self.thresholds.auto_merge_at_or_above:
            return "auto_merge"
        return "uncertain"

    @property
    def uncertainty(self) -> float:
        """Distance from the nearest confident endpoint: 1.0 at score 0.5."""
        return 1.0 - abs(2.0 * float(self.score) - 1.0)

    def to_scored_candidate_kwargs(self) -> dict[str, Any]:
        """Kwargs for DP-04-05 ``ScoredCandidateV1`` (adjudication input)."""
        return {
            "pair": self.pair,
            "score": float(self.score),
            "score_explanation": self.explanation,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scorer_ruleset_id": self.scorer_ruleset_id,
            "entity_type": self.entity_type,
            "pair": self.pair.to_dict(),
            "deterministic_score": self.deterministic_score,
            "model_score": self.model_score,
            "model_weight": self.model_weight,
            "score": self.score,
            "routing_band": self.routing_band,
            "thresholds": self.thresholds.to_dict(),
            "feature_contributions": [c.to_dict() for c in self.feature_contributions],
            "missing_features": list(self.missing_features),
        }


# ---------------------------------------------------------------------------
# The pairwise scorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringConfig:
    """Everything that determines a score, so runs are reproducible."""

    scorer_ruleset_id: str = SCORER_RULESET_V1_ID
    entity_type: str = "boat"
    thresholds: ThresholdConfig = field(
        default_factory=lambda: DEFAULT_THRESHOLDS_V1["boat"]
    )
    #: Blend weight λ for an optional learned ``model_score``.  0.0 (the
    #: default) means fully deterministic; the cap is :data:`MAX_MODEL_WEIGHT`.
    model_weight: float = 0.0

    def __post_init__(self) -> None:
        get_scorer_ruleset(self.scorer_ruleset_id)
        if not (0.0 <= float(self.model_weight) <= MAX_MODEL_WEIGHT):
            raise ScoringError(
                f"model_weight must be in [0, {MAX_MODEL_WEIGHT}], got {self.model_weight!r}"
            )

    def fingerprint(self) -> str:
        """Stable fingerprint of the config (reproducibility anchor)."""
        payload = json.dumps(
            {
                "scorer_ruleset_id": self.scorer_ruleset_id,
                "entity_type": self.entity_type,
                "thresholds": self.thresholds.to_dict(),
                "model_weight": self.model_weight,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class PairwiseScorer:
    """Deterministic, explainable pairwise scorer.

    Usage::

        scorer = PairwiseScorer()                      # scorer-rules-v1, boat
        sp = scorer.score_pair(pair, left, right)      # ScoredPairV1
        report = scorer.score(observations, report)    # ScoringReportV1

    The scorer is stateless and deterministic: equal inputs (same
    observations, same pair, same config) yield an identical score and an
    identical explanation, every time.
    """

    def __init__(self, config: ScoringConfig | None = None) -> None:
        self.config = config or ScoringConfig()
        self.features = get_scorer_ruleset(self.config.scorer_ruleset_id)

    # -- single pair ------------------------------------------------------

    def score_pair(
        self,
        pair: CandidatePair,
        left: EntityObservation,
        right: EntityObservation,
        *,
        model_score: float | None = None,
    ) -> ScoredPairV1:
        """Score one candidate pair with a full explanation."""
        if model_score is not None and not (0.0 <= float(model_score) <= 1.0):
            raise ScoringError(f"model_score must be in [0, 1], got {model_score!r}")

        contributions: list[FeatureContribution] = []
        missing: list[str] = []
        det = 0.0
        for feat in self.features:
            value = feat.compute(left, right, pair)
            if value is None:
                contributions.append(
                    FeatureContribution(
                        feature_id=feat.feature_id,
                        name=feat.name,
                        weight=feat.weight,
                        value=None,
                        points=0.0,
                        missing=True,
                        detail=feat.description,
                    )
                )
                missing.append(feat.name)
                continue
            value = max(0.0, min(1.0, float(value)))
            points = feat.weight * value
            det += points
            contributions.append(
                FeatureContribution(
                    feature_id=feat.feature_id,
                    name=feat.name,
                    weight=feat.weight,
                    value=value,
                    points=points,
                    missing=False,
                    detail=feat.description,
                )
            )

        det = max(0.0, min(1.0, det))

        lam = float(self.config.model_weight)
        if model_score is not None and lam > 0.0:
            score = (1.0 - lam) * det + lam * float(model_score)
        else:
            score = det
        score = max(0.0, min(1.0, score))

        return ScoredPairV1(
            pair=pair,
            entity_type=self.config.entity_type,
            deterministic_score=det,
            model_score=float(model_score) if model_score is not None else None,
            model_weight=lam if model_score is not None else 0.0,
            score=score,
            feature_contributions=tuple(contributions),
            missing_features=tuple(missing),
            thresholds=self.config.thresholds,
            scorer_ruleset_id=self.config.scorer_ruleset_id,
        )

    # -- a whole candidate report -----------------------------------------

    def score(
        self,
        observations: Iterable[EntityObservation],
        candidate_report: Any,
        *,
        model_scores: Mapping[tuple[str, str], float] | None = None,
    ) -> "ScoringReportV1":
        """Score every pair in a DP-04-02 ``CandidateReport``.

        ``model_scores`` optionally maps a canonical ``(left_id, right_id)``
        pair to a learned model's score in [0, 1]; pairs absent from the
        mapping are scored purely deterministically.
        """
        by_id = {o.observation_id: o for o in observations}
        model_scores = model_scores or {}
        scored: list[ScoredPairV1] = []
        for pair in candidate_report.pairs:
            try:
                left = by_id[pair.left_id]
                right = by_id[pair.right_id]
            except KeyError as exc:
                raise ScoringError(
                    f"candidate pair references unknown observation {exc}"
                ) from exc
            key = (pair.left_id, pair.right_id)
            ms = model_scores.get(key, model_scores.get((key[1], key[0])))
            scored.append(self.score_pair(pair, left, right, model_score=ms))
        return ScoringReportV1(
            config_fingerprint=self.config.fingerprint(),
            scored_pairs=tuple(scored),
        )


@dataclass(frozen=True)
class ScoringReportV1:
    """The full output of a scoring run (handoff contract)."""

    config_fingerprint: str
    scored_pairs: tuple[ScoredPairV1, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "config_fingerprint": self.config_fingerprint,
            "scored_pairs": [sp.to_dict() for sp in self.scored_pairs],
        }


# ---------------------------------------------------------------------------
# Threshold calibration on labelled examples
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelledPair:
    """One labelled example: a scored pair plus ground truth."""

    scored: ScoredPairV1
    is_match: bool
    #: True when a wrong merge here is *expensive* (rated boat, has results,
    #: has a certificate).  Drives the high-cost false-merge guard.
    high_cost: bool = False


def _fit_fingerprint(labels: Iterable["LabelledPair"]) -> str:
    payload = json.dumps(
        sorted(
            (lp.scored.pair.left_id, lp.scored.pair.right_id, lp.is_match, lp.high_cost)
            for lp in labels
        ),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def fit_thresholds(
    labelled: Iterable[LabelledPair],
    *,
    entity_type: str = "boat",
    max_high_cost_false_merges: int = 0,
    max_false_merges: int = 0,
    min_recall: float = 0.95,
) -> ThresholdConfig:
    """Calibrate the decision band for *entity_type* on labelled examples.

    The calibration is deliberately **conservative** — a wrong merge is far
    more expensive than a pair sent to adjudication, so the auto-merge line
    only ever sits where false merges are (near-)impossible:

    * ``auto_reject_below`` — the highest value that keeps **recall**
      (matches kept at-or-above the reject line) ≥ ``min_recall``.  Matches
      below the line would be auto-rejected, so the line stays at-or-below
      the lowest match score that keeps the recall budget.
    * ``auto_merge_at_or_above`` — the lowest value that yields at most
      ``max_false_merges`` false merges *overall* **and** at most
      ``max_high_cost_false_merges`` high-cost false merges (both default to
      zero).  If no non-match exists the line falls back to the
      :data:`AUTO_MERGE_FLOOR` default.

    The search is a pure function of the labelled scores, so the fitted band
    is reproducible for a fixed labelled set (and carries that set's
    fingerprint for audit).
    """
    labelled = tuple(labelled)
    if not labelled:
        raise ScoringError("fit_thresholds requires at least one labelled pair")

    match_scores = sorted(lp.scored.score for lp in labelled if lp.is_match)
    nonmatch_scores = sorted(lp.scored.score for lp in labelled if not lp.is_match)
    nonmatch_high = sorted(
        lp.scored.score for lp in labelled if (not lp.is_match) and lp.high_cost
    )

    # --- auto_reject_below: highest line keeping recall >= min_recall -----
    # recall(line) = (# matches with score >= line) / (# matches)
    best_reject = 0.0
    if match_scores:
        total = len(match_scores)
        # candidate lines just below each distinct match score
        candidates = sorted({0.0} | {max(0.0, s - 1e-9) for s in match_scores})
        for line in candidates:
            kept = sum(1 for s in match_scores if s >= line)
            if kept / total >= min_recall and line > best_reject:
                best_reject = line
        # never reject at-or-above the lowest match (would drop recall below 1)
        best_reject = min(best_reject, match_scores[0])
    best_reject = round(min(max(best_reject, 0.0), 0.99), 4)

    # --- auto_merge_at_or_above: conservative line -------------------------
    # The lowest line at which the number of non-matches at-or-above it is
    # within the false-merge budget (overall and high-cost).  With the default
    # zero budgets this sits strictly above the highest-scoring non-match.
    def _line_above(scores: list[float], budget: int) -> float | None:
        """Lowest merge line leaving ≤ ``budget`` of ``scores`` at-or-above it."""
        if not scores:
            return None
        # With budget b, the line must sit strictly above the (len-b)-th
        # highest score; if b >= len(scores) the floor is enough.
        if budget >= len(scores):
            return None  # every non-match could sit below the default floor
        # index of the (budget+1)-th highest score (0-based ascending)
        pivot = scores[len(scores) - 1 - budget]
        return min(1.0, pivot + 0.01)

    candidates = [c for c in (
        _line_above(nonmatch_scores, max_false_merges),
        _line_above(nonmatch_high, max_high_cost_false_merges),
    ) if c is not None]
    best_merge = max(candidates) if candidates else AUTO_MERGE_FLOOR
    # keep a strictly-valid band
    best_merge = round(max(best_merge, best_reject + 0.01), 4)
    if best_merge > 1.0:
        best_merge = 1.0

    return ThresholdConfig(
        entity_type=entity_type,
        auto_reject_below=best_reject,
        auto_merge_at_or_above=best_merge,
        fit_pairs=len(labelled),
        fit_fingerprint=_fit_fingerprint(labelled),
    )


# ---------------------------------------------------------------------------
# Holdout evaluation — precision / recall / calibration / high-cost merges
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationBin:
    """One calibration bin: mean predicted score vs empirical match rate."""

    lo: float
    hi: float
    count: int
    mean_score: float
    empirical_rate: float
    gap: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lo": self.lo,
            "hi": self.hi,
            "count": self.count,
            "mean_score": self.mean_score,
            "empirical_rate": self.empirical_rate,
            "gap": self.gap,
        }


@dataclass(frozen=True)
class HoldoutMetrics:
    """Precision / recall / calibration / high-cost-false-merge report."""

    entity_type: str
    pairs: int
    positives: int
    negatives: int
    threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    #: High-cost false merges: non-matches scored ≥ threshold that were
    #: flagged high_cost.  The acceptance criteria call these out by name.
    high_cost_false_merges: int
    #: Pairs in the uncertain band (routed to adjudication).
    uncertain: int
    calibration: tuple[CalibrationBin, ...]
    expected_calibration_error: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "entity_type": self.entity_type,
            "pairs": self.pairs,
            "positives": self.positives,
            "negatives": self.negatives,
            "threshold": self.threshold,
            "confusion": {
                "tp": self.true_positives,
                "fp": self.false_positives,
                "fn": self.false_negatives,
                "tn": self.true_negatives,
            },
            "precision": self.precision,
            "recall": self.recall,
            "high_cost_false_merges": self.high_cost_false_merges,
            "uncertain": self.uncertain,
            "expected_calibration_error": self.expected_calibration_error,
            "calibration": [b.to_dict() for b in self.calibration],
        }


def evaluate_holdout(
    labelled: Iterable[LabelledPair],
    thresholds: ThresholdConfig,
    *,
    bins: int = 10,
) -> HoldoutMetrics:
    """Measure precision, recall, calibration and high-cost false merges.

    The decision threshold is ``auto_merge_at_or_above`` (the line above
    which the pipeline would auto-merge); pairs in the uncertain band are
    counted separately (they route to adjudication, not to a decision).
    Purely set-based and deterministic.
    """
    labelled = tuple(labelled)
    line = thresholds.auto_merge_at_or_above
    reject = thresholds.auto_reject_below

    tp = fp = fn = tn = 0
    hcfm = 0
    uncertain = 0
    for lp in labelled:
        s = lp.scored.score
        predicted_merge = s >= line
        if reject <= s < line:
            uncertain += 1
        if predicted_merge and lp.is_match:
            tp += 1
        elif predicted_merge and not lp.is_match:
            fp += 1
            if lp.high_cost:
                hcfm += 1
        elif not predicted_merge and lp.is_match:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    # --- calibration ------------------------------------------------------
    cal: list[CalibrationBin] = []
    ece = 0.0
    n = len(labelled)
    if n:
        for i in range(bins):
            lo = i / bins
            hi = (i + 1) / bins
            members = [
                lp for lp in labelled
                if lo <= lp.scored.score < hi or (i == bins - 1 and lp.scored.score == 1.0)
            ]
            if not members:
                cal.append(CalibrationBin(lo, hi, 0, 0.0, 0.0, 0.0))
                continue
            mean_score = sum(lp.scored.score for lp in members) / len(members)
            rate = sum(1 for lp in members if lp.is_match) / len(members)
            gap = abs(mean_score - rate)
            ece += (len(members) / n) * gap
            cal.append(CalibrationBin(lo, hi, len(members), mean_score, rate, gap))

    return HoldoutMetrics(
        entity_type=thresholds.entity_type,
        pairs=n,
        positives=sum(1 for lp in labelled if lp.is_match),
        negatives=sum(1 for lp in labelled if not lp.is_match),
        threshold=line,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        high_cost_false_merges=hcfm,
        uncertain=uncertain,
        calibration=tuple(cal),
        expected_calibration_error=ece,
    )


def split_labelled(
    labelled: Iterable[LabelledPair],
    *,
    holdout_fraction: float = 0.4,
    seed: int = 20260522,
) -> tuple[tuple[LabelledPair, ...], tuple[LabelledPair, ...]]:
    """Deterministic stratified-ish split into (calibration, holdout).

    The split is seeded (never time-based) so the calibration/holdout
    partition — and therefore every downstream number — is reproducible.
    Positives and negatives are split independently so the holdout keeps
    both classes represented.
    """
    labelled = list(labelled)
    rng = random.Random(seed)
    pos = [lp for lp in labelled if lp.is_match]
    neg = [lp for lp in labelled if not lp.is_match]
    rng.shuffle(pos)
    rng.shuffle(neg)

    def _cut(items: list[LabelledPair]) -> tuple[list[LabelledPair], list[LabelledPair]]:
        k = max(1, int(round(len(items) * (1.0 - holdout_fraction))))
        return items[:k], items[k:]

    pos_cal, pos_hold = _cut(pos)
    neg_cal, neg_hold = _cut(neg)
    cal = tuple(pos_cal + neg_cal)
    hold = tuple(pos_hold + neg_hold)
    return cal, hold
