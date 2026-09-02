"""Transformation package — schema-versioned transformation pipeline (DP-03-04).

Converts extracted records (DP-02-03 :class:`ExtractionBatchV1`) into
canonical assertions reproducibly:

* Pure transformation stages validate input/output schemas.
* Every published assertion identifies its transformer and schema
  version, and carries full lineage back to the raw artifact.
* Invalid records are emitted separately in the reject stream and never
  partially publish.
* Reruns are deterministic — identical inputs yield identical
  ``transformation_hash`` and assertion IDs.

Public API::

    from irc_data.transform import (
        # Contract (DP-03-04)
        ASSERTION_CONTRACT_VERSION,
        ASSERTION_SCHEMA_VERSION,
        TransformationError,
        InputSchemaValidationError,
        UnknownAssertionSchemaError,
        RecordTransformError,
        RejectStage,
        AssertionLineage,
        CanonicalAssertionV1,
        RejectedRecordV1,
        TransformationBatchV1,
        Transformer,
        BaseTransformer,
        # Canonical schemas
        RaceResultAssertionV1,
        CertificateAssertionV1,
        TCCListingAssertionV1,
        ASSERTION_SCHEMAS,
        register_assertion_schema,
        get_assertion_schema,
        has_assertion_schema,
        validate_assertion,
        # Reference transformers
        RaceResultTransformer,
        CertificateTransformer,
        TCCListingTransformer,
        TRANSFORMER_REGISTRY,
        RECORD_TYPE_TRANSFORMERS,
        get_transformer,
        get_transformer_for_record_type,
        transform_batch,
        # Contract suite (verification)
        TransformationContractSuite,
        run_sample_pipeline_contract,
    )
"""

from irc_data.transform.transformation_contract import (
    ASSERTION_CONTRACT_VERSION,
    ASSERTION_SCHEMA_VERSION,
    AssertionLineage,
    BaseTransformer,
    CanonicalAssertionV1,
    InputSchemaValidationError,
    RecordTransformError,
    RejectedRecordV1,
    RejectStage,
    TransformationBatchV1,
    TransformationError,
    Transformer,
    UnknownAssertionSchemaError,
)
from irc_data.transform.schemas import (
    ASSERTION_SCHEMAS,
    CertificateAssertionV1,
    RaceResultAssertionV1,
    TCCListingAssertionV1,
    get_assertion_schema,
    has_assertion_schema,
    register_assertion_schema,
    validate_assertion,
)
from irc_data.transform.reference_transformers import (
    RECORD_TYPE_TRANSFORMERS,
    TRANSFORMER_REGISTRY,
    CertificateTransformer,
    RaceResultTransformer,
    TCCListingTransformer,
    get_transformer,
    get_transformer_for_record_type,
    transform_batch,
)
from irc_data.transform.contract_suite import (
    TransformationContractSuite,
    run_sample_pipeline_contract,
)

__all__ = [
    # Contract
    "ASSERTION_CONTRACT_VERSION",
    "ASSERTION_SCHEMA_VERSION",
    "TransformationError",
    "InputSchemaValidationError",
    "UnknownAssertionSchemaError",
    "RecordTransformError",
    "RejectStage",
    "AssertionLineage",
    "CanonicalAssertionV1",
    "RejectedRecordV1",
    "TransformationBatchV1",
    "Transformer",
    "BaseTransformer",
    # Canonical schemas
    "RaceResultAssertionV1",
    "CertificateAssertionV1",
    "TCCListingAssertionV1",
    "ASSERTION_SCHEMAS",
    "register_assertion_schema",
    "get_assertion_schema",
    "has_assertion_schema",
    "validate_assertion",
    # Reference transformers
    "RaceResultTransformer",
    "CertificateTransformer",
    "TCCListingTransformer",
    "TRANSFORMER_REGISTRY",
    "RECORD_TYPE_TRANSFORMERS",
    "get_transformer",
    "get_transformer_for_record_type",
    "transform_batch",
    # Contract suite
    "TransformationContractSuite",
    "run_sample_pipeline_contract",
]
