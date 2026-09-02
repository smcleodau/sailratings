"""Canonical merge, compatibility views, and migration/backup evidence (DP-03-05).

This is the **capstone** revision that turns the previously-tangled migration
graph (duplicate ``0023``/``0024``/``0025`` ids, four heads, ambiguous
``alembic upgrade head``) into a single canonical linear chain and adds the
schema-evolution machinery the platform needs to *evolve the data model
without losing history or breaking consumers*.

It creates (all idempotent):

Bookkeeping / evidence
----------------------
* ``schema_migrations``  — one row per applied revision (applied_at, checksum,
  rows affected, duration).  This is the durable *migration evidence* store
  the acceptance criteria ask for; the verification harness writes into it.
* ``backup_checks``      — one row per backup/restore verification
  (backup id, db name, size, SHA-256, verified_at, status).  Records the
  "backup checks" required before/after a migration and documents the
  restore strategy.

Compatibility views (stable v1 API surface)
-------------------------------------------
These views are the **versioned consumer contract**.  As underlying tables
evolve, the views are kept stable (or a ``v2_*`` is added alongside) so
existing consumers keep working:

* ``v1_boat_ratings``            — one row per boat with its *latest* TCC
                                   snapshot (the "current rating" read model).
* ``v1_race_results``            — race results flattened with boat identity
                                   (the classic consumer join).
* ``v1_fact_assertions_current`` — the *current* resolved truth from the
                                   bitemporal ``fact_assertions`` store:
                                   active and not superseded.

Rollback / restore
------------------
Everything here is **additive** — no existing table or column is altered or
dropped.  ``downgrade()`` drops only the views and the two bookkeeping
tables, leaving all user data intact; re-running ``upgrade()`` recreates the
views.  That pair is the *tested rollback / restore strategy* exercised by
``tests/migrations/test_rollback.py``.

Revision ID: 0026
Revises: 20260526a
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "20260526a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# SQL (each statement executed individually for clarity + idempotency)
# ---------------------------------------------------------------------------

_STATEMENTS = [
    # -- migration evidence -------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id              BIGSERIAL PRIMARY KEY,
        revision        TEXT NOT NULL,
        direction       TEXT NOT NULL DEFAULT 'upgrade',
        applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        checksum        TEXT,
        rows_affected   BIGINT,
        duration_ms     BIGINT,
        notes           TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_schema_migrations_revision ON schema_migrations (revision)",
    "CREATE INDEX IF NOT EXISTS ix_schema_migrations_applied_at ON schema_migrations (applied_at)",

    # -- backup / restore checks -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS backup_checks (
        id              BIGSERIAL PRIMARY KEY,
        backup_id       TEXT NOT NULL,
        db_name         TEXT NOT NULL,
        taken_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        size_bytes      BIGINT,
        sha256          TEXT,
        verified_at     TIMESTAMPTZ,
        status          TEXT NOT NULL DEFAULT 'pending',
        notes           TEXT,
        CONSTRAINT ck_backup_checks_status
            CHECK (status IN ('pending', 'verified', 'failed'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_backup_checks_backup_id ON backup_checks (backup_id)",
    "CREATE INDEX IF NOT EXISTS ix_backup_checks_status ON backup_checks (status)",

    # -- v1_boat_ratings: current rating per boat ---------------------------
    """
    CREATE OR REPLACE VIEW v1_boat_ratings AS
    SELECT
        b.id            AS boat_id,
        b.boat_name     AS boat_name,
        b.sail_number   AS sail_number,
        b.cert_number   AS cert_number,
        b.design        AS design,
        b.country       AS country,
        b.year_built    AS year_built,
        t.snapshot_date AS rating_date,
        t.tcc           AS tcc,
        t.non_spi_tcc   AS non_spi_tcc,
        t.endorsed      AS endorsed
    FROM boats b
    LEFT JOIN LATERAL (
        SELECT s.snapshot_date, s.tcc, s.non_spi_tcc, s.endorsed
        FROM tcc_snapshots s
        WHERE s.boat_id = b.id
        ORDER BY s.snapshot_date DESC
        LIMIT 1
    ) t ON true
    """,

    # -- v1_race_results: results flattened via the strict-3NF join ---------
    # race_results -> event_entries -> (events, boats)
    """
    CREATE OR REPLACE VIEW v1_race_results AS
    SELECT
        r.id             AS race_result_id,
        e.name           AS event_name,
        e.start_date     AS event_date,
        r.race_name      AS race_name,
        r.race_number    AS race_number,
        r.division       AS division,
        r.class_name     AS class_name,
        r.place          AS place,
        r.status         AS status,
        r.rating_value   AS rating_value,
        r.tcc_at_race    AS tcc_at_race,
        r.elapsed_time   AS elapsed_time,
        r.corrected_time AS corrected_time,
        b.id             AS boat_id,
        b.boat_name      AS boat_name,
        b.sail_number    AS sail_number,
        b.design         AS design,
        b.country        AS country
    FROM race_results r
    JOIN event_entries ee ON ee.id = r.event_entry_id
    LEFT JOIN events e ON e.id = ee.event_id
    LEFT JOIN boats b ON b.id = ee.boat_id
    """,

    # -- v1_fact_assertions_current: current resolved truth -----------------
    """
    CREATE OR REPLACE VIEW v1_fact_assertions_current AS
    SELECT
        a.assertion_id   AS assertion_id,
        a.entity_type    AS entity_type,
        a.entity_key     AS entity_key,
        a.field          AS field,
        a.value_json     AS value_json,
        a.unit           AS unit,
        a.valid_from     AS valid_from,
        a.valid_to       AS valid_to,
        a.recorded_at    AS recorded_at,
        a.source_slug    AS source_slug,
        a.confidence     AS confidence
    FROM fact_assertions a
    WHERE a.status = 'active'
      AND a.superseded_by IS NULL
    """,
]

_DOWN_STATEMENTS = [
    "DROP VIEW IF EXISTS v1_fact_assertions_current",
    "DROP VIEW IF EXISTS v1_race_results",
    "DROP VIEW IF EXISTS v1_boat_ratings",
    "DROP TABLE IF EXISTS backup_checks",
    "DROP TABLE IF EXISTS schema_migrations",
]


def upgrade() -> None:
    for stmt in _STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWN_STATEMENTS:
        op.execute(stmt)
