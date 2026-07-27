"""Pydantic request/response schemas for the news API (SPEC-14 Sec. 4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NewsIngestRequest(BaseModel):
    """Body for POST /v1/news/ingest."""

    source: str = Field(..., description="Feed/domain the article came from")
    title: str
    body_text: str | None = None
    source_url: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    process: bool = Field(
        False,
        description="If true, run the extraction+linkage workflow immediately after ingest.",
    )


class NewsIngestResponse(BaseModel):
    news_id: int
    processing_status: str = "pending"
    extraction: dict[str, Any] | None = None
    matches: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class BoatNewsMentionOut(BaseModel):
    news_id: int
    boat_id: int
    boat_name: str | None = None
    mention_text: str | None = None
    match_method: str
    match_confidence: float | None = None


class NewsArticleOut(BaseModel):
    id: int
    source: str
    source_url: str | None = None
    title: str
    author: str | None = None
    published_at: datetime | None = None
    ingested_at: datetime | None = None
    processing_status: str
    processed_at: datetime | None = None
    mentioned_boats: list[str] = Field(default_factory=list)
    mentioned_events: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    key_takeaway: str | None = None
    extraction_model: str | None = None
    error_message: str | None = None
