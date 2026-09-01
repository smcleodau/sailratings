"""Pydantic schemas for the Lineage API (DP-02-05)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LineageNodeSchema(BaseModel):
    """A single node in the lineage chain."""

    node_id: str
    node_type: str = Field(
        ...,
        description="canonical_field | transformed_value | extracted_span | raw_artifact",
    )
    entity: str = ""
    field: str | None = None
    value: str | None = None
    record_id: int | None = None
    source_slug: str | None = None
    content_hash: str | None = None
    url: str | None = None
    fetched_at: str | None = None
    model_version: str | None = None
    rule_version: str | None = None
    confidence: float | None = None
    span_text: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    observed_at: str | None = None


class LineageEdgeSchema(BaseModel):
    """A directed edge in the lineage chain."""

    from_node: str
    to_node: str
    edge_type: str = Field(
        ...,
        description="transformed_to | derived_from | extracted_from | merged_from | adjudicated_from",
    )
    confidence: float | None = None


class LineageGraphSchema(BaseModel):
    """DP-02-05 output contract — field-level lineage graph."""

    canonical_field: str
    canonical_value: str | None = None
    record_id: int | None = None
    nodes: list[LineageNodeSchema] = Field(default_factory=list)
    edges: list[LineageEdgeSchema] = Field(default_factory=list)
    citation: str = ""
    complete: bool = False
    missing_edges: list[str] = Field(default_factory=list)
    schema_version: str = "1"
    generated_at: str = ""
