"""Lineage API router — field-level lineage queries (DP-02-05).

Answers the question "where did every displayed fact come from?" by
tracing a canonical field through transformations to the extracted
span and raw artifact.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from irc_data.api.schemas.lineage import LineageGraphSchema
from irc_data.lineage.service import LineageService

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get(
    "/{entity}/{field}",
    response_model=LineageGraphSchema,
    summary="Get field-level lineage",
    description=(
        "Trace a canonical field through transformations to the extracted "
        "span and raw artifact, including model/rule version and confidence. "
        "Returns a complete acyclic chain or explicitly reports missing edges."
    ),
)
def get_lineage(
    entity: str,
    field: str,
    record_id: int = Query(..., description="Primary key of the canonical record"),
):
    """Build and return the lineage graph for a single canonical field."""
    service = LineageService()
    graph = service.get_lineage(entity, field, record_id)
    return graph.to_dict()


@router.get(
    "/",
    response_model=LineageGraphSchema,
    summary="Get field-level lineage (query-param form)",
    include_in_schema=False,
)
def get_lineage_query(
    entity: str = Query(..., description="Entity table name"),
    field: str = Query(..., description="Field name"),
    record_id: int = Query(..., description="Primary key of the canonical record"),
):
    """Alternative entry point using query parameters."""
    service = LineageService()
    graph = service.get_lineage(entity, field, record_id)
    return graph.to_dict()
