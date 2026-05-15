"""add boat_corrections table

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "boat_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("boat_id", sa.Integer(), sa.ForeignKey("boats.id"), nullable=True),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("current_value", sa.Text()),
        sa.Column("proposed_value", sa.Text(), nullable=False),
        sa.Column("submitted_email", sa.Text()),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("review_notes", sa.Text()),
    )
    op.create_index(
        "idx_boat_corrections_status",
        "boat_corrections",
        ["status", sa.text("submitted_at DESC")],
    )
    op.create_index(
        "idx_boat_corrections_boat",
        "boat_corrections",
        ["boat_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_boat_corrections_boat", table_name="boat_corrections")
    op.drop_index("idx_boat_corrections_status", table_name="boat_corrections")
    op.drop_table("boat_corrections")
