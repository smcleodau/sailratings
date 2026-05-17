"""Drop never-written `boats.current_*` columns; rename + bump
`irc_certificates.displacement` to `displacement_kg` (Numeric(10,1)).

Two wins from the schema-dedup audit, bundled because they're both
safe-and-simple cleanups paired with mechanical code edits.

1. Drop `boats.current_name`, `boats.current_sail_number`,
   `boats.current_flag`. Verified across all 9,384 rows: 100% NULL on
   every column. No code path writes them. The Python schema + admin
   docstring referenced them but were always serialising / describing
   NULL. The real source of truth for historical name/sail/owner data
   is `boat_identities`, which has its own write path.

2. Rename `irc_certificates.displacement` -> `displacement_kg` and bump
   precision from Numeric(8,1) to Numeric(10,1) to match the other
   three displacement columns in the schema. The bare `displacement`
   name was ambiguous next to `orc_certificates.displacement`; the
   `_kg` suffix matches `boats.displacement_kg` and the established
   convention.

Materialised view `mv_within_class_stats` (added in migration 0003)
references `irc_certificates.displacement` in both its aggregate
columns and in the LATERAL subquery. Postgres blocks ALTER COLUMN TYPE
when a view depends on the column, so this migration drops and
recreates the view around the column changes.

ORC columns named `displacement` are intentionally untouched — that
table's column is part of the public ORC RMS payload shape.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


# View body captured verbatim from the running DB (definition from 0003)
# with `displacement` swapped to `displacement_kg` for the upgrade and
# left as `displacement` for the downgrade. Body otherwise identical.

_MV_BODY_NEW = """
    WITH latest_snapshot AS (
        SELECT DISTINCT ON (tcc_snapshots.boat_id) tcc_snapshots.boat_id,
            tcc_snapshots.tcc,
            tcc_snapshots.non_spi_tcc,
            tcc_snapshots.lh,
            tcc_snapshots.beam,
            tcc_snapshots.draft,
            tcc_snapshots.crew,
            tcc_snapshots.dlr,
            tcc_snapshots.headsails,
            tcc_snapshots.flying_headsails,
            tcc_snapshots.spinnakers
        FROM tcc_snapshots
        ORDER BY tcc_snapshots.boat_id, tcc_snapshots.snapshot_date DESC
    ), boat_design AS (
        SELECT b.id AS boat_id,
            COALESCE(b.design_canonical, b.design) AS design_name
        FROM boats b
        WHERE COALESCE(b.design_canonical, b.design) IS NOT NULL
    )
    SELECT bd.design_name,
        count(*) AS n_boats,
        avg(ls.tcc)::numeric(8,5)        AS mean_tcc,
        stddev(ls.tcc)::numeric(8,5)     AS std_tcc,
        min(ls.tcc)::numeric(8,5)        AS min_tcc,
        max(ls.tcc)::numeric(8,5)        AS max_tcc,
        avg(ls.lh)::numeric(8,3)         AS mean_lh,
        stddev(ls.lh)::numeric(8,3)      AS std_lh,
        avg(ls.beam)::numeric(8,3)       AS mean_beam,
        stddev(ls.beam)::numeric(8,3)    AS std_beam,
        avg(ls.draft)::numeric(8,3)      AS mean_draft,
        stddev(ls.draft)::numeric(8,3)   AS std_draft,
        avg(ls.crew)::numeric(6,2)       AS mean_crew,
        avg(ls.dlr)::numeric(8,1)        AS mean_dlr,
        avg(ls.headsails)::numeric(4,2)  AS mean_headsails,
        avg(ls.spinnakers)::numeric(4,2) AS mean_spinnakers,
        count(c.id)                              AS n_with_certs,
        avg(c.{disp_col})::numeric(10,1)         AS mean_displacement,
        stddev(c.{disp_col})::numeric(10,1)      AS std_displacement,
        avg(c.p)::numeric(6,2)                   AS mean_p,
        stddev(c.p)::numeric(6,2)                AS std_p,
        avg(c.e)::numeric(6,2)                   AS mean_e,
        stddev(c.e)::numeric(6,2)                AS std_e,
        avg(c.j)::numeric(6,2)                   AS mean_j,
        stddev(c.j)::numeric(6,2)                AS std_j,
        avg(c.hlu)::numeric(6,2)                 AS mean_hlu,
        stddev(c.hlu)::numeric(6,2)              AS std_hlu,
        avg(c.hlp)::numeric(6,2)                 AS mean_hlp,
        stddev(c.hlp)::numeric(6,2)              AS std_hlp,
        avg(c.muw)::numeric(6,2)                 AS mean_muw,
        stddev(c.muw)::numeric(6,2)              AS std_muw,
        avg(c.mhw)::numeric(6,2)                 AS mean_mhw,
        stddev(c.mhw)::numeric(6,2)              AS std_mhw,
        avg(c.stl)::numeric(6,2)                 AS mean_stl,
        stddev(c.stl)::numeric(6,2)              AS std_stl,
        avg(c.sym_slu)::numeric(6,2)             AS mean_sym_slu,
        stddev(c.sym_slu)::numeric(6,2)          AS std_sym_slu,
        avg(c.sym_sf)::numeric(6,2)              AS mean_sym_sf,
        stddev(c.sym_sf)::numeric(6,2)           AS std_sym_sf
    FROM boat_design bd
    JOIN latest_snapshot ls ON ls.boat_id = bd.boat_id
    LEFT JOIN LATERAL (
        SELECT * FROM irc_certificates
        WHERE irc_certificates.boat_id = bd.boat_id
        ORDER BY irc_certificates.issue_date DESC NULLS LAST
        LIMIT 1
    ) c ON true
    GROUP BY bd.design_name
    HAVING count(*) >= 2
"""


def _create_mv(disp_col: str) -> None:
    """Create mv_within_class_stats with the given displacement column name."""
    op.execute(
        f"CREATE MATERIALIZED VIEW mv_within_class_stats AS {_MV_BODY_NEW.format(disp_col=disp_col)}"
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_within_class_stats_design "
        "ON mv_within_class_stats USING btree (design_name)"
    )


def upgrade() -> None:
    # 1. Drop the materialised view that pins the column type.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_within_class_stats")

    # 2. Drop dead current_* columns from boats.
    op.drop_column("boats", "current_name")
    op.drop_column("boats", "current_sail_number")
    op.drop_column("boats", "current_flag")

    # 3. Rename + retype irc_certificates.displacement -> displacement_kg.
    op.alter_column(
        "irc_certificates",
        "displacement",
        new_column_name="displacement_kg",
        type_=sa.Numeric(10, 1),
        existing_type=sa.Numeric(8, 1),
        existing_nullable=True,
    )

    # 4. Recreate the materialised view with the new column name.
    _create_mv("displacement_kg")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_within_class_stats")

    op.alter_column(
        "irc_certificates",
        "displacement_kg",
        new_column_name="displacement",
        type_=sa.Numeric(8, 1),
        existing_type=sa.Numeric(10, 1),
        existing_nullable=True,
    )

    op.add_column("boats", sa.Column("current_flag", sa.Text(), nullable=True))
    op.add_column("boats", sa.Column("current_sail_number", sa.Text(), nullable=True))
    op.add_column("boats", sa.Column("current_name", sa.Text(), nullable=True))

    _create_mv("displacement")
