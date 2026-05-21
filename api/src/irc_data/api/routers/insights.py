"""AI insights endpoints — streaming boat analysis via Claude."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter()


class InsightRequest(BaseModel):
    query: str


class InsightAskRequest(BaseModel):
    boat_id: int
    question: str
    detail_level: str = "free"  # "free" or "premium"
    thinking_style: str = "steps"  # "steps" (legacy) or "prose" (modal)


def _search_boats(engine: Engine, query: str) -> list[dict]:
    """Quick search for boats matching a query string."""
    with engine.connect() as conn:
        # Try exact sail number first
        result = conn.execute(text("""
            SELECT id, boat_name, sail_number, design, country
            FROM boats
            WHERE UPPER(TRIM(sail_number)) = UPPER(TRIM(:q))
            LIMIT 5
        """), {"q": query})
        exact = [dict(r._mapping) for r in result]
        if exact:
            return exact

        # Fuzzy name search
        result = conn.execute(text("""
            SELECT id, boat_name, sail_number, design, country,
                   similarity(boat_name, :q) AS score
            FROM boats
            WHERE similarity(boat_name, :q) > 0.3
            ORDER BY score DESC
            LIMIT 10
        """), {"q": query})
        return [dict(r._mapping) for r in result]


async def _sse_generator(engine: Engine, boat_id: int, question: str | None = None, detail_level: str = "free", thinking_style: str = "steps"):
    """Generate SSE events for streaming insight."""
    from irc_data.api.services.insights_service import stream_insight

    async for event in stream_insight(
        engine, boat_id, question, detail_level=detail_level, thinking_style=thinking_style
    ):
        yield f"data: {json.dumps(event)}\n\n"


@router.post("/insights")
async def create_insight(
    body: InsightRequest,
    engine: Engine = Depends(get_db),
):
    """Get AI-powered insight about a boat.

    Send a boat name, sail number, or question. If the query matches a single
    boat, streams an AI analysis. If ambiguous, returns search results.
    """
    candidates = _search_boats(engine, body.query)

    if not candidates:
        raise HTTPException(status_code=404, detail="No boats found matching query")

    if len(candidates) > 1:
        # Ambiguous — return candidates for the frontend to disambiguate
        return {
            "type": "search_results",
            "data": candidates,
            "message": f"Found {len(candidates)} boats. Please select one.",
        }

    # Single match — stream the insight
    boat_id = candidates[0]["id"]
    return StreamingResponse(
        _sse_generator(engine, boat_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/insights/ask")
async def ask_about_boat(
    body: InsightAskRequest,
    engine: Engine = Depends(get_db),
):
    """Ask a follow-up question about a specific boat.

    Requires a boat_id (from a previous search) and a question.
    Streams the AI response as SSE events.
    """
    # Verify boat exists
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM boats WHERE id = :id"), {"id": body.boat_id}
        ).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"Boat {body.boat_id} not found")

    return StreamingResponse(
        _sse_generator(
            engine,
            body.boat_id,
            body.question,
            detail_level=body.detail_level,
            thinking_style=body.thinking_style,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/insights/context/{boat_id}")
async def get_boat_context(
    boat_id: int,
    detail_level: str = Query("free", pattern="^(free|premium)$"),
    engine: Engine = Depends(get_db),
):
    """Get the raw context document for a boat (for debugging/testing).

    Returns the markdown document that would be sent to the LLM.
    Use ?detail_level=premium to include analytics sections.
    """
    from irc_data.api.services.insights_service import build_boat_context

    context = build_boat_context(engine, boat_id, detail_level=detail_level)
    if not context:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    return {"boat_id": boat_id, "detail_level": detail_level, "context": context}
