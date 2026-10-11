"""Provenance helpers for the intelligence query service (SPEC-25 §2.3).

All SQL here is dialect-neutral (runs on PostgreSQL and SQLite) so the
provenance of an answer can be computed in unit tests with a SQLite fixture.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from irc_data.api.schemas.intelligence import EvidenceRef

logger = logging.getLogger(__name__)

DATASET_PREFIX = "irc-canonical"
EMPTY_DATASET_VERSION = f"{DATASET_PREFIX}-empty"
# Older than this and an otherwise-complete answer is marked ``degraded``.
STALE_AFTER_DAYS = 365


def canonical_sha(obj: Any) -> str:
    """sha256 of canonical JSON (sorted keys, compact) - same recipe as ReportFactsV1."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def to_datetime(value: Any) -> datetime | None:
    """Coerce a DB timestamp/date/ISO string to an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def latest(*values: Any) -> datetime | None:
    """Most recent of several timestamps, ignoring missing ones."""
    dts = [d for d in (to_datetime(v) for v in values) if d is not None]
    return max(dts) if dts else None


def dataset_version(engine: Engine) -> str:
    """Version tag of the canonical dataset: ``irc-canonical-YYYYMMDD``.

    Derived from the newest ``boats.updated_at`` - it moves whenever the
    canonical view is rewritten, and is identical for every answer served
    from the same dataset state.
    """
    with engine.connect() as conn:
        newest = conn.execute(text("SELECT max(updated_at) FROM boats")).scalar()
    dt = to_datetime(newest)
    return f"{DATASET_PREFIX}-{dt:%Y%m%d}" if dt else EMPTY_DATASET_VERSION


def boats_freshness(engine: Engine, boat_ids: Iterable[int]) -> datetime | None:
    """Newest ``boats.updated_at`` over the given ids."""
    ids = sorted({int(i) for i in boat_ids})
    if not ids:
        return None
    stmt = text("SELECT max(updated_at) FROM boats WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    with engine.connect() as conn:
        return to_datetime(conn.execute(stmt, {"ids": ids}).scalar())


def active_quarantine_count(engine: Engine) -> int | None:
    """Active ``publication_quarantine`` rows; ``None`` if the table is absent."""
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT count(*) FROM publication_quarantine WHERE status = 'active'")
                ).scalar()
                or 0
            )
    except Exception:  # table not migrated yet - no quarantine information
        return None


def is_stale(freshness_at: datetime | None, now: datetime | None = None) -> bool:
    if freshness_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - freshness_at).days > STALE_AFTER_DAYS


def table_ref(table: str, **keys: Any) -> str:
    """Stable lineage ref for a canonical row/slice: ``boats:id=17213``."""
    if not keys:
        return f"table:{table}"
    return f"{table}:" + ",".join(f"{k}={v}" for k, v in sorted(keys.items()))


def lineage_refs_from_report(report: Any) -> list[str]:
    """Flatten a ``transform.lineage.LineageReport`` chain into lineage refs.

    ``assertion:<id>`` -> ``extraction_batch:<id>`` -> ``artifact:<id>@<sha>``
    so a canonical fact can cite its raw artifact (DP-06-03).
    """
    refs: list[str] = []
    for hop in report.chain:
        kind = hop.get("hop")
        if kind == "assertion":
            refs.append(f"assertion:{hop['assertion_id']}")
        elif kind == "extraction_batch":
            refs.append(f"extraction_batch:{hop['extraction_batch_id']}")
        elif kind == "artifact":
            refs.append(f"artifact:{hop['artifact_id']}@{hop['content_hash']}")
    return refs


def evidence(source: str, ref: str, label: str | None = None, value: Any = None) -> EvidenceRef:
    return EvidenceRef(source=source, ref=ref, label=label, value=value)
