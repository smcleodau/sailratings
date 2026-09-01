"""Idempotent, resumable replay batch store (DP-02-04 / SPEC-013).

This module provides the database operations for the replay / backfill
pipeline.  It is DB-agnostic: it uses raw SQL via ``text()`` so the
test suite can run against in-memory SQLite as well as Postgres.

Key operations:

* :func:`init_replay_tables` — create the schema (idempotent).
* :func:`create_or_get_batch` — idempotent batch creation keyed by
  ``plan_id``.
* :func:`select_artifacts` — query published artifacts by
  source/time/version.
* :func:`store_parsed_output` — insert a parsed artifact into an
  isolated batch.
* :func:`compare_batches` — diff old (published) vs new (isolated)
  parsed outputs.
* :func:`promote_batch` — explicitly promote a batch to publication.
  The old published batch is retained (``status`` → ``superseded``)
  and a :class:`PublicationReceiptV1` is produced.
* :func:`get_receipt` — retrieve the publication receipt for a batch.

Idempotency
-----------
``create_or_get_batch`` is the idempotency anchor.  Calling it with
the same ``plan_id`` returns the existing batch with its current
status.  The workflow checks the status on resume and skips
already-completed steps.

Resumability
------------
The batch ``status`` field tracks progress through the lifecycle:
``pending → running → comparing → awaiting_approval → promoted``.
A crashed workflow reads the batch status and picks up where it left
off.

Auditability
------------
Every batch, artifact, and promotion is a database row with
timestamps.  Superseded batches are retained (never deleted) so the
full history is queryable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import JSON, bindparam, text
from sqlalchemy.engine import Engine

from irc_data.temporal.replay.contracts import (
    SCHEMA_VERSION,
    ArtifactFilter,
    BatchStatus,
    ComparisonResult,
    PublicationReceiptV1,
    ReplayPlanV1,
)


# ---------------------------------------------------------------------------
# Schema (SQLite-compatible, mirrors the Alembic migration)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS replay_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL UNIQUE,
    source_slug TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    artifact_filter TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    promoted_at TIMESTAMP,
    promoted_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS replay_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    artifact_url TEXT NOT NULL,
    content_hash TEXT,
    parsed_output TEXT,
    old_parsed_output TEXT,
    parse_status TEXT DEFAULT 'pending',
    parse_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id) REFERENCES replay_batches(id)
);

CREATE INDEX IF NOT EXISTS ix_replay_artifacts_batch_id
    ON replay_artifacts(batch_id);

CREATE TABLE IF NOT EXISTS publication_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id TEXT NOT NULL UNIQUE,
    batch_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    promoted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    old_batch_id INTEGER,
    old_retained BOOLEAN DEFAULT 1,
    artifact_count INTEGER DEFAULT 0,
    promoted_by TEXT,
    schema_version TEXT DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS ix_publication_receipts_batch_id
    ON publication_receipts(batch_id);
"""

# Postgres variant (uses JSONB for parsed_output, BIGSERIAL for ids).
SCHEMA_SQL_POSTGRES = """
CREATE TABLE IF NOT EXISTS replay_batches (
    id BIGSERIAL PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    source_slug TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    artifact_filter JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    promoted_at TIMESTAMPTZ,
    promoted_by TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS ix_replay_batches_plan_id
    ON replay_batches(plan_id);

CREATE TABLE IF NOT EXISTS replay_artifacts (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL REFERENCES replay_batches(id),
    artifact_url TEXT NOT NULL,
    content_hash TEXT,
    parsed_output JSONB,
    old_parsed_output JSONB,
    parse_status TEXT DEFAULT 'pending',
    parse_error TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_replay_artifacts_batch_id
    ON replay_artifacts(batch_id);

CREATE TABLE IF NOT EXISTS publication_receipts (
    id BIGSERIAL PRIMARY KEY,
    receipt_id TEXT NOT NULL UNIQUE,
    batch_id BIGINT NOT NULL,
    plan_id TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    promoted_at TIMESTAMPTZ DEFAULT now(),
    old_batch_id BIGINT,
    old_retained BOOLEAN DEFAULT TRUE,
    artifact_count INTEGER DEFAULT 0,
    promoted_by TEXT,
    schema_version TEXT DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS ix_publication_receipts_batch_id
    ON publication_receipts(batch_id);
"""


def init_replay_tables(engine: Engine) -> None:
    """Create the replay tables (idempotent).

    On Postgres this is normally handled by the Alembic migration
    (0024_replay_backfill).  This helper exists so tests can set up an
    in-memory SQLite schema without Alembic.
    """
    # Detect dialect — Postgres uses JSONB, SQLite uses TEXT for JSON.
    is_postgres = engine.dialect.name == "postgresql"
    sql = SCHEMA_SQL_POSTGRES if is_postgres else SCHEMA_SQL
    with engine.begin() as conn:
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


# ---------------------------------------------------------------------------
# Batch lifecycle
# ---------------------------------------------------------------------------


def create_or_get_batch(
    engine: Engine,
    plan: ReplayPlanV1,
) -> dict[str, Any]:
    """Idempotently create a replay batch or return the existing one.

    If a batch with ``plan.plan_id`` already exists, it is returned
    with its current status.  This is the idempotency anchor: calling
    this twice with the same plan yields the same batch.

    Returns a dict with ``id``, ``plan_id``, ``source_slug``,
    ``parser_version``, ``status``, ``created_at``.
    """
    with engine.begin() as conn:
        existing = conn.execute(
            text(
                "SELECT id, plan_id, source_slug, parser_version, status, "
                "       created_at, promoted_at, promoted_by, notes "
                "FROM replay_batches WHERE plan_id = :plan_id"
            ),
            {"plan_id": plan.plan_id},
        ).first()

        if existing:
            return dict(existing._mapping)

        conn.execute(
            text(
                "INSERT INTO replay_batches "
                "  (plan_id, source_slug, parser_version, status, "
                "   artifact_filter, notes) "
                "VALUES (:plan_id, :source_slug, :parser_version, :status, "
                "        :artifact_filter, :notes)"
            ),
            {
                "plan_id": plan.plan_id,
                "source_slug": plan.source_slug,
                "parser_version": plan.new_parser_version,
                "status": BatchStatus.PENDING.value,
                "artifact_filter": json.dumps(plan.artifact_filter.to_dict()),
                "notes": plan.notes or "",
            },
        )

        row = conn.execute(
            text(
                "SELECT id, plan_id, source_slug, parser_version, status, "
                "       created_at, promoted_at, promoted_by, notes "
                "FROM replay_batches WHERE plan_id = :plan_id"
            ),
            {"plan_id": plan.plan_id},
        ).first()

        return dict(row._mapping)


def get_batch(engine: Engine, plan_id: str) -> dict[str, Any] | None:
    """Return the batch for *plan_id* or ``None``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, plan_id, source_slug, parser_version, status, "
                "       created_at, promoted_at, promoted_by, notes "
                "FROM replay_batches WHERE plan_id = :plan_id"
            ),
            {"plan_id": plan_id},
        ).first()
    return dict(row._mapping) if row else None


def update_batch_status(
    engine: Engine,
    batch_id: int,
    status: BatchStatus,
    **extra: Any,
) -> None:
    """Update the status of a batch (and optionally other fields)."""
    sets = ["status = :status", "updated_at = CURRENT_TIMESTAMP"]
    params: dict[str, Any] = {"batch_id": batch_id, "status": status.value}

    for key, val in extra.items():
        sets.append(f"{key} = :{key}")
        params[key] = val

    sql = f"UPDATE replay_batches SET {', '.join(sets)} WHERE id = :batch_id"
    with engine.begin() as conn:
        conn.execute(text(sql), params)


# ---------------------------------------------------------------------------
# Artifact selection (by source / time / version)
# ---------------------------------------------------------------------------


def select_artifacts(
    engine: Engine,
    artifact_filter: ArtifactFilter,
    *,
    published_artifacts_table: str = "published_artifacts",
    fetch_fn: Callable[[Engine, ArtifactFilter, str], list[dict]] | None = None,
) -> list[dict[str, Any]]:
    """Select published artifacts matching *artifact_filter*.

    This queries the published artifacts store (by default a table
    called ``published_artifacts``).  In production, this is the
    canonical store of previously-parsed outputs keyed by source /
    fetch-time / parser-version.

    For testing or custom sources, a ``fetch_fn`` can be injected.  This
    decouples the replay store from any specific table schema and lets
    tests use in-memory fixtures.

    Parameters
    ----------
    engine
        SQLAlchemy engine.
    artifact_filter
        Selection criteria.
    published_artifacts_table
        Name of the published artifacts table (default
        ``published_artifacts``).
    fetch_fn
        Optional callable ``(engine, filter, table) -> list[dict]`` that
        overrides the default SQL query.  Each dict must have at least
        ``artifact_url`` and ``content_hash``; optionally
        ``parsed_output`` and ``parser_version``.

    Returns
    -------
    list[dict]
        One dict per artifact with keys ``artifact_url``,
        ``content_hash``, ``parsed_output`` (may be ``None``),
        ``parser_version`` (may be ``None``).
    """
    if fetch_fn is not None:
        return fetch_fn(engine, artifact_filter, published_artifacts_table)

    # Build a parameterised query from the filter.
    conditions: list[str] = []
    params: dict[str, Any] = {}

    if artifact_filter.source_slug:
        conditions.append("source_slug = :source_slug")
        params["source_slug"] = artifact_filter.source_slug
    if artifact_filter.fetched_after:
        conditions.append("fetched_at >= :fetched_after")
        params["fetched_after"] = artifact_filter.fetched_after
    if artifact_filter.fetched_before:
        conditions.append("fetched_at <= :fetched_before")
        params["fetched_before"] = artifact_filter.fetched_before
    if artifact_filter.parser_version:
        conditions.append("parser_version = :parser_version")
        params["parser_version"] = artifact_filter.parser_version
    if artifact_filter.content_hash:
        conditions.append("content_hash = :content_hash")
        params["content_hash"] = artifact_filter.content_hash

    where_clause = " AND ".join(conditions) if conditions else "1=1"
    limit_clause = f"LIMIT {artifact_filter.limit}" if artifact_filter.limit else ""

    sql = (
        f"SELECT artifact_url, content_hash, parsed_output, parser_version "
        f"FROM {published_artifacts_table} "
        f"WHERE {where_clause} "
        f"ORDER BY fetched_at ASC "
        f"{limit_clause}"
    ).strip()

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping)
        # SQLite stores JSON as TEXT; parse it back to a dict for
        # consistency with Postgres (which returns native JSON).
        po = d.get("parsed_output")
        if isinstance(po, str) and po:
            try:
                d["parsed_output"] = json.loads(po)
            except (json.JSONDecodeError, TypeError):
                pass  # leave as string if not valid JSON
        results.append(d)

    return results


# ---------------------------------------------------------------------------
# Parsed output storage (isolated batch)
# ---------------------------------------------------------------------------


def store_parsed_output(
    engine: Engine,
    batch_id: int,
    artifact_url: str,
    content_hash: str,
    parsed_output: Any,
    old_parsed_output: Any = None,
    parse_status: str = "parsed",
    parse_error: str | None = None,
) -> int:
    """Store a parsed artifact in an isolated batch.

    The artifact is written to ``replay_artifacts`` which is separate
    from the published store.  Nothing in the published store is
    modified.

    Returns the artifact row id.
    """
    parsed_json = (
        json.dumps(parsed_output, sort_keys=True, default=str)
        if parsed_output is not None
        else None
    )
    old_json = (
        json.dumps(old_parsed_output, sort_keys=True, default=str)
        if old_parsed_output is not None
        else None
    )

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO replay_artifacts "
                "  (batch_id, artifact_url, content_hash, parsed_output, "
                "   old_parsed_output, parse_status, parse_error) "
                "VALUES (:batch_id, :artifact_url, :content_hash, "
                "        :parsed_output, :old_parsed_output, "
                "        :parse_status, :parse_error)"
            ).bindparams(
                bindparam("parsed_output", type_=JSON),
                bindparam("old_parsed_output", type_=JSON),
            ),
            {
                "batch_id": batch_id,
                "artifact_url": artifact_url,
                "content_hash": content_hash,
                "parsed_output": parsed_json if parsed_output is not None else None,
                "old_parsed_output": old_json if old_parsed_output is not None else None,
                "parse_status": parse_status,
                "parse_error": parse_error,
            },
        )
        # SQLite doesn't support RETURNING reliably across versions;
        # fetch the last inserted id.
        row = conn.execute(
            text(
                "SELECT id FROM replay_artifacts "
                "WHERE batch_id = :batch_id AND artifact_url = :artifact_url "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"batch_id": batch_id, "artifact_url": artifact_url},
        ).first()
        return row[0] if row else 0


def get_batch_artifacts(
    engine: Engine,
    batch_id: int,
) -> list[dict[str, Any]]:
    """Return all artifacts in a batch."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, batch_id, artifact_url, content_hash, "
                "       parsed_output, old_parsed_output, parse_status, "
                "       parse_error, created_at "
                "FROM replay_artifacts WHERE batch_id = :batch_id "
                "ORDER BY id"
            ),
            {"batch_id": batch_id},
        ).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping)
        # SQLite stores JSON columns as TEXT; parse them for consistency.
        for key in ("parsed_output", "old_parsed_output"):
            val = d.get(key)
            if isinstance(val, str) and val:
                try:
                    d[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)

    return results


def count_batch_artifacts(engine: Engine, batch_id: int) -> int:
    """Return the number of artifacts in a batch."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT COUNT(*) FROM replay_artifacts WHERE batch_id = :batch_id"
            ),
            {"batch_id": batch_id},
        ).scalar()


# ---------------------------------------------------------------------------
# Comparison (old vs new)
# ---------------------------------------------------------------------------


def compare_batches(
    engine: Engine,
    batch_id: int,
) -> ComparisonResult:
    """Compare the new batch's parsed outputs against the old ones.

    Each artifact in ``replay_artifacts`` has a ``parsed_output`` (new)
    and ``old_parsed_output`` (from the published store at selection
    time).  This function counts identical, changed, added, and
    removed artifacts.

    Returns a :class:`ComparisonResult`.
    """
    artifacts = get_batch_artifacts(engine, batch_id)

    identical = 0
    changed = 0
    added = 0

    for art in artifacts:
        new_out = art.get("parsed_output")
        old_out = art.get("old_parsed_output")

        # Normalise to comparable strings.
        new_str = (
            json.dumps(new_out, sort_keys=True, default=str)
            if new_out is not None
            else None
        )
        old_str = (
            json.dumps(old_out, sort_keys=True, default=str)
            if old_out is not None
            else None
        )

        if old_str is None:
            added += 1
        elif new_str == old_str:
            identical += 1
        else:
            changed += 1

    total = len(artifacts)
    summary = (
        f"Compared {total} artifacts: "
        f"{identical} identical, {changed} changed, {added} new"
    )

    return ComparisonResult(
        batch_id=batch_id,
        total_artifacts=total,
        identical=identical,
        changed=changed,
        added=added,
        removed=0,  # "removed" requires comparing the old batch's full
                     # artifact set; in the replay model we select from
                     # the published store, so removed=0 by construction.
        diff_summary=summary,
    )


# ---------------------------------------------------------------------------
# Promotion (explicit, not in-place)
# ---------------------------------------------------------------------------


def get_currently_promoted_batch(
    engine: Engine,
    source_slug: str,
) -> int | None:
    """Return the batch_id of the currently-promoted batch for *source_slug*.

    Returns ``None`` if no batch has been promoted for this source.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT batch_id FROM publication_receipts "
                "WHERE source_slug = :source_slug "
                "ORDER BY promoted_at DESC LIMIT 1"
            ),
            {"source_slug": source_slug},
        ).first()
    return row[0] if row else None


def promote_batch(
    engine: Engine,
    batch_id: int,
    plan: ReplayPlanV1,
    promoted_by: str = "",
) -> PublicationReceiptV1:
    """Explicitly promote a batch to publication.

    This is the only function that changes the published state.  It:

    1. Marks the batch as ``promoted``.
    2. Finds the previously-promoted batch for the same source (if any)
       and marks it as ``superseded`` — it is **retained**, not deleted.
    3. Inserts a :class:`PublicationReceiptV1` row recording the
       promotion with ``old_retained = True``.

    Returns the :class:`PublicationReceiptV1`.

    Raises ``ValueError`` if the batch is not in
    ``awaiting_approval`` status (or already promoted).
    """
    batch = _get_batch_by_id(engine, batch_id)
    if not batch:
        raise ValueError(f"Batch {batch_id} not found")

    status = batch["status"]
    if status not in (
        BatchStatus.AWAITING_APPROVAL.value,
        BatchStatus.PROMOTED.value,
    ):
        raise ValueError(
            f"Batch {batch_id} is in status '{status}'; "
            f"must be '{BatchStatus.AWAITING_APPROVAL.value}' to promote"
        )

    # If already promoted, return the existing receipt (idempotent).
    if status == BatchStatus.PROMOTED.value:
        existing_receipt = get_receipt(engine, batch_id)
        if existing_receipt:
            return existing_receipt

    artifact_count = count_batch_artifacts(engine, batch_id)
    old_batch_id = get_currently_promoted_batch(engine, plan.source_slug)

    receipt_id = f"rcpt-{plan.plan_id}-{batch_id}"

    with engine.begin() as conn:
        # Mark the new batch as promoted.
        conn.execute(
            text(
                "UPDATE replay_batches "
                "SET status = :status, promoted_at = CURRENT_TIMESTAMP, "
                "    promoted_by = :promoted_by, "
                "    updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :batch_id"
            ),
            {
                "batch_id": batch_id,
                "status": BatchStatus.PROMOTED.value,
                "promoted_by": promoted_by,
            },
        )

        # Mark the old batch as superseded (retained).
        if old_batch_id is not None and old_batch_id != batch_id:
            conn.execute(
                text(
                    "UPDATE replay_batches "
                    "SET status = :status, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = :old_batch_id"
                ),
                {
                    "old_batch_id": old_batch_id,
                    "status": BatchStatus.SUPERSEDED.value,
                },
            )

        # Insert the receipt (idempotent on receipt_id).
        conn.execute(
            text(
                "INSERT INTO publication_receipts "
                "  (receipt_id, batch_id, plan_id, source_slug, "
                "   promoted_at, old_batch_id, old_retained, "
                "   artifact_count, promoted_by, schema_version) "
                "VALUES (:receipt_id, :batch_id, :plan_id, :source_slug, "
                "        CURRENT_TIMESTAMP, :old_batch_id, :old_retained, "
                "        :artifact_count, :promoted_by, :schema_version) "
                "ON CONFLICT(receipt_id) DO NOTHING"
            ),
            {
                "receipt_id": receipt_id,
                "batch_id": batch_id,
                "plan_id": plan.plan_id,
                "source_slug": plan.source_slug,
                "old_batch_id": old_batch_id,
                "old_retained": True,
                "artifact_count": artifact_count,
                "promoted_by": promoted_by,
                "schema_version": SCHEMA_VERSION,
            },
        )

    return get_receipt(engine, batch_id)  # type: ignore[return-value]


def reject_batch(
    engine: Engine,
    batch_id: int,
    reason: str = "",
) -> None:
    """Mark a batch as rejected.  Old outputs are untouched."""
    update_batch_status(engine, batch_id, BatchStatus.REJECTED, notes=reason)


# ---------------------------------------------------------------------------
# Receipt queries
# ---------------------------------------------------------------------------


def get_receipt(
    engine: Engine,
    batch_id: int,
) -> PublicationReceiptV1 | None:
    """Return the publication receipt for *batch_id* or ``None``."""
    row = _get_receipt_for_batch(engine, batch_id)
    if row is None:
        return None
    return PublicationReceiptV1.from_dict(row)


def _get_receipt_for_batch(
    engine: Engine,
    batch_id: int,
) -> dict[str, Any] | None:
    """Return the raw receipt row for *batch_id*."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT receipt_id, batch_id, plan_id, source_slug, "
                "       promoted_at, old_batch_id, old_retained, "
                "       artifact_count, promoted_by, schema_version "
                "FROM publication_receipts WHERE batch_id = :batch_id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"batch_id": batch_id},
        ).first()
    if row is None:
        return None

    d = dict(row._mapping)
    # Normalise booleans (SQLite returns 0/1).
    if isinstance(d.get("old_retained"), int):
        d["old_retained"] = bool(d["old_retained"])
    # Normalise promoted_at to ISO string.
    if d.get("promoted_at") is not None and not isinstance(
        d["promoted_at"], str
    ):
        d["promoted_at"] = str(d["promoted_at"])
    return d


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_batch_by_id(
    engine: Engine,
    batch_id: int,
) -> dict[str, Any] | None:
    """Return the batch row for *batch_id*."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, plan_id, source_slug, parser_version, status, "
                "       created_at, promoted_at, promoted_by, notes "
                "FROM replay_batches WHERE id = :batch_id"
            ),
            {"batch_id": batch_id},
        ).first()
    return dict(row._mapping) if row else None


# ---------------------------------------------------------------------------
# Published artifacts store helpers (for test fixtures)
# ---------------------------------------------------------------------------


def init_published_artifacts_table(
    engine: Engine,
    table_name: str = "published_artifacts",
) -> None:
    """Create a minimal published_artifacts table for testing.

    In production, the published store is the existing race_results /
    tcc_snapshots / irc_certificates tables.  For replay tests we need
    a standalone table that mirrors the contract: artifacts keyed by
    (source_slug, artifact_url, content_hash, fetched_at, parser_version,
    parsed_output).
    """
    is_postgres = engine.dialect.name == "postgresql"
    json_type = "JSONB" if is_postgres else "TEXT"
    serial = "BIGSERIAL" if is_postgres else "INTEGER"

    sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id {serial} PRIMARY KEY,
        source_slug TEXT NOT NULL,
        artifact_url TEXT NOT NULL,
        content_hash TEXT,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        parser_version TEXT,
        parsed_output {json_type}
    );
    CREATE INDEX IF NOT EXISTS ix_{table_name}_source
        ON {table_name}(source_slug);
    """
    with engine.begin() as conn:
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


def insert_published_artifact(
    engine: Engine,
    source_slug: str,
    artifact_url: str,
    content_hash: str,
    parsed_output: Any,
    parser_version: str = "1.0.0",
    fetched_at: str | None = None,
    table_name: str = "published_artifacts",
) -> int:
    """Insert a published artifact (for test fixtures)."""
    is_postgres = engine.dialect.name == "postgresql"
    parsed_json = (
        json.dumps(parsed_output, sort_keys=True, default=str)
        if parsed_output is not None
        else None
    )

    if is_postgres:
        sql = (
            f"INSERT INTO {table_name} "
            f"  (source_slug, artifact_url, content_hash, fetched_at, "
            f"   parser_version, parsed_output) "
            f"VALUES (:source_slug, :artifact_url, :content_hash, "
            f"        :fetched_at, :parser_version, CAST(:parsed_output AS JSONB))"
        )
        params: dict[str, Any] = {
            "source_slug": source_slug,
            "artifact_url": artifact_url,
            "content_hash": content_hash,
            "fetched_at": fetched_at,
            "parser_version": parser_version,
            "parsed_output": parsed_json,
        }
    else:
        sql = (
            f"INSERT INTO {table_name} "
            f"  (source_slug, artifact_url, content_hash, fetched_at, "
            f"   parser_version, parsed_output) "
            f"VALUES (:source_slug, :artifact_url, :content_hash, "
            f"        :fetched_at, :parser_version, :parsed_output)"
        )
        params = {
            "source_slug": source_slug,
            "artifact_url": artifact_url,
            "content_hash": content_hash,
            "fetched_at": fetched_at,
            "parser_version": parser_version,
            "parsed_output": parsed_json,
        }

    with engine.begin() as conn:
        conn.execute(text(sql), params)
        row = conn.execute(
            text(
                f"SELECT id FROM {table_name} "
                f"WHERE source_slug = :source_slug "
                f"  AND artifact_url = :artifact_url "
                f"ORDER BY id DESC LIMIT 1"
            ),
            {"source_slug": source_slug, "artifact_url": artifact_url},
        ).first()
        return row[0] if row else 0
