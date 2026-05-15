"""Admin table browser + editor.

Provides paginated read access to every user table in the dev DB plus
in-place UPDATE for a curated set of tables (`policy.editable`). Big
scraper-owned tables (race_results, orc_certificates, certificates,
tcc_snapshots, orc_snapshots) are read-only by policy to prevent
accidents — the moderation queue in /admin/corrections is the right
surface for fixing those values.

All endpoints require the same Bearer admin password as the rest of
/admin/*. Every UPDATE is logged to `admin_edits` so we have a trail.
"""

import datetime as _dt
import decimal as _decimal
import json
import os
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/admin/tables", tags=["Admin", "Tables"])

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Per-table policy. Tables not listed are hidden from the UI entirely.
TABLE_POLICY: dict[str, dict] = {
    "boats":               {"editable": True,  "pk": "id"},
    "design_classes":      {"editable": True,  "pk": "id"},
    "boat_identities":     {"editable": True,  "pk": "id"},
    "boat_corrections":    {"editable": True,  "pk": "id"},
    "orders":              {"editable": True,  "pk": "id"},
    "admin_conversations": {"editable": True,  "pk": "id"},
    "admin_messages":      {"editable": True,  "pk": "id"},
    "ingestion_log":       {"editable": True,  "pk": "id"},
    "survey_responses":    {"editable": True,  "pk": "id"},
    "insight_cache":       {"editable": True,  "pk": "id"},
    "dupe_review_queue":   {"editable": True,  "pk": "id"},
    "admin_edits":         {"editable": False, "pk": "id"},
    "cert_probe_attempts": {"editable": False, "pk": "id"},
    "race_results":        {"editable": False, "pk": "id"},
    "orc_certificates":    {"editable": False, "pk": "id"},
    "orc_snapshots":       {"editable": False, "pk": "id"},
    "certificates":        {"editable": False, "pk": "id"},
    "tcc_snapshots":       {"editable": False, "pk": "id"},
}

# Columns that are always rejected on UPDATE (primary key, audit columns).
FORBIDDEN_COLS = {"id", "created_at", "updated_at"}


def _verify_admin(authorization: str | None) -> None:
    expected = f"Bearer {ADMIN_PASSWORD}"
    if not ADMIN_PASSWORD or not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _ensure_audit_table(engine: Engine) -> None:
    """Create the admin_edits audit log on first use. Idempotent."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_edits (
                id          BIGSERIAL PRIMARY KEY,
                edited_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                table_name  TEXT NOT NULL,
                pk_value    TEXT NOT NULL,
                column_name TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_admin_edits_table_pk "
            "ON admin_edits (table_name, pk_value, edited_at DESC)"
        ))


def _table_columns(engine: Engine, name: str) -> list[dict]:
    """Return ordered column metadata (name, type, nullable, has_default)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT column_name, data_type, is_nullable, column_default,
                       character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :n
                ORDER BY ordinal_position
            """),
            {"n": name},
        ).fetchall()
    return [
        {
            "name": r.column_name,
            "type": r.data_type,
            "nullable": r.is_nullable == "YES",
            "has_default": r.column_default is not None,
            "max_length": r.character_maximum_length,
        }
        for r in rows
    ]


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("")
def list_tables(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """List every user table with row count + size + edit policy."""
    _verify_admin(authorization)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
              relname AS name,
              n_live_tup AS rows,
              pg_total_relation_size(relid) AS total_bytes,
              pg_relation_size(relid) AS table_bytes,
              pg_total_relation_size(relid) - pg_relation_size(relid) AS index_bytes
            FROM pg_stat_user_tables
            ORDER BY relname
        """)).fetchall()

    out = []
    for r in rows:
        policy = TABLE_POLICY.get(r.name)
        if policy is None:
            # Hide unknown tables from the UI by default. They'll get added to
            # the policy when we want them visible.
            continue
        out.append({
            "name": r.name,
            "rows": r.rows,
            "total_bytes": r.total_bytes,
            "table_bytes": r.table_bytes,
            "index_bytes": r.index_bytes,
            "editable": policy.get("editable", False),
            "pk": policy.get("pk", "id"),
        })
    return {"tables": out}


@router.get("/{name}")
def get_rows(
    name: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    order_by: str | None = Query(None, description="Column name to order by"),
    order_dir: str = Query("desc", regex="^(asc|desc)$"),
    q: str | None = Query(None, description="Filter: column=value or column~text"),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Paginated rows from a single table.

    Filter syntax:
        column=value       — exact match
        column~text        — case-insensitive substring match (ILIKE %text%)
        column>value       — numeric / date >
        column<value       — numeric / date <
        column!=value      — exact mismatch (incl. NOT NULL)
        column:null        — IS NULL
        column:not_null    — IS NOT NULL
    """
    _verify_admin(authorization)
    policy = TABLE_POLICY.get(name)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Table {name!r} not visible")

    columns = _table_columns(engine, name)
    col_names = {c["name"] for c in columns}
    pk = policy.get("pk", "id")
    if pk not in col_names:
        raise HTTPException(status_code=500, detail=f"PK {pk!r} missing on {name}")

    # Build optional WHERE
    where_clause = ""
    params: dict[str, Any] = {}
    if q:
        ops = [
            ("!=", "<>"),
            ("~",  "ILIKE"),
            (">=", ">="),
            ("<=", "<="),
            ("=",  "="),
            (">",  ">"),
            ("<",  "<"),
            (":",  ":"),
        ]
        col, op_sql, val = None, None, None
        for marker, sql_op in ops:
            if marker in q:
                head, _, tail = q.partition(marker)
                head, tail = head.strip(), tail.strip()
                if head in col_names:
                    col, op_sql, val = head, sql_op, tail
                    break
        if not col:
            raise HTTPException(status_code=422, detail=f"Bad filter {q!r}")
        if op_sql == ":":
            if val == "null":
                where_clause = f"WHERE {col} IS NULL"
            elif val == "not_null":
                where_clause = f"WHERE {col} IS NOT NULL"
            else:
                raise HTTPException(status_code=422, detail=f"Bad filter {q!r}")
        elif op_sql == "ILIKE":
            where_clause = f"WHERE CAST({col} AS TEXT) ILIKE :_v"
            params["_v"] = f"%{val}%"
        else:
            where_clause = f"WHERE {col} {op_sql} :_v"
            params["_v"] = val

    # Order
    order_col = order_by if order_by in col_names else pk
    order_sql = f"ORDER BY {order_col} {order_dir.upper()}"

    sql = f"SELECT * FROM {name} {where_clause} {order_sql} LIMIT :_lim OFFSET :_off"
    count_sql = f"SELECT COUNT(*) AS n FROM {name} {where_clause}"

    with engine.connect() as conn:
        total = conn.execute(text(count_sql), params).scalar() or 0
        params2 = {**params, "_lim": limit, "_off": offset}
        rs = conn.execute(text(sql), params2).fetchall()

    return {
        "table": name,
        "editable": policy.get("editable", False),
        "pk": pk,
        "columns": columns,
        "rows": [
            {k: _jsonable(v) for k, v in dict(r._mapping).items()}
            for r in rs
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
        "order_by": order_col,
        "order_dir": order_dir,
        "q": q,
    }


class UpdateBody(BaseModel):
    column: str
    value: Any | None  # JSON-friendly; null allowed


@router.patch("/{name}/{pk_value}")
def update_cell(
    name: str,
    pk_value: str,
    body: UpdateBody,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Update a single column on a single row. Audited to admin_edits."""
    _verify_admin(authorization)
    policy = TABLE_POLICY.get(name)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Table {name!r} not visible")
    if not policy.get("editable", False):
        raise HTTPException(status_code=403, detail=f"Table {name!r} is read-only")
    if body.column in FORBIDDEN_COLS:
        raise HTTPException(status_code=400, detail=f"Column {body.column!r} is not editable")

    columns = _table_columns(engine, name)
    col_names = {c["name"] for c in columns}
    if body.column not in col_names:
        raise HTTPException(status_code=400, detail=f"Unknown column {body.column!r}")
    pk = policy.get("pk", "id")

    _ensure_audit_table(engine)

    with engine.begin() as conn:
        old = conn.execute(
            text(f"SELECT {body.column} AS v FROM {name} WHERE {pk} = :pk"),
            {"pk": pk_value},
        ).first()
        if not old:
            raise HTTPException(status_code=404, detail=f"Row {pk}={pk_value} not found")

        result = conn.execute(
            text(f"UPDATE {name} SET {body.column} = :v WHERE {pk} = :pk"),
            {"v": body.value, "pk": pk_value},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="No rows updated")

        conn.execute(
            text("""
                INSERT INTO admin_edits (table_name, pk_value, column_name, old_value, new_value)
                VALUES (:t, :pk, :c, :ov, :nv)
            """),
            {
                "t": name,
                "pk": str(pk_value),
                "c": body.column,
                "ov": None if old.v is None else str(old.v),
                "nv": None if body.value is None else str(body.value),
            },
        )

    return {
        "table": name,
        "pk": pk_value,
        "column": body.column,
        "old_value": _jsonable(old.v),
        "new_value": body.value,
    }


@router.get("/{name}/{pk_value}")
def get_row(
    name: str,
    pk_value: str,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Fetch one row by primary key."""
    _verify_admin(authorization)
    policy = TABLE_POLICY.get(name)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Table {name!r} not visible")
    pk = policy.get("pk", "id")

    with engine.connect() as conn:
        r = conn.execute(
            text(f"SELECT * FROM {name} WHERE {pk} = :pk"),
            {"pk": pk_value},
        ).first()
    if not r:
        raise HTTPException(status_code=404, detail=f"Row {pk}={pk_value} not found")
    return {
        "table": name,
        "pk": pk_value,
        "row": {k: _jsonable(v) for k, v in dict(r._mapping).items()},
        "columns": _table_columns(engine, name),
    }
