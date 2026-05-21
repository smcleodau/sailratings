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


EXPANDERS: dict[str, ExpanderFn] = {
    "cowesweek": cowesweek_year_expand,
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
