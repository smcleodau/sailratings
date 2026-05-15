"""Boat identity resolution — match ORC certificates to IRC boats.

Matching strategy (in priority order):
1. Exact normalized sail number match (strip spaces, dashes, case-insensitive)
2. Sail number match + name match (for disambiguating short numeric sail numbers)
3. Country-scoped name match + same design class (for boats with different sail formats)

Short numeric sail numbers (< 5 chars) are ambiguous across countries, so they
require additional signals (name match or country match) to avoid false positives.
"""

import re
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine


def normalize_sail(sail_no: str | None) -> str:
    """Normalize a sail number for matching: uppercase, strip spaces/dashes/dots."""
    if not sail_no:
        return ""
    return re.sub(r"[\s\-\./]+", "", sail_no.strip().upper())


def normalize_sail_tokens(sail_no: str | None) -> set[str]:
    """Return the set of plausible tokens a sail string identifies.

    Handles scraper concatenations (`2561&011&192561`), comma lists, and
    class-prefix variants (`EAUS1213` → `{EAUS1213, AUS1213, 1213}`).
    Two boats whose token sets intersect are sail-identifier matches.
    """
    if not sail_no:
        return set()
    tokens: set[str] = set()
    for chunk in re.split(r"[&,;|]", sail_no):
        chunk = chunk.strip()
        if not chunk:
            continue
        for cand in normalize_sailsys_sail(chunk):
            if cand:
                tokens.add(cand)
        norm = normalize_sail(chunk)
        if norm:
            tokens.add(norm)
    return tokens


# SailSys class-area prefixes: class letter(s) + country code + number.
# E.g. EAUS1343 = Etchells, Australia, #1343.
# Ordered longest-first so EAUS matches before E.
_SAILSYS_CLASS_PREFIXES = [
    "EAUS", "YAUS", "JAUS", "MAUS", "DAUS", "VAUS",
    "ENGR",  # Etchells Great Britain
    "RPAYC", "RSYS", "CYCA", "MHYC",  # club boat prefixes
    "SYD", "HYC", "MH", "SM", "FB", "YC",  # other club codes
    "PACER", "Pacer",
    "E", "Y", "J", "M", "D", "V",
    "A", "B", "C", "G", "R", "Z",
]


def normalize_sailsys_sail(sail_no: str | None) -> list[str]:
    """Generate candidate normalized sail numbers from a SailSys sail number.

    SailSys uses class-area prefixes (EAUS1343, YAUS60) that IRC doesn't have.
    Returns a list of candidate sail numbers to try matching against IRC boats,
    ordered by confidence:
    1. The raw normalized sail (in case it matches directly)
    2. AUS + numeric suffix (most SailSys boats are Australian)
    3. Bare numeric suffix
    """
    if not sail_no:
        return []

    raw = sail_no.strip().upper()
    norm = re.sub(r"[\s\-\./]+", "", raw)
    candidates = [norm]  # always try the raw normalized form first

    # Try stripping each known prefix (longest-first)
    bare_number = None
    for prefix in _SAILSYS_CLASS_PREFIXES:
        p = prefix.upper()
        if norm.startswith(p) and len(norm) > len(p):
            remainder = norm[len(p):]
            if remainder and remainder[0].isdigit():
                bare_number = remainder
                break

    if bare_number:
        # Try AUS + number (since these are Australian clubs)
        aus_sail = f"AUS{bare_number}"
        if aus_sail != norm:
            candidates.append(aus_sail)
        # Try bare number
        if bare_number != norm:
            candidates.append(bare_number)

    return candidates


def normalize_name(name: str | None) -> str:
    """Normalize a boat name for matching: uppercase, strip suffixes like '- SEC'."""
    if not name:
        return ""
    name = name.strip().upper()
    # Remove common ORC/IRC suffixes
    name = re.sub(r"\s*-\s*SEC$", "", name)
    # Remove trailing whitespace/special chars
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _is_ambiguous_sail(sail: str) -> bool:
    """Short numeric-only sails are ambiguous across countries."""
    clean = normalize_sail(sail)
    return len(clean) <= 4 and clean.isdigit()


def _orc_country_to_irc_country(orc_country_id: str) -> str | None:
    """Map ORC country code to IRC country code (they mostly match)."""
    # Most are the same. A few differ:
    mapping = {
        "SLO": "SLO",
        "KOR": "KOR",
        "MU_": None,  # Multihull office, no IRC equivalent
    }
    return mapping.get(orc_country_id, orc_country_id)


def match_orc_to_irc(engine: Engine, dry_run: bool = False) -> dict:
    """Match ORC certificates to existing IRC boats.

    Returns stats dict with match counts.
    """
    stats = {
        "total_orc": 0,
        "already_matched": 0,
        "matched_sail_exact": 0,
        "matched_sail_name": 0,
        "matched_name_country": 0,
        "ambiguous_skipped": 0,
        "unmatched": 0,
    }

    with engine.begin() as conn:
        # Get all unmatched ORC certificates
        orc_certs = conn.execute(text("""
            SELECT id, sail_no, yacht_name, country_id, class_name
            FROM orc_certificates
            WHERE boat_id IS NULL
            ORDER BY country_id, yacht_name
        """)).fetchall()

        stats["total_orc"] = len(orc_certs)

        # Check already matched
        already = conn.execute(text(
            "SELECT COUNT(*) FROM orc_certificates WHERE boat_id IS NOT NULL"
        )).scalar()
        stats["already_matched"] = already

        # Build IRC boat lookup by normalized sail number
        irc_boats = conn.execute(text("""
            SELECT id, sail_number, boat_name, country
            FROM boats
        """)).fetchall()

        # Index by normalized sail number
        sail_index: dict[str, list[dict]] = {}
        for boat in irc_boats:
            norm = normalize_sail(boat.sail_number)
            if norm:
                sail_index.setdefault(norm, []).append({
                    "id": boat.id,
                    "sail_number": boat.sail_number,
                    "boat_name": boat.boat_name,
                    "country": boat.country,
                })

        # Also index by normalized name + country for fallback matching
        name_country_index: dict[str, list[dict]] = {}
        for boat in irc_boats:
            norm_name = normalize_name(boat.boat_name)
            if norm_name and boat.country:
                key = f"{norm_name}|{boat.country}"
                name_country_index.setdefault(key, []).append({
                    "id": boat.id,
                    "sail_number": boat.sail_number,
                    "boat_name": boat.boat_name,
                    "country": boat.country,
                })

        # Match each ORC cert
        updates = []
        for orc in orc_certs:
            orc_sail_norm = normalize_sail(orc.sail_no)
            orc_name_norm = normalize_name(orc.yacht_name)
            irc_country = _orc_country_to_irc_country(orc.country_id)
            matched_boat_id = None

            # Strategy 1: Exact sail number match
            candidates = sail_index.get(orc_sail_norm, [])

            if len(candidates) == 1:
                # Unique match — but if sail is ambiguous, verify name too
                if _is_ambiguous_sail(orc.sail_no):
                    cand_name = normalize_name(candidates[0]["boat_name"])
                    if cand_name == orc_name_norm or not orc_name_norm:
                        matched_boat_id = candidates[0]["id"]
                        stats["matched_sail_name"] += 1
                    else:
                        stats["ambiguous_skipped"] += 1
                        continue
                else:
                    matched_boat_id = candidates[0]["id"]
                    stats["matched_sail_exact"] += 1

            elif len(candidates) > 1:
                # Multiple boats with same sail number — disambiguate by name
                for cand in candidates:
                    if normalize_name(cand["boat_name"]) == orc_name_norm:
                        matched_boat_id = cand["id"]
                        stats["matched_sail_name"] += 1
                        break
                if not matched_boat_id:
                    # Try country match
                    for cand in candidates:
                        if cand["country"] == irc_country:
                            matched_boat_id = cand["id"]
                            stats["matched_sail_name"] += 1
                            break

            # Strategy 2: Name + country fallback (only for non-ambiguous names)
            if not matched_boat_id and orc_name_norm and irc_country:
                key = f"{orc_name_norm}|{irc_country}"
                name_candidates = name_country_index.get(key, [])
                if len(name_candidates) == 1:
                    matched_boat_id = name_candidates[0]["id"]
                    stats["matched_name_country"] += 1

            if matched_boat_id:
                updates.append((matched_boat_id, orc.id))
            else:
                stats["unmatched"] += 1

        # Apply updates
        if not dry_run and updates:
            for boat_id, orc_id in updates:
                conn.execute(
                    text("UPDATE orc_certificates SET boat_id = :boat_id WHERE id = :orc_id"),
                    {"boat_id": boat_id, "orc_id": orc_id},
                )

    stats["matched_total"] = stats["matched_sail_exact"] + stats["matched_sail_name"] + stats["matched_name_country"]
    return stats


def record_identities_from_orc(engine: Engine) -> int:
    """Record boat identities from matched ORC certificates into boat_identities.

    For each matched ORC cert, record the name/sail/owner/country as an identity
    observation. Returns count of identities created.
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO boat_identities (boat_id, boat_name, sail_number, owner, flag, source, observed_date)
            SELECT DISTINCT ON (oc.boat_id, oc.yacht_name, oc.sail_no)
                oc.boat_id,
                oc.yacht_name,
                oc.sail_no,
                oc.owner_name,
                oc.country_id,
                'orc_certificate',
                oc.snapshot_date
            FROM orc_certificates oc
            WHERE oc.boat_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM boat_identities bi
                  WHERE bi.boat_id = oc.boat_id
                    AND bi.source = 'orc_certificate'
                    AND bi.sail_number = oc.sail_no
              )
            ON CONFLICT DO NOTHING
        """))
        return result.rowcount


def record_identities_from_irc(engine: Engine) -> int:
    """Record boat identities from IRC data (boats table) into boat_identities.

    Creates one identity per boat from the current IRC data. Returns count.
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO boat_identities (boat_id, boat_name, sail_number, flag, source, observed_date)
            SELECT b.id, b.boat_name, b.sail_number, b.country, 'irc_tcc',
                   (SELECT MAX(snapshot_date) FROM tcc_snapshots WHERE boat_id = b.id)
            FROM boats b
            WHERE NOT EXISTS (
                SELECT 1 FROM boat_identities bi
                WHERE bi.boat_id = b.id
                  AND bi.source = 'irc_tcc'
            )
        """))
        return result.rowcount


def backfill_boat_details_from_orc(engine: Engine) -> int:
    """Backfill boats table with builder/designer/year/design from matched ORC certs.

    Only fills in NULL fields — doesn't overwrite existing data. Returns count updated.
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE boats b
            SET
                builder = COALESCE(b.builder, oc.builder),
                designer = COALESCE(b.designer, oc.designer),
                design = COALESCE(b.design, oc.class_name),
                year_built = COALESCE(b.year_built, oc.year_built),
                loa = COALESCE(b.loa, oc.loa),
                displacement_kg = COALESCE(b.displacement_kg, oc.displacement),
                updated_at = now()
            FROM (
                SELECT DISTINCT ON (boat_id)
                    boat_id, builder, designer, class_name, year_built, loa, displacement
                FROM orc_certificates
                WHERE boat_id IS NOT NULL
                ORDER BY boat_id, snapshot_date DESC
            ) oc
            WHERE b.id = oc.boat_id
              AND (b.builder IS NULL OR b.designer IS NULL OR b.year_built IS NULL
                   OR b.loa IS NULL OR b.displacement_kg IS NULL OR b.design IS NULL)
        """))
        return result.rowcount


def backfill_design_from_irc_certs(engine: Engine) -> int:
    """Backfill boats.design from IRC certificate PDF raw data.

    Extracts design_raw from certificates.raw_data JSONB for matched boats
    where boats.design is NULL. Returns count updated.
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE boats b
            SET
                design = c.design_raw,
                updated_at = now()
            FROM (
                SELECT DISTINCT ON (boat_id)
                    boat_id,
                    raw_data->>'design_raw' AS design_raw
                FROM certificates
                WHERE boat_id IS NOT NULL
                  AND raw_data->>'design_raw' IS NOT NULL
                  AND TRIM(raw_data->>'design_raw') != ''
                ORDER BY boat_id, issue_date DESC NULLS LAST
            ) c
            WHERE b.id = c.boat_id
              AND b.design IS NULL
        """))
        return result.rowcount


def backfill_design_from_sailsys(engine: Engine) -> int:
    """Backfill boats.design from SailSys race result raw data.

    Concatenates boat_make + boat_model from race_results.raw_data for matched boats
    where boats.design is NULL. Returns count updated.
    """
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE boats b
            SET
                design = ss.design,
                updated_at = now()
            FROM (
                SELECT DISTINCT ON (boat_id)
                    boat_id,
                    TRIM(
                        COALESCE(raw_data->>'boat_make', '') || ' ' ||
                        COALESCE(raw_data->>'boat_model', '')
                    ) AS design
                FROM race_results
                WHERE boat_id IS NOT NULL
                  AND source = 'sailsys'
                  AND (raw_data->>'boat_make' IS NOT NULL
                       OR raw_data->>'boat_model' IS NOT NULL)
                ORDER BY boat_id, event_date DESC NULLS LAST
            ) ss
            WHERE b.id = ss.boat_id
              AND b.design IS NULL
              AND TRIM(ss.design) != ''
        """))
        return result.rowcount
