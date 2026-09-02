"""Quality-gate engine (DP-05-02).

Wires the built-in validators (:mod:`irc_data.quality.validators`) to
the versioned batch store (:mod:`irc_data.quality.gate_store`) and owns
the lifecycle::

    ingest_batch_version  →  validate_batch  →  quarantined
                                              ↘  awaiting_promotion
                                                      ↓ promote_batch
                                                  promoted (consumers see)

Public API
----------

* :func:`ingest_batch_version` — ingest a payload (extraction /
  canonical / identity) as a **new version** and stage its rows.
* :func:`validate_batch` — run the registered rules for the batch's
  gate; on any ``error``-severity finding, quarantine the batch (with
  samples + rule failures) and return the verdict.
* :func:`promote_batch` — explicit promotion (thin wrapper over the
  store that first validates if not yet validated).
* :func:`ingest_validate_and_optionally_promote` — the full pipeline in
  one call (used by the replay/backfill promotion seam and by tests).
* :func:`get_consumer_view` — the read model: promoted rows only.

The engine is DB-agnostic (SQLite in tests, Postgres in production).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from irc_data.quality import gate_store
from irc_data.quality.contracts import (
    GateFinding,
    GateKind,
    GateVerdictV1,
    PromotionReceiptV1,
    QualityBatchStatus,
    QuarantineRecordV1,
)
from irc_data.quality.validators import (
    IdentityEffectBatch,
    all_rules,
    rules_for,
)


# ---------------------------------------------------------------------------
# Payload → (rows, record_count) staging helpers
# ---------------------------------------------------------------------------


def _payload_to_rows(gate: str, payload: Any) -> list[tuple[str, Any]]:
    """Flatten a gate payload into ``(row_kind, row_dict)`` pairs.

    The staged rows are what quarantine samples and the consumer view
    read.  Extraction batches stage each extracted record; canonical
    batches stage assertions and rejects as separate row kinds;
    identity batches stage each effect.
    """
    if gate == GateKind.EXTRACTION.value:
        return [
            ("record", r.to_dict()) for r in payload.records
        ]
    if gate == GateKind.CANONICAL.value:
        rows: list[tuple[str, Any]] = [
            ("assertion", a.to_dict()) for a in payload.assertions
        ]
        rows.extend(("reject", r.to_dict()) for r in payload.rejects)
        return rows
    if gate == GateKind.IDENTITY.value:
        return [("effect", e.to_dict()) for e in payload.effects]
    raise ValueError(f"unknown gate {gate!r}")


def _payload_record_count(gate: str, payload: Any) -> int:
    if gate == GateKind.EXTRACTION.value:
        return payload.record_count()
    if gate == GateKind.CANONICAL.value:
        return payload.record_count()
    if gate == GateKind.IDENTITY.value:
        return len(payload.effects)
    raise ValueError(f"unknown gate {gate!r}")


def _payload_content_hash(gate: str, payload: Any) -> str:
    if gate == GateKind.EXTRACTION.value:
        return payload.extraction_hash
    if gate == GateKind.CANONICAL.value:
        return payload.transformation_hash
    if gate == GateKind.IDENTITY.value:
        return payload.batch_id
    raise ValueError(f"unknown gate {gate!r}")


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest_batch_version(
    engine: Engine,
    *,
    pipeline: str,
    source_slug: str,
    gate: str,
    payload: Any,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest *payload* as a new batch version and stage its rows.

    Retry/replay safety: a re-ingestion of the same ``(pipeline,
    source_slug)`` content always lands in a fresh ``version`` — the
    ``(pipeline, source_slug, version)`` uniqueness constraint means a
    previously-quarantined version is never reused.
    """
    rows = _payload_to_rows(gate, payload)
    return gate_store.ingest_batch(
        engine,
        pipeline=pipeline,
        source_slug=source_slug,
        gate=gate,
        rows=rows,
        content_hash=_payload_content_hash(gate, payload),
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def evaluate_payload(
    gate: str,
    payload: Any,
    context: dict[str, Any] | None = None,
) -> tuple[int, list[GateFinding]]:
    """Run every registered rule for *gate* against *payload*.

    Returns ``(rules_evaluated, error_findings)``.  ``warning``-severity
    findings are included in the returned list but do not by themselves
    quarantine a batch.
    """
    ctx = dict(context or {})
    findings: list[GateFinding] = []
    rules = rules_for(gate)
    for rule in rules:
        findings.extend(rule.fn(payload, ctx))
    return len(rules), findings


def validate_batch(
    engine: Engine,
    batch_key: str,
    payload: Any,
    *,
    context: dict[str, Any] | None = None,
    auto_quarantine: bool = True,
) -> GateVerdictV1:
    """Validate a staged batch and quarantine it on any error finding.

    The verdict is persisted; on failure the batch is quarantined with
    the failures and a bounded sample of the staged rows attached.

    Returns the :class:`GateVerdictV1`.
    """
    batch = gate_store.get_batch(engine, batch_key)
    if batch is None:
        raise KeyError(f"batch {batch_key!r} not found")

    gate_store.set_batch_status(
        engine, batch_key, QualityBatchStatus.VALIDATING
    )

    gate = batch["gate"]
    rules_evaluated, findings = evaluate_payload(gate, payload, context)
    errors = [f for f in findings if f.severity == "error"]
    outcome = "quarantined" if errors else "passed"

    verdict = GateVerdictV1(
        batch_key=batch_key,
        pipeline=batch["pipeline"],
        source_slug=batch["source_slug"],
        version=int(batch["version"]),
        gate=gate,
        outcome=outcome,
        rules_evaluated=rules_evaluated,
        rules_failed=len({f.rule_id for f in errors}),
        failures=errors,
        record_count=int(batch["record_count"]),
    )
    gate_store.record_verdict(engine, verdict)

    if errors and auto_quarantine:
        # Sample rows: take the first few staged rows for reviewer
        # context (bounded).
        staged = gate_store.get_batch_rows(engine, batch_key)
        sample_rows = [r["row_json"] for r in staged[:25]]
        record = QuarantineRecordV1(
            batch_key=batch_key,
            pipeline=batch["pipeline"],
            source_slug=batch["source_slug"],
            version=int(batch["version"]),
            gate=gate,
            failures=errors,
            sample_rows=sample_rows,
        )
        gate_store.quarantine_batch(engine, record)
    elif not errors:
        gate_store.set_batch_status(
            engine, batch_key, QualityBatchStatus.AWAITING_PROMOTION
        )
    else:
        # auto_quarantine=False — leave in validating state.
        pass

    return verdict


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def promote_batch(
    engine: Engine,
    batch_key: str,
    *,
    promoted_by: str = "",
    auto: bool = False,
) -> PromotionReceiptV1:
    """Explicitly promote an ``awaiting_promotion`` batch.

    See :func:`irc_data.quality.gate_store.promote_batch` — promotion is
    atomic (promote + supersede in one transaction) and only legal from
    ``awaiting_promotion``.
    """
    return gate_store.promote_batch(
        engine, batch_key, promoted_by=promoted_by, auto=auto
    )


def ingest_validate_and_optionally_promote(
    engine: Engine,
    *,
    pipeline: str,
    source_slug: str,
    gate: str,
    payload: Any,
    context: dict[str, Any] | None = None,
    auto_promote: bool = False,
    promoted_by: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full pipeline: ingest → validate → (quarantine | await | promote).

    This is the seam the replay/backfill workflow plugs into at
    promotion time, and the seam tests use to drive fault fixtures.

    Returns a summary dict with the batch, the verdict, and either the
    quarantine record or the promotion receipt.
    """
    batch = ingest_batch_version(
        engine,
        pipeline=pipeline,
        source_slug=source_slug,
        gate=gate,
        payload=payload,
        metadata=metadata,
    )
    verdict = validate_batch(
        engine, batch["batch_key"], payload, context=context
    )

    result: dict[str, Any] = {
        "batch": batch,
        "verdict": verdict.to_dict(),
        "outcome": verdict.outcome,
    }

    if not verdict.passed:
        q = gate_store.get_quarantine(engine, batch["batch_key"])
        result["quarantine"] = q.to_dict() if q else None
        return result

    if auto_promote:
        receipt = promote_batch(
            engine,
            batch["batch_key"],
            promoted_by=promoted_by,
            auto=True,
        )
        result["receipt"] = receipt.to_dict()
        result["outcome"] = "promoted"
    else:
        result["outcome"] = "awaiting_promotion"

    return result


# ---------------------------------------------------------------------------
# Consumer view
# ---------------------------------------------------------------------------


def get_consumer_view(
    engine: Engine,
    pipeline: str,
    source_slug: str,
    *,
    row_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Return the consumer-visible rows for (pipeline, source).

    Promoted versions only — quarantined/pending/superseded versions
    never appear here.
    """
    return gate_store.get_consumer_view_rows(
        engine, pipeline, source_slug, row_kind=row_kind
    )


__all__ = [
    "IdentityEffectBatch",
    "ingest_batch_version",
    "validate_batch",
    "evaluate_payload",
    "promote_batch",
    "ingest_validate_and_optionally_promote",
    "get_consumer_view",
    "all_rules",
    "rules_for",
]
