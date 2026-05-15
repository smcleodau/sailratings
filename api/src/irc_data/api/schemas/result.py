"""Race result API response schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class RaceResult(BaseModel):
    """A single race result for a boat."""

    id: int
    event_name: str
    event_date: date | None = None
    event_type: str | None = None
    race_name: str | None = None
    race_number: int | None = None
    course_distance_nm: float | None = None

    place: int | None = None
    fleet_size: int | None = None
    class_name: str | None = None
    class_place: int | None = None
    class_fleet_size: int | None = None
    status: str | None = Field("finished", description="finished, DNF, DNS, DSQ, etc.")

    rating_type: str | None = Field(None, description="IRC, ORC, PHF, etc.")
    rating_value: float | None = None
    division: str | None = None

    elapsed_time: str | None = Field(None, description="Elapsed time as HH:MM:SS or ISO duration")
    corrected_time: str | None = Field(None, description="Corrected time as HH:MM:SS or ISO duration")

    source: str | None = None


class RaceResultList(BaseModel):
    """Paginated list of race results for a boat."""

    boat_id: int
    boat_name: str
    results: list[RaceResult] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


class EventSummary(BaseModel):
    """Summary of a single event across all participating boats."""

    event_name: str
    event_date: date | None = None
    event_type: str | None = None
    organizing_club: str | None = None
    fleet_size: int = 0
    boat_count: int = Field(0, description="Distinct boats that participated")
    race_count: int = Field(0, description="Number of individual races in the event")


class PerformanceStats(BaseModel):
    """Aggregated racing performance statistics for a boat."""

    boat_id: int
    boat_name: str
    total_races: int = 0
    total_events: int = 0
    finishes: int = 0
    wins: int = 0
    podiums: int = Field(0, description="Top-3 finishes")
    top_ten_pct: float | None = Field(
        None, description="Percentage of races finishing in top 10% of fleet"
    )
    avg_place: float | None = None
    avg_fleet_size: float | None = None
    median_place_pct: float | None = Field(
        None,
        description="Median finishing position as percentage of fleet (0=first, 100=last)",
    )
    first_race_date: date | None = None
    last_race_date: date | None = None
