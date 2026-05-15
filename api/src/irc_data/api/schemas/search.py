"""Search API response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from irc_data.api.schemas.boat import BoatSummary


class SearchResponse(BaseModel):
    """Paginated search results across boats."""

    query: str
    results: list[BoatSummary] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    filters_applied: dict[str, str] = Field(
        default_factory=dict,
        description="Active filters: country, design, min_tcc, max_tcc, etc.",
    )
