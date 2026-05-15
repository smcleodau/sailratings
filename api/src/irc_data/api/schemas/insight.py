"""LLM insight API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class InsightRequest(BaseModel):
    """Request body for generating an AI insight about a boat or fleet."""

    boat_id: int | None = Field(None, description="Target boat (omit for fleet-level queries)")
    query: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Natural language question about the boat or fleet",
    )


class InsightEvent(BaseModel):
    """Streamed or cached AI-generated insight response."""

    boat_id: int | None = None
    query: str
    response: str = Field(..., description="Markdown-formatted insight text")
    model: str | None = Field(None, description="LLM model used to generate the response")
    cached: bool = Field(False, description="True if served from cache")
    generated_at: datetime | None = None
