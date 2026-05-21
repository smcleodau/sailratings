"""Add event_discovery table for the crawler pipeline.

The pipeline crawls event-website URLs (e.g. brisbanetogladstone.com),
asks Claude which scoring platform they link to (SailSys / TopYacht /
Sailwave / YachtScoring / PDF), and parks the candidate here for Justin
to confirm before we ingest. Confirmed entries route to the existing
per-platform scrapers.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_discovery",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, nullable=False,
                  server_default=sa.text("'manual'")),  # manual | seed_crawl | dispatched
        sa.Column("seed_url", sa.Text),  # the seed that found this URL, if any

        # Extraction
        sa.Column("scoring_platform", sa.Text),  # sailsys | topyacht | sailwave | yachtscoring | pdf | unknown
        sa.Column("platform_ids", postgresql.JSONB),  # {"club_id": 37, "series_id": 5204} etc.
        sa.Column("title", sa.Text),
        sa.Column("event_date", sa.Date),
        sa.Column("event_location", sa.Text),
        sa.Column("confidence", sa.Numeric(3, 2)),  # 0..1, how sure Claude is
        sa.Column("raw_extraction", postgresql.JSONB),  # full Claude output for audit

        # Lifecycle
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        # pending | confirmed | rejected | ingested | failed | duplicate
        sa.Column("confirmed_by", sa.Text),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True)),
        sa.Column("ingestion_log_id", sa.Integer),  # FK-ish to ingestion_log.id
        sa.Column("error_message", sa.Text),
        sa.Column("notes", sa.Text),
    )
    op.create_index("idx_event_discovery_status", "event_discovery", ["status"])
    op.create_index("idx_event_discovery_platform", "event_discovery", ["scoring_platform"])
    op.create_index("idx_event_discovery_source_url", "event_discovery", ["source_url"])


def downgrade() -> None:
    op.drop_index("idx_event_discovery_source_url", table_name="event_discovery")
    op.drop_index("idx_event_discovery_platform", table_name="event_discovery")
    op.drop_index("idx_event_discovery_status", table_name="event_discovery")
    op.drop_table("event_discovery")
