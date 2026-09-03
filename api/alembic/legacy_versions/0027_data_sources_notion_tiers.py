"""DP-01-01 — carry Notion Data Source Register tiers onto data_sources.

Adds the three Notion-lineage columns to the governed register so the
Priority Tier, Current Status and License Status of every one of the 30
seeded sources survive the move from Notion into Postgres:

* ``tier``           — Notion "Priority Tier" (e.g. "Tier 1: Core Identifiers")
* ``notion_status``  — Notion "Current Status" (Active|Prototyped|Unexplored|Broken)
* ``notion_license`` — Notion "License Status" (Public Domain|TOS Restricted|…)

The ``adapter_status`` column already exists; the DP-01-01 seed data maps
Notion "Current Status" onto it (Active→active, Prototyped→prototyped,
Unexplored→unexplored, Broken→broken) so the register's health vocabulary
matches Notion 1:1.

Idempotent: ``ADD COLUMN IF NOT EXISTS``.  Re-seeding via
``irc_data.sources.registry.seed_sources(engine)`` upserts the full 30-row
register (tiers included) on top of these columns.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
#
# Like earlier revisions in this graph, multiple files may declare "0026";
# the migration is idempotent and applied via the documented
# stamp / direct-execute path.
revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS tier TEXT")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS notion_status TEXT")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS notion_license TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_sources_tier ON data_sources (tier)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_sources_adapter_status "
        "ON data_sources (adapter_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_data_sources_adapter_status")
    op.execute("DROP INDEX IF EXISTS idx_data_sources_tier")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS notion_license")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS notion_status")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS tier")
