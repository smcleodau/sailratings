"""Gaps found during the Nov 2026 full-history backfill of rorc / cowesweek.

No network. HTML snippets are trimmed from the live pages.

* RORC 2007-2011 keep per-race files in sub-folders
  (``/raceresults/2007/cervantestrophyrace/ms0003.html``); the link pattern
  only accepted ``/raceresults/{YYYY}/{file}`` so those years lost most races.
* RORC start headings carry a time ("Start: Sun 11 Aug 13 12:30:00") which
  the strptime formats rejected: ~4,400 rows were stored without a date.
* RORC archive rows hold the boat type in class_name; the runner did not
  store class_name, so every re-scrape duplicated the archive.
* Cowes Week 2001-2004 class options read "Class 1 IRC", their points tables
  have no header row, and 2012 was missing from the season list.
"""

from __future__ import annotations

from datetime import date

import pytest

from irc_data.scrapers import cowesweek, rorc
from irc_data.temporal import legacy_adapters


# ---------------------------------------------------------------- RORC

@pytest.mark.parametrize("href", [
    "https://www.rorc.org/raceresults/2007/cervantestrophyrace/ms0003.html",
    "https://www.rorc.org/raceresults/2010/easter/oneos.html",
    "https://www.rorc.org/files/raceresults/2009/channelrace/msov13.html",
])
def test_rorc_result_links_in_subfolders(href):
    assert rorc.RESULT_LINK_RE.search(href)


@pytest.mark.parametrize("heading,expected", [
    ("Start: Sun 11 Aug 13 12:30:00", date(2013, 8, 11)),
    ("Start: Sat 5 May 07 08:50:00", date(2007, 5, 5)),
    ("Start: Fri 10 May 13", date(2013, 5, 10)),
    ("Start: 14 Aug 2021", date(2021, 8, 14)),
    ("Provisional Results", None),
])
def test_rorc_parse_start_date(heading, expected):
    assert rorc.parse_start_date(heading) == expected


RORC_PAGE = """
<html><body>
<h1>2013 RORC Mainseries IRC Four</h1>
<h1>Rolex Fastnet Race</h1>
<h2>Start: Sun 11 Aug 13 12:30:00</h2>
<h2>Provisional Results</h2>
<table border="1">
<tr><td>Sail No</td><td>Boat</td><td>Type of boat</td><td>Owner</td><td>Elapsed</td>
<td>Handicap</td><td>Corrected</td><td>Finishing<br/>Place</td><td>Comments</td></tr>
<tr><td>FRA37310</td><td>Foggy Dew</td><td>JPK 10.10</td><td>Noel Racine</td>
<td>3 - 10:01:02</td><td>1.016</td><td>3 - 11:00:00</td><td>1</td><td>&nbsp;</td></tr>
<tr><td>GBR8752T</td><td>Daring</td><td></td><td>X</td><td></td><td>0.990</td><td></td><td></td><td>DNF</td></tr>
</table>
</body></html>
"""


def test_rorc_parse_page_date_class_and_heading():
    rows = rorc.parse_rorc_result_html(
        RORC_PAGE, "2013 RORC - IRCFour", "https://www.rorc.org/raceresults/2013/four11.html")
    assert len(rows) == 2
    first, dnf = rows
    # event_name stays the series/class title: it is part of the stored identity.
    assert first.event_name == "2013 RORC Mainseries IRC Four"
    assert first.event_date == date(2013, 8, 11)
    assert first.class_name == "JPK 10.10"  # boat type, as the archive rows hold it
    assert first.raw_data["race_heading"] == "Rolex Fastnet Race"
    assert first.place == 1 and str(first.rating_value) == "1.016"
    assert dnf.class_name is None and dnf.status == "DNF" and dnf.place is None


def test_rorc_runner_stores_class_name(monkeypatch):
    seen = {}

    def fake(record, **kw):
        seen.update(kw)
        return {}

    monkeypatch.setattr(legacy_adapters, "_run_event_source", fake)
    legacy_adapters._run_rorc({})
    assert seen["source"] == "rorc" and seen["include_class_name"] is True


# ---------------------------------------------------------------- Cowes Week

def test_cowesweek_years_cover_archive():
    years = set(cowesweek.CW_YEARS)
    assert {2001, 2002, 2003, 2004, 2012, 2025, 2026} <= years
    assert 2020 not in years  # no regatta (COVID)


@pytest.mark.parametrize("option,expected", [
    ("IRC Class 0                             ", "IRC Class 0"),
    ("IRC Class Zero", "IRC Class Zero"),
    ("Class 1 IRC", "IRC Class 1"),
    ("Class 0 IRC ", "IRC Class 0"),
    ("Class 10 IRC Bowsprit", "IRC Class 10 Bowsprit"),
    ("IRC Ocean Racer", "IRC Ocean Racer"),
    ("IRC no spinnaker  ", "IRC no spinnaker"),
    ("Class 0 IRC - Delta Trophy", None),  # sub-trophy within a class
    ("Black Group Overall", None),
    ("J/109", None),
    ("Performance Cruiser A", None),
])
def test_cowesweek_irc_class_name(option, expected):
    assert cowesweek.irc_class_name(option) == expected


def test_cowesweek_class_options_2003_layout():
    html = """<select name="resultrequest">
    <option value="0">Select a series...</option>
    <option value="10" >Class 0 IRC</option><option value="15" >Class 0 IRC - Delta Trophy</option>
    <option value="20" >Class 1 IRC</option><option value="200" >Sigma 38</option>
    </select>"""
    assert cowesweek.parse_irc_class_options(html) == {"IRC Class 0": 10, "IRC Class 1": 20}


HEADERLESS_POINTS = """
<table><tr><td>menu</td></tr></table>
<table>
<tr><td class="p1">1</td><td class="p1"> AERA </td><td class="p1">  2.0 </td><td class="p1"> 1.0 </td><td class="p1"> 14.0</td></tr>
<tr><td class="p2">2</td><td class="p2"> ISLAND FLING </td><td class="p2"> 3.0 </td><td class="p2"> 2.0 </td><td class="p2"> 19.0</td></tr>
<tr><td class="p1">3=</td><td class="p1"> CHERNIKEEFF 2 </td><td class="p1"> 4.0 </td><td class="p1"> 3.0 </td><td class="p1"> 21.0</td></tr>
</table>
"""


def test_cowesweek_headerless_points_table():
    rows = cowesweek.parse_points_table(HEADERLESS_POINTS, 2003, "IRC Class 0")
    assert [(r["place"], r["boat_name"]) for r in rows] == [
        (1, "AERA"), (2, "ISLAND FLING"), (3, "CHERNIKEEFF 2")]
    assert rows[0]["boatref"] is None and rows[0]["fleet_size"] == 3
    assert rows[0]["day_scores"][0] == {"score": "2.0", "status": ""}


def test_cowesweek_headed_points_table_unchanged():
    html = """<table>
    <tr><td>Pos</td><td>Boat</td><td>Sat</td></tr>
    <tr><td>1</td><td><a href="main_c.php?page=boatdetails2024&boatref=209">KATABATIC</a>
    <span class="entrant">A Skipper</span></td><td>1.0</td></tr>
    </table>"""
    rows = cowesweek.parse_points_table(html, 2024, "IRC Class 1")
    assert rows == [{"boat_name": "KATABATIC", "skipper": "A Skipper", "boatref": 209, "place": 1,
                     "fleet_size": 1, "day_scores": [{"score": "1.0", "status": ""}]}]
