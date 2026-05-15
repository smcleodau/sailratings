"""Certificate-related API response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class IRCCertificate(BaseModel):
    """Parsed IRC certificate with hull, rig, and sail measurements."""

    cert_number: str | None = None
    issue_date: date | None = None
    source: str | None = None

    # Hull
    lh: float | None = Field(None, description="Rated hull length")
    beam: float | None = None
    draft: float | None = None
    displacement: float | None = Field(None, description="Displacement in kg")
    bo: float | None = Field(None, description="Bow overhang")
    so: float | None = Field(None, description="Stern overhang")

    # Rig
    p: float | None = Field(None, description="Mainsail hoist")
    e: float | None = Field(None, description="Mainsail foot")
    j: float | None = Field(None, description="Foretriangle base")
    fl: float | None = Field(None, description="Foretriangle height")
    stl: float | None = Field(None, description="Staysail luff")
    spl: float | None = Field(None, description="Spinnaker pole length")
    rig_type: str | None = None
    mast_material: str | None = None
    spreaders: int | None = None

    # Mainsail widths
    muw: float | None = Field(None, description="Mainsail upper width")
    mtw: float | None = Field(None, description="Mainsail three-quarter width")
    mhw: float | None = Field(None, description="Mainsail half width")

    # Headsail
    hlu: float | None = Field(None, description="Headsail luff")
    hlp: float | None = Field(None, description="Headsail perpendicular")
    hhw: float | None = Field(None, description="Headsail half width")
    htw: float | None = Field(None, description="Headsail three-quarter width")
    huw: float | None = Field(None, description="Headsail upper width")

    # Symmetric spinnaker
    sym_slu: float | None = None
    sym_sle: float | None = None
    sym_sf: float | None = None
    sym_shw: float | None = None

    # Asymmetric spinnaker
    asym_slu: float | None = None
    asym_sle: float | None = None
    asym_sf: float | None = None
    asym_shw: float | None = None

    # Stability
    water_ballast: float | None = None
    stix: float | None = Field(None, description="STIX stability index")
    avs: float | None = Field(None, description="Angle of vanishing stability")
    design_category: str | None = None

    # Extended measurements
    lwp: float | None = Field(None, description="Waterline length")
    dlr: int | None = Field(None, description="Displacement-length ratio")
    x: float | None = Field(None, description="Hull overhang x")
    y: float | None = Field(None, description="Hull overhang y")
    internal_ballast: float | None = Field(None, description="Internal ballast in kg")
    hsa: float | None = Field(None, description="Headsail area m²")
    headsails_max: int | None = Field(None, description="Max headsails carried")
    flying_headsails_max: int | None = Field(None, description="Max flying headsails")
    fsa: float | None = Field(None, description="Flying headsail area m²")
    flu: float | None = Field(None, description="Flying headsail luff")
    flp: float | None = Field(None, description="Flying headsail perpendicular")
    fuw: float | None = Field(None, description="Flying headsail upper width")
    ftw: float | None = Field(None, description="Flying headsail 3/4 width")
    fhw: float | None = Field(None, description="Flying headsail half width")
    fsfl: float | None = Field(None, description="Flying headsail foot length")
    fshw: float | None = Field(None, description="Flying headsail... width")
    spa: float | None = Field(None, description="Spinnaker area m²")
    spinnakers_max: int | None = Field(None, description="Max spinnakers carried")
    stl_fh_max: float | None = Field(None, description="STLFHmax")
    aft_rigging: int | None = Field(None, description="Aft rigging count")


class ORCCertificate(BaseModel):
    """ORC certificate with key ratings and dimensions."""

    ref_no: str
    country_id: str
    snapshot_date: date
    yacht_name: str | None = None
    sail_no: str | None = None
    class_name: str | None = None
    builder: str | None = None
    designer: str | None = None
    year_built: int | None = None

    # Ratings
    gph: float | None = Field(None, description="General Purpose Handicap (sec/NM)")
    osn: float | None = Field(None, description="Offshore Scoring Number")
    cdl: float | None = Field(None, description="Calculated Dynamic Length")
    triple_low: float | None = Field(None, description="Triple number - light wind")
    triple_med: float | None = Field(None, description="Triple number - medium wind")
    triple_high: float | None = Field(None, description="Triple number - heavy wind")

    # Dimensions
    loa: float | None = None
    displacement: float | None = None
    draft: float | None = None
    sail_area_upwind: float | None = None
    sail_area_downwind: float | None = None
    stability_index: float | None = None


class FieldChange(BaseModel):
    """A single field that changed between two certificates or snapshots."""

    field: str
    old_value: float | str | int | None = None
    new_value: float | str | int | None = None
    change_pct: float | None = Field(
        None, description="Percentage change for numeric fields"
    )


class CertificateDiff(BaseModel):
    """Differences between two certificates or rating snapshots for the same boat."""

    boat_id: int
    boat_name: str
    old_date: date
    new_date: date
    source: str = Field(..., description="'irc' or 'orc'")
    changes: list[FieldChange] = Field(default_factory=list)
