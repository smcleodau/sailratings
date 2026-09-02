"""Tests for the IRC certificate PDF scraper (DP-00-05).

Policy: v1.0

Test categories:
  1. parse_search_results — recorded-fixture HTML parsing
  2. _parse_filename       — filename decomposition
  3. scrape_irc_pdfs       — idempotency: second run fetches zero new certs
  4. scrape_irc_pdfs       — kill switch prevents collection
  5. scrape_irc_pdfs       — window check blocks out-of-hours collection
  6. scrape_irc_pdfs       — max_fetches cap is respected

All tests run without real network calls. Real HTML fixtures are recorded from
ircrating.org and committed to tests/scrapers/fixtures/.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from irc_data.scrapers.irc_pdf import (
    CertRecord,
    RunLedger,
    _parse_filename,
    parse_search_results,
    scrape_irc_pdfs,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helper: minimal valid PDF bytes
# ---------------------------------------------------------------------------

STUB_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"
STUB_PDF_HASH = hashlib.sha256(STUB_PDF).hexdigest()

# Alternate PDF bytes for re-issue test (same cert, different content)
STUB_PDF_V2 = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Version 2 >>\nendobj\n%%EOF"
STUB_PDF_V2_HASH = hashlib.sha256(STUB_PDF_V2).hexdigest()


# ---------------------------------------------------------------------------
# 1. parse_search_results — recorded fixtures
# ---------------------------------------------------------------------------


class TestParseSearchResults:
    """Tests against real recorded HTML fixtures."""

    def test_single_result_cert_14163(self):
        """Fixture: search for cert 14163 — should return exactly one record."""
        html = (FIXTURES / "irc_pdf_search_14163.html").read_text(encoding="utf-8")
        records = parse_search_results(html)
        assert len(records) == 1
        rec = records[0]
        assert rec.cert_no == "14163"
        assert rec.boat_name == "KOA"
        assert rec.sail_no == "AUS52152"
        assert rec.filename == "14163_KOA_AUS52152.pdf"
        assert "irc_dl=14163_KOA_AUS52152.pdf" in rec.download_url
        assert "tk=" in rec.download_url
        assert rec.listing_ref == "https://ircrating.org/boat-data-for-valid-irc-certificates/"

    def test_no_results_fixture(self):
        """Fixture: search for non-existent term — should return empty list."""
        html = (FIXTURES / "irc_pdf_search_noresults.html").read_text(encoding="utf-8")
        records = parse_search_results(html)
        assert records == []

    def test_multi_result_fixture(self):
        """Fixture: search for 'KOA' — should return multiple certs."""
        html = (FIXTURES / "irc_pdf_search_multi.html").read_text(encoding="utf-8")
        records = parse_search_results(html)
        assert len(records) >= 2, f"expected >=2 results, got {len(records)}"

        # At least one should be cert 14163
        cert_nos = {r.cert_no for r in records}
        assert "14163" in cert_nos, f"expected cert 14163 in results, got {cert_nos}"

        # SEC cert should be present (48182_KOA - SEC_AUS52152.pdf)
        filenames = {r.filename for r in records}
        assert any("SEC" in f for f in filenames), "expected a SEC cert in results"

    def test_sec_cert_filename_parsing(self):
        """SEC endorsement filenames contain spaces and hyphens."""
        html = (FIXTURES / "irc_pdf_search_multi.html").read_text(encoding="utf-8")
        records = parse_search_results(html)
        sec_records = [r for r in records if "SEC" in r.filename]
        assert len(sec_records) >= 1
        sec = sec_records[0]
        # Cert no should be numeric
        assert sec.cert_no.isdigit(), f"expected numeric cert_no, got {sec.cert_no!r}"
        # Download URL must be valid
        assert sec.download_url.startswith("https://ircrating.org/?irc_dl=")

    def test_download_url_is_unescaped(self):
        """Download URLs must be HTML-decoded (&amp; → &)."""
        html = (FIXTURES / "irc_pdf_search_14163.html").read_text(encoding="utf-8")
        records = parse_search_results(html)
        assert records
        url = records[0].download_url
        assert "&amp;" not in url, "HTML entities should be decoded"
        assert "&#038;" not in url, "HTML numeric entities should be decoded"
        assert "&tk=" in url, "decoded URL should contain &tk="


# ---------------------------------------------------------------------------
# 2. _parse_filename — unit tests
# ---------------------------------------------------------------------------


class TestParseFilename:
    def test_simple(self):
        assert _parse_filename("14163_KOA_AUS52152.pdf") == ("14163", "KOA", "AUS52152")

    def test_no_extension(self):
        assert _parse_filename("14163_KOA_AUS52152") == ("14163", "KOA", "AUS52152")

    def test_sec_with_spaces(self):
        cert_no, boat_name, sail_no = _parse_filename("48182_KOA - SEC_AUS52152.pdf")
        assert cert_no == "48182"
        assert boat_name == "KOA - SEC"
        assert sail_no == "AUS52152"

    def test_two_parts_only(self):
        cert_no, boat_name, sail_no = _parse_filename("12345_MYBOAT.pdf")
        assert cert_no == "12345"
        assert boat_name == "MYBOAT"
        assert sail_no == ""

    def test_one_part_fallback(self):
        cert_no, boat_name, sail_no = _parse_filename("justname.pdf")
        assert cert_no == "justname"
        assert boat_name == ""
        assert sail_no == ""


# ---------------------------------------------------------------------------
# Helpers for scraper tests
# ---------------------------------------------------------------------------


def _build_search_html(cert_no: str, boat_name: str, sail_no: str, token: str = "12345.abc") -> str:
    """Build a minimal search-result HTML page for a single cert."""
    filename = f"{cert_no}_{boat_name}_{sail_no}.pdf"
    return f"""
<html><body>
<div id="pdf-results">
<p>{filename} <a href="https://ircrating.org/?irc_dl={filename}&amp;tk={token}"
   rel="nofollow noopener">Download</a></p>
</div>
</body></html>
"""


def _build_no_results_html() -> str:
    return """
<html><body>
<div id="pdf-results">
<p>No files found.</p>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# 3. Idempotency test
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Second run: same certs → zero new artifacts stored."""

    def test_second_run_fetches_zero_new(self, tmp_path):
        """Running the scraper twice for the same certs stores nothing on the second run."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))

        cert_nos = ["14163"]
        search_html = _build_search_html("14163", "KOA", "AUS52152")

        call_count = {"search": 0, "pdf": 0}

        def _mock_request(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                call_count["search"] += 1
                return httpx.Response(200, text=search_html)
            else:
                call_count["pdf"] += 1
                return httpx.Response(
                    200,
                    content=STUB_PDF,
                    headers={"Content-Type": "application/pdf"},
                )

        transport = httpx.MockTransport(_mock_request)

        # Patch _make_client to use the mock transport
        with patch("irc_data.scrapers.irc_pdf._make_client") as mock_make_client:
            mock_client = httpx.Client(transport=transport, follow_redirects=True)
            mock_make_client.return_value = mock_client

            # First run
            ledger1 = scrape_irc_pdfs(
                cert_nos=cert_nos,
                store=store,
                enforce_window=False,
                check_kill_switch=False,
            )

        assert ledger1.certs_new == 1
        assert ledger1.certs_unchanged == 0

        call_count2 = {"search": 0, "pdf": 0}

        def _mock_request2(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                call_count2["search"] += 1
                return httpx.Response(200, text=search_html)
            else:
                call_count2["pdf"] += 1
                return httpx.Response(
                    200,
                    content=STUB_PDF,
                    headers={"Content-Type": "application/pdf"},
                )

        transport2 = httpx.MockTransport(_mock_request2)

        with patch("irc_data.scrapers.irc_pdf._make_client") as mock_make_client2:
            mock_client2 = httpx.Client(transport=transport2, follow_redirects=True)
            mock_make_client2.return_value = mock_client2

            # Second run — same store, same PDF bytes
            ledger2 = scrape_irc_pdfs(
                cert_nos=cert_nos,
                store=store,
                enforce_window=False,
                check_kill_switch=False,
            )

        # No new objects should be stored (idempotent)
        assert ledger2.certs_new == 0, f"expected 0 new on second run, got {ledger2.certs_new}"
        assert ledger2.certs_unchanged == 1

    def test_reissue_detected_as_new(self, tmp_path):
        """If same cert number is reissued with different bytes, it's stored as new."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))
        cert_nos = ["14163"]
        search_html = _build_search_html("14163", "KOA", "AUS52152")

        def _mock_v1(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, text=search_html)
            return httpx.Response(
                200,
                content=STUB_PDF,
                headers={"Content-Type": "application/pdf"},
            )

        def _mock_v2(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, text=search_html)
            return httpx.Response(
                200,
                content=STUB_PDF_V2,  # Different bytes = reissued cert
                headers={"Content-Type": "application/pdf"},
            )

        with patch("irc_data.scrapers.irc_pdf._make_client") as m:
            m.return_value = httpx.Client(
                transport=httpx.MockTransport(_mock_v1), follow_redirects=True
            )
            ledger1 = scrape_irc_pdfs(
                cert_nos=cert_nos, store=store, enforce_window=False, check_kill_switch=False
            )

        assert ledger1.certs_new == 1

        with patch("irc_data.scrapers.irc_pdf._make_client") as m:
            m.return_value = httpx.Client(
                transport=httpx.MockTransport(_mock_v2), follow_redirects=True
            )
            ledger2 = scrape_irc_pdfs(
                cert_nos=cert_nos, store=store, enforce_window=False, check_kill_switch=False
            )

        # Reissued cert has different hash → stored as new artifact
        assert ledger2.certs_new == 1, "reissued cert (different bytes) should be stored as new"
        # Two distinct objects in the store
        assert store.count() == 2


# ---------------------------------------------------------------------------
# 4. Kill switch test
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_kill_switch_prevents_collection(self, tmp_path):
        """When the source is disabled, scrape_irc_pdfs returns immediately."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))

        mock_engine = MagicMock()
        # Simulate the kill switch being active (enabled=False)
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (False,)

        fetch_called = {"called": False}

        def _mock_request(request: httpx.Request) -> httpx.Response:
            fetch_called["called"] = True
            return httpx.Response(200, text="should not be called")

        with patch("irc_data.scrapers.irc_pdf._make_client") as m:
            m.return_value = httpx.Client(
                transport=httpx.MockTransport(_mock_request), follow_redirects=True
            )
            ledger = scrape_irc_pdfs(
                cert_nos=["14163"],
                store=store,
                enforce_window=False,
                check_kill_switch=True,
                db_engine=mock_engine,
            )

        assert ledger.status == "kill_switch"
        assert not fetch_called["called"], "No HTTP requests should be made when kill switch is active"
        assert store.count() == 0


# ---------------------------------------------------------------------------
# 5. Collection window test
# ---------------------------------------------------------------------------


class TestCollectionWindow:
    def test_outside_window_aborts(self, tmp_path):
        """Scraper should abort when called outside the 01:00–06:00 window."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))

        # Patch is_within_collection_window to return False
        with patch("irc_data.scrapers.irc_pdf.is_within_collection_window", return_value=False):
            ledger = scrape_irc_pdfs(
                cert_nos=["14163"],
                store=store,
                enforce_window=True,
                check_kill_switch=False,
            )

        assert ledger.status == "window_closed"
        assert store.count() == 0

    def test_within_window_proceeds(self, tmp_path):
        """Scraper runs normally when inside the collection window."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))
        search_html = _build_search_html("14163", "KOA", "AUS52152")

        def _mock(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, text=search_html)
            return httpx.Response(200, content=STUB_PDF, headers={"Content-Type": "application/pdf"})

        with patch("irc_data.scrapers.irc_pdf.is_within_collection_window", return_value=True):
            with patch("irc_data.scrapers.irc_pdf._make_client") as m:
                m.return_value = httpx.Client(
                    transport=httpx.MockTransport(_mock), follow_redirects=True
                )
                ledger = scrape_irc_pdfs(
                    cert_nos=["14163"],
                    store=store,
                    enforce_window=True,
                    check_kill_switch=False,
                )

        assert ledger.status == "ok"
        assert ledger.certs_new == 1


# ---------------------------------------------------------------------------
# 6. max_fetches cap test
# ---------------------------------------------------------------------------


class TestMaxFetchesCap:
    def test_cap_respected(self, tmp_path):
        """Scraper stops after max_fetches is reached."""
        from irc_data.sources.provenance import RawObjectStore

        store = RawObjectStore(str(tmp_path / "raw_store"))
        # 10 cert numbers; each requires 2 fetches (POST + GET)
        cert_nos = [str(i) for i in range(10000, 10010)]

        def _make_html(cert_no: str) -> str:
            return _build_search_html(cert_no, "BOAT", f"AUS{cert_no}")

        total_fetches = {"n": 0}

        def _mock(request: httpx.Request) -> httpx.Response:
            total_fetches["n"] += 1
            if request.method == "POST":
                # Extract the cert number from the body
                body = request.content.decode()
                cert_no = body.split("pdf_search=")[-1] if "pdf_search=" in body else "10000"
                return httpx.Response(200, text=_make_html(cert_no))
            return httpx.Response(200, content=STUB_PDF, headers={"Content-Type": "application/pdf"})

        with patch("irc_data.scrapers.irc_pdf._make_client") as m:
            m.return_value = httpx.Client(
                transport=httpx.MockTransport(_mock), follow_redirects=True
            )
            ledger = scrape_irc_pdfs(
                cert_nos=cert_nos,
                store=store,
                max_fetches=4,  # Very small cap
                enforce_window=False,
                check_kill_switch=False,
            )

        # Should stop well before processing all 10 certs
        assert ledger.fetch_count <= 4
        assert ledger.certs_new <= 2  # At most 2 complete cert fetches (2 fetches each)


# ---------------------------------------------------------------------------
# 7. RunLedger tests
# ---------------------------------------------------------------------------


class TestRunLedger:
    def test_to_dict_structure(self):
        ledger = RunLedger("irc-certs", "v1.0")
        ledger.certs_found = 5
        ledger.certs_new = 3
        ledger.add_error("9999", "search: timeout")
        ledger.finish("ok")

        d = ledger.to_dict()
        assert d["source_slug"] == "irc-certs"
        assert d["policy_version"] == "v1.0"
        assert d["certs_found"] == 5
        assert d["certs_new"] == 3
        assert d["error_count"] == 1
        assert d["status"] == "ok"
        assert d["finished_at"] is not None

    def test_finish_sets_timestamp(self):
        ledger = RunLedger("irc-certs", "v1.0")
        assert ledger.finished_at is None
        ledger.finish("ok")
        assert ledger.finished_at is not None

    def test_errors_capped_in_to_dict(self):
        ledger = RunLedger("irc-certs", "v1.0")
        for i in range(50):
            ledger.add_error(str(i), f"error {i}")
        ledger.finish("ok")
        d = ledger.to_dict()
        # to_dict caps at 20
        assert len(d["errors"]) == 20
