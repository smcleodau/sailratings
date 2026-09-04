"""Duplicate-boats review queue API (AD-01-14).

Gives the pending ``dupe_review_queue`` clusters a human verdict on the
tables that already exist, without waiting for the DP-04 contracts
(AD-01-03/04 re-base onto this surface later):

  GET  /admin/dupes                — pending clusters grouped by cluster_id,
                                     boats ordered by evidence, filters
                                     (tier, size, country) + cursor paging
  GET  /admin/dupes/meta           — distinct tiers / sizes / countries for
                                     the filter chips
  POST /admin/dupes/clusters/{cluster_id}/merge
                                   — re-point every FK table to the chosen
                                     winner in one transaction (re-uses the
                                     sail-number merge routine in
                                     irc_data.operations.boat_merge), write
                                     one boat_merges row per loser with the
                                     loser_snapshot jsonb, set the queue
                                     rows verdict=MERGED with reviewed_by /
                                     reviewed_at, and audit to admin_edits
  POST /admin/dupes/clusters/{cluster_id}/not-dupe
                                   — write boat_not_dupe and set verdict
                                     NOT_DUPE with the reason
  POST /admin/dupes/clusters/{cluster_id}/skip
                                   — set verdict SKIPPED (the cluster stays
                                     out of the pending queue until a later
                                     triage pass re-queues it)
  GET  /admin/dupes/history        — merge history from boat_merges joined
                                     with boat_not_dupe

Verdict state machine (per cluster): PENDING → MERGED | NOT_DUPE | SKIPPED.
Deciding an already-decided cluster returns 409.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.operations import boat_merge

router = APIRouter(prefix="/admin/dupes", tags=["Admin", "Dupes"])

SCHEMA_VERSION = "dupe-review-v1"

# Verdicts written by this surface.
VERDICT_MERGED = "MERGED"
VERDICT_NOT_DUPE = "NOT_DUPE"
VERDICT_SKIPPED = "SKIPPED"

# Not-dupe reasons offered by the screen's footer select.  The reason travels
# to boat_not_dupe.reason / dupe_review_queue.verdict_note verbatim.
NOT_DUPE_REASONS: tuple[str, ...] = (
    "different_design",
    "different_year",
    "different_region",
    "name_coincidence",
    "other",
)

_MERGE_AUDIT_TABLE = "dupe_review_queue"
_ADMIN_EDITS_DDL = """
CREATE TABLE IF NOT EXISTS admin_edits (
    id          BIGSERIAL PRIMARY KEY,
    edited_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    who         TEXT,
    table_name  TEXT NOT NULL,
    pk_value    TEXT NOT NULL,
    column_name TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
)
"""
_ADMIN_EDITS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS admin_edits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    edited_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    who         TEXT,
    table_name  TEXT NOT NULL,
    pk_value    TEXT NOT NULL,
    column_name TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT
)
"""


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class MergeIn(BaseModel):
    winner_id: int
    note: str | None = None
    reviewed_by: str | None = Field(default=None, max_length=200)


class NotDupeIn(BaseModel):
    reason: str = Field(min_length=1, max_length=120)
    note: str | None = None
    reviewed_by: str | None = Field(default=None, max_length=200)


class SkipIn(BaseModel):
    note: str | None = None
    reviewed_by: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _reviewer(authorization: str | None, reviewed_by: str | None) -> str:
    """The identity recorded on the verdict: explicit reviewer, else the
    bearer-token holder identified as the shared admin credential."""
    if reviewed_by and reviewed_by.strip():
        return reviewed_by.strip()
    return "admin"


def _jsonable(value: Any) -> Any:
    import datetime as _dt
    import decimal as _decimal

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _encode_cursor(cluster_id: str) -> str:
    return base64.urlsafe_b64encode(cluster_id.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> str:
    try:
        raw = cursor.encode("ascii")
        # Reject anything outside the urlsafe alphabet (urlsafe_b64decode has
        # no validate= flag — it silently drops bad characters).
        if not raw or not all(
            chr(c).isalnum() or chr(c) in "-_=" for c in raw
        ):
            raise binascii.Error("outside urlsafe alphabet")
        return base64.urlsafe_b64decode(raw).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def _ensure_admin_edits(conn: Connection) -> None:
    """The admin_tables router creates admin_edits lazily on first edit; a
    dupe merge may be the very first audited write on a fresh DB, so create
    it here too (idempotent)."""
    try:
        conn.execute(
            text(
                _ADMIN_EDITS_DDL_SQLITE
                if conn.dialect.name == "sqlite"
                else _ADMIN_EDITS_DDL
            )
        )
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE admin_edits ADD COLUMN who TEXT"))
    except Exception:
        pass


def _write_admin_edit(
    conn: Connection,
    pk_value: str,
    column_name: str,
    old_value: str | None,
    new_value: str | None,
    who: str | None = None,
) -> None:
    try:
        conn.execute(
            text(
                "INSERT INTO admin_edits (who, table_name, pk_value, column_name, "
                "old_value, new_value) VALUES (:w, :t, :pk, :c, :old, :new)"
            ),
            {
                "w": who,
                "t": _MERGE_AUDIT_TABLE,
                "pk": pk_value,
                "c": column_name,
                "old": old_value,
                "new": new_value,
            },
        )
    except Exception:
        conn.execute(
            text(
                "INSERT INTO admin_edits (table_name, pk_value, column_name, "
                "old_value, new_value) VALUES (:t, :pk, :c, :old, :new)"
            ),
            {
                "t": _MERGE_AUDIT_TABLE,
                "pk": pk_value,
                "c": column_name,
                "old": old_value,
                "new": new_value,
            },
        )


# ---------------------------------------------------------------------------
# Queue reads
# ---------------------------------------------------------------------------

_QUEUE_COLUMNS = (
    "id", "cluster_id", "tier", "boat_id", "boat_name", "country",
    "sail_number", "design", "year_built", "race_results", "cert_count",
    "latest_activity", "owner", "cluster_size", "why",
    "verdict", "verdict_note", "reviewed_at", "reviewed_by",
)


def _fetch_cluster_rows(
    conn: Connection, cluster_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            f"SELECT {', '.join(_QUEUE_COLUMNS)} FROM dupe_review_queue "
            "WHERE cluster_id = :cid ORDER BY id"
        ),
        {"cid": cluster_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def _pending_cluster_ids(
    conn: Connection,
    tier: str | None,
    size: int | None,
    country: str | None,
    after: str | None,
    limit: int,
) -> list[str]:
    """Distinct cluster_ids with ≥1 PENDING row, page of ``limit`` + 1."""
    where = ["verdict = 'PENDING'"]
    params: dict[str, Any] = {"lim": limit + 1}
    if tier:
        where.append("tier = :tier")
        params["tier"] = tier
    if size:
        where.append("cluster_size = :size")
        params["size"] = size
    if country:
        where.append("country = :country")
        params["country"] = country
    if after is not None:
        where.append("cluster_id > :after")
        params["after"] = after
    rows = conn.execute(
        text(
            "SELECT cluster_id FROM dupe_review_queue "
            f"WHERE {' AND '.join(where)} "
            "GROUP BY cluster_id ORDER BY cluster_id LIMIT :lim"
        ),
        params,
    ).fetchall()
    return [r[0] for r in rows]


def _count_filtered_clusters(
    conn: Connection,
    tier: str | None,
    size: int | None,
    country: str | None,
) -> int:
    where = ["verdict = 'PENDING'"]
    params: dict[str, Any] = {}
    if tier:
        where.append("tier = :tier")
        params["tier"] = tier
    if size:
        where.append("cluster_size = :size")
        params["size"] = size
    if country:
        where.append("country = :country")
        params["country"] = country
    return conn.execute(
        text(
            "SELECT COUNT(DISTINCT cluster_id) FROM dupe_review_queue "
            f"WHERE {' AND '.join(where)}"
        ),
        params,
    ).scalar() or 0


def _evidence_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
    """Order boats by evidence: race results, then certs, then recency, then
    lowest boat id (the longest-lived record).  The highest-scoring boat is
    the pre-selected merge target on the screen."""
    latest = row.get("latest_activity")
    recency = latest.toordinal() if hasattr(latest, "toordinal") else 0
    return (
        -(row.get("race_results") or 0),
        -(row.get("cert_count") or 0),
        -recency,
        row.get("boat_id") or 0,
    )


def _cluster_payload(
    cluster_id: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    boats = sorted(rows, key=_evidence_score)
    return {
        "cluster_id": cluster_id,
        "tier": rows[0]["tier"],
        "size": rows[0]["cluster_size"],
        "countries": sorted({r["country"] for r in rows if r.get("country")}),
        "boats": [{k: _jsonable(v) for k, v in r.items()} for r in boats],
    }


# ---------------------------------------------------------------------------
# GET /admin/dupes — the pending queue
# ---------------------------------------------------------------------------


@router.get("")
def list_dupe_clusters(
    tier: str | None = Query(None, description="Filter by tier (B, D, …)"),
    size: int | None = Query(None, ge=2, le=10, description="Cluster size"),
    country: str | None = Query(None, description="Filter by country code"),
    cursor: str | None = Query(None, description="Opaque page cursor"),
    limit: int = Query(20, ge=1, le=100),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    _verify_admin(authorization)
    after = _decode_cursor(cursor) if cursor else None

    with engine.connect() as conn:
        cluster_ids = _pending_cluster_ids(conn, tier, size, country, after, limit)
        page_ids = cluster_ids[:limit]
        clusters = []
        for cid in page_ids:
            rows = [r for r in _fetch_cluster_rows(conn, cid) if r["verdict"] == "PENDING"]
            if not rows:
                continue
            clusters.append(_cluster_payload(cid, rows))
        total = _count_filtered_clusters(conn, tier, size, country)
        total_pending = _count_filtered_clusters(conn, None, None, None)

    next_cursor = None
    if len(cluster_ids) > limit and page_ids:
        next_cursor = _encode_cursor(page_ids[-1])

    return {
        "schema_version": SCHEMA_VERSION,
        "clusters": clusters,
        "next_cursor": next_cursor,
        "total": total,
        "pending_total": total_pending,
        "filters": {"tier": tier, "size": size, "country": country},
    }


@router.get("/meta")
def dupe_filter_meta(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Distinct tiers / cluster sizes / countries with pending rows — the
    filter-chip vocabulary for the screen."""
    _verify_admin(authorization)
    with engine.connect() as conn:
        tiers = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT tier FROM dupe_review_queue "
                    "WHERE verdict = 'PENDING' ORDER BY tier"
                )
            ).fetchall()
        ]
        sizes = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT cluster_size FROM dupe_review_queue "
                    "WHERE verdict = 'PENDING' ORDER BY cluster_size"
                )
            ).fetchall()
        ]
        countries = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT country FROM dupe_review_queue "
                    "WHERE verdict = 'PENDING' AND country IS NOT NULL "
                    "ORDER BY country"
                )
            ).fetchall()
        ]
    return {
        "tiers": tiers,
        "sizes": sizes,
        "countries": countries,
        "not_dupe_reasons": list(NOT_DUPE_REASONS),
    }


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


def _lock_pending_rows(
    conn: Connection, cluster_id: str
) -> list[dict[str, Any]]:
    """All queue rows for the cluster, row-locked on Postgres.

    Raises 404 when the cluster is unknown and 409 when it has already been
    decided (no PENDING rows left) — repeating a merge is a conflict.
    """
    rows = _fetch_cluster_rows(conn, cluster_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown cluster {cluster_id!r}")
    if conn.dialect.name == "postgresql":
        conn.execute(
            text(
                "SELECT id FROM dupe_review_queue WHERE cluster_id = :cid "
                "FOR UPDATE"
            ),
            {"cid": cluster_id},
        )
    pending = [r for r in rows if r["verdict"] == "PENDING"]
    if not pending:
        verdicts = sorted({r["verdict"] for r in rows})
        raise HTTPException(
            status_code=409,
            detail=(
                f"cluster {cluster_id!r} already decided "
                f"(verdicts: {', '.join(verdicts)})"
            ),
        )
    return rows


def _set_verdict(
    conn: Connection,
    rows: list[dict[str, Any]],
    verdict: str,
    reviewer: str,
    note: str | None,
) -> list[int]:
    """Set verdict/reviewed_* on the cluster's PENDING rows; returns ids."""
    ids = [r["id"] for r in rows if r["verdict"] == "PENDING"]
    for qid in ids:
        conn.execute(
            text(
                "UPDATE dupe_review_queue SET verdict = :v, "
                "verdict_note = :note, reviewed_at = CURRENT_TIMESTAMP, "
                "reviewed_by = :who WHERE id = :id"
            ),
            {"v": verdict, "note": note, "who": reviewer, "id": qid},
        )
    return ids


def _audit_verdict(
    conn: Connection,
    queue_ids: list[int],
    reviewer: str,
    verdict: str,
    detail: str,
) -> None:
    _ensure_admin_edits(conn)
    for qid in queue_ids:
        _write_admin_edit(conn, str(qid), "verdict", "PENDING", verdict, reviewer)
    _write_admin_edit(conn, ",".join(str(i) for i in queue_ids), "reviewed_by", None, reviewer, reviewer)
    if detail:
        _write_admin_edit(conn, ",".join(str(i) for i in queue_ids), "verdict_note", None, detail, reviewer)


# ---------------------------------------------------------------------------
# POST …/merge — the transactional merge
# ---------------------------------------------------------------------------


@router.post("/clusters/{cluster_id}/merge")
def merge_cluster_endpoint(
    cluster_id: str,
    body: MergeIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    _verify_admin(authorization)
    reviewer = _reviewer(authorization, body.reviewed_by)

    with engine.begin() as conn:
        rows = _lock_pending_rows(conn, cluster_id)
        pending = [r for r in rows if r["verdict"] == "PENDING"]
        pending_ids = sorted({r["boat_id"] for r in pending})

        if body.winner_id not in pending_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"winner_id {body.winner_id} is not a pending member of "
                    f"cluster {cluster_id!r} (members: {pending_ids})"
                ),
            )
        loser_ids = [b for b in pending_ids if b != body.winner_id]
        if not loser_ids:
            raise HTTPException(
                status_code=422,
                detail=f"cluster {cluster_id!r} has no other pending member to merge",
            )

        try:
            report = boat_merge.merge_boat_records(
                conn, cluster_id, body.winner_id, loser_ids
            )
        except boat_merge.MergeError as exc:
            # engine.begin() rolls the whole transaction back on the way out.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        queue_ids = _set_verdict(
            conn, rows, VERDICT_MERGED, reviewer, body.note
        )
        _audit_verdict(
            conn, queue_ids, reviewer, VERDICT_MERGED,
            body.note or f"merged into boat {body.winner_id}",
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "cluster_id": cluster_id,
        "verdict": VERDICT_MERGED,
        "winner_id": report.winner_id,
        "loser_ids": report.loser_ids,
        "rows_repointed": report.rows_repointed,
        "collisions_resolved": report.collisions_resolved,
        "queue_rows": queue_ids,
        "reviewed_by": reviewer,
    }


# ---------------------------------------------------------------------------
# POST …/not-dupe
# ---------------------------------------------------------------------------


@router.post("/clusters/{cluster_id}/not-dupe")
def not_dupe_endpoint(
    cluster_id: str,
    body: NotDupeIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    _verify_admin(authorization)
    reviewer = _reviewer(authorization, body.reviewed_by)

    with engine.begin() as conn:
        rows = _lock_pending_rows(conn, cluster_id)
        pending = [r for r in rows if r["verdict"] == "PENDING"]
        boat_ids = sorted({r["boat_id"] for r in pending})

        note = body.reason if not body.note else f"{body.reason}: {body.note}"
        if conn.dialect.name == "sqlite":
            conn.execute(
                text(
                    "INSERT INTO boat_not_dupe (cluster_key, boat_ids, reason) "
                    "VALUES (:ck, :ids, :reason)"
                ),
                {"ck": cluster_id, "ids": json.dumps(boat_ids), "reason": body.reason},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO boat_not_dupe (cluster_key, boat_ids, reason) "
                    "VALUES (:ck, :ids, :reason)"
                ),
                {"ck": cluster_id, "ids": boat_ids, "reason": body.reason},
            )

        queue_ids = _set_verdict(conn, rows, VERDICT_NOT_DUPE, reviewer, note)
        _audit_verdict(conn, queue_ids, reviewer, VERDICT_NOT_DUPE, note)

    return {
        "schema_version": SCHEMA_VERSION,
        "cluster_id": cluster_id,
        "verdict": VERDICT_NOT_DUPE,
        "boat_ids": boat_ids,
        "reason": body.reason,
        "queue_rows": queue_ids,
        "reviewed_by": reviewer,
    }


# ---------------------------------------------------------------------------
# POST …/skip
# ---------------------------------------------------------------------------


@router.post("/clusters/{cluster_id}/skip")
def skip_endpoint(
    cluster_id: str,
    body: SkipIn,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    _verify_admin(authorization)
    reviewer = _reviewer(authorization, body.reviewed_by)

    with engine.begin() as conn:
        rows = _lock_pending_rows(conn, cluster_id)
        queue_ids = _set_verdict(
            conn, rows, VERDICT_SKIPPED, reviewer, body.note or "skipped for now"
        )
        _audit_verdict(conn, queue_ids, reviewer, VERDICT_SKIPPED, body.note or "")

    return {
        "schema_version": SCHEMA_VERSION,
        "cluster_id": cluster_id,
        "verdict": VERDICT_SKIPPED,
        "queue_rows": queue_ids,
        "reviewed_by": reviewer,
    }


# ---------------------------------------------------------------------------
# GET /admin/dupes/history — merge + not-dupe history
# ---------------------------------------------------------------------------


@router.get("/history")
def dupe_history(
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, description="Opaque page cursor"),
    kind: Literal["all", "merged", "not_dupe"] = Query("all"),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Merge history from boat_merges (loser snapshots included) interleaved
    with boat_not_dupe, newest first.  Cursor is the last seen row key."""
    _verify_admin(authorization)
    after = _decode_cursor(cursor) if cursor else None

    with engine.connect() as conn:
        entries: list[dict[str, Any]] = []
        if kind in ("all", "merged"):
            merges = conn.execute(
                text(
                    "SELECT m.id, m.merged_at, m.winner_id, m.loser_id, "
                    "m.cluster_key, m.loser_snapshot, "
                    "b.boat_name AS winner_name, b.sail_number AS winner_sail, "
                    "b.country AS winner_country "
                    "FROM boat_merges m LEFT JOIN boats b ON b.id = m.winner_id "
                    "ORDER BY m.id DESC LIMIT :lim"
                ),
                {"lim": limit * 2},
            ).mappings().all()
            for m in merges:
                snap = m["loser_snapshot"]
                if isinstance(snap, str):
                    try:
                        snap = json.loads(snap)
                    except ValueError:
                        snap = None
                loser_boat = (snap or {}).get("boat") or {}
                entries.append(
                    {
                        "kind": "merged",
                        "at": _jsonable(m["merged_at"]),
                        "cluster_key": m["cluster_key"],
                        "winner_id": m["winner_id"],
                        "winner_name": m["winner_name"],
                        "winner_sail": m["winner_sail"],
                        "winner_country": m["winner_country"],
                        "loser_id": m["loser_id"],
                        "loser_name": loser_boat.get("boat_name"),
                        "loser_sail": loser_boat.get("sail_number"),
                        "loser_snapshot": _jsonable(snap),
                        "reviewed_by": None,
                    }
                )
        if kind in ("all", "not_dupe"):
            not_dupes = conn.execute(
                text(
                    "SELECT n.id, n.marked_at, n.cluster_key, n.boat_ids, "
                    "n.reason, "
                    "(SELECT q.reviewed_by FROM dupe_review_queue q "
                    " WHERE q.cluster_id = n.cluster_key AND q.reviewed_by IS NOT NULL "
                    " ORDER BY q.reviewed_at DESC LIMIT 1) AS reviewed_by "
                    "FROM boat_not_dupe n ORDER BY n.id DESC LIMIT :lim"
                ),
                {"lim": limit * 2},
            ).mappings().all()
            for n in not_dupes:
                boat_ids = n["boat_ids"]
                if isinstance(boat_ids, str):
                    try:
                        boat_ids = json.loads(boat_ids)
                    except ValueError:
                        boat_ids = []
                entries.append(
                    {
                        "kind": "not_dupe",
                        "at": _jsonable(n["marked_at"]),
                        "cluster_key": n["cluster_key"],
                        "boat_ids": list(boat_ids or []),
                        "reason": n["reason"],
                        "reviewed_by": n["reviewed_by"],
                    }
                )

    # Newest first across both sources; stable tie-break on cluster key.
    entries.sort(key=lambda e: (e["at"] or "", e["cluster_key"]), reverse=True)

    if after is not None:
        # Cursor: "<at>|<cluster_key>" — everything strictly newer was served.
        entries = [
            e for e in entries if f"{e['at'] or ''}|{e['cluster_key']}" < after
        ]

    page = entries[:limit]
    next_cursor = None
    if len(entries) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(f"{last['at'] or ''}|{last['cluster_key']}")

    return {
        "schema_version": SCHEMA_VERSION,
        "entries": page,
        "next_cursor": next_cursor,
    }
