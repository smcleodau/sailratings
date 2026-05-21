"""Pydantic models for all IRC data types."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class TCCListingRow(BaseModel):
    """A single row from the TCC listing CSV."""

    boat_name: str
    sail_number: str
    cert_number: str
    issue_date: str | None = None
    cert_year: int | None = None
    tcc: Decimal
    endorsed: str | None = None
    secondary: str | None = None
    is_secondary: bool = False  # True when this CSV row is a secondary cert
                                # (boat_name ends in ' - SEC' or 'Secondary' col
                                # is set). The importer must NOT create a new
                                # boats row for these — the cert is attached to
                                # the primary boat via the cert PDF scraper.
    non_spi_tcc: Decimal | None = None
    crew: int | None = None
    dlr: int | None = None
    lh: Decimal | None = None
    beam: Decimal | None = None
    draft: Decimal | None = None
    single_furling_headsail: str | None = None
    headsails: int | None = None
    flying_headsails: int | None = None
    spinnakers: int | None = None
    series_date: int | None = None
    age_date: int | None = None
    racing_area: int | None = None
    ssb_base_value: int | None = None
    stix: int | None = None
    avs: int | None = None
    category: str | None = None
    valid_code: str | None = None


class BoatRecord(BaseModel):
    """A boat in the database."""

    id: int | None = None
    boat_name: str
    sail_number: str
    cert_number: str | None = None
    design: str | None = None
    country: str | None = None
    year_built: int | None = None


class TCCSnapshot(BaseModel):
    """A point-in-time TCC snapshot."""

    id: int | None = None
    boat_id: int
    snapshot_date: date
    cert_year: int | None = None
    tcc: Decimal
    non_spi_tcc: Decimal | None = None
    endorsed: str | None = None
    secondary: str | None = None
    crew: int | None = None
    dlr: int | None = None
    lh: Decimal | None = None
    beam: Decimal | None = None
    draft: Decimal | None = None
    single_furling_headsail: str | None = None
    headsails: int | None = None
    flying_headsails: int | None = None
    spinnakers: int | None = None
    series_date: int | None = None
    age_date: int | None = None
    racing_area: int | None = None
    ssb_base_value: int | None = None
    stix: int | None = None
    avs: int | None = None
    category: str | None = None


class CertificateData(BaseModel):
    """Parsed IRC certificate data from PDF."""

    cert_number: str | None = None
    issue_date: date | None = None
    source: str = "ircrating.org"
    source_url: str | None = None
    pdf_path: str | None = None

    # Hull
    lh: Decimal | None = None
    beam: Decimal | None = None
    draft: Decimal | None = None
    displacement: Decimal | None = None
    bo: Decimal | None = None
    so: Decimal | None = None

    # Rig
    p: Decimal | None = None
    e: Decimal | None = None
    j: Decimal | None = None
    fl: Decimal | None = None
    stl: Decimal | None = None
    spl: Decimal | None = None
    rig_type: str | None = None
    mast_material: str | None = None
    spreaders: int | None = None

    # Mainsail
    muw: Decimal | None = None
    mtw: Decimal | None = None
    mhw: Decimal | None = None

    # Headsail
    hlu: Decimal | None = None
    hlp: Decimal | None = None
    hhw: Decimal | None = None
    htw: Decimal | None = None
    huw: Decimal | None = None

    # Spinnakers
    sym_slu: Decimal | None = None
    sym_sle: Decimal | None = None
    sym_sf: Decimal | None = None
    sym_shw: Decimal | None = None
    asym_slu: Decimal | None = None
    asym_sle: Decimal | None = None
    asym_sf: Decimal | None = None
    asym_shw: Decimal | None = None

    # Other
    water_ballast: Decimal | None = None
    stix: Decimal | None = None
    avs: Decimal | None = None
    design_category: str | None = None

    # Extended measurements
    lwp: Decimal | None = None
    dlr: int | None = None
    x: Decimal | None = None
    y: Decimal | None = None
    internal_ballast: Decimal | None = None
    hsa: Decimal | None = None
    headsails_max: int | None = None
    flying_headsails_max: int | None = None
    fsa: Decimal | None = None
    flu: Decimal | None = None
    flp: Decimal | None = None
    fuw: Decimal | None = None
    ftw: Decimal | None = None
    fhw: Decimal | None = None
    fsfl: Decimal | None = None
    fshw: Decimal | None = None
    spa: Decimal | None = None
    spinnakers_max: int | None = None
    stl_fh_max: Decimal | None = None
    aft_rigging: int | None = None

    raw_data: dict[str, Any] | None = None


class RaceResult(BaseModel):
    """A race result record."""

    boat_id: int | None = None
    event_name: str
    event_date: date | None = None
    source_url: str | None = None
    tcc_at_race: Decimal | None = None
    place: int | None = None
    division: str | None = None
    elapsed_time: str | None = None
    corrected_time: str | None = None
    raw_data: dict[str, Any] | None = None
