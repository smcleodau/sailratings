"""Scheduled runners for sailwave / warsash-spring-series / hamble-winter-series / halsail."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import irc_data.scrapers.result_import as ri
from irc_data.scrapers.result_base import NormalizedResult
from irc_data.temporal import legacy_adapters
from irc_data.temporal.legacy_adapters import adapter_for_slug

SLUGS = {
    # slug -> (module attr, seeds module, host platform slug)
    "sailwave": ("sailwave", "sailwave_seeds", "sailwave"),
    "warsash-spring-series": ("sailwave", "sailwave_seeds", "sailwave"),
    "hamble-winter-series": ("halsail", "halsail_seeds", "halsail"),
    "halsail": ("halsail", "halsail_seeds", "halsail"),
}


@pytest.fixture()
def calls(monkeypatch):
    import irc_data.scrapers.halsail as halsail
    import irc_data.scrapers.sailwave as sailwave

    seen: dict = {"run": [], "approved": [], "since": []}

    def fake_run(record=None, *, source, seeds, since=None, **kw):
        seen["run"].append({"source": source, "seeds": list(seeds), "since": since})
        return {"records_written": 3, "records_found": 4, "source": source}

    monkeypatch.setattr(sailwave, "run", fake_run)
    monkeypatch.setattr(halsail, "run", fake_run)
    monkeypatch.setattr(legacy_adapters, "_assert_approved", lambda *s: seen["approved"].append(s))

    def fake_since(slug, lookback_days=1):
        seen["since"].append((slug, lookback_days))
        return date(2026, 10, 20)

    monkeypatch.setattr(legacy_adapters, "_last_successful_run_since", fake_since)
    return seen


@pytest.mark.parametrize("slug", sorted(SLUGS))
def test_runner_registered_and_reports_records_written(slug, calls):
    adapter = adapter_for_slug(slug)
    assert adapter is not None and adapter.runner is not None
    out = adapter.run({"slug": slug})
    assert out["records_written"] == 3
    run = calls["run"][0]
    assert run["source"] == slug
    assert run["seeds"] and all(s.source == slug for s in run["seeds"])
    # Default window: last successful run minus 14 days.
    assert calls["since"] == [(slug, 14)] and run["since"] == date(2026, 10, 20)
    # Policy gate on the series row and on the host platform's row.
    assert calls["approved"] == [(slug, SLUGS[slug][2])]


@pytest.mark.parametrize("slug", sorted(SLUGS))
def test_record_since_overrides_ledger(slug, calls):
    adapter_for_slug(slug).run({"slug": slug, "since": "2018-01-01"})
    assert calls["run"][0]["since"] == date(2018, 1, 1)
    assert calls["since"] == []


def test_runner_failure_propagates(calls, monkeypatch):
    import irc_data.scrapers.sailwave as sailwave

    def boom(*a, **k):
        raise sailwave.DiscoveryError("listing empty")

    monkeypatch.setattr(sailwave, "run", boom)
    with pytest.raises(sailwave.DiscoveryError):
        adapter_for_slug("warsash-spring-series").run({})


def test_store_normalized_results_detail_fields(monkeypatch):
    got: list[dict] = []

    def fake_upsert(engine, boat_id, event_name, event_date=None, race_name=None, **kw):
        got.append(dict(event_name=event_name, event_date=event_date, race_name=race_name, **kw))
        return 1

    counts = iter([0, 1])
    monkeypatch.setattr(ri, "upsert_race_result", fake_upsert)
    monkeypatch.setattr(ri, "count_source_rows", lambda e, s: next(counts))
    monkeypatch.setattr(ri, "find_boat_by_sail_number", lambda e, s: None)
    monkeypatch.setattr(ri, "_find_boat_by_name", lambda *a, **k: None)
    row = NormalizedResult(
        boat_name="Luna", sail_number="ITA15656", event_name="E", event_date=date(2026, 4, 18),
        race_name="R1", race_number=1, place=1, class_name="IRC 1", class_place=1,
        class_fleet_size=8, fleet_size=8, rating_type="irc_tcc", rating_value=Decimal("1.122"),
        elapsed_time="0:48:00", corrected_time="0:53:51", source_url="u",
        raw_data={"race_date": "2026-04-18", "boat_name": "Luna"},
    )
    stats = ri.store_normalized_results(object(), [row], source="sailwave", log=False, detail=True)
    assert stats["written"] == 1
    kw = got[0]
    assert kw["race_number"] == 1 and kw["class_name"] == "IRC 1"
    assert kw["race_date_specific"] == date(2026, 4, 18)
    assert kw["tcc_at_race"] == Decimal("1.122")
    assert kw["elapsed_time"] == timedelta(minutes=48)
    assert kw["corrected_time"] == timedelta(minutes=53, seconds=51)
    assert kw["class_place"] == 1 and kw["class_fleet_size"] == 8


def test_store_normalized_results_default_unchanged(monkeypatch):
    got: list[dict] = []
    monkeypatch.setattr(ri, "upsert_race_result", lambda *a, **kw: got.append(kw) or 1)
    monkeypatch.setattr(ri, "count_source_rows", lambda e, s: 0)
    monkeypatch.setattr(ri, "find_boat_by_sail_number", lambda e, s: None)
    monkeypatch.setattr(ri, "_find_boat_by_name", lambda *a, **k: None)
    row = NormalizedResult(boat_name="A", event_name="E", race_number=1, elapsed_time="0:48:00")
    ri.store_normalized_results(object(), [row], source="x", log=False)
    assert "race_number" not in got[0] and "elapsed_time" not in got[0]
