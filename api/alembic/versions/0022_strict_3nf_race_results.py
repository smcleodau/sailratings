"""strict 3nf race results

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-26 11:09:28.445503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0022'
down_revision: Union[str, Sequence[str], None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add event_entry_id column to race_results (nullable initially)
    op.add_column('race_results', sa.Column('event_entry_id', sa.Integer(), nullable=True))
    op.alter_column('events', 'start_date', existing_type=sa.DATE(), nullable=True)

    # 2. Backfill events and event_entries from race_results
    # Note: since events has no unique constraint, we use a CTE to generate distinct events and insert them if they don't match exactly.
    # To handle the backfill properly, we can do it via a plpgsql DO block
    op.execute("""
        INSERT INTO events (name, start_date, organiser, created_at, updated_at)
        SELECT DISTINCT r.event_name, r.event_date, r.organizing_club, NOW(), NOW()
        FROM race_results r
        WHERE NOT EXISTS (
            SELECT 1 FROM events e 
            WHERE e.name = r.event_name AND e.start_date IS NOT DISTINCT FROM r.event_date
        );

        INSERT INTO event_entries (event_id, boat_id, boat_name, created_at)
        SELECT DISTINCT e.id, r.boat_id, r.raw_data->>'boat_name', NOW()
        FROM race_results r
        JOIN events e ON e.name = r.event_name AND e.start_date IS NOT DISTINCT FROM r.event_date
        WHERE NOT EXISTS (
            SELECT 1 FROM event_entries ee
            WHERE ee.event_id = e.id 
              AND ee.boat_id IS NOT DISTINCT FROM r.boat_id
              AND ee.boat_name IS NOT DISTINCT FROM (r.raw_data->>'boat_name')
        );

        UPDATE race_results r
        SET event_entry_id = ee.id
        FROM events e, event_entries ee
        WHERE r.event_name = e.name 
          AND r.event_date IS NOT DISTINCT FROM e.start_date
          AND ee.event_id = e.id
          AND ee.boat_id IS NOT DISTINCT FROM r.boat_id
          AND ee.boat_name IS NOT DISTINCT FROM (r.raw_data->>'boat_name');
    """)

    # 3. Add foreign key constraint to event_entry_id
    op.create_foreign_key('fk_race_results_event_entry_id', 'race_results', 'event_entries', ['event_entry_id'], ['id'])

    # 4. Make event_entry_id NOT NULL
    op.alter_column('race_results', 'event_entry_id', existing_type=sa.Integer(), nullable=False)

    # 5. Drop old unique constraint and create new one
    op.drop_index('race_results_matched_unique_key', table_name='race_results')
    op.drop_index('race_results_unmatched_unique_key', table_name='race_results')
    # op.create_unique_constraint('uq_race_results_entry_race', 'race_results', ['event_entry_id', 'race_name'])

    # 6. Drop old denormalized columns
    # op.drop_column('race_results', 'boat_id')
    # op.drop_column('race_results', 'event_name')
    # op.drop_column('race_results', 'event_date')
    # op.drop_column('race_results', 'event_series')
    # op.drop_column('race_results', 'organizing_club')
    # op.drop_column('race_results', 'event_type')


def downgrade() -> None:
    # 1. Re-add denormalized columns
    op.add_column('race_results', sa.Column('event_type', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('race_results', sa.Column('organizing_club', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('race_results', sa.Column('event_series', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('race_results', sa.Column('event_date', sa.DATE(), autoincrement=False, nullable=True))
    op.add_column('race_results', sa.Column('event_name', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('race_results', sa.Column('boat_id', sa.INTEGER(), autoincrement=False, nullable=True))

    # 2. Restore data from events and event_entries
    op.execute("""
    UPDATE race_results r
    SET boat_id = ee.boat_id,
        event_name = e.name,
        event_date = e.start_date,
        organizing_club = e.organiser
    FROM event_entries ee
    JOIN events e ON ee.event_id = e.id
    WHERE r.event_entry_id = ee.id;
    """)

    op.alter_column('race_results', 'event_name', nullable=False)

    # 3. Drop new constraint and restore old
    # op.drop_constraint('uq_race_results_entry_race', 'race_results', type_='unique')
    op.execute(
        "CREATE UNIQUE INDEX race_results_matched_unique_key ON race_results (boat_id, event_name, race_name, event_date) WHERE boat_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX race_results_unmatched_unique_key ON race_results ((raw_data->>'boat_name'), event_name, race_name, event_date) WHERE boat_id IS NULL AND (raw_data->>'boat_name') IS NOT NULL"
    )

    # 4. Drop new column
    op.drop_constraint('fk_race_results_event_entry_id', 'race_results', type_='foreignkey')
    op.drop_column('race_results', 'event_entry_id')
