"""Per-row ingest-event logging.

The companion module to ``ingestion_log`` (which tracks scraper *runs*).
``ingest_events`` records *per-row* diagnostics: which certs failed to
parse, which ORC entries didn't match any IRC boat, and why.

Callers should not block ingestion on logging failures — every helper
swallows DB errors after writing them to stderr.
"""

from __future__ import annotations

import sys
from typing import Any

from sqlalchemy import JSON, bindparam, text
from sqlalchemy.engine import Engine


def log_event(
    engine: Engine,
    source: str,
    event_type: str,
    status: str,
    reference: str | None,
    reason: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Insert a row into ``ingest_events``.

    Parameters
    ----------
    source
        Logical source identifier, e.g. ``"orc"``, ``"irc"``, ``"sailsys"``.
    event_type
        What we were doing, e.g. ``"parse"``, ``"match"``, ``"download"``.
    status
        ``"ok"`` / ``"error"`` / ``"orphan"`` / ``"skipped"`` etc.
    reference
        Stable external id (cert ref no, race result id, ...).
    reason
        Human-readable explanation when status != ok.
    meta
        Optional JSON-serialisable bag of extra context.

    Logging failures are swallowed (and surfaced to stderr) so the
    caller never crashes on an audit-log hiccup.
    """
    # ``meta`` is bound through SQLAlchemy's ``JSON`` type so Postgres,
    # SQLite (used in tests), and anything else with a native JSON
    # type can ingest the value without per-dialect ``CAST(... AS JSON)``
    # gymnastics. The type adapter handles serialisation itself, so we
    # pass the dict (or None) — *not* a pre-serialised string.
    stmt = text(
        """
        INSERT INTO ingest_events
          (source, event_type, status, reference, reason, meta)
        VALUES
          (:source, :event_type, :status, :reference, :reason, :meta)
        """
    ).bindparams(bindparam("meta", type_=JSON))

    try:
        with engine.begin() as conn:
            conn.execute(
                stmt,
                {
                    "source": source,
                    "event_type": event_type,
                    "status": status,
                    "reference": reference,
                    "reason": reason,
                    "meta": meta,
                },
            )
    except Exception as exc:  # pragma: no cover — defensive
        print(
            f"[ingest_log] failed to log {source}/{event_type}/{status} "
            f"ref={reference!r}: {exc}",
            file=sys.stderr,
        )
