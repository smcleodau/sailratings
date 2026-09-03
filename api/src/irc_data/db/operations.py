"""Database insert/upsert/query helpers."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine
from irc_data.db.models import (
    Boat,
    Certificate,
    IngestionLog,
    ORCCertificate,
    ORCSnapshot,
    RaceResultModel,
    TCCSnapshotModel,
)


# ---------------------------------------------------------------------------
# Boats
# ---------------------------------------------------------------------------


def upsert_boat(
    engine: Engine,
    boat_name: str,
    sail_number: str,
    cert_number: str | None = None,
    design: str | None = None,
    country: str | None = None,
    year_built: int | None = None,
) -> int:
    """Insert or update a boat, returning its id.

    Defensive guard: strip trailing " - SEC" / " (SH)" suffixes from
    boat_name. The IRC TCC CSV publishes secondary-cert rows with these
    suffixes (re-issued and short-handed certs respectively), and
    historically every caller had to remember to handle them; centralising
    the strip here ensures no path can create duplicate boats again. The
    TCC importer additionally routes secondary rows to attach to the
    primary boat instead of upserting through here.
    """
    import re as _re
    boat_name = _re.sub(r"\s*-\s*SEC\s*$", "", boat_name or "", flags=_re.IGNORECASE)
    boat_name = _re.sub(r"\s*\(\s*SH\s*\)\s*$", "", boat_name, flags=_re.IGNORECASE).strip()
    stmt = pg_insert(Boat.__table__).values(
        boat_name=boat_name,
        sail_number=sail_number,
        cert_number=cert_number,
        design=design,
        country=country,
        year_built=year_built,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="boats_sail_number_cert_number_key",
        set_=dict(
            boat_name=stmt.excluded.boat_name,
            design=stmt.excluded.design,
            country=stmt.excluded.country,
            year_built=stmt.excluded.year_built,
            updated_at=text("now()"),
        ),
    )
    stmt = stmt.returning(Boat.__table__.c.id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# TCC Snapshots
# ---------------------------------------------------------------------------


def upsert_tcc_snapshot(
    engine: Engine,
    boat_id: int,
    snapshot_date: date,
    **kwargs,
) -> int:
    """Insert or update a TCC snapshot, returning its id."""
    values = dict(boat_id=boat_id, snapshot_date=snapshot_date, **kwargs)
    stmt = pg_insert(TCCSnapshotModel.__table__).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="tcc_snapshots_boat_id_snapshot_date_key",
        set_={k: stmt.excluded[k] for k in kwargs},
    )
    stmt = stmt.returning(TCCSnapshotModel.__table__.c.id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


def upsert_certificate(engine: Engine, boat_id: int | None, cert_data: dict) -> int:
    """Insert or update a certificate record."""
    values = {"boat_id": boat_id, **cert_data}
    stmt = pg_insert(Certificate.__table__).values(**values)
    update_cols = {k: stmt.excluded[k] for k in cert_data if k != "cert_number"}
    update_cols["scraped_at"] = text("now()")
    stmt = stmt.on_conflict_do_update(
        constraint="certificates_cert_number_key",
        set_=update_cols,
    )
    stmt = stmt.returning(Certificate.__table__.c.id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Race Results
# ---------------------------------------------------------------------------


def _ensure_event_id(conn, *, name: str, start_date, organiser: str | None) -> int:
    """Return ``events.id`` for (name, start_date), creating it if needed.

    Mirrors the backfill in alembic 0022: one event per (name, start_date),
    organiser taken from the first row that mentions it.
    """
    row = conn.execute(
        text(
            "SELECT id FROM events WHERE name = :n AND "
            "start_date IS NOT DISTINCT FROM :d LIMIT 1"
        ),
        {"n": name, "d": start_date},
    ).first()
    if row:
        return int(row[0])
    return int(
        conn.execute(
            text(
                "INSERT INTO events (name, start_date, end_date, organiser, "
                "created_at, updated_at) VALUES (:n, :d, :d, :o, now(), now()) "
                "RETURNING id"
            ),
            {"n": name, "d": start_date, "o": organiser},
        ).scalar_one()
    )


def _ensure_event_entry_id(
    conn,
    *,
    event_id: int,
    boat_id: int | None,
    boat_name: str | None,
    sail_number: str | None,
    tcc,
) -> int:
    """Return ``event_entries.id`` for this boat within an event.

    NULL-safe matching on (event_id, boat_id, boat_name) so unmatched legacy
    rows (boat_id NULL) still resolve to a stable entry, exactly like the
    tolerant backfill pass in alembic 0022.
    """
    row = conn.execute(
        text(
            "SELECT id FROM event_entries WHERE event_id = :e AND "
            "boat_id IS NOT DISTINCT FROM :b AND "
            "boat_name IS NOT DISTINCT FROM :bn LIMIT 1"
        ),
        {"e": event_id, "b": boat_id, "bn": boat_name},
    ).first()
    if row:
        return int(row[0])
    return int(
        conn.execute(
            text(
                "INSERT INTO event_entries (event_id, boat_id, sail_number, "
                "boat_name, tcc, created_at) VALUES (:e, :b, :sn, :bn, :tcc, now()) "
                "RETURNING id"
            ),
            {
                "e": event_id,
                "b": boat_id,
                "sn": sail_number,
                "bn": boat_name,
                "tcc": float(tcc) if isinstance(tcc, Decimal) else tcc,
            },
        ).scalar_one()
    )


def upsert_race_result(
    engine: Engine,
    boat_id: int | None,
    event_name: str,
    event_date: date | None = None,
    race_name: str | None = None,
    **kwargs,
) -> int:
    """Insert a race result, resolving its NOT NULL ``event_entry_id``.

    ``race_results.event_entry_id`` has been NOT NULL since alembic 0022
    (strict 3NF), so every row must link to an event_entry. The previous
    implementation assumed the legacy denormalised shape and, after the ORM
    model drifted to drop ``event_name``/``boat_id``/``organizing_club``,
    raised ``KeyError('organizing_club')`` on every row — which is why the
    SailSys runs logged found>0 / new=0 (OPS-02-02).
    """
    # Default ingestion-path tag. Firecrawl callers set transport='firecrawl'
    # explicitly via import_scraper_results; bespoke scrapers and JSON imports
    # call this directly and inherit 'legacy' so the parity diagnostic has
    # both sides to compare.
    kwargs.setdefault("transport", "legacy")

    raw = kwargs.get("raw_data") or {}
    boat_name = raw.get("boat_name")
    sail_number = raw.get("sail_number") or raw.get("sail_no")
    tcc = kwargs.get("tcc_at_race") or kwargs.get("rating_value")
    organiser = kwargs.get("organizing_club")

    with engine.begin() as conn:
        event_id = _ensure_event_id(
            conn, name=event_name, start_date=event_date, organiser=organiser,
        )
        event_entry_id = _ensure_event_entry_id(
            conn, event_id=event_id, boat_id=boat_id,
            boat_name=boat_name, sail_number=sail_number, tcc=tcc,
        )

        values = dict(
            boat_id=boat_id,
            event_name=event_name,
            event_date=event_date,
            race_name=race_name,
            event_entry_id=event_entry_id,
            **kwargs,
        )
        table = RaceResultModel.__table__
        # Drift guard: only pass columns the mapped table actually has.
        values = {k: v for k, v in values.items() if k in table.c}

        # Dedup: results are stable once published; if an identical row for
        # this entry+race already exists, return it instead of duplicating.
        existing = conn.execute(
            text(
                "SELECT id FROM race_results WHERE event_entry_id = :e AND "
                "race_name IS NOT DISTINCT FROM :rn LIMIT 1"
            ),
            {"e": event_entry_id, "rn": race_name},
        ).first()
        if existing:
            return int(existing[0])

        stmt = pg_insert(table).values(**values).returning(table.c.id)
        return int(conn.execute(stmt).scalar_one())


# ---------------------------------------------------------------------------
# ORC Certificates
# ---------------------------------------------------------------------------


# Fields inside raw_data that the daily scrape writes and that identify whether
# the certificate's published shape has changed. Used by the dedup logic to
# detect a "no-op" daily snapshot (vs a genuine cert re-issue).
_ORC_DAILY_FINGERPRINT_KEYS = (
    "dxt_id",
    "cert_type",
    "cert_type_name",
    "family",
    "family_name",
    "vpp_year",
    "is_one_design",
    "issue_date_str",
    "expiry_str",
)


def _orc_daily_fingerprint(raw: dict | None) -> dict:
    """Extract the stable daily-scrape fingerprint from a raw_data dict.

    Backfilled rows have raw_data replaced with full RMS JSON, which does NOT
    contain these keys, so the result is {}. The caller treats an empty
    fingerprint on either side as "fingerprint unavailable" and falls back to
    column-only comparison (yacht_name, sail_no, class_name).
    """
    if not raw:
        return {}
    return {k: raw.get(k) for k in _ORC_DAILY_FINGERPRINT_KEYS if k in raw}


def orc_content_matches(
    existing_row: dict,
    candidate_yacht_name: str | None,
    candidate_sail_no: str | None,
    candidate_class_name: str | None,
    candidate_raw_data: dict | None,
) -> bool:
    """Return True iff the candidate cert content matches the existing row.

    Compares:
      - yacht_name, sail_no, class_name (always)
      - The "daily fingerprint" subset of raw_data, but only when BOTH sides
        have it. If the existing row has been backfilled (raw_data is the big
        RMS blob with no fingerprint keys), we compare columns only — that is
        safe because backfill never changes yacht/sail/class names.
    """
    if (
        existing_row.get("yacht_name") != candidate_yacht_name
        or existing_row.get("sail_no") != candidate_sail_no
        or existing_row.get("class_name") != candidate_class_name
    ):
        return False

    existing_fp = _orc_daily_fingerprint(existing_row.get("raw_data"))
    candidate_fp = _orc_daily_fingerprint(candidate_raw_data)
    # If either side lacks a fingerprint (post-backfill row, or weird input),
    # treat the columns as authoritative.
    if not existing_fp or not candidate_fp:
        return True
    return existing_fp == candidate_fp


def upsert_orc_certificate(
    engine: Engine,
    snapshot_date: date,
    ref_no: str,
    country_id: str,
    yacht_name: str | None = None,
    sail_no: str | None = None,
    class_name: str | None = None,
    raw_data: dict | None = None,
    boat_id: int | None = None,
    **extra_fields,
) -> bool:
    """Insert or update an ORC certificate, deduplicating unchanged snapshots.

    Behaviour:
      - If the latest existing row for (ref_no, country_id) has identical
        content (yacht_name, sail_no, class_name, daily fingerprint), advance
        its snapshot_date to `snapshot_date` (only forward) and return False.
        No new row is inserted — the timeline stays contiguous without
        accumulating byte-identical duplicates.
      - If content differs or no prior row exists, insert (or, on same-day
        conflict, update) and return True only when a brand-new row was
        physically created.

    `was_new` reflects an actual INSERT (using xmax = 0), not a heuristic.
    """
    with engine.begin() as conn:
        # 1. Look up the latest existing row for this (ref_no, country_id).
        latest = conn.execute(
            text(
                """
                SELECT id, snapshot_date, yacht_name, sail_no, class_name, raw_data
                FROM orc_certificates
                WHERE ref_no = :ref_no AND country_id = :country_id
                ORDER BY snapshot_date DESC, id DESC
                LIMIT 1
                """
            ),
            {"ref_no": ref_no, "country_id": country_id},
        ).mappings().first()

        if latest is not None and orc_content_matches(
            dict(latest), yacht_name, sail_no, class_name, raw_data
        ):
            # Content unchanged. Advance the snapshot_date forward (never
            # backwards, and never if today's row already exists at that date).
            if latest["snapshot_date"] < snapshot_date:
                # Guard against a collision: another row may already exist
                # at the target date for this (ref_no, country_id). In that
                # case just delete our latest (older) row in favour of the
                # newer one — net effect is still "no new row added".
                conflict = conn.execute(
                    text(
                        """
                        SELECT 1 FROM orc_certificates
                        WHERE ref_no = :ref_no
                          AND country_id = :country_id
                          AND snapshot_date = :snapshot_date
                        """
                    ),
                    {
                        "ref_no": ref_no,
                        "country_id": country_id,
                        "snapshot_date": snapshot_date,
                    },
                ).first()
                if conflict is None:
                    conn.execute(
                        text(
                            "UPDATE orc_certificates SET snapshot_date = :d WHERE id = :id"
                        ),
                        {"d": snapshot_date, "id": latest["id"]},
                    )
            return False

        # 2. Content differs (or no prior row): insert; on same-day conflict,
        #    update in place. Use xmax = 0 to know whether a row was inserted.
        values = dict(
            snapshot_date=snapshot_date,
            ref_no=ref_no,
            country_id=country_id,
            yacht_name=yacht_name,
            sail_no=sail_no,
            class_name=class_name,
            raw_data=raw_data,
            boat_id=boat_id,
            **extra_fields,
        )
        stmt = pg_insert(ORCCertificate.__table__).values(**values)

        update_cols = {
            "yacht_name": stmt.excluded.yacht_name,
            "sail_no": stmt.excluded.sail_no,
            "class_name": stmt.excluded.class_name,
            "raw_data": stmt.excluded.raw_data,
        }
        for k in extra_fields:
            update_cols[k] = stmt.excluded[k]

        stmt = stmt.on_conflict_do_update(
            constraint="orc_certificates_ref_no_country_id_snapshot_date_key",
            set_=update_cols,
        )
        # xmax = 0 iff this tuple was just inserted (not updated).
        stmt = stmt.returning(
            ORCCertificate.__table__.c.id,
            text("(xmax = 0) AS inserted"),
        )
        row = conn.execute(stmt).first()
        return bool(row.inserted) if row is not None else False


def dedup_orc_certificates(
    engine: Engine,
    batch_size: int = 500,
    progress_callback=None,
) -> dict:
    """Collapse runs of byte-identical ORC certificate snapshots in place.

    For each (ref_no, country_id), walks rows in snapshot_date order. Any row
    whose content (yacht_name, sail_no, class_name, daily fingerprint) matches
    the immediately preceding "run-start" row is deleted; the run-start row
    keeps the earliest snapshot_date of the run (which it already has).

    Idempotent: a subsequent run finds no duplicates and deletes nothing.

    Returns: {"groups_scanned": int, "rows_deleted": int, "rows_before": int,
              "rows_after": int}
    """
    with engine.connect() as conn:
        rows_before = conn.execute(text("SELECT COUNT(*) FROM orc_certificates")).scalar() or 0
        groups = conn.execute(
            text(
                """
                SELECT ref_no, country_id, COUNT(*) AS n
                FROM orc_certificates
                GROUP BY ref_no, country_id
                HAVING COUNT(*) > 1
                ORDER BY ref_no, country_id
                """
            )
        ).fetchall()

    total_groups = len(groups)
    groups_scanned = 0
    rows_deleted = 0
    to_delete: list[int] = []

    def _flush(ids: list[int]) -> int:
        if not ids:
            return 0
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM orc_certificates WHERE id = ANY(:ids)"),
                {"ids": ids},
            )
        return len(ids)

    for ref_no, country_id, _n in groups:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, snapshot_date, yacht_name, sail_no, class_name, raw_data
                    FROM orc_certificates
                    WHERE ref_no = :ref_no AND country_id = :country_id
                    ORDER BY snapshot_date ASC, id ASC
                    """
                ),
                {"ref_no": ref_no, "country_id": country_id},
            ).mappings().fetchall()

        # Walk forward, collapsing duplicates against the current run-start row.
        run_start = dict(rows[0])
        for row in rows[1:]:
            row_d = dict(row)
            if orc_content_matches(
                run_start,
                row_d.get("yacht_name"),
                row_d.get("sail_no"),
                row_d.get("class_name"),
                row_d.get("raw_data"),
            ):
                to_delete.append(row_d["id"])
            else:
                run_start = row_d

        groups_scanned += 1

        if len(to_delete) >= batch_size:
            rows_deleted += _flush(to_delete)
            to_delete = []

        if progress_callback and groups_scanned % 100 == 0:
            progress_callback(groups_scanned, total_groups, rows_deleted)

    rows_deleted += _flush(to_delete)

    with engine.connect() as conn:
        rows_after = conn.execute(text("SELECT COUNT(*) FROM orc_certificates")).scalar() or 0

    return {
        "groups_scanned": groups_scanned,
        "rows_deleted": rows_deleted,
        "rows_before": rows_before,
        "rows_after": rows_after,
    }


def upsert_orc_snapshot(
    engine: Engine,
    country_id: str,
    snapshot_date: date,
    records_found: int | None = None,
    raw_json_path: str | None = None,
) -> int:
    """Insert or update an ORC snapshot metadata record."""
    values = dict(
        country_id=country_id,
        snapshot_date=snapshot_date,
        records_found=records_found,
        raw_json_path=raw_json_path,
    )
    stmt = pg_insert(ORCSnapshot.__table__).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="orc_snapshots_country_id_snapshot_date_key",
        set_={
            "records_found": stmt.excluded.records_found,
            "raw_json_path": stmt.excluded.raw_json_path,
        },
    )
    stmt = stmt.returning(ORCSnapshot.__table__.c.id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.scalar_one()


# ---------------------------------------------------------------------------
# Ingestion logging
# ---------------------------------------------------------------------------


def log_ingestion_start(
    engine: Engine,
    source: str,
    metadata: dict | None = None,
) -> int:
    """Log the start of a scraper run. Returns the log entry id."""
    stmt = pg_insert(IngestionLog.__table__).values(
        source=source,
        started_at=text("now()"),
        status="running",
        metadata=metadata,
    )
    stmt = stmt.returning(IngestionLog.__table__.c.id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return result.scalar_one()


def log_ingestion_end(
    engine: Engine,
    log_id: int,
    status: str = "completed",
    records_found: int | None = None,
    records_new: int | None = None,
    records_updated: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update an ingestion log entry with completion info.

    OPS-02-02: ``completed_with_errors``/``failed`` rows must never persist a
    blank ``error_message`` — see ``scrape_supervision.require_error_message``.
    """
    from irc_data.scrape_supervision import require_error_message

    error_message = require_error_message(status, error_message)
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE ingestion_log
                SET completed_at = now(),
                    status = :status,
                    records_found = :records_found,
                    records_new = :records_new,
                    records_updated = :records_updated,
                    error_message = :error_message
                WHERE id = :log_id
            """),
            {
                "log_id": log_id,
                "status": status,
                "records_found": records_found,
                "records_new": records_new,
                "records_updated": records_updated,
                "error_message": error_message,
            },
        )


# ---------------------------------------------------------------------------
# Materialized views
# ---------------------------------------------------------------------------


def refresh_materialized_views(engine: Engine) -> list[str]:
    """Refresh all materialized views. Returns list of refreshed view names."""
    views = [
        "mv_design_class_stats",
        "mv_country_fleet_trends",
        "mv_within_class_stats",
        "mv_boat_performance_summary",
        "mv_tcc_drift",
    ]
    refreshed = []
    with engine.begin() as conn:
        for view in views:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
                refreshed.append(view)
            except Exception:
                # CONCURRENTLY requires a unique index; fall back to blocking
                try:
                    conn.execute(text(f"REFRESH MATERIALIZED VIEW {view}"))
                    refreshed.append(view)
                except Exception as e:
                    print(f"  Warning: could not refresh {view}: {e}")
    return refreshed


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_boats(
    engine: Engine,
    country: str | None = None,
    design: str | None = None,
) -> list[dict]:
    """List boats with optional filters, including latest TCC."""
    query = """
        SELECT b.id, b.boat_name, b.sail_number, b.cert_number,
               b.design, b.country, b.year_built,
               t.tcc, t.non_spi_tcc, t.dlr, t.headsails, t.spinnakers,
               t.lh, t.beam, t.draft
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE 1=1
    """
    params: dict = {}
    if country:
        query += " AND b.country = :country"
        params["country"] = country
    if design:
        query += " AND b.design ILIKE :design"
        params["design"] = f"%{design}%"
    query += " ORDER BY t.tcc ASC NULLS LAST"

    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        return [dict(row._mapping) for row in result]


def get_boat_detail(engine: Engine, sail_number: str) -> dict | None:
    """Get full detail for a boat by sail number."""
    query = """
        SELECT b.*, t.tcc, t.non_spi_tcc, t.dlr, t.crew,
               t.headsails, t.flying_headsails, t.spinnakers,
               t.lh AS tcc_lh, t.beam AS tcc_beam, t.draft AS tcc_draft,
               t.single_furling_headsail, t.series_date, t.age_date,
               t.racing_area, t.stix AS tcc_stix, t.avs AS tcc_avs,
               t.category, t.snapshot_date
        FROM boats b
        LEFT JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE b.sail_number = :sail_number
        LIMIT 1
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"sail_number": sail_number})
        row = result.first()
        if row is None:
            return None
        return dict(row._mapping)


def get_boat_snapshots(engine: Engine, boat_id: int) -> list[dict]:
    """Get all TCC snapshots for a boat, ordered by date."""
    query = """
        SELECT * FROM tcc_snapshots
        WHERE boat_id = :boat_id
        ORDER BY snapshot_date
    """
    with engine.connect() as conn:
        result = conn.execute(text(query), {"boat_id": boat_id})
        return [dict(row._mapping) for row in result]


def find_boat_by_sail_number(engine: Engine, sail_number: str) -> int | None:
    """Find a boat ID by sail number (exact match)."""
    query = "SELECT id FROM boats WHERE sail_number = :sail_number LIMIT 1"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"sail_number": sail_number})
        row = result.first()
        return row[0] if row else None


def find_boat_by_cert_number(engine: Engine, cert_number: str) -> int | None:
    """Find a boat ID by cert number."""
    query = "SELECT id FROM boats WHERE cert_number = :cert_number LIMIT 1"
    with engine.connect() as conn:
        result = conn.execute(text(query), {"cert_number": cert_number})
        row = result.first()
        return row[0] if row else None


def get_stats(engine: Engine) -> dict:
    """Get summary statistics."""
    with engine.connect() as conn:
        boats = conn.execute(text("SELECT COUNT(*) FROM boats")).scalar()
        snapshots = conn.execute(text("SELECT COUNT(*) FROM tcc_snapshots")).scalar()
        certs = conn.execute(text("SELECT COUNT(*) FROM irc_certificates")).scalar()
        races = conn.execute(text("SELECT COUNT(*) FROM race_results")).scalar()
        countries = conn.execute(
            text("SELECT COUNT(DISTINCT country) FROM boats WHERE country IS NOT NULL")
        ).scalar()
        designs = conn.execute(
            text("SELECT COUNT(DISTINCT design) FROM boats WHERE design IS NOT NULL")
        ).scalar()

        # ORC stats (gracefully handle table not existing yet)
        try:
            orc_certs = conn.execute(text("SELECT COUNT(*) FROM orc_certificates")).scalar()
        except Exception:
            orc_certs = 0

    return {
        "boats": boats,
        "snapshots": snapshots,
        "irc_certificates": certs,
        "race_results": races,
        "countries": countries,
        "designs": designs,
        "orc_certificates": orc_certs,
    }
