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
    # Tables are already created manually, this is to keep Alembic history consistent.
    # To be safe, we could use IF NOT EXISTS if raw SQL was used.
    pass


def downgrade() -> None:
    op.drop_table('boat_events')
    op.drop_table('event_entries')
    op.drop_table('events')

