"""Replace race_results UNIQUE with NULLS NOT DISTINCT partial indexes.

Background
----------
The old UNIQUE constraint `race_results_boat_event_race_key` was
`(boat_id, event_name, race_name, event_date)` with default NULLS
DISTINCT semantics, so two rows with NULL race_name or NULL event_date
never collided. The sailsys scraper writes some rows with NULL
race_name and topyacht writes many with NULL event_date, so every
cron tick was inserting fresh duplicates of the same race. By the
2026-05-20 cleanup pass there were 116,150 such duplicates.

Two partial UNIQUE indexes replace the old constraint:

  - race_results_matched_unique_key
      (boat_id, event_name, race_name, event_date) NULLS NOT DISTINCT
      WHERE boat_id IS NOT NULL

  - race_results_unmatched_unique_key
      ((raw_data->>'boat_name'), event_name, race_name, event_date)
        NULLS NOT DISTINCT
      WHERE boat_id IS NULL AND raw_data->>'boat_name' IS NOT NULL

Splitting matched vs unmatched rows is necessary because NULL boat_id
itself is a discriminator — three different unmatched boats at the
same event are three separate rows, not one. The unmatched index uses
the raw_data boat name (the only identity we have for an unmatched
row) as the primary key element instead.

The application's upsert_race_result was updated to use index_elements
+ index_where instead of constraint=, so the right partial index is
chosen automatically.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the broken UNIQUE that let NULL columns through unchecked.
    op.execute("ALTER TABLE race_results DROP CONSTRAINT IF EXISTS race_results_boat_event_race_key;")

    # Matched rows: dedupe by (boat_id, event_name, race_name, event_date).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS race_results_matched_unique_key
          ON race_results (boat_id, event_name, race_name, event_date)
          NULLS NOT DISTINCT
          WHERE boat_id IS NOT NULL;
    """)

    # Unmatched rows: dedupe by (raw_name, event_name, race_name, event_date)
    # so different unmatched boats at the same event don't collide.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS race_results_unmatched_unique_key
          ON race_results ((raw_data->>'boat_name'), event_name, race_name, event_date)
          NULLS NOT DISTINCT
          WHERE boat_id IS NULL AND raw_data->>'boat_name' IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS race_results_unmatched_unique_key;")
    op.execute("DROP INDEX IF EXISTS race_results_matched_unique_key;")
    op.execute("""
        ALTER TABLE race_results
          ADD CONSTRAINT race_results_boat_event_race_key
          UNIQUE (boat_id, event_name, race_name, event_date);
    """)
