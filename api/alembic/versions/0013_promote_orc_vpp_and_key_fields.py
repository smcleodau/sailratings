"""Promote VPP polars and key ORC fields from raw_data JSON to columns.

The ORC scraper has been storing the full RMS response in `orc_certificates.raw_data`
since the start (json type, populated on 9,886 of 13,997 rows — the rest are
slimmer directory-style payloads that don't carry the full VPP). Several
high-value fields sit in that JSON unused because no column exists:

  - Allowances        full VPP polar table (beat / reach / run at multiple wind
                      speeds, both crewed and windward-leeward); the engine for
                      speed prediction, design-compare, and IRC<->ORC cross-rating
  - Dynamic_Allowance no-spinnaker TMF allowance (needed for IRC's no-spin races)
  - Dspl_Sailing      sailing displacement (vs Dspl_Measurement already promoted);
                      delta reveals ballast / buoyancy differences
  - IMSL              mast height above WL (a primary sail-area driver)
  - MB                maximum beam (primary input to IRC's beam handling)
  - APHD              appendage depth (keel draft to bulb)
  - APHT              appendage type code (fin / bulb / canting, etc.)
  - WSS               wetted surface area at sailing trim
  - TMF_Offshore      time multiplication factor, offshore courses
  - TMF_Inshore       time multiplication factor, inshore courses

This migration:
  1. Adds the columns to `orc_certificates`.
  2. Backfills them from the existing raw_data JSON in a single UPDATE.
     Defensive: each value uses `(raw_data::jsonb) ?? key` first, so rows
     that don't carry a given key get NULL rather than a cast error.

Code-side work this migration does NOT do, but is needed:
  - `db/models.py`: add the new columns to `ORCCertificate` so SQLAlchemy
    can read them. Without this, queries still work (raw SQL ignores
    unknown columns) but ORM-based code can't see them.
  - `scrapers/orc.py`: populate these columns on future ingest so new
    rows don't depend on the backfill.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


# Backfill SQL is in one statement so it runs in one transaction.
# Defensive casts: ->>'Key' returns text; ::numeric handles missing/null.
# For numeric casts we use NULLIF to coerce empty strings to NULL safely.
_BACKFILL_SQL = """
    UPDATE orc_certificates
    SET
        allowances        = (raw_data::jsonb) -> 'Allowances',
        dynamic_allowance = NULLIF((raw_data::jsonb) ->> 'Dynamic_Allowance', '')::numeric(5,3),
        dspl_sailing      = NULLIF((raw_data::jsonb) ->> 'Dspl_Sailing',      '')::numeric(10,1),
        imsl              = NULLIF((raw_data::jsonb) ->> 'IMSL',              '')::numeric(6,3),
        mb                = NULLIF((raw_data::jsonb) ->> 'MB',                '')::numeric(6,3),
        aphd              = NULLIF((raw_data::jsonb) ->> 'APHD',              '')::numeric(6,3),
        apht              = NULLIF((raw_data::jsonb) ->> 'APHT',              ''),
        wss               = NULLIF((raw_data::jsonb) ->> 'WSS',               '')::numeric(8,2),
        tmf_offshore      = NULLIF((raw_data::jsonb) ->> 'TMF_Offshore',      '')::numeric(6,4),
        tmf_inshore       = NULLIF((raw_data::jsonb) ->> 'TMF_Inshore',       '')::numeric(6,4)
    WHERE raw_data IS NOT NULL;
"""


def upgrade() -> None:
    op.add_column(
        "orc_certificates",
        sa.Column("allowances", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("orc_certificates", sa.Column("dynamic_allowance", sa.Numeric(5, 3), nullable=True))
    op.add_column("orc_certificates", sa.Column("dspl_sailing",      sa.Numeric(10, 1), nullable=True))
    op.add_column("orc_certificates", sa.Column("imsl",              sa.Numeric(6, 3), nullable=True))
    op.add_column("orc_certificates", sa.Column("mb",                sa.Numeric(6, 3), nullable=True))
    op.add_column("orc_certificates", sa.Column("aphd",              sa.Numeric(6, 3), nullable=True))
    op.add_column("orc_certificates", sa.Column("apht",              sa.Text(), nullable=True))
    op.add_column("orc_certificates", sa.Column("wss",               sa.Numeric(8, 2), nullable=True))
    op.add_column("orc_certificates", sa.Column("tmf_offshore",      sa.Numeric(6, 4), nullable=True))
    op.add_column("orc_certificates", sa.Column("tmf_inshore",       sa.Numeric(6, 4), nullable=True))

    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("orc_certificates", "tmf_inshore")
    op.drop_column("orc_certificates", "tmf_offshore")
    op.drop_column("orc_certificates", "wss")
    op.drop_column("orc_certificates", "apht")
    op.drop_column("orc_certificates", "aphd")
    op.drop_column("orc_certificates", "mb")
    op.drop_column("orc_certificates", "imsl")
    op.drop_column("orc_certificates", "dspl_sailing")
    op.drop_column("orc_certificates", "dynamic_allowance")
    op.drop_column("orc_certificates", "allowances")
