"""Health check and data freshness API response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class IngestionEntry(BaseModel):
    """Status of a single ingestion source."""

    source: str = Field(..., description="Scraper/ingestion source name")
    status: str = Field(..., description="running, completed, failed")
    started_at: datetime
    completed_at: datetime | None = None
    records_found: int | None = None
    records_new: int | None = None
    records_updated: int | None = None
    error_message: str | None = None


class DataFreshness(BaseModel):
    """How fresh each data source is."""

    irc_tcc_latest: datetime | None = Field(
        None, description="Most recent IRC TCC snapshot timestamp"
    )
    orc_latest: datetime | None = Field(
        None, description="Most recent ORC certificate timestamp"
    )
    certificates_latest: datetime | None = Field(
        None, description="Most recent parsed IRC certificate timestamp"
    )
    race_results_latest: datetime | None = Field(
        None, description="Most recent race result timestamp"
    )
    recent_ingestions: list[IngestionEntry] = Field(
        default_factory=list,
        description="Last few ingestion runs",
    )


class HealthResponse(BaseModel):
    """API health check response."""

    status: str = Field("ok", description="'ok' or 'degraded'")
    version: str | None = None
    boat_count: int = 0
    snapshot_count: int = 0
    certificate_count: int = 0
    orc_certificate_count: int = 0
    race_result_count: int = 0
    freshness: DataFreshness | None = None
