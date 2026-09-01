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

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- replay_batches ---------------------------------------------------
    op.create_table(
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
    op.create_index("ix_replay_batches_plan_id", "replay_batches", ["plan_id"])

    # -- replay_artifacts -------------------------------------------------
    op.create_table(
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
    op.create_index("ix_replay_artifacts_batch_id", "replay_artifacts", ["batch_id"])

    # -- publication_receipts ---------------------------------------------
    op.create_table(
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
    op.create_index(
        "ix_publication_receipts_batch_id", "publication_receipts", ["batch_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_receipts_batch_id", table_name="publication_receipts"
    )
    op.drop_table("publication_receipts")

    op.drop_index("ix_replay_artifacts_batch_id", table_name="replay_artifacts")
    op.drop_table("replay_artifacts")

    op.drop_index("ix_replay_batches_plan_id", table_name="replay_batches")
    op.drop_table("replay_batches")
