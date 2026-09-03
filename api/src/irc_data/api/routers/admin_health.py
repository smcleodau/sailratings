"""AD-01-15 — data-health facts API.

Endpoints behind the admin credential, backing the AD-01 admin console's
data-health page (tables census + completeness meters):

  GET /v1/admin/health/tables
      The pg_stat census: every user table with its statistics-collector
      row estimate and on-disk size, ``rows = 0`` tables flagged, plus the
      built-never-written list (a user table with a synthetic-key build
      default that has recorded zero writes since it was built).

      Reads ``pg_stat_user_tables`` + catalog views only — never scans a
      base table, so it stays well inside the page's 200 ms budget.

  GET /v1/admin/health/completeness
      The nightly completeness meters.  Rendered entirely from the
      precomputed ``admin_metrics`` stream (via ``health_metric_latest``):
      % non-null for the boats core columns, the events venue-null rate,
      and a sample of raw event names exactly as ingested.  Returns
      ``available=False`` honestly when the nightly job has never run.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.ops import admin_metrics as adm

router = APIRouter(prefix="/admin/health", tags=["Admin", "Health"])


@router.get("/tables")
def health_tables(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The pg_stat census: counts + sizes, empty tables flagged."""
    _verify_admin(authorization)
    return adm.get_table_health(engine)


@router.get("/completeness")
def health_completeness(
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """Completeness meters, rendered from ``admin_metrics`` (nightly job)."""
    _verify_admin(authorization)
    return adm.get_completeness(engine)
