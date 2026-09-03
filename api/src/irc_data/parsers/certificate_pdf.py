"""IRC certificate PDF parser using pdfplumber.

IRC Boat Data PDFs have a consistent layout:
- Header: boat name, sail number, design, cert number, dates, crew
- Hull section: LH, LWP, Boat Weight, DLR, Draft
- Rig/sails: P, E, J, STL, HLP, spreaders
- Mainsail widths: MUW, MTW, MHW
- Headsail: HLU/HLUmax, HUW, HTW, HHW, HSA
- Spinnaker: SPA, SLU, SLE, SHW, SFL
- Overhangs: BO, SO
- Water ballast: WB (if present)
- Sail counts: headsails, flying headsails, spinnakers

Values are positioned around a boat profile drawing, not in clean tables.
We extract by matching label+value patterns from the word stream.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import unquote

import pdfplumber

from irc_data.parsers.schemas import CertificateData


def _dec(val: str | None) -> Decimal | None:
    if not val:
        return None
    val = val.strip().replace(",", ".")
    val = re.sub(r"[^\d.\-]", "", val)
    if not val:
        return None
    try:
        return Decimal(val)
    except (InvalidOperation, ValueError):
        return None


def _extract_lines(page) -> list[str]:
    """Extract text as lines from a PDF page, sorted by position."""
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3, y_tolerance=3)
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (round(w["top"], 0), w["x0"]))

    lines = []
    current_y = None
    current_line: list[str] = []

    for w in words_sorted:
        y = round(w["top"], 0)
        if current_y is None or abs(y - current_y) > 5:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = []
            current_y = y
        current_line.append(w["text"])

    if current_line:
        lines.append(" ".join(current_line))

    return lines


def _search_value(text: str, pattern: str) -> str | None:
    """Search for a pattern and return the first capture group."""
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else None


# ---------------------------------------------------------------------------
# Spinnaker classification (OPS-02-13)
# ---------------------------------------------------------------------------
#
# IRC certificates print a single spinnaker block labelled with the generic
# codes SLU / SLE / SHW / SFL regardless of whether the sail is symmetric or
# asymmetric.  The two types are distinguished by geometry, which gives a
# deterministic discriminant:
#
#   * symmetric spinnaker  — both luffs are the same length:  SLU == SLE
#   * asymmetric spinnaker — the luff is longer than the leech:  SLU > SLE
#
# The legacy parser stored every spinnaker block into the ``sym_*`` columns;
# the extend.ai audit (OPS-02-01) showed 1,631 of 3,503 spinnaker certs were
# actually asymmetric (see the ``cert_sym_asym_reclassify`` audit table).
# Re-applying this rule to the DB agrees with the extend.ai ground truth on
# all 20 stratified sample certs of audit bucket A.
_ASYM_TIE_MARGIN = Decimal("0.005")  # half the 0.01 m print resolution


def classify_spinnaker(
    slu: Decimal | None,
    sle: Decimal | None,
    shw: Decimal | None,
    sfl: Decimal | None,
) -> tuple[str | None, dict[str, Decimal | None]]:
    """Classify a spinnaker measurement block as symmetric or asymmetric.

    Returns ``(kind, values)`` where ``kind`` is ``"sym"``, ``"asym"`` or
    ``None`` (no spinnaker data at all) and ``values`` has the keys
    ``slu``/``sle``/``sf``/``shw`` ready to be splatted onto the matching
    ``sym_*`` / ``asym_*`` certificate fields.  The certificate's printed
    foot length (``SFL``) is exposed as the generic ``sf`` measurement,
    mirroring the legacy mapping ``sym_sf = SFL``.
    """
    if slu is None and sle is None and shw is None and sfl is None:
        return None, {"slu": None, "sle": None, "sf": None, "shw": None}

    if slu is not None and sle is not None:
        kind = "asym" if (slu - sle) > _ASYM_TIE_MARGIN else "sym"
    else:
        # Only part of the block parsed — keep the historic default so we
        # never drop data, and so pre-2026 behaviour is preserved.
        kind = "sym"
    return kind, {"slu": slu, "sle": sle, "sf": sfl, "shw": shw}


def parse_certificate_pdf(pdf_path: Path) -> CertificateData:
    """Parse an IRC certificate PDF into structured data."""
    with pdfplumber.open(pdf_path) as pdf:
        all_lines = []
        for page in pdf.pages:
            all_lines.extend(_extract_lines(page))

    full_text = "\n".join(all_lines)

    # Split into header (notes) and measurement sections.
    # The measurement section starts at "HULL" line. Notes before that can
    # contain measurement dates like "LH 6/25, E 12/25" which would confuse
    # the regex patterns, so we extract measurements only from the body.
    hull_idx = full_text.find("HULL")
    measurement_text = full_text[hull_idx:] if hull_idx >= 0 else full_text

    # --- Header extraction (EN, FR, ES, IT variants) ---
    name = _search_value(full_text, r"Name:\s*(.+?)(?:\n|$)")
    if not name:
        name = _search_value(full_text, r"Nom:\s*(.+?)(?:\n|$)")  # French
    if not name:
        name = _search_value(full_text, r"Nombre:\s*(.+?)(?:\n|$)")  # Spanish
    if not name:
        name = _search_value(full_text, r"Nome:\s*(.+?)(?:\n|$)")  # Italian
    sail_number = _search_value(full_text, r"Sail Number:\s*(\S+)")
    if not sail_number:
        sail_number = _search_value(full_text, r"No\. de voile:\s*(\S+)")  # French
    if not sail_number:
        sail_number = _search_value(full_text, r"N° de vela:\s*(\S+)")  # Spanish
    if not sail_number:
        sail_number = _search_value(full_text, r"N° Velico:\s*(\S+)")  # Italian
    design_raw = _search_value(full_text, r"Design:\s*(.+?)(?:\n|$)")
    if not design_raw:
        design_raw = _search_value(full_text, r"Type\s+(.+?)(?:CERTIFICAT|$)")  # French
    if not design_raw:
        design_raw = _search_value(full_text, r"Tipo:\s*(.+?)(?:CERTIFICADO|$)")  # Spanish
    if not design_raw:
        design_raw = _search_value(full_text, r"Typo:\s*(.+?)(?:CERTIFICATO|$)")  # Italian
    cert_number = _search_value(full_text, r"Cert No\.?:\s*(\d+)")
    if not cert_number:
        cert_number = _search_value(full_text, r"N° de Cert\.?:\s*(\d+)")  # French
    if not cert_number:
        cert_number = _search_value(full_text, r"No\. de Cert\.?:\s*(\d+)")  # Spanish
    if not cert_number:
        cert_number = _search_value(full_text, r"N° di Cert\.?:\s*(\d+)")  # Italian
    crew_str = _search_value(full_text, r"Crew No\.?:\s*(\d+)")
    if not crew_str:
        crew_str = _search_value(full_text, r"Equipiers:\s*(\d+)")  # French
    if not crew_str:
        crew_str = _search_value(full_text, r"No\. de tripulantes:\s*(\d+)")  # Spanish
    if not crew_str:
        crew_str = _search_value(full_text, r"N° di Equipaggio:\s*(\d+)")  # Italian

    # Parse expiry date to derive certificate year.
    # The cert year = expiry - 1 year (indicates which IRC algorithm version).
    # EN: "Expires: 31 Dec 26", FR: "Au: 31 Dec 25",
    # IT: "Al: 31 Dec 25", ES: "Hasta el: 31 Dec 26"
    expires_str = _search_value(full_text, r"Expires:\s*(.+?)(?:\n|$)")
    if not expires_str:
        expires_str = _search_value(full_text, r"\bAu:\s*(\d.+?)(?:\n|$)")
    if not expires_str:
        expires_str = _search_value(full_text, r"\bAl:\s*(\d.+?)(?:\n|$)")
    if not expires_str:
        expires_str = _search_value(full_text, r"Hasta el:\s*(.+?)(?:\n|$)")
    issue_date = None
    if expires_str:
        french_months = {
            "Fév": "Feb", "Avr": "Apr", "Mai": "May", "Juin": "Jun",
            "Juil": "Jul", "Août": "Aug", "Déc": "Dec"
        }
        expires_normalized = expires_str.strip()
        for fr, en in french_months.items():
            expires_normalized = expires_normalized.replace(fr, en)
        expiry_date = None
        for fmt in ["%d %b %y", "%d %b %Y", "%d %b %y %H:%M:%S", "%d %b %Y %H:%M:%S"]:
            try:
                expiry_date = datetime.strptime(expires_normalized, fmt).date()
                break
            except ValueError:
                continue
        if expiry_date:
            # Certificate year = expiry minus 1 year
            issue_date = expiry_date.replace(year=expiry_date.year - 1)

    # Parse design name — may include draft variant and WB flag
    # e.g. "SUN FAST 3300 1.95 WB" or "J/99"
    design = None
    water_ballast_flag = False
    if design_raw:
        design = design_raw.strip()
        if "WB" in design.upper():
            water_ballast_flag = True
            design = re.sub(r"\s*WB\s*", " ", design, flags=re.IGNORECASE).strip()
        # Remove trailing draft number from design if present
        design = re.sub(r"\s+\d+\.\d+\s*$", "", design).strip()

    # --- Hull measurements (from measurement section only) ---
    # Require decimal point in values to avoid matching dates like "6/25"
    lh = _dec(_search_value(measurement_text, r"LH\s+(\d+\.\d+)"))
    # LWP is normally printed with decimals ("LWP 9.41") but some certs print
    # an integer ("LWP 10"); allow both (OPS-02-13, cert_reparse_lwp_dlr).
    lwp = _dec(_search_value(measurement_text, r"LWP\s+(\d+(?:\.\d+)?)"))
    # Displacement label varies by language:
    #   EN "Boat Weight: 1234"; FR "Poids: 1234"; ES/IT "Peso: 1234"; DE "Gewicht: 1234".
    # Allow optional comma thousands separator and optional kg/kgs unit suffix so we
    # cover variants like "Boat Weight: 1,234 kg". Require a colon so we don't match
    # section headers like "Poids & Elancements" or "Peso con cuscini e batterie".
    displacement = _dec(
        _search_value(
            measurement_text,
            r"(?:Boat\s*Weight|Poids|Peso|Gewicht)\s*:\s*([\d,]+)(?:\s*kgs?)?",
        )
    )
    # DLR is usually an integer but some certs print decimals ("DLR 150.5").
    dlr = _dec(_search_value(measurement_text, r"DLR\s+(\d+(?:\.\d+)?)"))
    # Draft label varies by language:
    #   EN "Draft: 1.23"; FR "Tirant d'eau : 1.23"; ES "Calado : 1.23"; IT "Pescaggio : 1.23";
    #   DE "Tiefgang : 1.23"; British "Draught : 1.23". Colon may be preceded by a space.
    # Some certs print integer drafts (e.g. "Draft: 2"), so allow optional decimal.
    draft = _dec(
        _search_value(
            measurement_text,
            r"(?:Draft|Draught|Tirant\s*d['’]eau|Calado|Pescaggio|Tiefgang)\s*:\s*(\d+(?:\.\d+)?)",
        )
    )

    # --- Rig measurements ---
    p = _dec(_search_value(measurement_text, r"\bP\s+(\d+\.\d+)"))
    e = _dec(_search_value(measurement_text, r"\bE\s+(\d+\.\d+)"))
    j = _dec(_search_value(measurement_text, r"\bJ\s+(\d+\.\d+)"))
    stl = _dec(_search_value(measurement_text, r"STL\s+(\d+\.\d+)"))
    hlp = _dec(_search_value(measurement_text, r"HLP\s+(\d+\.\d+)"))

    # Spreaders
    spreaders_str = _search_value(measurement_text, r"(\d+)\s+Spreader")
    spreaders = int(spreaders_str) if spreaders_str else None

    # --- Mainsail widths ---
    muw = _dec(_search_value(measurement_text, r"MUW\s+(\d+\.\d+)"))
    mtw = _dec(_search_value(measurement_text, r"MTW\s+(\d+\.\d+)"))
    mhw = _dec(_search_value(measurement_text, r"MHW\s+(\d+\.\d+)"))

    # --- Headsail ---
    hlu_raw = _search_value(measurement_text, r"HLU(?:/HLUmax)?\s+(\d+\.\d+)")
    hlu = _dec(hlu_raw)
    huw = _dec(_search_value(measurement_text, r"HUW\s+(\d+\.\d+)"))
    htw = _dec(_search_value(measurement_text, r"HTW\s+(\d+\.\d+)"))
    hhw = _dec(_search_value(measurement_text, r"HHW\s+(\d+\.\d+)"))
    hsa_str = _search_value(measurement_text, r"HSA\s+(\d+\.\d+)")
    hsa = _dec(hsa_str)

    # --- Spinnaker ---
    spa_str = _search_value(measurement_text, r"SPA\s+(\d+\.\d+)")
    spa = _dec(spa_str)
    slu = _dec(_search_value(measurement_text, r"SLU\s+(\d+\.\d+)"))
    sle = _dec(_search_value(measurement_text, r"SLE\s+(\d+\.\d+)"))
    shw = _dec(_search_value(measurement_text, r"SHW\s+(\d+\.\d+)"))
    sfl = _dec(_search_value(measurement_text, r"SFL\s+(\d+\.\d+)"))
    # OPS-02-13: certs print one spinnaker block for both sym and asym sails;
    # classify by geometry (SLU vs SLE) so asymmetric kites land in the
    # ``asym_*`` fields instead of being misclassified as symmetric.
    spinnaker_kind, spin = classify_spinnaker(slu, sle, shw, sfl)

    # --- Overhangs ---
    bo = _dec(_search_value(measurement_text, r"BO\s+(\d+\.\d+)"))
    so = _dec(_search_value(measurement_text, r"SO\s+(\d+\.\d+)"))

    # --- Water ballast ---
    water_ballast = None
    wb_str = _search_value(measurement_text, r"WB\s+(\d+)(?:\.\d+)?\s*L")
    if wb_str:
        water_ballast = _dec(wb_str)
    elif water_ballast_flag:
        water_ballast = Decimal("0")  # Flag present but no amount specified

    # --- Spinnaker pole ---
    spl = _dec(_search_value(measurement_text, r"SPL\s+(\d+\.\d+)"))
    stl_fh_max = _dec(_search_value(measurement_text, r"STLFHmax\s+(\d+\.\d+)"))

    # --- Hull overhang x, y from SO line: "SO 0.87, y 0.08 x 0.00, h 0.00" ---
    x_val = _dec(_search_value(measurement_text, r"\bx\s+(\d+\.\d+)"))
    y_val = _dec(_search_value(measurement_text, r",\s*y\s+(\d+\.\d+)"))

    # --- Internal ballast: "IB 900kg" ---
    internal_ballast = _dec(_search_value(measurement_text, r"IB\s+(\d+)\s*kg"))

    # --- Flying headsail dimensions ---
    flu = _dec(_search_value(measurement_text, r"FLU\s+(\d+\.\d+)"))
    fuw = _dec(_search_value(measurement_text, r"FUW\s+(\d+\.\d+)"))
    ftw = _dec(_search_value(measurement_text, r"FTW\s+(\d+\.\d+)"))
    fhw = _dec(_search_value(measurement_text, r"FHW\s+(\d+\.\d+)"))
    fshw = _dec(_search_value(measurement_text, r"FSHW\s+(\d+\.\d+)"))
    flp = _dec(_search_value(measurement_text, r"FLP\s+(\d+\.\d+)"))
    fsa = _dec(_search_value(measurement_text, r"FSA\s+(\d+[\.\d]*)\s*m"))
    fsfl = _dec(_search_value(measurement_text, r"FSFL\s+(\d+\.\d+)"))

    # --- Sail counts ---
    headsails_str = _search_value(measurement_text, r"headsails carried:\s*(\d+)")
    flying_str = _search_value(measurement_text, r"flying headsails carried:\s*(\d+)")
    spinnakers_str = _search_value(measurement_text, r"spinnakers carried:\s*(\d+)")
    headsails_max = int(headsails_str) if headsails_str else None
    flying_headsails_max = int(flying_str) if flying_str else None
    spinnakers_max = int(spinnakers_str) if spinnakers_str else None

    # --- Aft rigging: number appears alone on the line after "Aft" ---
    # Relaxed 2-line fallback removed — it captures E/HLP/SPL values as false positives
    aft_match = re.search(r"Aft\b[^\n]*\n(\d+)(?:\s|$)", measurement_text)
    aft_rigging = int(aft_match.group(1)) if aft_match else None

    # --- FL (headsail luff perpendicular) ---
    # IRC certificates never print a literal "FL" label — the headsail luff
    # perpendicular is printed as "HLP" (extend.ai audit bucket C: 0/10
    # sampled certs print FL, all print HLP).  Populate ``fl`` from HLP so
    # downstream consumers (regression/optimizer "simulator") read a filled
    # field instead of NULL (OPS-02-13).
    fl = hlp

    # --- DLR as integer ---
    dlr_int = int(dlr) if dlr else None

    # Determine rig type from notes
    rig_type = None
    mast_material = None
    if re.search(r"carbon", full_text, re.IGNORECASE):
        mast_material = "carbon"
    if re.search(r"alumin", full_text, re.IGNORECASE):
        mast_material = "aluminium"

    # Build raw_data with everything for debugging
    raw_data = {
        "design_raw": design_raw,
        "name": name,
        "sail_number": sail_number,
        "crew": crew_str,
        "expires": expires_str,
        "lwp": str(lwp) if lwp else None,
        "dlr": str(dlr) if dlr else None,
        "hsa": str(hsa) if hsa else None,
        "spa": str(spa) if spa else None,
        "sfl": str(sfl) if sfl else None,
        "spinnaker_kind": spinnaker_kind,
        "spl": str(spl) if spl else None,
        "x": str(x_val) if x_val is not None else None,
        "y": str(y_val) if y_val is not None else None,
        "internal_ballast": str(internal_ballast) if internal_ballast else None,
        "flu": str(flu) if flu else None,
        "fuw": str(fuw) if fuw else None,
        "ftw": str(ftw) if ftw else None,
        "fhw": str(fhw) if fhw else None,
        "fshw": str(fshw) if fshw else None,
        "flp": str(flp) if flp else None,
        "fsa": str(fsa) if fsa else None,
        "fsfl": str(fsfl) if fsfl else None,
        "stl_fh_max": str(stl_fh_max) if stl_fh_max else None,
        "aft_rigging": aft_rigging,
        "headsails": headsails_str,
        "flying_headsails": flying_str,
        "spinnakers": spinnakers_str,
        "water_ballast_flag": water_ballast_flag,
    }

    return CertificateData(
        cert_number=cert_number,
        issue_date=issue_date,
        pdf_path=str(pdf_path),
        source="ircrating.org",
        lh=lh,
        beam=None,  # Not on the Boat Data sheet (beam is only in TCC listing)
        draft=draft,
        displacement=displacement,
        bo=bo,
        so=so,
        p=p,
        e=e,
        j=j,
        fl=fl,  # Headsail luff perpendicular (printed as "HLP" on certs)
        stl=stl,
        spl=spl,
        rig_type=rig_type,
        mast_material=mast_material,
        spreaders=spreaders,
        muw=muw,
        mtw=mtw,
        mhw=mhw,
        hlu=hlu,
        hlp=hlp,
        hhw=hhw,
        htw=htw,
        huw=huw,
        sym_slu=spin["slu"] if spinnaker_kind == "sym" else None,
        sym_sle=spin["sle"] if spinnaker_kind == "sym" else None,
        sym_sf=spin["sf"] if spinnaker_kind == "sym" else None,
        sym_shw=spin["shw"] if spinnaker_kind == "sym" else None,
        asym_slu=spin["slu"] if spinnaker_kind == "asym" else None,
        asym_sle=spin["sle"] if spinnaker_kind == "asym" else None,
        asym_sf=spin["sf"] if spinnaker_kind == "asym" else None,
        asym_shw=spin["shw"] if spinnaker_kind == "asym" else None,
        water_ballast=water_ballast,
        design_category=None,
        # Extended measurements
        lwp=lwp,
        dlr=dlr_int,
        x=x_val,
        y=y_val,
        internal_ballast=internal_ballast,
        hsa=hsa,
        headsails_max=headsails_max,
        flying_headsails_max=flying_headsails_max,
        fsa=fsa,
        flu=flu,
        flp=flp,
        fuw=fuw,
        ftw=ftw,
        fhw=fhw,
        fsfl=fsfl,
        fshw=fshw,
        spa=spa,
        spinnakers_max=spinnakers_max,
        stl_fh_max=stl_fh_max,
        aft_rigging=aft_rigging,
        raw_data=raw_data,
    )


def parse_all_certificates(cert_dir: Path) -> list[CertificateData]:
    """Parse all PDF certificates in a directory."""
    results = []
    pdf_files = sorted(cert_dir.glob("*.pdf"))
    for pdf_path in pdf_files:
        try:
            data = parse_certificate_pdf(pdf_path)
            results.append(data)
        except Exception as e:
            print(f"  Failed: {pdf_path.name} — {e}")
    return results


def parse_filename_info(filename: str) -> dict:
    """Extract cert number, boat name, sail number from PDF filename.

    Format: {certno}_{boatname}_{sailno}.pdf
    """
    filename = unquote(filename)
    name = filename.removesuffix(".pdf")
    parts = name.split("_", 2)
    if len(parts) >= 3:
        return {"cert_number": parts[0], "boat_name": parts[1], "sail_number": parts[2]}
    return {"cert_number": None, "boat_name": None, "sail_number": name}
