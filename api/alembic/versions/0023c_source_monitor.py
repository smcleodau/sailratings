"""Source monitor — change & breakage detection (DP-01-05 / SPEC-012 §6).

Creates four tables:

  * ``source_baselines``      — last-known-good fingerprint per (source_id, url)
  * ``source_health_events``  — one row per ``check_source()`` invocation
  * ``source_incidents``       — opened on material deviations; carry
                                 representative artifacts (sample_records,
                                 content_excerpt, deviations)
  * ``publication_quarantine`` — blocks downstream publishing while an
                                 incident is open

Canonical chain (DP-03-05): renumbered from the duplicated id ``0023`` to
the unique id ``20260830b`` so the alembic graph is a single linear head.
``source_incidents`` may already exist (created by ``20260830a``); this
revision creates it if needed and adds the monitor-specific columns
idempotently.

Revision ID: 20260830b
Revises: 20260830a
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260830b"
down_revision: Union[str, Sequence[str], None] = "20260830a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_baselines",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("fetch_success", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("http_status", sa.Integer),
        sa.Column("content_type", sa.Text),
        sa.Column("content_hash", sa.Text),
        sa.Column("structure_signature", sa.Text),
        sa.Column("record_count", sa.Integer),
        sa.Column("parser_yield", sa.Integer),
        sa.Column("content_length", sa.Integer),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("source_id", "url", name="uq_source_baselines_source_url"),
    )
    op.create_index("ix_source_baselines_source_id", "source_baselines", ["source_id"])

    op.create_table(
        "source_health_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("material", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deviations", sa.JSON),
        sa.Column("diff_ratio", sa.Numeric(8, 4)),
        sa.Column("baseline_hash", sa.Text),
        sa.Column("current_hash", sa.Text),
        sa.Column("incident_id", sa.Integer),
        sa.Column("quarantined", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("event_payload", sa.JSON),
    )
    op.create_index("ix_source_health_events_source_id", "source_health_events", ["source_id"])
    op.create_index("ix_source_health_events_status", "source_health_events", ["status"])

    # source_incidents may already exist from 20260830a (policy flavour with a
    # source_slug FK).  Create the monitor flavour if absent, then ensure
    # the monitor-specific columns exist either way (idempotent).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_incidents (
            id BIGSERIAL PRIMARY KEY,
            source_id VARCHAR(128),
            url TEXT,
            incident_type VARCHAR(64) NOT NULL,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            deviations JSON,
            sample_records JSON,
            content_excerpt TEXT,
            previous_hash TEXT,
            current_hash TEXT,
            notes TEXT
        )
        """
    )
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS source_id VARCHAR(128)")
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS url TEXT")
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'open'")
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS deviations JSON")
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS sample_records JSON")
    op.execute("ALTER TABLE source_incidents ADD COLUMN IF NOT EXISTS content_excerpt TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_incidents_source_id ON source_incidents (source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_incidents_status ON source_incidents (status)"
    )

    op.create_table(
        "publication_quarantine",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("incident_id", sa.Integer),
        sa.Column("reason", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.UniqueConstraint("source_id", name="uq_publication_quarantine_source"),
    )
    op.create_index("ix_publication_quarantine_source_id", "publication_quarantine", ["source_id"])


def downgrade() -> None:
    # Drop only the objects this revision uniquely owns.  ``source_incidents``
    # is shared with the ``20260830a`` policy flavour and is left in place.
    op.drop_index("ix_publication_quarantine_source_id", table_name="publication_quarantine")
    op.drop_table("publication_quarantine")

    op.execute("DROP INDEX IF EXISTS ix_source_incidents_status")
    op.execute("DROP INDEX IF EXISTS ix_source_incidents_source_id")

    op.drop_index("ix_source_health_events_status", table_name="source_health_events")
    op.drop_index("ix_source_health_events_source_id", table_name="source_health_events")
    op.drop_table("source_health_events")

    op.drop_index("ix_source_baselines_source_id", table_name="source_baselines")
    op.drop_table("source_baselines")
