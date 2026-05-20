"""Regression tests for the SEC-duplicate fix.

Background: the IRC TCC CSV publishes two rows for re-rated boats — the
primary cert and a secondary cert whose `boat_name` ends in " - SEC". The
old import path keyed on (sail_number, cert_number) and so created a
second `boats` row for every secondary cert, polluting the table with
~230 duplicate boats over time.

These tests pin the parser's behaviour: secondary rows are flagged with
`is_secondary=True` and the " - SEC" suffix is stripped from `boat_name`
so the canonical name is consistent across primary and secondary certs.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from irc_data.parsers.tcc_csv import _detect_secondary, parse_tcc_csv


def test_detect_secondary_suffix():
    name, is_sec = _detect_secondary("SUN FISH - SEC", None)
    assert is_sec is True
    assert name == "SUN FISH"


def test_detect_secondary_case_insensitive():
    name, is_sec = _detect_secondary("toucan - sec", None)
    assert is_sec is True
    assert name == "toucan"


def test_detect_secondary_with_extra_whitespace():
    name, is_sec = _detect_secondary("VERITE  -  SEC ", None)
    assert is_sec is True
    assert name == "VERITE"


def test_detect_secondary_via_column_flag():
    # Secondary column non-empty marks the row even without the suffix.
    name, is_sec = _detect_secondary("SUN FISH", "Y")
    assert is_sec is True
    assert name == "SUN FISH"


def test_detect_primary_passes_through():
    name, is_sec = _detect_secondary("WILD OATS XI", "")
    assert is_sec is False
    assert name == "WILD OATS XI"


def test_detect_primary_without_secondary_column():
    name, is_sec = _detect_secondary("WILD OATS XI", None)
    assert is_sec is False
    assert name == "WILD OATS XI"


def test_does_not_strip_unrelated_dashes():
    # Boats with hyphens in their names must not be falsely marked.
    name, is_sec = _detect_secondary("MR. GRAY-MATTER", None)
    assert is_sec is False
    assert name == "MR. GRAY-MATTER"


def test_parse_tcc_csv_marks_sec_rows():
    """End-to-end: a CSV with primary + secondary rows for the same boat
    produces two TCCListingRow records, the secondary one flagged."""
    headers = "Boat Name,Sail No,Cert No,TCC,Secondary\n"
    rows = "\n".join([
        "SUN FISH,3375,48835,1.0120,",
        "SUN FISH - SEC,3375,48836,1.0240,Y",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(headers + rows + "\n")
        path = Path(f.name)

    out = parse_tcc_csv(path)
    by_cert = {r.cert_number: r for r in out}
    assert "48835" in by_cert and "48836" in by_cert

    primary = by_cert["48835"]
    secondary = by_cert["48836"]
    assert primary.is_secondary is False
    assert primary.boat_name == "SUN FISH"
    assert secondary.is_secondary is True
    assert secondary.boat_name == "SUN FISH"  # suffix stripped
    assert secondary.secondary == "Y"


def test_parse_tcc_csv_detects_via_suffix_alone():
    """Some legacy CSV variants don't populate the Secondary column;
    the " - SEC" suffix alone must still flag the row."""
    headers = "Boat Name,Sail No,Cert No,TCC\n"
    rows = "TOUCAN - SEC,3322,50009,1.0080\n"
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(headers + rows)
        path = Path(f.name)

    out = parse_tcc_csv(path)
    assert len(out) == 1
    assert out[0].is_secondary is True
    assert out[0].boat_name == "TOUCAN"
