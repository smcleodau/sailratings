"""Rating history API response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class RatingEntry(BaseModel):
    """A single data point in a boat's rating history."""

    snapshot_date: date
    tcc: float = Field(..., description="IRC Time Correction Coefficient")
    non_spi_tcc: float | None = None
    crew: int | None = None
    headsails: int | None = None
    spinnakers: int | None = None
    lh: float | None = Field(None, description="Rated hull length")
    beam: float | None = None
    draft: float | None = None
    dlr: int | None = None
    stix: int | None = None
    avs: int | None = None
    category: str | None = None


class RatingHistory(BaseModel):
    """Complete IRC TCC rating history for a boat."""

    boat_id: int
    boat_name: str
    sail_number: str
    design: str | None = None
    entries: list[RatingEntry] = Field(default_factory=list)
    tcc_min: float | None = Field(None, description="Lowest TCC seen")
    tcc_max: float | None = Field(None, description="Highest TCC seen")
    tcc_current: float | None = Field(None, description="Most recent TCC")
    total_snapshots: int = 0
