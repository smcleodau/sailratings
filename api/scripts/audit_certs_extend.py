"""
Audit IRC certificate parser accuracy by comparing DB rows to extend.ai
extractions of the source PDFs.

Read-only. Does not write to the DB. Reads EXTEND_API_KEY from env.
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import psycopg
import requests

API = "https://api.extend.ai"
VERSION = "2026-02-09"
API_KEY = os.environ["EXTEND_API_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "x-extend-api-version": VERSION,
}

# Stratified sample: 15 certs across size buckets
SAMPLE_CERTS = [
    # large (3)
    "48019", "36270", "33023",
    # mid (5)
    "15417", "4271", "50343", "16053", "50233",
    # small (4)
    "50173", "14940", "12558", "5242",
    # tiny (2)
    "21050", "48825",
    # nodisp (1)
    "44756",
]

# Field map: DB column -> (extend schema property, type, description)
FIELDS = {
    "lh": ("LH", "number", "Hull length LH in metres"),
    "beam": ("beam", "number", "Maximum beam in metres"),
    "draft": ("draft", "number", "Maximum draft in metres"),
    "displacement": ("displacement", "number", "Boat displacement in kilograms (kg)"),
    "p": ("P", "number", "Mainsail luff length P in metres"),
    "e": ("E", "number", "Mainsail foot length E in metres"),
    "j": ("J", "number", "Foretriangle base J in metres"),
    "fl": ("FL", "number", "Headsail luff perpendicular FL in metres"),
    "spl": ("SPL", "number", "Spinnaker pole length SPL in metres"),
    "asym_slu": ("AsymSLU", "number", "Asymmetric spinnaker luff length SLU in metres"),
}

SCHEMA = {
    "type": "object",
    "properties": {
        name: {"type": [typ, "null"], "description": desc}
        for _, (name, typ, desc) in FIELDS.items()
    },
}

EXTRACTION_RULES = (
    "This is an IRC measurement certificate from the Royal Ocean Racing Club "
    "(RORC). Extract the numerical rating measurement values exactly as printed. "
    "All lengths are in metres, displacement in kilograms. If a value is not "
    "present on the certificate, return null. Do not infer or compute values. "
    "LH is the hull length (not LOA). P, E, J, FL are standard rig measurements. "
    "SPL is the spinnaker pole length. AsymSLU is the asymmetric spinnaker luff."
)


def upload_pdf(path: Path) -> str:
    with path.open("rb") as f:
        r = requests.post(
            f"{API}/files/upload",
            headers=HEADERS,
            files={"file": (path.name, f, "application/pdf")},
            timeout=120,
        )
    r.raise_for_status()
    body = r.json()
    # response shape: {"success": true, "file": {"id": "...", ...}}
    fobj = body.get("file") or body
    return fobj["id"]


def extract(file_id: str) -> dict:
    payload = {
        "config": {
            "baseProcessor": "extraction_performance",
            "schema": SCHEMA,
            "extractionRules": EXTRACTION_RULES,
        },
        "file": {"id": file_id},
    }
    r = requests.post(
        f"{API}/extract",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=300,
    )
    if r.status_code >= 400:
        print(f"  ERROR {r.status_code}: {r.text[:400]}", file=sys.stderr)
        r.raise_for_status()
    return r.json()


def to_float(x):
    if x is None or x == "":
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def classify(db_val, ex_val):
    db = to_float(db_val)
    ex = to_float(ex_val)
    if db is None and ex is None:
        return "BOTH_NULL", None
    if db is None:
        return "MISSING_DB", None
    if ex is None:
        return "MISSING_PDF", None
    if db == 0 and ex == 0:
        return "OK", 0.0
    denom = abs(db) if db != 0 else abs(ex)
    pct = abs(db - ex) / denom * 100.0
    if pct < 0.5:
        return "OK", pct
    if pct <= 1.0:
        return "MINOR", pct
    if pct <= 5.0:
        return "MODERATE", pct
    return "MAJOR", pct


def main():
    out_dir = Path("/home/irc-data/code/sailratings/api/data/audit_extend")
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = psycopg.connect("postgresql://irc:irc@localhost:5433/irc_data")
    cols = ["cert_number", "pdf_path", "issue_date"] + list(FIELDS.keys())
    col_sql = ", ".join(cols)
    cur = conn.cursor()
    cur.execute(
        f"SELECT {col_sql} FROM certificates WHERE cert_number = ANY(%s)",
        (SAMPLE_CERTS,),
    )
    rows = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    conn.close()

    results = []
    for cn in SAMPLE_CERTS:
        if cn not in rows:
            print(f"[{cn}] not in DB, skipping", file=sys.stderr)
            continue
        row = rows[cn]
        pdf_path = Path(row["pdf_path"])
        if not pdf_path.exists():
            print(f"[{cn}] PDF missing: {pdf_path}", file=sys.stderr)
            continue
        print(f"[{cn}] uploading {pdf_path.name} ...", file=sys.stderr)
        try:
            file_id = upload_pdf(pdf_path)
            t0 = time.time()
            ex_resp = extract(file_id)
            print(f"[{cn}] extracted in {time.time()-t0:.1f}s", file=sys.stderr)
        except Exception as e:
            print(f"[{cn}] FAILED: {e}", file=sys.stderr)
            continue

        # Extend returns {"object":"extract_run", "output": {"value": {...}, "metadata": {...}}}
        output = None
        out_root = ex_resp.get("output")
        if isinstance(out_root, dict):
            if "value" in out_root and isinstance(out_root["value"], dict):
                output = out_root["value"]
            else:
                output = out_root
        if output is None:
            print(f"[{cn}] could not find output in response. Keys: {list(ex_resp.keys())}", file=sys.stderr)
            # dump for inspection
            (out_dir / f"{cn}_raw.json").write_text(json.dumps(ex_resp, indent=2, default=str))
            continue

        rec = {"cert_number": cn, "pdf": pdf_path.name, "fields": {}}
        for db_col, (ex_name, _, _) in FIELDS.items():
            db_val = row[db_col]
            ex_val = output.get(ex_name)
            # extend.ai sometimes wraps values: {"value": ..., "confidence": ...}
            if isinstance(ex_val, dict) and "value" in ex_val:
                ex_val = ex_val["value"]
            klass, pct = classify(db_val, ex_val)
            rec["fields"][db_col] = {
                "db": to_float(db_val),
                "extend": to_float(ex_val),
                "class": klass,
                "pct": pct,
            }
        results.append(rec)
        (out_dir / f"{cn}_extend.json").write_text(json.dumps(output, indent=2, default=str))

    (out_dir / "audit_results.json").write_text(json.dumps(results, indent=2, default=str))

    # Summary table
    print("\n=== PER-FIELD ERROR RATES ===")
    print(f"{'field':<14} {'OK':>4} {'MIN':>4} {'MOD':>4} {'MAJ':>4} {'MdDB':>5} {'MdPDF':>6} {'N':>3}")
    for col in FIELDS:
        counts = {"OK": 0, "MINOR": 0, "MODERATE": 0, "MAJOR": 0, "MISSING_DB": 0, "MISSING_PDF": 0, "BOTH_NULL": 0}
        for r in results:
            counts[r["fields"][col]["class"]] += 1
        n = sum(counts.values()) - counts["BOTH_NULL"]
        print(f"{col:<14} {counts['OK']:>4} {counts['MINOR']:>4} {counts['MODERATE']:>4} {counts['MAJOR']:>4} {counts['MISSING_DB']:>5} {counts['MISSING_PDF']:>6} {n:>3}")

    # Top mismatches
    print("\n=== MISMATCHES (MODERATE/MAJOR/MISSING_DB) ===")
    for r in results:
        for col, v in r["fields"].items():
            if v["class"] in ("MODERATE", "MAJOR", "MISSING_DB"):
                print(f"  {r['cert_number']:<8} {col:<14} db={v['db']!s:<10} extend={v['extend']!s:<10} class={v['class']} pct={v['pct']}")


if __name__ == "__main__":
    main()
