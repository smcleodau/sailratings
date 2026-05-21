"""Add transport column to race_results.

Background
----------
The Firecrawl + Claude extractor cuts over five bespoke scrapers (ISORA,
SailRaceHQ, RHKYC, Cowes Week, Sydney-Hobart, Sailwave) onto a single
pipeline. During the 14-day parallel-run window we need to attribute each
row to the path that produced it so we can diff legacy vs Firecrawl row
counts per source.

The ``transport`` column stores:

- ``'legacy'``  — written by the bespoke per-source scraper.
- ``'firecrawl'`` — written by ``discover-and-ingest`` / ``ingest-event``
  via the Firecrawl + extractor pipeline.

After the parallel-run gate closes (Task A6 follow-up), the bespoke
scrapers are deleted and the column becomes informational only.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "race_results",
        sa.Column("transport", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_race_results_transport",
        "race_results",
        ["transport"],
    )


def downgrade() -> None:
    op.drop_index("ix_race_results_transport", "race_results")
    op.drop_column("race_results", "transport")
