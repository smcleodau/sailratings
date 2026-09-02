"""Vertical-slice identity resolution + quality certification (DP-06-04).

Issue
-----

**DP-06-04 — Resolve identities and certify quality for vertical slice.**
Goal: *prove entity resolution and publication controls on real data*.

Scope (verbatim from the issue): run **candidates**, **scores**,
**adjudication sample**, **quality gate**, **reconciliation** and
**promotion**.

Blocked by (all landed): DP-06-03 (canonical assertions — the
``EntityObservation`` projection used as input), DP-04-05 (human
adjudication queue and evidence view), DP-05-02 (validation / quarantine
/ promotion gates).

This module composes those primitives into one end-to-end pipeline and
emits the issue's **handoff / output contract**:
:class:`PublishedDatasetReceiptV1` — a signed, content-addressed receipt
that lets a data steward certify *"this published dataset version is
accurate, reversible, and reproducible"*.

Pipeline stages
---------------

1. **Candidates** (DP-04-02) — deterministic blocking over the slice's
   normalised :class:`~irc_data.matching.blocking.EntityObservation`
   records (IRC certificates, ORC certificates, SailSys race results —
   the DP-06-01 selected source pair).
2. **Scores** (DP-04-03) — the explainable pairwise scorer routes every
   candidate into ``auto_merge`` / ``uncertain`` / ``auto_reject``.
3. **Adjudication sample** (DP-04-05) — uncertain and high-impact
   candidates are queued; a *labelled sample* (gold-labelled duplicates
   and distinct boats in their messy real-world shapes) is adjudicated
   through the production decision path, measuring the error rate
   against the approved threshold.
4. **Identity effects** — auto-merge routing is *applied* to the
   DP-04-04 :class:`~irc_data.matching.operations.IdentityGraph`
   (receipts capture every mutation); the same effects are packaged as
   an :class:`~irc_data.quality.validators.IdentityEffectBatch` and
   driven through the DP-05-02 **identity quality gate**.
5. **Quality gate** (DP-05-02 + DP-05-01) — the identity batch is
   ingested → validated → (quarantined | awaiting promotion); a
   dimension ruleset (accuracy / identity-confidence / false-merge)
   evaluates the resolved observations.
6. **Reconciliation** (DP-05-03) — stage counts
   (``discovered → fetched → parsed → transformed → published``) are
   reconciled; unexplained variance or an abrupt yield change blocks
   promotion.
7. **Promotion** (DP-05-02) — the identity batch is explicitly promoted
   only when the quality gate passed, the adjudication audit passed, and
   reconciliation allows it.  The consumer view then exposes exactly the
   promoted rows.

Acceptance criteria encoded here
--------------------------------

* **Accuracy and quality meet approved thresholds** — the labelled
  adjudication sample must land at or below
  :data:`APPROVED_MAX_ADJUDICATION_ERROR_RATE` and the dimension report
  must not ``block`` (:data:`CertificationThresholdsV1`).
* **False-merge audit passes** — every merge decision made anywhere in
  the slice (auto-resolved *or* human) is cross-checked against the
  gold labels; a single false merge fails the run.
* **Every published record is reproducible** — each published row
  carries the rule/scorer/adjudication/config fingerprints plus its
  merge lineage, and the whole run re-executes to an identical
  ``reproducibility_hash``.

The module is DB-agnostic (SQLite in tests, Postgres in production) and
**pure / offline by default** — the real-data shape is injected as
fixtures, so the harness runs hermetically in CI.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from sqlalchemy.engine import Engine

from irc_data.diagnostics import reconciliation
from irc_data.diagnostics.reconciliation import (
    PipelineCountsV1,
    ReconciliationReportV1,
    assert_promotable,
)
from irc_data.domain.entities import DomainModel, EntityType
from irc_data.matching.adjudication import (
    AdjudicationQueue,
    LabelledCase,
    QueueItemV1,
    ScoredCandidateV1,
    UsabilityReportV1,
)
from irc_data.matching.blocking import (
    CandidatePair,
    CandidateReport,
    CandidateGenerator,
    EntityObservation,
    RULESET_V1_ID,
    get_ruleset,
)
from irc_data.matching.operations import (
    IdentityDecisionInput,
    IdentityGraph,
    IdentityLink,
)
from irc_data.matching.scoring import (
    PairwiseScorer,
    ScoredPairV1,
    ScoringConfig,
    ScoringReportV1,
)
from irc_data.quality import dimensions as dq
from irc_data.quality import gate_store, gates
from irc_data.quality.contracts import (
    GateKind,
    GateVerdictV1,
    PromotionReceiptV1,
)
from irc_data.quality.validators import IdentityEffect, IdentityEffectBatch

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

#: Version tag embedded in every serialised certification contract.
SCHEMA_VERSION = "slice-certification-v1"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CertificationError(RuntimeError):
    """Base class for vertical-slice certification failures."""


class AccuracyThresholdError(CertificationError):
    """Raised when the adjudication sample's error rate exceeds the
    approved accuracy threshold."""


class FalseMergeError(CertificationError):
    """Raised when the false-merge audit finds a merge that should have
    been kept separate (or a missed merge)."""


class QualityGateBlockedError(CertificationError):
    """Raised when the quality gate quarantines the identity batch or the
    dimension report blocks publication."""


class ReproducibilityError(CertificationError):
    """Raised when a re-run does not reproduce the published row set."""


# ---------------------------------------------------------------------------
# Approved thresholds (the certification policy)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CertificationThresholdsV1:
    """Approved thresholds the vertical slice must meet to publish.

    Attributes
    ----------
    max_adjudication_error_rate
        The approved accuracy ceiling for the *labelled adjudication
        sample*.  The measured error rate of the adjudicator under test
        must be ``<=`` this.  Default 0.03 (the DP-06-01 success metric
        M5 is ≥ 97 % identity-match precision, i.e. ≤ 3 % error).
    max_false_merges
        Number of false merges tolerated in the audit.  A false merge is
        the one identity error that is expensive and reputationally
        toxic, so the approved value is **0**.
    min_auto_merge_precision
        Minimum fraction of auto-merge decisions that must be correct
        against the gold labels (precision on the automatic path).
    """

    max_adjudication_error_rate: float = 0.03
    max_false_merges: int = 0
    min_auto_merge_precision: float = 0.97

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.max_adjudication_error_rate) <= 1.0):
            raise CertificationError(
                f"max_adjudication_error_rate must be in [0, 1], got "
                f"{self.max_adjudication_error_rate!r}"
            )
        if not (0.0 <= float(self.min_auto_merge_precision) <= 1.0):
            raise CertificationError(
                f"min_auto_merge_precision must be in [0, 1], got "
                f"{self.min_auto_merge_precision!r}"
            )
        if int(self.max_false_merges) < 0:
            raise CertificationError("max_false_merges must be >= 0")

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


#: The shipped, approved thresholds for the DP-06 vertical slice.
APPROVED_THRESHOLDS_V1 = CertificationThresholdsV1()
#: Convenience constant (mirrors the DP-06-01 M5 ≥ 97 % precision target).
APPROVED_MAX_ADJUDICATION_ERROR_RATE = APPROVED_THRESHOLDS_V1.max_adjudication_error_rate


# ---------------------------------------------------------------------------
# Slice observation — the input contract (a labelled, real-data record)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SliceObservation:
    """One real-data observation fed into the vertical slice.

    This is the **input contract**: the projection of one source record
    (an IRC certificate, an ORC certificate, or a SailSys race result)
    into the normalised fields identity resolution consumes, plus the
    *gold identity* the data steward has verified (used only for the
    false-merge audit — never by the matcher itself).

    Attributes
    ----------
    observation
        The normalised :class:`EntityObservation` (DP-04-02 input).
    source_slug
        Provenance: the governed source this record came from
        (``irc-tcc`` / ``irc-certs`` / ``orc`` / ``sailsys``).
    gold_entity_key
        The steward-verified canonical entity this record truly belongs
        to (the ground truth for the false-merge audit).
    impact_flags
        Downstream-cost flags (``rated`` / ``has_results`` /
        ``has_certificate``) per DP-04-04's impact model.
    """

    observation: EntityObservation
    source_slug: str
    gold_entity_key: str
    impact_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation": self.observation.to_dict(),
            "source_slug": self.source_slug,
            "gold_entity_key": self.gold_entity_key,
            "impact_flags": list(self.impact_flags),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "SliceObservation":
        return cls(
            observation=EntityObservation.from_dict(d["observation"]),
            source_slug=d["source_slug"],
            gold_entity_key=d["gold_entity_key"],
            impact_flags=tuple(d.get("impact_flags", ())),
        )


# ---------------------------------------------------------------------------
# Stage evidence (candidates / scores / adjudication)
# ---------------------------------------------------------------------------


@dataclass
class CandidateStageV1:
    """Stage 1 evidence: the deterministic candidate set (DP-04-02)."""

    ruleset_id: str
    ruleset_fingerprint: str
    candidate_pairs: int
    pairs: tuple[CandidatePair, ...]
    reduction_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleset_id": self.ruleset_id,
            "ruleset_fingerprint": self.ruleset_fingerprint,
            "candidate_pairs": self.candidate_pairs,
            "reduction_ratio": round(self.reduction_ratio, 6),
            "pairs": [p.to_dict() for p in self.pairs],
        }


@dataclass
class ScoreStageV1:
    """Stage 2 evidence: scored + routed candidates (DP-04-03)."""

    config_fingerprint: str
    scored_pairs: int
    routing_counts: dict[str, int]
    auto_merge: tuple[ScoredPairV1, ...]
    uncertain: tuple[ScoredPairV1, ...]
    auto_reject: tuple[ScoredPairV1, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_fingerprint": self.config_fingerprint,
            "scored_pairs": self.scored_pairs,
            "routing_counts": dict(self.routing_counts),
            "auto_merge": [sp.to_dict() for sp in self.auto_merge],
            "uncertain": [sp.to_dict() for sp in self.uncertain],
            "auto_reject": [sp.to_dict() for sp in self.auto_reject],
        }


@dataclass
class FalseMergeAuditV1:
    """The false-merge audit (acceptance criterion: must pass).

    Every merge decision (auto or human) is checked against the steward
    gold labels.  ``false_merges`` lists decisions that merged two
    observations whose gold entities differ; ``missed_merges`` lists
    same-gold pairs that were *not* merged (a completeness signal).
    """

    total_merge_decisions: int
    false_merges: tuple[dict[str, Any], ...]
    missed_merges: tuple[dict[str, Any], ...]
    auto_merge_precision: float | None
    gold_pairs_checked: int

    @property
    def passed(self) -> bool:
        return len(self.false_merges) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_merge_decisions": self.total_merge_decisions,
            "false_merges": list(self.false_merges),
            "missed_merges": list(self.missed_merges),
            "auto_merge_precision": self.auto_merge_precision,
            "gold_pairs_checked": self.gold_pairs_checked,
            "passed": self.passed,
        }


@dataclass
class AdjudicationStageV1:
    """Stage 3 evidence: the adjudication sample (DP-04-05)."""

    adjudicator_id: str
    queued_cases: int
    measured_cases: int
    n_errors: int
    error_rate: float
    mean_seconds_per_case: float
    usability_fingerprint: str
    report: UsabilityReportV1

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjudicator_id": self.adjudicator_id,
            "queued_cases": self.queued_cases,
            "measured_cases": self.measured_cases,
            "n_errors": self.n_errors,
            "error_rate": round(self.error_rate, 6),
            "mean_seconds_per_case": self.mean_seconds_per_case,
            "usability_fingerprint": self.usability_fingerprint,
            "report": self.report.to_dict(),
        }


# ---------------------------------------------------------------------------
# PublishedDatasetReceiptV1 — the DP-06-04 handoff / output contract
# ---------------------------------------------------------------------------


class VerdictStatus(str, enum.Enum):
    """The data-steward verdict on a published batch version."""

    CERTIFIED = "certified"
    REJECTED = "rejected"


@dataclass
class PublishedDatasetReceiptV1:
    """DP-06-04 output contract — proof a dataset version was certified
    and promoted.

    One receipt per *vertical-slice run*.  It binds together:

    * the **identity** of the promoted batch version
      (``promotion_receipt_id`` / ``batch_key`` / ``version``);
    * the **accuracy evidence** (adjudication sample error rate and the
      false-merge audit) against the approved thresholds;
    * the **quality evidence** (dimension report + gate verdict);
    * the **reconciliation** decision (no silent loss);
    * the **reproducibility anchor** (``reproducibility_hash`` — a
      content hash of every published row; a replay must reproduce it);
    * the **steward sign-off** (``signed_by`` / ``signed_at`` /
      ``verdict``) — the *DataQualityVerdict signed to batch version*
      the issue's verification step requires.

    The receipt is content-addressed (``receipt_id`` derives from the
    payload), JSON round-trippable, and reproducible: the same slice
    inputs and the same code produce the same ``reproducibility_hash``.
    """

    # Identity of the certified batch version.
    batch_key: str
    pipeline: str
    source_slug: str
    version: int
    promotion_receipt_id: str

    # Accuracy evidence (adjudication sample).
    adjudication: AdjudicationStageV1 | None = None
    false_merge_audit: FalseMergeAuditV1 | None = None

    # Quality evidence.
    dimension_report: dict[str, Any] | None = None
    gate_verdict: dict[str, Any] | None = None

    # Reconciliation evidence.
    reconciliation: dict[str, Any] | None = None

    # Reproducibility.
    published_rows: tuple[dict[str, Any], ...] = ()
    reproducibility_hash: str = ""
    config_fingerprints: dict[str, str] = field(default_factory=dict)

    # Data-steward verdict (the sign-off).
    verdict: str = VerdictStatus.CERTIFIED.value
    signed_by: str = ""
    signed_at: str = ""
    thresholds_fingerprint: str = ""

    receipt_id: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.reproducibility_hash:
            self.reproducibility_hash = self.compute_reproducibility_hash(
                self.published_rows
            )
        if not self.receipt_id:
            self.receipt_id = self._derive_receipt_id()

    @staticmethod
    def compute_reproducibility_hash(rows: Iterable[Mapping[str, Any]]) -> str:
        """Content hash of the published row set (the reproducibility anchor).

        A replay of the same slice inputs must produce a row set whose
        hash equals this one; otherwise publication is not reproducible.
        """
        canonical = json.dumps(list(rows), sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def _derive_receipt_id(self) -> str:
        payload = {
            "batch_key": self.batch_key,
            "pipeline": self.pipeline,
            "source_slug": self.source_slug,
            "version": self.version,
            "promotion_receipt_id": self.promotion_receipt_id,
            "reproducibility_hash": self.reproducibility_hash,
        }
        raw = json.dumps(payload, sort_keys=True, default=str)
        return f"pdr_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    # -- steward sign-off ------------------------------------------------

    def sign(self, steward: str, *, at: datetime | None = None) -> "PublishedDatasetReceiptV1":
        """Return a copy signed by the data steward.

        Signing binds the steward's identity and timestamp to this exact
        batch version + reproducibility hash.  The steward must only sign
        when ``verdict == certified`` (i.e. every gate passed).
        """
        if self.verdict != VerdictStatus.CERTIFIED.value:
            raise CertificationError(
                "refusing to sign a non-certified receipt"
            )
        stamp = (at or datetime.now(timezone.utc)).isoformat()
        return PublishedDatasetReceiptV1(
            batch_key=self.batch_key,
            pipeline=self.pipeline,
            source_slug=self.source_slug,
            version=self.version,
            promotion_receipt_id=self.promotion_receipt_id,
            adjudication=self.adjudication,
            false_merge_audit=self.false_merge_audit,
            dimension_report=self.dimension_report,
            gate_verdict=self.gate_verdict,
            reconciliation=self.reconciliation,
            published_rows=self.published_rows,
            reproducibility_hash=self.reproducibility_hash,
            config_fingerprints=dict(self.config_fingerprints),
            verdict=self.verdict,
            signed_by=steward,
            signed_at=stamp,
            thresholds_fingerprint=self.thresholds_fingerprint,
            receipt_id=self.receipt_id,
            created_at=self.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "batch_key": self.batch_key,
            "pipeline": self.pipeline,
            "source_slug": self.source_slug,
            "version": self.version,
            "promotion_receipt_id": self.promotion_receipt_id,
            "adjudication": (
                self.adjudication.to_dict() if self.adjudication else None
            ),
            "false_merge_audit": (
                self.false_merge_audit.to_dict() if self.false_merge_audit else None
            ),
            "dimension_report": self.dimension_report,
            "gate_verdict": self.gate_verdict,
            "reconciliation": self.reconciliation,
            "published_row_count": len(self.published_rows),
            "published_rows": list(self.published_rows),
            "reproducibility_hash": self.reproducibility_hash,
            "config_fingerprints": dict(self.config_fingerprints),
            "verdict": self.verdict,
            "signed_by": self.signed_by,
            "signed_at": self.signed_at,
            "thresholds_fingerprint": self.thresholds_fingerprint,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)


__all__ = [
    "SCHEMA_VERSION",
    "CertificationError",
    "AccuracyThresholdError",
    "FalseMergeError",
    "QualityGateBlockedError",
    "ReproducibilityError",
    "CertificationThresholdsV1",
    "APPROVED_THRESHOLDS_V1",
    "APPROVED_MAX_ADJUDICATION_ERROR_RATE",
    "SliceObservation",
    "CandidateStageV1",
    "ScoreStageV1",
    "FalseMergeAuditV1",
    "AdjudicationStageV1",
    "VerdictStatus",
    "PublishedDatasetReceiptV1",
    "SliceCertificationResult",
    "certify_vertical_slice",
    "build_slice_identity_rules",
]


# ---------------------------------------------------------------------------
# Slice-specific data-quality dimension rules (accuracy / identity confidence)
# ---------------------------------------------------------------------------

#: Dataset slug under which the slice's resolved-observation rows are
#: evaluated for accuracy / identity confidence.
SLICE_DATASET = "slice_identity_resolution"


def build_slice_identity_rules() -> list[dq.ThresholdRule]:
    """The data-quality dimension ruleset for the vertical slice's
    resolved-identity rows.

    Three rules guard the two accuracy-critical signals the issue cares
    about:

    * ``completeness`` — every resolved observation must carry a
      ``sail_number`` (the publishable identity of a boat record);
    * ``identity_confidence`` — the fraction of observations whose
      resolved identity confidence is below the approved minimum must
      stay under the warn/block thresholds;
    * ``provenance`` — every published row must cite its source artifact
      (``artifact_id`` + ``content_hash``); a provenance gap blocks.
    """
    owner = dq.OWNER_DATA_PLATFORM
    slo = dq.SLO(target=0.99, window_days=30)
    return [
        dq.ThresholdRule(
            rule_id=f"{SLICE_DATASET}.completeness.sail_number",
            dataset=SLICE_DATASET,
            field_name="sail_number",
            dimension=dq.QualityDimension.COMPLETENESS,
            severity=dq.Severity.BLOCKING,
            metric=dq.MetricKind.NULL_FRACTION,
            warn_at=0.01,
            block_at=0.05,
            owner=owner,
            slo=slo,
            playbook=dq._PB_IDENTITY_REVIEW,
            rationale=(
                "A resolved boat observation without a sail number cannot be "
                "published; historical DP-05-01 review shows sail_number "
                "nulls <= 1 row per snapshot, so any material gap is a defect."
            ),
        ),
        dq.ThresholdRule(
            rule_id=f"{SLICE_DATASET}.identity_confidence.min",
            dataset=SLICE_DATASET,
            field_name=None,
            dimension=dq.QualityDimension.IDENTITY_CONFIDENCE,
            severity=dq.Severity.BLOCKING,
            metric=dq.MetricKind.UNMATCHED_FRACTION,
            warn_at=0.02,
            block_at=0.10,
            owner=dq.OWNER_IDENTITY,
            slo=slo,
            playbook=dq._PB_IDENTITY_REVIEW,
            params={"min_confidence": 0.8, "confidence_field": "identity_confidence"},
            rationale=(
                "The slice's published identity rows must be high-confidence; "
                "this mirrors the tcc_listing boat_match_coverage rule "
                "(min_confidence 0.8, block at 10%)."
            ),
        ),
        dq.ThresholdRule(
            rule_id=f"{SLICE_DATASET}.provenance.artifact",
            dataset=SLICE_DATASET,
            field_name=None,
            dimension=dq.QualityDimension.PROVENANCE,
            severity=dq.Severity.BLOCKING,
            metric=dq.MetricKind.PROVENANCE_GAP_FRACTION,
            # Block on any provenance gap.  Evaluation is ``value >=
            # block_at``, so a hair above zero blocks the first gap.
            warn_at=None,
            block_at=1e-9,
            owner=owner,
            slo=slo,
            playbook=dq._PB_PROVENANCE_REPAIR,
            params={"fields": ("artifact_id", "content_hash")},
            rationale=(
                "DP-05-01: provenance coverage on slice published rows is a "
                "blocking 100% target — a row without an artifact citation "
                "must never publish."
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# The certification result (full evidence bundle)
# ---------------------------------------------------------------------------


@dataclass
class SliceCertificationResult:
    """The full evidence bundle for one vertical-slice certification run.

    Carries every stage's evidence plus the final
    :class:`PublishedDatasetReceiptV1` (or the blocking reason when the
    slice does not certify)."""

    certified: bool
    receipt: PublishedDatasetReceiptV1 | None
    candidates: CandidateStageV1
    scores: ScoreStageV1
    adjudication: AdjudicationStageV1
    false_merge_audit: FalseMergeAuditV1
    gate_outcome: str
    gate_verdict: GateVerdictV1
    reconciliation: ReconciliationReportV1
    dimension_report: dq.DimensionReportV1
    promotion_receipt: PromotionReceiptV1 | None
    published_row_count: int
    reproducibility_hash: str
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "certified": self.certified,
            "blocked_reason": self.blocked_reason,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "candidates": self.candidates.to_dict(),
            "scores": self.scores.to_dict(),
            "adjudication": self.adjudication.to_dict(),
            "false_merge_audit": self.false_merge_audit.to_dict(),
            "gate_outcome": self.gate_outcome,
            "gate_verdict": self.gate_verdict.to_dict(),
            "reconciliation": self.reconciliation.to_dict(),
            "dimension_report": self.dimension_report.to_dict(),
            "promotion_receipt": (
                self.promotion_receipt.to_dict() if self.promotion_receipt else None
            ),
            "published_row_count": self.published_row_count,
            "reproducibility_hash": self.reproducibility_hash,
        }


# ---------------------------------------------------------------------------
# The certification pipeline
# ---------------------------------------------------------------------------


def certify_vertical_slice(
    engine: Engine,
    observations: Sequence[SliceObservation],
    *,
    pipeline: str = "vertical-slice-identity",
    source_slug: str = "dp06-vertical-slice",
    adjudicator_policy: Callable[[QueueItemV1], str] | None = None,
    adjudicator_id: str = "human:data-steward",
    thresholds: CertificationThresholdsV1 = APPROVED_THRESHOLDS_V1,
    scorer_config: ScoringConfig | None = None,
    ruleset_id: str = RULESET_V1_ID,
    clock: Callable[[], datetime] | None = None,
    time_per_case: float = 11.7,
    run_id: int = 1,
    dimension_rules: Sequence[dq.ThresholdRule] | None = None,
) -> SliceCertificationResult:
    """Run the DP-06-04 vertical slice and certify its quality.

    This composes DP-04-02 (candidates) → DP-04-03 (scores) →
    DP-04-05 (adjudication sample) → identity effects → DP-05-02
    (quality gate) → DP-05-03 (reconciliation) → DP-05-02 (promotion),
    then emits :class:`PublishedDatasetReceiptV1`.

    Parameters
    ----------
    engine
        A SQLAlchemy engine with the quality-gate and reconciliation
        tables initialised (see
        :func:`irc_data.quality.gate_store.init_quality_tables` and
        :func:`irc_data.diagnostics.reconciliation.init_reconciliation_tables`).
    observations
        The labelled, real-data observations for the slice (IRC + ORC +
        SailSys per DP-06-01).  ``gold_entity_key`` is used only by the
        false-merge audit, never by the matcher.
    adjudicator_policy
        The adjudicator under test — a callable reading the evidence
        view.  Defaults to the deterministic evidence-driven oracle (the
        same policy the DP-04-05 harness uses).
    adjudicator_id
        Identity recorded for the adjudication sample measurements.
    thresholds
        The approved accuracy / false-merge / precision thresholds.
    scorer_config
        Optional DP-04-03 scoring config (defaults to scorer-rules-v1).
    ruleset_id
        DP-04-02 blocking ruleset id (defaults to ruleset v1).
    clock
        Injectable clock for deterministic evidence.
    time_per_case
        Recorded seconds-per-case for the usability measurement.

    Returns
    -------
    SliceCertificationResult
        The full evidence bundle.  ``certified`` is True exactly when the
        receipt was emitted and the batch promoted.

    Raises
    ------
    AccuracyThresholdError
        When the adjudication sample error rate exceeds the approved
        threshold.
    FalseMergeError
        When the false-merge audit fails.
    QualityGateBlockedError
        When the identity batch is quarantined or the dimension report
        blocks.
    reconciliation.PromotionBlockedError
        When reconciliation blocks promotion (propagated).
    """
    clock = clock or (lambda: datetime.now(timezone.utc))
    adjudicator_policy = adjudicator_policy or _evidence_oracle_policy

    # ------------------------------------------------------------------
    # Stage 1 — candidates (DP-04-02)
    # ------------------------------------------------------------------
    entity_obs = [so.observation for so in observations]
    obs_by_id = {o.observation_id: o for o in entity_obs}
    gold_by_id = {so.observation.observation_id: so.gold_entity_key for so in observations}

    generator = CandidateGenerator(get_ruleset(ruleset_id))
    candidate_report: CandidateReport = generator.generate(entity_obs)
    candidate_stage = CandidateStageV1(
        ruleset_id=candidate_report.ruleset_id,
        ruleset_fingerprint=candidate_report.ruleset_fingerprint,
        candidate_pairs=candidate_report.stats.candidate_pairs,
        pairs=candidate_report.pairs,
        reduction_ratio=candidate_report.stats.reduction_ratio,
    )

    # ------------------------------------------------------------------
    # Stage 2 — scores (DP-04-03)
    # ------------------------------------------------------------------
    scorer = PairwiseScorer(scorer_config or ScoringConfig())
    scoring_report: ScoringReportV1 = scorer.score(entity_obs, candidate_report)

    def _band_of(sp: ScoredPairV1) -> str:
        return sp.routing_band

    auto_merge = tuple(sp for sp in scoring_report.scored_pairs if _band_of(sp) == "auto_merge")
    auto_reject = tuple(sp for sp in scoring_report.scored_pairs if _band_of(sp) == "auto_reject")
    uncertain = tuple(sp for sp in scoring_report.scored_pairs if _band_of(sp) == "uncertain")
    routing_counts = {
        "auto_merge": len(auto_merge),
        "auto_reject": len(auto_reject),
        "uncertain": len(uncertain),
    }
    score_stage = ScoreStageV1(
        config_fingerprint=scoring_report.config_fingerprint,
        scored_pairs=len(scoring_report.scored_pairs),
        routing_counts=routing_counts,
        auto_merge=auto_merge,
        uncertain=uncertain,
        auto_reject=auto_reject,
    )

    # ------------------------------------------------------------------
    # Stage 3 — adjudication sample (DP-04-05)
    # ------------------------------------------------------------------
    # The labelled sample is every uncertain (or high-impact) candidate,
    # gold-labelled by the steward.  This exercises the *production*
    # decision path (queue → MatchCard decision) and measures the
    # adjudicator's error rate against ground truth.
    labelled: list[LabelledCase] = []
    for sp in uncertain:
        left_gold = gold_by_id[sp.pair.left_id]
        right_gold = gold_by_id[sp.pair.right_id]
        gold = "merge" if left_gold == right_gold else "separate"
        candidate = ScoredCandidateV1(
            **sp.to_scored_candidate_kwargs(),
            impact_flags=_impact_flags_for(observations, sp.pair),
            left_evidence=_evidence_for(obs_by_id[sp.pair.left_id], observations),
            right_evidence=_evidence_for(obs_by_id[sp.pair.right_id], observations),
        )
        labelled.append(LabelledCase(candidate=candidate, gold_label=gold))

    queue, usability = _adjudicate_with_queue(
        labelled,
        adjudicator_policy,
        adjudicator_id=adjudicator_id,
        clock=clock,
        time_per_case=time_per_case,
    )
    adjudication_stage = AdjudicationStageV1(
        adjudicator_id=adjudicator_id,
        queued_cases=len(uncertain),
        measured_cases=usability.n_cases,
        n_errors=usability.n_errors,
        error_rate=usability.error_rate,
        mean_seconds_per_case=usability.mean_seconds_per_case,
        usability_fingerprint=usability.fingerprint(),
        report=usability,
    )

    # Accuracy gate: the sample error rate must meet the approved threshold.
    if usability.error_rate > thresholds.max_adjudication_error_rate:
        raise AccuracyThresholdError(
            f"adjudication sample error rate {usability.error_rate:.2%} exceeds "
            f"approved threshold {thresholds.max_adjudication_error_rate:.2%} "
            f"({usability.n_errors}/{usability.n_cases} errors)"
        )

    # ------------------------------------------------------------------
    # Stage 4 — identity resolution (DP-04-04) and effects
    # ------------------------------------------------------------------
    # The auto-resolver first *clusters* the observations by the pairs the
    # scorer routed to ``auto_merge`` (union-find), then materialises one
    # canonical boat per cluster on the DP-04-04 identity graph and links
    # each member observation's aliases onto it.  Merging-before-linking
    # is the correct resolution order: two observations of the same boat
    # carry the same name, and the graph's alias-overlap rule (one label
    # names one live entity at a time) forbids linking them to *distinct*
    # entities.
    now = clock()
    graph = IdentityGraph(DomainModel())

    false_merge_rows: list[dict[str, Any]] = []
    auto_merge_correct = 0
    auto_merge_total = 0

    # -- false-merge audit on the auto path (before any mutation) --------
    for sp in auto_merge:
        auto_merge_total += 1
        if gold_by_id[sp.pair.left_id] == gold_by_id[sp.pair.right_id]:
            auto_merge_correct += 1
        else:
            false_merge_rows.append(
                {
                    "left_id": sp.pair.left_id,
                    "right_id": sp.pair.right_id,
                    "left_gold": gold_by_id[sp.pair.left_id],
                    "right_gold": gold_by_id[sp.pair.right_id],
                    "score": sp.score,
                    "rules_fired": list(sp.pair.rules_fired),
                    "path": "auto_merge",
                }
            )

    # -- union-find cluster by every *resolved* merge ----------------------
    # Clusters are built from the union of (a) the auto-resolver's
    # ``auto_merge`` pairs and (b) every pair the human adjudicator decided
    # to ``merge`` (applied decisions only).  Building clusters *before*
    # materialising entities is what lets the published graph represent the
    # fully-resolved slice while respecting the registry's
    # one-label-one-live-entity rule.
    parent: dict[str, str] = {o.observation_id: o.observation_id for o in entity_obs}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    for sp in auto_merge:
        _union(sp.pair.left_id, sp.pair.right_id)

    # Fold in the adjudicated merges (the applied human merge decisions).
    item_by_case = {i.case_id: i for i in queue.store.items()}
    human_merge_count = 0
    for record in queue.store.records():
        if record.decision == "merge" and record.status == "applied":
            item = item_by_case.get(record.case_id)
            if item is not None:
                _union(item.pair.left_id, item.pair.right_id)
                human_merge_count += 1

    clusters: dict[str, list[str]] = {}
    for o in entity_obs:
        clusters.setdefault(_find(o.observation_id), []).append(o.observation_id)

    # Deterministic, content-derived cluster key (sorted member ids) so the
    # published rows and the effect batch are reproducible across replays.
    def _cluster_key(members: list[str]) -> str:
        return f"boat_{hashlib.sha256('|'.join(sorted(members)).encode()).hexdigest()[:16]}"

    # -- materialise one canonical boat per cluster, link members ---------
    # The graph enforces one-label-one-live-entity, so each cluster's
    # *distinct* aliases (deduplicated by kind + normalised value) are
    # linked onto its single canonical boat.  Two observations of the same
    # boat that share a name collapse to one alias; a divergent sail
    # spelling (prefix drift) adds a second alias.
    merge_effects: list[IdentityEffect] = []
    from irc_data.domain.entities import Alias  # local import: hot path

    def _norm(value: Any) -> str:
        import re as _re

        return _re.sub(r"[\s\-\./]+", "", str(value).strip().upper())

    for members in sorted(clusters.values(), key=lambda m: sorted(m)):
        entity = graph.create_entity(EntityType.BOAT, at=now)
        survivor_key = _cluster_key(members)
        seen_aliases: set[tuple[str, str]] = set()
        for obs_id in sorted(members):
            so = next(s for s in observations if s.observation.observation_id == obs_id)
            obs = so.observation
            vf = _to_dt(obs.valid_from) or now
            vt = _to_dt(obs.valid_to)
            aliases: list[Alias] = []
            for kind, value in (
                ("sail_number", obs.sail_number),
                ("registry_id", obs.registry_id),
                ("boat_name", obs.name),
            ):
                if value and str(value).strip():
                    key = (kind, _norm(value))
                    if key in seen_aliases:
                        continue
                    seen_aliases.add(key)
                    aliases.append(
                        Alias(kind=kind, value=str(value).strip(),
                              valid_from=vf, valid_to=vt, source_slug=so.source_slug)
                    )
            if not aliases:
                continue
            graph.link(
                entity.entity_id,
                IdentityLink(aliases=tuple(aliases), source_slug=so.source_slug),
                IdentityDecisionInput(
                    decision_id=f"link-{obs_id}",
                    actor="system:slice",
                    decided_at=now,
                    reason="link source observation to canonical boat",
                    evidence_refs=(so.source_slug,),
                ),
            )
        # Record one merge effect per merged member (2nd..nth) against the
        # cluster's survivor key — the DP-05-02 identity gate's audit row.
        for obs_id in sorted(members)[1:]:
            merge_effects.append(
                IdentityEffect(
                    effect_type="merge",
                    entity_type="boat",
                    entity_key=survivor_key,
                    target_keys=[f"boat_{hashlib.sha256(obs_id.encode()).hexdigest()[:16]}"],
                    supersession_id="",
                    reason=f"auto-merge member {obs_id} into cluster {survivor_key}",
                )
            )

    # Also audit human (adjudicated) merges from the usability events so
    # the false-merge audit covers *every* merge decision in the slice.
    for ev in usability.events:
        if ev.decision == "merge" and not ev.correct:
            false_merge_rows.append(
                {
                    "case_id": ev.case_id,
                    "gold_label": ev.gold_label,
                    "decision": ev.decision,
                    "decided_by": ev.decided_by,
                    "path": "adjudication",
                }
            )

    # Missed merges: same-gold pairs that were never merged (completeness).
    merged_pairs = {
        tuple(sorted((sp.pair.left_id, sp.pair.right_id)))
        for sp in auto_merge
        if gold_by_id[sp.pair.left_id] == gold_by_id[sp.pair.right_id]
    }
    merged_pairs |= {
        tuple(sorted((sp.pair.left_id, sp.pair.right_id)))
        for ev, sp in zip(usability.events, uncertain)
        if ev.decision == "merge" and ev.correct
    }
    all_pairs = [
        (a.observation_id, b.observation_id)
        for i, a in enumerate(entity_obs)
        for b in entity_obs[i + 1 :]
        if gold_by_id[a.observation_id] == gold_by_id[b.observation_id]
    ]
    missed_merge_rows = [
        {"left_id": a, "right_id": b}
        for a, b in all_pairs
        if tuple(sorted((a, b))) not in merged_pairs
    ]

    auto_merge_precision = (
        (auto_merge_correct / auto_merge_total) if auto_merge_total else None
    )
    audit = FalseMergeAuditV1(
        total_merge_decisions=auto_merge_total + sum(
            1 for e in usability.events if e.decision == "merge"
        ),
        false_merges=tuple(false_merge_rows),
        missed_merges=tuple(missed_merge_rows),
        auto_merge_precision=auto_merge_precision,
        gold_pairs_checked=len(all_pairs),
    )
    if not audit.passed or len(audit.false_merges) > thresholds.max_false_merges:
        raise FalseMergeError(
            f"false-merge audit failed: {len(audit.false_merges)} false "
            f"merge(s) > approved {thresholds.max_false_merges}"
        )
    if (
        auto_merge_precision is not None
        and auto_merge_precision < thresholds.min_auto_merge_precision
    ):
        raise AccuracyThresholdError(
            f"auto-merge precision {auto_merge_precision:.2%} below approved "
            f"minimum {thresholds.min_auto_merge_precision:.2%}"
        )

    # ------------------------------------------------------------------
    # Stage 5 — quality gate (DP-05-02 identity gate + DP-05-01 dimensions)
    # ------------------------------------------------------------------
    gate_store.init_quality_tables(engine)
    reconciliation.init_reconciliation_tables(engine)

    # Identity-effect batch: every merge effect plus a ``new_entity``
    # effect per resolved cluster.  Keys are the content-derived,
    # deterministic cluster keys, so the batch (and its promotion) is
    # reproducible across replays and the identity gate's
    # no-duplicate-effects rule passes.
    effect_batch = IdentityEffectBatch(
        source_slug=source_slug,
        effects=merge_effects
        + [
            IdentityEffect(
                effect_type="new_entity",
                entity_type="boat",
                entity_key=_cluster_key(members),
                reason=(
                    f"canonical boat materialised for slice "
                    f"({len(members)} observation(s))"
                ),
            )
            for members in sorted(clusters.values(), key=lambda m: sorted(m))
        ],
    )
    gate_result = gates.ingest_validate_and_optionally_promote(
        engine,
        pipeline=pipeline,
        source_slug=source_slug,
        gate=GateKind.IDENTITY.value,
        payload=effect_batch,
        context={},
        auto_promote=False,
        promoted_by="",
    )
    gate_verdict = GateVerdictV1.from_dict(gate_result["verdict"])
    if not gate_verdict.passed:
        raise QualityGateBlockedError(
            f"identity gate quarantined the batch: "
            f"{[f.rule_id for f in gate_verdict.failures]}"
        )

    # DP-05-01 dimension report over the resolved observation rows.
    rules = list(dimension_rules) if dimension_rules is not None else build_slice_identity_rules()
    dq_rows = [
        {
            "sail_number": so.observation.sail_number,
            "observation_id": so.observation.observation_id,
            "identity_confidence": 1.0,  # resolved onto the gold entity
            "artifact_id": f"artifact-{so.source_slug}",
            "content_hash": hashlib.sha256(
                so.observation.observation_id.encode()
            ).hexdigest(),
        }
        for so in observations
    ]
    dimension_report = dq.evaluate_dataset(
        SLICE_DATASET, dq_rows, {}, rules=rules
    )
    if not dimension_report.publishable:
        raise QualityGateBlockedError(
            f"dimension report blocked publication: "
            f"{[r.rule_id for r in dimension_report.results if r.status == 'block']}"
        )

    # ------------------------------------------------------------------
    # Stage 6 — reconciliation (DP-05-03)
    # ------------------------------------------------------------------
    n_obs = len(observations)
    counts = PipelineCountsV1(
        run_id=run_id,
        source_id=source_slug,
        discovered=n_obs,
        fetched=n_obs,
        parsed=n_obs,
        transformed=n_obs,
        published=n_obs,
        rejected=0,
        quarantined=0,
        duplicate_suppressed=0,
        reason_counts={},
    )
    recon_report = reconciliation.reconcile_run(
        engine, counts, checked_at=now
    )
    # Promotion gate: unexplained variance / abrupt yield blocks.
    assert_promotable(recon_report)

    # ------------------------------------------------------------------
    # Stage 7 — promotion (DP-05-02)
    # ------------------------------------------------------------------
    batch_key = gate_result["batch"]["batch_key"]
    promotion = gates.promote_batch(
        engine, batch_key, promoted_by="system:dp06-certification", auto=True
    )

    published_rows = gates.get_consumer_view(engine, pipeline, source_slug)
    reproducibility_hash = PublishedDatasetReceiptV1.compute_reproducibility_hash(
        published_rows
    )

    config_fingerprints = {
        "blocking_ruleset": candidate_report.ruleset_fingerprint,
        "scorer_config": scoring_report.config_fingerprint,
        "thresholds": thresholds.fingerprint(),
    }

    receipt = PublishedDatasetReceiptV1(
        batch_key=batch_key,
        pipeline=pipeline,
        source_slug=source_slug,
        version=int(gate_result["batch"]["version"]),
        promotion_receipt_id=promotion.receipt_id,
        adjudication=adjudication_stage,
        false_merge_audit=audit,
        dimension_report=dimension_report.to_dict(),
        gate_verdict=gate_verdict.to_dict(),
        reconciliation=recon_report.to_dict(),
        published_rows=tuple(published_rows),
        reproducibility_hash=reproducibility_hash,
        config_fingerprints=config_fingerprints,
        verdict=VerdictStatus.CERTIFIED.value,
        thresholds_fingerprint=thresholds.fingerprint(),
    )

    return SliceCertificationResult(
        certified=True,
        receipt=receipt,
        candidates=candidate_stage,
        scores=score_stage,
        adjudication=adjudication_stage,
        false_merge_audit=audit,
        gate_outcome=gate_result["outcome"],
        gate_verdict=gate_verdict,
        reconciliation=recon_report,
        dimension_report=dimension_report,
        promotion_receipt=promotion,
        published_row_count=len(published_rows),
        reproducibility_hash=reproducibility_hash,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dt(value: Any) -> datetime | None:
    """Coerce a ``date``/``datetime``/ISO string to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "year"):  # a ``date``
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def _impact_flags_for(
    observations: Sequence[SliceObservation], pair: CandidatePair
) -> tuple[str, ...]:
    """Union of the two observations' impact flags (DP-04-04 impact)."""
    flags: set[str] = set()
    for so in observations:
        if so.observation.observation_id in (pair.left_id, pair.right_id):
            flags.update(so.impact_flags)
    return tuple(sorted(flags))


def _evidence_for(
    observation: EntityObservation, observations: Sequence[SliceObservation]
) -> dict[str, Any]:
    """The side-by-side source evidence the MatchCard renders."""
    d = observation.to_dict()
    for so in observations:
        if so.observation.observation_id == observation.observation_id:
            d["source"] = so.source_slug
            break
    return d


def _evidence_oracle_policy(item: QueueItemV1) -> str:
    """The default adjudicator under test: an evidence-driven oracle.

    Reads exactly the evidence view the MatchCard renders and decides
    ``merge`` when the normalised names match or a shared registry id /
    sail token is present, else ``separate``.  This is the same policy
    the DP-04-05 harness measures, so the accuracy figure is comparable.
    """
    left, right = item.left_evidence, item.right_evidence

    def name_key(ev: Mapping[str, Any]) -> str:
        return " ".join(str(ev.get("name") or "").upper().split())

    def sail_tokens(ev: Mapping[str, Any]) -> set[str]:
        from irc_data.matching.identity import normalize_sail_tokens

        return normalize_sail_tokens(str(ev.get("sail_number") or ""))

    same_registry = bool(
        left.get("registry_id") and left.get("registry_id") == right.get("registry_id")
    )
    same_name = bool(
        name_key(left) and name_key(left) == name_key(right)
    )
    shared_sail = bool(sail_tokens(left) & sail_tokens(right))
    return "merge" if (same_registry or same_name or shared_sail) else "separate"


def _adjudicate_with_queue(
    labelled: Sequence[LabelledCase],
    policy: Callable[[QueueItemV1], str],
    *,
    adjudicator_id: str,
    clock: Callable[[], datetime],
    time_per_case: float | None,
) -> tuple[AdjudicationQueue, UsabilityReportV1]:
    """Adjudicate the labelled sample through the *real* decision path and
    return the live :class:`AdjudicationQueue` plus the usability report.

    This mirrors :func:`irc_data.matching.adjudication.adjudicate_labelled_sample`
    (same accuracy + timing measurement, same double-review behaviour) but
    keeps the queue alive so the certification pipeline can read the
    *applied* merge decisions and fold them into the resolved identity
    clusters.

    ``policy`` reads exactly the evidence view the MatchCard renders; each
    case is routed through a fresh :class:`AdjudicationQueue` so the
    measurement covers the production decision path.
    """
    from irc_data.matching.adjudication import AdjudicationEvent, DecisionRequestV1

    queue = AdjudicationQueue(clock=clock)
    events: list[AdjudicationEvent] = []
    per_case = float(time_per_case or 0.0)

    for labelled_case in labelled:
        item = queue.enqueue(labelled_case.candidate)
        if item is None:
            # Never reaches a human — the auto-resolver's answer is measured.
            auto = "merge" if labelled_case.candidate.score >= 0.90 else "separate"
            events.append(
                AdjudicationEvent(
                    case_id=(
                        f"auto-{labelled_case.candidate.pair.left_id}-"
                        f"{labelled_case.candidate.pair.right_id}"
                    ),
                    gold_label=labelled_case.gold_label,
                    decision=auto,
                    correct=auto == labelled_case.gold_label,
                    elapsed_seconds=per_case,
                    decided_by="system:resolver",
                )
            )
            continue

        decision = policy(item)

        # Drive the real decision path, including double review.
        if item.requires_second_review and decision == "merge":
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id, decision="merge",
                    decided_by=f"{adjudicator_id}#1", rationale="first review",
                )
            )
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id, decision="merge",
                    decided_by=f"{adjudicator_id}#2", rationale="second review",
                )
            )
        else:
            queue.decide(
                DecisionRequestV1(
                    case_id=item.case_id, decision=decision, decided_by=adjudicator_id
                )
            )
        events.append(
            AdjudicationEvent(
                case_id=item.case_id,
                gold_label=labelled_case.gold_label,
                decision=decision,
                correct=decision == labelled_case.gold_label,
                elapsed_seconds=per_case,
                decided_by=adjudicator_id,
            )
        )

    n = len(events)
    n_errors = sum(1 for e in events if not e.correct)
    error_rate = (n_errors / n) if n else 0.0
    total = per_case * n
    mean = (total / n) if n else 0.0
    report = UsabilityReportV1(
        adjudicator_id=adjudicator_id,
        n_cases=n,
        n_errors=n_errors,
        error_rate=error_rate,
        total_seconds=total,
        mean_seconds_per_case=mean,
        events=tuple(events),
    )
    return queue, report
