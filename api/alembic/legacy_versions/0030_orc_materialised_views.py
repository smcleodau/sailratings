"""OPS-02-10 — ORC in materialised views.

Two new materialised views expose the drained ORC VPP detail (GPH, CDL,
allowances, triple numbers) to the analytics surface so that dual-rated
pages and the design comparator have ORC numbers to stand on:

1. ``mv_orc_design_stats``
   Per ORC class/design aggregates over the *latest* ORC snapshot only
   (certificates are re-snapshotted; older snapshots would double-count).
   Feeds the design comparator with mean/min/max GPH/CDL/triple numbers.

2. ``mv_orc_country_fleet``
   Per-country ORC fleet stats on the latest snapshot — how many certs,
   GPH/CDL distribution, and how many certs have VPP detail drained.
   Backs the fleet endpoint's ORC stats block.

Both views are created with ``IF NOT EXISTS`` and carry a unique index so
``REFRESH MATERIALIZED VIEW CONCURRENTLY`` works; ``refresh_materialized_views``
in ``db/operations.py`` is updated to include them.

Downgrade drops only the objects this revision owns.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. mv_orc_design_stats — per-class ORC performance aggregates
    #    (latest snapshot only).
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orc_design_stats AS
    WITH latest AS (
        SELECT * FROM orc_certificates
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
    )
    SELECT
        class_name AS design_name,
        COUNT(*) AS fleet_size,
        COUNT(gph) AS n_with_gph,
        COUNT(cdl) AS n_with_cdl,
        COUNT(allowances) AS n_with_allowances,
        AVG(gph)::numeric(10,2) AS mean_gph,
        MIN(gph)::numeric(10,2) AS min_gph,
        MAX(gph)::numeric(10,2) AS max_gph,
        AVG(cdl)::numeric(8,3) AS mean_cdl,
        AVG(triple_low)::numeric(10,2) AS mean_triple_low,
        AVG(triple_med)::numeric(10,2) AS mean_triple_med,
        AVG(triple_high)::numeric(10,2) AS mean_triple_high,
        AVG(loa)::numeric(8,3) AS mean_loa,
        AVG(displacement)::numeric(10,1) AS mean_displacement,
        AVG(sail_area_upwind)::numeric(8,2) AS mean_sail_area_upwind,
        COUNT(DISTINCT country_id) AS country_count
    FROM latest
    WHERE class_name IS NOT NULL AND class_name != ''
    GROUP BY class_name
    HAVING COUNT(*) >= 1
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_orc_design_stats_name
    ON mv_orc_design_stats (design_name)
    """)

    # ---------------------------------------------------------------
    # 2. mv_orc_country_fleet — per-country ORC fleet stats (latest
    #    snapshot), incl. detail-drain coverage.
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_orc_country_fleet AS
    WITH latest AS (
        SELECT * FROM orc_certificates
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
    )
    SELECT
        country_id AS country,
        COUNT(*) AS cert_count,
        COUNT(gph) AS n_with_gph,
        COUNT(cdl) AS n_with_cdl,
        COUNT(allowances) AS n_with_allowances,
        AVG(gph)::numeric(10,2) AS avg_gph,
        MIN(gph)::numeric(10,2) AS min_gph,
        MAX(gph)::numeric(10,2) AS max_gph,
        AVG(cdl)::numeric(8,3) AS avg_cdl,
        COUNT(DISTINCT class_name) AS design_count
    FROM latest
    WHERE country_id IS NOT NULL AND country_id != ''
    GROUP BY country_id
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_orc_country_fleet_pk
    ON mv_orc_country_fleet (country)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_orc_country_fleet")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_orc_design_stats")
