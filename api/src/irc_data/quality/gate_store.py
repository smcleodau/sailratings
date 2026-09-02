"""Versioned quality-gate batch store (DP-05-02).

Database layer for the validation / quarantine / promotion gates.
DB-agnostic: raw SQL via ``text()`` so the test suite runs against
in-memory SQLite as well as Postgres (the Alembic migration
``20260904a``/``0028`` mirrors this schema for production).

Tables
------

* ``quality_batches``
    One row per **batch version**.  ``(pipeline, source_slug, version)``
    is unique — a retry/replay of the same content is ingested as a new
    version, never an in-place rewrite.  ``status`` tracks the gate
    lifecycle (``pending → validating → quarantined |
    awaiting_promotion → promoted | superseded``).

* ``quality_batch_rows``
    The staged payload rows for a batch (extraction records, canonical
    assertions, or identity effects — serialized as JSON).  These rows
    are **never** read by consumers directly; the consumer view
    (:func:`get_consumer_view_rows`) joins on promoted batches only.

* ``quality_quarantine``
    One row per quarantined batch — the :class:`QuarantineRecordV1`
    serialized in full, plus indexed columns (rule classes, gate) for
    the review UI.

* ``quality_verdicts``
    One row per validation run — the :class:`GateVerdictV1` report.

* ``quality_promotions``
    One row per explicit promotion — the :class:`PromotionReceiptV1`.

Invariants enforced here
------------------------

* **Partial publication cannot occur.**  :func:`promote_batch` is the
  only function that changes consumer-visible state, and it does so in
  a single transaction: the batch moves to ``promoted`` *and* any prior
  promoted version moves to ``superseded`` atomically.  Promotion from
  any state other than ``awaiting_promotion`` raises
  :class:`PromotionError` — a quarantined batch can never be promoted.

* **Retry creates a new version.**  :func:`ingest_batch` computes
  ``version = max(existing versions) + 1`` under a uniqueness
  constraint, so a retry never reuses a quarantined version.

* **Consumers see only promoted versions.**
  :func:`get_consumer_view_rows` filters on ``status = 'promoted'``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.quality.contracts import (
    SCHEMA_VERSION,
    GateFinding,
    GateVerdictV1,
    PromotionReceiptV1,
    QualityBatchStatus,
    QuarantineRecordV1,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GateStoreError(Exception):
    """Base class for quality-gate store errors."""


class PromotionError(GateStoreError):
    """Promotion was attempted from an illegal state."""


class QuarantineError(GateStoreError):
    """Quarantine was attempted on a batch in an illegal state."""


# ---------------------------------------------------------------------------
# Schema (SQLite-compatible, mirrors the Alembic migration)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS quality_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_key TEXT NOT NULL UNIQUE,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    gate TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    record_count INTEGER DEFAULT 0,
    content_hash TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    promoted_at TIMESTAMP,
    promoted_by TEXT,
    UNIQUE (pipeline, source_slug, version)
);

CREATE INDEX IF NOT EXISTS ix_quality_batches_pipeline_source
    ON quality_batches(pipeline, source_slug);

CREATE INDEX IF NOT EXISTS ix_quality_batches_status
    ON quality_batches(status);

CREATE TABLE IF NOT EXISTS quality_batch_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_key TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    row_kind TEXT NOT NULL,
    row_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_key) REFERENCES quality_batches(batch_key)
);

CREATE INDEX IF NOT EXISTS ix_quality_batch_rows_batch_key
    ON quality_batch_rows(batch_key);

CREATE TABLE IF NOT EXISTS quality_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    gate TEXT NOT NULL,
    rule_classes TEXT,
    failures TEXT,
    sample_rows TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    resolution TEXT
);

CREATE INDEX IF NOT EXISTS ix_quality_quarantine_batch_key
    ON quality_quarantine(batch_key);

CREATE TABLE IF NOT EXISTS quality_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    gate TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rules_evaluated INTEGER DEFAULT 0,
    rules_failed INTEGER DEFAULT 0,
    failures TEXT,
    record_count INTEGER DEFAULT 0,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_quality_verdicts_batch_key
    ON quality_verdicts(batch_key);

CREATE TABLE IF NOT EXISTS quality_promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    record_count INTEGER DEFAULT 0,
    superseded_batch_key TEXT,
    superseded_version INTEGER,
    promoted_by TEXT,
    auto BOOLEAN DEFAULT 0,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    schema_version TEXT DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS ix_quality_promotions_batch_key
    ON quality_promotions(batch_key);

CREATE INDEX IF NOT EXISTS ix_quality_promotions_pipeline_source
    ON quality_promotions(pipeline, source_slug);
"""

SCHEMA_SQL_POSTGRES = """
CREATE TABLE IF NOT EXISTS quality_batches (
    id BIGSERIAL PRIMARY KEY,
    batch_key TEXT NOT NULL UNIQUE,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    gate TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    record_count INTEGER DEFAULT 0,
    content_hash TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    promoted_at TIMESTAMPTZ,
    promoted_by TEXT,
    UNIQUE (pipeline, source_slug, version)
);

CREATE INDEX IF NOT EXISTS ix_quality_batches_pipeline_source
    ON quality_batches(pipeline, source_slug);

CREATE INDEX IF NOT EXISTS ix_quality_batches_status
    ON quality_batches(status);

CREATE TABLE IF NOT EXISTS quality_batch_rows (
    id BIGSERIAL PRIMARY KEY,
    batch_key TEXT NOT NULL REFERENCES quality_batches(batch_key),
    row_index INTEGER NOT NULL,
    row_kind TEXT NOT NULL,
    row_json JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_quality_batch_rows_batch_key
    ON quality_batch_rows(batch_key);

CREATE TABLE IF NOT EXISTS quality_quarantine (
    id BIGSERIAL PRIMARY KEY,
    quarantine_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    gate TEXT NOT NULL,
    rule_classes JSONB,
    failures JSONB,
    sample_rows JSONB,
    status TEXT NOT NULL DEFAULT 'open',
    quarantined_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolution TEXT
);

CREATE INDEX IF NOT EXISTS ix_quality_quarantine_batch_key
    ON quality_quarantine(batch_key);

CREATE TABLE IF NOT EXISTS quality_verdicts (
    id BIGSERIAL PRIMARY KEY,
    verdict_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    gate TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rules_evaluated INTEGER DEFAULT 0,
    rules_failed INTEGER DEFAULT 0,
    failures JSONB,
    record_count INTEGER DEFAULT 0,
    evaluated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_quality_verdicts_batch_key
    ON quality_verdicts(batch_key);

CREATE TABLE IF NOT EXISTS quality_promotions (
    id BIGSERIAL PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    batch_key TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    record_count INTEGER DEFAULT 0,
    superseded_batch_key TEXT,
    superseded_version INTEGER,
    promoted_by TEXT,
    auto BOOLEAN DEFAULT FALSE,
    promoted_at TIMESTAMPTZ DEFAULT now(),
    schema_version TEXT DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS ix_quality_promotions_batch_key
    ON quality_promotions(batch_key);

CREATE INDEX IF NOT EXISTS ix_quality_promotions_pipeline_source
    ON quality_promotions(pipeline, source_slug);
"""


def init_quality_tables(engine: Engine) -> None:
    """Create the quality-gate tables (idempotent).

    On Postgres this is normally handled by the Alembic migration.
    This helper exists so tests can set up an in-memory SQLite schema
    without Alembic.
    """
    sql = (
        SCHEMA_SQL_POSTGRES
        if engine.dialect.name == "postgresql"
        else SCHEMA_SQL
    )
    with engine.begin() as conn:
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


# ---------------------------------------------------------------------------
# Ingest (versioned)
# ---------------------------------------------------------------------------


def _derive_batch_key(pipeline: str, source_slug: str, version: int) -> str:
    return f"{pipeline}:{source_slug}:v{version}"


def ingest_batch(
    engine: Engine,
    *,
    pipeline: str,
    source_slug: str,
    gate: str,
    rows: list[tuple[str, Any]],
    content_hash: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest a new batch version and stage its rows.

    ``rows`` is a list of ``(row_kind, row_payload)`` tuples where
    ``row_kind`` is a free-form discriminator (``"record"``,
    ``"assertion"``, ``"reject"``, ``"effect"`` …) and ``row_payload``
    is any JSON-serializable object.

    Returns the batch dict with a fresh ``version`` (one more than the
    highest existing version for ``(pipeline, source_slug)``).
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version "
                "FROM quality_batches "
                "WHERE pipeline = :pipeline AND source_slug = :source_slug"
            ),
            {"pipeline": pipeline, "source_slug": source_slug},
        ).first()
        version = int(row[0])
        batch_key = _derive_batch_key(pipeline, source_slug, version)

        conn.execute(
            text(
                "INSERT INTO quality_batches "
                "  (batch_key, pipeline, source_slug, gate, version, "
                "   status, record_count, content_hash, metadata) "
                "VALUES (:batch_key, :pipeline, :source_slug, :gate, "
                "        :version, :status, :record_count, :content_hash, "
                "        :metadata)"
            ),
            {
                "batch_key": batch_key,
                "pipeline": pipeline,
                "source_slug": source_slug,
                "gate": gate,
                "version": version,
                "status": QualityBatchStatus.PENDING.value,
                "record_count": len(rows),
                "content_hash": content_hash or "",
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

        for i, (kind, payload) in enumerate(rows):
            conn.execute(
                text(
                    "INSERT INTO quality_batch_rows "
                    "  (batch_key, row_index, row_kind, row_json) "
                    "VALUES (:batch_key, :row_index, :row_kind, :row_json)"
                ),
                {
                    "batch_key": batch_key,
                    "row_index": i,
                    "row_kind": kind,
                    "row_json": json.dumps(payload, sort_keys=True, default=str),
                },
            )

    return get_batch(engine, batch_key)  # type: ignore[return-value]


def get_batch(engine: Engine, batch_key: str) -> dict[str, Any] | None:
    """Return the batch row for *batch_key* or ``None``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, batch_key, pipeline, source_slug, gate, version, "
                "       status, record_count, content_hash, metadata, "
                "       created_at, updated_at, promoted_at, promoted_by "
                "FROM quality_batches WHERE batch_key = :batch_key"
            ),
            {"batch_key": batch_key},
        ).first()
    if row is None:
        return None
    d = dict(row._mapping)
    if isinstance(d.get("metadata"), str) and d["metadata"]:
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def get_batch_rows(
    engine: Engine,
    batch_key: str,
    *,
    row_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Return staged rows for a batch (parsed JSON), ordered by index."""
    sql = (
        "SELECT row_index, row_kind, row_json FROM quality_batch_rows "
        "WHERE batch_key = :batch_key"
    )
    params: dict[str, Any] = {"batch_key": batch_key}
    if row_kind is not None:
        sql += " AND row_kind = :row_kind"
        params["row_kind"] = row_kind
    sql += " ORDER BY row_index"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping)
        val = d.get("row_json")
        if isinstance(val, str) and val:
            try:
                d["row_json"] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out


def set_batch_status(
    engine: Engine,
    batch_key: str,
    status: QualityBatchStatus,
    **extra: Any,
) -> None:
    """Update the status of a batch (and optionally other fields)."""
    sets = ["status = :status", "updated_at = CURRENT_TIMESTAMP"]
    params: dict[str, Any] = {"batch_key": batch_key, "status": status.value}
    for key, val in extra.items():
        sets.append(f"{key} = :{key}")
        params[key] = val
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE quality_batches SET {', '.join(sets)} "
                 f"WHERE batch_key = :batch_key"),
            params,
        )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def record_verdict(engine: Engine, verdict: GateVerdictV1) -> None:
    """Persist a validation verdict (idempotent on ``verdict_id``)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO quality_verdicts "
                "  (verdict_id, batch_key, pipeline, source_slug, version, "
                "   gate, outcome, rules_evaluated, rules_failed, failures, "
                "   record_count) "
                "VALUES (:verdict_id, :batch_key, :pipeline, :source_slug, "
                "        :version, :gate, :outcome, :rules_evaluated, "
                "        :rules_failed, :failures, :record_count) "
                "ON CONFLICT(verdict_id) DO NOTHING"
            ),
            {
                "verdict_id": verdict.verdict_id,
                "batch_key": verdict.batch_key,
                "pipeline": verdict.pipeline,
                "source_slug": verdict.source_slug,
                "version": verdict.version,
                "gate": verdict.gate,
                "outcome": verdict.outcome,
                "rules_evaluated": verdict.rules_evaluated,
                "rules_failed": verdict.rules_failed,
                "failures": json.dumps(
                    [f.to_dict() for f in verdict.failures], default=str
                ),
                "record_count": verdict.record_count,
            },
        )


def get_verdicts(engine: Engine, batch_key: str) -> list[GateVerdictV1]:
    """Return all verdicts for a batch, newest first."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT verdict_id, batch_key, pipeline, source_slug, version, "
                "       gate, outcome, rules_evaluated, rules_failed, failures, "
                "       record_count, evaluated_at "
                "FROM quality_verdicts WHERE batch_key = :batch_key "
                "ORDER BY id DESC"
            ),
            {"batch_key": batch_key},
        ).fetchall()
    out: list[GateVerdictV1] = []
    for r in rows:
        d = dict(r._mapping)
        failures_raw = d.pop("failures", None)
        if isinstance(failures_raw, str) and failures_raw:
            failures_raw = json.loads(failures_raw)
        d["failures"] = [
            GateFinding.from_dict(f) for f in (failures_raw or [])
        ]
        if d.get("evaluated_at") is not None and not isinstance(
            d["evaluated_at"], str
        ):
            d["evaluated_at"] = str(d["evaluated_at"])
        out.append(GateVerdictV1.from_dict(d))
    return out


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def quarantine_batch(
    engine: Engine,
    record: QuarantineRecordV1,
) -> QuarantineRecordV1:
    """Quarantine a batch, attaching rule failures and samples.

    Sets the batch status to ``quarantined`` and upserts the quarantine
    record (idempotent on ``quarantine_id``).  A quarantined batch can
    never be promoted — see :func:`promote_batch`.
    """
    with engine.begin() as conn:
        batch = conn.execute(
            text("SELECT status FROM quality_batches WHERE batch_key = :bk"),
            {"bk": record.batch_key},
        ).first()
        if batch is None:
            raise QuarantineError(f"batch {record.batch_key!r} not found")

        conn.execute(
            text(
                "INSERT INTO quality_quarantine "
                "  (quarantine_id, batch_key, pipeline, source_slug, version, "
                "   gate, rule_classes, failures, sample_rows, status) "
                "VALUES (:quarantine_id, :batch_key, :pipeline, :source_slug, "
                "        :version, :gate, :rule_classes, :failures, "
                "        :sample_rows, :status) "
                "ON CONFLICT(quarantine_id) DO NOTHING"
            ),
            {
                "quarantine_id": record.quarantine_id,
                "batch_key": record.batch_key,
                "pipeline": record.pipeline,
                "source_slug": record.source_slug,
                "version": record.version,
                "gate": record.gate,
                "rule_classes": json.dumps(record.rule_classes()),
                "failures": json.dumps(
                    [f.to_dict() for f in record.failures], default=str
                ),
                "sample_rows": json.dumps(record.sample_rows, default=str),
                "status": record.status,
            },
        )

        conn.execute(
            text(
                "UPDATE quality_batches "
                "SET status = :status, updated_at = CURRENT_TIMESTAMP "
                "WHERE batch_key = :batch_key"
            ),
            {
                "status": QualityBatchStatus.QUARANTINED.value,
                "batch_key": record.batch_key,
            },
        )
    return record


def get_quarantine(
    engine: Engine, batch_key: str
) -> QuarantineRecordV1 | None:
    """Return the quarantine record for a batch, or ``None``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT quarantine_id, batch_key, pipeline, source_slug, "
                "       version, gate, failures, sample_rows, status, "
                "       quarantined_at "
                "FROM quality_quarantine WHERE batch_key = :batch_key "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"batch_key": batch_key},
        ).first()
    if row is None:
        return None
    d = dict(row._mapping)
    for key in ("failures", "sample_rows"):
        if isinstance(d.get(key), str) and d[key]:
            d[key] = json.loads(d[key])
    # Leave ``failures`` as raw dicts — QuarantineRecordV1.from_dict
    # converts them to GateFinding objects.
    if d.get("quarantined_at") is not None and not isinstance(
        d["quarantined_at"], str
    ):
        d["quarantined_at"] = str(d["quarantined_at"])
    return QuarantineRecordV1.from_dict(d)


def list_quarantine(
    engine: Engine,
    *,
    status: str = "open",
    pipeline: str | None = None,
    source_slug: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List quarantined batches for the review UI (summaries)."""
    sql = (
        "SELECT quarantine_id, batch_key, pipeline, source_slug, version, "
        "       gate, rule_classes, status, quarantined_at "
        "FROM quality_quarantine WHERE status = :status"
    )
    params: dict[str, Any] = {"status": status, "limit": limit}
    if pipeline:
        sql += " AND pipeline = :pipeline"
        params["pipeline"] = pipeline
    if source_slug:
        sql += " AND source_slug = :source_slug"
        params["source_slug"] = source_slug
    sql += " ORDER BY quarantined_at DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        if isinstance(d.get("rule_classes"), str) and d["rule_classes"]:
            d["rule_classes"] = json.loads(d["rule_classes"])
        if d.get("quarantined_at") is not None and not isinstance(
            d["quarantined_at"], str
        ):
            d["quarantined_at"] = str(d["quarantined_at"])
        out.append(d)
    return out


def resolve_quarantine(
    engine: Engine,
    quarantine_id: str,
    *,
    resolution: str,
    status: str = "released",
) -> None:
    """Mark a quarantine record resolved (released / overridden)."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE quality_quarantine "
                "SET status = :status, resolution = :resolution, "
                "    resolved_at = CURRENT_TIMESTAMP "
                "WHERE quarantine_id = :quarantine_id"
            ),
            {
                "status": status,
                "resolution": resolution,
                "quarantine_id": quarantine_id,
            },
        )


# ---------------------------------------------------------------------------
# Promotion (explicit, atomic, no partial publication)
# ---------------------------------------------------------------------------


def get_promoted_batch(
    engine: Engine,
    pipeline: str,
    source_slug: str,
) -> dict[str, Any] | None:
    """Return the currently-promoted batch for (pipeline, source), or None."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT b.batch_key, b.version, b.record_count, b.gate, "
                "       b.status, b.promoted_at, b.promoted_by "
                "FROM quality_batches b "
                "WHERE b.pipeline = :pipeline AND b.source_slug = :source_slug "
                "  AND b.status = :promoted "
                "ORDER BY b.version DESC LIMIT 1"
            ),
            {
                "pipeline": pipeline,
                "source_slug": source_slug,
                "promoted": QualityBatchStatus.PROMOTED.value,
            },
        ).first()
    return dict(row._mapping) if row else None


def promote_batch(
    engine: Engine,
    batch_key: str,
    *,
    promoted_by: str = "",
    auto: bool = False,
) -> PromotionReceiptV1:
    """Explicitly promote a batch version to the consumer view.

    This is the **only** function that changes consumer-visible state.
    In a single transaction it:

    1. Asserts the batch is in ``awaiting_promotion`` (a quarantined or
       pending batch raises :class:`PromotionError` — partial
       publication cannot occur).
    2. Marks the batch ``promoted``.
    3. Marks any previously-promoted version of the same
       ``(pipeline, source_slug)`` as ``superseded`` (retained).
    4. Inserts a :class:`PromotionReceiptV1` (idempotent on
       ``receipt_id`` — promoting the same batch twice returns the same
       receipt).

    Returns the receipt.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT batch_key, pipeline, source_slug, version, status, "
                "       record_count "
                "FROM quality_batches WHERE batch_key = :batch_key"
            ),
            {"batch_key": batch_key},
        ).first()
        if row is None:
            raise PromotionError(f"batch {batch_key!r} not found")
        batch = dict(row._mapping)

        if batch["status"] == QualityBatchStatus.PROMOTED.value:
            # Idempotent: already promoted — return existing receipt.
            existing = _get_receipt_for_batch(conn, batch_key)
            if existing is not None:
                return existing

        if batch["status"] != QualityBatchStatus.AWAITING_PROMOTION.value:
            raise PromotionError(
                f"batch {batch_key!r} is in status {batch['status']!r}; "
                f"only 'awaiting_promotion' batches can be promoted "
                f"(quarantined batches can never be promoted — ingest a "
                f"new version instead)"
            )

        # Find the currently-promoted version to supersede.
        prev = conn.execute(
            text(
                "SELECT batch_key, version FROM quality_batches "
                "WHERE pipeline = :pipeline AND source_slug = :source_slug "
                "  AND status = :promoted AND batch_key != :batch_key "
                "ORDER BY version DESC LIMIT 1"
            ),
            {
                "pipeline": batch["pipeline"],
                "source_slug": batch["source_slug"],
                "promoted": QualityBatchStatus.PROMOTED.value,
                "batch_key": batch_key,
            },
        ).first()

        # Mark the batch promoted.
        conn.execute(
            text(
                "UPDATE quality_batches "
                "SET status = :status, promoted_at = CURRENT_TIMESTAMP, "
                "    promoted_by = :promoted_by, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE batch_key = :batch_key"
            ),
            {
                "status": QualityBatchStatus.PROMOTED.value,
                "promoted_by": promoted_by,
                "batch_key": batch_key,
            },
        )

        # Supersede the previously-promoted version (retained).
        superseded_key = None
        superseded_version = None
        if prev is not None:
            superseded_key = prev[0]
            superseded_version = prev[1]
            conn.execute(
                text(
                    "UPDATE quality_batches "
                    "SET status = :status, updated_at = CURRENT_TIMESTAMP "
                    "WHERE batch_key = :prev_key"
                ),
                {
                    "status": QualityBatchStatus.SUPERSEDED.value,
                    "prev_key": superseded_key,
                },
            )

        receipt_id = f"prc_{uuid.uuid4().hex[:16]}"
        conn.execute(
            text(
                "INSERT INTO quality_promotions "
                "  (receipt_id, batch_key, pipeline, source_slug, version, "
                "   record_count, superseded_batch_key, superseded_version, "
                "   promoted_by, auto, schema_version) "
                "VALUES (:receipt_id, :batch_key, :pipeline, :source_slug, "
                "        :version, :record_count, :superseded_batch_key, "
                "        :superseded_version, :promoted_by, :auto, "
                "        :schema_version) "
                "ON CONFLICT(receipt_id) DO NOTHING"
            ),
            {
                "receipt_id": receipt_id,
                "batch_key": batch_key,
                "pipeline": batch["pipeline"],
                "source_slug": batch["source_slug"],
                "version": batch["version"],
                "record_count": batch["record_count"],
                "superseded_batch_key": superseded_key,
                "superseded_version": superseded_version,
                "promoted_by": promoted_by,
                "auto": auto,
                "schema_version": SCHEMA_VERSION,
            },
        )

    with engine.connect() as conn:
        receipt = _get_receipt_for_batch(conn, batch_key)
    return receipt  # type: ignore[return-value]


def _get_receipt_for_batch(conn, batch_key: str) -> PromotionReceiptV1 | None:
    row = conn.execute(
        text(
            "SELECT receipt_id, batch_key, pipeline, source_slug, version, "
            "       record_count, superseded_batch_key, superseded_version, "
            "       promoted_by, auto, promoted_at, schema_version "
            "FROM quality_promotions WHERE batch_key = :batch_key "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"batch_key": batch_key},
    ).first()
    if row is None:
        return None
    d = dict(row._mapping)
    if isinstance(d.get("auto"), int):
        d["auto"] = bool(d["auto"])
    if d.get("promoted_at") is not None and not isinstance(d["promoted_at"], str):
        d["promoted_at"] = str(d["promoted_at"])
    return PromotionReceiptV1.from_dict(d)


def get_receipt(engine: Engine, batch_key: str) -> PromotionReceiptV1 | None:
    """Return the promotion receipt for a batch, or ``None``."""
    with engine.connect() as conn:
        return _get_receipt_for_batch(conn, batch_key)


# ---------------------------------------------------------------------------
# Consumer view — promoted versions only
# ---------------------------------------------------------------------------


def get_consumer_view_rows(
    engine: Engine,
    pipeline: str,
    source_slug: str,
    *,
    row_kind: str | None = None,
) -> list[dict[str, Any]]:
    """Return the staged rows consumers are allowed to see.

    The consumer view is defined as **rows belonging to the promoted
    batch version only**.  Pending, quarantined, awaiting-promotion and
    superseded versions are invisible — this is the enforcement point
    for "consumers see only promoted versions".
    """
    promoted = get_promoted_batch(engine, pipeline, source_slug)
    if promoted is None:
        return []
    return get_batch_rows(
        engine, promoted["batch_key"], row_kind=row_kind
    )


def list_batches(
    engine: Engine,
    *,
    pipeline: str | None = None,
    source_slug: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List batches (summaries) for the review UI."""
    sql = (
        "SELECT batch_key, pipeline, source_slug, gate, version, status, "
        "       record_count, created_at, promoted_at, promoted_by "
        "FROM quality_batches WHERE 1=1"
    )
    params: dict[str, Any] = {"limit": limit}
    if pipeline:
        sql += " AND pipeline = :pipeline"
        params["pipeline"] = pipeline
    if source_slug:
        sql += " AND source_slug = :source_slug"
        params["source_slug"] = source_slug
    if status:
        sql += " AND status = :status"
        params["status"] = status
    sql += " ORDER BY created_at DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    out = []
    for r in rows:
        d = dict(r._mapping)
        for k in ("created_at", "promoted_at"):
            if d.get(k) is not None and not isinstance(d[k], str):
                d[k] = str(d[k])
        out.append(d)
    return out


__all__ = [
    "GateStoreError",
    "PromotionError",
    "QuarantineError",
    "SCHEMA_SQL",
    "SCHEMA_SQL_POSTGRES",
    "init_quality_tables",
    "ingest_batch",
    "get_batch",
    "get_batch_rows",
    "set_batch_status",
    "record_verdict",
    "get_verdicts",
    "quarantine_batch",
    "get_quarantine",
    "list_quarantine",
    "resolve_quarantine",
    "get_promoted_batch",
    "promote_batch",
    "get_receipt",
    "get_consumer_view_rows",
    "list_batches",
]
