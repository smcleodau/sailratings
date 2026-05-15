"""Country fleet and trends API response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class CountryInfo(BaseModel):
    """Basic country-level metadata."""

    country: str = Field(..., description="ISO 3166 country code or ISAF MNA code")
    boat_count: int = 0
    design_count: int = Field(0, description="Distinct design classes in this fleet")
    tcc_mean: float | None = None
    tcc_median: float | None = None


class CountryFleet(BaseModel):
    """Full fleet listing for a country with summary stats."""

    country: str
    boat_count: int = 0
    irc_count: int = Field(0, description="Boats with IRC ratings")
    orc_count: int = Field(0, description="Boats with ORC certificates")
    top_designs: list[DesignCount] = Field(
        default_factory=list,
        description="Most popular design classes in this fleet",
    )
    tcc_mean: float | None = None
    tcc_median: float | None = None


class DesignCount(BaseModel):
    """Design class with boat count, used in fleet summaries."""

    design: str
    count: int


class FleetTrends(BaseModel):
    """Time-series fleet statistics for a country or globally."""

    country: str | None = Field(None, description="Country code, or null for global")
    data_points: list[FleetDataPoint] = Field(default_factory=list)


class FleetDataPoint(BaseModel):
    """A single time-series data point in fleet trends."""

    date: date
    boat_count: int = 0
    tcc_mean: float | None = None
    tcc_median: float | None = None
    new_certificates: int = Field(0, description="New certificates issued in this period")
