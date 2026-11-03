"""Tests for the Cowes Week per-race URL expander (OPS-02-06).

"Cowes Week extractor follows per-race pages for TCCs."

The per-race expander must emit one URL per (IRC class, race) pair pointing
at the daily-results view — the pages that carry each boat's *per-race* IRC
TCC, as opposed to the series-points pages which only carry the aggregate.
"""

from __future__ import annotations

from irc_data.discovery import url_expanders as ux


def test_cowesweek_race_expand_covers_classes_and_races():
    urls = ux.cowesweek_race_expand("https://www.cowesweek.co.uk/results/2025", 2025)
    # 8 IRC classes × 8 races
    assert len(urls) == 64
    # All point at the per-race (resultsYYYY) view, not the points view.
    assert all("page=results2025" in u for u in urls)
    assert all("points2025" not in u for u in urls)


def test_cowesweek_race_expand_uses_day_and_classrequest_params():
    urls = ux.cowesweek_race_expand("seed", 2025)
    # IRC Class 0 (series 5, class code 10) race 1 -> race number 1005.
    assert any("dayrequest=1&" in u and "classrequest=10%2F1005" in u for u in urls)
    # IRC Class 1 (series 10, class code 11) on day 7 -> 11/7010 (live-verified).
    assert any("dayrequest=7&" in u and "classrequest=11%2F7010" in u for u in urls)
    # IRC Class 7 (series 70, class code 17) race 8.
    assert any("dayrequest=8&" in u and "classrequest=17%2F8070" in u for u in urls)


def test_cowesweek_urls_use_current_site_skin():
    """The old map=cw26&ui=cw4 skin answers every page with a stub."""
    for urls in (ux.cowesweek_race_expand("seed", 2025), ux.cowesweek_year_expand("seed", 2025)):
        assert all("map=cw-res&ui=cw-res" in u and "section=regatta" in u for u in urls)
        assert not any("cw26" in u or "ui=cw4" in u for u in urls)


def test_cowesweek_race_expand_requires_year():
    assert ux.cowesweek_race_expand("seed", None) == []


def test_expander_registry_has_both_cowesweek_modes():
    assert "cowesweek" in ux.EXPANDERS
    assert "cowesweek-races" in ux.EXPANDERS


def test_expand_for_source_dispatches_per_race():
    urls = ux.expand_for_source("cowesweek-races", "seed", 2025)
    assert all("page=results2025" in u for u in urls)


def test_expand_for_source_series_points_unchanged():
    urls = ux.expand_for_source("cowesweek", "seed", 2025)
    assert len(urls) == 8
    assert all("page=points2025" in u for u in urls)


def test_expand_for_source_falls_back_to_seed_for_unknown():
    assert ux.expand_for_source("unknown-src", "https://seed/", 2025) == ["https://seed/"]
