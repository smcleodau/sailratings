"""add analytics materialized views

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. mv_within_class_stats — per-design measurement means/stddevs
    #    from both certificates AND tcc_snapshots for z-score computation
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_within_class_stats AS
    WITH latest_snapshot AS (
        SELECT DISTINCT ON (boat_id)
            boat_id, tcc, non_spi_tcc, lh, beam, draft, crew, dlr,
            headsails, flying_headsails, spinnakers
        FROM tcc_snapshots
        ORDER BY boat_id, snapshot_date DESC
    ),
    boat_design AS (
        SELECT b.id AS boat_id,
               COALESCE(b.design_canonical, b.design) AS design_name
        FROM boats b
        WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
    )
    SELECT
        bd.design_name,
        COUNT(*) AS n_boats,
        -- TCC stats
        AVG(ls.tcc)::numeric(8,5) AS mean_tcc,
        STDDEV(ls.tcc)::numeric(8,5) AS std_tcc,
        MIN(ls.tcc)::numeric(8,5) AS min_tcc,
        MAX(ls.tcc)::numeric(8,5) AS max_tcc,
        -- Snapshot dimension stats
        AVG(ls.lh)::numeric(8,3) AS mean_lh,
        STDDEV(ls.lh)::numeric(8,3) AS std_lh,
        AVG(ls.beam)::numeric(8,3) AS mean_beam,
        STDDEV(ls.beam)::numeric(8,3) AS std_beam,
        AVG(ls.draft)::numeric(8,3) AS mean_draft,
        STDDEV(ls.draft)::numeric(8,3) AS std_draft,
        AVG(ls.crew)::numeric(6,2) AS mean_crew,
        AVG(ls.dlr)::numeric(8,1) AS mean_dlr,
        AVG(ls.headsails)::numeric(4,2) AS mean_headsails,
        AVG(ls.spinnakers)::numeric(4,2) AS mean_spinnakers,
        -- Certificate measurement stats (joined where available)
        COUNT(c.id) AS n_with_certs,
        AVG(c.displacement)::numeric(10,1) AS mean_displacement,
        STDDEV(c.displacement)::numeric(10,1) AS std_displacement,
        AVG(c.p)::numeric(6,2) AS mean_p,
        STDDEV(c.p)::numeric(6,2) AS std_p,
        AVG(c.e)::numeric(6,2) AS mean_e,
        STDDEV(c.e)::numeric(6,2) AS std_e,
        AVG(c.j)::numeric(6,2) AS mean_j,
        STDDEV(c.j)::numeric(6,2) AS std_j,
        AVG(c.hlu)::numeric(6,2) AS mean_hlu,
        STDDEV(c.hlu)::numeric(6,2) AS std_hlu,
        AVG(c.hlp)::numeric(6,2) AS mean_hlp,
        STDDEV(c.hlp)::numeric(6,2) AS std_hlp,
        AVG(c.muw)::numeric(6,2) AS mean_muw,
        STDDEV(c.muw)::numeric(6,2) AS std_muw,
        AVG(c.mhw)::numeric(6,2) AS mean_mhw,
        STDDEV(c.mhw)::numeric(6,2) AS std_mhw,
        AVG(c.stl)::numeric(6,2) AS mean_stl,
        STDDEV(c.stl)::numeric(6,2) AS std_stl,
        AVG(c.sym_slu)::numeric(6,2) AS mean_sym_slu,
        STDDEV(c.sym_slu)::numeric(6,2) AS std_sym_slu,
        AVG(c.sym_sf)::numeric(6,2) AS mean_sym_sf,
        STDDEV(c.sym_sf)::numeric(6,2) AS std_sym_sf
    FROM boat_design bd
    JOIN latest_snapshot ls ON ls.boat_id = bd.boat_id
    LEFT JOIN LATERAL (
        SELECT * FROM certificates
        WHERE boat_id = bd.boat_id
        ORDER BY issue_date DESC NULLS LAST
        LIMIT 1
    ) c ON true
    GROUP BY bd.design_name
    HAVING COUNT(*) >= 2
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_within_class_stats_design
    ON mv_within_class_stats (design_name)
    """)

    # ---------------------------------------------------------------
    # 2. mv_boat_performance_summary — pre-computed per-boat racing stats
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_boat_performance_summary AS
    WITH race_stats AS (
        SELECT
            r.boat_id,
            COUNT(*) FILTER (WHERE r.status = 'finished') AS finished_races,
            COUNT(*) FILTER (WHERE r.status = 'finished' AND r.place = 1) AS wins,
            COUNT(*) FILTER (WHERE r.status = 'finished' AND r.place <= 3) AS podiums,
            AVG(r.place::float / NULLIF(r.fleet_size, 0))
                FILTER (WHERE r.status = 'finished' AND r.place IS NOT NULL AND r.fleet_size > 0)
                AS avg_finish_pct,
            MIN(COALESCE(r.race_date_specific, r.event_date)) AS first_race,
            MAX(COALESCE(r.race_date_specific, r.event_date)) AS last_race,
            COUNT(DISTINCT r.event_name) AS distinct_events
        FROM race_results r
        WHERE r.boat_id IS NOT NULL
        GROUP BY r.boat_id
    )
    SELECT
        rs.boat_id,
        rs.finished_races,
        rs.wins,
        rs.podiums,
        rs.avg_finish_pct::numeric(5,4),
        rs.first_race,
        rs.last_race,
        rs.distinct_events
    FROM race_stats rs
    WHERE rs.finished_races > 0
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_boat_perf_boat_id
    ON mv_boat_performance_summary (boat_id)
    """)

    # ---------------------------------------------------------------
    # 3. mv_tcc_drift — consecutive-snapshot TCC changes with
    #    measurement stability flags for drift analysis
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_tcc_drift AS
    WITH ordered_snapshots AS (
        SELECT
            t.boat_id,
            t.snapshot_date,
            t.tcc,
            t.lh, t.beam, t.draft, t.headsails, t.spinnakers, t.crew,
            b.design,
            COALESCE(b.design_canonical, b.design) AS design_name,
            b.country,
            LAG(t.snapshot_date) OVER w AS prev_date,
            LAG(t.tcc) OVER w AS prev_tcc,
            LAG(t.lh) OVER w AS prev_lh,
            LAG(t.beam) OVER w AS prev_beam,
            LAG(t.draft) OVER w AS prev_draft,
            LAG(t.headsails) OVER w AS prev_headsails,
            LAG(t.spinnakers) OVER w AS prev_spinnakers
        FROM tcc_snapshots t
        JOIN boats b ON b.id = t.boat_id
        WINDOW w AS (PARTITION BY t.boat_id ORDER BY t.snapshot_date)
    )
    SELECT
        boat_id,
        prev_date AS date_from,
        snapshot_date AS date_to,
        prev_tcc AS tcc_from,
        tcc AS tcc_to,
        (tcc - prev_tcc)::numeric(8,5) AS tcc_delta,
        design_name,
        country,
        -- Stability flag: true if none of the key measurements changed
        (COALESCE(lh = prev_lh, true)
         AND COALESCE(beam = prev_beam, true)
         AND COALESCE(draft = prev_draft, true)
         AND COALESCE(headsails = prev_headsails, true)
         AND COALESCE(spinnakers = prev_spinnakers, true)
        ) AS measurements_stable,
        -- Individual deltas for decomposition
        (lh - prev_lh)::numeric(6,3) AS delta_lh,
        (beam - prev_beam)::numeric(6,3) AS delta_beam,
        (draft - prev_draft)::numeric(6,3) AS delta_draft,
        (headsails - prev_headsails) AS delta_headsails,
        (spinnakers - prev_spinnakers) AS delta_spinnakers
    FROM ordered_snapshots
    WHERE prev_tcc IS NOT NULL
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_tcc_drift_pk
    ON mv_tcc_drift (boat_id, date_from, date_to)
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_mv_tcc_drift_stable
    ON mv_tcc_drift (measurements_stable)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_tcc_drift")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_boat_performance_summary")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_within_class_stats")
