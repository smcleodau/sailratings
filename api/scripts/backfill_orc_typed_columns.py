"""Backfill typed columns on orc_certificates from raw_data JSONB.

Two raw_data shapes exist in this table:

1. RICH (~2.2k rows) — full RMS payload from data.orc.org `DownBoatRMS` action.
   Keys: RefNo, YachtName, SailNo, Class, Builder, Designer, Age_Year, GPH,
   OSN, CDL, TND_Offshore_Low/Medium/High, LOA, Dspl_Measurement, Draft,
   Area_Main, Area_Jib, Area_Sym, Area_Asym, Stability_Index, ...

2. SHALLOW (~11.6k rows) — active-cert XML index only.
   Keys: yacht_name, sail_no, class_name, country_id, family, family_name,
   cert_type, cert_type_name, vpp_year, ref_no, dxt_id, is_one_design,
   issue_date_str, expiry_str. NO designer/builder/dimensions.

This script handles both. It only writes a typed column when:
  - the typed column is currently NULL, AND
  - the raw_data has a non-empty value for the corresponding key.

It NEVER overwrites a non-null typed column (raw_data is authoritative
only when the typed col is null — protects any human edits).

The script is idempotent — re-running it after success is a no-op.

USAGE:
    source .venv/bin/activate && source ~/.env
    python scripts/backfill_orc_typed_columns.py            # apply changes
    python scripts/backfill_orc_typed_columns.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from irc_data.db.connection import get_engine

BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# raw_data key -> typed column mapping. Each entry is:
#     typed_column: (list_of_raw_keys_in_priority_order, coercer)
#
# The coercer converts the raw JSON value to the typed-column Python type.
# For "synthetic" columns (sail_area_upwind = Area_Main + Area_Jib) we set
# the raw keys to () and resolve in code.
# ---------------------------------------------------------------------------
def _safe_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    s = str(value).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    try:
        return int(float(s))  # handles "2019" and "2019.0"
    except (ValueError, TypeError):
        return None


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return str(value).strip() or None


# typed_column -> (priority list of raw_data keys, coercer fn)
MAPPING: dict[str, tuple[tuple[str, ...], Any]] = {
    # text fields — try rich-shape key first, fall back to shallow-shape key
    "yacht_name":   (("YachtName", "yacht_name", "BIN"), _safe_str),
    "sail_no":      (("SailNo", "sail_no"),              _safe_str),
    "class_name":   (("Class", "class_name"),            _safe_str),
    "builder":      (("Builder",),                       _safe_str),
    "designer":     (("Designer",),                      _safe_str),
    # numerics — rich-shape only
    "year_built":   (("Age_Year",),                      _safe_int),
    "gph":          (("GPH",),                           _safe_decimal),
    "osn":          (("OSN",),                           _safe_decimal),
    "cdl":          (("CDL",),                           _safe_decimal),
    "triple_low":   (("TND_Offshore_Low",),              _safe_decimal),
    "triple_med":   (("TND_Offshore_Medium",),           _safe_decimal),
    "triple_high":  (("TND_Offshore_High",),             _safe_decimal),
    "loa":          (("LOA",),                           _safe_decimal),
    "displacement": (("Dspl_Measurement",),              _safe_decimal),
    "draft":        (("Draft",),                         _safe_decimal),
    "stability_index": (("Stability_Index",),            _safe_decimal),
    # synthetic — handled specially in compute_value()
    "sail_area_upwind":   ((), None),
    "sail_area_downwind": ((), None),
}

# Columns NOT in MAPPING but in the schema (calculated/external):
#   - id, boat_id           — surrogate / FK
#   - snapshot_date         — ingest metadata
#   - ref_no, country_id    — primary key parts (always populated at insert)
#   - owner_name            — NEVER in raw_data (ORC API does not expose it).
#                             Flagged as not-backfillable. Likely sourced
#                             elsewhere or remains permanently NULL.
#   - created_at            — server default
#   - raw_data              — source
UNMAPPED_TYPED_COLS = {"owner_name"}


def _first_present(raw: dict, keys: tuple[str, ...]) -> Any:
    """Return the first non-empty value for any of `keys` in `raw`."""
    for k in keys:
        if k in raw:
            v = raw[k]
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            return v
    return None


def compute_value(col: str, raw: dict) -> Any:
    """Resolve the value for `col` from `raw`. Returns None if not derivable."""
    if col == "sail_area_upwind":
        main = _safe_decimal(raw.get("Area_Main"))
        jib = _safe_decimal(raw.get("Area_Jib"))
        if main is not None and jib is not None:
            return main + jib
        return None
    if col == "sail_area_downwind":
        # Prefer symmetric area; fall back to asymmetric (per scraper logic).
        for k in ("Area_Sym", "Area_Asym"):
            v = _safe_decimal(raw.get(k))
            if v is not None:
                return v
        return None

    keys, coercer = MAPPING[col]
    raw_val = _first_present(raw, keys)
    if raw_val is None:
        return None
    return coercer(raw_val)


def fetch_fill_counts(session: Session) -> dict[str, int]:
    """Return current non-null count per typed column."""
    cols = list(MAPPING.keys())
    select_parts = ", ".join(f"COUNT({c}) AS {c}" for c in cols)
    row = session.execute(text(f"SELECT {select_parts} FROM orc_certificates")).one()
    return dict(zip(cols, row))


def fetch_total(session: Session) -> int:
    return session.execute(text("SELECT COUNT(*) FROM orc_certificates")).scalar_one()


def run_backfill(dry_run: bool = False) -> None:
    engine = get_engine()
    updates_per_col: dict[str, int] = {c: 0 for c in MAPPING}
    rows_touched = 0
    rows_scanned = 0

    with Session(engine) as session:
        total = fetch_total(session)
        before = fetch_fill_counts(session)

        print(f"Scanning {total:,} orc_certificates rows...")
        print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'APPLYING UPDATES'}")
        print()

        # Stream rows in batches. Only need rows where at least ONE typed col is NULL.
        null_filter = " OR ".join(f"{c} IS NULL" for c in MAPPING)
        sql = text(f"""
            SELECT id, raw_data,
                   {', '.join(MAPPING.keys())}
            FROM orc_certificates
            WHERE ({null_filter})
            ORDER BY id
        """)

        result = session.execute(sql)
        cols = list(MAPPING.keys())

        pending_updates: list[tuple[int, dict[str, Any]]] = []

        for row in result:
            rows_scanned += 1
            row_id = row[0]
            raw = row[1]
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (ValueError, TypeError):
                    continue
            if not isinstance(raw, dict):
                continue

            current = dict(zip(cols, row[2:]))
            updates: dict[str, Any] = {}

            for col in cols:
                if current[col] is not None:
                    continue  # never overwrite an existing typed value
                new_val = compute_value(col, raw)
                if new_val is None:
                    continue
                updates[col] = new_val
                updates_per_col[col] += 1

            if updates:
                rows_touched += 1
                pending_updates.append((row_id, updates))

            # Flush batch
            if not dry_run and len(pending_updates) >= BATCH_SIZE:
                _flush(session, pending_updates)
                pending_updates.clear()
                print(f"  ... committed batch (touched {rows_touched:,} rows so far)")

        if not dry_run and pending_updates:
            _flush(session, pending_updates)
            pending_updates.clear()

        if dry_run:
            session.rollback()
        else:
            session.commit()

        after = fetch_fill_counts(session)

    # ----- Report -----
    print()
    print(f"Rows scanned (had at least one NULL typed col): {rows_scanned:,}")
    print(f"Rows that received at least one new value:      {rows_touched:,}")
    print()
    print(f"{'column':<24}{'before':>10}{'after':>10}{'delta':>10}{'now %':>8}")
    print("-" * 62)
    for c in cols:
        b = before[c]
        a = after[c]
        pct = (a / total * 100) if total else 0
        print(f"{c:<24}{b:>10,}{a:>10,}{(a - b):>+10,}{pct:>7.1f}%")
    print()
    print("Columns NOT backfillable (not present in raw_data):")
    for c in sorted(UNMAPPED_TYPED_COLS):
        print(f"  - {c}")


def _flush(session: Session, pending: list[tuple[int, dict[str, Any]]]) -> None:
    """Apply a batch of per-row updates. Each row gets only the columns it needs."""
    for row_id, updates in pending:
        set_clause = ", ".join(f"{c} = :{c}" for c in updates)
        params = {**updates, "id": row_id}
        session.execute(
            text(f"UPDATE orc_certificates SET {set_clause} WHERE id = :id"),
            params,
        )
    session.flush()
    session.commit()
    session.begin()  # start a new transaction for the next batch


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill orc_certificates typed columns from raw_data.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write.")
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
