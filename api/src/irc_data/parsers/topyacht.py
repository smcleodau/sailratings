"""Certified TopYacht parser (DP-06-02).

Parses TopYacht static-HTML race-result pages into **extracted records**
on the DP-02-03 parser execution contract
(:class:`~irc_data.parsers.extraction_contract.BaseParser` /
:class:`ParserInputV1` / :class:`ExtractionBatchV1`).

The parser is a **pure function** of ``(artifact bytes, parser_version,
schema_version)``:

* No network, no filesystem, no clock.
* Every extracted field carries a :class:`Locator` (row index + snippet)
  that cites where in the artifact the value came from.
* Replaying the same artifact with the same versions yields the same
  deterministic ``extraction_hash`` and ``batch_id``.

It wraps the battle-tested :func:`~irc_data.scrapers.topyacht.parse_topyacht_html`
HTML table extraction and lifts each :class:`~irc_data.parsers.schemas.RaceResult`
into an :class:`ExtractedRecord` with per-field locators.

Only **IRC-scored** tables are emitted (PHS / ORC / AMS tables are
skipped by the underlying extractor).
"""

from __future__ import annotations

from typing import Any

from irc_data.parsers.extraction_contract import (
    BaseParser,
    ExtractedRecord,
    ExtractionBatchV1,
    LocatorType,
    ParserInputV1,
)

#: Parser version — bump on any behaviour change so replays are versioned.
PARSER_VERSION = "1.0.0"

#: The extraction schema version this parser emits.
SCHEMA_VERSION = "v1"


class TopYachtParser(BaseParser):
    """Parse TopYacht race-result HTML into extracted race-result records.

    Each IRC result row becomes one :class:`ExtractedRecord` with
    ``record_type="race_result"`` and per-field locators that cite the
    table row the value came from.
    """

    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        # Local import keeps the parser importable without the scraper's
        # optional deps at module import time.
        from irc_data.scrapers.topyacht import parse_topyacht_html

        html = input.content.decode("utf-8", errors="replace")
        race_results = parse_topyacht_html(html, source_url=input.url)

        records: list[ExtractedRecord] = []
        for idx, rr in enumerate(race_results):
            record = self._to_record(input, rr, idx)
            records.append(record)

        return self.finalize(input, records)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_record(
        self, input: ParserInputV1, rr: Any, index: int
    ) -> ExtractedRecord:
        """Lift a :class:`RaceResult` into an :class:`ExtractedRecord`.

        Locators use ``TABLE_ROW`` with the row index and a short snippet
        so every field cites its source span within the artifact.
        """
        raw = rr.raw_data or {}
        snippet = str(raw.get("boat_name") or "")[:64]

        fields = [
            self.make_field(
                input,
                "event_name",
                rr.event_name,
                locator_type=LocatorType.WHOLE_ARTIFACT.value,
            ),
            self.make_field(
                input,
                "boat_name",
                raw.get("boat_name"),
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
                snippet=snippet,
            ),
            self.make_field(
                input,
                "sail_number",
                raw.get("sail_number"),
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "skipper",
                raw.get("skipper"),
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "tcc_at_race",
                str(rr.tcc_at_race) if rr.tcc_at_race is not None else None,
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "place",
                rr.place,
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "division",
                rr.division,
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "elapsed_time",
                rr.elapsed_time,
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "corrected_time",
                rr.corrected_time,
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
            self.make_field(
                input,
                "event_date",
                str(rr.event_date) if rr.event_date else None,
                locator_type=LocatorType.WHOLE_ARTIFACT.value,
            ),
            self.make_field(
                input,
                "source_url",
                rr.source_url or input.url,
                locator_type=LocatorType.WHOLE_ARTIFACT.value,
            ),
            self.make_field(
                input,
                "status",
                raw.get("status"),
                locator_type=LocatorType.TABLE_CELL.value,
                row=index,
            ),
        ]

        # Drop fields with None values to keep the batch lean.
        fields = [f for f in fields if f.value is not None]

        return ExtractedRecord(
            record_type="race_result",
            record_index=index,
            fields=fields,
        )


__all__ = ["TopYachtParser", "PARSER_VERSION", "SCHEMA_VERSION"]
