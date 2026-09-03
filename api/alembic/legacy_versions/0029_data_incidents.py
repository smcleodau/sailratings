"""data incidents — owned recovery work (DP-05-04)

One table backs the data-health incident workflow:

* ``data_incidents`` — one row per :class:`DataIncidentV1`: kind,
  severity, workflow status (``open → acknowledged → mitigating →
  resolved``), the accountable owner (DP-05-01 registry), the affected
  batches / consumers, the evidence refs back to the underlying quality
  events (health events, reconciliation reports, source incidents,
  quarantine records, gate verdicts, ledger runs) and the recommended
  replay-or-policy action.

Canonical chain: follows ``20260904b`` (quality gates) so the alembic
graph remains a single linear head.

Revision ID: 20260905a
Revises: 20260904b
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260905a"
down_revision: Union[str, Sequence[str], None] = "20260904b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ctine(name, *columns, **kwargs):
    """create_table IF NOT EXISTS (idempotent for canonical chain)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(name):
        return
    from sqlalchemy import MetaData, Table

    md = MetaData()
    Table(name, md, *columns, **kwargs)
    md.tables[name].create(bind)


def _cidx_if_not_exists(index_name, table_name, columns, unique=False):
    """create_index IF NOT EXISTS (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes(table_name)}
    if index_name in existing:
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    jsonb = (
        sa.dialects.postgresql.JSONB
        if op.get_bind().dialect.name == "postgresql"
        else sa.Text
    )

    _ctine(
        "data_incidents",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("source_slug", sa.Text),
        sa.Column("dataset", sa.Text),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("owner", jsonb),
        sa.Column("acknowledged_by", sa.Text),
        sa.Column("affected_batches", jsonb),
        sa.Column("affected_consumers", jsonb),
        sa.Column("evidence", jsonb),
        sa.Column("recommended_action", jsonb),
        sa.Column("alert_sent_at", sa.DateTime(timezone=True)),
        sa.Column("notes", jsonb),
        sa.Column("schema_version", sa.Text, server_default="v1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    _cidx_if_not_exists(
        "ix_data_incidents_status",
        "data_incidents",
        ["status", "detected_at"],
    )
    _cidx_if_not_exists(
        "ix_data_incidents_source",
        "data_incidents",
        ["source_slug", "detected_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("data_incidents"):
        for ix in ("ix_data_incidents_source", "ix_data_incidents_status"):
            existing = {i["name"] for i in insp.get_indexes("data_incidents")}
            if ix in existing:
                op.drop_index(ix, table_name="data_incidents")
        op.drop_table("data_incidents")
