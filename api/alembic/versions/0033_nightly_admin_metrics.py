"""AD-01-15 — nightly ``admin_metrics`` data-health support.

Three deliverables, one revision (all idempotent, all cheap):

1. ``admin_metrics`` spec-alias columns
   Revision ``0029`` created the durable ops-evidence store with
   ``recorded_at`` / ``value_num`` / ``value_text``.  AD-01-15's schema
   contract names the columns ``(metric text, value numeric, computed_at
   timestamptz)``.  Rather than migrate the existing evidence stream, we add
   the two spec-named aliases as real columns and keep them in lock-step via
   a BEFORE INSERT trigger, so both reader conventions work:

       computed_at  ← COALESCE(NEW.computed_at, NEW.recorded_at, now())
       recorded_at  ← COALESCE(NEW.recorded_at, NEW.computed_at, now())
       value        ← COALESCE(NEW.value, NEW.value_num)
       value_num    ← COALESCE(NEW.value_num, NEW.value)

2. ``health_metric_latest`` view
   The page's completeness meter must not scan the (append-only)
   ``admin_metrics`` stream on request: the view projects the latest row per
   ``(metric, scope, phase)``.  Reads are over a stream that grows by ~15
   rows/night, so even the un-materialised view is trivially cheap, and it
   can be swapped for a materialised view later without touching the API.

3. ``health_tables_built_never_written`` view
   "Built but never written" = a user table that was created with at least
   one genuine data column (so it was *meant* to hold rows — a surrogate
   ``id`` plus audit ``created_at``/``updated_at`` clocks alone don't count)
   while ``pg_stat_user_tables`` has recorded **zero writes of any kind**
   (insert/update/delete, hot or not) and the table estimates 0 live rows.
   These are tables a migration built and nothing ever populated.  Purely
   additive: a table that starts receiving rows falls out of the list
   automatically, and a table with rows is never listed.

4. Opportunistic ``ANALYZE`` on the small set of user tables so
   ``pg_stat_user_tables.n_live_tup`` estimates are fresh for the
   ``/v1/admin/health/tables`` census right after a fresh migration run
   (pg_stat counts are what the page must match).

Revision ID: 0033
Revises: 0030, 0032  (merge of the two current heads)
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = ("0030", "0032")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# 1. spec-alias columns + lock-step trigger
# ---------------------------------------------------------------------------

_ADD_ALIAS_COLUMNS = """
ALTER TABLE admin_metrics
    ADD COLUMN IF NOT EXISTS computed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS value       DOUBLE PRECISION
"""

_SYNC_FUNCTION = """
CREATE OR REPLACE FUNCTION admin_metrics_sync_aliases() RETURNS trigger AS $$
BEGIN
    -- computed_at / recorded_at are the same instant under two names.
    IF NEW.computed_at IS NULL THEN
        NEW.computed_at := COALESCE(NEW.recorded_at, now());
    END IF;
    IF NEW.recorded_at IS NULL THEN
        NEW.recorded_at := NEW.computed_at;
    END IF;

    -- value / value_num are the same number under two names.
    IF NEW.value IS NULL THEN
        NEW.value := NEW.value_num;
    END IF;
    IF NEW.value_num IS NULL THEN
        NEW.value_num := NEW.value;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""

_TRIGGER = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_admin_metrics_sync_aliases'
          AND tgrelid = 'admin_metrics'::regclass
    ) THEN
        CREATE TRIGGER trg_admin_metrics_sync_aliases
        BEFORE INSERT ON admin_metrics
        FOR EACH ROW EXECUTE FUNCTION admin_metrics_sync_aliases();
    END IF;
END
$$
"""

# Backfill alias columns for any rows written before this revision.
_BACKFILL = """
UPDATE admin_metrics
SET computed_at = COALESCE(computed_at, recorded_at),
    value       = COALESCE(value, value_num)
WHERE computed_at IS NULL OR value IS NULL
"""

_ALIAS_INDEX = """
CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric_computed_at
    ON admin_metrics (metric, computed_at DESC)
"""


# ---------------------------------------------------------------------------
# 2. latest-row projection (what the page reads)
# ---------------------------------------------------------------------------

_LATEST_VIEW = """
CREATE OR REPLACE VIEW health_metric_latest AS
SELECT DISTINCT ON (metric, scope, phase)
    metric,
    scope,
    phase,
    value_num  AS value,
    value_text,
    computed_at,
    meta
FROM admin_metrics
ORDER BY metric, scope, phase, computed_at DESC
"""


# ---------------------------------------------------------------------------
# 3. built-never-written census view
# ---------------------------------------------------------------------------

_BUILT_NEVER_WRITTEN_VIEW = """
CREATE OR REPLACE VIEW health_tables_built_never_written AS
SELECT
    s.relname                                        AS table_name,
    s.n_live_tup                                     AS est_rows,
    pg_total_relation_size(s.relid)                  AS total_bytes,
    c.n_data_cols                                    AS data_cols
FROM pg_stat_user_tables s
JOIN LATERAL (
    -- count of real data columns: not the synthetic key, not an audit
    -- clock.  A table "built to hold rows" has at least one of these.
    SELECT COUNT(*) AS n_data_cols
    FROM pg_attribute a
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE a.attrelid = s.relid
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND a.attname <> 'id'
      -- exclude pure audit timestamps (created_at / updated_at with a
      -- clock default): they are scaffolding, not data.  A column with no
      -- default at all (pg_get_expr IS NULL) is a data column.
      AND NOT (
          pg_get_expr(d.adbin, d.adrelid) IS NOT NULL
          AND pg_get_expr(d.adbin, d.adrelid) ~ 'now\\(\\)|clock_timestamp|CURRENT_TIMESTAMP'
      )
) c ON true
WHERE s.n_tup_ins = 0
  AND s.n_tup_upd = 0
  AND s.n_tup_del = 0
  AND s.n_tup_hot_upd = 0
  AND s.n_live_tup = 0
  -- the table was built with at least one genuine data column …
  AND c.n_data_cols >= 1
  -- … and it's still empty after that investment.  (The system catalog
  --   tables of alembic itself are excluded by pg_stat_user_tables.)
  AND pg_total_relation_size(s.relid) > 0
ORDER BY s.relname
"""


# ---------------------------------------------------------------------------
# 4. fresh pg_stat estimates for the census
# ---------------------------------------------------------------------------

_ANALYZE = """
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT relname FROM pg_stat_user_tables LOOP
        EXECUTE format('ANALYZE %I', r.relname);
    END LOOP;
END
$$
"""


def upgrade() -> None:
    op.execute(_ADD_ALIAS_COLUMNS)
    op.execute(_SYNC_FUNCTION)
    op.execute(_TRIGGER)
    op.execute(_BACKFILL)
    op.execute(_ALIAS_INDEX)
    op.execute(_LATEST_VIEW)
    op.execute(_BUILT_NEVER_WRITTEN_VIEW)
    # Keep the census estimates honest for /v1/admin/health/tables.
    op.execute(_ANALYZE)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS health_tables_built_never_written")
    op.execute("DROP VIEW IF EXISTS health_metric_latest")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_admin_metrics_sync_aliases ON admin_metrics"
    )
    op.execute("DROP FUNCTION IF EXISTS admin_metrics_sync_aliases")
    op.execute("DROP INDEX IF EXISTS ix_admin_metrics_metric_computed_at")
    op.execute("ALTER TABLE admin_metrics DROP COLUMN IF EXISTS value")
    op.execute("ALTER TABLE admin_metrics DROP COLUMN IF EXISTS computed_at")
