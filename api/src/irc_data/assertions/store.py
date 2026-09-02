"""Append-only bitemporal assertion store (DP-03-02).

Canonical data layer over the ``fact_assertions`` table.  The table is
**append-only**: assertions are inserted, and the only mutations ever
performed are:

  * setting ``superseded_by`` when a correction arrives, and
  * flipping ``status`` to ``retracted`` for a deletion.

Neither operation touches the asserted value, the timestamps, or the
provenance — so **history is never overwritten** and the resolved view
is reproducible for any prior system time.

Portability note: like ``db.run_ledger``, queries avoid Postgres-only
constructs.  Filtering/ordering for resolution is done in Python on
materialised rows (resolution sets are per-fact and small), so behaviour
is byte-for-byte identical on SQLite (tests) and Postgres (production).

Handoff / output contract: :class:`~irc_data.assertions.models.AssertionV1`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Connection, Engine

from irc_data.assertions.models import (
    AssertionStatus,
    AssertionV1,
    ResolutionV1,
    resolve,
)


# ---------------------------------------------------------------------------
# Table definition (canonical, shared by the Alembic migration and tests)
# ---------------------------------------------------------------------------

metadata = MetaData()

fact_assertions = Table(
    "fact_assertions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Content-addressed id; unique so re-submitting the same assertion is
    # idempotent (INSERT ... on conflict do nothing semantics via pre-check).
    Column("assertion_id", String(64), nullable=False, unique=True),
    # Fact identity.
    Column("entity_type", String(64), nullable=False),
    Column("entity_key", String(255), nullable=False),
    Column("field", String(128), nullable=False),
    # Asserted value (JSON-encoded) + unit.
    Column("value_json", Text, nullable=False),
    Column("unit", String(32)),
    # Source-valid time (when the source says it was/is true).
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True)),
    # System-observed time (when we learned it).  Immutable.
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    # Provenance.
    Column("source_slug", String(128), nullable=False),
    Column("provenance_uri", Text),
    # Trust weight for conflict resolution.
    Column("confidence", Float, nullable=False, default=1.0),
    # Supersession pointers (corrections).  History preserved via links.
    Column("supersedes", String(64)),
    Column("superseded_by", String(64)),
    # System time of supersession (the successor's recorded_at).
    Column("superseded_at", DateTime(timezone=True)),
    # active | retracted (retraction = deletion without erasing history).
    Column("status", String(16), nullable=False, default="active"),
    # System time of retraction; NULL while the assertion stands.
    Column("retracted_at", DateTime(timezone=True)),
    # Free-form extras.
    Column("metadata_json", Text),
    Index("ix_fact_assertions_fact", "entity_type", "entity_key", "field"),
    Index("ix_fact_assertions_recorded_at", "recorded_at"),
    Index("ix_fact_assertions_source", "source_slug"),
)


def init_assertion_tables(engine: Engine) -> None:
    """Create the assertion tables (used by tests / local dev)."""
    metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Row <-> contract mapping
# ---------------------------------------------------------------------------


def _row_to_assertion(row: Any) -> AssertionV1:
    m = row._mapping
    return AssertionV1.from_dict(
        {
            "assertion_id": m["assertion_id"],
            "entity_type": m["entity_type"],
            "entity_key": m["entity_key"],
            "field": m["field"],
            "value": json.loads(m["value_json"]),
            "unit": m["unit"],
            "valid_from": m["valid_from"].isoformat(),
            "valid_to": m["valid_to"].isoformat() if m["valid_to"] else None,
            "recorded_at": m["recorded_at"].isoformat(),
            "source_slug": m["source_slug"],
            "provenance_uri": m["provenance_uri"],
            "confidence": m["confidence"],
            "supersedes": m["supersedes"],
            "superseded_by": m["superseded_by"],
            "superseded_at": (
                m["superseded_at"].isoformat() if m["superseded_at"] else None
            ),
            "status": m["status"],
            "retracted_at": (
                m["retracted_at"].isoformat() if m["retracted_at"] else None
            ),
            "metadata": json.loads(m["metadata_json"]) if m["metadata_json"] else {},
        }
    )


# ---------------------------------------------------------------------------
# Write path — append-only
# ---------------------------------------------------------------------------


def record_assertion(conn: Connection, assertion: AssertionV1) -> str:
    """Append an assertion.  Idempotent on ``assertion_id``.

    If the assertion declares ``supersedes``, the superseded row's
    ``superseded_by`` pointer is set to this assertion's id (the only
    mutation ever applied to a prior row — the value/timestamps are
    untouched).

    Returns the assertion_id (existing id if already present).
    """
    existing = conn.execute(
        select(fact_assertions.c.assertion_id).where(
            fact_assertions.c.assertion_id == assertion.assertion_id
        )
    ).first()
    if existing:
        return assertion.assertion_id  # idempotent re-submit

    conn.execute(
        fact_assertions.insert().values(
            assertion_id=assertion.assertion_id,
            entity_type=assertion.entity_type,
            entity_key=assertion.entity_key,
            field=assertion.field,
            value_json=json.dumps(assertion.value),
            unit=assertion.unit,
            valid_from=assertion.valid_from,
            valid_to=assertion.valid_to,
            recorded_at=assertion.recorded_at,
            source_slug=assertion.source_slug,
            provenance_uri=assertion.provenance_uri,
            confidence=float(assertion.confidence),
            supersedes=assertion.supersedes,
            superseded_by=assertion.superseded_by,
            superseded_at=assertion.superseded_at,
            status=assertion.status,
            retracted_at=assertion.retracted_at,
            metadata_json=json.dumps(assertion.metadata or {}),
        )
    )

    if assertion.supersedes:
        # Mark the superseded row: pointer + the system time of the
        # supersession (the successor's recorded_at).  The old row's
        # value/timestamps are untouched — history is preserved.
        conn.execute(
            fact_assertions.update()
            .where(fact_assertions.c.assertion_id == assertion.supersedes)
            .values(
                superseded_by=assertion.assertion_id,
                superseded_at=assertion.recorded_at,
            )
        )
    return assertion.assertion_id


def retract_assertion(
    conn: Connection,
    assertion_id: str,
    retracted_at: datetime | None = None,
) -> bool:
    """Mark an assertion retracted (a deletion).  Returns True if it existed.

    The row is retained — only ``status``/``retracted_at`` are set — so the
    deletion itself is part of history and the view as of any time before
    ``retracted_at`` is unchanged.  ``retracted_at`` defaults to now; pass
    an explicit timestamp in fixtures/replays for reproducibility.
    """
    res = conn.execute(
        fact_assertions.update()
        .where(fact_assertions.c.assertion_id == assertion_id)
        .values(
            status=AssertionStatus.RETRACTED.value,
            retracted_at=retracted_at or datetime.now(timezone.utc),
        )
    )
    return res.rowcount > 0


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


def get_assertions_for_fact(
    conn: Connection,
    entity_type: str,
    entity_key: str,
    field: str,
) -> list[AssertionV1]:
    """All assertions (any status) about one fact, in recorded order."""
    rows = conn.execute(
        select(fact_assertions)
        .where(
            fact_assertions.c.entity_type == entity_type,
            fact_assertions.c.entity_key == entity_key,
            fact_assertions.c.field == field,
        )
        .order_by(fact_assertions.c.recorded_at, fact_assertions.c.id)
    ).all()
    return [_row_to_assertion(r) for r in rows]


def get_assertion(conn: Connection, assertion_id: str) -> AssertionV1 | None:
    row = conn.execute(
        select(fact_assertions).where(
            fact_assertions.c.assertion_id == assertion_id
        )
    ).first()
    return _row_to_assertion(row) if row else None


def list_assertions(
    conn: Connection,
    entity_type: str | None = None,
    entity_key: str | None = None,
) -> list[AssertionV1]:
    """All assertions, optionally filtered by entity.  Full history."""
    stmt = select(fact_assertions).order_by(
        fact_assertions.c.recorded_at, fact_assertions.c.id
    )
    if entity_type is not None:
        stmt = stmt.where(fact_assertions.c.entity_type == entity_type)
    if entity_key is not None:
        stmt = stmt.where(fact_assertions.c.entity_key == entity_key)
    return [_row_to_assertion(r) for r in conn.execute(stmt).all()]


def resolve_fact(
    conn: Connection,
    entity_type: str,
    entity_key: str,
    field: str,
    as_of: datetime,
    valid_as_of: datetime | None = None,
) -> ResolutionV1:
    """Resolve the current truth of one fact as of system time *as_of*.

    Reads the full history for the fact and applies the deterministic
    bitemporal resolution rules from :mod:`irc_data.assertions.models`.
    """
    history = get_assertions_for_fact(conn, entity_type, entity_key, field)
    return resolve(
        history, entity_type, entity_key, field, as_of, valid_as_of
    )


# ---------------------------------------------------------------------------
# Convenience engine-level wrapper
# ---------------------------------------------------------------------------


class AssertionStore:
    """Thin engine-owning convenience wrapper around the functions above.

    Production code may hold one of these; tests can equally call the
    module-level functions with their own connection.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def in_memory(cls) -> "AssertionStore":
        eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
        init_assertion_tables(eng)
        return cls(eng)

    def init(self) -> None:
        init_assertion_tables(self.engine)

    def record(self, assertion: AssertionV1) -> str:
        with self.engine.begin() as conn:
            return record_assertion(conn, assertion)

    def record_many(self, assertions: Iterable[AssertionV1]) -> list[str]:
        ids = []
        with self.engine.begin() as conn:
            for a in assertions:
                ids.append(record_assertion(conn, a))
        return ids

    def retract(
        self, assertion_id: str, at: datetime | None = None
    ) -> bool:
        with self.engine.begin() as conn:
            return retract_assertion(conn, assertion_id, retracted_at=at)

    def get(self, assertion_id: str) -> AssertionV1 | None:
        with self.engine.connect() as conn:
            return get_assertion(conn, assertion_id)

    def history(
        self, entity_type: str, entity_key: str, field: str
    ) -> list[AssertionV1]:
        with self.engine.connect() as conn:
            return get_assertions_for_fact(conn, entity_type, entity_key, field)

    def all(self) -> list[AssertionV1]:
        with self.engine.connect() as conn:
            return list_assertions(conn)

    def resolve(
        self,
        entity_type: str,
        entity_key: str,
        field: str,
        as_of: datetime,
        valid_as_of: datetime | None = None,
    ) -> ResolutionV1:
        with self.engine.connect() as conn:
            return resolve_fact(
                conn, entity_type, entity_key, field, as_of, valid_as_of
            )
