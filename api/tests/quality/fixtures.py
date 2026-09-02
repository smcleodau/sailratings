"""Fault fixtures for the DP-05-02 quality gates.

Every rule class in
:class:`~irc_data.quality.contracts.RuleClass` has at least one fixture
that reliably triggers it, plus one **clean** fixture per gate that
passes all rules.  The verification suite
(:mod:`tests.quality.test_quality_gates`) drives each fixture through
the gate and asserts the expected rule class fired and the batch was
isolated.

Payload builders return *already-constructed* contract objects.  Where
a fixture needs to be invalid in a way the contract's ``__post_init__``
would normally recompute (e.g. a wrong ``extraction_hash``), we build a
valid object first and then mutate the frozen/public field to the fault
value — the point of the gate is to catch exactly this kind of drift
between declared and recomputed identity.
"""

from __future__ import annotations

import hashlib
from typing import Any

from irc_data.parsers.extraction_contract import (
    ExtractedField,
    ExtractedRecord,
    ExtractionBatchV1,
    Locator,
    LocatorType,
)
from irc_data.quality.validators import IdentityEffect, IdentityEffectBatch
from irc_data.transform.transformation_contract import (
    AssertionLineage,
    CanonicalAssertionV1,
    RejectedRecordV1,
    TransformationBatchV1,
)


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

ARTIFACT_ID = "art_test_001"
CONTENT_HASH = hashlib.sha256(b"<html>results page</html>").hexdigest()
SOURCE_SLUG = "fault-fixtures"
URL = "https://example.test/results"


def _locator() -> Locator:
    return Locator(
        artifact_id=ARTIFACT_ID,
        content_hash=CONTENT_HASH,
        locator_type=LocatorType.TABLE_CELL.value,
        row=0,
        start=0,
        snippet="<td>1</td>",
    )


def _field(name: str, value: Any, locator: Locator | None = None) -> ExtractedField:
    return ExtractedField(name=name, value=value, locator=locator or _locator())


def _record(
    record_type: str,
    record_index: int,
    fields: list[ExtractedField],
) -> ExtractedRecord:
    return ExtractedRecord(
        record_type=record_type, record_index=record_index, fields=fields
    )


# ---------------------------------------------------------------------------
# Extraction payloads
# ---------------------------------------------------------------------------


def clean_extraction_batch() -> ExtractionBatchV1:
    """A clean extraction batch — passes every extraction rule."""
    records = [
        _record(
            "race_result",
            i,
            [
                _field("sail_number", f"GBR{1000 + i}"),
                _field("boat_name", f"Boat {i}"),
                _field("place", i + 1),
                _field("tcc", "1.015"),
            ],
        )
        for i in range(3)
    ]
    return ExtractionBatchV1(
        artifact_id=ARTIFACT_ID,
        content_hash=CONTENT_HASH,
        parser_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        url=URL,
        records=records,
    )


def extraction_empty_records() -> ExtractionBatchV1:
    """Fault: zero records → completeness."""
    return ExtractionBatchV1(
        artifact_id=ARTIFACT_ID,
        content_hash=CONTENT_HASH,
        parser_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        url=URL,
        records=[],
    )


def extraction_broken_provenance() -> ExtractionBatchV1:
    """Fault: a field whose locator cites a different artifact → provenance."""
    bad_locator = Locator(
        artifact_id="art_OTHER",  # mismatched artifact_id
        content_hash=CONTENT_HASH,
        locator_type=LocatorType.WHOLE_ARTIFACT.value,
    )
    records = [
        _record(
            "race_result", 0,
            [_field("sail_number", "GBR1000"), _field("place", 1, locator=bad_locator)],
        ),
    ]
    return ExtractionBatchV1(
        artifact_id=ARTIFACT_ID,
        content_hash=CONTENT_HASH,
        parser_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        url=URL,
        records=records,
    )


def extraction_broken_determinism() -> ExtractionBatchV1:
    """Fault: extraction_hash does not recompute from records → determinism."""
    batch = clean_extraction_batch()
    # Mutate after construction so __post_init__ can't fix it.
    batch.extraction_hash = "0" * 64
    return batch


def extraction_bad_indices() -> ExtractionBatchV1:
    """Fault: duplicate record_index values → value_domain."""
    records = [
        _record("race_result", 0, [_field("sail_number", "GBR1000")]),
        _record("race_result", 0, [_field("sail_number", "GBR1001")]),  # dup index
    ]
    return ExtractionBatchV1(
        artifact_id=ARTIFACT_ID,
        content_hash=CONTENT_HASH,
        parser_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        url=URL,
        records=records,
    )


def extraction_bad_schema_envelope() -> ExtractionBatchV1:
    """Fault: wrong schema_version on the envelope → schema."""
    batch = clean_extraction_batch()
    batch.schema_version = "v99"
    # Recompute determinism anchors so ONLY the schema rule fires.
    batch.batch_id = batch._derive_batch_id()
    return batch


# ---------------------------------------------------------------------------
# Canonical payloads
# ---------------------------------------------------------------------------


def _lineage(batch: ExtractionBatchV1, record_type: str, record_index: int) -> AssertionLineage:
    return AssertionLineage(
        artifact_id=batch.artifact_id,
        content_hash=batch.content_hash,
        source_slug=batch.source_slug,
        extraction_batch_id=batch.batch_id,
        extraction_hash=batch.extraction_hash,
        parser_version=batch.parser_version,
        extraction_schema_version=batch.schema_version,
        source_record_type=record_type,
        source_record_index=record_index,
        url=batch.url,
        source_locators=[],
    )


def _assertion(
    batch: ExtractionBatchV1,
    record_type: str,
    record_index: int,
    data: dict[str, Any],
    assertion_type: str = "race_result",
) -> CanonicalAssertionV1:
    return CanonicalAssertionV1(
        assertion_type=assertion_type,
        assertion_id=CanonicalAssertionV1.derive_assertion_id(
            extraction_batch_id=batch.batch_id,
            record_type=record_type,
            record_index=record_index,
            transformer_version="1.0.0",
            schema_version="v1",
        ),
        transformer_name="RaceResultTransformer",
        transformer_version="1.0.0",
        schema_version="v1",
        data=data,
        lineage=_lineage(batch, record_type, record_index),
    )


def clean_canonical_batch() -> TransformationBatchV1:
    """A clean canonical batch — passes every canonical rule.

    Two assertions (records 0 and 1) and one reject (record 2) — a
    disjoint partition of the three-record source extraction batch.
    """
    ext = clean_extraction_batch()
    assertions = [
        _assertion(ext, "race_result", 0, {
            "sail_number": "GBR1000", "boat_name": "Boat 0",
            "place": 1, "tcc": "1.015",
        }),
        _assertion(ext, "race_result", 1, {
            "sail_number": "GBR1001", "boat_name": "Boat 1",
            "place": 2, "tcc": "0.985",
        }),
    ]
    rejects = [
        RejectedRecordV1.create(
            batch=ext,
            record=_record("race_result", 2, [_field("sail_number", "")]),
            stage="output_schema_validation",
            reasons=["sail_number: must be non-empty"],
            transformer_name="RaceResultTransformer",
            transformer_version="1.0.0",
            schema_version="v1",
        )
    ]
    return TransformationBatchV1(
        extraction_batch_id=ext.batch_id,
        extraction_hash=ext.extraction_hash,
        parser_version=ext.parser_version,
        extraction_schema_version=ext.schema_version,
        transformer_name="RaceResultTransformer",
        transformer_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        artifact_id=ext.artifact_id,
        content_hash=ext.content_hash,
        url=ext.url,
        assertions=assertions,
        rejects=rejects,
    )


def canonical_broken_determinism() -> TransformationBatchV1:
    """Fault: transformation_hash does not recompute → determinism."""
    batch = clean_canonical_batch()
    batch.transformation_hash = "f" * 64
    return batch


def canonical_partition_overlap() -> TransformationBatchV1:
    """Fault: the same source record appears as both assertion and reject
    → completeness (partition)."""
    batch = clean_canonical_batch()
    # Add a reject for record 0, which already has an assertion.
    ext = clean_extraction_batch()
    batch.rejects.append(
        RejectedRecordV1.create(
            batch=ext,
            record=_record("race_result", 0, [_field("sail_number", "GBR1000")]),
            stage="transform",
            reasons=["injected fault: overlapping partition"],
            transformer_name="RaceResultTransformer",
            transformer_version="1.0.0",
            schema_version="v1",
        )
    )
    # Recompute determinism anchors so ONLY the partition rule fires.
    batch.transformation_hash = batch._derive_transformation_hash()
    return batch


def canonical_broken_lineage() -> TransformationBatchV1:
    """Fault: an assertion with empty lineage anchors → provenance."""
    batch = clean_canonical_batch()
    bad = batch.assertions[0]
    bad.lineage.extraction_batch_id = ""
    bad.lineage.extraction_hash = ""
    # Recompute the batch hash so only the lineage rule fires.
    batch.transformation_hash = batch._derive_transformation_hash()
    return batch


def canonical_output_schema_violation() -> TransformationBatchV1:
    """Fault: assertion payload fails the registered output schema
    (tcc out of range) → value_domain."""
    ext = clean_extraction_batch()
    assertion = _assertion(ext, "race_result", 0, {
        "sail_number": "GBR1000", "boat_name": "Boat 0",
        "place": 1,
        "tcc": "9.99",  # out of domain (schema requires tcc <= 3.0)
    })
    batch = TransformationBatchV1(
        extraction_batch_id=ext.batch_id,
        extraction_hash=ext.extraction_hash,
        parser_version=ext.parser_version,
        extraction_schema_version=ext.schema_version,
        transformer_name="RaceResultTransformer",
        transformer_version="1.0.0",
        schema_version="v1",
        source_slug=SOURCE_SLUG,
        artifact_id=ext.artifact_id,
        content_hash=ext.content_hash,
        url=ext.url,
        assertions=[assertion],
        rejects=[],
    )
    return batch


def canonical_bad_envelope() -> TransformationBatchV1:
    """Fault: transformation envelope missing identity → schema."""
    batch = clean_canonical_batch()
    # Corrupt an envelope identity field that participates in the
    # transformation_id derivation (transformer_name), then recompute
    # BOTH determinism anchors so ONLY the schema rule fires — the ids
    # are self-consistent but the envelope is missing a required field.
    batch.transformer_name = ""
    batch.transformation_id = batch._derive_transformation_id()
    batch.transformation_hash = batch._derive_transformation_hash()
    return batch


# ---------------------------------------------------------------------------
# Identity payloads
# ---------------------------------------------------------------------------


def clean_identity_batch() -> IdentityEffectBatch:
    """A clean identity-effect batch — passes every identity rule."""
    return IdentityEffectBatch(
        source_slug=SOURCE_SLUG,
        effects=[
            IdentityEffect(
                effect_type="merge",
                entity_type="boat",
                entity_key="GBR1000",
                target_keys=["GBR 1000", "gbr-1000"],
                supersession_id="ent_gbr1000",
                reason="sail-number normalisation merge",
            ),
            IdentityEffect(
                effect_type="new_entity",
                entity_type="boat",
                entity_key="AUS5678",
                reason="first sighting",
            ),
        ],
    )


def identity_self_merge() -> IdentityEffectBatch:
    """Fault: an entity merged with itself → identity_effect.self_merge."""
    return IdentityEffectBatch(
        source_slug=SOURCE_SLUG,
        effects=[
            IdentityEffect(
                effect_type="merge",
                entity_type="boat",
                entity_key="GBR1000",
                target_keys=["GBR1000"],  # self-merge
                reason="faulty resolver output",
            ),
        ],
    )


def identity_malformed() -> IdentityEffectBatch:
    """Fault: unknown effect type / merge without targets → shape."""
    return IdentityEffectBatch(
        source_slug=SOURCE_SLUG,
        effects=[
            IdentityEffect(
                effect_type="teleport",  # unknown
                entity_type="boat",
                entity_key="GBR1000",
            ),
            IdentityEffect(
                effect_type="merge",
                entity_type="boat",
                entity_key="GBR2000",
                target_keys=[],  # merge with no targets
            ),
        ],
    )


def identity_duplicates() -> IdentityEffectBatch:
    """Fault: the same effect twice in one batch → identity_effect.duplicates."""
    eff = IdentityEffect(
        effect_type="new_entity",
        entity_type="boat",
        entity_key="GBR3000",
        reason="first sighting",
    )
    return IdentityEffectBatch(
        source_slug=SOURCE_SLUG,
        effects=[eff, IdentityEffect.from_dict(eff.to_dict())],
    )


def identity_churn(threshold: int = 3) -> IdentityEffectBatch:
    """Fault: more effects than the configured churn threshold → value_domain."""
    return IdentityEffectBatch(
        source_slug=SOURCE_SLUG,
        effects=[
            IdentityEffect(
                effect_type="new_entity",
                entity_type="boat",
                entity_key=f"GBR{4000 + i}",
                reason="bulk import",
            )
            for i in range(threshold + 2)
        ],
    )
