"""Sailwave IRC parser, discovery and runner — real Warsash SC pages, no network."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from irc_data.scrapers import sailwave as sw
from irc_data.scrapers.result_base import NormalizedResult

FIX = Path(__file__).parent / "fixtures" / "sailwave"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("0.48.00", "0:48:00"),
    ("1:02:03", "1:02:03"),
    ("1.00.23", "1:00:23"),
    ("1:02:03:04", "26:03:04"),
    ("48:00", "0:48:00"),
    ("DNC", None),
    ("", None),
    ("0.00.00", None),
])
def test_parse_duration(raw, expected):
    assert sw.parse_duration(raw) == expected


def test_parse_tcc_rejects_placeholders():
    assert sw.parse_tcc("1.122") == Decimal("1.122")
    assert sw.parse_tcc("2.000") is None  # Sailwave's "no certificate" value
    assert sw.parse_tcc("950") is None    # PY number
    assert sw.parse_tcc("") is None


@pytest.mark.parametrize("text,ref,expected", [
    ("April 18", date(2026, 4, 21), date(2026, 4, 18)),
    ("18 April", date(2026, 4, 21), date(2026, 4, 18)),
    ("Sun 18th Apr", date(2026, 4, 21), date(2026, 4, 18)),
    ("17/04/21", None, date(2021, 4, 17)),
    ("2026-10-11", None, date(2026, 10, 11)),
    ("April 20 at 14.15", date(2025, 4, 25), date(2025, 4, 20)),
    # Winter series: "December 13" read in January belongs to the previous year.
    ("December 13", date(2027, 1, 20), date(2026, 12, 13)),
])
def test_parse_race_date(text, ref, expected):
    assert sw.parse_race_date(text, ref) == expected


def test_parse_race_date_no_roll_back_for_single_year_events():
    assert sw.parse_race_date("April 20", date(2025, 4, 12), roll_back=False) is None


@pytest.mark.parametrize("fleet,system,header,expected", [
    ("IRC 1 Champs", None, "rating", True),
    ("IRC2", None, "rating", True),
    ("Class 1", "IRC", "rating", True),
    ("IRC 1 Division", "TCF", "rating", True),      # generic system, IRC fleet name
    ("P40 Extract Class", "TCF", "rating", False),
    ("ECHO 1", None, "rating", False),
    ("IRC/ECHO", None, "rating", False),           # combined: ambiguous rating
    ("Cruisers", "PY", "rating", False),
    ("Class 1", None, "rating_irc", True),         # column headed IRC/TCC
    ("Class 1", None, "rating_other", False),      # column headed ECHO/PY
    ("SB20 Class", None, None, False),
    ("Class 1", None, "rating", False),
])
def test_fleet_is_irc(fleet, system, header, expected):
    assert sw.fleet_is_irc(fleet, system, header) is expected


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------


def test_race_file_rows():
    rows = sw.parse_sailwave_html(_html("WSC_Champs_26_BG_D1_R1.htm"), source_url="u")
    assert {r.class_name for r in rows} == {"IRC 1 Champs", "IRC 2 Champs", "IRC 3 Champs"}
    luna = next(r for r in rows if r.sail_number == "ITA15656")
    assert luna.boat_name == "Luna"
    assert luna.event_name == "The Henri-Lloyd Warsash Spring Championships 2026"
    assert luna.organizing_club == "Warsash Sailing Club"
    assert luna.event_date == date(2026, 4, 18)
    assert (luna.race_name, luna.race_number, luna.place) == ("R1", 1, 1)
    assert luna.rating_type == "irc_tcc" and luna.rating_value == Decimal("1.122")
    assert (luna.elapsed_time, luna.corrected_time) == ("0:48:00", "0:53:51")
    assert luna.raw_data["design"] == "Swan 45"
    assert luna.raw_data["race_date"] == "2026-04-18"
    assert luna.raw_data["row_origin"] == "race_table"
    # DNC rows (incl. the 2.000-rated Skermisher) are not stored.
    irc1 = [r for r in rows if r.class_name == "IRC 1 Champs"]
    assert len(irc1) == 8 and all(r.fleet_size == 8 for r in irc1)
    assert not any(r.boat_name == "Skermisher" for r in rows)


def test_race_file_retirement_has_status_no_place():
    rows = sw.parse_sailwave_html(_html("WSC_Champs_26_BG_D1_R1.htm"), source_url="u")
    ks = next(r for r in rows if r.boat_name == "Kindred Spirit")
    assert ks.status == "RET" and ks.place is None and ks.corrected_time is None


def test_summary_rows_come_from_points_columns():
    rows = sw.parse_sailwave_html(_html("WSC_Champs_26_BG_Sum.htm"), source_url="s")
    assert rows and all(r.raw_data["row_origin"] == "summary" for r in rows)
    luna_r6 = next(r for r in rows if r.sail_number == "ITA15656" and r.race_number == 6)
    assert luna_r6.place == 4 and luna_r6.raw_data["discarded"] is True
    assert luna_r6.event_date == date(2026, 4, 19)
    assert luna_r6.elapsed_time is None
    # "(13.0 DNS)" -> status, no place; DNC cells are dropped.
    jit_r12 = next(r for r in rows if r.sail_number == "GBR1111X" and r.race_number == 12)
    assert jit_r12.status == "DNS" and jit_r12.place is None
    assert not any("DNC" in r.raw_data["points"] for r in rows)


def test_one_design_fleet_without_rating_is_skipped():
    assert sw.parse_sailwave_html(_html("WSS26_WG_Summary.htm")) == []


def test_extract_sub_scoring_is_not_an_irc_fleet():
    # P30/P40 "extract" re-scores IRC 1 boats among themselves: not IRC places.
    assert sw.parse_sailwave_html(_html("WSS25_P30_P40_extract.htm")) == []


def test_single_race_file_without_date_uses_results_as_of_day():
    rows = sw.parse_sailwave_html(_html("WSS25_D3_R1.htm"))  # "R7 - IRC 1 Division"
    assert rows and {r.event_date for r in rows} == {date(2025, 4, 26)}
    assert {r.raw_data["date_source"] for r in rows} == {"results_as_of"}


def test_undated_race_table_takes_dates_from_the_event_summary(monkeypatch):
    race = _html("WSS25_D3_R1.htm")
    # Without the as-of fallback, an undated race is skipped.
    page = sw.parse_page(race)
    page.as_of = None
    real_parse = sw.parse_page
    monkeypatch.setattr(sw, "parse_page", lambda html: page if html == race else real_parse(html))
    assert sw.parse_sailwave_html(race) == []
    # The 2025 summary leaves R7-R9 undated, so it cannot date them either.
    summary = sw.parse_page(_html("WSS25_BG_Summary.htm"))
    dates = sw.merge_race_dates([sw.page_race_dates(summary)])
    assert ("irc1division", 6) in dates and ("irc1division", 7) not in dates
    assert sw.parse_sailwave_html(race, race_dates=dates) == []
    # A date stated for the same fleet elsewhere in the event is used.
    dates[("irc1division", 7)] = date(2025, 4, 25)
    rows = sw.parse_sailwave_html(race, race_dates=dates)
    assert rows and {r.class_name for r in rows} == {"IRC 1 Division"}
    assert {(r.race_number, r.event_date) for r in rows} == {(7, date(2025, 4, 25))}
    assert {r.raw_data["date_source"] for r in rows} == {"event_files"}


def test_extract_dates_do_not_leak_into_other_fleets():
    extract = sw.parse_page(_html("WSS25_P30_P40_extract.htm"))
    dates = sw.merge_race_dates([sw.page_race_dates(extract)])
    assert not any(k[0].startswith("irc") for k in dates)


def test_tcf_rating_system_with_irc_fleet_names_2021_layout():
    rows = sw.parse_sailwave_html(_html("IRC1_results_Spring_Championship.htm"), source_url="x")
    assert rows
    assert {r.class_name for r in rows} == {"IRC 1 Class"}
    assert all(r.event_date.year == 2021 for r in rows)
    assert all(Decimal("0.5") <= r.rating_value < Decimal("2.0") for r in rows)


def test_folder_index_parse():
    files = sw.parse_folder_index(_html("folder_warsashsc.html"), "https://sailwave.com/results/warsashsc/")
    urls = [u for u, _ in files]
    assert "https://sailwave.com/results/warsashsc/Melges_15_Nats_2026.htm" in urls
    assert "https://sailwave.com/results/warsashsc/Optimist%202026%20Main%20Fleet.htm" in urls
    assert files[0][1] == datetime(2026, 9, 20, 15, 59, 54)


def test_seed_kind_and_filter():
    folder = sw.SailwaveSeed("https://sailwave.com/results/warsashsc/", "W", include=r"^(WSS|WSC_Champs)")
    assert folder.kind == "folder"
    assert folder.wants("https://sailwave.com/results/warsashsc/WSS25_BG_Summary.htm")
    assert not folder.wants("https://sailwave.com/results/warsashsc/Melges_15_Nats_2026.htm")
    assert sw.SailwaveSeed("https://sailwave.com/results/x.htm", "C").kind == "file"
    assert sw.SailwaveSeed("https://warsashsc.org.uk/springseries/y2021/", "C").kind == "page"


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def _row(origin, url, uploaded, race=1, boat="A"):
    return NormalizedResult(boat_name=boat, sail_number="GBR1", event_name="E",
                            event_date=date(2026, 4, 18), race_number=race, class_name="IRC 1",
                            source_url=url, raw_data={"row_origin": origin, "uploaded": uploaded})


def test_dedupe_prefers_race_tables_and_latest_upload():
    rows = [
        _row("summary", "sum", "2026-04-26"),
        _row("race_table", "r1", "2026-04-21"),
        _row("race_table", "r1b", "2026-04-22"),  # same race re-uploaded under a new name
        _row("summary", "sum", "2026-04-26", race=2),
    ]
    kept, dropped = sw.dedupe_rows(rows)
    assert [(r.source_url, r.race_number) for r in kept] == [("r1b", 1), ("sum", 2)]
    assert dropped == 2


# ---------------------------------------------------------------------------
# Runner (fake client, no network, no DB)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, text, url, status=200):
        self.text = text
        self.status_code = status
        self.url = url
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.content = text.encode()


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.requests = 0
        self.got = []

    def get(self, url, method="GET"):
        self.requests += 1
        self.got.append(url)
        if url not in self.pages:
            return _Resp("", url, 404)
        return _Resp(self.pages[url], url)

    def close(self):
        pass


FOLDER = "https://sailwave.com/results/warsashsc/"
LISTING = (
    "<p>2025-04-27 18:00:00 <a href=\"http://sailwave.com/results/warsashsc/WSS25_BG_Summary.htm\">x</a>"
    "<p>2025-04-26 18:00:00 <a href=\"http://sailwave.com/results/warsashsc/WSS25_D3_R1.htm\">x</a>"
    "<p>2024-09-15 18:00:00 <a href=\"http://sailwave.com/results/warsashsc/ICTA24.htm\">x</a>"
)


def _pages():
    return {
        FOLDER: LISTING,
        FOLDER + "WSS25_BG_Summary.htm": _html("WSS25_BG_Summary.htm"),
        FOLDER + "WSS25_D3_R1.htm": _html("WSS25_D3_R1.htm"),
    }


def test_run_two_pass_over_folder(monkeypatch):
    client = _FakeClient(_pages())
    monkeypatch.setattr("irc_data.scrapers.polite_http.PoliteClient", lambda *a, **k: client)
    seeds = [sw.SailwaveSeed(FOLDER, "Warsash Sailing Club", source="warsash-spring-series",
                             include=r"^WSS")]
    captured = {}
    orig = sw.dedupe_rows

    def spy(rows):
        kept, dropped = orig(rows)
        captured["rows"] = kept
        return kept, dropped

    monkeypatch.setattr(sw, "dedupe_rows", spy)
    stats = sw.run(source="warsash-spring-series", seeds=seeds, store=False)
    assert stats["files"] == 2 and stats["files_with_irc"] == 2
    rows = captured["rows"]
    # Summary-derived rows for the dated races R1-R6 plus the R7 race table
    # (single-race file dated by its "as of" day).
    assert rows and {r.race_number for r in rows} == {1, 2, 3, 4, 5, 6, 7}
    r7 = [r for r in rows if r.race_number == 7]
    assert all(r.raw_data["row_origin"] == "race_table" for r in r7)
    assert all(r.organizing_club == "Warsash Sailing Club" for r in rows)


def test_run_since_skips_old_uploads(monkeypatch):
    client = _FakeClient(_pages())
    monkeypatch.setattr("irc_data.scrapers.polite_http.PoliteClient", lambda *a, **k: client)
    seeds = [sw.SailwaveSeed(FOLDER, "W", include=r"^WSS")]
    stats = sw.run(source="sailwave", seeds=seeds, since=date(2025, 4, 27), store=False)
    assert stats["files"] == 1
    assert FOLDER + "WSS25_D3_R1.htm" not in client.got


def test_run_fails_loudly_when_a_listing_is_empty(monkeypatch):
    pages = _pages()
    pages[FOLDER] = "<html>moved</html>"
    client = _FakeClient(pages)
    monkeypatch.setattr("irc_data.scrapers.polite_http.PoliteClient", lambda *a, **k: client)
    with pytest.raises(sw.DiscoveryError):
        sw.run(source="sailwave", seeds=[sw.SailwaveSeed(FOLDER, "W")], store=False)


def test_run_fails_loudly_when_every_seed_errors(monkeypatch):
    client = _FakeClient({})
    monkeypatch.setattr("irc_data.scrapers.polite_http.PoliteClient", lambda *a, **k: client)
    with pytest.raises(sw.DiscoveryError):
        sw.run(source="sailwave", seeds=[sw.SailwaveSeed(FOLDER, "W")], store=False)


def test_seed_lists_are_well_formed():
    from irc_data.scrapers.sailwave_seeds import SAILWAVE_SEEDS, seeds_for

    assert seeds_for("warsash-spring-series")
    assert len({s.url for s in SAILWAVE_SEEDS}) == len(SAILWAVE_SEEDS)
    for s in SAILWAVE_SEEDS:
        assert s.url.startswith("https://")
        assert s.source in ("sailwave", "warsash-spring-series")


def test_scrape_event_api(monkeypatch):
    client = _FakeClient(_pages())
    src = sw.SailwaveSource([], client=client)
    ref = sw.EventRef(source="sailwave", event_name="x", event_url=FOLDER + "WSS25_BG_Summary.htm",
                      organizing_club="Warsash Sailing Club", metadata={})
    rows = asyncio.run(src.scrape_event(ref))
    assert rows and all(r.rating_type == "irc_tcc" for r in rows)


# ---------------------------------------------------------------------------
# Sailwave pages printed to PDF (Warsash 2024)
# ---------------------------------------------------------------------------


def test_pdf_race_blocks_parse_like_html():
    from irc_data.scrapers.sailwave_pdf import pdf_to_sailwave_html

    html = pdf_to_sailwave_html((FIX / "WSS-24-BG-Champs-R1c.pdf").read_bytes())
    assert html
    rows = sw.parse_sailwave_html(html, source_url="https://warsashsc.org.uk/x.pdf")
    assert {r.class_name for r in rows} == {"IRC 1 Champs", "IRC 2 Champs", "IRC 3 Champs"}
    jm = next(r for r in rows if r.sail_number == "GBR7751R")
    assert (jm.boat_name, jm.place, jm.rating_value) == ("JourneyMaker 11", 1, Decimal("1.092"))
    assert (jm.elapsed_time, jm.corrected_time) == ("0:39:21", "0:42:58")
    assert jm.event_date == date(2024, 4, 20)
    assert jm.event_name == "Warsash Spring Championships 2024"
    assert not any(r.boat_name == "Bulldog" for r in rows)  # DNC


def test_pdf_that_is_not_text_returns_none():
    from irc_data.scrapers.sailwave_pdf import pdf_to_sailwave_html

    assert pdf_to_sailwave_html(b"not a pdf") is None


def test_pdf_seed_discovers_pdf_links():
    page = '<a href="/wp-content/uploads/WSS-24-BG-Champs-R1c.pdf">R1</a><a href="/x.htm">x</a>'
    links = sw.discover_sailwave_links(page, "https://warsashsc.org.uk/springseries/2024-2/", pdf=True)
    assert [l["filename"] for l in links] == ["WSS-24-BG-Champs-R1c.pdf", "x.htm"]
    assert sw.discover_sailwave_links(page, "https://warsashsc.org.uk/", pdf=False)[0]["filename"] == "x.htm"


def test_dedupe_amended_reissue_wins_and_keeps_race_day():
    def row(url, as_of, d, src="results_as_of", boat="Cobra"):
        return NormalizedResult(
            boat_name=boat, sail_number="GBR8888N", event_name="Warsash Spring Championships 2024",
            event_date=d, race_name="R9", race_number=9, class_name="IRC 1 Champs", source_url=url,
            raw_data={"row_origin": "race_table", "results_as_of": as_of, "date_source": src,
                      "race_date": d.isoformat()})

    rows = [row("D5-R3.pdf", "2024-04-27", date(2024, 4, 27)),
            row("270424-R4a.pdf", "2024-04-29", date(2024, 4, 29))]
    kept, dropped = sw.dedupe_rows(rows)
    assert dropped == 1 and len(kept) == 1
    assert kept[0].source_url == "270424-R4a.pdf"          # amended copy
    assert kept[0].event_date == date(2024, 4, 27)          # race day, not the re-issue day
    assert kept[0].raw_data["race_date"] == "2024-04-27"


def test_iis_subfolder_listing_parse():
    html = ('<pre><A HREF="/results/royalsolentyc/">[To Parent Directory]</A><br><br>'
            '    Sunday, December 29, 2019 10:14 AM       101787 '
            '<A HREF="/results/royalsolentyc/2019TaittingerRegatta/results.htm">results.htm</A><br></pre>')
    files = sw.parse_folder_index(html, "https://sailwave.com/results/royalsolentyc/2019TaittingerRegatta/")
    assert files == [("https://sailwave.com/results/royalsolentyc/2019TaittingerRegatta/results.htm",
                      datetime(2019, 12, 29, 10, 14))]
