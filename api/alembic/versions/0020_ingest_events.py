"""Add ``ingest_events`` for per-cert error + match-failure logging.

Per-row diagnostics complement the existing run-level ``ingestion_log``
table — when a scraper or matcher silently drops a cert, this is where
we record *why*. Used by:

  - ``scrapers.orc.backfill_orc_details`` (parse failures)
  - ``matching.identity.match_orc_to_irc`` (orphan / no-match)
  - the ``irc-data report orc-orphans`` CLI

Coordination note
-----------------
Plan A creates revision 0019 (``race_results.transport``). This is 0020
and revises 0019. If 0019 is not present at apply time, edit
``down_revision`` to point at whatever the current head is.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("meta", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ingest_events_source", "ingest_events", ["source"])
    op.create_index("ix_ingest_events_reference", "ingest_events", ["reference"])
    op.create_index("ix_ingest_events_created_at", "ingest_events", ["created_at"])
    op.create_index(
        "ix_ingest_events_source_status",
        "ingest_events",
        ["source", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_events_source_status", table_name="ingest_events")
    op.drop_index("ix_ingest_events_created_at", table_name="ingest_events")
    op.drop_index("ix_ingest_events_reference", table_name="ingest_events")
    op.drop_index("ix_ingest_events_source", table_name="ingest_events")
    op.drop_table("ingest_events")
