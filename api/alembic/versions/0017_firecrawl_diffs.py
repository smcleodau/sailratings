"""Comparison table for Firecrawl-vs-legacy parallel runs.

Each row records one event URL replayed through the new extractor and
compared row-by-row against existing race_results. Surfacing this on
/justin/firecrawl tells Stuart when a source is safe to cut over and the
legacy scraper safe to delete.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firecrawl_diffs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source", sa.Text, nullable=False),       # legacy source name
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("event_name", sa.Text),
        sa.Column("event_date", sa.Date),
        sa.Column("legacy_rows", sa.Integer, nullable=False),
        sa.Column("firecrawl_rows", sa.Integer, nullable=False),
        sa.Column("matched", sa.Integer, nullable=False),
        sa.Column("match_rate", sa.Numeric(4, 3), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("missing_names", postgresql.JSONB),       # in legacy NOT in fc
        sa.Column("extra_names", postgresql.JSONB),         # in fc NOT in legacy
        sa.Column("notes", sa.Text),
    )
    op.create_index("idx_fc_diffs_source", "firecrawl_diffs", ["source"])
    op.create_index("idx_fc_diffs_ran_at", "firecrawl_diffs", ["ran_at"])
    op.create_index("idx_fc_diffs_source_url", "firecrawl_diffs", ["source_url"])


def downgrade() -> None:
    op.drop_index("idx_fc_diffs_source_url", table_name="firecrawl_diffs")
    op.drop_index("idx_fc_diffs_ran_at", table_name="firecrawl_diffs")
    op.drop_index("idx_fc_diffs_source", table_name="firecrawl_diffs")
    op.drop_table("firecrawl_diffs")
