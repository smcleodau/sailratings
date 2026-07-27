"""News entity-extraction workflow (SPEC-14 Sec. 4).

Orchestrates the three steps prescribed by the spec:

1. **Ingest** - a news article is temporarily stored in the unstructured
   ``raw_news`` table.
2. **Extract** - a workflow passes the article text to the Sailing LLM, which
   returns the strict extraction schema (mentioned_boats, mentioned_events,
   sentiment, key_takeaway).
3. **Link** - the extracted ``mentioned_boats`` are cross-referenced against
   ``boats`` + ``boat_identities`` (fuzzy) and linked in
   ``boat_news_mentions``.

Temporal mapping
----------------
Each step is implemented as a standalone activity-style function so it can be
composed by a Temporal workflow (matching the pattern in
``irc_data.temporal.workflows`` / ``scrape_activities``). The synchronous
``process_news_article`` below is the equivalent of one workflow run for a
single article and is what the CLI / API call directly when Temporal is not
provisioned.

The extraction step is injectable (``extractor`` argument) so deterministic
tests can run the full pipeline without an LLM call. Production callers leave
it as ``None`` to use the configured Sailing LLM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.news.extraction import (
    ExtractionResult,
    NewsExtraction,
    extract_with_llm,
)
from irc_data.news.linking import BoatMatch, link_article_mentions

# Lifecycle status values stored on raw_news.processing_status.
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"


@dataclass
class ProcessOutcome:
    """Result of processing a single raw_news article."""

    news_id: int
    status: str
    extraction: NewsExtraction | None = None
    model: str | None = None
    matches: list[BoatMatch] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "news_id": self.news_id,
            "status": self.status,
            "matches": [asdict(m) if hasattr(m, "__dataclass_fields__") else m for m in self.matches],
            "model": self.model,
            "error": self.error,
        }
        if self.extraction is not None:
            d["extraction"] = self.extraction.to_json_dict()
        return d


# ---------------------------------------------------------------------------
# Activity 1: ingest
# ---------------------------------------------------------------------------


def ingest_news(
    engine: Engine,
    *,
    source: str,
    title: str,
    body_text: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    published_at: datetime | None = None,
    raw_data: dict | None = None,
) -> int:
    """Insert a news article into ``raw_news`` in ``pending`` status.

    Returns the new raw_news.id. Idempotent on ``source_url``: re-ingesting the
    same URL returns the existing id without creating a duplicate.
    """
    if source_url:
        with engine.connect() as conn:
            existing = conn.execute(
                text("SELECT id FROM raw_news WHERE source_url = :url"),
                {"url": source_url},
            ).first()
        if existing:
            return int(existing[0])

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO raw_news
                    (source, source_url, title, body_text, author,
                     published_at, processing_status, raw_data)
                VALUES
                    (:source, :source_url, :title, :body_text, :author,
                     :published_at, :status, CAST(:raw_data AS JSONB))
                RETURNING id
                """
            ),
            {
                "source": source,
                "source_url": source_url,
                "title": (title or "Untitled")[:1000],
                "body_text": body_text,
                "author": author,
                "published_at": published_at,
                "status": STATUS_PENDING,
                "raw_data": _json(raw_data) if raw_data is not None else None,
            },
        ).first()
    return int(row[0])


# ---------------------------------------------------------------------------
# Activity 2: extract
# ---------------------------------------------------------------------------


def extract_entities(
    engine: Engine,
    news_id: int,
    *,
    extractor=None,
) -> ExtractionResult:
    """Load an article and run the Sailing LLM extraction.

    ``extractor`` is an optional callable ``(title, body_text) -> ExtractionResult``
    for deterministic tests; defaults to the configured LLM provider.
    Marks the article ``processing`` for the duration.
    """
    row = _fetch_article(engine, news_id)
    if row is None:
        raise LookupError(f"raw_news id={news_id} not found")

    _set_status(engine, news_id, STATUS_PROCESSING)

    if extractor is None:
        result = extract_with_llm(row.title, row.body_text)
    else:
        result = extractor(row.title, row.body_text)

    persist_extraction(engine, news_id, result)
    return result


def _fetch_article(engine: Engine, news_id: int):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT title, body_text FROM raw_news WHERE id = :id"),
            {"id": news_id},
        ).first()


def persist_extraction(engine: Engine, news_id: int, result: ExtractionResult) -> None:
    """Store an ExtractionResult (success or failure) on the raw_news row."""
    ex = result.extraction
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE raw_news
                SET extracted_entities = CAST(:entities AS JSONB),
                    mentioned_events   = CAST(:events AS JSONB),
                    sentiment          = :sentiment,
                    key_takeaway       = :key_takeaway,
                    extraction_model   = :model,
                    error_message      = :error
                WHERE id = :id
            """
        ),
        {
            "id": news_id,
            "entities": _json(ex.to_json_dict()) if ex else None,
            "events": _json(ex.mentioned_events) if ex else None,
            "sentiment": ex.sentiment if ex else None,
            "key_takeaway": ex.key_takeaway if ex else None,
            "model": result.model,
            "error": result.error,
        },
    )


# ---------------------------------------------------------------------------
# Activity 3: link
# ---------------------------------------------------------------------------


def link_mentions(
    engine: Engine, news_id: int, mentioned_boats: list[str]
) -> list[BoatMatch]:
    """Resolve extracted boat mentions to canonical boats and persist links."""
    return link_article_mentions(engine, news_id, mentioned_boats)


# ---------------------------------------------------------------------------
# Workflow: process one article (extract -> link)
# ---------------------------------------------------------------------------


def process_news_article(
    engine: Engine, news_id: int, *, extractor=None
) -> ProcessOutcome:
    """Run the full extraction+linkage workflow for one article.

    On extraction failure the article is marked ``failed`` and the linking
    step is skipped. On success it is marked ``processed``.
    """
    result = extract_entities(engine, news_id, extractor=extractor)

    if not result.succeeded:
        _set_status(engine, news_id, STATUS_FAILED)
        return ProcessOutcome(
            news_id=news_id,
            status=STATUS_FAILED,
            extraction=result.extraction,
            model=result.model,
            error=result.error,
        )

    matches = link_mentions(engine, news_id, result.extraction.mentioned_boats)
    _set_status(engine, news_id, STATUS_PROCESSED)
    return ProcessOutcome(
        news_id=news_id,
        status=STATUS_PROCESSED,
        extraction=result.extraction,
        model=result.model,
        matches=matches,
    )


def process_pending(
    engine: Engine, *, limit: int = 50, extractor=None
) -> list[ProcessOutcome]:
    """Process all ``pending`` articles (oldest first).

    Each article is processed independently; a failure on one does not stop the
    batch. Returns the per-article outcomes.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id FROM raw_news
                WHERE processing_status = :pending
                ORDER BY ingested_at ASC
                LIMIT :limit
                """
            ),
            {"pending": STATUS_PENDING, "limit": limit},
        ).fetchall()
    outcomes: list[ProcessOutcome] = []
    for (nid,) in rows:
        try:
            outcomes.append(process_news_article(engine, int(nid), extractor=extractor))
        except Exception as e:  # defensive: keep the batch going
            _set_status(engine, int(nid), STATUS_FAILED, error=str(e)[:500])
            outcomes.append(
                ProcessOutcome(news_id=int(nid), status=STATUS_FAILED, error=str(e))
            )
    return outcomes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_status(
    engine: Engine, news_id: int, status: str, *, error: str | None = None
) -> None:
    """Set processing_status; stamp processed_at on terminal states."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE raw_news
                SET processing_status = :status,
                    processed_at = CASE WHEN :status IN ('processed','failed')
                                       THEN now() ELSE processed_at END,
                    error_message = COALESCE(:error, error_message)
                WHERE id = :id
                """
            ),
            {"id": news_id, "status": status, "error": error},
        )


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)
