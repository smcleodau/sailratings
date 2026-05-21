"""Parity test for the Firecrawl + Claude extractor against known Sailwave events.

Sailwave's published results are static HTML pages; clubs host them on their own
domains (sailwave.com itself is the software's marketing site, not a results
index). The extractor under test is ``irc_data.discovery.extractor.extract_results``
running against markdown returned by ``irc_data.discovery.firecrawl_client.scrape_url``.

This test is **live-network and live-Claude**, so it is gated on:

- ``FIRECRAWL_API_KEY`` being set in the environment, AND
- ``ANTHROPIC_API_KEY`` being set in the environment, AND
- ``RUN_LIVE_EXTRACTOR_TESTS=1`` being explicitly opted into.

Without those, the test skips so CI runs stay hermetic and Firecrawl credits
stay unburned.

The ``KNOWN_EVENTS`` list is a hand-curated trio of long-tail Sailwave-published
events. ``expected_min_rows`` is a conservative floor — extraction is judged
green if it returns at least that many rows with a non-empty ``boat_name`` and
a non-null ``place``. Pages frequently include retired/DNF rows we tolerate.
"""

from __future__ import annotations

import os

import pytest

LIVE_OK = (
    os.environ.get("RUN_LIVE_EXTRACTOR_TESTS") == "1"
    and bool(os.environ.get("FIRECRAWL_API_KEY"))
    and bool(os.environ.get("ANTHROPIC_API_KEY"))
)

# Three public Sailwave-published event pages. Replace with site-specific
# URLs from the long tail when we cut over the per-club crons. Picked from
# public results pages that have been live for >12 months.
KNOWN_EVENTS = [
    # (url, expected_min_rows). Fill in once a sailwave event URL has been
    # verified against a known finisher count.
    # ("https://example-club.sailwave.com/event-2024", 25),
]


pytestmark = pytest.mark.skipif(
    not LIVE_OK,
    reason="set RUN_LIVE_EXTRACTOR_TESTS=1 + FIRECRAWL_API_KEY + ANTHROPIC_API_KEY",
)


@pytest.mark.skipif(not KNOWN_EVENTS, reason="no Sailwave events curated yet")
@pytest.mark.parametrize("url, expected_min_rows", KNOWN_EVENTS)
def test_sailwave_extraction_returns_results(url: str, expected_min_rows: int) -> None:
    from irc_data.discovery.extractor import extract_results
    from irc_data.discovery.firecrawl_client import scrape_url

    page = scrape_url(url, caller="test.sailwave")
    assert page.markdown, "Firecrawl returned empty markdown"

    payload = extract_results(url=url, markdown=page.markdown)
    assert payload.get("_error") is None, f"extractor error: {payload.get('_error')}"

    results = payload["results"]
    assert (
        len(results) >= expected_min_rows
    ), f"got {len(results)}, expected >= {expected_min_rows}"

    for r in results:
        assert r["boat_name"], "every row must have a boat_name"
        if r.get("status") in (None, "finished"):
            assert r["place"] is not None, "finished rows must have a place"
            assert r["place"] >= 1


def test_extractor_module_imports() -> None:
    """Lightweight import smoke. Always runs."""
    from irc_data.discovery import extractor

    assert hasattr(extractor, "extract_results")
    assert callable(extractor.extract_results)
