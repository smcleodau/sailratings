"""DP-05-02 — validation, quarantine and promotion gates.

Creates the five tables that keep bad batches out of the canonical views:

* ``quality_batches``       — one row per **batch version**.
                              ``(pipeline, source_slug, version)`` is unique
                              so a retry/replay always lands in a fresh
                              version (acceptance: retry creates a new
                              version).  ``status`` tracks the gate
                              lifecycle.
* ``quality_batch_rows``    — staged payload rows for a batch (extraction
                              records / canonical assertions+rejects /
                              identity effects).  Never read by consumers
                              directly — the consumer view joins on
                              promoted batches only.
* ``quality_quarantine``    — one row per quarantined batch: the rule
                              failures (with samples) and a bounded sample
                              of staged rows, for the review UI.
* ``quality_verdicts``      — one row per validation run (full report).
* ``quality_promotions``    — one row per explicit promotion; promotion is
                              the only transition that changes
                              consumer-visible state, and it happens in a
                              single transaction (partial publication
                              cannot occur).

Idempotent: every ``CREATE TABLE`` / ``CREATE INDEX`` is guarded so the
migration is safe to re-apply on the canonical chain (DP-03-05).

This revision hangs off the DP-05-03 reconciliation revision
(``20260904a``), which itself follows the numeric ``0027`` line — so
the DP-05 series lands on one linear branch of the graph.

Revision ID: 20260904b
Revises: 20260904a
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260904b"
down_revision: Union[str, Sequence[str], None] = "20260904a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MD = None


def _metadata():
    """Lazily-shared MetaData so ForeignKey references resolve across tables
    created in the same migration."""
    global _MD
    if _MD is None:
        from sqlalchemy import MetaData

        _MD = MetaData()
    return _MD


def _ctine(name, *columns, **kwargs):
    """create_table IF NOT EXISTS (idempotent for canonical chain, DP-03-05)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(name):
        return
    from sqlalchemy import Table

    md = _metadata()
    Table(name, md, *columns, **kwargs)
    md.tables[name].create(bind)


def _cidx_if_not_exists(index_name, table_name, columns, unique=False):
    """create_index IF NOT EXISTS (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes(table_name)}
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def _cunique_if_not_exists(constraint_name, table_name, columns):
    """create_unique_constraint IF NOT EXISTS (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {uc["name"] for uc in insp.get_unique_constraints(table_name)}
    if constraint_name in existing:
        return
    op.create_unique_constraint(constraint_name, table_name, columns)


def upgrade() -> None:
    jsonb = sa.dialects.postgresql.JSONB if op.get_bind().dialect.name == "postgresql" else sa.Text

    # -- quality_batches ---------------------------------------------------
    _ctine(
        "quality_batches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("batch_key", sa.Text, nullable=False, unique=True),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("source_slug", sa.Text, nullable=False),
        sa.Column("gate", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("record_count", sa.Integer, server_default="0"),
        sa.Column("content_hash", sa.Text),
        sa.Column("metadata", jsonb),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("promoted_at", sa.DateTime(timezone=True)),
        sa.Column("promoted_by", sa.Text),
    )
    _cunique_if_not_exists(
        "uq_quality_batches_pipeline_source_version",
        "quality_batches",
        ["pipeline", "source_slug", "version"],
    )
    _cidx_if_not_exists(
        "ix_quality_batches_pipeline_source",
        "quality_batches",
        ["pipeline", "source_slug"],
    )
    _cidx_if_not_exists(
        "ix_quality_batches_status", "quality_batches", ["status"]
    )

    # -- quality_batch_rows ------------------------------------------------
    _ctine(
        "quality_batch_rows",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "batch_key",
            sa.Text,
            sa.ForeignKey("quality_batches.batch_key"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer, nullable=False),
        sa.Column("row_kind", sa.Text, nullable=False),
        sa.Column("row_json", jsonb, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _cidx_if_not_exists(
        "ix_quality_batch_rows_batch_key", "quality_batch_rows", ["batch_key"]
    )

    # -- quality_quarantine -------------------------------------------------
    _ctine(
        "quality_quarantine",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("quarantine_id", sa.Text, nullable=False, unique=True),
        sa.Column("batch_key", sa.Text, nullable=False),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("source_slug", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("gate", sa.Text, nullable=False),
        sa.Column("rule_classes", jsonb),
        sa.Column("failures", jsonb),
        sa.Column("sample_rows", jsonb),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution", sa.Text),
    )
    _cidx_if_not_exists(
        "ix_quality_quarantine_batch_key", "quality_quarantine", ["batch_key"]
    )

    # -- quality_verdicts ---------------------------------------------------
    _ctine(
        "quality_verdicts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("verdict_id", sa.Text, nullable=False, unique=True),
        sa.Column("batch_key", sa.Text, nullable=False),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("source_slug", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("gate", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("rules_evaluated", sa.Integer, server_default="0"),
        sa.Column("rules_failed", sa.Integer, server_default="0"),
        sa.Column("failures", jsonb),
        sa.Column("record_count", sa.Integer, server_default="0"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    _cidx_if_not_exists(
        "ix_quality_verdicts_batch_key", "quality_verdicts", ["batch_key"]
    )

    # -- quality_promotions -------------------------------------------------
    _ctine(
        "quality_promotions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.Text, nullable=False, unique=True),
        sa.Column("batch_key", sa.Text, nullable=False),
        sa.Column("pipeline", sa.Text, nullable=False),
        sa.Column("source_slug", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("record_count", sa.Integer, server_default="0"),
        sa.Column("superseded_batch_key", sa.Text),
        sa.Column("superseded_version", sa.Integer),
        sa.Column("promoted_by", sa.Text),
        sa.Column("auto", sa.Boolean, server_default=sa.false()),
        sa.Column("promoted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("schema_version", sa.Text, server_default="v1"),
    )
    _cidx_if_not_exists(
        "ix_quality_promotions_batch_key", "quality_promotions", ["batch_key"]
    )
    _cidx_if_not_exists(
        "ix_quality_promotions_pipeline_source",
        "quality_promotions",
        ["pipeline", "source_slug"],
    )


def downgrade() -> None:
    # Tables are additive and isolated; downgrade drops them in FK-safe order.
    for table in (
        "quality_promotions",
        "quality_verdicts",
        "quality_quarantine",
        "quality_batch_rows",
        "quality_batches",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
