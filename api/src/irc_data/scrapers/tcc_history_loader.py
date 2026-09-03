"""Load harvested historical TCC listing CSVs into ``tcc_snapshots``.

OPS-02-12 — IRC history reconstruction at scale.

The daily ``irc-data scrape tcc`` importer only ever loads the *current*
listing, so ``tcc_snapshots`` carries a 2009/2025-2026 sandwich with a
15-year hole in the middle.  The Wayback harvest
(:func:`irc_data.scrapers.wayback.harvest_tcc_archives`) fills that hole
with ``tcc_{year}_{timestamp}.csv`` snapshots; this module is the piece
that turns those CSVs into per-boat rating history rows so the
"≥3 years of TCC history" KPI can actually move.

Differences from the live importer (``cli.import_csv``):

* **snapshot_date semantics** — historical rows use the snapshot's
  mid-year anchor ``date(year, 6, 1)`` rather than the literal download
  day.  The live importer writes one row per scrape *day*, which for
  history would create dozens of duplicate-year rows per boat and make
  the "years of history" KPI meaningless.  Anchoring to mid-year means
  ``(boat_id, snapshot_date)`` stays unique per snapshot-year and the
  calendar-year span between a boat's first and last row is a faithful
  "years of history" measure.
* **match-first** — historical rows never create ``boats`` rows.  A boat
  is matched by ``cert_number`` → ``(sail_number, cert_number)`` →
  ``(sail_number, normalised boat_name)`` (same precedence the secondary
  importer uses).  Unmatched rows are counted as *coverage*: a boat that
  appears in ≥3 distinct snapshot-years has ≥3 years of history even if
  it has since left the fleet and can't be matched to a live boats row.
* **secondary rows** (``- SEC`` / short-handed) are folded onto the
  primary boat exactly like the live importer — they never create boats.

Idempotent: re-importing the same snapshot file upserts the same
``(boat_id, snapshot_date)`` key, so re-runs converge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.operations import upsert_tcc_snapshot
from irc_data.parsers.tcc_csv import parse_tcc_csv

# How the mid-year anchor is derived from a ``tcc_{year}_{ts}.csv`` name.
_FNAME_RE = re.compile(r"^tcc_(\d{4})_(\d{4,})\.csv$")

_SNAPSHOT_FIELD_NAMES = (
    "cert_year",
    "tcc",
    "non_spi_tcc",
    "endorsed",
    "secondary",
    "crew",
    "dlr",
    "lh",
    "beam",
    "draft",
    "single_furling_headsail",
    "headsails",
    "flying_headsails",
    "spinnakers",
    "series_date",
    "age_date",
    "racing_area",
    "ssb_base_value",
    "stix",
    "avs",
    "category",
)


def _norm_name(name: str | None) -> str:
    return (name or "").strip().upper()


def _norm_sail(sail: str | None) -> str:
    return (sail or "").strip().upper()


def snapshot_anchor(year: int) -> date:
    """Mid-year anchor date for a snapshot-year (see module docstring)."""
    return date(year, 6, 1)


def year_from_path(path: Path) -> int | None:
    """Extract ``year`` from a ``tcc_{year}_{timestamp}.csv`` filename."""
    m = _FNAME_RE.match(Path(path).name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


@dataclass
class _BoatMaps:
    """Pre-loaded lookup tables so the import is O(rows) not O(rows × queries)."""

    by_cert: dict[str, int] = field(default_factory=dict)
    by_sail_cert: dict[tuple[str, str], int] = field(default_factory=dict)
    by_sail_name: dict[tuple[str, str], int] = field(default_factory=dict)

    @classmethod
    def load(cls, engine: Engine) -> "_BoatMaps":
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, boat_name, sail_number, cert_number FROM boats")
            ).fetchall()
        maps = cls()
        for boat_id, boat_name, sail_number, cert_number in rows:
            cert = (cert_number or "").strip()
            sail = _norm_sail(sail_number)
            name = _norm_name(boat_name)
            if cert and cert not in maps.by_cert:
                maps.by_cert[cert] = boat_id
            if cert:
                maps.by_sail_cert.setdefault((sail, cert), boat_id)
            if sail and name:
                maps.by_sail_name.setdefault((sail, name), boat_id)
        return maps

    def match(self, *, cert_number: str, sail_number: str, boat_name: str) -> int | None:
        cert = (cert_number or "").strip()
        sail = _norm_sail(sail_number)
        name = _norm_name(boat_name)
        if cert and cert in self.by_cert:
            return self.by_cert[cert]
        if cert and (sail, cert) in self.by_sail_cert:
            return self.by_sail_cert[(sail, cert)]
        if sail and name and (sail, name) in self.by_sail_name:
            return self.by_sail_name[(sail, name)]
        return None


def import_historical_tcc_dir(
    engine: Engine,
    dir_path: Path,
    *,
    progress_every: int = 0,
) -> dict:
    """Import every ``tcc_*.csv`` snapshot under ``dir_path``.

    Returns a stats dict::

        {
          "files": <snapshot files processed>,
          "rows": <CSV rows seen>,
          "snapshots_written": <tcc_snapshots upserted (matched boats)>,
          "secondary_attached": <secondary rows folded onto a primary boat>,
          "matched_boats": <distinct boats that received >=1 snapshot>,
          "coverage_boats_3y": <distinct boat-keys with >=3 snapshot-years>,
          "unmatched_rows": <rows with no boats match (coverage-only)>,
          "skipped_files": <filenames that didn't parse as a snapshot>,
        }
    """
    dir_path = Path(dir_path)
    maps = _BoatMaps.load(engine)

    stats = {
        "files": 0,
        "rows": 0,
        "snapshots_written": 0,
        "secondary_attached": 0,
        "matched_boats": 0,
        "coverage_boats_3y": 0,
        "unmatched_rows": 0,
        "skipped_files": 0,
    }

    matched_boat_ids: set[int] = set()
    # coverage key: prefer cert_number (stable identity across renames),
    # else normalised sail number.
    boat_years: dict[str, set[int]] = {}

    files = sorted(dir_path.glob("tcc_*.csv")) if dir_path.exists() else []
    for path in files:
        year = year_from_path(path)
        if year is None:
            stats["skipped_files"] += 1
            continue
        try:
            rows = parse_tcc_csv(path)
        except Exception as exc:  # noqa: BLE001 - skip a bad snapshot, keep going
            print(f"  Skipping {path.name}: {exc}")
            stats["skipped_files"] += 1
            continue

        stats["files"] += 1
        anchor = snapshot_anchor(year)

        for row in rows:
            stats["rows"] += 1
            cov_key = (
                f"cert:{row.cert_number.strip()}"
                if row.cert_number and row.cert_number.strip()
                else f"sail:{_norm_sail(row.sail_number)}"
            )
            boat_years.setdefault(cov_key, set()).add(year)

            boat_id = maps.match(
                cert_number=row.cert_number,
                sail_number=row.sail_number,
                boat_name=row.boat_name,
            )
            if boat_id is None:
                stats["unmatched_rows"] += 1
                continue

            if row.is_secondary:
                # Fold the secondary flag onto the primary snapshot, exactly
                # like the live importer — never create a parallel boat.
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE tcc_snapshots
                               SET secondary = COALESCE(:flag, secondary)
                             WHERE boat_id = :bid AND snapshot_date = :sd
                            """
                        ),
                        {"flag": row.secondary or "SEC", "bid": boat_id, "sd": anchor},
                    )
                stats["secondary_attached"] += 1
                matched_boat_ids.add(boat_id)
                continue

            fields = {name: getattr(row, name) for name in _SNAPSHOT_FIELD_NAMES}
            # cert_year is authoritative for the listing's year; fall back to
            # the snapshot filename year when the column is blank.
            if fields.get("cert_year") is None:
                fields["cert_year"] = year
            upsert_tcc_snapshot(engine, boat_id, anchor, **fields)
            stats["snapshots_written"] += 1
            matched_boat_ids.add(boat_id)

        if progress_every and stats["files"] % progress_every == 0:
            print(
                f"  ... {stats['files']} files, {stats['snapshots_written']} "
                f"snapshots written, {len(matched_boat_ids)} boats matched"
            )

    stats["matched_boats"] = len(matched_boat_ids)
    stats["coverage_boats_3y"] = sum(1 for yrs in boat_years.values() if len(yrs) >= 3)
    return stats
