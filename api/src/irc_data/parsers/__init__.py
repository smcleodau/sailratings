"""Parser package — extraction contracts and reference parsers (DP-02-03)."""

from irc_data.parsers.extraction_contract import (
    BaseParser,
    ExtractedField,
    ExtractedRecord,
    ExtractionBatchV1,
    Locator,
    LocatorType,
    Parser,
    ParserInputV1,
    SCHEMA_VERSION,
)

__all__ = [
    "SCHEMA_VERSION",
    "LocatorType",
    "Locator",
    "ExtractedField",
    "ExtractedRecord",
    "ParserInputV1",
    "ExtractionBatchV1",
    "Parser",
    "BaseParser",
]
