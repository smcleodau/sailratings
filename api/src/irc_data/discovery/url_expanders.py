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


def cowesweek_year_expand(_seed: str, year: int | None) -> list[str]:
    """Cowes Week year landing → 8 per-class points pages.

    The year-landing URL (e.g. /results/2025) is an index. The real per-class
    results live at a stable query-string pattern with resultrequest={class_id}.
    Class IDs 5, 10, 20, 30, 40, 50, 60, 70 cover IRC Classes 0–7.
    """
    if not year:
        return []
    class_ids = [5, 10, 20, 30, 40, 50, 60, 70]
    return [
        (
            "https://www.cowesweek.co.uk/web/code/php/main_c.php"
            f"?map=cw26&ui=cw4&style=std&override=&section=racing"
            f"&page=points{year}&resultrequest={cls}"
        )
        for cls in class_ids
    ]


def warsash_spring_series_expand(_seed: str, year: int | None) -> list[str]:
    """Warsash Spring Series landing → the per-group results index pages.

    The Black/White group results index pages link out to the static Sailwave
    result files (``sailwave.com/results/warsashsc/…``) for the current
    season.  The expander returns the two index pages; the actual Sailwave
    file URLs are discovered by ``irc_data.discovery.solent`` (they are
    fetched with plain HTTP and parsed by the existing Sailwave parser).
    ``year`` is accepted for interface uniformity but the index pages always
    reflect the current season.
    """
    return [
        "https://warsashsc.org.uk/springseries/black-group-results/",
        "https://warsashsc.org.uk/springseries/white-group-results/",
    ]


EXPANDERS: dict[str, ExpanderFn] = {
    "cowesweek": cowesweek_year_expand,
    "warsash-spring-series": warsash_spring_series_expand,
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
