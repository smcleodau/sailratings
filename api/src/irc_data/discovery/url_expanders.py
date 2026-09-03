"""Per-source URL expanders for index-style pages.

Some sources publish a year-landing URL that links to N per-class pages.
Rather than rely on map_site (which over-discovers and burns Firecrawl
credits), we hand each known source a small expander that turns the seed
URL + year into the concrete list of leaf URLs to scrape.

Register new sources by adding a function and an EXPANDERS entry.
"""

from __future__ import annotations

from typing import Callable

ExpanderFn = Callable[[str, int | None], list[str]]

#: IRC class series IDs used in Cowes Week's ``resultrequest`` GET param
#: (IRC Classes 0–7).  Mirrors ``scrapers/cowesweek.IRC_CLASS_IDS``.
_CW_CLASS_IDS = [5, 10, 20, 30, 40, 50, 60, 70]

#: Cowes Week runs 8 races over the week (one per day, typically Sat–Sat).
#: The per-race (daily results) pages carry the *per-race* TCC for each boat
#: alongside the finishing position — that per-race TCC is what OPS-02-06
#: requires, since the series-points pages only expose the series score.
_CW_RACE_NUMBERS = list(range(1, 9))


def _cw_url(page: str, **params) -> str:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    return (
        "https://www.cowesweek.co.uk/web/code/php/main_c.php"
        f"?map=cw26&ui=cw4&style=std&override=&section=racing&page={page}"
        + (f"&{qs}" if qs else "")
    )


def cowesweek_year_expand(_seed: str, year: int | None) -> list[str]:
    """Cowes Week year landing → per-class points pages.

    The year-landing URL (e.g. /results/2025) is an index. The per-class
    series results live at a stable query-string pattern with
    resultrequest={class_id}. Class IDs 5, 10, 20, 30, 40, 50, 60, 70 cover
    IRC Classes 0–7.
    """
    if not year:
        return []
    return [
        _cw_url(f"points{year}", resultrequest=cls)
        for cls in _CW_CLASS_IDS
    ]


def cowesweek_race_expand(_seed: str, year: int | None) -> list[str]:
    """Cowes Week year landing → per-race (daily results) pages.

    Each IRC class races once per day; the ``page=results{YYYY}`` view with
    ``resultrequest={class_id}&race={n}`` renders that class's finishing
    table for race ``n`` — including each boat's **per-race IRC TCC** (the
    TCC in force for that race, which can change mid-week after a re-rating).

    This is the OPS-02-06 "follow per-race pages for TCCs" requirement: the
    series-points pages only expose the aggregate score, so the expander
    emits the per-race URLs the extractor reads TCCs from. Returns one URL
    per (class, race) pair.
    """
    if not year:
        return []
    return [
        _cw_url(f"results{year}", resultrequest=cls, race=race)
        for cls in _CW_CLASS_IDS
        for race in _CW_RACE_NUMBERS
    ]


EXPANDERS: dict[str, ExpanderFn] = {
    "cowesweek": cowesweek_year_expand,
    # Per-race expansion (OPS-02-06): per-race pages carry the per-race TCC.
    "cowesweek-races": cowesweek_race_expand,
}


def expand_for_source(source: str, seed_url: str, year: int | None) -> list[str]:
    """Return concrete leaf URLs for a source.

    Falls back to [seed_url] for sources without a registered expander,
    so the caller can treat the result uniformly regardless of mode.
    """
    fn = EXPANDERS.get(source)
    if not fn:
        return [seed_url]
    return fn(seed_url, year) or [seed_url]
