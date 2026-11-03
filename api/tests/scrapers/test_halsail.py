"""HalSail rendered-page parser, seed policy and runner — real rendered pages, no network."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from irc_data.scrapers import halsail as hs

FIX = Path(__file__).parent / "fixtures" / "halsail"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Conditions of the approval, enforced in code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://www.halsail.com/Result/Club/3560",
    "https://halsail.com/Result/Class/33212",
    "https://halsail.com/result/event/3103",
    "https://archive.halsail.com/Result/Club/1297",
])
def test_club_linked_result_pages_are_valid_seeds(url):
    hs.HalSailSeed(url, "Club").validate()


@pytest.mark.parametrize("url", [
    "https://halsail.com/Result/PrintRace/667718",
    "https://halsail.com/Result/PrintOverall/98833",
    "https://halsail.com/HalApi/Results/1",
    "https://halsail-1e484.kxcdn.com/Result/Public/98833",  # not a club-linked page
    "https://halsail.com/Result/_Boat/98833",
    "http://halsail.com/Result/Club/3560",
])
def test_other_halsail_urls_are_rejected_as_seeds(url):
    with pytest.raises(ValueError):
        hs.HalSailSeed(url, "Club").validate()


@pytest.mark.parametrize("url,blocked", [
    ("https://halsail.com/Result/PrintRace/667718", True),
    ("https://halsail-1e484.kxcdn.com/Result/PrintOverall/98833", True),
    ("https://halsail.com/HalApi/Anything", True),
    ("https://halsail-1e484.kxcdn.com/Result/Public/98833", False),
    ("https://halsail.com/Result/PdfRaceByBoat/667718", False),
])
def test_forbidden_paths_are_aborted_in_the_browser(url, blocked):
    assert bool(hs.FORBIDDEN_PATH_RE.search(url)) is blocked


def test_every_registered_seed_is_valid():
    from irc_data.scrapers.halsail_seeds import HALSAIL_SEEDS, seeds_for

    assert seeds_for("hamble-winter-series")
    for s in HALSAIL_SEEDS:
        s.validate()
        assert s.source in ("halsail", "hamble-winter-series")


@pytest.mark.parametrize("name,keep", [
    ("HWS IRC2", True),
    ("Autumn Regatta IRC1", True),
    ("2025 Members IRC Class 1", True),
    ("INSHORE SERIES IRC 2026", True),
    ("BESC ISCRS 2026", False),
    ("Contessa 32", False),
    ("ERS CLASS 1 2026", False),
    ("IRC/ECHO combined", False),
])
def test_only_irc_classes(name, keep):
    assert hs.HalSailSeed("https://halsail.com/Result/Club/1", "C").wants_class(name) is keep


# ---------------------------------------------------------------------------
# Parsing rendered pages
# ---------------------------------------------------------------------------


def test_selectors_public_layout():
    sel = hs.parse_selectors(_html("hws_public_irc2.html"))
    assert sel["club"] == "Hamble Winter Series"
    by_text = {c.text: c for c in sel["classes"]}
    assert by_text["HWS IRC2"].has_results and not by_text["HWS IRC1"].has_results
    assert [s.value for s in sel["series"]["21969"]] == ["98832"]


def test_public_race_rows():
    rows = hs.parse_public_results(
        _html("hws_public_irc2.html"), class_name="HWS IRC2", series_name="Hamble Winter Series",
        series_id="98832", class_id="21969", club="Hamble River Sailing Club",
        seed_url="https://www.halsail.com/Result/Club/3560", event_series="Hamble Winter Series",
    )
    # Races 1-2 cancelled, 3-5 not part of this series' scoring: only 6 and 7.
    assert {r.race_number for r in rows} == {6, 7}
    assert {r.event_date for r in rows} == {date(2026, 10, 18)}
    assert all(r.event_name == "Hamble Winter Series 2026" for r in rows)
    assert all(r.class_name == "HWS IRC2" and r.rating_type == "irc_tcc" for r in rows)
    assert all(r.source_url == "https://halsail.com/Result/Public/98832" for r in rows)
    first = next(r for r in rows if r.race_number == 6 and r.place == 1)
    assert first.rating_value is not None and Decimal("0.5") <= first.rating_value < 2
    assert first.elapsed_time and first.corrected_time
    assert first.raw_data["halsail_race_id"] and first.raw_data["race_date"] == "2026-10-18"
    # One copy per race even though the page repeats tables per breakpoint.
    keys = [(r.race_number, r.boat_name) for r in rows]
    assert len(keys) == len(set(keys))


def test_status_rows_have_no_place():
    rows = hs.parse_public_results(
        _html("hws_public_irc2.html"), class_name="HWS IRC2", series_name="Hamble Winter Series",
        series_id="98832",
    )
    statuses = {r.status for r in rows}
    assert statuses <= {"finished", "RET", "NSC", "DNF", "DNS"}
    assert all(r.place is None for r in rows if r.status != "finished")
    # "3=" ties keep the numeric place.
    assert any(r.raw_data.get("place_text") == "3=" and r.place == 3 for r in rows)


def test_since_filters_races():
    rows = hs.parse_public_results(
        _html("hws_public_irc2.html"), class_name="HWS IRC2", series_name="Hamble Winter Series",
        series_id="98832", since=date(2026, 10, 19),
    )
    assert rows == []


def test_generic_series_name_uses_class_name():
    rows = hs.parse_public_results(
        _html("isc_public_besc_irc.html"), class_name="BESC IRC 2026", series_name="New Series",
        series_id="96365", club="Island Sailing Club",
    )
    assert rows and {r.event_name for r in rows} == {"BESC IRC 2026"}
    theseus = next(r for r in rows if r.boat_name == "THESEUS")
    assert theseus.sail_number == "GBR8788R" and theseus.place == 1
    assert theseus.rating_value == Decimal("0.900")
    assert (theseus.elapsed_time, theseus.corrected_time) == ("1:06:55", "1:00:14")


def test_archive_layout():
    html = _html("rys_archive_members_irc1.html")
    sel = hs.parse_selectors(html)
    assert sel["layout"] == "archive"
    assert any("2018" in a.text for a in sel["archives"])
    assert any(c.text == "2025 Members IRC Class 1" for c in sel["classes"])
    rows = hs.parse_public_results(
        html, class_name="2025 Members IRC Class 1", series_name="2025 Members IRC Regatta",
        series_id="3444/18", source_url="https://archive.halsail.com/Result/Public/3444/18",
    )
    leon = next(r for r in rows if r.boat_name == "LEON" and r.race_number == 1)
    assert leon.event_date == date(2025, 6, 27)
    assert leon.rating_value == Decimal("1.049") and leon.corrected_time == "2:02:35"
    assert leon.source_url == "https://archive.halsail.com/Result/Public/3444/18"


def test_event_name_for():
    assert hs.event_name_for("HWS IRC2", "Hamble Winter Series", date(2026, 10, 18)) == "Hamble Winter Series 2026"
    assert hs.event_name_for("BESC IRC 2026", "New Series", date(2026, 5, 23)) == "BESC IRC 2026"
    assert hs.event_name_for("X", "RYS Members Regatta 2021", date(2021, 6, 1)) == "RYS Members Regatta 2021"


# ---------------------------------------------------------------------------
# Runner with a fake browser
# ---------------------------------------------------------------------------


class _FakePage:
    url = "https://halsail-1e484.kxcdn.com/Result/Public/98833"


class _FakeBrowser:
    def __init__(self, html):
        self.html = html
        self.page = _FakePage()
        self.navigations = 0
        self.blocked = []
        self.choices = []

    def open_seed(self, url):
        self.navigations += 1
        return self.html

    def choose(self, select_id, value):
        self.choices.append((select_id, value))
        return self.html

    def close(self):
        pass


def test_run_walks_only_irc_classes_with_results():
    b = _FakeBrowser(_html("hws_public_irc2.html"))
    seed = hs.HalSailSeed("https://www.halsail.com/Result/Club/3560", "Hamble River Sailing Club",
                          source="hamble-winter-series")
    stats = hs.run(source="hamble-winter-series", seeds=[seed], store=False, browser=b)
    chosen_classes = [v for k, v in b.choices if k == "ddRacingClasses"]
    # HWS IRC1 has no results yet; the rest are IRC classes with results.
    assert "21968" not in chosen_classes and "21969" in chosen_classes
    assert stats["records_found"] > 0
    assert stats["seeds"][seed.url]["irc_classes"]


def test_run_fails_loudly_when_page_has_no_selectors():
    b = _FakeBrowser("<html><body><h1>Results</h1></body></html>")
    seed = hs.HalSailSeed("https://www.halsail.com/Result/Club/3560", "HRSC")
    with pytest.raises(RuntimeError):
        hs.run(source="halsail", seeds=[seed], store=False, browser=b)
