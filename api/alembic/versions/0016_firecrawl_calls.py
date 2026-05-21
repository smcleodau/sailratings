"""Telemetry table for every Firecrawl API call.

We log one row per scrape/map so the /justin/firecrawl dashboard can
answer: how many credits are we burning, on which domains, how often do
calls fail, and how long do they take. Without this we're flying blind
on the migration off bespoke scrapers — the whole point of moving to
Firecrawl is operational visibility, and that starts with a count.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firecrawl_calls",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("called_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("mode", sa.Text, nullable=False),           # 'scrape' | 'map'
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("domain", sa.Text, nullable=False),         # denormalised for fast group-by
        sa.Column("status", sa.Text, nullable=False),         # 'ok' | 'empty' | 'error'
        sa.Column("http_status", sa.Integer),
        sa.Column("credits", sa.Integer),                     # SDK-reported credits_used, fallback 1
        sa.Column("duration_ms", sa.Integer),
        sa.Column("response_chars", sa.Integer),              # len(markdown) for scrape, 0 for map
        sa.Column("links_found", sa.Integer),                 # for map mode
        sa.Column("error_message", sa.Text),
        sa.Column("caller", sa.Text),                         # 'discovery' | 'cli' | 'manual' | …
    )
    op.create_index("idx_fc_calls_called_at", "firecrawl_calls", ["called_at"])
    op.create_index("idx_fc_calls_domain", "firecrawl_calls", ["domain"])
    op.create_index("idx_fc_calls_status", "firecrawl_calls", ["status"])
    op.create_index("idx_fc_calls_mode", "firecrawl_calls", ["mode"])


def downgrade() -> None:
    op.drop_index("idx_fc_calls_mode", table_name="firecrawl_calls")
    op.drop_index("idx_fc_calls_status", table_name="firecrawl_calls")
    op.drop_index("idx_fc_calls_domain", table_name="firecrawl_calls")
    op.drop_index("idx_fc_calls_called_at", table_name="firecrawl_calls")
    op.drop_table("firecrawl_calls")
