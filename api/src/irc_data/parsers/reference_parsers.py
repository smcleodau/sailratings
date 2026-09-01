"""Reference parser implementations (DP-02-03).

Three reference parsers that demonstrate the :class:`Parser` execution
contract against HTML, JSON and PDF artifacts.  Each parser:

1. Accepts a :class:`ParserInputV1` (immutable raw reference).
2. Extracts structured records with source spans (:class:`Locator`).
3. Returns an :class:`ExtractionBatchV1` (handoff / output contract).

These are **reference implementations** — they prove the contract works
end-to-end and serve as templates for production parsers.
"""

from __future__ import annotations

import io
import json
import re
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


# ===========================================================================
# HTMLRaceResultParser — CSS-selector locators
# ===========================================================================


class HTMLRaceResultParser(BaseParser):
    """Parse an HTML race results table into extracted records.

    Extracts place, sail number, boat name, TCC/rating, elapsed time,
    and corrected time from a standard results table.  Each field
    is located via a CSS-selector-style path that identifies the
    table row and column.

    The parser is a pure function of the artifact content — it does
    not fetch from the web or read from the filesystem.
    """

    parser_version = "1.0.0"
    schema_version = "v1"

    #: Column header aliases.  Each canonical field name maps to a set
    #: of possible header labels found in HTML tables.
    COLUMN_ALIASES: dict[str, set[str]] = {
        "place": {"place", "pos", "position", "rank"},
        "sail_number": {"sail no", "sail", "sail number", "sailno"},
        "boat_name": {"boat name", "boat", "yacht", "name"},
        "tcc": {"ahc", "irc", "tcc", "rating", "irc rating", "irc tcc"},
        "elapsed_time": {"elapsed", "elapsed time"},
        "corrected_time": {"corrected", "corrected time"},
        "division": {"division", "div", "class"},
    }

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        """Parse the HTML artifact into an :class:`ExtractionBatchV1`."""
        from bs4 import BeautifulSoup

        html = input.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        records: list[ExtractedRecord] = []
        record_index = 0

        for table_idx, table in enumerate(soup.find_all("table")):
            headers = self._extract_headers(table)
            if not headers:
                continue

            col_map = self._map_columns(headers)
            if not col_map:
                continue

            # Require at least sail_number or boat_name
            if "sail_number" not in col_map and "boat_name" not in col_map:
                continue

            data_rows = table.find_all("tr")[1:]  # skip header
            for row_idx, row in enumerate(data_rows):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < len(headers):
                    continue

                fields: list[ExtractedField] = []
                for field_name, col_idx in col_map.items():
                    if col_idx >= len(cells):
                        continue
                    value = cells[col_idx]
                    if not value:
                        continue

                    # CSS-selector-style locator path
                    css_path = (
                        f"table:nth-of-type({table_idx + 1})"
                        f" tr:nth-of-type({row_idx + 2})"
                        f" td:nth-of-type({col_idx + 1})"
                    )
                    # Find byte offset of the cell text in the source
                    byte_offset = self._find_byte_offset(html, value)

                    locator = Locator(
                        artifact_id=input.artifact_id,
                        content_hash=input.content_hash,
                        locator_type=LocatorType.CSS_SELECTOR.value,
                        path=css_path,
                        start=byte_offset,
                        snippet=value[:80] if value else None,
                    )
                    fields.append(ExtractedField(
                        name=field_name,
                        value=self._convert_value(field_name, value),
                        locator=locator,
                    ))

                if fields:
                    records.append(ExtractedRecord(
                        record_type="race_result",
                        record_index=record_index,
                        fields=fields,
                    ))
                    record_index += 1

        return self.finalize(input, records)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_headers(self, table) -> list[str]:
        """Extract lowercase header labels from a table element."""
        headers: list[str] = []
        for th in table.find_all("th"):
            headers.append(th.get_text(strip=True).lower())
        if not headers:
            first_row = table.find("tr")
            if first_row:
                for td in first_row.find_all("td"):
                    headers.append(td.get_text(strip=True).lower())
        return headers

    def _map_columns(self, headers: list[str]) -> dict[str, int]:
        """Map canonical field names to column indices."""
        col_map: dict[str, int] = {}
        for i, h in enumerate(headers):
            for field_name, aliases in self.COLUMN_ALIASES.items():
                if h in aliases and field_name not in col_map:
                    col_map[field_name] = i
        return col_map

    def _convert_value(self, field_name: str, value: str) -> Any:
        """Convert a string cell value to the appropriate type."""
        if field_name in ("place",):
            try:
                return int(value)
            except ValueError:
                return value
        if field_name in ("tcc",):
            try:
                return float(value)
            except ValueError:
                return value
        return value

    def _find_byte_offset(self, html: str, text: str) -> int | None:
        """Find the byte offset of *text* in *html* (first occurrence)."""
        pos = html.find(text)
        if pos < 0:
            return None
        return len(html[:pos].encode("utf-8"))


# ===========================================================================
# JSONResultsParser — JSON-path locators
# ===========================================================================


class JSONResultsParser(BaseParser):
    """Parse a JSON race results payload into extracted records.

    Expects a JSON object with a ``results`` array.  Each element is
    extracted into an :class:`ExtractedRecord` with JSON-path locators
    (e.g. ``"results[0].place"``).

    The parser is a pure function of the artifact content.
    """

    parser_version = "1.0.0"
    schema_version = "v1"

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        """Parse the JSON artifact into an :class:`ExtractionBatchV1`."""
        text = input.content.decode("utf-8", errors="replace")
        data = json.loads(text)

        records: list[ExtractedRecord] = []

        # Handle both {"results": [...]} and top-level [...]
        if isinstance(data, dict):
            results = data.get("results", [])
            event_name = data.get("event_name") or data.get("name")
        elif isinstance(data, list):
            results = data
            event_name = None
        else:
            results = []
            event_name = None

        for idx, item in enumerate(results):
            if not isinstance(item, dict):
                continue

            fields: list[ExtractedField] = []

            for key, value in item.items():
                json_path = f"results[{idx}].{key}"
                locator = Locator.json_path(
                    artifact_id=input.artifact_id,
                    content_hash=input.content_hash,
                    path=json_path,
                    snippet=str(value)[:80] if value is not None else None,
                )
                fields.append(ExtractedField(
                    name=key,
                    value=value,
                    locator=locator,
                ))

            if fields:
                records.append(ExtractedRecord(
                    record_type="race_result",
                    record_index=idx,
                    fields=fields,
                ))

        return self.finalize(input, records)


# ===========================================================================
# PDFCertificateParser — PDF-page locators
# ===========================================================================


class PDFCertificateParser(BaseParser):
    """Parse an IRC certificate PDF into extracted records.

    Wraps the existing :func:`irc_data.parsers.certificate_pdf.parse_certificate_pdf`
    logic, but operates on raw bytes (via :class:`ParserInputV1`) rather
    than a filesystem path.  Each extracted field is located via a
    PDF-page locator.

    The parser is a pure function of the artifact content — it does
    not read from the filesystem.  The PDF bytes are loaded into an
    in-memory file handle.
    """

    parser_version = "1.0.0"
    schema_version = "v1"

    #: Maps CertificateData fields to the PDF page they appear on.
    #: Page 1 is the main boat data sheet.
    DEFAULT_PAGE = 1

    def parse(self, input: ParserInputV1) -> ExtractionBatchV1:
        """Parse the PDF artifact into an :class:`ExtractionBatchV1`."""
        # Parse the PDF from raw bytes (in-memory, no filesystem)
        pdf_file = io.BytesIO(input.content)

        # Extract structured data using the existing parser logic
        cert_data = self._extract_certificate_data(pdf_file)
        if cert_data is None:
            return self.finalize(input, [])

        # Build extracted fields with PDF-page locators
        fields: list[ExtractedField] = []

        # Header fields
        for field_name in ("cert_number", "issue_date", "source"):
            value = getattr(cert_data, field_name, None)
            if value is not None:
                fields.append(self._make_pdf_field(
                    input, field_name, value, self.DEFAULT_PAGE,
                ))

        # Measurement fields
        measurement_fields = [
            "lh", "lwp", "beam", "draft", "displacement", "bo", "so",
            "p", "e", "j", "stl", "spl", "fl",
            "muw", "mtw", "mhw",
            "hlu", "hlp", "hhw", "htw", "huw", "hsa",
            "spa", "sym_slu", "sym_sle", "sym_sf", "sym_shw",
            "asym_slu", "asym_sle", "asym_sf", "asym_shw",
            "water_ballast", "stix", "avs",
            "rig_type", "mast_material", "spreaders",
            "dlr", "x", "y", "internal_ballast",
            "headsails_max", "flying_headsails_max", "spinnakers_max",
            "fsa", "flu", "flp", "fuw", "ftw", "fhw", "fsfl", "fshw",
            "stl_fh_max", "aft_rigging",
        ]

        for field_name in measurement_fields:
            value = getattr(cert_data, field_name, None)
            if value is not None:
                fields.append(self._make_pdf_field(
                    input, field_name, value, self.DEFAULT_PAGE,
                ))

        records = [ExtractedRecord(
            record_type="certificate",
            record_index=0,
            fields=fields,
        )] if fields else []

        return self.finalize(input, records)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_certificate_data(self, pdf_file: io.BytesIO):
        """Extract :class:`CertificateData` from an in-memory PDF file."""
        try:
            import pdfplumber

            from irc_data.parsers.certificate_pdf import (
                _extract_lines,
                _search_value,
                _dec,
            )
            from irc_data.parsers.schemas import CertificateData
            import re
            from datetime import datetime
            from decimal import Decimal

            with pdfplumber.open(pdf_file) as pdf:
                all_lines: list[str] = []
                for page in pdf.pages:
                    all_lines.extend(_extract_lines(page))

            full_text = "\n".join(all_lines)

            hull_idx = full_text.find("HULL")
            measurement_text = full_text[hull_idx:] if hull_idx >= 0 else full_text

            # Header extraction
            name = _search_value(full_text, r"Name:\s*(.+?)(?:\n|$)")
            sail_number = _search_value(full_text, r"Sail Number:\s*(\S+)")
            design_raw = _search_value(full_text, r"Design:\s*(.+?)(?:\n|$)")
            cert_number = _search_value(full_text, r"Cert No\.?:\s*(\d+)")

            # Hull measurements
            lh = _dec(_search_value(measurement_text, r"LH\s+(\d+\.\d+)"))
            lwp = _dec(_search_value(measurement_text, r"LWP\s+(\d+\.\d+)"))
            p = _dec(_search_value(measurement_text, r"\bP\s+(\d+\.\d+)"))
            e = _dec(_search_value(measurement_text, r"\bE\s+(\d+\.\d+)"))
            j = _dec(_search_value(measurement_text, r"\bJ\s+(\d+\.\d+)"))
            stl = _dec(_search_value(measurement_text, r"STL\s+(\d+\.\d+)"))
            hlp = _dec(_search_value(measurement_text, r"HLP\s+(\d+\.\d+)"))

            displacement = _dec(
                _search_value(
                    measurement_text,
                    r"(?:Boat\s*Weight|Poids|Peso|Gewicht)\s*:\s*([\d,]+)(?:\s*kgs?)?",
                )
            )
            draft = _dec(
                _search_value(
                    measurement_text,
                    r"(?:Draft|Draught|Tirant\s*d['\u2019]eau|Calado|Pescaggio|Tiefgang)\s*:\s*(\d+(?:\.\d+)?)",
                )
            )

            dlr = _dec(_search_value(measurement_text, r"DLR\s+(\d+)"))

            return CertificateData(
                cert_number=cert_number,
                source="ircrating.org",
                lh=lh,
                lwp=lwp,
                draft=draft,
                displacement=displacement,
                bo=_dec(_search_value(measurement_text, r"BO\s+(\d+\.\d+)")),
                so=_dec(_search_value(measurement_text, r"SO\s+(\d+\.\d+)")),
                p=p,
                e=e,
                j=j,
                stl=stl,
                hlp=hlp,
                dlr=int(dlr) if dlr else None,
                raw_data={
                    "name": name,
                    "sail_number": sail_number,
                    "design": design_raw,
                },
            )
        except Exception:
            return None

    def _make_pdf_field(
        self,
        input: ParserInputV1,
        name: str,
        value: Any,
        page: int,
    ) -> ExtractedField:
        """Create an :class:`ExtractedField` with a PDF-page locator."""
        locator = Locator.pdf_page(
            artifact_id=input.artifact_id,
            content_hash=input.content_hash,
            page=page,
            snippet=str(value)[:80] if value is not None else None,
        )
        return ExtractedField(name=name, value=value, locator=locator)


# ===========================================================================
# Parser registry
# ===========================================================================

#: Registry mapping parse_hint → parser class.
PARSER_REGISTRY: dict[str, type[BaseParser]] = {
    "html": HTMLRaceResultParser,
    "json": JSONResultsParser,
    "pdf": PDFCertificateParser,
}


def get_parser_for_hint(parse_hint: str) -> BaseParser | None:
    """Return a parser instance for the given parse_hint, or ``None``."""
    cls = PARSER_REGISTRY.get(parse_hint)
    if cls is None:
        return None
    return cls()


def parse_artifact(input: ParserInputV1) -> ExtractionBatchV1:
    """Parse an artifact using the parser indicated by its ``parse_hint``.

    This is the main entry point for the parser execution contract.
    If no parser is registered for the ``parse_hint``, returns an empty
    :class:`ExtractionBatchV1`.
    """
    parser = get_parser_for_hint(input.parse_hint)
    if parser is None:
        return ExtractionBatchV1.from_parser_input(input, [])
    return parser.parse(input)


__all__ = [
    "HTMLRaceResultParser",
    "JSONResultsParser",
    "PDFCertificateParser",
    "PARSER_REGISTRY",
    "get_parser_for_hint",
    "parse_artifact",
]
