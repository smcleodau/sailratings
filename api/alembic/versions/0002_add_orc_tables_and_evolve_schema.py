"""add orc tables and evolve schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # 1. Evolve boats table: add new columns
    # ---------------------------------------------------------------
    op.add_column("boats", sa.Column("hull_id", sa.Text))
    op.add_column("boats", sa.Column("builder", sa.Text))
    op.add_column("boats", sa.Column("designer", sa.Text))
    op.add_column("boats", sa.Column("design_canonical", sa.Text))
    op.add_column("boats", sa.Column("current_name", sa.Text))
    op.add_column("boats", sa.Column("current_sail_number", sa.Text))
    op.add_column("boats", sa.Column("current_flag", sa.Text))
    op.add_column("boats", sa.Column("loa", sa.Numeric(6, 2)))
    op.add_column("boats", sa.Column("lwl", sa.Numeric(6, 2)))
    op.add_column("boats", sa.Column("beam_max", sa.Numeric(6, 2)))
    op.add_column("boats", sa.Column("displacement_kg", sa.Numeric(10, 1)))

    op.create_index("idx_boats_design_canonical", "boats", ["design_canonical"])

    # ---------------------------------------------------------------
    # 2. Evolve race_results table: add new columns
    # ---------------------------------------------------------------
    op.add_column("race_results", sa.Column("event_series", sa.Text))
    op.add_column("race_results", sa.Column("organizing_club", sa.Text))
    op.add_column("race_results", sa.Column("event_type", sa.Text))
    op.add_column("race_results", sa.Column("race_name", sa.Text))
    op.add_column("race_results", sa.Column("race_date_specific", sa.Date))
    op.add_column("race_results", sa.Column("race_number", sa.Integer))
    op.add_column("race_results", sa.Column("course_distance_nm", sa.Numeric(8, 2)))
    op.add_column("race_results", sa.Column("fleet_size", sa.Integer))
    op.add_column("race_results", sa.Column("class_name", sa.Text))
    op.add_column("race_results", sa.Column("class_place", sa.Integer))
    op.add_column("race_results", sa.Column("class_fleet_size", sa.Integer))
    op.add_column("race_results", sa.Column("status", sa.Text, server_default="finished"))
    op.add_column("race_results", sa.Column("rating_type", sa.Text))
    op.add_column("race_results", sa.Column("rating_value", sa.Numeric(8, 4)))
    op.add_column("race_results", sa.Column("time_behind_winner", sa.Interval))
    op.add_column("race_results", sa.Column("source", sa.Text))
    op.add_column("race_results", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")))

    # Drop old unique constraint (boat_id, event_name, event_date) and create new one
    # that includes race_name for per-race granularity
    op.drop_constraint("race_results_boat_id_event_name_event_date_key", "race_results", type_="unique")
    op.create_unique_constraint(
        "race_results_boat_event_race_key",
        "race_results",
        ["boat_id", "event_name", "race_name", "event_date"],
    )

    op.create_index("idx_race_source", "race_results", ["source"])
    op.create_index("idx_race_rating_type", "race_results", ["rating_type"])

    # ---------------------------------------------------------------
    # 3. Create ORC certificates table
    # ---------------------------------------------------------------
    op.create_table(
        "orc_certificates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("boat_id", sa.Integer, sa.ForeignKey("boats.id")),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("ref_no", sa.Text, nullable=False),
        sa.Column("country_id", sa.Text, nullable=False),
        sa.Column("yacht_name", sa.Text),
        sa.Column("sail_no", sa.Text),
        sa.Column("owner_name", sa.Text),
        sa.Column("class_name", sa.Text),
        sa.Column("builder", sa.Text),
        sa.Column("designer", sa.Text),
        sa.Column("year_built", sa.Integer),
        sa.Column("gph", sa.Numeric(8, 2)),
        sa.Column("osn", sa.Numeric(8, 2)),
        sa.Column("cdl", sa.Numeric(8, 3)),
        sa.Column("triple_low", sa.Numeric(8, 2)),
        sa.Column("triple_med", sa.Numeric(8, 2)),
        sa.Column("triple_high", sa.Numeric(8, 2)),
        sa.Column("loa", sa.Numeric(6, 2)),
        sa.Column("displacement", sa.Numeric(10, 1)),
        sa.Column("draft", sa.Numeric(6, 3)),
        sa.Column("sail_area_upwind", sa.Numeric(8, 2)),
        sa.Column("sail_area_downwind", sa.Numeric(8, 2)),
        sa.Column("stability_index", sa.Numeric(8, 2)),
        sa.Column("raw_data", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("ref_no", "country_id", "snapshot_date"),
    )
    op.create_index("idx_orc_boat", "orc_certificates", ["boat_id"])
    op.create_index("idx_orc_country", "orc_certificates", ["country_id"])
    op.create_index("idx_orc_snapshot_date", "orc_certificates", ["snapshot_date"])
    op.create_index("idx_orc_sail_number", "orc_certificates", ["sail_no"])

    # ---------------------------------------------------------------
    # 4. Create ORC snapshots table (per-country download tracking)
    # ---------------------------------------------------------------
    op.create_table(
        "orc_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("country_id", sa.Text, nullable=False),
        sa.Column("snapshot_date", sa.Date, nullable=False),
        sa.Column("records_found", sa.Integer),
        sa.Column("raw_json_path", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("country_id", "snapshot_date"),
    )

    # ---------------------------------------------------------------
    # 5. Create boat_identities table
    # ---------------------------------------------------------------
    op.create_table(
        "boat_identities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("boat_id", sa.Integer, sa.ForeignKey("boats.id"), nullable=False),
        sa.Column("boat_name", sa.Text),
        sa.Column("sail_number", sa.Text),
        sa.Column("owner", sa.Text),
        sa.Column("flag", sa.Text),
        sa.Column("source", sa.Text),
        sa.Column("observed_date", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_identity_boat", "boat_identities", ["boat_id"])

    # ---------------------------------------------------------------
    # 6. Create design_classes table
    # ---------------------------------------------------------------
    op.create_table(
        "design_classes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name_canonical", sa.Text, nullable=False, unique=True),
        sa.Column("aliases", sa.JSON),
        sa.Column("builder", sa.Text),
        sa.Column("designer", sa.Text),
        sa.Column("nominal_loa", sa.Numeric(6, 2)),
        sa.Column("nominal_lwl", sa.Numeric(6, 2)),
        sa.Column("nominal_beam", sa.Numeric(6, 2)),
        sa.Column("nominal_draft", sa.Numeric(6, 2)),
        sa.Column("nominal_displacement", sa.Numeric(10, 1)),
        sa.Column("year_first", sa.Integer),
        sa.Column("year_last", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ---------------------------------------------------------------
    # 7. Create ingestion_log table
    # ---------------------------------------------------------------
    op.create_table(
        "ingestion_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, server_default="running"),
        sa.Column("records_found", sa.Integer),
        sa.Column("records_new", sa.Integer),
        sa.Column("records_updated", sa.Integer),
        sa.Column("error_message", sa.Text),
        sa.Column("metadata", sa.JSON),
    )
    op.create_index("idx_ingestion_source", "ingestion_log", ["source"])
    op.create_index("idx_ingestion_started", "ingestion_log", ["started_at"])

    # ---------------------------------------------------------------
    # 8. Create insight_cache table
    # ---------------------------------------------------------------
    op.create_table(
        "insight_cache",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("boat_id", sa.Integer, sa.ForeignKey("boats.id")),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("rendered_context", sa.Text),
        sa.Column("response", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_insight_boat", "insight_cache", ["boat_id"])
    op.create_index("idx_insight_expires", "insight_cache", ["expires_at"])

    # ---------------------------------------------------------------
    # 9. Enable pg_trgm for fuzzy search
    # ---------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        "idx_boats_name_trgm", "boats", ["boat_name"],
        postgresql_using="gin",
        postgresql_ops={"boat_name": "gin_trgm_ops"},
    )
    op.create_index(
        "idx_boats_sail_trgm", "boats", ["sail_number"],
        postgresql_using="gin",
        postgresql_ops={"sail_number": "gin_trgm_ops"},
    )

    # ---------------------------------------------------------------
    # 10. Create materialized views
    # ---------------------------------------------------------------
    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_design_class_stats AS
    SELECT
        COALESCE(b.design_canonical, b.design) AS design_name,
        COUNT(*) AS fleet_size,
        MIN(t.tcc) AS min_tcc,
        MAX(t.tcc) AS max_tcc,
        AVG(t.tcc) AS avg_tcc,
        STDDEV(t.tcc) AS stddev_tcc,
        COUNT(DISTINCT b.country) AS country_count
    FROM boats b
    LEFT JOIN LATERAL (
        SELECT tcc FROM tcc_snapshots
        WHERE boat_id = b.id
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) t ON true
    WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
    GROUP BY COALESCE(b.design_canonical, b.design)
    HAVING COUNT(*) >= 2
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_design_stats_name
    ON mv_design_class_stats (design_name)
    """)

    op.execute("""
    CREATE MATERIALIZED VIEW IF NOT EXISTS mv_country_fleet_trends AS
    SELECT
        b.country,
        t.snapshot_date,
        COUNT(DISTINCT b.id) AS boat_count,
        AVG(t.tcc) AS avg_tcc
    FROM boats b
    JOIN tcc_snapshots t ON t.boat_id = b.id
    WHERE b.country IS NOT NULL
    GROUP BY b.country, t.snapshot_date
    """)

    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_country_trends_pk
    ON mv_country_fleet_trends (country, snapshot_date)
    """)


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_country_fleet_trends")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_design_class_stats")
    op.drop_table("insight_cache")
    op.drop_table("ingestion_log")
    op.drop_table("design_classes")
    op.drop_table("boat_identities")
    op.drop_table("orc_snapshots")
    op.drop_table("orc_certificates")

    # Revert race_results constraint
    op.drop_constraint("race_results_boat_event_race_key", "race_results", type_="unique")
    op.create_unique_constraint(
        "race_results_boat_id_event_name_event_date_key",
        "race_results",
        ["boat_id", "event_name", "event_date"],
    )
    op.drop_index("idx_race_source", "race_results")
    op.drop_index("idx_race_rating_type", "race_results")
    op.drop_column("race_results", "created_at")
    op.drop_column("race_results", "source")
    op.drop_column("race_results", "time_behind_winner")
    op.drop_column("race_results", "rating_value")
    op.drop_column("race_results", "rating_type")
    op.drop_column("race_results", "status")
    op.drop_column("race_results", "class_fleet_size")
    op.drop_column("race_results", "class_place")
    op.drop_column("race_results", "class_name")
    op.drop_column("race_results", "fleet_size")
    op.drop_column("race_results", "course_distance_nm")
    op.drop_column("race_results", "race_number")
    op.drop_column("race_results", "race_date_specific")
    op.drop_column("race_results", "race_name")
    op.drop_column("race_results", "event_type")
    op.drop_column("race_results", "organizing_club")
    op.drop_column("race_results", "event_series")

    # Revert boats columns
    op.drop_index("idx_boats_design_canonical", "boats")
    op.drop_index("idx_boats_name_trgm", "boats")
    op.drop_index("idx_boats_sail_trgm", "boats")
    op.drop_column("boats", "displacement_kg")
    op.drop_column("boats", "beam_max")
    op.drop_column("boats", "lwl")
    op.drop_column("boats", "loa")
    op.drop_column("boats", "current_flag")
    op.drop_column("boats", "current_sail_number")
    op.drop_column("boats", "current_name")
    op.drop_column("boats", "design_canonical")
    op.drop_column("boats", "designer")
    op.drop_column("boats", "builder")
    op.drop_column("boats", "hull_id")

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
