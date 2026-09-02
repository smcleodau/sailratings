"""Validation, quarantine and promotion gates (DP-05-02).

This package prevents bad batches from entering the canonical views:

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

__all__ = [
    "SCHEMA_VERSION",
    "GateFinding",
    "GateKind",
    "GateVerdictV1",
    "PromotionReceiptV1",
    "QualityBatchStatus",
    "QuarantineRecordV1",
    "RuleClass",
]
