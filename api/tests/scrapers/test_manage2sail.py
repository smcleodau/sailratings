"""Manage2Sail scraper: parsing, IRC filtering, discovery and the adapter wiring.

Fixtures under ``fixtures/manage2sail`` are trimmed copies of real pages
(Nov 2026): the public event list, a club page, the Damen Breskens Sailing
Weekend 2026 event page and its combined "IRC/ORC 1" class results JSON, and
a one-design (Europe) class. No network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from irc_data.scrapers import manage2sail as m2s
from irc_data.scrapers.base import RateLimiter

FIX = Path(__file__).parent / "fixtures" / "manage2sail"
EVENT_GUID = "6fb16996-5715-45f2-b9f1-d6a2f855ed33"
IRC_CLASS = "b660d1d5-278b-4dc6-abcd-c51264493ce1"


def _text(name: str) -> str:
    return (FIX / name).read_text()


def _json(name: str) -> dict:
    return json.loads(_text(name))


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------


def test_parse_event_list_rows_dates_and_clubs():
    refs = m2s.parse_event_list(_text("event_list.html"))
    assert len(refs) == 4
    first = refs[0]
    assert first.event_name == "Ferienprogramm LYC"
    assert first.event_date == date(2026, 1, 1)
    assert first.metadata["end_date"] == "2026-11-30"
    assert first.metadata["country"] == "GER"
    assert first.metadata["club_ids"] == ["56134cbf-a11c-4d4f-b25d-9f2594d6964e"]
    assert first.event_url.startswith("https://www.manage2sail.com/en-US/event/")
    # Winter series 28.11. - 07.03. ends the following year.
    winter = [r for r in refs if r.metadata["end_date"].startswith("2027")]
    assert winter and winter[0].event_date == date(2026, 11, 28)
    assert m2s.has_next_page(_text("event_list.html"))


def test_parse_club_events_single_day_and_span():
    refs = m2s.parse_club_events(_text("club_detail.html"), "club-1")
    assert [r.event_date for r in refs][:2] == [date(2026, 5, 1), date(2026, 5, 15)]
    assert refs[0].metadata["end_date"] == "2026-05-01"
    assert refs[1].metadata["end_date"] == "2026-05-17"
    assert all(r.metadata["club_ids"] == ["club-1"] for r in refs)


def test_parse_event_page():
    info = m2s.parse_event_page(_text("event_page.html"))
    assert info["guid"] == EVENT_GUID
    assert info["name"] == "Damen Breskens Sailing Weekend 2026"
    assert info["club_ids"] == ["55571942-e6d9-453d-9c03-59c097f3d922"]
    assert info["club_name"] == "Stichting Breskens Sailing"
    by_id = {c["id"]: c for c in info["classes"]}
    assert by_id[IRC_CLASS]["has_results"] is True
    assert by_id["e2c07701-9d97-4930-bdee-302e1da596fd"]["has_results"] is False


@pytest.mark.parametrize("name,expected", [
    ("ILCA 7", True), ("Optimist B", True), ("J/70", True), ("Europe", True),
    ("IRC/ORC 1 (TCC ≥ 1.043/APH < 531)", False), ("ORC 2", False),
    ("Yardstick Gruppe A", False), ("CR 1,2 & SW< 90", False), ("Double Handed CR&SW", False),
])
def test_is_one_design_class(name, expected):
    assert m2s.is_one_design_class(name) is expected


# ---------------------------------------------------------------------------
# IRC extraction
# ---------------------------------------------------------------------------


def _irc_rows():
    return m2s.extract_irc_results(
        _json("regattaresult_irc_orc.json"),
        event_name="Damen Breskens Sailing Weekend 2026",
        event_date=date(2026, 8, 28),
        event_guid=EVENT_GUID,
        class_id=IRC_CLASS,
        class_name="IRC/ORC 1 (TCC ≥ 1.043/APH < 531)",
        organizing_club="Stichting Breskens Sailing",
        organizer_club_ids=["55571942-e6d9-453d-9c03-59c097f3d922"],
    )


def test_only_the_irc_scoring_is_kept():
    rows = _irc_rows()
    assert rows
    assert {r.raw_data["rating_system"] for r in rows} == {"IRC"}
    assert {r.raw_data["scoring_name"] for r in rows} == {"IRC 1"}
    assert all(r.race_name.startswith("IRC 1 - R") for r in rows)
    assert all(r.rating_type == "irc_tcc" for r in rows)


def test_irc_row_fields():
    rows = _irc_rows()
    r1 = next(r for r in rows if r.boat_name == "Jack Rabbit" and r.race_number == 1)
    assert r1.sail_number == "NED 1112"
    assert r1.rating_value == Decimal("1.047")  # per-race Hdcp = TCC
    assert r1.place == 1 and r1.status == "finished"
    assert r1.elapsed_time == "0:47:03" and r1.corrected_time == "0:49:16"
    assert r1.fleet_size == 3  # entries in this (trimmed) scoring
    assert r1.raw_data["race_date"] == "2026-08-28"
    assert r1.raw_data["boat_type"] == "J-112"
    assert r1.raw_data["series_rank"] == 1
    assert r1.raw_data["organizer_club_ids"] == ["55571942-e6d9-453d-9c03-59c097f3d922"]
    assert r1.source_url == f"https://www.manage2sail.com/en-US/event/{EVENT_GUID}#!results?classId={IRC_CLASS}"


def test_status_codes_have_no_place():
    flagged = [r for r in _irc_rows() if r.status != "finished"]
    assert flagged, "fixture keeps an entry with OCS/DNF races"
    assert all(r.place is None for r in flagged)
    assert {r.status for r in flagged} <= m2s._STATUS_CODES


def _scoring(name, entries, sid):
    return {
        "Id": sid, "ScoringName": name, "RatingSystem": "IRC",
        "RaceNames": [{"Name": "R1", "RaceIndex": 1}],
        "RaceInfos": [{"RaceId": "race-1", "StartDate": "2026-05-24T00:00:00"}],
        "EntryResults": entries,
    }


def _entry(boat, rank=None, code=None):
    rr = {"RaceId": "race-1", "OverallRaceIndex": 1, "Hdcp": 1.015, "RaceTime": "1:02:19"}
    if code:
        rr["RaceStatusCode"] = code
    else:
        rr["Rank"] = rank
    return {"BoatName": boat, "SailNumber": "NED 1", "EntryRaceResults": [rr]}


def test_overlapping_irc_scorings_keep_one_row_per_boat_race():
    """North Sea Regatta style: 'IRC Overall' and a subset scoring share races."""
    overall = _scoring("IRC Overall", [_entry("Warp 5", 3), _entry("A"), _entry("B")], "s1")
    subset = _scoring("IRC2/3 Inshores", [_entry("Warp 5", 1)], "s2")
    data = {"RatingSystem": "ORC", "Id": "d", "ScoringResults": [
        {"Name": "IRC2/3 Inshores", "Result": subset}, {"Name": "IRC Overall", "Result": overall},
    ]}
    rows = m2s.extract_irc_results(data, event_name="NSR", event_date=None, event_guid="g",
                                   class_id="c", class_name="Fully Crewed ORC/IRC 2")
    warp = [r for r in rows if r.boat_name == "Warp 5"]
    assert len(warp) == 1
    assert warp[0].race_name == "IRC Overall - R1" and warp[0].place == 3


def test_dnc_rows_are_not_stored():
    data = _scoring("IRC 1", [_entry("Here", 1), _entry("Absent", code="DNC"), _entry("Broke", code="DNF")], "s")
    rows = m2s.extract_irc_results(data, event_name="E", event_date=None, event_guid="g",
                                   class_id="c", class_name="IRC 1")
    assert {r.boat_name: r.status for r in rows} == {"Here": "finished", "Broke": "DNF"}
    assert {r.fleet_size for r in rows} == {2}  # the DNC boat was not in the race


def test_one_design_class_yields_nothing():
    rows = m2s.extract_irc_results(
        _json("regattaresult_one_design.json"), event_name="x", event_date=None,
        event_guid="g", class_id="c", class_name="Europe",
    )
    assert rows == []


def test_interval_parsing():
    assert m2s._parse_interval("2:34:01") == timedelta(hours=2, minutes=34, seconds=1)
    assert m2s._parse_interval("1.02:03:04") == timedelta(days=1, hours=2, minutes=3, seconds=4)
    assert m2s._parse_interval("0:00:00") is None
    assert m2s._parse_interval("") is None


# ---------------------------------------------------------------------------
# Discovery + scrape against a mocked transport
# ---------------------------------------------------------------------------


def _mock_client(calls: list[str]) -> httpx.AsyncClient:
    breskens_club = "55571942-e6d9-453d-9c03-59c097f3d922"
    club_page = (
        "<table class=\"table table-hover\"><tr><td>2026</td><td>28/08 - 30/08</td>"
        f"<td><a href=\"/en-US/event/{EVENT_GUID}\">Damen Breskens Sailing Weekend 2026</a></td></tr>"
        "<tr><td>2026</td><td>01/12</td><td><a href=\"/en-US/event/future\">Future Cup</a></td></tr></table>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        assert "/search" not in url, "robots.txt disallows /*/search"
        assert request.headers["user-agent"].startswith("SailRatings/")
        if url.endswith("/en-US/event"):
            return httpx.Response(200, text="<table></table>")
        if f"/Club/Detail/{breskens_club}" in url:
            return httpx.Response(200, text=club_page)
        if "/Club/Detail/" in url:
            return httpx.Response(200, text="<table></table>")
        if url.endswith(f"/en-US/event/{EVENT_GUID}"):
            return httpx.Response(200, text=_text("event_page.html"))
        if url.endswith(f"/regattaresult/{IRC_CLASS}"):
            return httpx.Response(200, json=_json("regattaresult_irc_orc.json"))
        if "/regattaresult/" in url:
            return httpx.Response(200, json=_json("regattaresult_one_design.json"))
        return httpx.Response(404)

    from irc_data.sources.policy import ACTIVE_POLICY

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": ACTIVE_POLICY.attribution.user_agent},
    )


def test_discover_and_scrape_end_to_end_without_network():
    calls: list[str] = []
    scraper = m2s.Manage2SailScraper(
        client=_mock_client(calls),
        rate_limiter=RateLimiter(min_delay=0, jitter=0),
        today=date(2026, 11, 3),
    )

    async def go():
        events = await scraper.discover_events(since=date(2026, 8, 1))
        rows = []
        for ev in events:
            rows.extend(await scraper.scrape_event(ev))
        return events, rows

    events, rows = asyncio.run(go())
    # Future events (and ones ending before `since`) are filtered out.
    assert [e.event_name for e in events] == ["Damen Breskens Sailing Weekend 2026"]
    assert rows and all(r.rating_type == "irc_tcc" for r in rows)
    # Class without results (IRC/ORC 3) is never requested.
    assert not any("e2c07701" in c for c in calls)
    assert scraper.stats["irc_scorings"] == 1


# ---------------------------------------------------------------------------
# Adapter wiring
# ---------------------------------------------------------------------------


def test_manage2sail_is_a_registered_legacy_adapter():
    from irc_data.temporal.legacy_adapters import adapter_for_slug

    adapter = adapter_for_slug("manage2sail")
    assert adapter is not None and adapter.runner is not None
    assert adapter.argv() == ["scrape", "results", "--source", "manage2sail"]


def test_runner_persists_and_reports_records_written(monkeypatch):
    """The adapter runs the scraper with the ledger `since` and returns real writes."""
    from irc_data.temporal import legacy_adapters

    seen = {}
    monkeypatch.setattr(legacy_adapters, "_last_successful_run_since",
                        lambda slug, lookback_days=1: seen.setdefault("since_args", (slug, lookback_days)) and date(2026, 10, 20))

    def fake_run(record, *, since=None):
        seen["since"] = since
        return {"records_written": 7, "records_found": 9}

    monkeypatch.setattr(m2s, "run", fake_run)
    out = legacy_adapters.adapter_for_slug("manage2sail").run({"slug": "manage2sail"})
    assert out["records_written"] == 7
    assert seen["since_args"] == ("manage2sail", 14)
    assert seen["since"] == date(2026, 10, 20)


def test_store_results_counts_only_new_rows(monkeypatch):
    """records_written is the race_results delta, not the number of upserts."""
    import irc_data.db.operations as ops
    import irc_data.scrapers.result_import as ri

    counts = iter([100, 103])
    upserts = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _R:
                def scalar(self_inner):
                    return next(counts)
            return _R()

    class _Engine:
        def connect(self):
            return _Conn()

    monkeypatch.setattr(ops, "log_ingestion_start", lambda *a, **k: 1)
    monkeypatch.setattr(ops, "log_ingestion_end", lambda *a, **k: None)
    monkeypatch.setattr(ops, "find_boat_by_sail_number", lambda *a, **k: None)
    monkeypatch.setattr(ri, "_find_boat_by_name", lambda *a, **k: None)
    monkeypatch.setattr(ops, "upsert_race_result", lambda engine, **kw: upserts.append(kw) or 1)

    rows = _irc_rows()
    out = m2s.store_results(_Engine(), rows)
    assert out == {"written": 3, "matched": 0, "errors": 0}
    assert len(upserts) == len(rows)
    kw = upserts[0]
    assert kw["source"] == "manage2sail" and kw["rating_type"] == "irc_tcc"
    assert kw["tcc_at_race"] == kw["rating_value"]
    assert kw["race_date_specific"] == date(2026, 8, 28)
    assert kw["elapsed_time"] == timedelta(minutes=47, seconds=3)
    assert kw["division"] == "IRC 1"
