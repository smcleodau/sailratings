"""Add orders.stripe_customer_id (PAY-01-08).

The checkout session's Stripe customer is recorded on the order when the
payment completes so purchases are auditable per customer and the guest
purchase can be claimed by the user on their next sign-in. The canonical
``users`` table (``clerk_id`` / ``stripe_customer_id``) is created by
revision ``0032`` (PAY-01-07/09 schema).

Idempotent (guards ``has_column`` / index existence) to stay consistent
with the canonical linear-chain convention used across this repo's
migrations.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "stripe_customer_id" not in cols:
        op.add_column("orders", sa.Column("stripe_customer_id", sa.Text, nullable=True))

    insp = sa.inspect(bind)
    indexes = {ix["name"] for ix in insp.get_indexes("orders")}
    if "idx_orders_stripe_customer_id" not in indexes:
        op.create_index(
            "idx_orders_stripe_customer_id", "orders", ["stripe_customer_id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = {ix["name"] for ix in insp.get_indexes("orders")}
    if "idx_orders_stripe_customer_id" in indexes:
        op.drop_index("idx_orders_stripe_customer_id", table_name="orders")

    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("orders")}
    if "stripe_customer_id" in cols:
        op.drop_column("orders", "stripe_customer_id")
