"""Boat-related API response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class Dimensions(BaseModel):
    """Physical dimensions of a boat."""

    loa: float | None = Field(None, description="Length overall in metres")
    lwl: float | None = Field(None, description="Load waterline length in metres")
    beam: float | None = Field(None, description="Maximum beam in metres")
    draft: float | None = Field(None, description="Draft in metres")
    displacement_kg: float | None = Field(None, description="Displacement in kilograms")


class RatingSnapshot(BaseModel):
    """A single IRC TCC rating at a point in time."""

    snapshot_date: date
    tcc: float = Field(..., description="IRC Time Correction Coefficient")
    non_spi_tcc: float | None = Field(None, description="Non-spinnaker TCC")
    crew: int | None = None
    dlr: int | None = Field(None, description="DLR code")
    lh: float | None = Field(None, description="Rated hull length")
    beam: float | None = None
    draft: float | None = None
    headsails: int | None = None
    spinnakers: int | None = None
    category: str | None = Field(None, description="Stability category (e.g. '0', '1', '2')")


class BoatIdentity(BaseModel):
    """A recorded identity (name/sail/owner/flag) for a boat at a point in time."""

    boat_name: str | None = None
    sail_number: str | None = None
    owner: str | None = None
    flag: str | None = None
    source: str | None = None
    observed_date: date | None = None


class BoatSummary(BaseModel):
    """Compact boat representation for lists and search results."""

    id: int
    boat_name: str
    sail_number: str
    design: str | None = None
    country: str | None = None
    year_built: int | None = None
    current_tcc: float | None = Field(None, description="Most recent IRC TCC value")
    latest_snapshot_date: date | None = None


class BoatDetail(BaseModel):
    """Full boat profile with dimensions, ratings, and identity history."""

    id: int
    boat_name: str
    sail_number: str
    cert_number: str | None = None
    design: str | None = None
    design_canonical: str | None = None
    country: str | None = None
    year_built: int | None = None
    builder: str | None = None
    designer: str | None = None
    hull_id: str | None = None

    current_name: str | None = Field(None, description="Latest known boat name")
    current_sail_number: str | None = Field(None, description="Latest known sail number")
    current_flag: str | None = Field(None, description="Latest known flag state")

    dimensions: Dimensions | None = None
    latest_rating: RatingSnapshot | None = None
    identities: list[BoatIdentity] = Field(default_factory=list)

    total_results: int = Field(0, description="Number of race results on record")
    total_certificates: int = Field(0, description="Number of IRC certificates on record")
    total_orc_certificates: int = Field(0, description="Number of ORC certificates on record")
