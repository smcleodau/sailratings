import asyncio
from datetime import date

import httpx

from irc_data.scrapers import topyacht as ty

DIRECTORY = """
<table>
<tr><td>RYCV Season 2025/2026 <br> Royal Yacht Club of Victoria</td><td>KB</td><td>RYCV</td>
  <td><a href="https://www.topyacht.com.au/db/kb2/entry_menu.php?EventID=1">Enter</a></td>
  <td><a href="https://tes.topyacht.net.au/results/rycv/2025/summer/index.htm">Results</a></td></tr>
<tr><td>Old Regatta 2019</td><td>KB</td><td>X</td><td></td>
  <td><a href="https://example.org/2019/index.htm">Results</a></td></tr>
<tr><td>No Results Season 2026</td><td>KB</td><td>Y</td><td><a href="x">Enter</a></td><td></td></tr>
<tr><td>Club Page Season 2026/27</td><td>KB</td><td>Z</td><td></td>
  <td><a href="https://club.example/results/">Results</a></td></tr>
</table>
"""

INDEX = '<a href="BBst26/series.htm">Bluebird</a> <a href="Win/series.htm">Winter</a>'
SERIES = """<span class="heading1">Bluebird State Champs</span>
<table class="centre_index_table">
<tr><td></td><td>PHS</td><td>IRC</td></tr>
<tr><td>Series Scores</td><td></td><td><a href="SIRC.htm">x</a></td></tr>
<tr><td>14/03/2026 Long Distance</td><td></td><td><a href="03RGrp1.htm">x</a></td></tr>
<tr><td>21/02/2026</td><td></td><td><a href="01RGrp1.htm">x</a></td></tr>
</table>"""
CLUB_PAGE = '<a href="https://tes.topyacht.net.au/results/z/2026/kb/index.htm">Keelboats</a> <a href="/news">News</a>'


def test_parse_events_directory_keeps_recent_events_with_results_links():
    events = ty.parse_events_directory(DIRECTORY, years={2025, 2026})
    assert [e["results_url"] for e in events] == [
        "https://tes.topyacht.net.au/results/rycv/2025/summer/index.htm",
        "https://club.example/results/",
    ]
    assert events[0]["name"].startswith("RYCV Season 2025/2026")


def test_race_label_dates_filter_out_races_before_since():
    async def run():
        transport = httpx.MockTransport(lambda req: httpx.Response(200, text=SERIES))
        async with httpx.AsyncClient(transport=transport) as c:
            return await ty.discover_races_from_series(c, "https://h/x/BBst26/series.htm", since=date(2026, 3, 1))
    ty.rate_limiter.min_delay = ty.rate_limiter.jitter = 0
    races = asyncio.run(run())
    assert [r["url"] for r in races if not r["is_series_scores"]] == ["https://h/x/BBst26/03RGrp1.htm"]


def _client(routes, last_modified=None):
    def handler(req):
        url = str(req.url)
        headers = {"last-modified": last_modified} if last_modified else {}
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body, headers=headers)
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_resolve_index_page_to_series_urls():
    async def run():
        async with _client({"summer/index.htm": INDEX}) as c:
            return await ty.resolve_series_urls(c, "https://tes.topyacht.net.au/results/rycv/2025/summer/index.htm")
    ty.rate_limiter.min_delay = ty.rate_limiter.jitter = 0
    assert asyncio.run(run()) == [
        "https://tes.topyacht.net.au/results/rycv/2025/summer/BBst26/series.htm",
        "https://tes.topyacht.net.au/results/rycv/2025/summer/Win/series.htm",
    ]


def test_resolve_follows_one_hop_from_club_page():
    async def run():
        async with _client({"club.example/results/": CLUB_PAGE, "z/2026/kb/index.htm": INDEX}) as c:
            return await ty.resolve_series_urls(c, "https://club.example/results/")
    ty.rate_limiter.min_delay = ty.rate_limiter.jitter = 0
    assert asyncio.run(run()) == [
        "https://tes.topyacht.net.au/results/z/2026/kb/BBst26/series.htm",
        "https://tes.topyacht.net.au/results/z/2026/kb/Win/series.htm",
    ]


def test_resolve_skips_index_unchanged_since_cutoff():
    async def run():
        async with _client({"summer/index.htm": INDEX}, last_modified="Sat, 14 Mar 2026 03:06:40 GMT") as c:
            return await ty.resolve_series_urls(c, "https://h/summer/index.htm", since=date(2026, 6, 1))
    ty.rate_limiter.min_delay = ty.rate_limiter.jitter = 0
    assert asyncio.run(run()) == []
