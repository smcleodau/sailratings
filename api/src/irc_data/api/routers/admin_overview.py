"""Admin overview API (AD-01-13) — "what needs a human today", in one call.

Backs the admin ``/admin`` Today screen.  One endpoint:

  GET /admin/overview — sources from ``ingestion_log`` joined to
                        ``source_schedule_state`` (last run, status,
                        ``stale_days``, ``last14``), today aggregates,
                        ``runs_per_day`` (60d), ``dupe_review_queue``
                        pending counts by tier, ``boat_corrections``
                        pending, boats count + completeness meters, and
                        the server-side ``attention[]`` rules
                        (SPEC-22 §3.1).

The aggregation lives in :mod:`irc_data.operations.overview` so it is
unit-testable without HTTP; this module is the thin auth + transport
shell.  The SQL is dialect-portable (SQLite contract tests, Postgres
production).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db
from irc_data.api.routers.admin import _verify_admin
from irc_data.operations import overview as overview_mod

router = APIRouter(prefix="/admin", tags=["Admin"])


def _parse_now(value: str | None) -> datetime | None:
    """Optional ``as_of`` override (ISO-8601) for fixtures/replays.

    Production callers omit it and get wall-clock UTC; the contract test
    pins ``2026-09-02`` so the snapshot acceptance numbers are exact.
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"as_of: invalid ISO-8601 datetime {value!r}"
        )


@router.get("/overview")
async def admin_overview(
    as_of: str | None = Query(
        default=None,
        description="Optional ISO-8601 'now' override (fixtures/replays).",
    ),
    runs_days: int = Query(
        default=overview_mod.RUNS_PER_DAY_DAYS,
        ge=1,
        le=120,
        description="Trailing days for the runs_per_day series.",
    ),
    engine: Engine = Depends(get_db),
    authorization: str = Header(None),
):
    """The one-call admin overview: what needs a human today.

    Returns the Today screen payload — stat tiles (today's runs/new
    rows, dupes pending, corrections pending, boats count), the sources
    table rows with stale pills and 14-day sparklines, the 60-day
    runs-per-day series (zero-filled so the UI can draw zero-run bands),
    fleet completeness meters and the ``attention[]`` list.
    """
    _verify_admin(authorization)
    return overview_mod.get_overview(
        engine, now=_parse_now(as_of), runs_days=runs_days
    )
