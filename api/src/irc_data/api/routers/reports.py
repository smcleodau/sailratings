"""Report access endpoints — serve generated reports and PDFs."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/{order_token}")
def get_report(
    order_token: str,
    engine: Engine = Depends(get_db),
):
    """Get report data for a paid order.

    Returns status, boat info, report content, and analytics data.
    Frontend polls this if status is 'pending' or 'paid' (still generating).
    """
    with engine.connect() as conn:
        order = conn.execute(
            text("""
                SELECT o.order_token, o.status, o.report_markdown, o.report_analytics,
                       o.pdf_path, o.created_at, o.paid_at, o.report_generated_at,
                       b.id AS boat_id, b.boat_name, b.sail_number,
                       COALESCE(b.design_canonical, b.design) AS design,
                       b.country,
                       t.tcc
                FROM orders o
                JOIN boats b ON b.id = o.boat_id
                LEFT JOIN LATERAL (
                    SELECT tcc FROM tcc_snapshots WHERE boat_id = b.id
                    ORDER BY snapshot_date DESC LIMIT 1
                ) t ON true
                WHERE o.order_token = :token
            """),
            {"token": order_token},
        ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Report not found")

    # Parse analytics if present
    analytics = order.report_analytics or {}

    return {
        "status": order.status,
        "boat": {
            "id": order.boat_id,
            "name": order.boat_name,
            "sail_number": order.sail_number,
            "design": order.design,
            "country": order.country,
            "tcc": float(order.tcc) if order.tcc else None,
        },
        "report_markdown": order.report_markdown if order.status == "generated" else None,
        "report_analytics": analytics if order.status == "generated" else None,
        "pdf_available": bool(order.pdf_path and Path(order.pdf_path).exists()),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "paid_at": order.paid_at.isoformat() if order.paid_at else None,
        "report_generated_at": (
            order.report_generated_at.isoformat() if order.report_generated_at else None
        ),
    }


@router.get("/{order_token}/pdf")
def get_report_pdf(
    order_token: str,
    engine: Engine = Depends(get_db),
):
    """Download the PDF report for a paid order."""
    with engine.connect() as conn:
        order = conn.execute(
            text("""
                SELECT o.pdf_path, o.status, b.boat_name
                FROM orders o
                JOIN boats b ON b.id = o.boat_id
                WHERE o.order_token = :token
            """),
            {"token": order_token},
        ).first()

    if not order:
        raise HTTPException(status_code=404, detail="Report not found")

    if order.status != "generated":
        raise HTTPException(status_code=409, detail="Report is still being generated")

    if not order.pdf_path or not Path(order.pdf_path).exists():
        raise HTTPException(status_code=404, detail="PDF not yet available")

    return FileResponse(
        order.pdf_path,
        media_type="application/pdf",
        filename=f"{order.boat_name} - IRC Rating Report.pdf",
    )
