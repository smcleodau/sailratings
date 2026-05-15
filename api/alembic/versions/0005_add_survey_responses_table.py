"""add survey_responses table for post-report feedback

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_token", sa.Uuid, nullable=False),
        sa.Column("usefulness_score", sa.Integer),
        sa.Column("newsletter_signup", sa.Boolean, server_default="false"),
        sa.Column("user_type", sa.Text),
        sa.Column("missing_info", sa.Text),
        sa.Column("email", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_index("idx_survey_order_token", "survey_responses", ["order_token"])


def downgrade() -> None:
    op.drop_index("idx_survey_order_token")
    op.drop_table("survey_responses")
