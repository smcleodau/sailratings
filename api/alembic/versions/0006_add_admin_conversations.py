"""add admin_conversations and admin_messages tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_conversations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "admin_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Integer,
            sa.ForeignKey("admin_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("queries", sa.JSON),
        sa.Column("proposed_changes", sa.JSON),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "idx_admin_messages_conversation",
        "admin_messages",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_admin_messages_conversation")
    op.drop_table("admin_messages")
    op.drop_table("admin_conversations")
