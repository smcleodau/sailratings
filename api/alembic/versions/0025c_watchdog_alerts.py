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

Canonical chain (DP-03-05): renumbered from the duplicated id ``0025`` to
the unique id ``20260902b`` so the alembic graph is a single linear head.

Revision ID: 20260902b
Revises: 20260902a
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260902b"
down_revision: Union[str, Sequence[str], None] = "20260902a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MD = None
def _metadata():
    """Lazily-shared MetaData so ForeignKey references resolve across tables
    created in the same migration."""
    global _MD
    if _MD is None:
        from sqlalchemy import MetaData
        _MD = MetaData()
    return _MD


def _ctine(name, *columns, **kwargs):
    """create_table IF NOT EXISTS (idempotent for canonical chain, DP-03-05).

    Builds the table inside the migration's shared MetaData so ForeignKey
    references to other tables resolve correctly."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(name):
        return
    from sqlalchemy import Table
    md = _metadata()
    Table(name, md, *columns, **kwargs)
    md.tables[name].create(bind)


def _cidx_if_not_exists(index_name, table_name, columns, unique=False):
    """create_index IF NOT EXISTS (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes(table_name)}
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, unique=unique)



def upgrade() -> None:
    _ctine(
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
    _cidx_if_not_exists("ix_watchdog_alerts_source", "watchdog_alerts", ["source"])
    _cidx_if_not_exists("ix_watchdog_alerts_status", "watchdog_alerts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_watchdog_alerts_status", table_name="watchdog_alerts")
    op.drop_index("ix_watchdog_alerts_source", table_name="watchdog_alerts")
    op.drop_table("watchdog_alerts")
