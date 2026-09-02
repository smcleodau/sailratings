"""OPS-01-02 — schedule registry + run ledger driven by the Data Source Register.

Creates (idempotently):

  * ``source_runs``           — the run ledger. One row per scheduled/ad-hoc
                                source collection run. Keyed by
                                (source_slug, run_key) for idempotency.
  * ``source_schedule_state`` — DB-side mirror of the Temporal schedule state
                                (handy when Temporal is unreachable in dev).
  * Adds ``cadence`` and ``adapter_status`` columns to ``data_sources`` when
    missing so the register can drive schedule cadence directly.

The Temporal schedule itself is the authoritative runtime object; these
tables are the ledger / reconciliation state that satisfies the OPS-01-02
acceptance criteria ("run → ledger row").

The migration is written to be *idempotent* (``CREATE TABLE IF NOT EXISTS``,
``ADD COLUMN IF NOT EXISTS``) because the repo's 0023/0024 revision ids form
a multi-branch tangle that prevents a clean ``alembic upgrade`` walk; this
file can be applied directly via ``python -m alembic`` *or* executed
standalone.

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


_SQL = """
-- data_sources: bring the register up to the full DP-01-04 contract shape
-- (idempotent — the live dev DB predates some of these columns).
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'data-platform';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS geography TEXT NOT NULL DEFAULT 'GLOBAL';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS terms_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS robots_status TEXT NOT NULL DEFAULT 'unchecked';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS licensing TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS access_method TEXT NOT NULL DEFAULT 'html_scrape';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS format TEXT NOT NULL DEFAULT 'html';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS change_detection TEXT NOT NULL DEFAULT 'content_hash';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 3;
-- cadence / adapter_status drive the schedule
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS cadence TEXT NOT NULL DEFAULT 'nightly';
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS adapter_status TEXT NOT NULL DEFAULT 'planned';

-- widen legal_status check to allow 'unknown'
ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_legal_status_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_legal_status_check
    CHECK (legal_status IN ('approved', 'hold', 'blocked', 'unknown'));

-- run ledger
CREATE TABLE IF NOT EXISTS source_runs (
    id              BIGSERIAL PRIMARY KEY,
    source_slug     TEXT NOT NULL REFERENCES data_sources(slug) ON DELETE CASCADE,
    run_key         TEXT NOT NULL,
    trigger         TEXT NOT NULL DEFAULT 'schedule',
    schedule_id     TEXT,
    workflow_id     TEXT,
    workflow_run_id TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    detail          TEXT,
    stats           JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_runs_slug_key UNIQUE (source_slug, run_key)
);
CREATE INDEX IF NOT EXISTS ix_source_runs_slug       ON source_runs (source_slug);
CREATE INDEX IF NOT EXISTS ix_source_runs_status     ON source_runs (status);
CREATE INDEX IF NOT EXISTS ix_source_runs_started_at ON source_runs (started_at);

-- DB mirror of the Temporal schedule desired state
CREATE TABLE IF NOT EXISTS source_schedule_state (
    id             BIGSERIAL PRIMARY KEY,
    source_slug    TEXT NOT NULL REFERENCES data_sources(slug) ON DELETE CASCADE,
    schedule_id    TEXT NOT NULL,
    cadence        TEXT NOT NULL,
    paused         BOOLEAN NOT NULL DEFAULT false,
    notes          TEXT,
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_schedule_state_slug UNIQUE (source_slug)
);
"""


def upgrade() -> None:
    # Execute each statement individually so the migration is robust and
    # readable; every statement is idempotent.
    for stmt in _SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_schedule_state")
    op.execute("DROP TABLE IF EXISTS source_runs")
