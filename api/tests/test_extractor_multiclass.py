"""Multi-class chunked extraction tests.

These tests verify that extract_results correctly handles pages with multiple
IRC class sections (series-points pages with 40+ rows across several class
headers). Before the chunker was added, a single 30k-char / 8k-token pass
would typically return only the first class and stop (~5–22 boats on pages
with 28–58 named boats).

Gated on live API keys — not run in CI unless RUN_LIVE_EXTRACTOR_TESTS=1.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_EXTRACTOR_TESTS") != "1"
    or not os.environ.get("GEMINI_API_KEY")
    or not os.environ.get("FIRECRAWL_API_KEY"),
    reason="Live extractor tests: set RUN_LIVE_EXTRACTOR_TESTS=1 + API keys",
)

from irc_data.discovery.extractor import extract_results
from irc_data.discovery.firecrawl_client import scrape_url


# Pages confirmed to have ≥2 IRC class sections in the markdown.
# min_rows: floor we must hit after the chunker lands (legacy row counts from DB).
MULTI_CLASS_URLS = [
    # ISORA Royal Dee Champs 2025 — 28 legacy rows, class headers "### IRC Class N Fleet"
    # was extracting ~5 rows (only first class); expect ≥20 after chunking
    (
        "https://www.isora.org/index.php/notice-board/results2/results-2025x"
        "?task=weblink.go&id=338",
        18,
    ),
    # ISORA D2D by class 2025 — 58 legacy rows, headers "### RaceName - IRC Class N Fleet"
    # was extracting ~22 rows (only a couple of classes); expect ≥40 after chunking
    (
        "https://www.isora.org/index.php/notice-board/results2/results-2025x"
        "?task=weblink.go&id=332",
        35,
    ),
]


@pytest.mark.parametrize("url, min_rows", MULTI_CLASS_URLS)
def test_multiclass_extraction_recovers_all_classes(url, min_rows):
    page = scrape_url(url, caller="test.multiclass")
    assert page.markdown, f"Firecrawl returned empty markdown for {url}"

    payload = extract_results(url=url, markdown=page.markdown)

    assert payload.get("_error") is None, f"Extraction error: {payload['_error']}"

    rows = payload["results"]
    assert len(rows) >= min_rows, (
        f"Got {len(rows)} rows, expected >= {min_rows}. "
        f"confidence={payload.get('confidence')}"
    )

    classes = {r.get("class_name") for r in rows if r.get("class_name")}
    assert len(classes) >= 2, (
        f"Expected >=2 distinct class_name values, got: {classes}"
    )
