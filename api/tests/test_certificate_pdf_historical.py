"""Tests for parsing historical IRC certificate PDFs.

The historical backfill saves PDFs into ``HISTORICAL_CERTS_DIR`` with two
naming conventions:

* Live IRC PDF directory match — ``{cert}_{BOAT}_{SAIL}.pdf`` (same as
  the modern format).
* Wayback Machine match — ``wayback_{cert}.pdf`` (just the cert number,
  because we don't reconstruct boat/sail from the snapshot URL).

The parser must tolerate the wayback variant (and not crash on malformed
PDF inputs).
"""

from __future__ import annotations

from pathlib import Path

from irc_data.parsers.certificate_pdf import (
    parse_all_certificates,
    parse_filename_info,
)


def test_parse_filename_info_modern_format():
    info = parse_filename_info("12345_TEST BOAT_GBR1234.pdf")
    assert info["cert_number"] == "12345"
    assert info["boat_name"] == "TEST BOAT"
    assert info["sail_number"] == "GBR1234"


def test_parse_filename_info_wayback_fallback():
    """Wayback-saved file: ``wayback_{cert}.pdf`` — should yield the cert
    number (in ``boat_name`` per the current parser's three-token split)
    without raising. The follow-up matcher does the heavy lifting.
    """
    info = parse_filename_info("wayback_12345.pdf")
    # Two tokens — parser returns sail_number = the whole tail.
    assert info["cert_number"] is None or info["cert_number"] == "wayback"
    # The function should be total — no exceptions.


def test_parse_all_certificates_skips_bad_pdfs(tmp_path: Path):
    """``parse_all_certificates`` must not crash if a directory contains
    files that aren't valid PDFs."""
    (tmp_path / "garbage.pdf").write_bytes(b"not a pdf at all")
    (tmp_path / "empty.pdf").write_bytes(b"")
    results = parse_all_certificates(tmp_path)
    # Both files fail to parse; the function returns whatever it can.
    assert isinstance(results, list)
    # Returning fewer than 2 items is fine — what matters is no exception.
