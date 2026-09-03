"""OPS-01/OPS-02 infra + AUTH-01-03 user_settings — reconciliation (SR-migrations)

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-03

This database has a second, unmerged migration lineage (task worktrees
building on a `feature/3cc37ffe-...` branch: schedule registry, watchdog,
crawl budget, admin metrics, data incidents, ORC materialised views,
v1/health views, plus AUTH-01-03's user_settings) whose objects were applied
directly to this shared dev database outside of any migration that ever
landed on `develop`. Every attempt to merge that branch's ~15 divergent,
internally-duplicated revision files (two different files each claim
revision "0026" and "0029") crash-looped the shared repo and corrupted the
live API. See the session notes for the incident.

Rather than reconstruct that branch's tangled history, this migration
captures the live schema for every object that exists on `irc_data` but
isn't described by any file in this directory — via `pg_dump --schema-only`
against the running database, so the DDL below is verified-accurate, not
reconstructed from intent. Every statement is idempotent (IF NOT EXISTS /
OR REPLACE) so this is a safe no-op on the database it was captured from,
and a real forward migration on a fresh build or another environment.

Deliberately NOT reconciled here: `0027_payments_auth`'s UUID-typed
users/subscriptions/stripe_events declarations were already corrected to
match live (integer-PK) reality in models.py this session; the file itself
still describes UUID types for a *fresh* build, which only matters for a
database that has never run PAY-01-09's integer-PK creation — i.e. never,
in practice, since it predates this file. Left as a known follow-up rather
than rewritten here to keep this migration scoped to genuinely new objects.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- OPS-01: source scheduling + run ledger --------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            id              BIGSERIAL PRIMARY KEY,
            source_slug     TEXT NOT NULL,
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
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_runs_slug ON source_runs (source_slug)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_runs_started_at ON source_runs (started_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_runs_status ON source_runs (status)"
    )
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'source_runs_source_slug_fkey'
            ) THEN
                ALTER TABLE source_runs ADD CONSTRAINT source_runs_source_slug_fkey
                    FOREIGN KEY (source_slug) REFERENCES data_sources(slug) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_schedule_state (
            id              BIGSERIAL PRIMARY KEY,
            source_slug     TEXT NOT NULL,
            schedule_id     TEXT NOT NULL,
            cadence         TEXT NOT NULL,
            paused          BOOLEAN NOT NULL DEFAULT false,
            notes           TEXT,
            last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_source_schedule_state_slug UNIQUE (source_slug)
        )
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'source_schedule_state_source_slug_fkey'
            ) THEN
                ALTER TABLE source_schedule_state ADD CONSTRAINT source_schedule_state_source_slug_fkey
                    FOREIGN KEY (source_slug) REFERENCES data_sources(slug) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )

    # --- OPS-01: watchdog alerting -----------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_alerts (
            id              BIGSERIAL PRIMARY KEY,
            alert_key       TEXT NOT NULL,
            source          TEXT NOT NULL,
            signal          TEXT NOT NULL,
            label           TEXT,
            cadence         TEXT,
            reason          TEXT,
            age_hours       DOUBLE PRECISION,
            budget_hours    DOUBLE PRECISION,
            status          TEXT NOT NULL DEFAULT 'active',
            first_seen_at   TIMESTAMPTZ NOT NULL,
            alerted_at      TIMESTAMPTZ NOT NULL,
            cooldown_until  TIMESTAMPTZ,
            recovered_at    TIMESTAMPTZ,
            details         TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_watchdog_alerts_source ON watchdog_alerts (source)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_watchdog_alerts_status ON watchdog_alerts (status)"
    )

    # --- OPS-02: crawl budget -----------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS crawl_budget_settings (
            id                BIGSERIAL PRIMARY KEY,
            provider          TEXT NOT NULL UNIQUE,
            period_credits    INTEGER NOT NULL,
            soft_cap_frac     DOUBLE PRECISION NOT NULL DEFAULT 0.8,
            hard_cap_frac     DOUBLE PRECISION NOT NULL DEFAULT 0.95,
            period_start      TIMESTAMPTZ NOT NULL DEFAULT to_timestamp(to_char(now(), 'YYYY-MM-01'), 'YYYY-MM-DD'),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            daily_credit_cap  INTEGER
        )
        """
    )

    # --- OPS-02: data-quality incidents -------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_incidents (
            id                   SERIAL PRIMARY KEY,
            incident_id          TEXT NOT NULL UNIQUE,
            kind                 TEXT NOT NULL,
            severity             TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'open',
            source_slug          TEXT,
            dataset              TEXT,
            title                TEXT NOT NULL,
            summary              TEXT,
            detected_at          TIMESTAMPTZ NOT NULL,
            acknowledged_at      TIMESTAMPTZ,
            resolved_at          TIMESTAMPTZ,
            owner                JSONB,
            acknowledged_by      TEXT,
            affected_batches     JSONB,
            affected_consumers   JSONB,
            evidence             JSONB,
            recommended_action   JSONB,
            alert_sent_at        TIMESTAMPTZ,
            notes                JSONB,
            schema_version       TEXT DEFAULT 'v1',
            created_at           TIMESTAMPTZ DEFAULT now(),
            updated_at           TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_data_incidents_source ON data_incidents (source_slug, detected_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_data_incidents_status ON data_incidents (status, detected_at)"
    )

    # --- OPS-02: admin metrics ledger + aliasing trigger --------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_metrics (
            id            BIGSERIAL PRIMARY KEY,
            recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            metric        TEXT NOT NULL,
            scope         TEXT NOT NULL DEFAULT '',
            phase         TEXT NOT NULL DEFAULT '',
            value_num     DOUBLE PRECISION,
            value_text    TEXT,
            meta          JSONB,
            computed_at   TIMESTAMPTZ,
            value         DOUBLE PRECISION
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric ON admin_metrics (metric)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric_computed_at ON admin_metrics (metric, computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric_scope ON admin_metrics (metric, scope, phase, recorded_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admin_metrics_recorded_at ON admin_metrics (recorded_at)"
    )
    # `value`/`value_num` and `recorded_at`/`computed_at` are each the same
    # fact under two historical names; the trigger keeps both populated so
    # neither naming convention silently reads NULL.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION admin_metrics_sync_aliases() RETURNS trigger
            LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.computed_at IS NULL THEN
                NEW.computed_at := COALESCE(NEW.recorded_at, now());
            END IF;
            IF NEW.recorded_at IS NULL THEN
                NEW.recorded_at := NEW.computed_at;
            END IF;
            IF NEW.value IS NULL THEN
                NEW.value := NEW.value_num;
            END IF;
            IF NEW.value_num IS NULL THEN
                NEW.value_num := NEW.value;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger WHERE tgname = 'trg_admin_metrics_sync_aliases'
            ) THEN
                CREATE TRIGGER trg_admin_metrics_sync_aliases
                    BEFORE INSERT ON admin_metrics
                    FOR EACH ROW EXECUTE FUNCTION admin_metrics_sync_aliases();
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW health_metric_latest AS
        SELECT DISTINCT ON (metric, scope, phase)
            metric, scope, phase, value_num AS value, value_text, computed_at, meta
        FROM admin_metrics
        ORDER BY metric, scope, phase, computed_at DESC
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW health_tables_built_never_written AS
        SELECT
            s.relname AS table_name,
            s.n_live_tup AS est_rows,
            pg_total_relation_size(s.relid::regclass) AS total_bytes,
            c.n_data_cols AS data_cols
        FROM pg_stat_user_tables s
        JOIN LATERAL (
            SELECT count(*) AS n_data_cols
            FROM pg_attribute a
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = s.relid AND a.attnum > 0 AND NOT a.attisdropped
              AND a.attname <> 'id'
              AND NOT (
                  pg_get_expr(d.adbin, d.adrelid) IS NOT NULL
                  AND pg_get_expr(d.adbin, d.adrelid) ~ 'now\\(\\)|clock_timestamp|CURRENT_TIMESTAMP'
              )
        ) c ON true
        WHERE s.n_tup_ins = 0 AND s.n_tup_upd = 0 AND s.n_tup_del = 0
          AND s.n_tup_hot_upd = 0 AND s.n_live_tup = 0
          AND c.n_data_cols >= 1
          AND pg_total_relation_size(s.relid::regclass) > 0
        ORDER BY s.relname
        """
    )

    # --- OPS-02: ORC materialised views (unpopulated until first refresh) --
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orc_country_fleet AS
        WITH latest AS (
            SELECT *
            FROM orc_certificates
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
        )
        SELECT
            country_id AS country,
            count(*) AS cert_count,
            count(gph) AS n_with_gph,
            count(cdl) AS n_with_cdl,
            count(allowances) AS n_with_allowances,
            avg(gph)::numeric(10,2) AS avg_gph,
            min(gph)::numeric(10,2) AS min_gph,
            max(gph)::numeric(10,2) AS max_gph,
            avg(cdl)::numeric(8,3) AS avg_cdl,
            count(DISTINCT class_name) AS design_count
        FROM latest
        WHERE country_id IS NOT NULL AND country_id <> ''
        GROUP BY country_id
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_orc_country_fleet_pk ON mv_orc_country_fleet (country)"
    )
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orc_design_stats AS
        WITH latest AS (
            SELECT *
            FROM orc_certificates
            WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
        )
        SELECT
            class_name AS design_name,
            count(*) AS fleet_size,
            count(gph) AS n_with_gph,
            count(cdl) AS n_with_cdl,
            count(allowances) AS n_with_allowances,
            avg(gph)::numeric(10,2) AS mean_gph,
            min(gph)::numeric(10,2) AS min_gph,
            max(gph)::numeric(10,2) AS max_gph,
            avg(cdl)::numeric(8,3) AS mean_cdl,
            avg(triple_low)::numeric(10,2) AS mean_triple_low,
            avg(triple_med)::numeric(10,2) AS mean_triple_med,
            avg(triple_high)::numeric(10,2) AS mean_triple_high,
            avg(loa)::numeric(8,3) AS mean_loa,
            avg(displacement)::numeric(10,1) AS mean_displacement,
            avg(sail_area_upwind)::numeric(8,2) AS mean_sail_area_upwind,
            count(DISTINCT country_id) AS country_count
        FROM latest
        WHERE class_name IS NOT NULL AND class_name <> ''
        GROUP BY class_name
        HAVING count(*) >= 1
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_orc_design_stats_name ON mv_orc_design_stats (design_name)"
    )

    # --- DP-03-05: v1 stable read-model views -------------------------------
    op.execute(
        """
        CREATE OR REPLACE VIEW v1_boat_ratings AS
        SELECT
            b.id AS boat_id, b.boat_name, b.sail_number, b.cert_number,
            b.design, b.country, b.year_built,
            t.snapshot_date AS rating_date, t.tcc, t.non_spi_tcc, t.endorsed
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT snapshot_date, tcc, non_spi_tcc, endorsed
            FROM tcc_snapshots s
            WHERE s.boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v1_fact_assertions_current AS
        SELECT
            assertion_id, entity_type, entity_key, field, value_json, unit,
            valid_from, valid_to, recorded_at, source_slug, confidence
        FROM fact_assertions a
        WHERE status::text = 'active' AND superseded_by IS NULL
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v1_race_results AS
        SELECT
            r.id AS race_result_id, e.name AS event_name, e.start_date AS event_date,
            r.race_name, r.race_number, r.division, r.class_name, r.place,
            r.status, r.rating_value, r.tcc_at_race, r.elapsed_time, r.corrected_time,
            b.id AS boat_id, b.boat_name, b.sail_number, b.design, b.country
        FROM race_results r
        JOIN event_entries ee ON ee.id = r.event_entry_id
        LEFT JOIN events e ON e.id = ee.event_id
        LEFT JOIN boats b ON b.id = ee.boat_id
        """
    )

    # --- AUTH-01-03: account settings ---------------------------------------
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_requested_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_completed_at TIMESTAMPTZ"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id                  BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            display_name             TEXT,
            home_club                TEXT,
            country                  TEXT,
            notify_product_updates   BOOLEAN NOT NULL DEFAULT false,
            notify_rating_changes    BOOLEAN NOT NULL DEFAULT false,
            notify_event_reminders   BOOLEAN NOT NULL DEFAULT false,
            notify_marketing         BOOLEAN NOT NULL DEFAULT false,
            created_at               TIMESTAMPTZ DEFAULT now(),
            updated_at               TIMESTAMPTZ DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_settings")
    op.execute("DROP VIEW IF EXISTS v1_race_results")
    op.execute("DROP VIEW IF EXISTS v1_fact_assertions_current")
    op.execute("DROP VIEW IF EXISTS v1_boat_ratings")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_orc_design_stats")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_orc_country_fleet")
    op.execute("DROP VIEW IF EXISTS health_tables_built_never_written")
    op.execute("DROP VIEW IF EXISTS health_metric_latest")
    op.execute("DROP TRIGGER IF EXISTS trg_admin_metrics_sync_aliases ON admin_metrics")
    op.execute("DROP FUNCTION IF EXISTS admin_metrics_sync_aliases()")
    op.execute("DROP TABLE IF EXISTS admin_metrics")
    op.execute("DROP TABLE IF EXISTS data_incidents")
    op.execute("DROP TABLE IF EXISTS crawl_budget_settings")
    op.execute("DROP TABLE IF EXISTS watchdog_alerts")
    op.execute("DROP TABLE IF EXISTS source_schedule_state")
    op.execute("DROP TABLE IF EXISTS source_runs")
