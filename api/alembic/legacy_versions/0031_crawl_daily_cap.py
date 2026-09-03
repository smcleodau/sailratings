"""Add a daily hard-stop credit cap to the crawl budget (OPS-02-06).

The existing budget (0025d / ``crawl_budget_settings``) caps spend *per
billing period* with soft/hard fractions. OPS-02-06 adds a **daily** cap so
a runaway discovery loop burns at most N credits in a day and then stops
hard — visible on the admin Firecrawl page (AD-01-08) and enforced by the
``check_throttle`` gate *before* any provider call.

The column is nullable: ``NULL`` means "no daily cap" (backwards-compatible
default for existing rows). Setting ``daily_credit_cap`` to an integer arms
the hard stop.

Idempotent (guards ``has_column``) to stay consistent with the canonical
linear-chain convention used across this repo's migrations.

Revision ID: 0030
Revises: 20260905a
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031"
down_revision = "20260905a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("crawl_budget_settings")}
    if "daily_credit_cap" not in cols:
        op.add_column(
            "crawl_budget_settings",
            sa.Column("daily_credit_cap", sa.Integer, nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("crawl_budget_settings")}
    if "daily_credit_cap" in cols:
        op.drop_column("crawl_budget_settings", "daily_credit_cap")
