"""
Targeted gap-audit for IRC cert parser. Three buckets:

  A) sym_slu certs (n=20) — does the PDF say the spinnaker is asymmetric?
     Heuristic: asymmetric if PDF prints SLE > 0 and SLU != SLE (asym spin has
     SLU > SLE), or shows a labelled "Asymmetric"/"Asym" spinnaker section.
  B) SER / nodisp certs (n=20) — does the PDF actually contain
     displacement/draft/beam values the parser dropped?
  C) FL field (n=10) — does the PDF print FL? Quantify prevalence.

Read-only on the DB. Writes per-PDF extend JSON to data/audit_extend/.

API key from env EXTEND_API_KEY. Never logged.
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

OUT_DIR = Path("/home/irc-data/code/sailratings/api/data/audit_extend")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = {
    "type": "object",
    "properties": {
        # Hull dims (Bucket B targets these)
        "LH": {"type": ["number", "null"], "description": "Hull length LH in metres"},
        "beam": {"type": ["number", "null"], "description": "Maximum beam in metres"},
        "draft": {"type": ["number", "null"], "description": "Maximum draft in metres"},
        "displacement": {
            "type": ["number", "null"],
            "description": "Boat displacement in kilograms (kg)",
        },
        # Rig
        "P": {"type": ["number", "null"], "description": "Mainsail luff P in metres"},
        "E": {"type": ["number", "null"], "description": "Mainsail foot E in metres"},
        "J": {"type": ["number", "null"], "description": "Foretriangle base J in metres"},
        "FL": {
            "type": ["number", "null"],
            "description": "Headsail luff perpendicular FL in metres. Look for a line labelled exactly 'FL' in the rig / headsail measurement section. Return null if not present.",
        },
        "SPL": {
            "type": ["number", "null"],
            "description": "Spinnaker pole length SPL in metres",
        },
        # Symmetric spinnaker section — typically labelled 'Symmetric Spinnaker' or 'Sym Spin'
        "SymSLU": {
            "type": ["number", "null"],
            "description": "Symmetric spinnaker luff length SLU in metres. Only return if explicitly under a 'Symmetric' spinnaker section or labelled as sym. Return null otherwise.",
        },
        "SymSLE": {
            "type": ["number", "null"],
            "description": "Symmetric spinnaker leech length SLE in metres",
        },
        "SymSF": {
            "type": ["number", "null"],
            "description": "Symmetric spinnaker foot SF in metres",
        },
        "SymSHW": {
            "type": ["number", "null"],
            "description": "Symmetric spinnaker half width SHW in metres",
        },
        # Asymmetric spinnaker section
        "AsymSLU": {
            "type": ["number", "null"],
            "description": "Asymmetric spinnaker luff length SLU in metres. Only return if explicitly under an 'Asymmetric' / 'Asym' spinnaker section. Return null otherwise.",
        },
        "AsymSLE": {
            "type": ["number", "null"],
            "description": "Asymmetric spinnaker leech length SLE in metres",
        },
        "AsymSF": {
            "type": ["number", "null"],
            "description": "Asymmetric spinnaker foot SF in metres",
        },
        "AsymSHW": {
            "type": ["number", "null"],
            "description": "Asymmetric spinnaker half width SHW in metres",
        },
        # Classification flags
        "spinnaker_type": {
            "type": ["string", "null"],
            "description": "Best label for the spinnaker on this cert: one of 'symmetric', 'asymmetric', 'both', or null if no spinnaker section.",
        },
        "is_SER": {
            "type": ["boolean", "null"],
            "description": "True if this is an IRC 'SER' (Series) certificate or otherwise omits hull measurements (no displacement, no hull weighing).",
        },
    },
}

RULES = (
    "This is an IRC measurement certificate from the Royal Ocean Racing Club. "
    "Extract the printed values exactly. All lengths metres, displacement kg. "
    "Return null for any field not printed on the certificate. Do not infer. "
    "Distinguish Symmetric vs Asymmetric spinnaker sections by their printed "
    "labels — only populate the Sym* fields if the section is labelled "
    "symmetric (or unlabelled but clearly sym), and Asym* fields if labelled "
    "asymmetric. If the cert uses an SER (Series) framework with no hull "
    "weighing, set is_SER=true."
)

BUCKETS = {
    "A": [  # sym_slu certs
        ("13634", "/home/irc-data/code/sailratings/api/data/raw/certificates/13634_CENTENNIAL V_PHI2018.pdf"),
        ("14683", "/home/irc-data/code/sailratings/api/data/raw/certificates/14683_WHITE LOTUS_IRL1333.pdf"),
        ("48329", "/home/irc-data/code/sailratings/api/data/raw/certificates/48329_POUSS'1_FRA53318.pdf"),
        ("50433", "/home/irc-data/code/sailratings/api/data/raw/certificates/50433_SPARROW IV_JPN6870.pdf"),
        ("48687", "/home/irc-data/code/sailratings/api/data/raw/certificates/48687_CHENAPAN IV - SEC_FRA53205.pdf"),
        ("42993", "/home/irc-data/code/sailratings/api/data/raw/certificates/42993_1122TREKKEE_JPN1122.pdf"),
        ("49794", "/home/irc-data/code/sailratings/api/data/raw/certificates/49794_ASTARTIA_DP4892.pdf"),
        ("13051", "/home/irc-data/code/sailratings/api/data/raw/certificates/13051_MOJO RISIN_GBR8809R.pdf"),
        ("16858", "/home/irc-data/code/sailratings/api/data/raw/certificates/16858_CHUTZPAH_R33.pdf"),
        ("9453",  "/home/irc-data/code/sailratings/api/data/raw/certificates/9453_ENERGY OF POOLE_GBR6525T.pdf"),
        ("45636", "/home/irc-data/code/sailratings/api/data/raw/certificates/45636_SIDNEY II_GBR1419L.pdf"),
        ("40701", "/home/irc-data/code/sailratings/api/data/raw/certificates/40701_ZEN_52001.pdf"),
        ("37251", "/home/irc-data/code/sailratings/api/data/raw/certificates/37251_RAMA RAMA VG_SIN88888.pdf"),
        ("49469", "/home/irc-data/code/sailratings/api/data/raw/certificates/historical/49469_H30_HKG1280.pdf"),
        ("35841", "/home/irc-data/code/sailratings/api/data/raw/certificates/35841_ZAPPY S_FRA37258.pdf"),
        ("50232", "/home/irc-data/code/sailratings/api/data/raw/certificates/50232_ARKAS BLUE MOON - SEC_TUR12122.pdf"),
        ("42335", "/home/irc-data/code/sailratings/api/data/raw/certificates/42335_SAMOA_JPN6669.pdf"),
        ("50074", "/home/irc-data/code/sailratings/api/data/raw/certificates/50074_PRINCESSE GOTIONUDE II _FRA53116.pdf"),
        ("17555", "/home/irc-data/code/sailratings/api/data/raw/certificates/17555_HOPE_GBR9403R.pdf"),
        ("50460", "/home/irc-data/code/sailratings/api/data/raw/certificates/50460_PRIORIDAD 314_CHI5088.pdf"),
    ],
    "B": [  # SER / nodisp certs
        ("50486", "/home/irc-data/code/sailratings/api/data/raw/certificates/50486_TONIC 3_COL4100.pdf"),
        ("50598", "/home/irc-data/code/sailratings/api/data/raw/certificates/50598_ENDURANCE_CHI219.pdf"),
        ("36386", "/home/irc-data/code/sailratings/api/data/raw/certificates/36386_UNDA 2_BEL799.pdf"),
        ("50717", "/home/irc-data/code/sailratings/api/data/raw/certificates/50717_CHIMERE - SEC_F 16.pdf"),
        ("48369", "/home/irc-data/code/sailratings/api/data/raw/certificates/48369_KO SAMUI_ESP0707.pdf"),
        ("27067", "/home/irc-data/code/sailratings/api/data/raw/certificates/27067_SOUL SEEKER_ITA14141.pdf"),
        ("45655", "/home/irc-data/code/sailratings/api/data/raw/certificates/45655_CHAMPAGNE_FRA72.pdf"),
        ("50357", "/home/irc-data/code/sailratings/api/data/raw/certificates/50357_MARACUJA_FRA53537.pdf"),
        ("39808", "/home/irc-data/code/sailratings/api/data/raw/certificates/historical/39808_41 SUD_FRA8995.pdf"),
        ("47921", "/home/irc-data/code/sailratings/api/data/raw/certificates/47921_FELICITA_FRA45204.pdf"),
        ("49298", "/home/irc-data/code/sailratings/api/data/raw/certificates/49298_SYLPHEA IV_ITA17843.pdf"),
        ("44562", "/home/irc-data/code/sailratings/api/data/raw/certificates/44562_LOLI FAST_ITA17300.pdf"),
        ("47824", "/home/irc-data/code/sailratings/api/data/raw/certificates/47824_FUN_FRA45596.pdf"),
        ("50481", "/home/irc-data/code/sailratings/api/data/raw/certificates/50481_INVENTION_COL5000.pdf"),
        ("34632", "/home/irc-data/code/sailratings/api/data/raw/certificates/34632_CORTO MALTESE_ITA15790.pdf"),
        ("49002", "/home/irc-data/code/sailratings/api/data/raw/certificates/49002_READY_FRA29.pdf"),
        ("47703", "/home/irc-data/code/sailratings/api/data/raw/certificates/47703_ZEPHYR OF FOLDING_FRA45386.pdf"),
        ("27225", "/home/irc-data/code/sailratings/api/data/raw/certificates/27225_PICHENETTE_FRA9010.pdf"),
        ("34275", "/home/irc-data/code/sailratings/api/data/raw/certificates/34275_TURI_ESP7555.pdf"),
        ("34829", "/home/irc-data/code/sailratings/api/data/raw/certificates/34829_JANNER BE GOOD_FRA35908.pdf"),
    ],
    "C": [  # FL prevalence random sample
        ("50249", "/home/irc-data/code/sailratings/api/data/raw/certificates/50249_TREX_075.pdf"),
        ("10569", "/home/irc-data/code/sailratings/api/data/raw/certificates/10569_THE J_FRA1335.pdf"),
        ("50402", "/home/irc-data/code/sailratings/api/data/raw/certificates/50402_L'EMBELLIE IV_FRA44049.pdf"),
        ("49433", "/home/irc-data/code/sailratings/api/data/raw/certificates/49433_SNATCH N' FURIOUS_FRA9183.pdf"),
        ("18587", "/home/irc-data/code/sailratings/api/data/raw/certificates/18587_RAKE_JPN6798.pdf"),
        ("47628", "/home/irc-data/code/sailratings/api/data/raw/certificates/47628_MAGIC CARPET E_GBR1001R.pdf"),
        ("33886", "/home/irc-data/code/sailratings/api/data/raw/certificates/33886_KICK_FRA9535.pdf"),
        ("48639", "/home/irc-data/code/sailratings/api/data/raw/certificates/48639_BLUE007 - SEC_FRA35082.pdf"),
        ("15490", "/home/irc-data/code/sailratings/api/data/raw/certificates/15490_SNAFU_GBR5818R.pdf"),
        ("49554", "/home/irc-data/code/sailratings/api/data/raw/certificates/49554_FIFTH ELEMENT _GBR8428N.pdf"),
    ],
}


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
    fobj = body.get("file") or body
    return fobj["id"]


def extract(file_id: str) -> dict:
    payload = {
        "config": {
            "baseProcessor": "extraction_performance",
            "schema": SCHEMA,
            "extractionRules": RULES,
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


def unwrap_value(v):
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def to_float(x):
    x = unwrap_value(x)
    if x is None or x == "":
        return None
    if isinstance(x, Decimal):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def run_one(cert_number: str, pdf_path: str) -> dict | None:
    p = Path(pdf_path)
    cache = OUT_DIR / f"gap_{cert_number}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    if not p.exists():
        print(f"[{cert_number}] PDF missing: {p}", file=sys.stderr)
        return None
    try:
        print(f"[{cert_number}] uploading {p.name}", file=sys.stderr)
        fid = upload_pdf(p)
        t0 = time.time()
        resp = extract(fid)
        print(f"[{cert_number}] done in {time.time()-t0:.1f}s", file=sys.stderr)
    except Exception as e:
        print(f"[{cert_number}] FAILED: {e}", file=sys.stderr)
        return None

    out = resp.get("output")
    if isinstance(out, dict) and "value" in out and isinstance(out["value"], dict):
        out = out["value"]
    if not isinstance(out, dict):
        (OUT_DIR / f"gap_{cert_number}_raw.json").write_text(
            json.dumps(resp, indent=2, default=str)
        )
        return None
    cache.write_text(json.dumps(out, indent=2, default=str))
    return out


def load_db_rows(cert_numbers: list[str]) -> dict[str, dict]:
    cols = [
        "cert_number", "pdf_path", "lh", "beam", "draft", "displacement",
        "p", "e", "j", "fl", "spl",
        "sym_slu", "sym_sle", "sym_sf", "sym_shw",
        "asym_slu", "asym_sle", "asym_sf", "asym_shw",
    ]
    conn = psycopg.connect("postgresql://irc:irc@localhost:5433/irc_data")
    cur = conn.cursor()
    cur.execute(
        f"SELECT {', '.join(cols)} FROM certificates WHERE cert_number = ANY(%s)",
        (cert_numbers,),
    )
    rows = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
    conn.close()
    return rows


def main():
    all_certs = [c for b in BUCKETS.values() for c, _ in b]
    db = load_db_rows(all_certs)
    results = {"A": [], "B": [], "C": []}

    for bucket, items in BUCKETS.items():
        for cn, pdf in items:
            ex = run_one(cn, pdf)
            if ex is None:
                continue
            row = db.get(cn, {})
            rec = {"cert_number": cn, "pdf": Path(pdf).name, "db": row, "extend": ex}
            results[bucket].append(rec)

    # ===== Bucket A: sym vs asym classification =====
    print("\n=== BUCKET A — sym_slu certs: actual sym vs asym ===")
    print(f"{'cert':<7} {'DB sym_slu':>10} {'ex spin_type':<13} "
          f"{'ex SymSLU':>9} {'ex SymSLE':>9} {'ex AsymSLU':>10} "
          f"{'ex AsymSLE':>10} {'classification'}")
    a_actual_asym = 0
    a_actual_sym = 0
    a_ambig = 0
    for r in results["A"]:
        ex = r["extend"]
        db = r["db"]
        sym_slu_ex = to_float(ex.get("SymSLU"))
        sym_sle_ex = to_float(ex.get("SymSLE"))
        asym_slu_ex = to_float(ex.get("AsymSLU"))
        asym_sle_ex = to_float(ex.get("AsymSLE"))
        spin_type = unwrap_value(ex.get("spinnaker_type"))
        # Classify
        is_asym = False
        is_sym = False
        if isinstance(spin_type, str):
            st = spin_type.lower()
            if "asym" in st:
                is_asym = True
            elif "sym" in st:
                is_sym = True
        # fallback: if AsymSLU populated and SymSLU empty → asym
        if not is_asym and not is_sym:
            if asym_slu_ex and not sym_slu_ex:
                is_asym = True
            elif sym_slu_ex and not asym_slu_ex:
                is_sym = True
        cls = "ASYM" if is_asym else ("SYM" if is_sym else "AMBIG")
        if cls == "ASYM":
            a_actual_asym += 1
        elif cls == "SYM":
            a_actual_sym += 1
        else:
            a_ambig += 1
        print(f"{r['cert_number']:<7} {str(db.get('sym_slu')):>10} "
              f"{str(spin_type):<13} {str(sym_slu_ex):>9} "
              f"{str(sym_sle_ex):>9} {str(asym_slu_ex):>10} "
              f"{str(asym_sle_ex):>10} {cls}")
    n_a = len(results["A"])
    print(f"\nBucket A totals: N={n_a}  asym={a_actual_asym}  sym={a_actual_sym}  ambig={a_ambig}")
    if n_a:
        print(f"  % mis-classified (actually asym): {a_actual_asym/n_a*100:.1f}%")

    # ===== Bucket B: SER / nodisp =====
    print("\n=== BUCKET B — nodisp certs: does PDF have hull data? ===")
    print(f"{'cert':<7} {'DB disp':>8} {'DB draft':>9} {'DB beam':>8} "
          f"{'ex disp':>9} {'ex draft':>9} {'ex beam':>8} {'is_SER':>7}  recoverable")
    b_disp_recoverable = 0
    b_draft_recoverable = 0
    b_beam_recoverable = 0
    b_ser = 0
    for r in results["B"]:
        ex = r["extend"]
        db = r["db"]
        disp_ex = to_float(ex.get("displacement"))
        draft_ex = to_float(ex.get("draft"))
        beam_ex = to_float(ex.get("beam"))
        is_ser = unwrap_value(ex.get("is_SER"))
        disp_db = db.get("displacement")
        draft_db = db.get("draft")
        beam_db = db.get("beam")
        recov = []
        if disp_db is None and disp_ex is not None:
            b_disp_recoverable += 1
            recov.append("disp")
        if draft_db is None and draft_ex is not None:
            b_draft_recoverable += 1
            recov.append("draft")
        if beam_db is None and beam_ex is not None:
            b_beam_recoverable += 1
            recov.append("beam")
        if is_ser:
            b_ser += 1
        print(f"{r['cert_number']:<7} {str(disp_db):>8} {str(draft_db):>9} "
              f"{str(beam_db):>8} {str(disp_ex):>9} {str(draft_ex):>9} "
              f"{str(beam_ex):>8} {str(is_ser):>7}  {','.join(recov)}")
    n_b = len(results["B"])
    print(f"\nBucket B totals: N={n_b}  is_SER={b_ser}  "
          f"disp_recov={b_disp_recoverable}  draft_recov={b_draft_recoverable}  "
          f"beam_recov={b_beam_recoverable}")

    # ===== Bucket C: FL prevalence =====
    print("\n=== BUCKET C — FL field prevalence ===")
    print(f"{'cert':<7} {'DB fl':>7} {'ex FL':>7}  classification")
    c_has_fl = 0
    for r in results["C"]:
        ex = r["extend"]
        db = r["db"]
        fl_ex = to_float(ex.get("FL"))
        fl_db = db.get("fl")
        if fl_ex is not None:
            c_has_fl += 1
            cls = "PDF_HAS_FL" if fl_db is None else "BOTH_HAVE"
        else:
            cls = "PDF_NO_FL"
        print(f"{r['cert_number']:<7} {str(fl_db):>7} {str(fl_ex):>7}  {cls}")
    n_c = len(results["C"])
    print(f"\nBucket C totals: N={n_c}  has_FL={c_has_fl}  pct={c_has_fl/n_c*100:.1f}%" if n_c else "")

    # Final dump
    (OUT_DIR / "gap_audit_results.json").write_text(
        json.dumps(results, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
