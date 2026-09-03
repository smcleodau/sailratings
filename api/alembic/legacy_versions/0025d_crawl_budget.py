"""Crawl credit-budget settings and throttle-event ledger (OPS-01-05).

Telemetry per call already lives in ``firecrawl_calls`` (0016) — mode, URL,
domain, status, credits, latency, caller. What's missing for "never run out
of crawl budget silently" is:

- ``crawl_budget_settings`` — one row per crawl provider ('firecrawl', …)
  carrying the period credit budget plus soft/hard cap fractions. The
  discovery pipeline checks these before every provider call.
- ``crawl_throttle_events`` — an auditable ledger of every throttle decision
  (allow / warn / soft_block / hard_block). This is the "not silent" half of
  the goal: when the gate starts refusing calls, there is a durable record
  of exactly when it started, at what utilisation, and for which caller.

Canonical chain (DP-03-05): renumbered from the duplicated id ``0025`` to
the unique id ``20260526a`` so the alembic graph is a single linear head.  The
historical ambiguity this file worked around (two files declaring ``0024``)
is now resolved by the renumbering.

Revision ID: 20260526a
Revises: 20260902b
Create Date: 2026-05-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260526a"
down_revision = "20260902b"
branch_labels = None
depends_on = None

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
        "crawl_budget_settings",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("provider", sa.Text, nullable=False, unique=True),
        # Credits allocated per billing period (e.g. monthly plan credits).
        sa.Column("period_credits", sa.Integer, nullable=False),
        # Fraction of period_credits at which new discovery work starts being
        # refused (existing/manual calls still allowed, warning emitted).
        sa.Column("soft_cap_frac", sa.Float, nullable=False, server_default="0.80"),
        # Fraction at which ALL non-manual calls are refused.
        sa.Column("hard_cap_frac", sa.Float, nullable=False, server_default="0.95"),
        # When the current billing period started (spend resets after this).
        # Portable month-truncate rather than date_trunc so the default
        # also works if the migration is ever run on a non-PG backend.
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text(
                      "to_timestamp(to_char(now(), 'YYYY-MM-01'), 'YYYY-MM-DD')")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # Seed the default Firecrawl row (100k plan credits) so the gate works
    # out of the box; admins adjust via SQL / dashboard as plans change.
    op.execute(
        sa.text("""
            INSERT INTO crawl_budget_settings (provider, period_credits)
            VALUES ('firecrawl', 100000)
            ON CONFLICT (provider) DO NOTHING
        """)
    )

    _ctine(
        "crawl_throttle_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        # 'allow' | 'warn' | 'soft_block' | 'hard_block'
        sa.Column("caller", sa.Text),
        sa.Column("mode", sa.Text),          # 'scrape' | 'map' | 'crawl' | None
        sa.Column("url", sa.Text),
        sa.Column("used_credits", sa.Integer),
        sa.Column("soft_cap", sa.Integer),
        sa.Column("hard_cap", sa.Integer),
        sa.Column("utilization", sa.Float),
        sa.Column("message", sa.Text),
    )
    _cidx_if_not_exists("idx_crawl_throttle_created", "crawl_throttle_events", ["created_at"])
    _cidx_if_not_exists("idx_crawl_throttle_action", "crawl_throttle_events", ["action"])


def downgrade() -> None:
    op.drop_index("idx_crawl_throttle_action", table_name="crawl_throttle_events")
    op.drop_index("idx_crawl_throttle_created", table_name="crawl_throttle_events")
    op.drop_table("crawl_throttle_events")
    op.drop_table("crawl_budget_settings")
