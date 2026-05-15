"""Design class API response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DesignSummary(BaseModel):
    """Compact design class representation for lists."""

    id: int
    name_canonical: str
    aliases: list[str] = Field(default_factory=list)
    builder: str | None = None
    designer: str | None = None
    nominal_loa: float | None = None
    boat_count: int = Field(0, description="Number of boats of this design in the database")


class DesignFleet(BaseModel):
    """A boat within a design class fleet listing."""

    boat_id: int
    boat_name: str
    sail_number: str
    country: str | None = None
    year_built: int | None = None
    current_tcc: float | None = None


class DesignDetail(BaseModel):
    """Full design class profile with nominal dimensions and fleet."""

    id: int
    name_canonical: str
    aliases: list[str] = Field(default_factory=list)
    builder: str | None = None
    designer: str | None = None
    year_first: int | None = Field(None, description="Earliest known build year")
    year_last: int | None = Field(None, description="Latest known build year")

    # Nominal dimensions
    nominal_loa: float | None = None
    nominal_lwl: float | None = None
    nominal_beam: float | None = None
    nominal_draft: float | None = None
    nominal_displacement: float | None = Field(None, description="Nominal displacement in kg")

    # Fleet stats
    boat_count: int = 0
    tcc_mean: float | None = Field(None, description="Average TCC across fleet")
    tcc_min: float | None = None
    tcc_max: float | None = None
    fleet: list[DesignFleet] = Field(default_factory=list)


class DesignComparison(BaseModel):
    """Side-by-side comparison of two or more design classes."""

    designs: list[DesignComparisonEntry] = Field(default_factory=list)


class DesignComparisonEntry(BaseModel):
    """One design in a side-by-side comparison."""

    name_canonical: str
    builder: str | None = None
    designer: str | None = None
    nominal_loa: float | None = None
    nominal_lwl: float | None = None
    nominal_beam: float | None = None
    nominal_draft: float | None = None
    nominal_displacement: float | None = None
    boat_count: int = 0
    tcc_mean: float | None = None
    tcc_min: float | None = None
    tcc_max: float | None = None
