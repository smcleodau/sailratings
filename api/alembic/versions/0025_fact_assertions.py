"""Source assertion and bitemporal history model (DP-03-02).

Creates the ``fact_assertions`` table — an **append-only** store of source
assertions that preserves *who said what, and when the truth changed*:

  * ``valid_from`` / ``valid_to``   — source-valid time (when the source
                                      claims the value was/is true).
  * ``recorded_at``                 — system-observed time (when we learned
                                      it).  Immutable once written.
  * ``source_slug`` / ``provenance_uri`` — provenance: who said it and the
                                      raw artifact it was parsed from.
  * ``value_json`` / ``unit``       — the asserted measurement and its unit.
  * ``confidence``                  — 0–1 trust weight for conflict
                                      resolution.
  * ``supersedes`` / ``superseded_by`` / ``superseded_at`` — correction
                                      links.  History is never overwritten;
                                      a correction is a new row plus a
                                      supersession pointer.
  * ``status`` / ``retracted_at``   — deletions are retractions: the row is
                                      kept, stamped with the system time of
                                      the retraction, so the resolved view
                                      is reproducible for any prior system
                                      time.

The only UPDATEs this table ever receives set ``superseded_by`` /
``superseded_at`` (on correction) or ``status`` / ``retracted_at`` (on
deletion) — never the value, timestamps, or provenance.

Follows the repo's existing convention of parallel same-number migrations
branching off a common parent (see the 0023_* / 0024_* series).

Revision ID: 0025
Revises: 0023
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "20260901b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_assertions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # Content-addressed id (SHA-256); unique so re-submitting the same
        # assertion is idempotent.
        sa.Column("assertion_id", sa.String(64), nullable=False, unique=True),
        # Fact identity.
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_key", sa.String(255), nullable=False),
        sa.Column("field", sa.String(128), nullable=False),
        # Asserted value (JSON-encoded) + unit.
        sa.Column("value_json", sa.Text, nullable=False),
        sa.Column("unit", sa.String(32)),
        # Source-valid time.
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        # System-observed time (immutable).
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        # Provenance.
        sa.Column("source_slug", sa.String(128), nullable=False),
        sa.Column("provenance_uri", sa.Text),
        # Trust weight.
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        # Supersession (corrections).
        sa.Column("supersedes", sa.String(64)),
        sa.Column("superseded_by", sa.String(64)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        # Deletion-via-retraction.
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),
        sa.Column("retracted_at", sa.DateTime(timezone=True)),
        # Free-form extras.
        sa.Column("metadata_json", sa.Text),
        sa.CheckConstraint(
            "status IN ('active', 'retracted')",
            name="ck_fact_assertions_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_fact_assertions_confidence",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_fact_assertions_valid_interval",
        ),
    )
    op.create_index(
        "ix_fact_assertions_fact",
        "fact_assertions",
        ["entity_type", "entity_key", "field"],
    )
    op.create_index(
        "ix_fact_assertions_recorded_at", "fact_assertions", ["recorded_at"]
    )
    op.create_index(
        "ix_fact_assertions_source", "fact_assertions", ["source_slug"]
    )
    op.create_index(
        "ix_fact_assertions_valid_from", "fact_assertions", ["valid_from"]
    )


def downgrade() -> None:
    op.drop_index("ix_fact_assertions_valid_from", table_name="fact_assertions")
    op.drop_index("ix_fact_assertions_source", table_name="fact_assertions")
    op.drop_index("ix_fact_assertions_recorded_at", table_name="fact_assertions")
    op.drop_index("ix_fact_assertions_fact", table_name="fact_assertions")
    op.drop_table("fact_assertions")
