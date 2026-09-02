"""Replay / backfill tables (DP-02-04 / SPEC-013).

Creates three tables:

  * ``replay_batches``         — one row per replay plan; keyed by
                                ``plan_id`` (idempotency).  Tracks the
                                batch lifecycle status.
  * ``replay_artifacts``       — one row per parsed artifact within a
                                batch.  Stores both the new parsed
                                output and the old published output for
                                comparison.  Separate from the
                                published store — no in-place rewrite.
  * ``publication_receipts``   — one row per explicit promotion.
                                Records the promoted batch, the old
                                batch (retained), and a ``receipt_id``
                                for audit.

Canonical chain (DP-03-05): renumbered from the duplicated id ``0024`` to
the unique id ``20260901b`` so the alembic graph is a single linear head.

Revision ID: 20260901b
Revises: 20260901a
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260901b"
down_revision: Union[str, Sequence[str], None] = "20260901a"
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
    """create_table IF NOT EXISTS (idempotent for canonical chain, DP-03-05).

    Builds the table inside the migration's shared MetaData so ForeignKey
    references to other tables resolve correctly."""
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



def upgrade() -> None:
    # -- replay_batches ---------------------------------------------------
    _ctine(
        "replay_batches",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("plan_id", sa.String(128), nullable=False, unique=True),
        sa.Column("source_slug", sa.String(128), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("artifact_filter", sa.JSON),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True)),
        sa.Column("promoted_by", sa.Text),
        sa.Column("notes", sa.Text),
    )
    _cidx_if_not_exists("ix_replay_batches_plan_id", "replay_batches", ["plan_id"])

    # -- replay_artifacts -------------------------------------------------
    _ctine(
        "replay_artifacts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "batch_id",
            sa.BigInteger,
            sa.ForeignKey("replay_batches.id"),
            nullable=False,
        ),
        sa.Column("artifact_url", sa.Text, nullable=False),
        sa.Column("content_hash", sa.Text),
        sa.Column("parsed_output", sa.JSON),
        sa.Column("old_parsed_output", sa.JSON),
        sa.Column("parse_status", sa.String(32), server_default="pending"),
        sa.Column("parse_error", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    _cidx_if_not_exists("ix_replay_artifacts_batch_id", "replay_artifacts", ["batch_id"])

    # -- publication_receipts ---------------------------------------------
    _ctine(
        "publication_receipts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("receipt_id", sa.String(256), nullable=False, unique=True),
        sa.Column("batch_id", sa.BigInteger, nullable=False),
        sa.Column("plan_id", sa.String(128), nullable=False),
        sa.Column("source_slug", sa.String(128), nullable=False),
        sa.Column(
            "promoted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("old_batch_id", sa.BigInteger),
        sa.Column("old_retained", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("artifact_count", sa.Integer, server_default="0"),
        sa.Column("promoted_by", sa.Text),
        sa.Column("schema_version", sa.String(16), server_default="v1"),
    )
    _cidx_if_not_exists("ix_publication_receipts_batch_id", "publication_receipts", ["batch_id"])


def downgrade() -> None:
    op.drop_index(
        "ix_publication_receipts_batch_id", table_name="publication_receipts"
    )
    op.drop_table("publication_receipts")

    op.drop_index("ix_replay_artifacts_batch_id", table_name="replay_artifacts")
    op.drop_table("replay_artifacts")

    op.drop_index("ix_replay_batches_plan_id", table_name="replay_batches")
    op.drop_table("replay_batches")
