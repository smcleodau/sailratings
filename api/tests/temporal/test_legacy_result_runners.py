"""The DP-01 results runners in legacy_adapters persist what they scrape.

isora / rhkyc / sailracehq / rorc / sydney-hobart / cowesweek / yachtscoring
used to scrape every event and return ``records_written: 0`` without storing
anything. These tests drive each registered runner against a fake source and
an in-memory race_results store (no network, no DB) and check that rows are
persisted under the CLI's source value, that ``records_written`` counts only
genuinely new rows, and that the ledger ``since`` (14-day lookback) reaches
the scraper.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import irc_data.db.connection as connection
import irc_data.db.operations as ops
import irc_data.scrapers.result_import as ri
from irc_data.scrapers.result_base import EventRef, NormalizedResult
from irc_data.temporal import legacy_adapters
from irc_data.temporal.legacy_adapters import adapter_for_slug

# register slug -> (scraper module, class name, race_results.source, stores class_name)
RUNNERS = {
    "isora": ("irc_data.scrapers.isora", "ISORASource", "isora", True),
    "rhkyc": ("irc_data.scrapers.legacy.rhkyc", "RHKYCSource", "rhkyc", True),
    "sailracehq": ("irc_data.scrapers.sailracehq", "SailRaceHQSource", "sailracehq", False),
    # rorc stores class_name (boat type) like its archive rows: part of the result identity.
    "rorc": ("irc_data.scrapers.rorc", "RORCSource", "rorc", True),
    "sydney-hobart": ("irc_data.scrapers.sydneyhobart", "SydneyHobartSource", "sydneyhobart", False),
    "cowesweek": ("irc_data.scrapers.cowesweek", "CowesWeekSource", "cowesweek", True),
    "yachtscoring": ("irc_data.scrapers.yachtscoring", "YachtScoringSource", "yachtscoring", True),
}

SINCE = date(2026, 10, 20)


def _rows(event: str, n: int, day: int) -> list[NormalizedResult]:
    return [
        NormalizedResult(
            boat_name=f"Boat {i}",
            sail_number=f"GBR {100 + i}" if i % 2 == 0 else None,
            event_name=event,
            event_date=date(2026, 10, day),
            race_name="Race 1",
            organizing_club="Club",
            class_name="IRC 1",
            place=i + 1,
            fleet_size=n,
            rating_type="irc_tcc",
            rating_value=Decimal("1.0") + Decimal(i) / 100,
            raw_data={"x": i},
        )
        for i in range(n)
    ]


class FakeSource:
    """Two events with results, one empty, recording the ``since`` it was given."""

    seen_since: list = []
    fail_events: set = set()

    def __init__(self, *a, **k):
        pass

    async def discover_events(self, since=None):
        FakeSource.seen_since.append(since)
        return [
            EventRef(source="fake", event_name=name, event_url=f"https://example.test/{name}")
            for name in ("Autumn Regatta", "Empty Event", "Club Series")
        ]

    async def scrape_event(self, ref):
        if ref.event_name in FakeSource.fail_events:
            raise RuntimeError("site changed")
        if ref.event_name == "Autumn Regatta":
            return _rows("Autumn Regatta", 3, 25)
        if ref.event_name == "Club Series":
            return _rows("Club Series", 2, 28)
        return []


@pytest.fixture()
def store(monkeypatch):
    """In-memory race_results keyed like upsert_race_result's dedup."""
    rows: dict[tuple, dict] = {}
    logs: list[dict] = []

    def fake_upsert(engine, boat_id, event_name, event_date=None, race_name=None, **kw):
        key = (kw["source"], event_name, (kw.get("raw_data") or {}).get("boat_name"), race_name)
        rows.setdefault(key, dict(boat_id=boat_id, event_name=event_name,
                                  event_date=event_date, race_name=race_name, **kw))
        return 1

    monkeypatch.setattr(ri, "upsert_race_result", fake_upsert)
    monkeypatch.setattr(ri, "count_source_rows",
                        lambda engine, source: sum(1 for k in rows if k[0] == source))
    monkeypatch.setattr(ri, "find_boat_by_sail_number", lambda engine, sail: 42 if sail else None)
    monkeypatch.setattr(ri, "_find_boat_by_name", lambda *a, **k: None)
    monkeypatch.setattr(ri, "log_ingestion_start", lambda *a, **k: pytest.fail("per-batch log"))
    monkeypatch.setattr(ri, "log_ingestion_end", lambda *a, **k: pytest.fail("per-batch log"))

    def log_start(engine, source, metadata=None):
        logs.append({"source": source, "metadata": metadata})
        return len(logs)

    def log_end(engine, log_id, **kw):
        logs[log_id - 1].update(kw)

    monkeypatch.setattr(ops, "log_ingestion_start", log_start)
    monkeypatch.setattr(ops, "log_ingestion_end", log_end)
    monkeypatch.setattr(connection, "get_engine", lambda: object())

    since_calls: list = []

    def fake_since(slug, lookback_days=1):
        since_calls.append((slug, lookback_days))
        return SINCE

    monkeypatch.setattr(legacy_adapters, "_last_successful_run_since", fake_since)
    FakeSource.seen_since = []
    FakeSource.fail_events = set()
    return {"rows": rows, "logs": logs, "since_calls": since_calls}


def _patch_source(monkeypatch, slug):
    import importlib

    module, cls, _, _ = RUNNERS[slug]
    monkeypatch.setattr(importlib.import_module(module), cls, FakeSource)


@pytest.mark.parametrize("slug", sorted(RUNNERS))
def test_runner_persists_and_reports_real_counts(slug, store, monkeypatch):
    _patch_source(monkeypatch, slug)
    source, with_class = RUNNERS[slug][2], RUNNERS[slug][3]

    out = adapter_for_slug(slug).run({"slug": slug})

    assert out["records_found"] == 5
    assert out["records_written"] == 5
    assert out["events"] == 3 and out["events_with_results"] == 2
    assert out["newest_event_date"] == "2026-10-28"
    assert out["source"] == source
    stored = list(store["rows"].values())
    assert len(stored) == 5 and {r["source"] for r in stored} == {source}
    # Same field mapping as the CLI per-row loop.
    r = next(r for r in stored if r["raw_data"]["boat_name"] == "Boat 0")
    assert r["raw_data"]["sail_number"] == "GBR 100" and r["boat_id"] == 42
    assert r["rating_type"] == "irc_tcc" and r["organizing_club"] == "Club"
    assert ("class_name" in r) is with_class
    # Incremental window: ledger since with the 14-day lookback reaches the scraper.
    assert store["since_calls"] == [(slug, 14)]
    assert FakeSource.seen_since == [SINCE]
    assert out["since"] == "2026-10-20"
    # One ingestion_log row per run, with the real new-row count.
    assert len(store["logs"]) == 1
    assert store["logs"][0]["source"] == source
    assert store["logs"][0]["records_new"] == 5 and store["logs"][0]["status"] == "completed"


@pytest.mark.parametrize("slug", sorted(RUNNERS))
def test_rerun_is_idempotent_and_writes_nothing(slug, store, monkeypatch):
    _patch_source(monkeypatch, slug)
    adapter_for_slug(slug).run({"slug": slug})
    again = adapter_for_slug(slug).run({"slug": slug})
    assert again["records_found"] == 5
    assert again["records_written"] == 0
    assert len(store["rows"]) == 5


def test_record_since_overrides_ledger(store, monkeypatch):
    _patch_source(monkeypatch, "isora")
    out = adapter_for_slug("isora").run({"slug": "isora", "since": "2026-01-01"})
    assert FakeSource.seen_since == [date(2026, 1, 1)]
    assert store["since_calls"] == []
    assert out["since"] == "2026-01-01"


def test_one_failing_event_keeps_the_rest(store, monkeypatch):
    _patch_source(monkeypatch, "rhkyc")
    FakeSource.fail_events = {"Autumn Regatta"}
    out = adapter_for_slug("rhkyc").run({"slug": "rhkyc"})
    assert out["records_written"] == 2 and out["event_errors"] == 1
    assert store["logs"][0]["status"] == "completed_with_errors"
    assert "site changed" in store["logs"][0]["error_message"]


def test_all_events_failing_fails_the_run(store, monkeypatch):
    _patch_source(monkeypatch, "cowesweek")
    FakeSource.fail_events = {"Autumn Regatta", "Empty Event", "Club Series"}
    with pytest.raises(RuntimeError, match="all 3 events failed"):
        adapter_for_slug("cowesweek").run({"slug": "cowesweek"})
    assert store["logs"][0]["status"] == "failed"


def test_store_normalized_results_counts_new_rows_and_logs(monkeypatch):
    """The shared CLI/adapter helper: written = row delta, imported = upserts."""
    counts = iter([10, 12])
    logged = {}
    monkeypatch.setattr(ri, "count_source_rows", lambda engine, source: next(counts))
    monkeypatch.setattr(ri, "upsert_race_result", lambda engine, **kw: 1)
    monkeypatch.setattr(ri, "find_boat_by_sail_number", lambda *a: None)
    monkeypatch.setattr(ri, "_find_boat_by_name", lambda *a, **k: None)
    monkeypatch.setattr(ri, "log_ingestion_start", lambda engine, source: logged.setdefault("source", source) and 7)
    monkeypatch.setattr(ri, "log_ingestion_end", lambda engine, log_id, **kw: logged.update(kw))

    stats = ri.store_normalized_results(object(), _rows("E", 3, 1), source="rorc")
    assert stats["imported"] == 3 and stats["written"] == 2 and stats["errors"] == 0
    assert logged["source"] == "rorc" and logged["records_new"] == 2


def test_cert_parse_persists_via_shared_ingest_and_counts_rows(monkeypatch):
    """irc-certs parse half used to discard every parsed certificate, then
    counted a ``certificates`` table that no longer exists (renamed to
    ``irc_certificates`` in 0012). It now runs the shared ingest."""
    import irc_data.parsers.certificate_ingest as ingest

    calls = {}

    def _fake_ingest(engine, paths, *, incremental, log):
        calls["incremental"] = incremental
        calls["paths"] = list(paths)
        return ingest.IngestStats(parsed=5, inserted=3, updated=1, linked_after=2)

    monkeypatch.setattr(connection, "get_engine", lambda: object())
    monkeypatch.setattr(ingest, "default_certificate_paths", lambda: ["a.pdf", "b.pdf"])
    monkeypatch.setattr(ingest, "ingest_certificates", _fake_ingest)
    out = legacy_adapters._run_cert_parse({})
    assert calls == {"incremental": True, "paths": ["a.pdf", "b.pdf"]}
    assert out["records_written"] == 4
    assert out["inserted"] == 3 and out["updated"] == 1 and out["linked"] == 2
    assert out["records_found"] == 2


def test_cert_chain_still_parses_when_discovery_fails(monkeypatch):
    parsed = []
    monkeypatch.setattr(legacy_adapters, "_run_cert_discovery", lambda r: (_ for _ in ()).throw(RuntimeError("site down")))
    monkeypatch.setattr(legacy_adapters, "_run_cert_parse", lambda r: parsed.append(1) or {"records_written": 0})
    import pytest

    with pytest.raises(RuntimeError):
        legacy_adapters._chain_cert_discovery_and_parse({})
    assert parsed == [1]
