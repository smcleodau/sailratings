"""payments: users, subscriptions, stripe_events; orders user/payment-status columns

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-08

PAY-01-09 — Stripe webhook: customer.subscription.* into subscriptions,
idempotent via stripe_events.

Creates:
  * users            — account records keyed by Clerk id (SPEC-09) with a
                       stripe_customer_id link.
  * subscriptions    — mirror of Stripe subscription state, upserted by
                       stripe_subscription_id from customer.subscription.*
                       webhook events.
  * stripe_events    — webhook idempotency ledger. Every received event is
                       INSERTed ON CONFLICT DO NOTHING; a conflict means the
                       delivery is a replay and is acknowledged without
                       re-processing. ``processed_at``/``error`` record the
                       dispatch outcome; rows whose error starts with
                       'parked:' are surfaced in the admin UI.

Alters:
  * orders           — adds ``user_id`` (FK users) and
                       ``stripe_payment_status``; both are set by the
                       existing checkout.session.completed handler.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("clerk_id", sa.Text, unique=True),
        sa.Column("email", sa.Text, unique=True),
        sa.Column("full_name", sa.Text),
        sa.Column(
            "subscription_status",
            sa.Text,
            nullable=False,
            server_default="none",
        ),
        sa.Column("stripe_customer_id", sa.Text, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_users_email", "users", ["email"])
    op.create_index("idx_users_stripe_customer", "users", ["stripe_customer_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("stripe_subscription_id", sa.Text, unique=True, nullable=False),
        sa.Column("stripe_customer_id", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("plan", sa.Text),
        sa.Column("lookup_key", sa.Text),
        sa.Column("price_id", sa.Text),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("raw", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_subscriptions_user", "subscriptions", ["user_id"]
    )
    op.create_index(
        "idx_subscriptions_customer", "subscriptions", ["stripe_customer_id"]
    )

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Text, unique=True, nullable=False),
        sa.Column("type", sa.Text),
        sa.Column("api_version", sa.Text),
        sa.Column("livemode", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("payload", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text),
    )
    op.create_index("idx_stripe_events_type", "stripe_events", ["type"])
    op.create_index("idx_stripe_events_error", "stripe_events", ["error"])

    op.add_column("orders", sa.Column("user_id", sa.Integer))
    op.create_foreign_key(
        "orders_user_id_fkey", "orders", "users", ["user_id"], ["id"]
    )
    op.add_column("orders", sa.Column("stripe_payment_status", sa.Text))
    op.create_index("idx_orders_user", "orders", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_orders_user", table_name="orders")
    op.drop_column("orders", "stripe_payment_status")
    op.drop_constraint("orders_user_id_fkey", "orders", type_="foreignkey")
    op.drop_column("orders", "user_id")

    op.drop_index("idx_stripe_events_error")
    op.drop_index("idx_stripe_events_type")
    op.drop_table("stripe_events")

    op.drop_index("idx_subscriptions_customer")
    op.drop_index("idx_subscriptions_user")
    op.drop_table("subscriptions")

    op.drop_index("idx_users_stripe_customer")
    op.drop_index("idx_users_email")
    op.drop_table("users")
