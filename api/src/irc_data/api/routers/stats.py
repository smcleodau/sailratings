"""GET /v1/stats — real database census for public marketing copy.

OPS-02-11: marketing numbers must not drift from the database.

The endpoint answers with one census query (a single round-trip) that
returns counts for boats, IRC/ORC certificates, race results, events,
countries, designs and registered data sources, plus per-domain
last-updated timestamps. Responses are cached in-process for
``STATS_CACHE_TTL_SECONDS`` (default 600 = 10 minutes) so the website can
read from it on every page view without hammering Postgres.

The SQL is deliberately dialect-portable (scalar subselects, no
Postgres-only functions) so the contract test can run against an
in-memory SQLite census fixture while production runs on Postgres.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/stats", tags=["stats"])

# 10-minute cache, per OPS-02-11 scope. Overridable via env for ops tuning
# and tests.
STATS_CACHE_TTL_SECONDS = float(os.environ.get("STATS_CACHE_TTL_SECONDS", "600"))

# One round-trip census. Keep this a *single statement*: scalar subselects
# against the census tables. Timestamps use each table's own audit column so
# "last updated" reflects real writes, not deploy time.
CENSUS_SQL = """
SELECT
    (SELECT COUNT(*) FROM boats)            AS boats,
    (SELECT COUNT(*) FROM tcc_snapshots)    AS tcc_snapshots,
    (SELECT COUNT(*) FROM irc_certificates) AS irc_certificates,
    (SELECT COUNT(*) FROM orc_certificates) AS orc_certificates,
    (SELECT COUNT(*) FROM race_results)     AS race_results,
    (SELECT COUNT(*) FROM events)           AS events,
    (SELECT COUNT(DISTINCT country) FROM boats
        WHERE country IS NOT NULL AND country <> '')   AS countries,
    (SELECT COUNT(DISTINCT design) FROM boats
        WHERE design IS NOT NULL AND design <> '')     AS designs,
    (SELECT COUNT(*) FROM data_sources)     AS sources,
    (SELECT MAX(updated_at) FROM boats)              AS boats_last_updated,
    (SELECT MAX(scraped_at) FROM irc_certificates)   AS irc_certificates_last_updated,
    (SELECT MAX(created_at) FROM orc_certificates)   AS orc_certificates_last_updated,
    (SELECT MAX(created_at) FROM race_results)       AS race_results_last_updated,
    (SELECT MAX(updated_at) FROM events)             AS events_last_updated,
    (SELECT MAX(updated_at) FROM data_sources)       AS sources_last_updated
"""

#: Count keys in canonical response order. Each maps to the same-named
#: column produced by ``CENSUS_SQL``.
COUNT_KEYS: tuple[str, ...] = (
    "boats",
    "tcc_snapshots",
    "irc_certificates",
    "orc_certificates",
    "race_results",
    "events",
    "countries",
    "designs",
    "sources",
)

#: Domains that expose a last-updated timestamp, mapped to the census column.
LAST_UPDATED_COLUMNS: dict[str, str] = {
    "boats": "boats_last_updated",
    "irc_certificates": "irc_certificates_last_updated",
    "orc_certificates": "orc_certificates_last_updated",
    "race_results": "race_results_last_updated",
    "events": "events_last_updated",
    "sources": "sources_last_updated",
}


class _StatsCache:
    """Tiny process-local TTL cache. The census is global (not per-user), so
    a single slot is sufficient; the lock keeps concurrent cold requests from
    stampeding the database."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._expires_at: float = 0.0

    def get_or_compute(self, compute, ttl: float) -> tuple[dict[str, Any], bool]:
        now = time.monotonic()
        with self._lock:
            if self._payload is not None and now < self._expires_at:
                return self._payload, True
        # Compute outside the lock so a slow census doesn't block cache hits.
        payload = compute()
        with self._lock:
            self._payload = payload
            self._expires_at = time.monotonic() + max(ttl, 0.0)
            return self._payload, False

    def reset(self) -> None:
        with self._lock:
            self._payload = None
            self._expires_at = 0.0


_cache = _StatsCache()


def reset_stats_cache() -> None:
    """Test hook: drop the cached census so the next request recomputes."""
    _cache.reset()


def _iso(value: Any) -> str | None:
    """Normalise DB timestamp values (datetime/date/str) to a string."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def compute_census(engine: Engine) -> dict[str, Any]:
    """Run the single census query and shape the Stats payload.

    Flat top-level keys (``boats``, ``race_results``, …) are kept for
    backward compatibility with the pre-OPS-02-11 endpoint; ``counts`` and
    ``last_updated`` are the structured view the website reads.
    """
    with engine.connect() as conn:
        row = conn.execute(text(CENSUS_SQL)).mappings().first() or {}

    counts = {key: int(row.get(key) or 0) for key in COUNT_KEYS}
    last_updated = {
        domain: _iso(row.get(column))
        for domain, column in LAST_UPDATED_COLUMNS.items()
    }
    generated_at = datetime.now().astimezone().isoformat()

    return {
        # Flat keys (legacy consumers + convenience).
        **counts,
        # Structured views.
        "counts": counts,
        "last_updated": last_updated,
        # Cache metadata — lets consumers display "as of" honestly.
        "generated_at": generated_at,
        "cache_ttl_seconds": int(STATS_CACHE_TTL_SECONDS),
    }


def get_stats_cached(engine: Engine, ttl: float | None = None) -> dict[str, Any]:
    """Return the census payload, recomputing at most once per TTL window."""
    payload, _ = _cache.get_or_compute(
        lambda: compute_census(engine),
        STATS_CACHE_TTL_SECONDS if ttl is None else ttl,
    )
    return payload


@router.get("/")
def get_stats(db: Engine = Depends(get_db)) -> dict[str, Any]:
    """Live database census: counts + last-updated timestamps.

    Cached for 10 minutes; numbers always come from the database census,
    never from copy, so marketing figures cannot drift from reality.
    """
    return get_stats_cached(db)
