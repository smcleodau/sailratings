#!/usr/bin/env python3
"""SM-01-08 — extract golden-fixture datasets from the dev database.

Pulls the full report universe for each fixture boat (the boat row, its
TCC snapshots, certificates, identities, race results, plus every boat
that ever shared a race with it — together with those boats' snapshots
and identities) and writes a self-contained ``dataset.json`` per boat
under ``api/tests/report/golden/<slug>/``.

Usage::

    python api/scripts/sm_01_08_extract_fixture.py [--only chilli_pepper]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from irc_data.analysis.backtest import GOLDEN_BOATS, GOLDEN_FIXTURES_ROOT  # noqa: E402
from irc_data.config import DATABASE_URL  # noqa: E402

# Boat ids in the dev database (verified 2026-06). DIABLO-J's design class
# is NULL upstream; the fixture restores the J/92 context so the design
# report exercises the design-class code paths.
FIXTURE_SOURCES = {
    "chilli_pepper": {"boat_id": 12067, "design_canonical": "Sunfast 3300"},
    "diablo_j": {"boat_id": 792, "design_canonical": "J/92"},
    "kestrel": {"boat_id": 21068, "design_canonical": "Sunfast 3300"},
}

_SNAPSHOT_COLS = (
    "boat_id, snapshot_date, cert_year, tcc, non_spi_tcc, lh, beam, draft, "
    "headsails, spinnakers, crew, dlr"
)
_CERT_COLS = (
    "boat_id, cert_number, issue_date, source, source_url, lh, beam, draft, "
    "displacement_kg, p, e, j, stl, muw, mhw, hlu, hlp, sym_slu, sym_sf, raw_data"
)
_RACE_COLS = (
    "boat_id, event_name, race_name, event_date, race_number, "
    "race_date_specific, place, fleet_size, class_name, status, "
    "rating_value, corrected_time, elapsed_time, organizing_club, "
    "source, source_url, raw_data"
)
_IDENTITY_COLS = "boat_id, boat_name, sail_number, owner, flag, source, observed_date"
_BOAT_COLS = (
    "id, boat_name, sail_number, cert_number, design, country, year_built, "
    "hull_id, builder, designer, design_canonical, loa, lwl, beam_max, "
    "displacement_kg"
)


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    out = []
    for r in conn.execute(text(sql), params or {}):
        d = dict(r._mapping)
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
            elif isinstance(v, (date, datetime)):
                d[k] = v.isoformat()
        out.append(d)
    return out


def extract(engine, slug: str, spec: dict) -> dict:
    boat_id = spec["boat_id"]
    with engine.connect() as conn:
        boat = _rows(conn, f"SELECT {_BOAT_COLS} FROM boats WHERE id = :i", {"i": boat_id})
        if not boat:
            raise SystemExit(f"boat_id {boat_id} not found")
        boat[0]["design_canonical"] = spec["design_canonical"]
        if boat[0]["design"] is None:
            boat[0]["design"] = spec["design_canonical"]

        # Peer boats = anyone who shared a finished race with the fixture boat.
        peer_ids = [
            r["peer_id"]
            for r in _rows(conn, """
                SELECT DISTINCT r2.boat_id AS peer_id
                FROM race_results r1
                JOIN race_results r2
                  ON r2.event_name = r1.event_name
                 AND COALESCE(r2.race_name, '') = COALESCE(r1.race_name, '')
                 AND r2.event_date IS NOT DISTINCT FROM r1.event_date
                WHERE r1.boat_id = :i AND r2.boat_id <> :i
                  AND r1.status = 'finished' AND r2.status = 'finished'
            """, {"i": boat_id})
        ]
        all_ids = sorted({boat_id, *peer_ids})

        boats = _rows(
            conn,
            f"SELECT {_BOAT_COLS} FROM boats WHERE id = ANY(:ids) ORDER BY id",
            {"ids": all_ids},
        )
        # Same design restoration for the fixture boat inside the full list.
        for b in boats:
            if b["id"] == boat_id:
                b["design_canonical"] = spec["design_canonical"]
                if b["design"] is None:
                    b["design"] = spec["design_canonical"]

        snapshots = _rows(conn, f"""
            SELECT DISTINCT ON (boat_id, snapshot_date) {_SNAPSHOT_COLS}
            FROM tcc_snapshots
            WHERE boat_id = ANY(:ids)
            ORDER BY boat_id, snapshot_date, id
        """, {"ids": all_ids})

        certs = _rows(conn, f"""
            SELECT DISTINCT ON (boat_id, cert_number) {_CERT_COLS}
            FROM irc_certificates
            WHERE boat_id = ANY(:ids)
            ORDER BY boat_id, cert_number, issue_date DESC NULLS LAST
        """, {"ids": all_ids})

        races = _rows(conn, f"""
            SELECT {_RACE_COLS} FROM race_results
            WHERE boat_id = ANY(:ids)
              AND (event_name, COALESCE(race_name, ''), COALESCE(event_date::text, '')) IN (
                  SELECT event_name, COALESCE(race_name, ''), COALESCE(event_date::text, '')
                  FROM race_results WHERE boat_id = :i
              )
            ORDER BY boat_id, event_date, id
        """, {"i": boat_id, "ids": all_ids})

        identities = _rows(conn, f"""
            SELECT DISTINCT ON (boat_id, boat_name, sail_number, source, observed_date)
                   {_IDENTITY_COLS}
            FROM boat_identities
            WHERE boat_id = ANY(:ids)
            ORDER BY boat_id, boat_name, sail_number, source, observed_date, id
        """, {"ids": all_ids})

    return {
        "fixture": slug,
        "fixture_boat_id": boat_id,
        "boat_name": boat[0]["boat_name"],
        "design_canonical": spec["design_canonical"],
        "boats": boats,
        "tcc_snapshots": snapshots,
        "irc_certificates": certs,
        "race_results": races,
        "boat_identities": identities,
        "counts": {
            "boats": len(boats),
            "tcc_snapshots": len(snapshots),
            "irc_certificates": len(certs),
            "race_results": len(races),
            "boat_identities": len(identities),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=[b.slug for b in GOLDEN_BOATS], default=None)
    ap.add_argument("--database-url", default=DATABASE_URL)
    args = ap.parse_args()

    engine = create_engine(args.database_url)
    for fb in GOLDEN_BOATS:
        if args.only and fb.slug != args.only:
            continue
        spec = FIXTURE_SOURCES[fb.slug]
        ds = extract(engine, fb.slug, spec)
        out_dir = GOLDEN_FIXTURES_ROOT / fb.slug
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "dataset.json"
        out.write_text(json.dumps(ds, indent=1, sort_keys=True, default=str))
        print(f"{fb.slug}: {ds['counts']} -> {out}")


if __name__ == "__main__":
    main()
