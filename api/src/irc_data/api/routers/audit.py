import json
from datetime import datetime
from typing import Any, List, Optional
import io
import csv

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin

router = APIRouter(prefix="/admin/audit-log")

class AuditItem(BaseModel):
    id: int
    created_at: datetime
    actor: Optional[str]
    action: str
    table: str
    pk: str
    before: Optional[Any]
    after: Optional[Any]
    source: Optional[str]

class AuditResponse(BaseModel):
    items: List[AuditItem]
    next_cursor: Optional[int]


def _build_audit_query(
    db: Engine,
    actor: Optional[str] = None,
    table: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    q: Optional[str] = None,
    cursor: Optional[int] = None,
    is_count: bool = False,
) -> tuple[str, dict[str, Any]]:
    # admin_edits columns: id, edited_at, who, table_name, pk_value, column_name, old_value, new_value
    
    where_clauses = ["1=1"]
    params = {}
    
    if actor:
        where_clauses.append("who = :actor")
        params["actor"] = actor
    if table:
        where_clauses.append("table_name = :table")
        params["table"] = table
    if action:
        where_clauses.append("column_name = :action")
        params["action"] = action
    if since:
        where_clauses.append("edited_at >= :since")
        params["since"] = since
    if until:
        where_clauses.append("edited_at <= :until")
        params["until"] = until
    if q:
        if db.dialect.name == "postgresql":
            where_clauses.append("(old_value ILIKE :q OR new_value ILIKE :q)")
        else:
            where_clauses.append("(LOWER(old_value) LIKE LOWER(:q) OR LOWER(new_value) LIKE LOWER(:q))")
        params["q"] = f"%{q}%"
        
    if cursor and not is_count:
        where_clauses.append("id < :cursor")
        params["cursor"] = cursor
        
    where_sql = " AND ".join(where_clauses)
    
    if is_count:
        sql = f"SELECT COUNT(*) FROM admin_edits WHERE {where_sql}"
    else:
        sql = f"""
            SELECT id, edited_at, who, table_name, pk_value, column_name, old_value, new_value
            FROM admin_edits
            WHERE {where_sql}
            ORDER BY edited_at DESC, id DESC
        """
        
    return sql, params


def _parse_row(row: tuple) -> dict[str, Any]:
    # row: id, edited_at, who, table_name, pk_value, column_name, old_value, new_value
    try:
        before = json.loads(row[6]) if row[6] else None
    except Exception:
        before = row[6]
        
    try:
        after = json.loads(row[7]) if row[7] else None
    except Exception:
        after = row[7]
        
    return {
        "id": row[0],
        "created_at": row[1],
        "actor": row[2],
        "table": row[3],
        "pk": row[4],
        "action": row[5],
        "before": before,
        "after": after,
        "source": None,
    }


@router.get("", response_model=AuditResponse)
def get_audit_log(
    actor: Optional[str] = None,
    table: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    q: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = Query(default=50, le=200),
    authorization: str = Header(default=""),
    db: Engine = Depends(get_db),
):
    _verify_admin(authorization)
    
    # Try to ensure table exists in case no action was ever logged
    try:
        with db.connect() as conn:
            conn.execute(text("SELECT 1 FROM admin_edits LIMIT 1"))
    except Exception:
        return AuditResponse(items=[], next_cursor=None)
        
    sql, params = _build_audit_query(db, actor, table, action, since, until, q, cursor)
    
    # Add limit + 1 to check for next page
    sql += f" LIMIT {limit + 1}"
    
    with db.connect() as conn:
        result = conn.execute(text(sql), params).fetchall()
        
    has_next = len(result) > limit
    rows = result[:limit]
    
    items = [_parse_row(r) for r in rows]
    
    next_cursor = None
    if has_next and items:
        next_cursor = items[-1]["id"]
        
    return AuditResponse(items=items, next_cursor=next_cursor)


@router.get("/export")
def export_audit_log(
    actor: Optional[str] = None,
    table: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    q: Optional[str] = None,
    authorization: str = Header(default=""),
    db: Engine = Depends(get_db),
):
    _verify_admin(authorization)
    
    try:
        with db.connect() as conn:
            conn.execute(text("SELECT 1 FROM admin_edits LIMIT 1"))
    except Exception:
        return Response(content="id,created_at,actor,action,table,pk,before,after,source\n", media_type="text/csv")

    sql, params = _build_audit_query(db, actor, table, action, since, until, q)
    
    # No limit for export, but we might want to cap it to e.g. 10000 or stream it
    # For now, just execute and return as string
    with db.connect() as conn:
        result = conn.execute(text(sql), params).fetchall()
        
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "created_at", "actor", "action", "table", "pk", "before", "after", "source"])
    
    for row in result:
        item = _parse_row(row)
        
        created_at_str = ""
        if item["created_at"]:
            if isinstance(item["created_at"], str):
                created_at_str = item["created_at"]
            else:
                created_at_str = item["created_at"].isoformat()
                
        writer.writerow([
            item["id"],
            created_at_str,
            item["actor"] or "",
            item["action"] or "",
            item["table"] or "",
            item["pk"] or "",
            json.dumps(item["before"]) if item["before"] is not None else "",
            json.dumps(item["after"]) if item["after"] is not None else "",
            item["source"] or "",
        ])
        
    return Response(content=output.getvalue(), media_type="text/csv")
