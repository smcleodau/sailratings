"""Parity test for RHKYC PDF result extraction via Firecrawl.

RHKYC publishes results as PDFs at predictable URLs under
``/storage/app/media/Sailing/result/{EVENT}/{YEAR}/{FILE}.pdf``. Firecrawl
renders the PDFs to markdown; ``extract_results`` then pulls a typed row list.

URLs were drawn from existing rows in ``race_results`` (source='rhkyc') as of
2026-05-21. Live-network and live-Claude — gated identically to the other
extractor parity tests.
"""

from __future__ import annotations

import os

import pytest

LIVE_OK = (
    os.environ.get("RUN_LIVE_EXTRACTOR_TESTS") == "1"
    and bool(os.environ.get("FIRECRAWL_API_KEY"))
    and bool(os.environ.get("ANTHROPIC_API_KEY"))
)

# Five PDFs sampled from race_results.source='rhkyc' on 2026-05-21.
PDF_URLS = [
    "https://www.rhkyc.org.hk/storage/app/media/Sailing/result/SPRING-REGATTA/2025/2025BigBoatDivision3.pdf",
    "https://www.rhkyc.org.hk/storage/app/media/Sailing/result/SPRING-REGATTA/2025/2025BigBoatDivision2.pdf",
    "https://www.rhkyc.org.hk/storage/app/media/Sailing/result/SPRING-REGATTA/2025/2025BigBoatDivision1.pdf",
    "https://www.rhkyc.org.hk/storage/app/media/Sailing/result/SPRING-REGATTA/2025/2025BigBoatDivision0.pdf",
    "https://www.rhkyc.org.hk/storage/app/media/Sailing/result/LADIES-HELM-DAY/2026/LH2026BBIRC.pdf",
]


pytestmark = pytest.mark.skipif(
    not LIVE_OK,
    reason="set RUN_LIVE_EXTRACTOR_TESTS=1 + FIRECRAWL_API_KEY + ANTHROPIC_API_KEY",
)


@pytest.mark.parametrize("url", PDF_URLS)
def test_rhkyc_pdf_extraction(url: str) -> None:
    from irc_data.discovery.extractor import extract_results
    from irc_data.discovery.firecrawl_client import scrape_url

    page = scrape_url(url, caller="test.rhkyc")
    assert page.markdown, f"Firecrawl returned empty markdown for {url}"

    payload = extract_results(url=url, markdown=page.markdown)
    assert payload.get("_error") is None, f"extractor error: {payload.get('_error')}"

    results = payload["results"]
    # PDFs typically list 5+ finishers per division.
    assert len(results) >= 5, f"got {len(results)} rows from {url}"
    for r in results:
        assert r["boat_name"], "boat_name required"
        if r.get("status") in (None, "finished"):
            assert r["place"] is not None, "finished rows must have place"
