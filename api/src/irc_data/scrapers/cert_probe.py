"""Strategy 3: Smart backward cert number scanning for boats.

For each boat (optionally filtered by design), scan backward from their
current cert number to find earlier certs with the same name + sail number.

Supports:
- Scanning specific designs (e.g., "Sunfast 3300")
- Scanning ALL boats in the database
- Filtering by cert number range for parallel processing

Uses async semaphore for concurrent HEAD requests.
"""

import asyncio
import json
from pathlib import Path

from irc_data.config import CERTIFICATES_DIR, HISTORICAL_CERTS_DIR, IRC_PDF_BASE_URL
from irc_data.scrapers.base import get_http_client
from irc_data.scrapers.historical_certs import (
    _existing_cert_numbers,
    _normalise_boat_name,
    _normalise_sail_number,
    download_cert,
)

# Tracking file for probe attempts to allow resumption
PROBE_STATE_FILE = HISTORICAL_CERTS_DIR / ".probe_state.json"

# Defaults
DEFAULT_SCAN_RANGE = 5000
CONSECUTIVE_404_SKIP = 500
MAX_CONCURRENT = 5
REQUEST_DELAY = 0.1  # delay per request within semaphore


def _load_probe_state() -> dict:
    """Load probe state from disk for resumption."""
    if PROBE_STATE_FILE.exists():
        return json.loads(PROBE_STATE_FILE.read_text())
    return {}


def _save_probe_state(state: dict) -> None:
    """Persist probe state for resumption."""
    PROBE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_STATE_FILE.write_text(json.dumps(state, indent=2))


async def _probe_cert_number(
    client,
    semaphore: asyncio.Semaphore,
    cert_no: int,
    boat_name: str,
    sail_no: str,
) -> bool:
    """Probe a single cert number. Returns True if found."""
    name = _normalise_boat_name(boat_name)
    sail = _normalise_sail_number(sail_no)
    url = f"{IRC_PDF_BASE_URL}/{cert_no}_{name}_{sail}.pdf"

    async with semaphore:
        await asyncio.sleep(REQUEST_DELAY)
        try:
            resp = await client.head(url, follow_redirects=True)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                cl = int(resp.headers.get("content-length", "0"))
                return "pdf" in ct.lower() or cl > 1000
        except Exception:
            pass
    return False


def _get_design_boats(design: str) -> list[dict]:
    """Get boats of a specific design from the 2026 TCC CSV.

    Returns list of dicts with cert_number, boat_name, sail_number.
    """
    from irc_data.parsers.tcc_csv import parse_tcc_csv
    from irc_data.config import TCC_LISTINGS_DIR

    boats = []
    # Use the most recent CSV
    csv_files = sorted(TCC_LISTINGS_DIR.glob("*.csv"), reverse=True)
    if not csv_files:
        return boats

    rows = parse_tcc_csv(csv_files[0])
    for row in rows:
        # Detect design by dimensions (Sunfast 3300: LH=9.99, Beam=3.40)
        from irc_data.parsers.tcc_csv import detect_design
        det = detect_design(row.lh, row.beam)
        if det and det.lower() == design.lower():
            boats.append({
                "cert_number": row.cert_number,
                "boat_name": row.boat_name,
                "sail_number": row.sail_number,
            })
    return boats


def _get_all_boats_from_db() -> list[dict]:
    """Get ALL boats from the database.

    Returns list of dicts with cert_number, boat_name, sail_number.
    """
    from sqlalchemy import select
    from irc_data.db.connection import get_engine
    from irc_data.db.models import Boat

    boats = []
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            select(Boat.cert_number, Boat.boat_name, Boat.sail_number)
            .where(Boat.cert_number.isnot(None))
        )
        for row in result:
            if row.cert_number and row.cert_number.strip():
                boats.append({
                    "cert_number": row.cert_number,
                    "boat_name": row.boat_name,
                    "sail_number": row.sail_number,
                })
    return boats


def _filter_boats_by_cert_range(
    boats: list[dict],
    start_cert: int | None = None,
    end_cert: int | None = None,
) -> list[dict]:
    """Filter boats to those with cert numbers in the specified range."""
    filtered = []
    for boat in boats:
        try:
            cert_no = int(boat["cert_number"])
            if start_cert and cert_no < start_cert:
                continue
            if end_cert and cert_no > end_cert:
                continue
            filtered.append(boat)
        except (ValueError, TypeError):
            continue
    return filtered


async def probe_design_boats(
    design: str | None = "Sunfast 3300",
    scan_range: int = DEFAULT_SCAN_RANGE,
    max_concurrent: int = MAX_CONCURRENT,
    output_dir: Path | None = None,
    dry_run: bool = False,
    all_boats: bool = False,
    start_cert: int | None = None,
    end_cert: int | None = None,
) -> dict:
    """Scan backward from current cert numbers for boats.

    For each boat, try cert numbers from (current - scan_range) to (current - 1)
    with the same boat name and sail number. Skip ahead after CONSECUTIVE_404_SKIP
    misses to save time.

    Args:
        design: Filter to specific design (e.g., "Sunfast 3300"). Ignored if all_boats=True.
        scan_range: How far back to scan from current cert number.
        max_concurrent: Max concurrent HEAD requests.
        output_dir: Where to save downloaded PDFs.
        dry_run: If True, just show what would be probed.
        all_boats: If True, scan ALL boats in database (ignores design filter).
        start_cert: Only process boats with cert numbers >= this value.
        end_cert: Only process boats with cert numbers <= this value.

    Returns stats dict.
    """
    output_dir = output_dir or HISTORICAL_CERTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get boats to scan
    if all_boats:
        boats = _get_all_boats_from_db()
        label = "all"
    else:
        boats = _get_design_boats(design or "Sunfast 3300")
        label = design or "Sunfast 3300"

    if not boats:
        print(f"No {label} boats found")
        return {"boats": 0, "probed": 0, "found": 0}

    # Apply cert range filter
    if start_cert or end_cert:
        boats = _filter_boats_by_cert_range(boats, start_cert, end_cert)
        range_str = f" (cert range {start_cert or 1}-{end_cert or 'max'})"
    else:
        range_str = ""

    print(f"Found {len(boats)} {label} boats{range_str}")

    existing = _existing_cert_numbers(CERTIFICATES_DIR)
    existing |= _existing_cert_numbers(output_dir)
    state = _load_probe_state()

    if dry_run:
        total_probes = 0
        for boat in boats:
            try:
                current = int(boat["cert_number"])
                start = max(1, current - scan_range)
                probes = current - start
                total_probes += probes
                print(
                    f"  {boat['boat_name']:<20} {boat['sail_number']:<12} "
                    f"cert {current}: scan {start}..{current-1} ({probes} probes)"
                )
            except ValueError:
                pass
        print(f"\nTotal probes: {total_probes:,} (~{total_probes / 10 / 3600:.1f}h at 10/sec)")
        return {"boats": len(boats), "probed": 0, "found": 0}

    stats = {"boats": len(boats), "probed": 0, "found": 0, "downloaded": 0}
    semaphore = asyncio.Semaphore(max_concurrent)

    async with get_http_client(timeout=None) as client:
        for boat in boats:
            boat_key = f"{boat['boat_name']}_{boat['sail_number']}"
            try:
                current = int(boat["cert_number"])
            except ValueError:
                continue

            # Check resume state
            last_probed = state.get(boat_key, {}).get("last_probed", current)
            start = max(1, current - scan_range)

            print(
                f"\n  {boat['boat_name']} ({boat['sail_number']}): "
                f"scanning {start}..{current-1}"
            )

            consecutive_misses = 0
            for cert_no in range(current - 1, start - 1, -1):
                if str(cert_no) in existing:
                    consecutive_misses = 0
                    continue

                found = await _probe_cert_number(
                    client, semaphore, cert_no,
                    boat["boat_name"], boat["sail_number"],
                )
                stats["probed"] += 1

                if found:
                    consecutive_misses = 0
                    stats["found"] += 1
                    name = _normalise_boat_name(boat["boat_name"])
                    sail = _normalise_sail_number(boat["sail_number"])
                    url = f"{IRC_PDF_BASE_URL}/{cert_no}_{name}_{sail}.pdf"
                    print(f"    FOUND: cert {cert_no}")
                    path = await download_cert(client, url, output_dir)
                    if path:
                        stats["downloaded"] += 1
                        existing.add(str(cert_no))
                else:
                    consecutive_misses += 1
                    if consecutive_misses >= CONSECUTIVE_404_SKIP:
                        print(
                            f"    Skipping: {CONSECUTIVE_404_SKIP} consecutive 404s "
                            f"at cert {cert_no}"
                        )
                        break

                # Save state periodically
                if stats["probed"] % 1000 == 0:
                    state[boat_key] = {"last_probed": cert_no}
                    _save_probe_state(state)
                    print(
                        f"    Progress: {stats['probed']:,} probed, "
                        f"{stats['found']} found"
                    )

            state[boat_key] = {"last_probed": start, "complete": True}
            _save_probe_state(state)

    print(
        f"\nDone: {stats['probed']:,} probed, "
        f"{stats['found']} found, {stats['downloaded']} downloaded"
    )
    return stats
