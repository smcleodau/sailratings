"""Validation, quarantine and promotion gates (DP-05-02) and the
data-quality dimension registry (DP-05-01).

This package prevents bad batches from entering the canonical views and
makes database health measurable:

* :mod:`irc_data.quality.dimensions` — DP-05-01: the eight data-quality
  dimensions (completeness, validity, uniqueness, consistency,
  timeliness, provenance, identity confidence, drift), per-dataset /
  per-field blocking and warning thresholds, accountable owners, SLOs
  and remediation playbooks, plus the evaluation engine that scores a
  batch into a :class:`~irc_data.quality.dimensions.DimensionReportV1`.
* :mod:`irc_data.quality.contracts` — the handoff / output contracts
  (``GateFinding``, ``QuarantineRecordV1``, ``GateVerdictV1``,
  ``PromotionReceiptV1``) and the rule taxonomy.
* :mod:`irc_data.quality.validators` — the built-in validators for the
  three gate kinds (extraction / canonical / identity).  Every rule
  class is fault-fixture-driven.
* :mod:`irc_data.quality.gate_store` — versioned batch store with
  quarantine (samples + rule failures) and explicit promotion.
  Consumers only ever see promoted versions.
* :mod:`irc_data.quality.gates` — the engine that wires validators to
  the store and owns the ingest → validate → quarantine | await
  promotion lifecycle.
"""

from irc_data.quality.contracts import (
    SCHEMA_VERSION,
    GateFinding,
    GateKind,
    GateVerdictV1,
    PromotionReceiptV1,
    QualityBatchStatus,
    QuarantineRecordV1,
    RuleClass,
)
from irc_data.quality.dimensions import (
    SCHEMA_VERSION as DIMENSIONS_SCHEMA_VERSION,
    DimensionReportV1,
    QualityDimension,
    ThresholdRule,
    assert_dataset_publishable,
    evaluate_dataset,
    published_datasets,
    validate_registry,
)

__all__ = [
    "SCHEMA_VERSION",
    "GateFinding",
    "GateKind",
    "GateVerdictV1",
    "PromotionReceiptV1",
    "QualityBatchStatus",
    "QuarantineRecordV1",
    "RuleClass",
    # DP-05-01 dimensions
    "DIMENSIONS_SCHEMA_VERSION",
    "DimensionReportV1",
    "QualityDimension",
    "ThresholdRule",
    "assert_dataset_publishable",
    "evaluate_dataset",
    "published_datasets",
    "validate_registry",
]
