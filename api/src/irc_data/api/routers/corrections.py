"""Owner-input boat corrections.

Visitors submit corrections / additions to boat data (designer, builder,
year_built, design_canonical, or a brand-new class). Submissions land in
`boat_corrections` as `pending`. Stuart approves/rejects via /justin.
Approval applies the change to `boats` (or `design_classes` for new
class proposals) in a single transaction and marks the row `applied`.
"""

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

logger = logging.getLogger(__name__)

from irc_data.api.deps import get_optional_identity, CallerIdentity
from irc_data.api.audit import log_admin_action
router = APIRouter(tags=["Corrections"])

ALLOWED_FIELDS = {"designer", "builder", "year_built", "design_canonical", "new_design_class"}
MAX_VALUE_LEN = 200
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def _verify_admin(authorization: str | None) -> None:
    expected = f"Bearer {ADMIN_PASSWORD}"
    if not ADMIN_PASSWORD or not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Public submission ────────────────────────────────────────────────────


class CorrectionSubmission(BaseModel):
    field_name: str = Field(..., description="One of: designer, builder, year_built, design_canonical, new_design_class")
    proposed_value: str = Field(..., min_length=1, max_length=MAX_VALUE_LEN)
    submitted_email: str | None = Field(None, max_length=320)


class CorrectionResponse(BaseModel):
    id: int
    status: str


@router.post("/boats/{boat_id}/corrections", response_model=CorrectionResponse, status_code=201)
def submit_correction(
    boat_id: int,
    body: CorrectionSubmission,
    engine: Engine = Depends(get_db),
):
    """Submit a correction to a boat's data. Goes into the moderation queue."""
    if body.field_name not in ALLOWED_FIELDS:
        raise HTTPException(status_code=422, detail=f"field_name must be one of {sorted(ALLOWED_FIELDS)}")

    with engine.connect() as conn:
        boat = conn.execute(
            text("SELECT id, designer, builder, year_built, design_canonical FROM boats WHERE id = :id"),
            {"id": boat_id},
        ).first()
    if not boat:
        raise HTTPException(status_code=404, detail=f"Boat {boat_id} not found")

    current = None
    if body.field_name == "designer":
        current = boat.designer
    elif body.field_name == "builder":
        current = boat.builder
    elif body.field_name == "year_built":
        current = str(boat.year_built) if boat.year_built is not None else None
    elif body.field_name == "design_canonical":
        current = boat.design_canonical
    # new_design_class has no current value

    with engine.begin() as conn:
        row = conn.execute(
            text("""
                INSERT INTO boat_corrections (boat_id, field_name, current_value, proposed_value, submitted_email)
                VALUES (:boat_id, :field, :current, :proposed, :email)
                RETURNING id, status
            """),
            {
                "boat_id": boat_id,
                "field": body.field_name,
                "current": current,
                "proposed": body.proposed_value.strip()[:MAX_VALUE_LEN],
                "email": (body.submitted_email or None),
            },
        ).first()

    try:
        from irc_data.api.services.analytics_service import track
        track("correction_submitted", str(boat_id), {
            "field_name": body.field_name,
            "had_current_value": current is not None,
            "boat_id": boat_id,
        })
    except Exception:
        pass

    return CorrectionResponse(id=row.id, status=row.status)


# ── Admin moderation ─────────────────────────────────────────────────────


class CorrectionOut(BaseModel):
    id: int
    boat_id: int | None
    boat_name: str | None
    field_name: str
    current_value: str | None
    proposed_value: str
    submitted_email: str | None
    submitted_at: str
    status: str
    reviewed_at: str | None = None
    review_notes: str | None = None


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


@router.get("/admin/corrections")
def list_corrections(
    status: str = "pending",
    limit: int = 100,
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """List corrections for moderation. Default: pending only."""
    _verify_admin(authorization)
    limit = max(1, min(limit, 500))

    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT c.id, c.boat_id, b.boat_name, c.field_name, c.current_value,
                       c.proposed_value, c.submitted_email, c.submitted_at, c.status,
                       c.reviewed_at, c.review_notes
                FROM boat_corrections c
                LEFT JOIN boats b ON b.id = c.boat_id
                WHERE c.status = :status
                ORDER BY c.submitted_at DESC
                LIMIT :limit
            """),
            {"status": status, "limit": limit},
        ).fetchall()

    return [
        {
            "id": r.id,
            "boat_id": r.boat_id,
            "boat_name": r.boat_name,
            "field_name": r.field_name,
            "current_value": r.current_value,
            "proposed_value": r.proposed_value,
            "submitted_email": r.submitted_email,
            "submitted_at": _iso(r.submitted_at),
            "status": r.status,
            "reviewed_at": _iso(r.reviewed_at),
            "review_notes": r.review_notes,
        }
        for r in rows
    ]


def _target_column(field: str) -> str:
    """The `boats` column a correction field writes to."""
    return "design_canonical" if field == "new_design_class" else field


def _read_boat_value(conn, boat_id: int, column: str) -> str | None:
    if column not in {"designer", "builder", "year_built", "design_canonical"}:
        return None
    row = conn.execute(
        text(f"SELECT {column} FROM boats WHERE id = :id"), {"id": boat_id}
    ).first()
    if row is None or row[0] is None:
        return None
    return str(row[0])


class ApproveBody(BaseModel):
    review_notes: str | None = None


@router.post("/admin/corrections/{correction_id}/approve")
def approve_correction(
    correction_id: int,
    body: ApproveBody = ApproveBody(),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Apply the correction to the underlying data, then mark applied."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        c = conn.execute(
            text("SELECT id, boat_id, field_name, proposed_value, status FROM boat_corrections WHERE id = :id"),
            {"id": correction_id},
        ).first()

    if not c:
        raise HTTPException(status_code=404, detail="Correction not found")
    if c.status != "pending":
        raise HTTPException(status_code=409, detail=f"Correction is {c.status}, not pending")

    proposed = c.proposed_value
    field = c.field_name
    target_column = _target_column(field)
    before_value: str | None = None

    try:
        with engine.begin() as conn:
            # Capture the live value BEFORE overwriting it so revert can
            # restore exactly what was there (not the possibly-stale
            # current_value snapshot taken at submission time).
            if c.boat_id is not None:
                before_value = _read_boat_value(conn, c.boat_id, target_column)

            if field == "new_design_class":
                conn.execute(
                    text("""
                        INSERT INTO design_classes (name_canonical)
                        VALUES (:name)
                        ON CONFLICT (name_canonical) DO NOTHING
                    """),
                    {"name": proposed},
                )
                if c.boat_id is not None:
                    conn.execute(
                        text("UPDATE boats SET design_canonical = :v WHERE id = :id"),
                        {"v": proposed, "id": c.boat_id},
                    )
            elif field == "year_built":
                try:
                    year_val = int(proposed)
                except ValueError:
                    raise HTTPException(status_code=422, detail="year_built must be an integer")
                conn.execute(
                    text("UPDATE boats SET year_built = :v WHERE id = :id"),
                    {"v": year_val, "id": c.boat_id},
                )
            elif field in {"designer", "builder", "design_canonical"}:
                conn.execute(
                    text(f"UPDATE boats SET {field} = :v WHERE id = :id"),
                    {"v": proposed, "id": c.boat_id},
                )
            else:
                raise HTTPException(status_code=422, detail=f"Unknown field {field}")

            conn.execute(
                text("""
                    UPDATE boat_corrections
                    SET status = 'applied', reviewed_at = now(),
                        review_notes = :notes
                    WHERE id = :id
                """),
                {"id": correction_id, "notes": body.review_notes},
            )
        who = caller.email if caller and caller.email else "admin"
        log_admin_action(
            engine, who, "approve", "corrections", str(correction_id),
            before={"boat_id": c.boat_id, "column": target_column, "value": before_value},
            after={"status": "applied", "value": proposed, "notes": body.review_notes},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to apply correction {correction_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    try:
        from irc_data.api.services.analytics_service import track
        track("correction_approved", str(c.boat_id or correction_id), {
            "field_name": field,
            "correction_id": correction_id,
            "boat_id": c.boat_id,
        })
    except Exception:
        pass

    return {"status": "applied", "id": correction_id}


class RejectBody(BaseModel):
    review_notes: str | None = None


@router.post("/admin/corrections/{correction_id}/reject")
def reject_correction(
    correction_id: int,
    body: RejectBody = RejectBody(),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Mark a correction rejected. No data change."""
    _verify_admin(authorization)

    with engine.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE boat_corrections
                SET status = 'rejected', reviewed_at = now(),
                    review_notes = :notes
                WHERE id = :id AND status = 'pending'
                RETURNING id
            """),
            {"id": correction_id, "notes": body.review_notes},
        ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Correction not found or already reviewed")
    
    who = caller.email if caller and caller.email else "admin"
    log_admin_action(engine, who, "reject", "corrections", str(correction_id), after={"status": "rejected", "notes": body.review_notes})

    try:
        from irc_data.api.services.analytics_service import track
        track("correction_rejected", str(correction_id), {
            "correction_id": correction_id,
        })
    except Exception:
        pass

    return {"status": "rejected", "id": correction_id}


class RevertBody(BaseModel):
    review_notes: str | None = None


def _audited_before_value(engine: Engine, correction_id: int) -> tuple[bool, str | None]:
    """Return (found, value) from the latest 'approve' admin_edits row."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT old_value FROM admin_edits
                    WHERE table_name = 'corrections' AND pk_value = :pk
                      AND column_name = 'approve'
                    ORDER BY id DESC LIMIT 1
                """),
                {"pk": str(correction_id)},
            ).first()
        if row and row[0]:
            payload = json.loads(row[0])
            if isinstance(payload, dict) and "value" in payload:
                return True, payload["value"]
    except Exception as e:  # admin_edits missing / malformed - fall back
        logger.warning("Could not read approve audit for correction %s: %s", correction_id, e)
    return False, None


@router.post("/admin/corrections/{correction_id}/revert")
def revert_correction(
    correction_id: int,
    body: RevertBody = RevertBody(),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
    caller: CallerIdentity | None = Depends(get_optional_identity),
):
    """Undo an applied correction: restore the before value, mark reverted."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        c = conn.execute(
            text("""
                SELECT id, boat_id, field_name, current_value, proposed_value, status
                FROM boat_corrections WHERE id = :id
            """),
            {"id": correction_id},
        ).first()

    if not c:
        raise HTTPException(status_code=404, detail="Correction not found")
    if c.status != "applied":
        raise HTTPException(status_code=409, detail=f"Correction is {c.status}, not applied")

    found, restore = _audited_before_value(engine, correction_id)
    if not found:
        restore = c.current_value
    column = _target_column(c.field_name)

    try:
        with engine.begin() as conn:
            live_value = None
            if c.boat_id is not None:
                live_value = _read_boat_value(conn, c.boat_id, column)
                if column == "year_built":
                    restore_param = int(restore) if restore is not None else None
                else:
                    restore_param = restore
                conn.execute(
                    text(f"UPDATE boats SET {column} = :v WHERE id = :id"),
                    {"v": restore_param, "id": c.boat_id},
                )
            conn.execute(
                text("""
                    UPDATE boat_corrections
                    SET status = 'reverted', reviewed_at = now(),
                        review_notes = COALESCE(:notes, review_notes)
                    WHERE id = :id
                """),
                {"id": correction_id, "notes": body.review_notes},
            )
    except Exception as e:
        logger.error(f"Failed to revert correction {correction_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    who = caller.email if caller and caller.email else "admin"
    log_admin_action(
        engine, who, "revert", "corrections", str(correction_id),
        before={"boat_id": c.boat_id, "column": column, "value": live_value, "status": "applied"},
        after={"boat_id": c.boat_id, "column": column, "value": restore, "status": "reverted", "notes": body.review_notes},
    )

    try:
        from irc_data.api.services.analytics_service import track
        track("correction_reverted", str(c.boat_id or correction_id), {
            "field_name": c.field_name,
            "correction_id": correction_id,
            "boat_id": c.boat_id,
        })
    except Exception:
        pass

    return {"status": "reverted", "id": correction_id, "restored_value": restore}
