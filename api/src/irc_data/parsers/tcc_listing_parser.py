"""IRC TCC listing CSV parser with source-span locators (DP-06-03).

This is the span-preserving sibling of :mod:`irc_data.parsers.tcc_csv`
(the legacy row importer).  It parses the ``irc-tcc`` source artifact —
the ``ClubListing_YYYYMMDD.csv`` published on
https://ircrating.org/irc-racing/online-tcc-listings/ — into a DP-02-03
:class:`ExtractionBatchV1` where **every extracted field carries a
``CSV_ROW`` locator** citing the artifact id, content hash, CSV row and
column index.

The parser is a pure function of the artifact bytes (DP-02-03): no
network, no filesystem, no clock dependence in the output identity.

Handled source variants (as in the legacy importer):

* **2026 format** — utf-8-sig encoded, headers ``Boat Name, Sail No,
  Cert No, Issue Date, Cert Year, TCC, Endorsed, Secondary, …``.
* **2009 Wayback format** — latin-1 encoded, headers ``BoatName,
  SailNo, CertNo, Valid Date, SYSCertYear, TCC, E, Short Handed, LOA,
  TCC Non spi, …`` (aliases collapsed to the same extracted field names
  via :data:`irc_data.parsers.tcc_csv.COLUMN_MAP`).

Secondary-cert semantics are identical to the legacy importer: rows
whose boat name ends in ``" - SEC"`` or ``" (SH)"`` (or with a non-empty
``Secondary`` column) are flagged ``is_secondary=True`` and the suffix
is stripped from ``boat_name`` so the canonical name is consistent.

Rows whose raw ``TCC`` cell is empty or unparseable are still emitted
(without a ``tcc`` field) so that the *transformation* stage
(:class:`~irc_data.transform.irc_tcc_mapping.IRCTCCListingTransformer`)
owns the reject decision with full machine-readable reasons — the parser
never silently drops a source row.
"""

from __future__ import annotations

import csv
from typing import Any

from irc_data.parsers.extraction_contract import (
    BaseParser,
    ExtractedField,
    ExtractedRecord,
    ExtractionBatchV1,
    Locator,
    LocatorType,
    ParserInputV1,
)
from irc_data.parsers.tcc_csv import COLUMN_MAP, _detect_secondary


class IRCTCCListingParser(BaseParser):
    """Parse the IRC TCC listing CSV into span-citing extracted records.

    Emits one ``tcc_listing_row`` :class:`ExtractedRecord` per CSV data
    row.  Every field's locator is a ``CSV_ROW`` span: ``row`` is the
    0-based CSV *data* row index (header is line 1, so CSV data row *i*
    sits on artifact line ``i + 2``) and ``start`` is the 0-based column
    index of the source column the value was read from.
    """

    parser_name = "IRCTCCListingParser"
    parser_version = "1.0.0"
    schema_version = "v1"

    RECORD_TYPE = "tcc_listing_row"

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        text = self._decode(input.content)
        reader = csv.reader(text.splitlines())
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if not rows:
            return self.finalize(input, [])

        headers = [h.strip() for h in rows[0]]
        # Column index → extracted field name (via the shared COLUMN_MAP).
        col_field: dict[int, str] = {
            idx: COLUMN_MAP[h] for idx, h in enumerate(headers) if h in COLUMN_MAP
        }

        records: list[ExtractedRecord] = []
        for data_idx, cells in enumerate(rows[1:]):
            # Raw values keyed by extracted field name; alias columns do
            # not overwrite a non-empty value (same rule as legacy).
            mapped: dict[str, str] = {}
            for col_idx, value in enumerate(cells):
                field_name = col_field.get(col_idx)
                if field_name is None:
                    continue
                value = value.strip()
                if value or field_name not in mapped:
                    mapped[field_name] = value

            # Secondary-cert semantics (shared with the legacy importer).
            cleaned_name, is_secondary = _detect_secondary(
                mapped.get("boat_name", ""), mapped.get("secondary")
            )
            if cleaned_name != (mapped.get("boat_name") or "").strip():
                mapped["boat_name"] = cleaned_name

            fields: list[ExtractedField] = []
            for field_name, value in mapped.items():
                col_idx = self._column_for(field_name, cells, headers, col_field)
                fields.append(
                    ExtractedField(
                        name=field_name,
                        value=value if value != "" else None,
                        locator=Locator(
                            artifact_id=input.artifact_id,
                            content_hash=input.content_hash,
                            locator_type=LocatorType.CSV_ROW.value,
                            row=data_idx,
                            start=col_idx,
                            snippet=value[:80] if value else None,
                        ),
                    )
                )
            fields.append(
                ExtractedField(
                    name="is_secondary",
                    value=is_secondary,
                    locator=Locator(
                        artifact_id=input.artifact_id,
                        content_hash=input.content_hash,
                        locator_type=LocatorType.CSV_ROW.value,
                        row=data_idx,
                        start=None,
                        snippet=("SEC" if is_secondary else "primary"),
                    ),
                )
            )

            records.append(
                ExtractedRecord(
                    record_type=self.RECORD_TYPE,
                    record_index=data_idx,
                    fields=fields,
                )
            )

        return self.finalize(input, records)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _decode(content: bytes) -> str:
        """Decode CSV bytes (utf-8-sig first, latin-1 fallback)."""
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    @staticmethod
    def _column_for(
        field_name: str,
        cells: list[str],
        headers: list[str],
        col_field: dict[int, str],
    ) -> int | None:
        """First column index mapped to *field_name* with content.

        Falls back to the first mapped column (or ``None``) so the
        locator always cites the column the value conceptually came from.
        """
        first: int | None = None
        for idx in range(len(cells)):
            if col_field.get(idx) != field_name:
                continue
            if first is None:
                first = idx
            if idx < len(cells) and cells[idx].strip():
                return idx
        return first


__all__ = ["IRCTCCListingParser"]
