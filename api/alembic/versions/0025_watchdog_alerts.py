"""watchdog_alerts — staleness watchdog alert log (OPS-01-04)

Durable alert history for the 15-minute staleness watchdog. One row per
alert incident:

* created when a source first breaches its freshness budget (status
  ``active``) — this is the single alert the cooldown guarantees;
* touched again only if the breach outlives the 4 h cooldown (``alerted_at``
  and ``cooldown_until`` move forward, one re-alert per window);
* closed (``status`` → ``recovered``, ``recovered_at`` set) when the source
  comes back within budget.

Rows are never deleted — the table *is* the retained alert history.
The admin banner ("Cron health: N sources not running") is driven from the
supervision config; the active rows here mirror exactly what the watchdog
has emailed about.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchdog_alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # "<source>" for run-health breaches, "<source>:data" for data-tap.
        sa.Column("alert_key", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("signal", sa.Text(), nullable=False),  # 'run' | 'data'
        sa.Column("label", sa.Text()),
        sa.Column("cadence", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("age_hours", sa.Double()),
        sa.Column("budget_hours", sa.Double()),
        # 'active' while the breach stands; 'recovered' once cleared.
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("details", sa.Text()),  # JSON blob for future context
    )
    op.create_index(
        "ix_watchdog_alerts_source", "watchdog_alerts", ["source"]
    )
    op.create_index(
        "ix_watchdog_alerts_status", "watchdog_alerts", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_watchdog_alerts_status", table_name="watchdog_alerts")
    op.drop_index("ix_watchdog_alerts_source", table_name="watchdog_alerts")
    op.drop_table("watchdog_alerts")
