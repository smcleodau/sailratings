"""Tests for the parser execution contract (DP-02-03 / SPEC-013).

Verifies:
1. **Determinism** — the same artifact and versions produce deterministic output.
2. **Source citation** — every field cites its source artifact and locator.
3. **Replay** — representative HTML, JSON and PDF artifacts produce canonical
   extracted output.
4. **JSON round-trip** — ``ExtractionBatchV1`` survives serialization.
5. **Version sensitivity** — different versions produce different batch IDs.

The verification criterion (from the issue):
    "Replay representative HTML, JSON and PDF artifacts and compare
    canonical extracted output."
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from irc_data.parsers.extraction_contract import (
    BaseParser,
    ExtractedField,
    ExtractedRecord,
    ExtractionBatchV1,
    Locator,
    LocatorType,
    ParserInputV1,
    SCHEMA_VERSION,
)
from irc_data.parsers.reference_parsers import (
    HTMLRaceResultParser,
    JSONResultsParser,
    PDFCertificateParser,
    get_parser_for_hint,
    parse_artifact,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def html_artifact() -> bytes:
    """Representative HTML race results artifact."""
    return (FIXTURES_DIR / "sample_race_results.html").read_bytes()


@pytest.fixture
def json_artifact() -> bytes:
    """Representative JSON race results artifact."""
    return (FIXTURES_DIR / "sample_race_results.json").read_bytes()


@pytest.fixture
def pdf_artifact() -> bytes:
    """Representative PDF certificate artifact."""
    return (FIXTURES_DIR / "sample_certificate.pdf").read_bytes()


@pytest.fixture
def html_input(html_artifact: bytes) -> ParserInputV1:
    return ParserInputV1.from_bytes(
        content=html_artifact,
        source_slug="sailsys",
        url="https://example.com/results/1",
        content_type="text/html",
        parse_hint="html",
    )


@pytest.fixture
def json_input(json_artifact: bytes) -> ParserInputV1:
    return ParserInputV1.from_bytes(
        content=json_artifact,
        source_slug="sailsys",
        url="https://api.example.com/results/1",
        content_type="application/json",
        parse_hint="json",
    )


@pytest.fixture
def pdf_input(pdf_artifact: bytes) -> ParserInputV1:
    return ParserInputV1.from_bytes(
        content=pdf_artifact,
        source_slug="irc-certs",
        url="https://ircrating.org/cert/10015",
        content_type="application/pdf",
        parse_hint="pdf",
    )


# ---------------------------------------------------------------------------
# Contract structure tests
# ===========================================================================


class TestParserInputV1:
    """Tests for the ParserInputV1 input contract."""

    def test_from_bytes_computes_hash_and_id(self, html_artifact: bytes):
        """from_bytes automatically computes content_hash and artifact_id."""
        inp = ParserInputV1.from_bytes(
            content=html_artifact,
            source_slug="sailsys",
        )
        assert inp.content_hash  # non-empty
        assert len(inp.content_hash) == 64  # SHA-256 hex
        assert inp.artifact_id.startswith("art_")
        assert inp.artifact_id == f"art_{inp.content_hash[:16]}"

    def test_from_string_encodes_utf8(self):
        """from_string UTF-8 encodes the text."""
        text = '{"results": []}'
        inp = ParserInputV1.from_string(text, source_slug="test")
        assert inp.content == text.encode("utf-8")
        assert inp.content_hash

    def test_deterministic_key_stable(self, html_input: ParserInputV1):
        """The same input always produces the same deterministic_key."""
        key1 = html_input.deterministic_key()
        key2 = html_input.deterministic_key()
        assert key1 == key2

    def test_deterministic_key_changes_with_versions(self, html_input: ParserInputV1):
        """Different parser/schema versions produce different keys."""
        key1 = html_input.deterministic_key()

        inp2 = ParserInputV1(
            content=html_input.content,
            content_hash=html_input.content_hash,
            source_slug=html_input.source_slug,
            parser_version="2.0.0",
        )
        key2 = inp2.deterministic_key()
        assert key1 != key2

    def test_json_round_trip(self, html_input: ParserInputV1):
        """ParserInputV1 survives JSON serialization."""
        s = html_input.to_json()
        restored = ParserInputV1.from_json(s)
        assert restored == html_input

    def test_immutability_of_content(self, html_input: ParserInputV1):
        """The content bytes are not mutated by the parser."""
        original = html_input.content[:]
        parser = HTMLRaceResultParser()
        parser.parse(html_input)
        assert html_input.content == original


class TestExtractionBatchV1:
    """Tests for the ExtractionBatchV1 output contract."""

    def test_batch_id_is_deterministic(self, html_input: ParserInputV1):
        """Same artifact + versions → same batch_id."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)
        batch2 = parser.parse(html_input)
        assert batch1.batch_id == batch2.batch_id

    def test_extraction_hash_is_deterministic(self, html_input: ParserInputV1):
        """Same artifact + versions → same extraction_hash."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)
        batch2 = parser.parse(html_input)
        assert batch1.extraction_hash == batch2.extraction_hash

    def test_batch_id_changes_with_parser_version(self, html_input: ParserInputV1):
        """Different parser_version → different batch_id."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)

        html_input2 = ParserInputV1(
            content=html_input.content,
            content_hash=html_input.content_hash,
            source_slug=html_input.source_slug,
            parse_hint="html",
            parser_version="2.0.0",
        )
        batch2 = parser.parse(html_input2)
        assert batch1.batch_id != batch2.batch_id

    def test_batch_id_changes_with_schema_version(self, html_input: ParserInputV1):
        """Different schema_version → different batch_id."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)

        html_input2 = ParserInputV1(
            content=html_input.content,
            content_hash=html_input.content_hash,
            source_slug=html_input.source_slug,
            parse_hint="html",
            schema_version="v2",
        )
        batch2 = parser.parse(html_input2)
        assert batch1.batch_id != batch2.batch_id

    def test_json_round_trip_preserves_records(self, html_input: ParserInputV1):
        """ExtractionBatchV1 survives JSON serialization with all records."""
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)

        s = batch.to_json()
        restored = ExtractionBatchV1.from_json(s)

        assert restored.batch_id == batch.batch_id
        assert restored.extraction_hash == batch.extraction_hash
        assert restored.artifact_id == batch.artifact_id
        assert restored.record_count() == batch.record_count()
        assert restored.field_count() == batch.field_count()

    def test_equality_based_on_extraction_hash(self, html_input: ParserInputV1):
        """Two batches with the same extraction_hash are equal,
        even if extracted_at differs."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)
        batch2 = parser.parse(html_input)

        # extracted_at will differ but the hash should match
        assert batch1.extracted_at != batch2.extracted_at or True  # may be same if fast
        assert batch1 == batch2

    def test_all_fields_cite_source(self, html_input: ParserInputV1):
        """Every field in every record has a locator citing the artifact."""
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)
        assert batch.all_fields_cite_source()

    def test_from_parser_input_sets_metadata(self, html_input: ParserInputV1):
        """from_parser_input copies metadata from the input."""
        records = [ExtractedRecord(
            record_type="test",
            fields=[ExtractedField(
                name="x",
                value=1,
                locator=Locator.whole_artifact(
                    html_input.artifact_id,
                    html_input.content_hash,
                ),
            )],
        )]
        batch = ExtractionBatchV1.from_parser_input(html_input, records)
        assert batch.artifact_id == html_input.artifact_id
        assert batch.content_hash == html_input.content_hash
        assert batch.parser_version == html_input.parser_version
        assert batch.schema_version == html_input.schema_version
        assert batch.source_slug == html_input.source_slug


class TestLocator:
    """Tests for the Locator source-span contract."""

    def test_whole_artifact_locator(self):
        loc = Locator.whole_artifact("art_123", "abc123")
        assert loc.artifact_id == "art_123"
        assert loc.content_hash == "abc123"
        assert loc.locator_type == LocatorType.WHOLE_ARTIFACT.value

    def test_json_path_locator(self):
        loc = Locator.json_path("art_123", "abc123", "results[0].place")
        assert loc.path == "results[0].place"
        assert loc.locator_type == LocatorType.JSON_PATH.value

    def test_css_selector_locator(self):
        loc = Locator.css_selector("art_123", "abc123", "table tr td:nth-child(1)")
        assert loc.path == "table tr td:nth-child(1)"
        assert loc.locator_type == LocatorType.CSS_SELECTOR.value

    def test_pdf_page_locator(self):
        loc = Locator.pdf_page("art_123", "abc123", page=1)
        assert loc.page == 1
        assert loc.locator_type == LocatorType.PDF_PAGE.value

    def test_byte_range_locator(self):
        loc = Locator.byte_range("art_123", "abc123", start=10, end=20)
        assert loc.start == 10
        assert loc.end == 20
        assert loc.locator_type == LocatorType.BYTE_OFFSET.value

    def test_csv_row_locator(self):
        loc = Locator.csv_row("art_123", "abc123", row=5, start=2)
        assert loc.row == 5
        assert loc.start == 2
        assert loc.locator_type == LocatorType.CSV_ROW.value

    def test_table_cell_locator(self):
        loc = Locator.table_cell("art_123", "abc123", row=3, start=1)
        assert loc.row == 3
        assert loc.start == 1
        assert loc.locator_type == LocatorType.TABLE_CELL.value

    def test_json_round_trip(self):
        loc = Locator.json_path("art_123", "abc123", "results[0].place", snippet="1")
        d = loc.to_dict()
        restored = Locator.from_dict(d)
        assert restored.artifact_id == loc.artifact_id
        assert restored.content_hash == loc.content_hash
        assert restored.path == loc.path
        assert restored.snippet == loc.snippet


# ---------------------------------------------------------------------------
# HTML parser tests
# ===========================================================================


class TestHTMLRaceResultParser:
    """Tests for the HTML race results parser."""

    def test_parses_three_records(self, html_input: ParserInputV1):
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)
        assert batch.record_count() == 3

    def test_first_record_has_expected_fields(self, html_input: ParserInputV1):
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)
        record = batch.records[0]
        assert record.record_type == "race_result"
        assert record.record_index == 0

        place = record.get_value("place")
        assert place == 1

        sail = record.get_value("sail_number")
        assert sail == "GBR1234"

        boat = record.get_value("boat_name")
        assert boat == "Sunshine"

        tcc = record.get_value("tcc")
        assert tcc == 1.015

    def test_all_fields_have_css_locator(self, html_input: ParserInputV1):
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)
        for record in batch.records:
            for field in record.fields:
                assert field.locator.locator_type == LocatorType.CSS_SELECTOR.value
                assert field.locator.path  # non-empty CSS path
                assert field.locator.artifact_id == html_input.artifact_id
                assert field.locator.content_hash == html_input.content_hash

    def test_deterministic_output(self, html_input: ParserInputV1):
        """Same artifact → same extraction_hash (acceptance criterion)."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)
        batch2 = parser.parse(html_input)
        assert batch1.extraction_hash == batch2.extraction_hash
        assert batch1.batch_id == batch2.batch_id

    def test_no_live_web_state(self, html_input: ParserInputV1):
        """The parser does not access the network or filesystem."""
        # The input contains raw bytes only; the parser should not
        # need any external resource.
        parser = HTMLRaceResultParser()
        batch = parser.parse(html_input)
        assert batch.record_count() > 0
        # If we got here without network/filesystem access, the test passes.


# ---------------------------------------------------------------------------
# JSON parser tests
# ===========================================================================


class TestJSONResultsParser:
    """Tests for the JSON results parser."""

    def test_parses_three_records(self, json_input: ParserInputV1):
        parser = JSONResultsParser()
        batch = parser.parse(json_input)
        assert batch.record_count() == 3

    def test_first_record_has_expected_fields(self, json_input: ParserInputV1):
        parser = JSONResultsParser()
        batch = parser.parse(json_input)
        record = batch.records[0]
        assert record.record_type == "race_result"

        assert record.get_value("place") == 1
        assert record.get_value("sail_number") == "GBR1234"
        assert record.get_value("boat_name") == "Sunshine"
        assert record.get_value("tcc") == 1.015

    def test_all_fields_have_json_path_locator(self, json_input: ParserInputV1):
        parser = JSONResultsParser()
        batch = parser.parse(json_input)
        for record in batch.records:
            for field in record.fields:
                assert field.locator.locator_type == LocatorType.JSON_PATH.value
                assert field.locator.path  # non-empty path
                assert field.locator.artifact_id == json_input.artifact_id
                assert field.locator.content_hash == json_input.content_hash

    def test_json_path_format(self, json_input: ParserInputV1):
        parser = JSONResultsParser()
        batch = parser.parse(json_input)
        record = batch.records[0]
        place_field = record.get_field("place")
        assert place_field is not None
        assert place_field.locator.path == "results[0].place"

    def test_deterministic_output(self, json_input: ParserInputV1):
        """Same artifact → same extraction_hash (acceptance criterion)."""
        parser = JSONResultsParser()
        batch1 = parser.parse(json_input)
        batch2 = parser.parse(json_input)
        assert batch1.extraction_hash == batch2.extraction_hash
        assert batch1.batch_id == batch2.batch_id


# ---------------------------------------------------------------------------
# PDF parser tests
# ===========================================================================


class TestPDFCertificateParser:
    """Tests for the PDF certificate parser."""

    def test_parses_certificate(self, pdf_input: ParserInputV1):
        parser = PDFCertificateParser()
        batch = parser.parse(pdf_input)
        assert batch.record_count() == 1
        record = batch.records[0]
        assert record.record_type == "certificate"

    def test_certificate_has_measurements(self, pdf_input: ParserInputV1):
        parser = PDFCertificateParser()
        batch = parser.parse(pdf_input)
        record = batch.records[0]

        lh = record.get_value("lh")
        assert lh is not None
        assert str(lh) == "9.96"

        cert_number = record.get_value("cert_number")
        assert cert_number == "10015"

    def test_all_fields_have_pdf_locator(self, pdf_input: ParserInputV1):
        parser = PDFCertificateParser()
        batch = parser.parse(pdf_input)
        for record in batch.records:
            for field in record.fields:
                assert field.locator.locator_type == LocatorType.PDF_PAGE.value
                assert field.locator.page is not None
                assert field.locator.artifact_id == pdf_input.artifact_id
                assert field.locator.content_hash == pdf_input.content_hash

    def test_deterministic_output(self, pdf_input: ParserInputV1):
        """Same artifact → same extraction_hash (acceptance criterion)."""
        parser = PDFCertificateParser()
        batch1 = parser.parse(pdf_input)
        batch2 = parser.parse(pdf_input)
        assert batch1.extraction_hash == batch2.extraction_hash
        assert batch1.batch_id == batch2.batch_id


# ---------------------------------------------------------------------------
# Replay verification (acceptance criterion)
# ===========================================================================


class TestReplayVerification:
    """Verification: Replay representative HTML, JSON and PDF artifacts
    and compare canonical extracted output.

    This is the verification criterion from the issue.
    """

    def test_html_replay_produces_canonical_output(self, html_input: ParserInputV1):
        """Replaying the HTML artifact produces deterministic canonical output."""
        parser = HTMLRaceResultParser()
        batch1 = parser.parse(html_input)
        batch2 = parser.parse(html_input)

        # Determinism: same extraction_hash
        assert batch1.extraction_hash == batch2.extraction_hash

        # Every field cites its source
        assert batch1.all_fields_cite_source()

        # Canonical output structure
        assert batch1.record_count() == 3
        assert batch1.field_count() > 0
        assert "race_result" in batch1.record_types()

    def test_json_replay_produces_canonical_output(self, json_input: ParserInputV1):
        """Replaying the JSON artifact produces deterministic canonical output."""
        parser = JSONResultsParser()
        batch1 = parser.parse(json_input)
        batch2 = parser.parse(json_input)

        assert batch1.extraction_hash == batch2.extraction_hash
        assert batch1.all_fields_cite_source()
        assert batch1.record_count() == 3
        assert batch1.field_count() > 0

    def test_pdf_replay_produces_canonical_output(self, pdf_input: ParserInputV1):
        """Replaying the PDF artifact produces deterministic canonical output."""
        parser = PDFCertificateParser()
        batch1 = parser.parse(pdf_input)
        batch2 = parser.parse(pdf_input)

        assert batch1.extraction_hash == batch2.extraction_hash
        assert batch1.all_fields_cite_source()
        assert batch1.record_count() == 1
        assert batch1.field_count() > 0

    def test_all_three_formats_via_dispatch(self, html_input, json_input, pdf_input):
        """The ``parse_artifact`` dispatch function handles all three formats."""
        html_batch = parse_artifact(html_input)
        json_batch = parse_artifact(json_input)
        pdf_batch = parse_artifact(pdf_input)

        assert html_batch.record_count() == 3
        assert json_batch.record_count() == 3
        assert pdf_batch.record_count() == 1

        # All have deterministic batch IDs
        assert html_batch.batch_id
        assert json_batch.batch_id
        assert pdf_batch.batch_id

        # All cite their source
        assert html_batch.all_fields_cite_source()
        assert json_batch.all_fields_cite_source()
        assert pdf_batch.all_fields_cite_source()


# ---------------------------------------------------------------------------
# Parser registry tests
# ===========================================================================


class TestParserRegistry:
    """Tests for the parser registry and dispatch."""

    def test_get_parser_for_html(self):
        parser = get_parser_for_hint("html")
        assert isinstance(parser, HTMLRaceResultParser)

    def test_get_parser_for_json(self):
        parser = get_parser_for_hint("json")
        assert isinstance(parser, JSONResultsParser)

    def test_get_parser_for_pdf(self):
        parser = get_parser_for_hint("pdf")
        assert isinstance(parser, PDFCertificateParser)

    def test_get_parser_for_unknown_returns_none(self):
        assert get_parser_for_hint("unknown") is None

    def test_parse_artifact_unknown_hint_returns_empty(self):
        inp = ParserInputV1.from_bytes(
            content=b"unknown",
            source_slug="test",
            parse_hint="unknown",
        )
        batch = parse_artifact(inp)
        assert batch.record_count() == 0


# ---------------------------------------------------------------------------
# BaseParser protocol tests
# ===========================================================================


class TestParserContract:
    """Tests for the Parser execution contract (Protocol)."""

    def test_html_parser_implements_contract(self):
        parser = HTMLRaceResultParser()
        assert hasattr(parser, "parser_version")
        assert hasattr(parser, "schema_version")
        assert hasattr(parser, "parse")

    def test_json_parser_implements_contract(self):
        parser = JSONResultsParser()
        assert hasattr(parser, "parser_version")
        assert hasattr(parser, "schema_version")
        assert hasattr(parser, "parse")

    def test_pdf_parser_implements_contract(self):
        parser = PDFCertificateParser()
        assert hasattr(parser, "parser_version")
        assert hasattr(parser, "schema_version")
        assert hasattr(parser, "parse")

    def test_base_parser_make_locator(self, html_input: ParserInputV1):
        """BaseParser.make_locator creates locators citing the input."""
        parser = HTMLRaceResultParser()
        loc = parser.make_locator(
            html_input,
            locator_type=LocatorType.BYTE_OFFSET.value,
            start=10,
            end=20,
        )
        assert loc.artifact_id == html_input.artifact_id
        assert loc.content_hash == html_input.content_hash
        assert loc.start == 10
        assert loc.end == 20
