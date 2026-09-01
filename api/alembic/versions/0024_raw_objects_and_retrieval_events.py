"""raw_objects and retrieval_events — content-addressed artifact store (DP-02-01)

Creates two tables that together implement the immutable raw artifact
and provenance envelope contracts:

* ``raw_objects`` — the **content-addressed, immutable** blob registry.
  The SHA-256 hash of the bytes is the primary key.  Raw bytes live in
  the filesystem :class:`~irc_data.sources.provenance.RawObjectStore`;
  this table records the metadata (size, content type, location,
  creation time).  Once written, a raw object is never modified.

* ``retrieval_events`` — one row **per capture**.  When the same
  content is fetched again (e.g. a page that hasn't changed), no new
  ``raw_objects`` row is created, but a **new** ``retrieval_events``
  row is inserted — preserving the distinct retrieval time, requested
  URI, status, and lineage.  This is the "duplicate captures reference
  existing bytes while retaining retrieval events" guarantee.

See SPEC-013 / DP-02-01 for the envelope contract.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # raw_objects — content-addressed immutable blob registry
    # ------------------------------------------------------------------
    op.create_table(
        "raw_objects",
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text()),
        sa.Column("object_location", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("content_hash", name="pk_raw_objects"),
    )
    op.create_index(
        "ix_raw_objects_content_hash",
        "raw_objects",
        ["content_hash"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # retrieval_events — one row per capture (provenance envelope)
    # ------------------------------------------------------------------
    op.create_table(
        "retrieval_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("requested_uri", sa.Text()),
        sa.Column("resolved_uri", sa.Text()),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("headers_subset", sa.JSON),
        sa.Column("status", sa.Integer()),
        sa.Column("object_location", sa.Text(), nullable=False),
        sa.Column("adapter_version", sa.Text()),
        sa.Column("lineage", sa.JSON),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'1'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["content_hash"], ["raw_objects.content_hash"], ondelete="RESTRICT",
            name="fk_retrieval_events_content_hash",
        ),
    )
    op.create_index(
        "ix_retrieval_events_content_hash",
        "retrieval_events",
        ["content_hash"],
    )
    op.create_index(
        "ix_retrieval_events_source",
        "retrieval_events",
        ["source"],
    )
    op.create_index(
        "ix_retrieval_events_retrieved_at",
        "retrieval_events",
        ["retrieved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_events_retrieved_at", table_name="retrieval_events")
    op.drop_index("ix_retrieval_events_source", table_name="retrieval_events")
    op.drop_index("ix_retrieval_events_content_hash", table_name="retrieval_events")
    op.drop_table("retrieval_events")

    op.drop_index("ix_raw_objects_content_hash", table_name="raw_objects")
    op.drop_table("raw_objects")
