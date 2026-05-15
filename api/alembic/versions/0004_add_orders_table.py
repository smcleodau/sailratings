"""add orders table for report purchases

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_token",
            sa.Uuid,
            unique=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("boat_id", sa.Integer, sa.ForeignKey("boats.id"), nullable=False),
        sa.Column("email", sa.Text),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.Text, nullable=False, server_default="usd"),
        sa.Column("stripe_session_id", sa.Text, unique=True),
        sa.Column("stripe_payment_intent", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("report_markdown", sa.Text),
        sa.Column("report_analytics", sa.JSON),
        sa.Column("pdf_path", sa.Text),
        sa.Column("search_query", sa.Text),
        sa.Column("teaser_text", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("report_generated_at", sa.DateTime(timezone=True)),
        sa.Column("email_sent_at", sa.DateTime(timezone=True)),
    )

    op.create_index("idx_orders_boat", "orders", ["boat_id"])
    op.create_index("idx_orders_status", "orders", ["status"])


def downgrade() -> None:
    op.drop_index("idx_orders_status")
    op.drop_index("idx_orders_boat")
    op.drop_table("orders")
