"""DP-06-03 golden verification — map the selected source (irc-tcc) to
canonical assertions.

Golden source records (real-shape IRC TCC listing CSV fixtures) are run
through the certified parse → transform path and compared against
expected assertions and provenance:

* **expected assertions** — the exact canonical payload for each
  publishable golden row;
* **expected rejects** — golden rows that must not publish, with
  machine-readable reasons;
* **mapping coverage** — every required canonical field has a mapping,
  and every extracted source field is mapped or explicitly unsupported
  (the acceptance criterion);
* **provenance / lineage** — every assertion's lineage query reaches the
  raw artifact (content-hash verified) and resolves the exact CSV span
  the value was read from;
* **determinism** — replaying the golden artifact yields identical
  transformation ids, assertion ids and hashes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from irc_data.parsers.extraction_contract import (
    ExtractionBatchV1,
    Locator,
    LocatorType,
    ParserInputV1,
)
from irc_data.parsers.tcc_listing_parser import IRCTCCListingParser
from irc_data.transform.irc_tcc_mapping import (
    ASSERTION_TYPE,
    CANONICAL_UNITS,
    FIELD_MAPPINGS,
    MAPPING_VERSION,
    SOURCE_SLUG,
    UNSUPPORTED_SOURCE_FIELDS,
    IRCTCCListingTransformer,
    audit_mapping_coverage,
    field_mapping_table,
)
from irc_data.transform.lineage import (
    LineageIndex,
    index_batch,
    trace_assertion,
    verify_lineage,
)
from irc_data.transform.transformation_contract import TransformationBatchV1

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN_2026 = FIXTURES / "irc_tcc_listing_golden.csv"
GOLDEN_2009 = FIXTURES / "irc_tcc_listing_2009_golden.csv"

SOURCE_URL = (
    "https://ircrating.org/wp-content/uploads/2026/03/ClubListing_20260301.csv"
)


def _golden_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _make_input(content: bytes, url: str = SOURCE_URL) -> ParserInputV1:
    return ParserInputV1(
        content=content,
        content_hash=hashlib.sha256(content).hexdigest(),
        source_slug=SOURCE_SLUG,
        url=url,
        content_type="text/csv",
        parse_hint="csv",
        parser_version=IRCTCCListingParser.parser_version,
    )


def _parse(content: bytes, url: str = SOURCE_URL) -> ExtractionBatchV1:
    return IRCTCCListingParser().parse(_make_input(content, url))


def _transform(content: bytes, url: str = SOURCE_URL) -> TransformationBatchV1:
    batch = _parse(content, url)
    return IRCTCCListingTransformer().transform(batch)


@pytest.fixture(scope="module")
def golden_2026() -> bytes:
    return _golden_bytes(GOLDEN_2026)


@pytest.fixture(scope="module")
def golden_2009() -> bytes:
    return _golden_bytes(GOLDEN_2009)


@pytest.fixture(scope="module")
def tx_2026(golden_2026) -> TransformationBatchV1:
    return _transform(golden_2026)


# ---------------------------------------------------------------------------
# Golden: parser preserves source spans
# ---------------------------------------------------------------------------


class TestParserSpans:
    def test_every_field_cites_artifact(self, golden_2026):
        batch = _parse(golden_2026)
        assert batch.records, "expected records from golden fixture"
        assert batch.all_fields_cite_source()

    def test_every_locator_is_csv_row_span(self, golden_2026):
        batch = _parse(golden_2026)
        for record in batch.records:
            for f in record.fields:
                assert f.locator.locator_type == LocatorType.CSV_ROW.value
                assert f.locator.row == record.record_index
                assert f.locator.artifact_id == batch.artifact_id
                assert f.locator.content_hash == batch.content_hash

    def test_record_count_matches_csv_rows(self, golden_2026):
        batch = _parse(golden_2026)
        # 7 data rows in the golden 2026 fixture
        assert len(batch.records) == 7
        assert [r.record_index for r in batch.records] == list(range(7))

    def test_secondary_suffix_stripped_and_flagged(self, golden_2026):
        batch = _parse(golden_2026)
        sec = batch.records[1]  # "SUN FISH - SEC"
        assert sec.get_value("boat_name") == "SUN FISH"
        assert sec.get_value("is_secondary") is True
        primary = batch.records[0]
        assert primary.get_value("is_secondary") is False

    def test_sh_suffix_flagged(self, golden_2026):
        batch = _parse(golden_2026)
        sh = batch.records[3]  # "NO DOUBT (SH)"
        assert sh.get_value("boat_name") == "NO DOUBT"
        assert sh.get_value("is_secondary") is True

    def test_2009_format_column_aliases(self, golden_2009):
        batch = _parse(
            golden_2009,
            url="https://web.archive.org/web/2009/ClubListing_2009.csv",
        )
        row = batch.records[0]
        # 'Valid Date' → issue_date, 'SYSCertYear' → cert_year,
        # 'LOA' → lh, 'TCC Non spi' → non_spi_tcc, 'E' → endorsed.
        assert row.get_value("issue_date") == "2009-05-01"
        assert row.get_value("cert_year") == "2009"
        assert row.get_value("lh") == "10.04"
        assert row.get_value("non_spi_tcc") == "0.991"
        assert row.get_value("endorsed") == "E"


# ---------------------------------------------------------------------------
# Golden: expected canonical assertions
# ---------------------------------------------------------------------------


class TestGoldenAssertions:
    def test_publishable_and_reject_partition(self, tx_2026):
        # 7 golden rows: 5 publishable, 2 rejected (bad TCC, blank sail).
        assert len(tx_2026.assertions) == 5
        assert len(tx_2026.rejects) == 2
        assert tx_2026.asserts_disjoint_partition()

    def test_golden_primary_row_payload(self, tx_2026):
        # Row 0 is the SUN FISH primary cert (row 1 shares the sail number).
        sun = next(
            a for a in tx_2026.assertions
            if a.lineage.source_record_index == 0
        )
        assert sun.data["sail_number"] == "GBR8310"
        d = sun.data
        assert d["boat_name"] == "SUN FISH"
        assert d["cert_number"] == "IRC12345"
        assert d["issue_date"] == "2026-01-15"
        assert d["cert_year"] == 2026
        assert d["tcc"] == "1.015"
        assert d["non_spi_tcc"] == "0.998"
        assert d["crew"] == 9
        assert d["dlr"] == 196
        assert d["lh"] == "9.99"
        assert d["beam"] == "3.38"
        assert d["draft"] == "1.98"
        assert d["headsails"] == 6
        assert d["flying_headsails"] == 2
        assert d["spinnakers"] == 3
        assert d["series_date"] == 2008
        assert d["age_date"] == 2008
        assert d["racing_area"] == 1
        assert d["ssb_base_value"] == 28
        assert d["stix"] == 33
        assert d["avs"] == 118
        assert d["category"] == "Cat 3"
        assert d["valid_code"] == "VALID"
        assert d["is_secondary"] is False

    def test_units_attached_and_canonical(self, tx_2026):
        for a in tx_2026.assertions:
            units = a.data.get("units") or {}
            assert units, "every assertion carries its unit declarations"
            for field_name, sem in units.items():
                declared = CANONICAL_UNITS[field_name]
                # Source already publishes canonical units → identity.
                assert sem["conversion"] == "identity"
                assert sem["canonical_unit"] == declared.canonical_unit

    def test_secondary_row_assertion(self, tx_2026):
        # Two rows share sail GBR8310 (primary + SEC); both publish, the
        # secondary flagged.
        secs = [
            a for a in tx_2026.assertions
            if a.data["sail_number"] == "GBR8310" and a.data["is_secondary"]
        ]
        assert len(secs) == 1
        assert secs[0].data["boat_name"] == "SUN FISH"
        assert secs[0].data["tcc"] == "1.021"
        assert secs[0].data["endorsed"] == "Y"

    def test_missing_optional_fields_publish_as_none(self, tx_2026):
        by_sail = {a.data["sail_number"]: a for a in tx_2026.assertions}
        mystery = by_sail["GBR0000X"]  # blank Cert No in the fixture
        assert mystery.data["cert_number"] is None
        assert mystery.data["tcc"] == "1.005"

    def test_2009_golden_row(self, golden_2009):
        tx = _transform(
            golden_2009,
            url="https://web.archive.org/web/2009/ClubListing_2009.csv",
        )
        assert len(tx.assertions) == 1
        d = tx.assertions[0].data
        assert d["sail_number"] == "GBR5176L"
        assert d["issue_date"] == "2009-05-01"
        assert d["cert_year"] == 2009
        assert d["lh"] == "10.04"
        assert d["non_spi_tcc"] == "0.991"
        assert d["endorsed"] == "E"
        # 2009 file has no SSS/STIX/AVS columns → missing → None.
        assert d["ssb_base_value"] is None
        assert d["stix"] is None
        assert d["avs"] is None


# ---------------------------------------------------------------------------
# Golden: expected rejects (machine-readable reasons)
# ---------------------------------------------------------------------------


class TestGoldenRejects:
    def test_unparseable_tcc_rejected(self, tx_2026):
        bad = [
            r for r in tx_2026.rejects
            if r.raw_fields.get("sail_number") == "GBR9999"
        ]
        assert len(bad) == 1
        reasons = bad[0].reject_reasons
        assert any("not_parseable" in r or "tcc" in r for r in reasons)
        assert any("missing_required_field:tcc" in r for r in reasons)

    def test_blank_sail_number_rejected(self, tx_2026):
        blank = [
            r for r in tx_2026.rejects
            if r.raw_fields.get("cert_number") == "IRC55555"
        ]
        assert len(blank) == 1
        assert any(
            "missing_required_field:sail_number" in r
            for r in blank[0].reject_reasons
        )

    def test_rejects_carry_identity_and_versions(self, tx_2026):
        for r in tx_2026.rejects:
            assert r.transformer_name == IRCTCCListingTransformer.transformer_name
            assert r.transformer_version == MAPPING_VERSION
            assert r.schema_version == "v1"
            assert r.reject_id.startswith("rej_")

    def test_2009_unparseable_tcc_rejected(self, golden_2009):
        tx = _transform(golden_2009)
        assert len(tx.rejects) == 1
        assert tx.rejects[0].raw_fields.get("sail_number") == "GBR0001"


# ---------------------------------------------------------------------------
# Acceptance: every required canonical field has mapping or explicit
# unsupported reason
# ---------------------------------------------------------------------------


class TestMappingCoverage:
    def test_required_fields_have_mappings(self):
        mapped = {m.canonical_field: m for m in FIELD_MAPPINGS}
        for required in ("sail_number", "tcc"):
            assert required in mapped
            assert mapped[required].required is True
            assert mapped[required].source_fields

    def test_every_extracted_field_mapped_or_unsupported(self, golden_2026):
        batch = _parse(golden_2026)
        report = audit_mapping_coverage(batch)
        assert report.unmapped_source_fields == ()
        assert report.complete

    def test_every_canonical_field_has_mapping_or_reason(self, golden_2026):
        batch = _parse(golden_2026)
        report = audit_mapping_coverage(batch)
        assert report.unmapped_canonical_fields == ()

    def test_unsupported_fields_have_reasons(self):
        for u in UNSUPPORTED_SOURCE_FIELDS:
            assert u.reason.strip(), f"{u.source_field} needs a reason"

    def test_coverage_audit_detects_unknown_source_field(self, golden_2026):
        batch = _parse(golden_2026)
        # Inject a record with an unmapped, undeclared source field.
        from irc_data.parsers.extraction_contract import (
            ExtractedField,
            ExtractedRecord,
        )

        rogue = ExtractedRecord(
            record_type="tcc_listing_row",
            record_index=99,
            fields=[
                ExtractedField(
                    name="mystery_column",
                    value="x",
                    locator=Locator.whole_artifact(
                        batch.artifact_id, batch.content_hash
                    ),
                )
            ],
        )
        batch.records.append(rogue)
        report = audit_mapping_coverage(batch)
        assert "mystery_column" in report.unmapped_source_fields
        assert not report.complete

    def test_mapping_table_is_auditable(self):
        table = field_mapping_table()
        assert any(r.get("canonical_field") == "tcc" for r in table)
        assert any(r.get("source_field") == "secondary" for r in table)


# ---------------------------------------------------------------------------
# Acceptance: lineage query reaches raw artifact
# ---------------------------------------------------------------------------


class TestLineage:
    def test_lineage_reaches_raw_artifact(self, tx_2026, golden_2026):
        for assertion in tx_2026.assertions:
            assert verify_lineage(assertion, artifact_content=golden_2026)

    def test_lineage_index_query(self, tx_2026, golden_2026):
        idx = index_batch(tx_2026)
        assert idx.all_reach_raw_artifact(artifact_content=golden_2026)

    def test_lineage_resolves_tcc_span(self, tx_2026, golden_2026):
        idx = LineageIndex(tx_2026)
        sun = next(
            a for a in tx_2026.assertions
            if a.data["sail_number"] == "GBR8310" and not a.data["is_secondary"]
        )
        report = idx.trace(sun.assertion_id, artifact_content=golden_2026)
        assert report is not None
        assert report.reaches_raw_artifact
        assert report.content_hash_verified is True
        # Chain: assertion → extraction batch → artifact.
        assert [hop["hop"] for hop in report.chain] == [
            "assertion", "extraction_batch", "artifact",
        ]
        assert report.chain[-1]["source_slug"] == SOURCE_SLUG
        # At least one span resolves to the raw "1.015" TCC cell.
        resolved = [s.resolved_text for s in report.spans.values()]
        assert any((t or "").strip() == "1.015" for t in resolved)

    def test_lineage_detects_artifact_tampering(self, tx_2026):
        assertion = tx_2026.assertions[0]
        tampered = b"Boat Name,Sail No\ntampered,x\n"
        assert not verify_lineage(assertion, artifact_content=tampered)
        report = trace_assertion(assertion, artifact_content=tampered)
        assert report.content_hash_verified is False
        assert not report.reaches_raw_artifact

    def test_lineage_without_content_still_cites_artifact(self, tx_2026):
        report = trace_assertion(tx_2026.assertions[0])
        assert report.content_hash_verified is None
        assert report.artifact_id.startswith("art_")
        assert report.content_hash

    def test_unknown_assertion_id_traces_to_none(self, tx_2026):
        idx = LineageIndex(tx_2026)
        assert idx.trace("asrt_doesnotexist") is None


# ---------------------------------------------------------------------------
# Determinism (replay)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_replay_is_deterministic(self, golden_2026):
        tx1 = _transform(golden_2026)
        tx2 = _transform(golden_2026)
        assert tx1.transformation_id == tx2.transformation_id
        assert tx1.transformation_hash == tx2.transformation_hash
        assert [a.assertion_id for a in tx1.assertions] == [
            a.assertion_id for a in tx2.assertions
        ]
        assert [a.assertion_hash for a in tx1.assertions] == [
            a.assertion_hash for a in tx2.assertions
        ]

    def test_round_trip(self, tx_2026):
        from irc_data.transform.transformation_contract import (
            TransformationBatchV1 as TB,
        )

        restored = TB.from_dict(tx_2026.to_dict())
        assert restored.transformation_hash == tx_2026.transformation_hash
        assert len(restored.assertions) == len(tx_2026.assertions)
