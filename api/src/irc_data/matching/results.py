"""Re-match unmatched race_results to boats in the database.

Promoted from scripts/rematch_results.py into a proper module with a single
entry point: run_rematch(engine, dry_run=False) -> dict.

Matching strategies (in order of confidence):
1. SailSys sail_number exact match (normalized)
2. SailSys boat_name exact match (case-insensitive)
3. SailSys boat_name fuzzy match via pg_trgm similarity()
4. RORC unambiguous owner mapping (owner -> single boat_id from matched results)
5. RORC ambiguous owner + TCC disambiguation
6. RORC unique exact TCC match (single boat with that exact TCC)
7. Generic all-source matching by sail number + boat name (from raw_data)
8. Historical identity fallback: boat_identities name/sail (catches renamed
   boats and identities recorded from ORC certs but not yet in boats table)
9. Create new boat records from unmatched results with sail_number + boat_name
"""

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.matching.identity import (
    normalize_name,
    normalize_sail,
    normalize_sail_tokens,
    normalize_sailsys_sail,
)


def _build_sail_index(conn) -> dict[str, list[dict]]:
    """Build a token-keyed sail index for boats.

    Returns a dict mapping each individual normalised sail *token* (not the
    concatenated normalised string) to the list of boat dicts whose sail
    string produces that token. Two boats are sail-identifier matches when
    their token sets intersect, so this is the lookup structure used by
    :func:`_match_sailsys` and :func:`_match_generic`.

    Example: a boat with ``sail_number = "2561&011"`` is indexed under
    ``{"2561011", "2561", "011"}`` so an incoming raw sail of ``"2561"``
    looks up successfully.
    """
    boats = conn.execute(text(
        "SELECT id, sail_number, boat_name, country FROM boats"
    )).fetchall()
    index: dict[str, list[dict]] = defaultdict(list)
    for b in boats:
        tokens = normalize_sail_tokens(b[1])
        if not tokens:
            continue
        boat_rec = {
            "id": b[0], "sail_number": b[1],
            "boat_name": b[2], "country": b[3],
            "tokens": tokens,
        }
        for tok in tokens:
            index[tok].append(boat_rec)
    return index


def _lookup_by_tokens(sail_index: dict[str, list[dict]], sail_no: str | None) -> list[dict]:
    """Look up boat candidates from the token-keyed sail index.

    Computes ``normalize_sail_tokens(sail_no)`` and unions the candidate
    lists for every token, deduplicating by boat id. Returns ``[]`` when
    the input has no tokens.
    """
    tokens = normalize_sail_tokens(sail_no)
    if not tokens:
        return []
    seen_ids: set[int] = set()
    candidates: list[dict] = []
    for tok in tokens:
        for boat in sail_index.get(tok, []):
            if boat["id"] in seen_ids:
                continue
            seen_ids.add(boat["id"])
            candidates.append(boat)
    return candidates


def _build_name_index(conn) -> dict[str, list[int]]:
    boats = conn.execute(text(
        "SELECT id, boat_name FROM boats"
    )).fetchall()
    index: dict[str, list[int]] = defaultdict(list)
    for b in boats:
        norm = normalize_name(b[1])
        if norm:
            index[norm].append(b[0])
    return index


def _build_identity_indexes(conn) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Build name and sail-number indexes from boat_identities.

    Historical aliases (a boat changed name or sail number) live in
    boat_identities — both ORC certificate observations and IRC TCC
    history. If a race_result's boat_name no longer matches the current
    boats.boat_name, this lets us still resolve it.

    Returns:
        (name_to_boat_ids, sail_to_boat_ids) — values are sets of boat_id
        because a name can legitimately appear under multiple boats (we
        only use it when the set has exactly one boat_id).
    """
    name_idx: dict[str, set[int]] = defaultdict(set)
    sail_idx: dict[str, set[int]] = defaultdict(set)
    rows = conn.execute(text(
        "SELECT boat_id, boat_name, sail_number FROM boat_identities"
    )).fetchall()
    for boat_id, name, sail in rows:
        norm_name = normalize_name(name)
        if norm_name:
            name_idx[norm_name].add(boat_id)
        norm_sail = normalize_sail(sail)
        if norm_sail:
            sail_idx[norm_sail].add(boat_id)
    return name_idx, sail_idx


def _build_owner_boat_map(conn):
    rows = conn.execute(text("""
        SELECT UPPER(TRIM(raw_data->>'owner')) as owner_norm, boat_id
        FROM race_results
        WHERE boat_id IS NOT NULL AND source = 'rorc'
        AND raw_data->>'owner' IS NOT NULL AND raw_data->>'owner' != ''
    """)).fetchall()
    owner_boats: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r[0]:
            owner_boats[r[0]].add(r[1])
    unambiguous = {k: next(iter(v)) for k, v in owner_boats.items() if len(v) == 1}
    ambiguous = {k: v for k, v in owner_boats.items() if len(v) > 1}
    return unambiguous, ambiguous


def _build_tcc_indexes(conn):
    rows = conn.execute(text(
        "SELECT boat_id, tcc FROM tcc_snapshots"
    )).fetchall()
    boat_tcc: dict[int, set] = defaultdict(set)
    tcc_boat: dict = defaultdict(set)
    for r in rows:
        boat_tcc[r[0]].add(r[1])
        tcc_boat[r[1]].add(r[0])
    return boat_tcc, tcc_boat


def _extract_boat_name(raw: dict) -> str:
    """Extract boat name from raw_data, checking multiple possible field names."""
    return (
        raw.get("boat_name")
        or raw.get("yacht_name")
        or ""
    ).strip()


def _extract_sail_number(raw: dict) -> str:
    """Extract sail number from raw_data, checking multiple possible field names."""
    return (
        raw.get("sail_number")
        or raw.get("sail_no")
        or ""
    ).strip()


# ---------------------------------------------------------------------------
# Country detection from sail number prefix
# ---------------------------------------------------------------------------

# Common sail number country prefixes (longest first to avoid partial matches)
_SAIL_COUNTRY_PREFIXES = [
    ("AUS", "AUS"), ("GBR", "GBR"), ("NZL", "NZL"), ("USA", "USA"),
    ("FRA", "FRA"), ("GER", "GER"), ("IRL", "IRL"), ("NED", "NED"),
    ("ITA", "ITA"), ("ESP", "ESP"), ("BEL", "BEL"), ("SWE", "SWE"),
    ("NOR", "NOR"), ("DEN", "DEN"), ("FIN", "FIN"), ("POL", "POL"),
    ("CRO", "CRO"), ("SLO", "SLO"), ("RUS", "RUS"), ("HKG", "HKG"),
    ("JPN", "JPN"), ("KOR", "KOR"), ("SIN", "SIN"), ("RSA", "RSA"),
    ("ARG", "ARG"), ("BRA", "BRA"), ("CAN", "CAN"), ("CHI", "CHI"),
    ("POR", "POR"), ("CZE", "CZE"), ("HUN", "HUN"), ("TUR", "TUR"),
    ("ISR", "ISR"), ("GRE", "GRE"), ("SUI", "SUI"), ("MLT", "MLT"),
    ("CYP", "CYP"), ("MON", "MON"), ("LUX", "LUX"), ("EST", "EST"),
    ("LAT", "LAT"), ("LTU", "LTU"), ("ROU", "ROU"), ("BUL", "BUL"),
    ("UKR", "UKR"), ("MAS", "MAS"), ("PHI", "PHI"), ("THA", "THA"),
    # Single-letter prefixes (less common, lower confidence)
    ("D", "GER"), ("F", "FRA"), ("I", "ITA"), ("E", "ESP"),
    ("B", "BEL"), ("S", "SWE"), ("N", "NOR"), ("K", "GBR"),
    ("Z", "NZL"), ("J", "JPN"), ("H", "HKG"),
]


def _country_from_sail(sail_number: str) -> str | None:
    """Extract country code from a sail number prefix.

    E.g. 'AUS3300' -> 'AUS', 'GBR5176L' -> 'GBR', '1234' -> None.
    """
    if not sail_number:
        return None
    upper = sail_number.strip().upper()
    for prefix, country in _SAIL_COUNTRY_PREFIXES:
        if upper.startswith(prefix) and len(upper) > len(prefix):
            # Ensure the character after the prefix is not alpha (it's a number)
            rest = upper[len(prefix):]
            if rest[0].isdigit():
                return country
    return None


def _match_sailsys(conn, sail_index, name_index, identity_name_index, identity_sail_index, stats):
    rows = conn.execute(text("""
        SELECT id, raw_data, tcc_at_race, rating_value
        FROM race_results
        WHERE boat_id IS NULL AND source = 'sailsys'
    """)).fetchall()

    updates = []
    for row in rows:
        raw = row[1] or {}
        boat_name = (raw.get("boat_name") or "").strip()
        sail_no = (raw.get("sail_number") or raw.get("sail_no") or "").strip()
        hull_id = (raw.get("hull_id") or "").strip()
        tcc = row[2] or row[3]
        matched_id = None
        method = None

        # Strategy 1: sail-token intersection match (handles class prefixes,
        # `EAUS1213` ↔ `AUS1213` ↔ `1213`, and scraper-concatenated sails like
        # `2561&011`). One pass replaces the old exact / prefix / variant
        # cascade because the token index already encodes those variants.
        if sail_no and len(sail_no) > 2:
            candidates = _lookup_by_tokens(sail_index, sail_no)
            if len(candidates) == 1:
                matched_id = candidates[0]["id"]
                method = "sailsys_sail_exact"
            elif len(candidates) > 1 and boat_name:
                # Multiple boats share a token — require name confirmation
                norm_name = normalize_name(boat_name)
                for c in candidates:
                    if normalize_name(c["boat_name"]) == norm_name:
                        matched_id = c["id"]
                        method = "sailsys_sail_prefix_name"
                        break

        # Strategy 1b: hull_id as sail number (also via token intersection)
        if not matched_id and hull_id and len(hull_id) >= 3:
            candidates = _lookup_by_tokens(sail_index, hull_id)
            if len(candidates) == 1:
                matched_id = candidates[0]["id"]
                method = "sailsys_hull_id"

        # Strategy 2: exact name match
        if not matched_id and boat_name:
            norm_name = normalize_name(boat_name)
            candidates = name_index.get(norm_name, [])
            if len(candidates) == 1:
                matched_id = candidates[0]
                method = "sailsys_name_exact"
            elif len(candidates) > 1 and tcc:
                result = conn.execute(
                    text("""
                        SELECT b.id FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE UPPER(TRIM(b.boat_name)) = :name
                          AND ABS(t.tcc - :tcc) < 0.005
                        ORDER BY t.snapshot_date DESC LIMIT 1
                    """),
                    {"name": norm_name, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_tcc"

        # Strategy 3: fuzzy name match
        if not matched_id and boat_name and len(boat_name) >= 4:
            norm = normalize_name(boat_name)
            if tcc:
                result = conn.execute(
                    text("""
                        SELECT b.id, similarity(UPPER(b.boat_name), :name) as sim
                        FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE similarity(UPPER(b.boat_name), :name) > 0.7
                          AND ABS(t.tcc - :tcc) < 0.01
                        ORDER BY sim DESC LIMIT 1
                    """),
                    {"name": norm, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_fuzzy"
            else:
                result = conn.execute(
                    text("""
                        SELECT id, similarity(UPPER(boat_name), :name) as sim
                        FROM boats
                        WHERE similarity(UPPER(boat_name), :name) > 0.85
                        ORDER BY sim DESC LIMIT 1
                    """),
                    {"name": norm},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_name_fuzzy"

        # Strategy 3b: ORC cross-reference matching
        # Use ORC certificates as a bridge — if boat_name matches an ORC cert
        # that is already linked to an IRC boat, use that link.
        if not matched_id and boat_name and len(boat_name) >= 3:
            orc_name = normalize_name(boat_name)
            make = (raw.get("boat_make") or "").strip()
            model = (raw.get("boat_model") or "").strip()
            design_str = f"{make} {model}".strip()

            if design_str and len(design_str) >= 3:
                # Match by name + design class in ORC
                result = conn.execute(
                    text("""
                        SELECT DISTINCT oc.boat_id
                        FROM orc_certificates oc
                        WHERE oc.boat_id IS NOT NULL
                          AND UPPER(TRIM(oc.yacht_name)) = :name
                          AND oc.class_name ILIKE :design
                    """),
                    {"name": orc_name, "design": f"%{design_str}%"},
                )
                orc_matches = [r[0] for r in result]
                if len(orc_matches) == 1:
                    matched_id = orc_matches[0]
                    method = "sailsys_orc_name_design"
            else:
                # Match by name only in ORC (require unique match)
                result = conn.execute(
                    text("""
                        SELECT DISTINCT oc.boat_id
                        FROM orc_certificates oc
                        WHERE oc.boat_id IS NOT NULL
                          AND UPPER(TRIM(oc.yacht_name)) = :name
                    """),
                    {"name": orc_name},
                )
                orc_matches = [r[0] for r in result]
                if len(orc_matches) == 1:
                    matched_id = orc_matches[0]
                    method = "sailsys_orc_name"

        # Strategy 3c: ORC sail number bridge
        # If SailSys sail didn't match IRC directly, check if an ORC cert with
        # that sail number is already linked to an IRC boat.
        if not matched_id and sail_no and len(sail_no) > 2:
            for candidate_sail in normalize_sailsys_sail(sail_no):
                result = conn.execute(
                    text("""
                        SELECT DISTINCT oc.boat_id
                        FROM orc_certificates oc
                        WHERE oc.boat_id IS NOT NULL
                          AND UPPER(REPLACE(REPLACE(oc.sail_no, ' ', ''), '-', '')) = :sail
                    """),
                    {"sail": candidate_sail},
                )
                orc_matches = [r[0] for r in result]
                if len(orc_matches) == 1:
                    matched_id = orc_matches[0]
                    method = "sailsys_orc_sail"
                    break

        # Strategy 3d: design-class matching (boat_make + boat_model + TCC proximity)
        if not matched_id and tcc:
            make = (raw.get("boat_make") or "").strip()
            model = (raw.get("boat_model") or "").strip()
            design_str = f"{make} {model}".strip()
            if design_str and len(design_str) >= 3:
                result = conn.execute(
                    text("""
                        SELECT b.id FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE b.design ILIKE :design
                          AND ABS(t.tcc - :tcc) < 0.003
                        ORDER BY ABS(t.tcc - :tcc), t.snapshot_date DESC
                        LIMIT 1
                    """),
                    {"design": f"%{design_str}%", "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "sailsys_design_tcc"

        # Strategy 4: historical identity match via boat_identities
        # (boats sometimes change name/sail; ORC and IRC observations of
        # the same hull live here even if boats.boat_name is current).
        if not matched_id and boat_name:
            norm_name = normalize_name(boat_name)
            id_candidates = identity_name_index.get(norm_name, set())
            if len(id_candidates) == 1:
                matched_id = next(iter(id_candidates))
                method = "sailsys_identity_name"

        if not matched_id and sail_no and len(sail_no) > 2:
            for candidate_sail in normalize_sailsys_sail(sail_no):
                id_candidates = identity_sail_index.get(candidate_sail, set())
                if len(id_candidates) == 1:
                    matched_id = next(iter(id_candidates))
                    method = "sailsys_identity_sail"
                    break

        if matched_id:
            updates.append((matched_id, row[0]))
            stats[method] = stats.get(method, 0) + 1
            stats["matched"] += 1

    return updates


def _match_rorc(conn, owner_unambiguous, owner_ambiguous, boat_tcc, tcc_boat, stats):
    rows = conn.execute(text("""
        SELECT id, raw_data, rating_value
        FROM race_results
        WHERE boat_id IS NULL AND source = 'rorc'
    """)).fetchall()

    updates = []
    for row in rows:
        raw = row[1] or {}
        owner = (raw.get("owner") or "").strip().upper()
        rating_value = row[2]
        matched_id = None
        method = None

        if owner and owner in owner_unambiguous:
            matched_id = owner_unambiguous[owner]
            method = "rorc_owner_exact"

        if not matched_id and owner and owner in owner_ambiguous and rating_value:
            candidate_boat_ids = owner_ambiguous[owner]
            tcc_matches = []
            for bid in candidate_boat_ids:
                known_tccs = boat_tcc.get(bid, set())
                for known_tcc in known_tccs:
                    if abs(known_tcc - rating_value) < 0.01:
                        tcc_matches.append(bid)
                        break
            if len(tcc_matches) == 1:
                matched_id = tcc_matches[0]
                method = "rorc_owner_tcc"

        if not matched_id and rating_value:
            tcc_candidates = tcc_boat.get(rating_value, set())
            if len(tcc_candidates) == 1:
                matched_id = next(iter(tcc_candidates))
                method = "rorc_tcc_unique"

        if matched_id:
            updates.append((matched_id, row[0]))
            stats[method] = stats.get(method, 0) + 1
            stats["matched"] += 1

    return updates


def _match_generic(conn, sail_index, name_index, identity_name_index, identity_sail_index, stats):
    """Match unmatched results from ALL sources using sail number and boat name.

    This is a catch-all matcher that works for any source. It extracts
    boat_name and sail_number from raw_data (checking multiple possible
    field names) and matches against the boats table.

    Sources where this is most useful: topyacht, sailsys (second pass),
    cowesweek, sydneyhobart, rhkyc, sailracehq, isora.

    Note: Some sources (rorc, isora, cowesweek, sydneyhobart, rhkyc,
    sailracehq) do not store boat_name/sail_number in raw_data — for
    those results this matcher won't find identifiers to match on.
    The source-specific matchers handle those cases.
    """
    # Fetch ALL unmatched results regardless of source
    rows = conn.execute(text("""
        SELECT id, raw_data, tcc_at_race, rating_value, source
        FROM race_results
        WHERE boat_id IS NULL
    """)).fetchall()

    updates = []
    for row in rows:
        raw = row[1] or {}
        boat_name = _extract_boat_name(raw)
        sail_no = _extract_sail_number(raw)
        tcc = row[2] or row[3]
        source = row[4] or ""
        matched_id = None
        method = None

        # Skip if we have no identifiers at all
        if not boat_name and not sail_no:
            continue

        # Strategy G1: Sail-token intersection match
        # (handles `EAUS1213` ↔ `AUS1213`, scraper concatenations, etc.)
        if sail_no and len(sail_no) > 2:
            candidates = _lookup_by_tokens(sail_index, sail_no)
            if len(candidates) == 1:
                matched_id = candidates[0]["id"]
                method = "generic_sail_exact"
            elif len(candidates) > 1 and boat_name:
                # Multiple boats share a token — disambiguate by name
                norm_name = normalize_name(boat_name)
                for c in candidates:
                    if normalize_name(c["boat_name"]) == norm_name:
                        matched_id = c["id"]
                        method = "generic_sail_name"
                        break
                # If name didn't help, try TCC disambiguation
                if not matched_id and tcc:
                    for c in candidates:
                        # Check if any TCC snapshot is close
                        tcc_result = conn.execute(
                            text("""
                                SELECT 1 FROM tcc_snapshots
                                WHERE boat_id = :bid AND ABS(tcc - :tcc) < 0.005
                                LIMIT 1
                            """),
                            {"bid": c["id"], "tcc": tcc},
                        )
                        if tcc_result.first():
                            matched_id = c["id"]
                            method = "generic_sail_tcc"
                            break

        # Strategy G2: Exact name match (case-insensitive)
        if not matched_id and boat_name:
            norm_name = normalize_name(boat_name)
            candidates = name_index.get(norm_name, [])
            if len(candidates) == 1:
                matched_id = candidates[0]
                method = "generic_name_exact"
            elif len(candidates) > 1 and tcc:
                # Multiple boats with same name — disambiguate by TCC
                result = conn.execute(
                    text("""
                        SELECT b.id FROM boats b
                        JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE UPPER(TRIM(b.boat_name)) = :name
                          AND ABS(t.tcc - :tcc) < 0.005
                        ORDER BY t.snapshot_date DESC LIMIT 1
                    """),
                    {"name": norm_name, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "generic_name_tcc"

        # Strategy G3: Fuzzy name match (requires higher similarity threshold)
        if not matched_id and boat_name and len(boat_name) >= 4:
            norm = normalize_name(boat_name)
            if tcc:
                result = conn.execute(
                    text("""
                        SELECT b.id, similarity(UPPER(b.boat_name), :name) as sim
                        FROM boats b JOIN tcc_snapshots t ON t.boat_id = b.id
                        WHERE similarity(UPPER(b.boat_name), :name) > 0.7
                          AND ABS(t.tcc - :tcc) < 0.01
                        ORDER BY sim DESC LIMIT 1
                    """),
                    {"name": norm, "tcc": tcc},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "generic_name_fuzzy"
            else:
                result = conn.execute(
                    text("""
                        SELECT id, similarity(UPPER(boat_name), :name) as sim
                        FROM boats
                        WHERE similarity(UPPER(boat_name), :name) > 0.85
                        ORDER BY sim DESC LIMIT 1
                    """),
                    {"name": norm},
                )
                r = result.first()
                if r:
                    matched_id = r[0]
                    method = "generic_name_fuzzy"

        # Strategy G4: ORC cross-reference by name
        if not matched_id and boat_name and len(boat_name) >= 3:
            orc_name = normalize_name(boat_name)
            result = conn.execute(
                text("""
                    SELECT DISTINCT oc.boat_id
                    FROM orc_certificates oc
                    WHERE oc.boat_id IS NOT NULL
                      AND UPPER(TRIM(oc.yacht_name)) = :name
                """),
                {"name": orc_name},
            )
            orc_matches = [r[0] for r in result]
            if len(orc_matches) == 1:
                matched_id = orc_matches[0]
                method = "generic_orc_name"

        # Strategy G5: ORC cross-reference by sail number
        if not matched_id and sail_no and len(sail_no) > 2:
            norm_sail = normalize_sail(sail_no)
            result = conn.execute(
                text("""
                    SELECT DISTINCT oc.boat_id
                    FROM orc_certificates oc
                    WHERE oc.boat_id IS NOT NULL
                      AND UPPER(REPLACE(REPLACE(oc.sail_no, ' ', ''), '-', '')) = :sail
                """),
                {"sail": norm_sail},
            )
            orc_matches = [r[0] for r in result]
            if len(orc_matches) == 1:
                matched_id = orc_matches[0]
                method = "generic_orc_sail"

        # Strategy G6: historical identity name match (boat_identities)
        # Catches boats whose current boats.boat_name no longer matches
        # the result's raw_data name (e.g., renamed boats), or where the
        # identity exists in boat_identities but not boats.
        if not matched_id and boat_name:
            norm_name = normalize_name(boat_name)
            id_candidates = identity_name_index.get(norm_name, set())
            if len(id_candidates) == 1:
                matched_id = next(iter(id_candidates))
                method = "generic_identity_name"

        # Strategy G7: historical identity sail-number match
        if not matched_id and sail_no and len(sail_no) > 2:
            norm_sail = normalize_sail(sail_no)
            id_candidates = identity_sail_index.get(norm_sail, set())
            if len(id_candidates) == 1:
                matched_id = next(iter(id_candidates))
                method = "generic_identity_sail"

        if matched_id:
            updates.append((matched_id, row[0]))
            stats[method] = stats.get(method, 0) + 1
            stats["matched"] += 1

    return updates


def _create_boats_from_results(conn, engine, sail_index, stats):
    """Create new boat records from unmatched results — token-aware.

    This is the load-bearing duplicate-prevention path. SailSys/SailWave
    scrapers run on a 30-min cron and re-deliver the same hull under
    differing sail strings (``EAUS1213`` one week, ``AUS1213`` the next,
    ``2561&011`` from a concat bug). The old version of this function
    keyed groups by a single normalised string, so those variants each
    minted a fresh ``boats`` row.

    The new flow is token-set based at every stage:
      1. Group unmatched results by *token set* (boats with overlapping
         token sets are merged into one group up-front).
      2. Before grouping, check the in-memory ``sail_index`` — if any of
         the incoming tokens hits an existing boat, attribute the result
         to that boat instead of creating a new one.
      3. At write time, re-check the live ``boats`` table for any token
         overlap with an authoritative SQL query — this catches boats
         inserted between when the index was built and now (e.g., a
         concurrent scraper run).
      4. If a live overlap is found, link results to the existing boat
         and append a ``boat_identities`` row recording the new sail
         observation. Only when there is *no* live overlap do we
         actually INSERT a new boat row.

    Returns list of ``(boat_id, result_id)`` tuples and mutates
    ``sail_index`` in place with any newly created boats.
    """
    # Fetch unmatched results that have identifiers in raw_data
    rows = conn.execute(text("""
        SELECT id, raw_data, tcc_at_race, rating_value, source
        FROM race_results
        WHERE boat_id IS NULL
    """)).fetchall()

    # Group results by token set. Two results with overlapping token sets
    # go into the same group (representative key = sorted concat of the
    # merged token set). We use union-find-lite: scan groups for any
    # token intersection before creating a fresh group.
    sail_groups: list[dict] = []
    # Token -> index into sail_groups, for O(1) lookup of which group
    # a token currently belongs to.
    token_to_group: dict[str, int] = {}

    # Side-channel for attribute-to-existing matches discovered during
    # grouping (an incoming sail's token already hits a boat in the
    # pre-built sail_index — likely a boat the matchers missed because
    # of name/country sanity checks, or simply a token the matcher
    # didn't try). We still surface those as updates rather than
    # creating yet another boat.
    inline_updates: list[tuple[int, int]] = []

    for row in rows:
        raw = row[1] or {}
        boat_name = _extract_boat_name(raw)
        sail_no = _extract_sail_number(raw)

        if not boat_name or not sail_no:
            continue

        # Require sail number to have some substance (not just "1" or "AB")
        if len(sail_no.strip()) < 3:
            continue

        tokens = normalize_sail_tokens(sail_no)
        if not tokens:
            continue

        # If any token already maps to an existing boat in the index,
        # don't create a new boat — attribute the result to that boat
        # provided name doesn't look like a wholly different hull. We
        # accept the match if either no other-name candidate beats it,
        # or the incoming name matches one of the candidates exactly.
        index_hits: dict[int, dict] = {}
        for tok in tokens:
            for boat in sail_index.get(tok, []):
                index_hits[boat["id"]] = boat
        if index_hits:
            norm_name = normalize_name(boat_name)
            # Prefer same-name candidate when ambiguous.
            chosen = None
            if len(index_hits) == 1:
                chosen = next(iter(index_hits.values()))
            else:
                for boat in index_hits.values():
                    if normalize_name(boat["boat_name"]) == norm_name:
                        chosen = boat
                        break
            if chosen:
                inline_updates.append((chosen["id"], row[0]))
                continue
            # Multiple existing boats share tokens but no name match —
            # fall through to grouping (which will hit the live DB check
            # later and likely link to one of them).

        tcc = row[2] or row[3]
        source = row[4] or ""

        # Find existing groups whose tokens intersect — merge into them.
        target_idx: int | None = None
        for tok in tokens:
            if tok in token_to_group:
                target_idx = token_to_group[tok]
                break

        if target_idx is None:
            target_idx = len(sail_groups)
            sail_groups.append({
                "sail_number": sail_no.strip(),
                "boat_name": boat_name,
                "country": _country_from_sail(sail_no),
                "tcc": tcc,
                "source": source,
                "result_ids": [],
                "count": 0,
                "tokens": set(tokens),
            })

        group = sail_groups[target_idx]
        group["tokens"].update(tokens)
        for tok in tokens:
            token_to_group[tok] = target_idx
        group["result_ids"].append(row[0])
        group["count"] += 1

        # Prefer the boat_name from the result with the most data
        # (longer name is likely more accurate)
        if len(boat_name) > len(group["boat_name"]):
            group["boat_name"] = boat_name

        # Keep the first non-None TCC
        if not group["tcc"] and tcc:
            group["tcc"] = tcc

    if not sail_groups and not inline_updates:
        return []

    # Create boats and link results — use engine for separate transactions
    updates = list(inline_updates)
    boats_created = 0
    BATCH = 200

    for i in range(0, len(sail_groups), BATCH):
        batch = sail_groups[i:i + BATCH]
        with engine.begin() as wconn:
            for group in batch:
                # Final live-DB token-intersection check. The in-memory
                # sail_index may be stale (boats inserted by a
                # concurrent scraper run, or by an earlier group within
                # this same call). Query every token against the live
                # boats table.
                token_list = sorted(group["tokens"])
                live_rows = wconn.execute(
                    text("""
                        SELECT id, sail_number, boat_name, country
                        FROM boats
                        WHERE UPPER(REPLACE(REPLACE(REPLACE(REPLACE(
                                sail_number,
                                ' ', ''), '-', ''), '.', ''), '/', ''))
                              = ANY(:tokens)
                    """),
                    {"tokens": token_list},
                ).fetchall()

                # The direct equality above won't catch boats whose
                # stored sail itself splits into multiple tokens
                # (e.g., live row stores `2561&011` and our incoming
                # is `2561`). Fall back to a token-intersection scan
                # of any boat sharing a prefix or short token — handled
                # by also walking the in-memory sail_index, which now
                # contains the same tokens.
                live_boat_ids: dict[int, dict] = {}
                for lr in live_rows:
                    live_boat_ids[lr[0]] = {
                        "id": lr[0],
                        "sail_number": lr[1],
                        "boat_name": lr[2],
                        "country": lr[3],
                        "tokens": normalize_sail_tokens(lr[1]),
                    }
                # In-memory index sweep — catches stored sails like
                # `2561&011` whose own tokens (`2561`, `011`) overlap
                # with this group's tokens.
                for tok in group["tokens"]:
                    for boat in sail_index.get(tok, []):
                        if boat["id"] not in live_boat_ids:
                            live_boat_ids[boat["id"]] = boat

                boat_id: int | None = None

                if live_boat_ids:
                    # Pick the live boat whose name matches; otherwise
                    # if there's only one candidate take it. If multiple
                    # candidates and no name match, take the smallest id
                    # (deterministic + likely-canonical).
                    norm_name = normalize_name(group["boat_name"])
                    chosen = None
                    if len(live_boat_ids) == 1:
                        chosen = next(iter(live_boat_ids.values()))
                    else:
                        for boat in live_boat_ids.values():
                            if normalize_name(boat["boat_name"]) == norm_name:
                                chosen = boat
                                break
                        if chosen is None:
                            chosen = min(live_boat_ids.values(), key=lambda b: b["id"])
                    boat_id = chosen["id"]

                    # Record the new sail observation as a boat_identity
                    # so future scans surface this token as a known
                    # alias. Idempotent — ON CONFLICT DO NOTHING and a
                    # NOT EXISTS guard cover repeated runs.
                    wconn.execute(
                        text("""
                            INSERT INTO boat_identities (
                                boat_id, boat_name, sail_number, source
                            )
                            SELECT :boat_id, :boat_name, :sail, :source
                            WHERE NOT EXISTS (
                                SELECT 1 FROM boat_identities
                                WHERE boat_id = :boat_id
                                  AND COALESCE(sail_number,'') = :sail
                                  AND COALESCE(source,'') = :source
                            )
                        """),
                        {
                            "boat_id": boat_id,
                            "boat_name": group["boat_name"],
                            "sail": group["sail_number"],
                            # Tag identities created by this dedup path so
                            # they're distinguishable from irc_tcc /
                            # orc_certificate observations.
                            "source": (
                                f"race_result:{group['source']}"
                                if group.get("source")
                                else "race_result"
                            ),
                        },
                    )
                else:
                    result = wconn.execute(
                        text("""
                            INSERT INTO boats (boat_name, sail_number, country)
                            VALUES (:name, :sail, :country)
                            RETURNING id
                        """),
                        {
                            "name": group["boat_name"],
                            "sail": group["sail_number"],
                            "country": group["country"],
                        },
                    )
                    new_row = result.first()
                    if not new_row:
                        continue

                    boat_id = new_row[0]
                    boats_created += 1

                    # Add the new boat to the in-memory index under each
                    # of its tokens so subsequent groups in this loop can
                    # find it.
                    boat_rec = {
                        "id": boat_id,
                        "sail_number": group["sail_number"],
                        "boat_name": group["boat_name"],
                        "country": group["country"],
                        "tokens": set(group["tokens"]),
                    }
                    for tok in group["tokens"]:
                        sail_index[tok].append(boat_rec)

                for result_id in group["result_ids"]:
                    updates.append((boat_id, result_id))

    stats["boats_created"] = boats_created
    stats["results_linked_to_new_boats"] = len(updates)

    return updates


def _apply_updates(engine, updates, stats):
    """Apply boat_id updates to race_results in batches to avoid OOM.

    Each row UPDATE is wrapped in a SAVEPOINT so a single duplicate-key
    violation (race_results_boat_event_race_key) only rolls back that row,
    not the entire 500-row batch transaction. Without the savepoint, the
    first failure poisons the surrounding transaction and every subsequent
    statement raises InFailedSqlTransactionError, which was silently
    counted as a "skipped" and prevented thousands of valid matches from
    persisting.
    """
    if not updates:
        return

    # De-duplicate (boat_id, result_id) pairs — the same result row can be
    # matched by multiple strategies/phases in one run.
    seen = set()
    unique_updates = []
    for pair in updates:
        if pair not in seen:
            seen.add(pair)
            unique_updates.append(pair)

    applied = 0
    skipped = 0
    BATCH = 500

    for i in range(0, len(unique_updates), BATCH):
        batch = unique_updates[i:i + BATCH]
        with engine.begin() as conn:
            for boat_id, result_id in batch:
                sp = conn.begin_nested()
                try:
                    result = conn.execute(
                        text("UPDATE race_results SET boat_id = :boat_id WHERE id = :id AND boat_id IS NULL"),
                        {"boat_id": boat_id, "id": result_id},
                    )
                    sp.commit()
                    if result.rowcount and result.rowcount > 0:
                        applied += 1
                    else:
                        skipped += 1
                except Exception:
                    sp.rollback()
                    skipped += 1

    stats["updated"] = stats.get("updated", 0) + applied
    stats["skipped_dupes"] = stats.get("skipped_dupes", 0) + skipped


def run_rematch(engine: Engine, dry_run: bool = False) -> dict:
    """Re-match all unmatched race results. Returns stats dict.

    Three-phase approach:
    1. Match against existing boats (source-specific + generic all-source)
    2. Create new boats from unmatched results with sail_number + boat_name
    3. Re-match remaining results against newly created boats
    """
    stats = {
        "total_unmatched": 0,
        "matched": 0,
        "updated": 0,
        "remaining": 0,
        "boats_created": 0,
    }

    with engine.connect() as conn:
        total = conn.execute(text(
            "SELECT COUNT(*) FROM race_results WHERE boat_id IS NULL"
        )).scalar()
        stats["total_unmatched"] = total

    if total == 0:
        stats["remaining"] = 0
        return stats

    # Build indexes (read-only)
    with engine.connect() as conn:
        sail_index = _build_sail_index(conn)
        name_index = _build_name_index(conn)
        owner_unambiguous, owner_ambiguous = _build_owner_boat_map(conn)
        boat_tcc, tcc_boat = _build_tcc_indexes(conn)
        identity_name_index, identity_sail_index = _build_identity_indexes(conn)

    # ---- Phase 1: Match against existing boats ----
    with engine.connect() as conn:
        sailsys_updates = _match_sailsys(
            conn, sail_index, name_index,
            identity_name_index, identity_sail_index, stats,
        )
        rorc_updates = _match_rorc(
            conn, owner_unambiguous, owner_ambiguous, boat_tcc, tcc_boat, stats,
        )

    phase1_updates = sailsys_updates + rorc_updates
    if phase1_updates and not dry_run:
        _apply_updates(engine, phase1_updates, stats)

    # Generic all-source matcher
    with engine.connect() as conn:
        generic_updates = _match_generic(
            conn, sail_index, name_index,
            identity_name_index, identity_sail_index, stats,
        )

    if generic_updates and not dry_run:
        _apply_updates(engine, generic_updates, stats)

    # ---- Phase 2: Create new boats from unmatched results ----
    if not dry_run:
        with engine.connect() as conn:
            create_updates = _create_boats_from_results(conn, engine, sail_index, stats)
        if create_updates:
            _apply_updates(engine, create_updates, stats)

        # ---- Phase 3: Rebuild indexes and re-match ----
        if stats.get("boats_created", 0) > 0:
            with engine.connect() as conn:
                sail_index = _build_sail_index(conn)
                name_index = _build_name_index(conn)

            with engine.connect() as conn:
                phase3_stats = {"matched": 0}
                rematch_updates = _match_generic(
                    conn, sail_index, name_index,
                    identity_name_index, identity_sail_index, phase3_stats,
                )
            stats["phase3_rematched"] = phase3_stats["matched"]
            for k, v in phase3_stats.items():
                if k != "matched":
                    stats[k] = stats.get(k, 0) + v
            stats["matched"] += phase3_stats["matched"]

            if rematch_updates:
                _apply_updates(engine, rematch_updates, stats)

    with engine.connect() as conn:
        remaining = conn.execute(text(
            "SELECT COUNT(*) FROM race_results WHERE boat_id IS NULL"
        )).scalar()
        stats["remaining"] = remaining

    return stats
