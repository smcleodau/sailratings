"""Build a master cert-number index from harvested TCC listing CSVs.

Inputs: a directory of CSV snapshots, one per (year, timestamp) — produced
by :func:`irc_data.scrapers.wayback.harvest_tcc_archives`. Files follow the
convention ``tcc_{year}_{timestamp}.csv``.

Output: a deduplicated list of records ``{cert_number, boat_name,
sail_number, year}`` keyed by cert number. When the same cert appears in
multiple snapshots, the newest snapshot wins — boat names and sail numbers
change over time and the most-recent snapshot is the closest to truth.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

# Columns we tolerate, in priority order. ircrating.org has used several
# header conventions over the years.
_CERT_KEYS = ("CertNo", "Cert No", "cert_number", "Certificate Number")
_BOAT_KEYS = ("BoatName", "Boat Name", "boat_name", "Boat")
_SAIL_KEYS = ("SailNo", "Sail No", "sail_number", "Sail Number", "Sail")


def _first(row: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        val = row.get(k)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


def _read_tcc_csv(path: Path) -> Iterator[dict]:
    """Yield ``{cert_number, boat_name, sail_number}`` per row of ``path``.

    The reader is permissive — utf-8-sig handles BOMs, ``errors='ignore'``
    skips occasional latin-1 bytes that appear in historical CSVs.
    """
    with path.open(encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cert = _first(row, _CERT_KEYS)
            if not cert:
                continue
            yield {
                "cert_number": cert,
                "boat_name": _first(row, _BOAT_KEYS),
                "sail_number": _first(row, _SAIL_KEYS),
            }


def _year_from_path(path: Path) -> int | None:
    """Extract the snapshot year from a ``tcc_{year}_{timestamp}.csv`` name."""
    parts = path.stem.split("_")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def build_index_from_tcc_dir(dir_path: Path) -> list[dict]:
    """Walk ``dir_path`` for ``tcc_*.csv`` files and build the cert index.

    Returns a list of dicts: ``{cert_number, boat_name, sail_number, year}``.
    For each cert number the *latest* snapshot wins.
    """
    dir_path = Path(dir_path)
    seen: dict[str, dict] = {}
    if not dir_path.exists():
        return []
    for csv_path in sorted(dir_path.glob("tcc_*.csv")):
        year = _year_from_path(csv_path)
        if year is None:
            continue
        try:
            rows = list(_read_tcc_csv(csv_path))
        except Exception as exc:  # pragma: no cover - I/O resilience
            print(f"  Skipping {csv_path.name}: {exc}")
            continue
        for row in rows:
            cert = row["cert_number"]
            if cert not in seen or year > seen[cert]["year"]:
                seen[cert] = {**row, "year": year}
    return list(seen.values())
