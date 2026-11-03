"""Site changes that made the rorc / sydneyhobart / cowesweek / sailracehq
scrapers return nothing (Nov 2026). No network.

* Sydney Hobart: bwps.cycaracing.com dropped its server-rendered standings;
  results now come from the CYCA feeds API. Fixtures under
  ``fixtures/sydneyhobart`` are trimmed copies of the real 2025 feed.
* Cowes Week: results skin moved from map=cw26/ui=cw4 to map=cw-res, and the
  season list stopped at 2025.
* RORC: year index pages now link ``/raceresults/{YYYY}/`` (was
  ``/files/raceresults/{YYYY}/``).
* SailRaceHQ: the Cloudflare bot check no longer clears; that must fail the
  run, not report a clean zero.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from irc_data.scrapers import cowesweek, rorc, sailracehq, sydneyhobart as sh

FIX = Path(__file__).parent / "fixtures" / "sydneyhobart"


def _json(name):
    return json.loads((FIX / name).read_text())


def test_sydneyhobart_editions_from_series_feed():
    eds = sh.editions_from_series(_json("series_15_races.json"))
    assert eds[2025]["race_id"] == 192 and eds[2024]["race_id"] == 184
    assert eds[2026] == {"race_id": 200, "status": "Pre-race", "start": "2026-12-26T13:00:00"}


def test_sydneyhobart_picks_overall_irc_category():
    cat = sh.irc_category(_json("race_192.json"))
    assert cat["id"] == 1102 and cat["name"] == "IRC"


def test_sydneyhobart_parse_feed_results():
    rows = sh.parse_feed_results(
        _json("race_192_irc_results_final.json"), 2025, 192, start="2025-12-26T13:00:00",
    )
    assert len(rows) == 4
    first = rows[0]
    assert first.boat_name == "Min River"  # "(DH)" marker stripped for matching
    assert first.raw_data["double_handed"] is True and first.raw_data["name_race"] == "Min River (DH)"
    assert first.sail_number == "AUS888"
    assert first.rating_type == "irc_tcc" and first.rating_value == Decimal("1.024")
    assert first.place == 1 and first.fleet_size == 4
    assert first.status == "finished"
    assert first.corrected_time == "97:56:09"  # 2025-12-30T14:56:09 - start
    assert first.event_name == "2025 Rolex Sydney Hobart Yacht Race"
    assert first.event_date == date(2025, 12, 26)
    assert first.class_name == "Division 2"
    retired = rows[-1]
    assert retired.boat_name == "Koa" and retired.status == "RET"
    assert retired.place is None and retired.corrected_time is None  # catPlace 999 / year-1 sentinel


def test_cowesweek_urls_use_current_skin_and_cover_2026():
    assert 2026 in cowesweek.CW_YEARS and 2025 in cowesweek.CW_YEARS
    url = cowesweek._build_points_url(2026, 10)
    assert "map=cw-res&ui=cw-res" in url and "page=points2026&resultrequest=10" in url
    assert "map=cw-res" in cowesweek._build_boat_details_url(2024, 169)
    assert cowesweek.CW_APPROXIMATE_DATES[2026] == date(2026, 8, 1)


@pytest.mark.parametrize("href", [
    "https://www.rorc.org/raceresults/2022/four01.html",
    "https://www.rorc.org/files/raceresults/2019/ircoverall02.html",
    "/raceresults/2021/fastnet-ircov01.htm",
])
def test_rorc_result_links_old_and_new_layout(href):
    assert rorc.RESULT_LINK_RE.search(href)


def test_rorc_result_link_ignores_other_links():
    assert not rorc.RESULT_LINK_RE.search("https://sailracehq.com/results")


def test_sailracehq_cloudflare_block_raises(monkeypatch):
    monkeypatch.setattr(sailracehq.time, "sleep", lambda s: None)

    class _Page:
        def __init__(self, title):
            self._title = title

        def goto(self, *a, **k):
            pass

        def title(self):
            return self._title

    with pytest.raises(sailracehq.CloudflareBlocked):
        sailracehq._pass_cloudflare(_Page("Just a moment..."), "https://sailracehq.com/results")
    sailracehq._pass_cloudflare(_Page("Results | SailRaceHQ"), "https://sailracehq.com/results")
