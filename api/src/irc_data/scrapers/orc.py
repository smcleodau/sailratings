"""ORC certificate scraper — downloads all active certificates from data.orc.org.

Strategy: For each of ~32 ORC member countries, fetch the active certificates
XML index via the public API. Parse key fields and store to orc_certificates.
Archive raw XML to data/raw/orc/.

The full index (~6,750 certs) takes about 60 seconds with 2s rate limiting.
"""

import asyncio
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from irc_data.config import ORC_API_BASE, ORC_COUNTRIES, ORC_RAW_DIR
from irc_data.scrapers.base import RateLimiter, get_http_client

# Rate limiter: 2 seconds between requests
orc_limiter = RateLimiter(min_delay=2.0, jitter=0.5)


def _safe_decimal(value: str | None) -> Decimal | None:
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None


def _safe_int(value: str | None) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_orc_date(value: str | None) -> date | None:
    """Parse ISO 8601 date from ORC API (e.g. '2026-02-11T20:50:30.000Z')."""
    if not value or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def parse_active_certs_xml(xml_text: str) -> list[dict]:
    """Parse the activecerts XML response into a list of certificate dicts."""
    # Strip BOM if present
    if xml_text.startswith("\ufeff"):
        xml_text = xml_text[1:]

    root = ET.fromstring(xml_text)
    certs = []

    for row in root.iter("ROW"):
        cert = {}
        for child in row:
            cert[child.tag] = child.text

        issue_date = _parse_orc_date(cert.get("dxtDate"))
        expiry = _parse_orc_date(cert.get("Expiry"))

        certs.append({
            "ref_no": cert.get("RefNo", ""),
            "country_id": cert.get("CountryId", ""),
            "dxt_id": cert.get("dxtID", ""),
            "yacht_name": cert.get("YachtName"),
            "sail_no": cert.get("SailNo"),
            "class_name": cert.get("Class"),
            "cert_type": _safe_int(cert.get("CertType")),
            "cert_type_name": cert.get("CertName"),
            "family": _safe_int(cert.get("Family")),
            "family_name": cert.get("FamilyName"),
            "vpp_year": _safe_int(cert.get("VPPYear")),
            "issue_date": issue_date,
            "expiry": expiry,
            "is_one_design": cert.get("IsOd") == "True",
            # String versions for JSON storage
            "issue_date_str": issue_date.isoformat() if issue_date else None,
            "expiry_str": expiry.isoformat() if expiry else None,
        })

    return certs


async def fetch_country_certs(
    client: httpx.AsyncClient,
    country_id: str,
) -> tuple[str, list[dict]]:
    """Fetch all active certificates for a single country.

    Returns (country_id, list of cert dicts).
    """
    await orc_limiter.wait()

    url = f"{ORC_API_BASE}?action=activecerts&CountryId={country_id}"
    response = await client.get(url)
    response.raise_for_status()

    xml_text = response.text
    certs = parse_active_certs_xml(xml_text)

    return country_id, certs, xml_text


async def fetch_certificate_detail(
    client: httpx.AsyncClient,
    dxt_id: str,
) -> str | None:
    """Fetch the full HTML certificate page for a single certificate.

    Returns the HTML text, or None on failure.
    """
    await orc_limiter.wait()

    url = f"{ORC_API_BASE[:-len('/WPub.dll')]}/WPub.dll/CC/{dxt_id}"
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
    except (httpx.HTTPStatusError, httpx.TransportError):
        return None


async def fetch_certificate_rms(
    client: httpx.AsyncClient,
    ref_no: str,
) -> dict | None:
    """Fetch the full RMS (Rating Management System) JSON for a certificate.

    Uses the DownBoatRMS action which returns GPH, CDL, triple numbers,
    dimensions, sail areas, stability, and all ORC performance data.
    Returns the parsed cert dict, or None on failure.
    """
    await orc_limiter.wait()

    url = f"{ORC_API_BASE}?action=DownBoatRMS&RefNo={ref_no}&ext=json"
    try:
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        if text.startswith("\ufeff"):
            text = text[1:]
        import json
        data = json.loads(text)
        rms_list = data.get("rms", [])
        if rms_list:
            return rms_list[0]
        return None
    except (httpx.HTTPStatusError, httpx.TransportError, Exception):
        return None


# Fields whose values are nested JSON structures (dicts), not scalars.
# The UPDATE builder must wrap them in `CAST(:k AS jsonb)` and JSON-encode
# the value rather than binding it as a normal parameter.
_JSON_FIELDS: frozenset[str] = frozenset({"allowances"})


def _extract_performance_fields(rms: dict) -> dict:
    """Extract key performance fields from an RMS JSON response.

    Returns one entry per column in `orc_certificates` that we know how to
    populate from the RMS payload. The dynamic UPDATE in `backfill_orc_details`
    skips entries whose value is None; values in `_JSON_FIELDS` are emitted as
    Python dicts and serialised + cast at UPDATE time.
    """
    main = _safe_decimal(str(rms.get("Area_Main", "")))
    jib = _safe_decimal(str(rms.get("Area_Jib", "")))
    return {
        # Existing scalar columns.
        "gph": _safe_decimal(str(rms.get("GPH", ""))),
        "cdl": _safe_decimal(str(rms.get("CDL", ""))),
        "osn": _safe_decimal(str(rms.get("OSN", ""))),
        "triple_low": _safe_decimal(str(rms.get("TND_Offshore_Low", ""))),
        "triple_med": _safe_decimal(str(rms.get("TND_Offshore_Medium", ""))),
        "triple_high": _safe_decimal(str(rms.get("TND_Offshore_High", ""))),
        "loa": _safe_decimal(str(rms.get("LOA", ""))),
        "displacement": _safe_decimal(str(rms.get("Dspl_Measurement", ""))),
        "draft": _safe_decimal(str(rms.get("Draft", ""))),
        "sail_area_upwind": main + jib if main and jib else None,
        "sail_area_downwind": _safe_decimal(str(rms.get("Area_Sym", ""))) or _safe_decimal(str(rms.get("Area_Asym", ""))),
        "stability_index": _safe_decimal(str(rms.get("Stability_Index", ""))),
        "builder": rms.get("Builder"),
        "designer": rms.get("Designer"),
        "year_built": _safe_int(str(rms.get("Age_Year", ""))),
        # VPP + dimension columns added in migration 0013.
        # `allowances` is the full polar table — dict, goes to JSONB column.
        "allowances": rms.get("Allowances"),
        "dynamic_allowance": _safe_decimal(str(rms.get("Dynamic_Allowance", ""))),
        "dspl_sailing": _safe_decimal(str(rms.get("Dspl_Sailing", ""))),
        "imsl": _safe_decimal(str(rms.get("IMSL", ""))),
        "mb": _safe_decimal(str(rms.get("MB", ""))),
        "aphd": _safe_decimal(str(rms.get("APHD", ""))),
        "apht": rms.get("APHT"),
        "wss": _safe_decimal(str(rms.get("WSS", ""))),
        "tmf_offshore": _safe_decimal(str(rms.get("TMF_Offshore", ""))),
        "tmf_inshore": _safe_decimal(str(rms.get("TMF_Inshore", ""))),
    }


async def backfill_orc_details(
    limit: int | None = None,
    concurrency: int = 3,
) -> dict:
    """Fetch full RMS data for ORC certs that are missing GPH.

    Only fetches for the latest snapshot to avoid re-fetching old data.
    Returns stats dict.

    ``concurrency`` bounds the number of in-flight RMS fetches.  Every
    request still passes through the shared 2s ``orc_limiter`` (the
    responsible-collection policy), so concurrency only overlaps the
    server-response + DB-write latency — it does not raise the request
    rate.  The scheduling policy (``cadence.DOMAIN_CONCURRENCY_CAPS``)
    caps ``data.orc.org`` at 3 in-flight requests; the default matches.
    """
    from sqlalchemy import text as sa_text
    from irc_data.db.connection import get_engine

    engine = get_engine()
    stats = {"total_missing": 0, "fetched": 0, "errors": 0}

    with engine.connect() as conn:
        # Get certs whose VPP detail (CDL/allowances) is missing from the
        # latest snapshot only.  CDL/allowances are the reliable "detail
        # drained" markers: every cert type that exposes an RMS payload
        # carries CDL + Allowances, whereas ~6% of certs are APH/single-
        # number and never expose GPH — keying the backlog on gph IS NULL
        # would re-fetch those forever.  GPH is still written whenever the
        # RMS payload provides it.
        rows = conn.execute(sa_text("""
            SELECT id, ref_no
            FROM orc_certificates
            WHERE (cdl IS NULL OR allowances IS NULL)
              AND snapshot_date = (SELECT max(snapshot_date) FROM orc_certificates)
              AND ref_no IS NOT NULL AND ref_no != ''
            ORDER BY id
        """ + (f" LIMIT {int(limit)}" if limit else ""))).fetchall()

    stats["total_missing"] = len(rows)
    print(f"  {len(rows)} certs missing detail data", flush=True)

    from irc_data.db.ingest_log import log_event

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    processed = 0

    async def _drain_one(client: httpx.AsyncClient, cert_id: int, ref_no: str) -> None:
        nonlocal processed
        async with sem:
            try:
                rms = await fetch_certificate_rms(client, ref_no)
                if rms:
                    import json as _json
                    fields = _extract_performance_fields(rms)
                    with engine.begin() as conn:
                        # Update the cert with performance data + full raw RMS.
                        # JSONB columns (see _JSON_FIELDS) need a CAST and
                        # serialised value; scalar columns bind directly.
                        set_clauses = []
                        params: dict = {"cert_id": cert_id, "raw_data": _json.dumps(rms)}
                        for k, v in fields.items():
                            if v is None:
                                continue
                            if k in _JSON_FIELDS:
                                set_clauses.append(f"{k} = CAST(:{k} AS jsonb)")
                                params[k] = _json.dumps(v)
                            else:
                                set_clauses.append(f"{k} = :{k}")
                                params[k] = v
                        set_clauses.append("raw_data = CAST(:raw_data AS jsonb)")
                        if set_clauses:
                            conn.execute(
                                sa_text(f"UPDATE orc_certificates SET {', '.join(set_clauses)} WHERE id = :cert_id"),
                                params,
                            )
                    stats["fetched"] += 1
                    log_event(engine, "orc", "parse", "ok", ref_no, None)
                else:
                    stats["errors"] += 1
                    log_event(
                        engine, "orc", "parse", "error", ref_no,
                        "fetch_certificate_rms returned empty payload",
                    )
            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 3:
                    print(f"    Error on {ref_no}: {e}", flush=True)
                log_event(engine, "orc", "parse", "error", ref_no, str(e))
            finally:
                processed += 1
                if processed % 100 == 0:
                    print(
                        f"    {processed}/{len(rows)} processed "
                        f"({stats['fetched']} fetched, {stats['errors']} errors)",
                        flush=True,
                    )

    async with get_http_client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        await asyncio.gather(
            *(_drain_one(client, row[0], row[1]) for row in rows)
        )

    print(f"  Done: {stats['fetched']} fetched, {stats['errors']} errors", flush=True)
    return stats


async def scrape_all_countries(
    snapshot_date: date | None = None,
    archive_raw: bool = True,
    countries: list[str] | None = None,
) -> dict:
    """Scrape ORC certificates for all (or specified) countries.

    Returns stats dict with counts.
    """
    from irc_data.db.connection import get_engine
    from irc_data.db.operations import log_ingestion_start, log_ingestion_end, upsert_orc_certificate, upsert_orc_snapshot

    snapshot_date = snapshot_date or date.today()
    country_list = countries or list(ORC_COUNTRIES.keys())
    engine = get_engine()

    # Start ingestion log
    log_id = log_ingestion_start(engine, "orc_api", metadata={"countries": country_list})

    total_found = 0
    total_new = 0
    errors = []

    # Archive directory
    if archive_raw:
        archive_dir = ORC_RAW_DIR / snapshot_date.isoformat()
        archive_dir.mkdir(parents=True, exist_ok=True)

    async with get_http_client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for country_id in country_list:
            country_name = ORC_COUNTRIES.get(country_id, country_id)
            try:
                print(f"  Fetching {country_id} ({country_name})...")
                cid, certs, xml_text = await fetch_country_certs(client, country_id)

                # Archive raw XML
                if archive_raw:
                    xml_path = archive_dir / f"{country_id}.xml"
                    xml_path.write_text(xml_text, encoding="utf-8")

                # Store snapshot metadata
                upsert_orc_snapshot(
                    engine,
                    country_id=country_id,
                    snapshot_date=snapshot_date,
                    records_found=len(certs),
                    raw_json_path=str(archive_dir / f"{country_id}.xml") if archive_raw else None,
                )

                # Upsert each certificate
                new_in_country = 0
                for cert in certs:
                    # Build JSON-safe raw_data (no date objects)
                    raw = {k: v for k, v in cert.items()
                           if k not in ("issue_date", "expiry")}
                    was_new = upsert_orc_certificate(
                        engine,
                        snapshot_date=snapshot_date,
                        ref_no=cert["ref_no"],
                        country_id=country_id,
                        yacht_name=cert.get("yacht_name"),
                        sail_no=cert.get("sail_no"),
                        class_name=cert.get("class_name"),
                        raw_data=raw,
                    )
                    if was_new:
                        new_in_country += 1

                total_found += len(certs)
                total_new += new_in_country
                print(f"    {len(certs)} certs ({new_in_country} new)")

            except Exception as e:
                error_msg = f"{country_id}: {e}"
                errors.append(error_msg)
                print(f"    ERROR: {e}")

    # Complete ingestion log
    log_ingestion_end(
        engine, log_id,
        status="completed" if not errors else "completed_with_errors",
        records_found=total_found,
        records_new=total_new,
        error_message="; ".join(errors) if errors else None,
    )

    return {
        "countries": len(country_list),
        "total_found": total_found,
        "total_new": total_new,
        "errors": errors,
        "snapshot_date": snapshot_date.isoformat(),
    }
