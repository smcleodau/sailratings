"""Owner-input boat corrections.

Visitors submit corrections / additions to boat data (designer, builder,
year_built, design_canonical, or a brand-new class). Submissions land in
`boat_corrections` as `pending`. Stuart approves/rejects via /justin.
Approval applies the change to `boats` (or `design_classes` for new
class proposals) in a single transaction and marks the row `applied`.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

logger = logging.getLogger(__name__)

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


@router.post("/boats/{boat_id}/corrections", response_model=CorrectionResponse)
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
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            "status": r.status,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "review_notes": r.review_notes,
        }
        for r in rows
    ]


class ApproveBody(BaseModel):
    review_notes: str | None = None


@router.post("/admin/corrections/{correction_id}/approve")
def approve_correction(
    correction_id: int,
    body: ApproveBody = ApproveBody(),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
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

    try:
        with engine.begin() as conn:
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

    try:
        from irc_data.api.services.analytics_service import track
        track("correction_rejected", str(correction_id), {
            "correction_id": correction_id,
        })
    except Exception:
        pass

    return {"status": "rejected", "id": correction_id}
