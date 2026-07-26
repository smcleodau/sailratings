"""Add boat news

Revision ID: aa0f8e0c178b
Revises: 0022
Create Date: 2026-07-26 11:19:11.563056

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa0f8e0c178b'
down_revision: Union[str, Sequence[str], None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'boat_news',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source_domain', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('published_at', sa.Date(), nullable=True),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('snippet', sa.Text(), nullable=True),
        sa.Column('raw_markdown', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('url')
    )
    
    op.create_table(
        'boat_news_mentions',
        sa.Column('news_id', sa.Integer(), nullable=False),
        sa.Column('boat_id', sa.Integer(), nullable=False),
        sa.Column('confidence', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.ForeignKeyConstraint(['boat_id'], ['boats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['news_id'], ['boat_news.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('news_id', 'boat_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('boat_news_mentions')
    op.drop_table('boat_news')
