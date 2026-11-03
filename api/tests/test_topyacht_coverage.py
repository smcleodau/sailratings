"""TopYacht coverage fixes (2026-11 audit): IRC header variants, multi-hop
club pages, stale directory links, and the topyacht.net public-results API.

Fixtures in tests/fixtures/topyacht/saved/ are trimmed copies of real pages
(fetched 2026-11-03); no network access here.
"""
import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from irc_data.scrapers import topyacht as ty

SAVED = Path(__file__).parent / "fixtures" / "topyacht" / "saved"


def _read(name):
    return (SAVED / name).read_text()


@pytest.fixture(autouse=True)
def _no_delay():
    old = ty.rate_limiter.min_delay, ty.rate_limiter.jitter
    ty.rate_limiter.min_delay = ty.rate_limiter.jitter = 0
    yield
    ty.rate_limiter.min_delay, ty.rate_limiter.jitter = old


def _client(routes):
    """routes: {url-substring: body-or-(status, body)}; first match wins."""
    calls = []

    def handler(req):
        url = str(req.url)
        calls.append(url)
        for key, body in routes.items():
            if key in url:
                status, body = body if isinstance(body, tuple) else (200, body)
                ctype = "application/json" if body.lstrip().startswith("{") else "text/html"
                return httpx.Response(status, text=body, headers={"content-type": ctype})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True), calls


def _run(coro_fn, routes):
    async def go():
        client, calls = _client(routes)
        async with client:
            return await coro_fn(client), calls
    return asyncio.run(go())


# --- IRC column header variants -------------------------------------------

@pytest.mark.parametrize("headers,expected", [
    (["", "PHS", "IRC", "Entrants"], [2]),
    (["", "ORC AP", "AMS Div1", "IRC  Div1", "Super40", "Entrants"], [3]),
    (["", "IRC TCC", "IRC Overall", "IRC 1", "ORCi"], [1, 2, 3]),
    (["", "PHS", "ORC NS AP", "IRCNS", "IRC_NS", "IRC2"], [3, 4, 5]),
    (["", "Circuit", "PHS", "AMS"], []),
])
def test_irc_column_indexes_accepts_header_variants(headers, expected):
    assert ty.irc_column_indexes(headers) == expected


def test_series_with_irc_div_header_yields_irc_races_since_cutoff():
    url = "https://tes.topyacht.net.au/results/2026/fos/pass-ratg-1/series.htm"
    races, _ = _run(lambda c: ty.discover_races_from_series(c, url, since=date(2026, 1, 26)),
                    {"series.htm": _read("fos2026_pass_ratg_1_series.htm")})
    race_pages = [r for r in races if not r["is_series_scores"]]
    # "Race 3 - 25/01/2026" is dated mid-label and before the cutoff.
    assert [r["url"].rsplit("/", 1)[1] for r in race_pages] == ["04RGrp3.htm"]
    assert race_pages[0]["irc_column"] == "IRC  Div1"


def test_fos_race_page_parses_irc_div_caption():
    rows = ty.parse_topyacht_html(_read("fos2026_pass_ratg_1_04RGrp3.htm"), source_url="u")
    assert [r.raw_data["boat_name"] for r in rows] == ["IKON", "TEAM HOLLYWOOD", "COOL C4TS"]
    assert rows[0].tcc_at_race == Decimal("1.113")
    assert rows[0].event_date == date(2026, 1, 26)
    assert rows[0].raw_data["race_name"] == "Race 4"


# --- Club pages needing more than one hop ---------------------------------

CLUB_HOME = '<a href="/sailing/results/">Results</a> <a href="/news/x/">Results news</a> <a href="/about/">About</a>'
CLUB_RESULTS = ('<a href="https://topyacht.net.au/results/ppnyc/2025/summer/index.htm">Combined</a>'
                '<a href="https://topyacht.net/publicresults/IndexResults?clubId=1330&eventId=348">Offshore</a>'
                '<a href="https://topyacht.net.au/results/ppnyc/2019/summer/index.htm">Old</a>')
INDEX = '<a href="BB/series.htm">Bluebird</a>'


def test_resolve_follows_club_results_page_then_topyacht_index():
    routes = {"club.example/sailing/results/": CLUB_RESULTS, "club.example/": CLUB_HOME,
              "2025/summer/index.htm": INDEX}
    out, calls = _run(lambda c: ty.resolve_series_urls(c, "https://club.example/", since=date(2025, 7, 1)), routes)
    assert out == [
        "https://topyacht.net/publicresults/IndexResults?clubId=1330&eventId=348",
        "https://topyacht.net.au/results/ppnyc/2025/summer/BB/series.htm",
    ]
    assert not any("/news/" in u or "/about/" in u or "/2019/" in u for u in calls)


def test_season_url_variants_rewrite_stale_directory_links():
    assert ty.season_url_variants("https://www.orcv.org.au/results/2024-25/", "ORCV Season 2025 - 2026") == [
        "https://www.orcv.org.au/results/2025-26/", "https://www.orcv.org.au/results/2026-27/"]
    assert ty.season_url_variants("https://www.topyacht.net.au/results/orwa/2024/index.htm",
                                  "Ocean Racing Western Australia - Season 2025-26") == [
        "https://www.topyacht.net.au/results/orwa/2025/index.htm"]
    assert ty.season_url_variants("http://www.topyacht.net.au/results/2026/fos/index.htm", "Festival of Sails 2026") == []
    assert ty.season_url_variants("https://club.example/results/", "Regatta 2026") == []


def test_directory_skips_blank_results_links():
    html = ('<table><tr><td>RFBYC Season 2026/2027</td><td>KB</td><td>RFBYC</td>'
            '<td><a href="x">Enter</a></td><td><a href="">Results</a></td></tr></table>')
    assert ty.parse_events_directory(html, {2026}) == []


# --- topyacht.net public results (JSON) -----------------------------------

def test_parse_publicresults_race_keeps_only_irc_rows():
    data = json.loads(_read("publicresults_race_1205_3006_2073.json"))["data"]
    rows = ty.parse_publicresults_race(
        data, series_name="Summer Passage 2026/27", fleet_label="D1", race_label="R1",
        source_url="https://topyacht.net/publicresults/SeriesIndex?seriesId=1205", organizing_club="Southport Yacht Club")
    assert {r.raw_data["result_option"] for r in rows} == {"IRC"}
    first = rows[0]
    assert first.event_name == "Summer Passage 2026/27 - Race 1"
    assert first.event_date == date(2026, 9, 27)
    assert first.raw_data["boat_name"] == "KERISMA" and first.tcc_at_race == Decimal("1.139")
    assert first.place == 1 and first.division == "D1"
    dnc = rows[-1]
    assert dnc.place is None and dnc.raw_data["status"] == "DNC"
    assert dnc.elapsed_time is None and dnc.raw_data["finish_time"] is None


def test_scrape_publicresults_event_end_to_end():
    routes = {
        "list?clubId=1330&eventId=348": _read("publicresults_list_1330_348.json"),
        "raceSessionsAndFleetsBySeriesId?seriesId=1205": _read("publicresults_sessions_1205.json"),
        "raceResults?seriesId=1205&fleetId=3006": _read("publicresults_race_1205_3006_2073.json"),
        "raceResults?seriesId=1205&fleetId=3007": '{"success":true,"data":{"resultGroupeds":'
                                                  '[{"resultOption":"PHS","results":[{"boatName":"X"}]}]}}',
    }

    async def go(c):
        return await ty.scrape_results_url(
            c, "https://topyacht.net/publicresults/IndexResults?clubId=1330&eventId=348", since=date(2026, 7, 1))

    rows, calls = _run(go, routes)
    assert rows and all(r.raw_data["result_option"] == "IRC" for r in rows)
    assert {r.division for r in rows} == {"D1"}
    assert rows[0].source_url == ("https://topyacht.net/publicresults/SeriesIndex?seriesId=1205"
                                  "&fleetId=3006&raceSessionId=2073")
    assert rows[0].raw_data["club_name"] == "Southport Yacht Club"
    # No HTML fetch of the JavaScript app itself.
    assert not any(u.startswith("https://topyacht.net/publicresults") for u in calls)


def test_publicresults_since_cutoff_drops_old_races():
    routes = {
        "raceSessionsAndFleetsBySeriesId?seriesId=1205": _read("publicresults_sessions_1205.json"),
        "raceResults?seriesId=1205&fleetId=3006": _read("publicresults_race_1205_3006_2073.json"),
    }
    rows, _ = _run(lambda c: ty.scrape_publicresults_series(c, "1205", since=date(2026, 10, 1)), routes)
    assert rows == []


def test_resolve_follows_results_nav_on_moved_club_page():
    moved = ('<nav><a href="/racing/">Results 2026-27</a></nav>'
             '<a href="https://topyacht.net/publicresults/SeriesIndex?seriesId=1267">CC Midweek</a>')
    routes = {"byc.example/current-race-results/": (404, moved),
              "byc.example/racing/": '<a href="https://topyacht.net/publicresults/SeriesIndex?seriesId=1400">Overnight</a>'}
    out, _ = _run(lambda c: ty.resolve_series_urls(c, "https://byc.example/current-race-results/"), routes)
    assert out == ["https://topyacht.net/publicresults/SeriesIndex?seriesId=1267",
                   "https://topyacht.net/publicresults/SeriesIndex?seriesId=1400"]


def test_resolve_drops_series_links_from_old_seasons():
    page = ('<a href="http://www.topyacht.net.au/results/rmys/2018/ayc/mono/series.htm">AYC 2018</a>'
            '<a href="https://topyacht.net.au/results/syc/2026/range/series.htm">Range 2026</a>')
    out, _ = _run(lambda c: ty.resolve_series_urls(c, "https://champs.example/range-series/", since=date(2025, 7, 1)),
                  {"champs.example/range-series/": page})
    assert out == ["https://topyacht.net.au/results/syc/2026/range/series.htm"]
