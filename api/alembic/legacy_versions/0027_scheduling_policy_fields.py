"""OPS-01-01 — scheduling policy: per-source cadence/budget register fields.

Implements ``docs/SCHEDULING-POLICY.md`` (``sched-v1.0``): make "how often,
how late is too late" explicit per source.

This migration:

1. Adds the OPS-01-01 scheduling columns to ``data_sources`` (idempotent):
     * ``cadence_class``          — daily_results | weekly_certificates |
                                    annual_identifiers | manual
     * ``staleness_budget_hours`` — "how late is too late" per source
     * ``nightly_window_start`` / ``nightly_window_end`` — per-source nightly
       window, inherited from the collection policy (01:00–06:00)
     * ``retry_policy``           — JSONB {"max_attempts", "backoff_seconds"}
     * ``cooldown_hours``         — alert / re-run cooldown (design: 4 h)
     * ``kill_switch_ack_hours``  — takedown acknowledgement window (4 h)
2. Backfills every register row from the cadence-class design defaults
   (matching ``irc_data.sources.seed_data``), so **every active source has
   values** — the OPS-01-01 acceptance criterion.  Notable per-source values:
     * sailsys       — daily_results, budget 2 h   (30-min published feed)
     * sailing-news  — daily_results, budget 6 h   (hourly RSS)
     * topyacht / irc-tcc / orc — daily_results, budget 30 h (daily crons)
     * irc-certs     — weekly_certificates, budget 192 h (8 d design example)
     * cowesweek / sydney-hobart — annual_identifiers, budget 8880 h (370 d)
     * rorc          — manual, budget 87600 h (decommissioned)
3. Adds CHECK constraints enforcing the controlled vocabularies and sane
   value ranges at the schema level.

Like its predecessors the migration is written idempotently
(``ADD COLUMN IF NOT EXISTS`` / ``DROP CONSTRAINT IF EXISTS``) so it
converges databases that took any historical branch.

Revision ID: 20260903a
Revises: 20260526a
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
#
# NOTE (pre-existing graph defect): two migration files declare the
# duplicate revision id ``0026`` (``0026_policy_v1_rulings.py`` and
# ``0026_canonical_merge_and_compat.py`` — see the "Revision 0026 is present
# more than once" alembic warning and the DP-03-05 canonical-chain tests,
# which already fail on that ambiguity).  Chaining this migration off the
# ambiguous short id ``0026`` makes the script graph un-walkable
# ("overlaps with other requested revisions"), so we chain off the other
# *unique* head, ``20260526a`` (0025d_crawl_budget), exactly as
# ``0026_canonical_merge_and_compat.py`` does.  The policy/rulings
# migration (0026) is a pure UPDATE over rows this migration also UPDATEs,
# so application order between the two is immaterial.
revision: str = "20260903a"
down_revision: Union[str, Sequence[str], None] = "20260526a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQL = """
-- 1. Scheduling-policy columns (OPS-01-01 §3).
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS cadence_class TEXT;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS staleness_budget_hours DOUBLE PRECISION;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS nightly_window_start TEXT;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS nightly_window_end TEXT;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS retry_policy JSONB;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS cooldown_hours DOUBLE PRECISION;
ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS kill_switch_ack_hours INTEGER;

-- 2. Class-default backfill (design defaults, SCHEDULING-POLICY.md §2).
--    nightly window: 01:00–06:00 (collection policy §4.3); cooldown 4 h;
--    kill-switch ack 4 h (SOURCE-POLICY.md §5).

-- annual / manual cadence strings first (they would otherwise match the
-- daily default below)
UPDATE data_sources
SET cadence_class = 'annual_identifiers',
    staleness_budget_hours = 8880.0,
    nightly_window_start = COALESCE(nightly_window_start, '01:00'),
    nightly_window_end   = COALESCE(nightly_window_end,   '06:00'),
    retry_policy  = COALESCE(retry_policy, '{"max_attempts": 1, "backoff_seconds": [86400]}'::jsonb),
    cooldown_hours = COALESCE(cooldown_hours, 24.0),
    kill_switch_ack_hours = COALESCE(kill_switch_ack_hours, 4),
    updated_at = now()
WHERE cadence IN ('annual', 'yearly')
  AND cadence_class IS NULL;

UPDATE data_sources
SET cadence_class = 'manual',
    staleness_budget_hours = 87600.0,
    nightly_window_start = COALESCE(nightly_window_start, '01:00'),
    nightly_window_end   = COALESCE(nightly_window_end,   '06:00'),
    retry_policy  = COALESCE(retry_policy, '{"max_attempts": 1, "backoff_seconds": [86400]}'::jsonb),
    cooldown_hours = COALESCE(cooldown_hours, 24.0),
    kill_switch_ack_hours = COALESCE(kill_switch_ack_hours, 4),
    updated_at = now()
WHERE cadence IN ('manual', 'decommissioned')
  AND cadence_class IS NULL;

UPDATE data_sources
SET cadence_class = 'weekly_certificates',
    staleness_budget_hours = 192.0,   -- 8 d design example (OPS-01-01 scope)
    nightly_window_start = COALESCE(nightly_window_start, '01:00'),
    nightly_window_end   = COALESCE(nightly_window_end,   '06:00'),
    retry_policy  = COALESCE(retry_policy, '{"max_attempts": 3, "backoff_seconds": [3600, 14400, 86400]}'::jsonb),
    cooldown_hours = COALESCE(cooldown_hours, 4.0),
    kill_switch_ack_hours = COALESCE(kill_switch_ack_hours, 4),
    updated_at = now()
WHERE cadence IN ('weekly', '7d', 'fortnightly', '14d')
  AND cadence_class IS NULL;

-- everything else defaults to the daily_results class
UPDATE data_sources
SET cadence_class = 'daily_results',
    staleness_budget_hours = 48.0,
    nightly_window_start = COALESCE(nightly_window_start, '01:00'),
    nightly_window_end   = COALESCE(nightly_window_end,   '06:00'),
    retry_policy  = COALESCE(retry_policy, '{"max_attempts": 3, "backoff_seconds": [600, 1800, 7200]}'::jsonb),
    cooldown_hours = COALESCE(cooldown_hours, 4.0),
    kill_switch_ack_hours = COALESCE(kill_switch_ack_hours, 4),
    updated_at = now()
WHERE cadence_class IS NULL;

-- 3. Named per-source values (mirrors irc_data.sources.seed_data).
--    These override the class defaults where operations already knows
--    "how late is too late" for the source.
UPDATE data_sources SET staleness_budget_hours = 2.0,  updated_at = now() WHERE slug = 'sailsys';
UPDATE data_sources SET staleness_budget_hours = 6.0,  updated_at = now() WHERE slug = 'sailing-news';
UPDATE data_sources SET staleness_budget_hours = 30.0, updated_at = now() WHERE slug IN ('topyacht', 'irc-tcc', 'orc');
UPDATE data_sources SET staleness_budget_hours = 192.0, updated_at = now() WHERE slug IN ('isora', 'rhkyc');
UPDATE data_sources SET cadence_class = 'weekly_certificates', staleness_budget_hours = 192.0, updated_at = now() WHERE slug = 'irc-certs';
UPDATE data_sources SET cadence_class = 'annual_identifiers', staleness_budget_hours = 8880.0, retry_policy = '{"max_attempts": 1, "backoff_seconds": [86400]}'::jsonb, cooldown_hours = 24.0, updated_at = now() WHERE slug IN ('cowesweek', 'sydney-hobart');
UPDATE data_sources SET cadence_class = 'manual', staleness_budget_hours = 87600.0, retry_policy = '{"max_attempts": 1, "backoff_seconds": [86400]}'::jsonb, cooldown_hours = 24.0, updated_at = now() WHERE slug = 'rorc';

-- 4. Schema-level enforcement (controlled vocabularies / sane ranges).
ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_cadence_class_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_cadence_class_check
    CHECK (cadence_class IS NULL OR cadence_class IN
           ('daily_results', 'weekly_certificates', 'annual_identifiers', 'manual'));

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_staleness_budget_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_staleness_budget_check
    CHECK (staleness_budget_hours IS NULL OR staleness_budget_hours > 0);

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_cooldown_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_cooldown_check
    CHECK (cooldown_hours IS NULL OR cooldown_hours > 0);

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_nightly_window_start_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_nightly_window_start_check
    CHECK (nightly_window_start IS NULL OR nightly_window_start ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$');

ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_nightly_window_end_check;
ALTER TABLE data_sources ADD CONSTRAINT data_sources_nightly_window_end_check
    CHECK (nightly_window_end IS NULL OR nightly_window_end ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$');
"""


def upgrade() -> None:
    # Execute each statement individually (idempotent, readable) — same
    # pattern as 20260902a (OPS-01-02).
    for stmt in _SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            op.execute(stmt)


def downgrade() -> None:
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_cadence_class_check")
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_staleness_budget_check")
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_cooldown_check")
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_nightly_window_start_check")
    op.execute("ALTER TABLE data_sources DROP CONSTRAINT IF EXISTS data_sources_nightly_window_end_check")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS kill_switch_ack_hours")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS cooldown_hours")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS retry_policy")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS nightly_window_end")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS nightly_window_start")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS staleness_budget_hours")
    op.execute("ALTER TABLE data_sources DROP COLUMN IF EXISTS cadence_class")
