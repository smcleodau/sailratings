"""Tests for the LLM news entity-extraction workflow (SPEC-14 Sec. 4).

Covers:
- Extraction JSON parsing / validation / sentiment normalization (pure).
- Fuzzy + exact boat-mention linking against boats + boat_identities (DB).
- End-to-end workflow (ingest -> extract -> link) against a throwaway DB,
  using a deterministic extractor that implements the real contract.

The deterministic extractor is justified: the live LLM call is
non-deterministic, costs money, and needs network + an API key. All other code
paths (parse, SQL matching, workflow orchestration, DB writes) run for real.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from irc_data.news.extraction import (
    ExtractionResult,
    NewsExtraction,
    parse_extraction,
)
from irc_data.news.linking import resolve_mentions, link_article_mentions
from irc_data.news.workflow import (
    ingest_news,
    process_news_article,
    process_pending,
)


# ---------------------------------------------------------------------------
# Pure extraction tests
# ---------------------------------------------------------------------------


def test_parse_spec_example():
    """The exact extraction schema example from SPEC-14 Sec. 4."""
    raw = (
        '{"mentioned_boats": ["WOXI", "Comanche"], '
        '"mentioned_events": ["Rolex Sydney Hobart"], '
        '"sentiment": "positive", '
        '"key_takeaway": "Comanche breaks race record."}'
    )
    e = parse_extraction(raw)
    assert e.mentioned_boats == ["WOXI", "Comanche"]
    assert e.mentioned_events == ["Rolex Sydney Hobart"]
    assert e.sentiment == "positive"
    assert e.key_takeaway == "Comanche breaks race record."


def test_parse_strips_markdown_fences_and_prose():
    raw = (
        'Here is the JSON:\n```json\n{"mentioned_boats": ["Black Jack"], '
        '"mentioned_events": [], "sentiment": "Bullish", '
        '"key_takeaway": "Won line honours."}\n```\nThanks!'
    )
    e = parse_extraction(raw)
    assert e.mentioned_boats == ["Black Jack"]
    assert e.sentiment == "positive"  # "Bullish" normalised
    assert e.key_takeaway == "Won line honours."


def test_parse_handles_object_style_boats():
    """LLMs sometimes return objects; we coerce to boat-name strings."""
    raw = '{"mentioned_boats": [{"boat_name": "Comanche"}, {"name": "Wild Oats XI"}]}'
    e = parse_extraction(raw)
    assert e.mentioned_boats == ["Comanche", "Wild Oats XI"]


def test_parse_empty_object_is_neutral():
    e = parse_extraction("{}")
    assert e.mentioned_boats == []
    assert e.mentioned_events == []
    assert e.sentiment == "neutral"
    assert e.key_takeaway == ""


def test_parse_sentiment_variants_normalize():
    assert NewsExtraction(sentiment="negative").sentiment == "negative"
    assert NewsExtraction(sentiment="bad").sentiment == "negative"
    assert NewsExtraction(sentiment="unfavourable").sentiment == "negative"
    assert NewsExtraction(sentiment="good").sentiment == "positive"
    assert NewsExtraction(sentiment=None).sentiment == "neutral"


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_extraction("not json at all")
    with pytest.raises(ValueError):
        parse_extraction("")


def test_parse_rejects_garbage_json_object():
    with pytest.raises(ValueError):
        parse_extraction("{this is not valid json}")


def test_extract_with_llm_returns_error_when_unconfigured(monkeypatch):
    """Real code path: with no provider key, extraction fails gracefully
    (RuntimeError captured into ExtractionResult.error) rather than raising."""
    from irc_data.news import extraction

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setenv("SAILING_LLM_PROVIDER", "anthropic")

    result = extraction.extract_with_llm("Title", "body")
    assert not result.succeeded
    assert result.error is not None
    assert "ANTHROPIC_API_KEY" in result.error
    # A failed extraction still yields a (defaulted) NewsExtraction object.
    assert isinstance(result.extraction, NewsExtraction)


# ---------------------------------------------------------------------------
# Deterministic extractor used by the integration tests
# ---------------------------------------------------------------------------


def _make_extractor(boats: list[str], *, events=None, sentiment="positive",
                    takeaway="A record fell."):
    """Return a real extractor function implementing the contract."""
    def _extract(title: str, body_text: str) -> ExtractionResult:
        ex = NewsExtraction(
            mentioned_boats=list(boats),
            mentioned_events=list(events or []),
            sentiment=sentiment,
            key_takeaway=takeaway,
        )
        return ExtractionResult(extraction=ex, model="test-extractor")
    return _extract


# ---------------------------------------------------------------------------
# Linking tests (DB)
# ---------------------------------------------------------------------------


def test_link_exact_sail_number(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    matches = resolve_mentions(engine, ["AUS 1"])
    assert len(matches) == 1
    assert matches[0].boat_id == 9000001
    assert matches[0].match_method == "sail_exact"
    assert matches[0].match_confidence == 1.0


def test_link_exact_boat_name(engine, seed_boat):
    seed_boat("Wild Oats XI", "AUS1004", boat_id=9000002)
    matches = resolve_mentions(engine, ["Wild Oats XI"])
    assert len(matches) == 1
    assert matches[0].boat_id == 9000002
    assert matches[0].match_method == "name_exact"


def test_link_identity_name_not_on_boat_row(engine, seed_boat):
    """A boat known only by an identity name (not boats.boat_name) still links."""
    # boats.boat_name differs, but boat_identities carries the historical name.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO boats (id, boat_name, sail_number) VALUES (:id,'X','ZZ9999')"
        ), {"id": 9000003})
        conn.execute(text(
            "INSERT INTO boat_identities (boat_id, boat_name, source) "
            "VALUES (:id, 'Black Jack', 'test')"
        ), {"id": 9000003})
    matches = resolve_mentions(engine, ["Black Jack"])
    assert len(matches) == 1
    assert matches[0].boat_id == 9000003
    assert matches[0].match_method == "name_exact"


def test_link_fuzzy_trigram(engine, seed_boat):
    """A near-miss spelling resolves via pg_trgm similarity."""
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    matches = resolve_mentions(engine, ["Commanche"])  # extra 'h'
    assert len(matches) == 1
    assert matches[0].boat_id == 9000001
    assert matches[0].match_method == "trigram"
    assert matches[0].match_confidence >= 0.55


def test_link_no_match_for_generic_word(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    # "Rolex" is a brand, not a boat; nothing should match.
    matches = resolve_mentions(engine, ["Rolex"])
    assert matches == []


def test_link_dedupes_repeated_mentions(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    matches = resolve_mentions(engine, ["Comanche", "comanche", "AUS1"])
    boat_ids = {m.boat_id for m in matches}
    assert boat_ids == {9000001}


# ---------------------------------------------------------------------------
# End-to-end workflow tests (DB)
# ---------------------------------------------------------------------------


def test_ingest_is_idempotent_on_url(engine):
    nid1 = ingest_news(
        engine, source="scuttlebutt", title="Race Report",
        body_text="Comanche won.", source_url="https://example.com/a",
    )
    nid2 = ingest_news(
        engine, source="scuttlebutt", title="Race Report",
        body_text="Comanche won.", source_url="https://example.com/a",
    )
    assert nid1 == nid2


def test_process_news_article_full_pipeline(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    seed_boat("Wild Oats XI", "AUS1004", boat_id=9000002)

    nid = ingest_news(
        engine, source="scuttlebutt",
        title="Comanche and Wild Oats XI race record",
        body_text="Comanche broke the race record ahead of Wild Oats XI.",
        source_url="https://example.com/record",
    )

    extractor = _make_extractor(["Comanche", "Wild Oats XI"],
                                 events=["Rolex Sydney Hobart"],
                                 takeaway="Comanche breaks race record.")
    outcome = process_news_article(engine, nid, extractor=extractor)

    assert outcome.status == "processed"
    assert outcome.error is None
    assert outcome.extraction.mentioned_boats == ["Comanche", "Wild Oats XI"]
    matched_ids = {m.boat_id for m in outcome.matches}
    assert matched_ids == {9000001, 9000002}

    # Extraction persisted on raw_news.
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT processing_status, sentiment, key_takeaway, mentioned_events "
            "FROM raw_news WHERE id = :id"
        ), {"id": nid}).first()
    assert row.processing_status == "processed"
    assert row.sentiment == "positive"
    assert row.key_takeaway == "Comanche breaks race record."
    assert "Rolex Sydney Hobart" in row.mentioned_events

    # boat_news_mentions rows written.
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM boat_news_mentions WHERE news_id = :id"
        ), {"id": nid}).scalar()
    assert n == 2


def test_process_pending_batch_processes_multiple(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    for i in range(3):
        ingest_news(
            engine, source="scuttlebutt", title=f"Report {i}",
            body_text="Comanche raced.", source_url=f"https://example.com/{i}",
        )
    outcomes = process_pending(engine, extractor=_make_extractor(["Comanche"]))
    assert len(outcomes) == 3
    assert all(o.status == "processed" for o in outcomes)
    # Each article linked to the one canonical boat.
    assert all(len(o.matches) == 1 for o in outcomes)


def test_failed_extraction_marks_article_failed(engine, seed_boat):
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    nid = ingest_news(
        engine, source="scuttlebutt", title="Bad Article",
        body_text="...", source_url="https://example.com/bad",
    )

    def failing_extractor(title, body_text):
        return ExtractionResult(
            extraction=NewsExtraction(), model="test", error="LLM blew up"
        )

    outcome = process_news_article(engine, nid, extractor=failing_extractor)
    assert outcome.status == "failed"
    assert outcome.error == "LLM blew up"

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT processing_status, error_message FROM raw_news WHERE id = :id"
        ), {"id": nid}).first()
    assert row.processing_status == "failed"
    assert "LLM blew up" in row.error_message

    # No mentions should have been written for a failed extraction.
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM boat_news_mentions WHERE news_id = :id"
        ), {"id": nid}).scalar()
    assert n == 0


def test_mention_with_no_boats_links_nothing(engine):
    """An article that extracts zero boats is processed with zero mentions."""
    nid = ingest_news(
        engine, source="scuttlebutt", title="Weather Report",
        body_text="Winds were light.", source_url="https://example.com/wx",
    )
    outcome = process_news_article(engine, nid, extractor=_make_extractor([]))
    assert outcome.status == "processed"
    assert outcome.matches == []


def test_link_article_mentions_upsert_is_idempotent(engine, seed_boat):
    """Re-running linkage for the same article does not duplicate mentions."""
    seed_boat("Comanche", "AUS1", boat_id=9000001)
    nid = ingest_news(
        engine, source="scuttlebutt", title="Report",
        body_text="Comanche.", source_url="https://example.com/up",
    )
    link_article_mentions(engine, nid, ["Comanche"])
    link_article_mentions(engine, nid, ["Comanche"])
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM boat_news_mentions WHERE news_id = :id"
        ), {"id": nid}).scalar()
    assert n == 1
