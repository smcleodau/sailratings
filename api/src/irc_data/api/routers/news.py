"""News ingestion + entity-extraction workflow API (SPEC-14 Sec. 4).

Endpoints:
- POST /v1/news/ingest        - ingest an article (optionally process immediately)
- GET  /v1/news                - list articles with their extraction status
- GET  /v1/news/{news_id}     - a single article + its extracted entities
- GET  /v1/news/{news_id}/mentions - the boat mentions linked to an article
- GET  /v1/boats/{boat_id}/news - news mentioning a canonical boat
- POST /v1/news/process-pending - run the workflow over pending articles
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.schemas.news import (
    BoatNewsMentionOut,
    NewsArticleOut,
    NewsIngestRequest,
    NewsIngestResponse,
)
from irc_data.news.workflow import ingest_news, process_news_article, process_pending

router = APIRouter(prefix="/news", tags=["News"])


@router.post("/ingest", response_model=NewsIngestResponse)
def ingest(req: NewsIngestRequest, engine: Engine = Depends(get_db)) -> NewsIngestResponse:
    """Ingest a news article into raw_news (and optionally run the workflow)."""
    news_id = ingest_news(
        engine,
        source=req.source,
        title=req.title,
        body_text=req.body_text,
        source_url=req.source_url,
        author=req.author,
        published_at=req.published_at,
    )

    if not req.process:
        return NewsIngestResponse(news_id=news_id, processing_status="pending")

    outcome = process_news_article(engine, news_id)

    return NewsIngestResponse(
        news_id=news_id,
        processing_status=outcome.status,
        extraction=outcome.extraction.to_json_dict() if outcome.extraction else None,
        matches=[asdict(m) for m in outcome.matches],
        error=outcome.error,
    )


def _article_row_to_out(row) -> NewsArticleOut:
    boats = row.mentioned_boats or []
    if isinstance(boats, dict):
        boats = boats.get("mentioned_boats", [])
    return NewsArticleOut(
        id=row.id,
        source=row.source,
        source_url=row.source_url,
        title=row.title,
        author=row.author,
        published_at=row.published_at,
        ingested_at=row.ingested_at,
        processing_status=row.processing_status,
        processed_at=row.processed_at,
        mentioned_boats=boats,
        mentioned_events=row.mentioned_events or [],
        sentiment=row.sentiment,
        key_takeaway=row.key_takeaway,
        extraction_model=row.extraction_model,
        error_message=row.error_message,
    )


@router.get("")
def list_news(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by processing_status"),
    engine: Engine = Depends(get_db),
):
    """List raw_news articles with their extraction status."""
    conditions = []
    params: dict = {"limit": limit, "offset": offset}
    if status:
        conditions.append("processing_status = :status")
        params["status"] = status
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = text(f"""
        SELECT id, source, source_url, title, author, published_at, ingested_at,
               processing_status, processed_at,
               (extracted_entities -> 'mentioned_boats') AS mentioned_boats,
               mentioned_events, sentiment, key_takeaway,
               extraction_model, error_message
        FROM raw_news
        {where}
        ORDER BY ingested_at DESC
        LIMIT :limit OFFSET :offset
    """)
    count_sql = text(f"SELECT COUNT(*) FROM raw_news {where}")

    with engine.connect() as conn:
        total = conn.execute(count_sql, params).scalar()
        rows = conn.execute(sql, params).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [_article_row_to_out(r).model_dump(mode="json") for r in rows],
    }


@router.get("/{news_id}", response_model=NewsArticleOut)
def get_news(news_id: int, engine: Engine = Depends(get_db)) -> NewsArticleOut:
    sql = text("""
        SELECT id, source, source_url, title, author, published_at, ingested_at,
               processing_status, processed_at,
               (extracted_entities -> 'mentioned_boats') AS mentioned_boats,
               mentioned_events, sentiment, key_takeaway,
               extraction_model, error_message
        FROM raw_news WHERE id = :id
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"id": news_id}).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"news article {news_id} not found")
    return _article_row_to_out(row)


@router.get("/{news_id}/mentions", response_model=list[BoatNewsMentionOut])
def get_news_mentions(news_id: int, engine: Engine = Depends(get_db)) -> list[BoatNewsMentionOut]:
    sql = text("""
        SELECT m.news_id, m.boat_id, b.boat_name, m.mention_text,
               m.match_method, m.match_confidence
        FROM boat_news_mentions m
        LEFT JOIN boats b ON b.id = m.boat_id
        WHERE m.news_id = :id
        ORDER BY m.match_confidence DESC NULLS LAST
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"id": news_id}).fetchall()
    return [
        BoatNewsMentionOut(
            news_id=r.news_id,
            boat_id=r.boat_id,
            boat_name=r.boat_name,
            mention_text=r.mention_text,
            match_method=r.match_method,
            match_confidence=float(r.match_confidence) if r.match_confidence is not None else None,
        )
        for r in rows
    ]


@router.post("/process-pending")
def process_pending_route(
    limit: int = Query(50, ge=1, le=500), engine: Engine = Depends(get_db)
):
    """Run the extraction + linkage workflow over pending articles."""
    outcomes = process_pending(engine, limit=limit)
    return {
        "processed": len(outcomes),
        "ok": sum(1 for o in outcomes if o.status == "processed"),
        "failed": sum(1 for o in outcomes if o.status == "failed"),
        "mentions_linked": sum(len(o.matches) for o in outcomes),
        "results": [o.to_dict() for o in outcomes],
    }


# --- Boat-centric news view (mounted on the boats router prefix too) --------


def boat_news_for(engine: Engine, boat_id: int, limit: int) -> list[dict]:
    sql = text("""
        SELECT n.id AS news_id, n.source, n.source_url, n.title, n.published_at,
               n.sentiment, n.key_takeaway, m.mention_text, m.match_method,
               m.match_confidence
        FROM boat_news_mentions m
        JOIN raw_news n ON n.id = m.news_id
        WHERE m.boat_id = :boat_id
        ORDER BY COALESCE(n.published_at, n.ingested_at) DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"boat_id": boat_id, "limit": limit}).fetchall()
    return [
        {
            "news_id": r.news_id,
            "source": r.source,
            "source_url": r.source_url,
            "title": r.title,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "sentiment": r.sentiment,
            "key_takeaway": r.key_takeaway,
            "mention_text": r.mention_text,
            "match_method": r.match_method,
            "match_confidence": float(r.match_confidence) if r.match_confidence is not None else None,
        }
        for r in rows
    ]
