"""Import race results from existing JSON files into the database.

Handles the cyca_sailsys_irc_results.json format and generic RaceResult
schema imports.
"""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.operations import (
    find_boat_by_sail_number,
    log_ingestion_end,
    log_ingestion_start,
    upsert_race_result,
)
from irc_data.matching.identity import normalize_name, normalize_sail


def _parse_date(date_str: str | None) -> date | None:
    """Parse date from various formats."""
    if not date_str:
        return None
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


_EXPLICIT_STATUS_CODES = {"DNF", "DNS", "DNC", "DSQ", "RET", "OCS", "RAF", "RDG", "ZFP"}


def named_legacy_count(engine: Engine, source: str, source_url: str) -> int:
    """Count distinct named boats in legacy rows for a URL.

    Used as the recall denominator in the ingest-event recall gate. Returns 0
    when there are no legacy rows (new URL — gate is skipped by the caller).
    """
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT COUNT(DISTINCT lower(regexp_replace(
                       coalesce(raw_data->>'boat_name', ''), '\\s+', '', 'g'
                   ))) AS n
            FROM race_results
            WHERE source = :source
              AND source_url = :url
              AND transport = 'legacy'
              AND coalesce(raw_data->>'boat_name', '') <> ''
        """), {"source": source, "url": source_url}).fetchone()
    return int(row.n or 0)


def _derive_status(raw_data: dict | None, place: int | None) -> str:
    """Decide race_results.status from a scraper's raw_data payload.

    SailSys and other scrapers populate raw_data['finish_time'] = None when a
    boat did not finish. A hardcoded 'finished' here mislabels every DNF in
    the database (e.g. SUN FISH B2G 2026, boat_id 12330).
    """
    raw = raw_data or {}
    explicit = (raw.get("status") or "").strip().upper()
    if explicit in _EXPLICIT_STATUS_CODES:
        return explicit
    if "finish_time" in raw:
        if raw["finish_time"]:
            return "finished"
        # SailSys sometimes returns a handicap place without a raw finish_time
        # for boats that did finish (corrected time present, raw time omitted).
        # Only treat as DNF when there is no place either.
        return "DNF" if place is None else "finished"
    return "finished"


def _safe_decimal(val) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError):
        return None


def _find_boat_by_name(engine: Engine, boat_name: str, tcc: Decimal | None = None) -> int | None:
    """Find a boat by name, optionally using TCC to disambiguate."""
    norm = normalize_name(boat_name)
    if not norm:
        return None

    with engine.connect() as conn:
        # Exact name match (case-insensitive)
        result = conn.execute(
            text("SELECT id FROM boats WHERE UPPER(TRIM(boat_name)) = :name LIMIT 2"),
            {"name": norm},
        )
        rows = result.fetchall()
        if len(rows) == 1:
            return rows[0][0]

        # If TCC provided, match by name + close TCC
        if tcc and rows:
            result = conn.execute(
                text("""
                    SELECT b.id FROM boats b
                    JOIN tcc_snapshots t ON t.boat_id = b.id
                    WHERE UPPER(TRIM(b.boat_name)) = :name
                      AND ABS(t.tcc - :tcc) < 0.005
                    ORDER BY t.snapshot_date DESC
                    LIMIT 1
                """),
                {"name": norm, "tcc": tcc},
            )
            row = result.first()
            if row:
                return row[0]

    return None


def import_sailsys_json(engine: Engine, json_path: Path) -> dict:
    """Import race results from cyca_sailsys_irc_results.json format.

    The JSON format has fields: series, series_id, race_date, sail_no,
    boat_name (hull ID), skipper (actual boat name), club, irc_tcc.

    Returns stats dict.
    """
    stats = {
        "total": 0,
        "imported": 0,
        "matched": 0,
        "skipped_no_name": 0,
        "errors": 0,
    }

    log_id = log_ingestion_start(engine, "sailsys_json_import", metadata={"file": str(json_path)})

    with open(json_path) as f:
        records = json.load(f)

    stats["total"] = len(records)

    for rec in records:
        # The "skipper" field is actually the boat name in this format
        boat_name = (rec.get("skipper") or "").strip()
        if not boat_name:
            stats["skipped_no_name"] += 1
            continue

        sail_no = (rec.get("sail_no") or "").strip()
        hull_id = (rec.get("boat_name") or "").strip()
        tcc = _safe_decimal(rec.get("irc_tcc"))
        event_date = _parse_date(rec.get("race_date"))
        series_name = rec.get("series", "Unknown Series")

        # Try to find the boat in our database
        boat_id = None

        # Strategy 1: sail number (if it's a real one, not just "C")
        if sail_no and len(sail_no) > 2:
            boat_id = find_boat_by_sail_number(engine, normalize_sail(sail_no))

        # Strategy 2: hull ID as sail number (SailSys stores hull IDs in boat_name)
        if not boat_id and hull_id and hull_id.isdigit() and len(hull_id) >= 3:
            boat_id = find_boat_by_sail_number(engine, hull_id)

        # Strategy 3: boat name match
        if not boat_id:
            boat_id = _find_boat_by_name(engine, boat_name, tcc)

        if boat_id:
            stats["matched"] += 1

        try:
            upsert_race_result(
                engine,
                boat_id=boat_id,
                event_name=series_name,
                event_date=event_date,
                race_name=None,  # SailSys JSON doesn't have per-race granularity
                event_series=series_name,
                organizing_club="CYCA",
                source="sailsys",
                rating_type="irc_tcc",
                rating_value=tcc,
                tcc_at_race=tcc,
                status=_derive_status(rec, place=None),
                raw_data={
                    "boat_name": boat_name,
                    "hull_id": hull_id,
                    "sail_no": sail_no,
                    "club": rec.get("club"),
                    "series_id": rec.get("series_id"),
                    "original": rec,
                },
            )
            stats["imported"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"  Error importing {boat_name}: {e}")

    log_ingestion_end(
        engine, log_id,
        status="completed" if not stats["errors"] else "completed_with_errors",
        records_found=stats["total"],
        records_new=stats["imported"],
        error_message=f"{stats['errors']} errors" if stats["errors"] else None,
    )

    return stats


def import_scraper_results(
    engine: Engine,
    results: list,
    source: str,
    organizing_club: str | None = None,
    transport: str | None = None,
) -> dict:
    """Import RaceResult objects from any scraper into the database.

    Works with the RaceResult pydantic model from parsers/schemas.py.
    Extracts fleet_size, class_name, event_date, and race_name from raw_data
    when available (SailSys populates these).

    ``transport`` is the ingestion-path tag persisted to ``race_results.transport``.
    Pass ``'legacy'`` for bespoke per-source scrapers and ``'firecrawl'`` for
    rows coming through the Firecrawl + Claude extractor pipeline. NULL is
    fine on pre-cutover rows.

    Returns stats dict.
    """
    stats = {"total": len(results), "imported": 0, "matched": 0, "errors": 0}

    log_id = log_ingestion_start(engine, source, metadata={"club": organizing_club})

    for result in results:
        boat_name = None
        sail_no = None

        # Extract boat info from raw_data
        raw = result.raw_data or {}
        boat_name = raw.get("boat_name", "")
        sail_no = raw.get("sail_number", "")

        # Try to find the boat
        boat_id = result.boat_id
        if not boat_id and sail_no:
            boat_id = find_boat_by_sail_number(engine, normalize_sail(sail_no))
        if not boat_id and boat_name:
            boat_id = _find_boat_by_name(engine, boat_name, result.tcc_at_race)

        if boat_id:
            stats["matched"] += 1

        # Extract enriched fields from raw_data
        fleet_size = raw.get("fleet_size")
        class_name = raw.get("division") or None
        race_name = raw.get("race_name")
        club = raw.get("club_name") or organizing_club

        # Use event_date from RaceResult model first, then parse from raw_data
        event_date = result.event_date
        if not event_date and raw.get("race_date"):
            event_date = _parse_date(raw["race_date"][:10] if raw["race_date"] else None)

        try:
            upsert_race_result(
                engine,
                boat_id=boat_id,
                event_name=result.event_name,
                event_date=event_date,
                race_name=race_name,
                source=source,
                source_url=result.source_url,
                organizing_club=club,
                rating_type="irc_tcc" if result.tcc_at_race else None,
                rating_value=result.tcc_at_race,
                tcc_at_race=result.tcc_at_race,
                place=result.place,
                fleet_size=fleet_size,
                class_name=class_name,
                division=result.division,
                status=_derive_status(result.raw_data, place=result.place),
                transport=transport,
                raw_data=result.raw_data,
            )
            stats["imported"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 5:
                print(f"  Error: {e}")

    log_ingestion_end(
        engine, log_id,
        status="completed" if not stats["errors"] else "completed_with_errors",
        records_found=stats["total"],
        records_new=stats["imported"],
    )

    return stats
