"""Raw lake metadata index (DP-02-02 / SPEC-013)

Creates the ``raw_lake_artifacts`` table in the operational Postgres
database for organisations that prefer to index raw-lake artifacts in
Postgres rather than (or in addition to) the standalone SQLite index.

The raw lake itself stores objects on the filesystem (outside the DB);
this table is the *metadata index* that makes those objects searchable.

Columns mirror :class:`irc_data.sources.raw_lake.RawArtifactReceiptV1`.

Revision ID: 0024_raw_lake
Revises: 0023
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from typing import Sequence, Union


revision: str = "0024_raw_lake"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_lake_artifacts",
        sa.Column("artifact_id", sa.Text(), primary_key=True),
        sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("content_length", sa.Integer(), nullable=False),
        sa.Column("source_slug", sa.Text(), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("content_type", sa.Text()),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "policy_version",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'interim-v0'"),
        ),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("encryption_key_id", sa.Text()),
        sa.Column("encrypted_length", sa.Integer()),
        sa.Column("retention_expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "legal_hold", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_raw_lake_artifacts_hash", "raw_lake_artifacts", ["content_hash"])
    op.create_index("ix_raw_lake_artifacts_source", "raw_lake_artifacts", ["source_slug"])
    op.create_index("ix_raw_lake_artifacts_url", "raw_lake_artifacts", ["url"])
    op.create_index(
        "ix_raw_lake_artifacts_retention", "raw_lake_artifacts", ["retention_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_raw_lake_artifacts_retention", table_name="raw_lake_artifacts")
    op.drop_index("ix_raw_lake_artifacts_url", table_name="raw_lake_artifacts")
    op.drop_index("ix_raw_lake_artifacts_source", table_name="raw_lake_artifacts")
    op.drop_index("ix_raw_lake_artifacts_hash", table_name="raw_lake_artifacts")
    op.drop_table("raw_lake_artifacts")
