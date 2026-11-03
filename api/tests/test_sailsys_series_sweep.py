"""SailSys series-ID sweep: finds current-season series clubs haven't grouped.

On 2026-11-03 CYCA, MHYC and RSYS were racing 2026-27 series that sat in no
season grouping, so grouping-only discovery found nothing for a month. The
fixtures in tests/fixtures/sailsys are trimmed live responses from that day
(MHYC Harbour Trek October 2026 = series 6004, Seven Islands Race November
2026 = series 6005, MHYC's 2025-2026 grouping list). No network here.
"""

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from irc_data.scrapers import sailsys
from irc_data.scrapers.sailsys import (
    HIDDEN,
    SeriesIndex,
    refresh_series_index,
    scrape_club_irc_results_detailed,
    sweep_series_index,
)

FIX = Path(__file__).parent / "fixtures" / "sailsys"
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)


def _fx(name):
    return json.loads((FIX / name).read_text())


HIDDEN_BODY = _fx("series_hidden_400.json")


def _series_body(sid, club_id=5, club="MHYC", races=()):
    return {"data": {"club": {"id": club_id, "shortName": club}, "name": f"S{sid}", "id": sid,
                     "races": list(races)}}


def _transport(series: dict, fail: set = frozenset(), extra: dict | None = None, log=None):
    """series: id -> response body (visible). Everything else answers the
    real 400 "not displaying" body; ids in ``fail`` answer 500."""
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if log is not None:
            log.append(path)
        if extra and path in extra:
            return httpx.Response(200, json=extra[path])
        parts = path.split("/")
        if path.startswith("/api/v1/series/") and path.endswith("/display/races"):
            sid = int(parts[4])
            if sid in fail:
                return httpx.Response(500, json={"errorMessage": "boom"})
            if sid in series:
                return httpx.Response(200, json=series[sid])
            return httpx.Response(400, json=HIDDEN_BODY)
        return httpx.Response(404, json={"errorMessage": f"unmocked {path}"})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def _instant(self):
        return None
    monkeypatch.setattr(sailsys.RateLimiter, "wait", _instant)
    monkeypatch.setattr(sailsys, "SERIES_SWEEP_START_ID", 100)
    monkeypatch.setattr(sailsys, "SERIES_SWEEP_MISS_LIMIT", 5)


def _sweep(index, transport, **kw):
    async def go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await sweep_series_index(c, index, now=kw.pop("now", NOW), **kw)
    return asyncio.run(go())


class TestSweep:
    def test_fresh_index_walks_from_floor_to_frontier(self):
        series = {100: _fx("series_6004_display_races.json"), 102: _series_body(102, 3, "CYCA")}
        index = SeriesIndex()
        rep = _sweep(index, _transport(series))

        assert sorted(index.series) == [100, 102]
        assert index.high_water == 102
        # 101 hidden; 103..107 = the 5 misses that mark the frontier
        assert sorted(index.hidden) == [101, 103, 104, 105, 106, 107]
        assert rep.probed == 8 and rep.new_series == [100, 102] and not rep.errors
        mhyc = index.series[100]
        assert mhyc["club_id"] == 5 and mhyc["club_name"] == "MHYC"
        assert mhyc["name"] == "Harbour Trek October 2026"
        assert mhyc["first_race"] == mhyc["last_race"] == "2026-10-24"
        assert mhyc["statuses"] == [4]

    def test_next_run_resumes_above_high_water_and_finds_new_series(self):
        index = SeriesIndex()
        _sweep(index, _transport({100: _series_body(100)}))
        log: list = []
        rep = _sweep(index, _transport({100: _series_body(100), 103: _series_body(103)}, log=log))
        assert log[0].endswith("/series/101/display/races")
        assert rep.new_series == [103] and index.high_water == 103
        assert 103 not in index.hidden

    def test_stale_hidden_ids_below_frontier_are_rechecked(self):
        index = SeriesIndex(high_water=110, series={110: {"club_id": 5}},
                            hidden={101: "2026-11-01T00:00:00+00:00",   # stale
                                    102: "2026-11-03T05:00:00+00:00"})  # checked an hour ago
        log: list = []
        rep = _sweep(index, _transport({101: _series_body(101), 110: _series_body(110)}, log=log))
        assert rep.revealed == [101]
        assert 101 in index.series and 101 not in index.hidden
        assert not any(p.endswith("/series/102/display/races") for p in log)
        assert 102 in index.hidden

    def test_server_error_stops_sweep_without_raising(self):
        index = SeriesIndex()
        rep = _sweep(index, _transport({100: _series_body(100)}, fail={101}))
        assert index.series.keys() == {100}
        assert rep.errors and "101" in rep.errors[0] and "500" in rep.errors[0]

    def test_request_cap(self):
        index = SeriesIndex()
        rep = _sweep(index, _transport({i: _series_body(i) for i in range(100, 200)}), max_requests=7)
        assert rep.probed == 7 and index.high_water == 106

    def test_probe_classifies_hidden(self):
        async def go():
            async with httpx.AsyncClient(transport=_transport({})) as c:
                return await sailsys.probe_series(c, 6000)
        assert asyncio.run(go()) == HIDDEN


class TestIndex:
    def test_series_for_club_filters_club_and_old_series(self):
        index = SeriesIndex(series={
            1: {"club_id": 5, "name": "Old", "last_race": "2026-03-01"},
            2: {"club_id": 5, "name": "Harbour Trek October 2026", "last_race": "2026-10-24"},
            3: {"club_id": 3, "name": "CYCA thing", "last_race": "2026-10-24"},
            4: {"club_id": 5, "name": "No races yet", "last_race": None},
        })
        got = index.series_for_club(5, since=date(2026, 10, 20))
        assert [s["series_id"] for s in got] == [2, 4]
        assert got[0] == {"series_id": 2, "name": "Harbour Trek October 2026", "club_id": 5}
        assert [s["series_id"] for s in index.series_for_club(5)] == [1, 2, 4]

    def test_save_load_roundtrip(self, tmp_path):
        p = tmp_path / "sailsys" / "series_index.json"
        index = SeriesIndex(high_water=7, series={7: {"club_id": 5}}, hidden={6: "x"})
        index.save(p)
        assert SeriesIndex.load(p) == index
        assert SeriesIndex.load(tmp_path / "missing.json") == SeriesIndex()

    def test_refresh_rebuilds_corrupt_index_and_saves(self, tmp_path, monkeypatch):
        p = tmp_path / "series_index.json"
        p.write_text("{not json")
        transport = _transport({100: _series_body(100)})
        monkeypatch.setattr(sailsys, "http_client",
                            lambda **k: httpx.AsyncClient(transport=transport))
        index, rep = asyncio.run(refresh_series_index(p))
        assert index.high_water == 100
        assert "unreadable" in rep.errors[0]
        assert SeriesIndex.load(p).high_water == 100


def test_http_client_sends_policy_user_agent():
    from irc_data.sources.policy import ACTIVE_POLICY

    seen = {}

    async def go():
        async with sailsys.http_client(transport=httpx.MockTransport(
            lambda r: seen.update(ua=r.headers["user-agent"]) or httpx.Response(200, json={}))) as c:
            await c.get("https://api.sailsys.com.au/api/v1/x")
    asyncio.run(go())
    assert seen["ua"] == ACTIVE_POLICY.attribution.user_agent


class TestClubScrapeWithSweptSeries:
    def _client(self, monkeypatch, log):
        extra = {
            "/api/v1/series/club/5/grouping/list": _fx("grouping_list_club5.json"),
            "/api/v1/series/6004/display/races": _fx("series_6004_display_races.json"),
            "/api/v1/series/6005/display/races": _fx("series_6005_display_races.json"),
            "/api/v1/races/44599/resultsentrants/display": _fx("race_44599_resultsentrants.json"),
        }
        # grouped series (4801, 4802, 6127) answer the hidden 400 -> no races
        client = httpx.AsyncClient(transport=_transport({}, extra=extra, log=log))
        monkeypatch.setattr(sailsys, "http_client", lambda **k: client)

    def test_ungrouped_current_season_series_are_scraped(self, monkeypatch):
        log: list = []
        self._client(monkeypatch, log)

        class _Today(date):
            @classmethod
            def today(cls):
                return date(2026, 11, 3)
        monkeypatch.setattr(sailsys, "date", _Today)

        extra_series = [
            {"series_id": 6004, "name": "Harbour Trek October 2026", "club_id": 5},
            {"series_id": 6005, "name": "Seven Islands Race November 2026", "club_id": 5},
            {"series_id": 6127, "name": "dup of grouped series", "club_id": 5},
        ]
        out = asyncio.run(scrape_club_irc_results_detailed(
            club_id=5, since=date(2026, 9, 1), extra_series=extra_series))

        assert not out.errors
        assert {r.raw_data["series_id"] for r in out.results} == {6004}
        assert len(out.results) == 2
        r = out.results[0]
        assert r.event_name == "Harbour Trek October 2026 - Race  1"
        assert r.event_date == date(2026, 10, 24)
        assert r.raw_data["boat_name"] == "Shibumi" and r.raw_data["club_name"] == "MHYC"
        # Seven Islands (28 Nov, scheduled) is never fetched; 6127 fetched once
        assert not any("44609" in p for p in log)
        assert sum(p.endswith("/series/6127/display/races") for p in log) == 1


def test_run_sailsys_feeds_swept_series_to_each_club(monkeypatch):
    from irc_data.db import connection
    from irc_data.scrapers import result_import
    from irc_data.temporal import legacy_adapters as la

    index = SeriesIndex(high_water=6004, series={
        6004: {"club_id": 5, "name": "Harbour Trek October 2026", "last_race": "2026-10-24"},
        5000: {"club_id": 5, "name": "Old", "last_race": "2026-01-01"},
    })
    report = sailsys.SweepReport(probed=3, new_series=[6004])

    async def fake_refresh(*a, **k):
        return index, report

    calls = {}

    async def fake_scrape(club_id, since=None, extra_series=None, **k):
        calls[club_id] = (since, extra_series)
        if club_id == 5:
            return [SimpleNamespace(event_date=date(2026, 10, 24), raw_data={"race_id": 44599})]
        return []

    imported = []
    monkeypatch.setattr(sailsys, "CLUBS", {"CYCA": 3, "MHYC": 5})
    monkeypatch.setattr(sailsys, "refresh_series_index", fake_refresh)
    monkeypatch.setattr(sailsys, "scrape_club_irc_results", fake_scrape)
    monkeypatch.setattr(la, "_last_successful_run_since", lambda *a, **k: date(2026, 10, 20))
    monkeypatch.setattr(la, "_existing_sailsys_race_dates", lambda engine, ids: {})
    monkeypatch.setattr(connection, "get_engine", lambda: None)
    monkeypatch.setattr(result_import, "import_scraper_results",
                        lambda engine, results, **k: imported.append((k["organizing_club"], len(results)))
                        or {"imported": len(results)})

    out = la._run_sailsys({})

    assert calls[3] == (date(2026, 10, 20), [])
    assert calls[5] == (date(2026, 10, 20),
                        [{"series_id": 6004, "name": "Harbour Trek October 2026", "club_id": 5}])
    assert imported == [("MHYC", 1)]
    assert out["records_written"] == 1 and out["clubs"] == {"CYCA": 0, "MHYC": 1}
    assert out["series_sweep"]["new_series"] == 1


class TestIrcDefinitionDetection:
    """CYCA's IRC definition is id 25; the old fixed {1, 33} list stored
    every CYCA IRC race with PHS as the rating and no irc_tcc."""

    def _scrape(self, body, race_id=43879):
        path = f"/api/v1/races/{race_id}/resultsentrants/display"

        async def go():
            async with httpx.AsyncClient(transport=_transport({}, extra={path: body})) as client:
                return await sailsys.scrape_race_results(client, 3, 5924, race_id,
                                                         series_name="SSOS", club_name="CYCA")
        return asyncio.run(go())

    def test_cyca_definition_25_is_irc(self):
        rows = self._scrape(_fx("race_43879_cyca_irc25.json"))
        about_time = next(r for r in rows if r.raw_data["boat_name"] == "About Time")
        assert about_time.raw_data["irc_tcc"] == "1.221"
        assert str(about_time.tcc_at_race) == "1.221"
        assert about_time.raw_data["irc_place"] == 1 and about_time.place == 1
        assert about_time.raw_data["irc_corrected"] == "06:01:40"
        assert about_time.raw_data["phs"] == "1.2249" and about_time.raw_data["phs_place"] == 2
        phs_only = next(r for r in rows if r.raw_data["boat_name"] == "Cadenza")
        assert phs_only.raw_data["irc_tcc"] is None

    def test_unknown_definition_id_recognised_by_name(self):
        race = {"handicappings": [{"id": 5, "shortName": "PHS"}, None],
                "competitors": [{"items": [{"handicap": {"currentHandicaps": [
                    {"definition": {"id": 9001, "shortName": "IRC SH", "fullName": "IRC Short-handed"},
                     "value": 1.0},
                    {"definition": {"id": 4, "shortName": "ORCGP"}, "value": 600}]}}]}]}
        ids = sailsys.irc_definition_ids(race)
        assert 9001 in ids and 5 not in ids and 4 not in ids
