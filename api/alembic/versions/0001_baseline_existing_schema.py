"""baseline existing schema

Revision ID: 0001
Revises:
Create Date: 2026-03-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create existing tables if they don't exist (idempotent baseline)."""
    op.execute("""
    CREATE TABLE IF NOT EXISTS boats (
        id SERIAL PRIMARY KEY,
        boat_name TEXT NOT NULL,
        sail_number TEXT NOT NULL,
        cert_number TEXT,
        design TEXT,
        country TEXT,
        year_built INTEGER,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(sail_number, cert_number)
    );

    CREATE TABLE IF NOT EXISTS tcc_snapshots (
        id SERIAL PRIMARY KEY,
        boat_id INTEGER REFERENCES boats(id),
        snapshot_date DATE NOT NULL,
        cert_year INTEGER,
        tcc NUMERIC(6,4) NOT NULL,
        non_spi_tcc NUMERIC(6,4),
        endorsed TEXT,
        secondary TEXT,
        crew INTEGER,
        dlr INTEGER,
        lh NUMERIC(6,2),
        beam NUMERIC(6,2),
        draft NUMERIC(6,2),
        single_furling_headsail TEXT,
        headsails INTEGER,
        flying_headsails INTEGER,
        spinnakers INTEGER,
        series_date INTEGER,
        age_date INTEGER,
        racing_area INTEGER,
        ssb_base_value INTEGER,
        stix INTEGER,
        avs INTEGER,
        category TEXT,
        UNIQUE(boat_id, snapshot_date)
    );

    CREATE TABLE IF NOT EXISTS certificates (
        id SERIAL PRIMARY KEY,
        boat_id INTEGER REFERENCES boats(id),
        cert_number TEXT,
        issue_date DATE,
        source TEXT,
        source_url TEXT,
        pdf_path TEXT,
        lh NUMERIC(6,2), beam NUMERIC(6,2), draft NUMERIC(6,2),
        displacement NUMERIC(8,1),
        bo NUMERIC(6,2), so NUMERIC(6,2),
        p NUMERIC(6,2), e NUMERIC(6,2), j NUMERIC(6,2),
        fl NUMERIC(6,2), stl NUMERIC(6,2), spl NUMERIC(6,2),
        rig_type TEXT, mast_material TEXT, spreaders INTEGER,
        muw NUMERIC(6,2), mtw NUMERIC(6,2), mhw NUMERIC(6,2),
        hlu NUMERIC(6,2), hlp NUMERIC(6,2), hhw NUMERIC(6,2),
        htw NUMERIC(6,2), huw NUMERIC(6,2),
        sym_slu NUMERIC(6,2), sym_sle NUMERIC(6,2),
        sym_sf NUMERIC(6,2), sym_shw NUMERIC(6,2),
        asym_slu NUMERIC(6,2), asym_sle NUMERIC(6,2),
        asym_sf NUMERIC(6,2), asym_shw NUMERIC(6,2),
        water_ballast NUMERIC(6,1),
        stix NUMERIC(6,1), avs NUMERIC(6,1),
        design_category TEXT,
        raw_data JSONB,
        scraped_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(cert_number)
    );

    CREATE TABLE IF NOT EXISTS race_results (
        id SERIAL PRIMARY KEY,
        boat_id INTEGER REFERENCES boats(id),
        event_name TEXT NOT NULL,
        event_date DATE,
        source_url TEXT,
        tcc_at_race NUMERIC(6,4),
        place INTEGER,
        division TEXT,
        elapsed_time INTERVAL,
        corrected_time INTERVAL,
        raw_data JSONB,
        UNIQUE(boat_id, event_name, event_date)
    );

    CREATE TABLE IF NOT EXISTS cert_probe_attempts (
        id SERIAL PRIMARY KEY,
        boat_name TEXT NOT NULL,
        sail_number TEXT NOT NULL,
        cert_number_tried TEXT NOT NULL,
        found BOOLEAN DEFAULT FALSE,
        probed_at TIMESTAMPTZ DEFAULT now(),
        UNIQUE(cert_number_tried, sail_number)
    );

    CREATE INDEX IF NOT EXISTS idx_boats_design ON boats(design);
    CREATE INDEX IF NOT EXISTS idx_boats_country ON boats(country);
    CREATE INDEX IF NOT EXISTS idx_tcc_boat_date ON tcc_snapshots(boat_id, snapshot_date);
    CREATE INDEX IF NOT EXISTS idx_certs_boat ON certificates(boat_id);
    CREATE INDEX IF NOT EXISTS idx_race_boat ON race_results(boat_id);
    CREATE INDEX IF NOT EXISTS idx_race_date ON race_results(event_date);
    CREATE INDEX IF NOT EXISTS idx_probe_boat ON cert_probe_attempts(boat_name, sail_number);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cert_probe_attempts CASCADE")
    op.execute("DROP TABLE IF EXISTS race_results CASCADE")
    op.execute("DROP TABLE IF EXISTS certificates CASCADE")
    op.execute("DROP TABLE IF EXISTS tcc_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS boats CASCADE")
