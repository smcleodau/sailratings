"""Add events, event_entries, and boat_events tables

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-26 07:13:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # DP-03-05 (canonical migrations): this revision was historically a no-op
    # stub ("tables are already created manually"), which made a from-scratch
    # ``alembic upgrade head`` fail at 0022 because ``events`` never existed.
    # Create the three tables idempotently (IF NOT EXISTS) so the canonical
    # chain builds cleanly from base on a fresh database while remaining a
    # no-op on databases where they were already created by hand.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id          SERIAL PRIMARY KEY,
            name        TEXT,
            start_date  DATE,
            end_date    DATE,
            venue       TEXT,
            course_type TEXT,
            organiser   TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS event_entries (
            id          SERIAL PRIMARY KEY,
            event_id    INTEGER NOT NULL REFERENCES events(id),
            boat_id     INTEGER REFERENCES boats(id),
            sail_number TEXT,
            boat_name   TEXT,
            tcc         NUMERIC(5, 3),
            design      TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS boat_events (
            id         SERIAL PRIMARY KEY,
            boat_id    INTEGER NOT NULL REFERENCES boats(id),
            event_type TEXT,
            event_date TIMESTAMPTZ,
            payload    JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS boat_events")
    op.execute("DROP TABLE IF EXISTS event_entries")
    op.execute("DROP TABLE IF EXISTS events")

