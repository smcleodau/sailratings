"""LLM news entity-extraction workflow (SPEC-14 Sec. 4).

Public entry points::

    from irc_data.news import ingest_news, process_news_article, process_pending
    from irc_data.news import resolve_mentions, link_article_mentions
    from irc_data.news import NewsExtraction, parse_extraction, extract_with_llm
"""

from irc_data.news.extraction import (
    ExtractionResult,
    ExtractorFn,
    NewsExtraction,
    extract_with_llm,
    parse_extraction,
)
from irc_data.news.linking import (
    BoatMatch,
    link_article_mentions,
    resolve_mentions,
    write_mentions,
)
from irc_data.news.workflow import (
    ProcessOutcome,
    extract_entities,
    ingest_news,
    link_mentions,
    persist_extraction,
    process_news_article,
    process_pending,
)

__all__ = [
    "BoatMatch",
    "ExtractionResult",
    "ExtractorFn",
    "NewsExtraction",
    "ProcessOutcome",
    "extract_entities",
    "extract_with_llm",
    "ingest_news",
    "link_article_mentions",
    "link_mentions",
    "parse_extraction",
    "persist_extraction",
    "process_news_article",
    "process_pending",
    "resolve_mentions",
    "write_mentions",
]
