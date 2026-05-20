"""TCC listing CSV parser — transforms CSV rows into structured data."""

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from irc_data.config import (
    COUNTRY_PREFIXES,
    DESIGN_MATCH_TOLERANCE,
    SUNFAST_3300_BEAM,
    SUNFAST_3300_LH,
)
from irc_data.parsers.schemas import TCCListingRow

# Column name mapping: CSV header → our field name
# Handles both 2026 format and 2009 Wayback format
COLUMN_MAP = {
    "Boat Name": "boat_name",
    "Sail No": "sail_number",
    "Cert No": "cert_number",
    "Issue Date": "issue_date",
    "Valid Date": "issue_date",       # 2009 format
    "Cert Year": "cert_year",
    "SYSCertYear": "cert_year",       # 2009 format
    "TCC": "tcc",
    "Endorsed": "endorsed",
    "E": "endorsed",                  # 2009 format
    "Secondary": "secondary",
    "Short Handed": "secondary",      # 2009 format (closest equivalent)
    "Non Spi TCC": "non_spi_tcc",
    "TCC Non spi": "non_spi_tcc",     # 2009 format (note trailing space in CSV)
    "TCC Non spi ": "non_spi_tcc",    # 2009 format with trailing space
    "Crew": "crew",
    "DLR": "dlr",
    "LH": "lh",
    "LOA": "lh",                      # 2009 format uses LOA instead of LH
    "Beam": "beam",
    "Draft": "draft",
    "Single Furling Headsail": "single_furling_headsail",
    "Headsails": "headsails",
    "Flying Headsails": "flying_headsails",
    "Spinnakers": "spinnakers",
    "Series Date": "series_date",
    "Age Date": "age_date",
    "Racing Area": "racing_area",
    "Racing area": "racing_area",     # 2009 format (lowercase 'a')
    "SSS Base Value": "ssb_base_value",
    "SSS Base Value ": "ssb_base_value",  # possible trailing space
    "STIX": "stix",
    "AVS": "avs",
    "Category": "category",
    "ValidCode": "valid_code",
}


def detect_country(sail_number: str) -> str | None:
    """Detect country from sail number prefix.

    Sail numbers like 'AUS3300' → 'AUS', 'GBR5176L' → 'GBR',
    'D3300' → 'GER', '3322' → unknown (pure numeric).
    """
    # Try longest prefixes first to avoid 'D' matching before 'DEN'
    for prefix in sorted(COUNTRY_PREFIXES.keys(), key=len, reverse=True):
        if sail_number.upper().startswith(prefix) and (
            len(sail_number) > len(prefix)
            and not sail_number[len(prefix)].isalpha()
            or prefix == sail_number.upper()
        ):
            return COUNTRY_PREFIXES[prefix]

    # Check for pure numeric — could be Australian
    if sail_number.isdigit():
        return None  # Can't determine from number alone

    return None


def detect_design(lh: Decimal | None, beam: Decimal | None) -> str | None:
    """Detect boat design from hull dimensions.

    Currently only detects Sunfast 3300.
    SF3300 measured LH varies by rating office: 9.95-10.04m, Beam: 3.35-3.41m.
    """
    if lh is None or beam is None:
        return None
    lh_f = float(lh)
    beam_f = float(beam)
    if 9.93 <= lh_f <= 10.06 and 3.33 <= beam_f <= 3.43:
        return "Sunfast 3300"
    return None


def _safe_decimal(val: str) -> Decimal | None:
    if not val or not val.strip():
        return None
    try:
        return Decimal(val.strip())
    except InvalidOperation:
        return None


def _safe_int(val: str) -> int | None:
    if not val or not val.strip():
        return None
    try:
        # Handle float-format integers (e.g. "196.46" DLR in 2009 format)
        return int(float(val.strip()))
    except (ValueError, OverflowError):
        return None


def load_all_known_certs() -> list[dict]:
    """Load all known cert/name/sail combinations from all CSVs in tcc_listings/.

    Merges across all snapshot dates and deduplicates by cert_number.
    Returns list of dicts: {cert_number, boat_name, sail_number, source_date, source_file}.
    """
    from irc_data.config import TCC_LISTINGS_DIR

    all_certs: dict[str, dict] = {}  # cert_number → info

    csv_files = sorted(TCC_LISTINGS_DIR.glob("*.csv"))
    if not csv_files:
        return []

    for csv_path in csv_files:
        # Extract date from filename: tcc_listing_YYYY-MM-DD.csv
        match = re.search(r"(\d{4}-\d{2}-\d{2})", csv_path.name)
        source_date = match.group(1) if match else csv_path.stem

        try:
            rows = parse_tcc_csv(csv_path)
        except Exception as e:
            print(f"  Warning: failed to parse {csv_path.name}: {e}")
            continue

        for row in rows:
            cert_no = row.cert_number.strip()
            if not cert_no:
                continue
            # Keep earliest source date for each cert
            if cert_no not in all_certs:
                all_certs[cert_no] = {
                    "cert_number": cert_no,
                    "boat_name": row.boat_name,
                    "sail_number": row.sail_number,
                    "source_date": source_date,
                    "source_file": csv_path.name,
                }

    return list(all_certs.values())


_SEC_SUFFIX_RE = re.compile(r"\s*-\s*SEC\s*$", re.IGNORECASE)


def _detect_secondary(boat_name: str, secondary_col: str | None) -> tuple[str, bool]:
    """Return (cleaned_boat_name, is_secondary).

    A row is "secondary" when:
      - boat_name ends with " - SEC" (the legacy IRC convention), OR
      - the CSV "Secondary" / "Short Handed" column is non-empty.

    The cleaned boat_name has any " - SEC" suffix stripped, so the boat's
    canonical name is consistent across primary and secondary certs.
    """
    name = boat_name or ""
    suffix_match = _SEC_SUFFIX_RE.search(name)
    if suffix_match:
        name = _SEC_SUFFIX_RE.sub("", name).strip()
    flag_set = bool(secondary_col and secondary_col.strip())
    return name, bool(suffix_match) or flag_set


def parse_tcc_csv(path: Path) -> list[TCCListingRow]:
    """Parse a TCC listing CSV file into structured rows.

    Handles both 2026 format (utf-8-sig) and 2009 Wayback format (latin-1).

    Marks secondary-cert rows (the "- SEC" / Short-Handed entries IRC
    publishes alongside the primary cert) with `is_secondary=True` and
    strips the " - SEC" suffix from `boat_name`. The importer downstream
    must not create separate boats rows for secondary rows — that's the
    bug that produced 230+ "BOAT - SEC" duplicate boat rows.
    """
    rows = []
    # Detect encoding: try utf-8 first, fall back to latin-1
    encoding = "utf-8-sig"
    try:
        with open(path, encoding="utf-8-sig") as f:
            f.read()
    except UnicodeDecodeError:
        encoding = "latin-1"

    with open(path, newline="", encoding=encoding) as f:
        reader = csv.DictReader(f)
        for raw in reader:
            mapped = {}
            for csv_col, field_name in COLUMN_MAP.items():
                val = raw.get(csv_col, "").strip()
                # Don't overwrite a non-empty value with empty (handles alias columns)
                if val or field_name not in mapped:
                    mapped[field_name] = val

            # Type conversions
            tcc = _safe_decimal(mapped["tcc"])
            if tcc is None:
                continue  # Skip rows without a valid TCC

            cleaned_name, is_secondary = _detect_secondary(
                mapped["boat_name"], mapped.get("secondary")
            )

            row = TCCListingRow(
                boat_name=cleaned_name,
                sail_number=mapped["sail_number"],
                cert_number=mapped["cert_number"],
                issue_date=mapped.get("issue_date"),
                cert_year=_safe_int(mapped.get("cert_year", "")),
                tcc=tcc,
                endorsed=mapped.get("endorsed") or None,
                secondary=mapped.get("secondary") or None,
                is_secondary=is_secondary,
                non_spi_tcc=_safe_decimal(mapped.get("non_spi_tcc", "")),
                crew=_safe_int(mapped.get("crew", "")),
                dlr=_safe_int(mapped.get("dlr", "")),
                lh=_safe_decimal(mapped.get("lh", "")),
                beam=_safe_decimal(mapped.get("beam", "")),
                draft=_safe_decimal(mapped.get("draft", "")),
                single_furling_headsail=mapped.get("single_furling_headsail") or None,
                headsails=_safe_int(mapped.get("headsails", "")),
                flying_headsails=_safe_int(mapped.get("flying_headsails", "")),
                spinnakers=_safe_int(mapped.get("spinnakers", "")),
                series_date=_safe_int(mapped.get("series_date", "")),
                age_date=_safe_int(mapped.get("age_date", "")),
                racing_area=_safe_int(mapped.get("racing_area", "")),
                ssb_base_value=_safe_int(mapped.get("ssb_base_value", "")),
                stix=_safe_int(mapped.get("stix", "")),
                avs=_safe_int(mapped.get("avs", "")),
                category=mapped.get("category") or None,
                valid_code=mapped.get("valid_code") or None,
            )
            rows.append(row)
    return rows
