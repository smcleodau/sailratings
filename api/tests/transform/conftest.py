"""Shared fixtures for the DP-03-04 transformation pipeline tests.

These helpers build :class:`ExtractionBatchV1` inputs directly (no
parser involved) so the transformation stage can be exercised in
isolation, plus adapter factories for the end-to-end contract suite.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from irc_data.parsers.extraction_contract import (
    SCHEMA_VERSION,
    ExtractedField,
    ExtractedRecord,
    ExtractionBatchV1,
    Locator,
)
from irc_data.sources.fake_adapter import StubSourceAdapter
from irc_data.sources.gate import CollectionGate
from irc_data.sources.policy import ACTIVE_POLICY
from irc_data.sources.registry import get_in_memory_source
from irc_data.transform.contract_suite import (
    SAMPLE_MIXED_HTML,
    SAMPLE_RESULTS_HTML,
)


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def make_batch(
    records: list[ExtractedRecord],
    *,
    artifact_content: bytes = b"<html>fixture</html>",
    source_slug: str = "sailsys",
    url: str = "http://stub.test/results/1",
    parser_version: str = "1.0.0",
    schema_version: str = SCHEMA_VERSION,
) -> ExtractionBatchV1:
    """Build a well-formed :class:`ExtractionBatchV1` over *records*."""
    content_hash = _sha256_hex(artifact_content)
    return ExtractionBatchV1(
        artifact_id=f"art_{content_hash[:16]}",
        content_hash=content_hash,
        parser_version=parser_version,
        schema_version=schema_version,
        source_slug=source_slug,
        url=url,
        records=records,
    )


def make_field(
    batch_identity: tuple[str, str],
    name: str,
    value: Any,
    *,
    path: str | None = None,
) -> ExtractedField:
    """Build an :class:`ExtractedField` with a citing locator."""
    artifact_id, content_hash = batch_identity
    return ExtractedField(
        name=name,
        value=value,
        locator=Locator.json_path(
            artifact_id=artifact_id,
            content_hash=content_hash,
            path=path or f"results[0].{name}",
            snippet=str(value)[:80] if value is not None else None,
        ),
    )


def make_record(
    batch_identity: tuple[str, str],
    record_type: str,
    record_index: int,
    fields: dict[str, Any],
) -> ExtractedRecord:
    """Build an :class:`ExtractedRecord` from a name → value mapping."""
    return ExtractedRecord(
        record_type=record_type,
        record_index=record_index,
        fields=[
            make_field(
                batch_identity,
                name,
                value,
                path=f"records[{record_index}].{name}",
            )
            for name, value in fields.items()
        ],
    )


@pytest.fixture
def batch_identity() -> tuple[str, str]:
    """(artifact_id, content_hash) used by record fixtures."""
    content = b"<html>fixture</html>"
    return (f"art_{_sha256_hex(content)[:16]}", _sha256_hex(content))


@pytest.fixture
def race_result_batch(batch_identity) -> ExtractionBatchV1:
    """A batch with two well-formed ``race_result`` records."""
    records = [
        make_record(
            batch_identity,
            "race_result",
            0,
            {
                "place": 1,
                "sail_number": "GBR1234",
                "boat_name": "Sunshine",
                "tcc": "1.015",
                "corrected_time": "01:22:10",
            },
        ),
        make_record(
            batch_identity,
            "race_result",
            1,
            {
                "place": 2,
                "sail_number": "AUS5678",
                "boat_name": "Wild Oats",
                "tcc": "0.985",
                "corrected_time": "01:23:15",
            },
        ),
    ]
    return make_batch(records)


@pytest.fixture
def mixed_race_result_batch(batch_identity) -> ExtractionBatchV1:
    """A batch with one valid and one invalid (blank sail) record."""
    records = [
        make_record(
            batch_identity,
            "race_result",
            0,
            {
                "place": 1,
                "sail_number": "GBR1234",
                "boat_name": "Sunshine",
                "tcc": "1.015",
            },
        ),
        make_record(
            batch_identity,
            "race_result",
            1,
            {
                "place": 2,
                "sail_number": "   ",
                "boat_name": "Nameless",
                "tcc": "0.985",
            },
        ),
    ]
    return make_batch(records)


@pytest.fixture
def stub_adapter_factory():
    """Factory producing fresh stub adapters serving the sample page."""

    def factory() -> StubSourceAdapter:
        source = get_in_memory_source("sailsys")
        gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
        return StubSourceAdapter(
            pages={"/results/1": SAMPLE_RESULTS_HTML},
            gate=gate,
        )

    return factory


@pytest.fixture
def json_stub_adapter_factory():
    """Factory producing stub adapters serving a JSON results payload."""

    payload = json.dumps({
        "event_name": "Contract Cup",
        "results": [
            {"place": 1, "sail_number": "GBR1234", "boat_name": "Sunshine",
             "tcc": "1.015"},
            {"place": 2, "sail_number": "AUS5678", "boat_name": "Wild Oats",
             "tcc": "0.985"},
        ],
    }).encode("utf-8")

    class JsonStubAdapter(StubSourceAdapter):
        source_slug = "sailsys"

        def parse_hint_for(self, url: str):
            from irc_data.sources.adapter import ParseHint

            return ParseHint.JSON

        async def fetch(self, url: str):
            envelope = await super().fetch(url)
            if envelope is not None:
                envelope.parse_hint = "json"
                envelope.content_type = "application/json"
            return envelope

    def factory() -> StubSourceAdapter:
        source = get_in_memory_source("sailsys")
        gate = CollectionGate(policy=ACTIVE_POLICY, sources=[source])
        return JsonStubAdapter(pages={"/results/1": payload}, gate=gate)

    return factory


__all__ = [
    "SAMPLE_MIXED_HTML",
    "SAMPLE_RESULTS_HTML",
    "make_batch",
    "make_field",
    "make_record",
]
