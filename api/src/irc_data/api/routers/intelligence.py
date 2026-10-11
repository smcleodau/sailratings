"""GET /v1/intelligence/* - versioned queries with provenance (IN-01-02, SPEC-25 §2).

Every route returns an :class:`IntelligenceAnswerV1`. Missing inputs or
unsupported boats are *200 with incomplete=true* (never a bare 404); only an
unregistered query name/version is a 404.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.schemas.intelligence import IntelligenceAnswerV1
from irc_data.api.services.intelligence import REGISTRY, UnknownQuery, answer_query

router = APIRouter(prefix="/intelligence", tags=["Intelligence"])


@router.get("/queries")
def list_queries() -> dict:
    """The registry of named, versioned queries."""
    return {
        "queries": [
            {"query": s.key, "description": s.description, "required": list(s.required)}
            for s in REGISTRY.values()
        ]
    }


@router.get("/query/{query_key}", response_model=IntelligenceAnswerV1)
def run_named_query(query_key: str, request: Request, engine: Engine = Depends(get_db)):
    """Run any registered query by ``<name>.<version>``; params come from the query string."""
    try:
        return answer_query(engine, query_key, dict(request.query_params))
    except UnknownQuery:
        raise HTTPException(status_code=404, detail=f"Unknown query '{query_key}'")


@router.get("/boats/{boat_id}", response_model=IntelligenceAnswerV1)
def intelligence_boat(boat_id: int, engine: Engine = Depends(get_db)):
    return answer_query(engine, "boat_profile.v1", {"boat_id": boat_id})


@router.get("/search", response_model=IntelligenceAnswerV1)
def intelligence_search(
    q: str = Query("", description="Free-text boat name or sail number"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    return answer_query(engine, "rating_book_search.v1", {"q": q, "limit": limit, "offset": offset})


@router.get("/fleet/{design:path}", response_model=IntelligenceAnswerV1)
def intelligence_fleet(
    design: str,
    sort: str = Query("tcc", pattern="^(tcc|name|country)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    engine: Engine = Depends(get_db),
):
    return answer_query(
        engine, "fleet_explorer.v1", {"design": design, "sort": sort, "limit": limit, "offset": offset}
    )


@router.get("/report-facts/{boat_id}", response_model=IntelligenceAnswerV1)
def intelligence_report_facts(boat_id: int, engine: Engine = Depends(get_db)):
    return answer_query(engine, "report_facts.v1", {"boat_id": boat_id})
