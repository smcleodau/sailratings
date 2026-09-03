"""reconciliation — silent-loss detection tables (DP-05-03)

Two tables back the reconciliation reconciler:

* ``pipeline_count_baseline`` — one row per pipeline run per source; the
  trailing yield series (published / discovered) used to detect abrupt
  yield change.  The reconciler reads the [p10, p50] band of the trailing
  window and blocks when the current run's yield collapses below it.
* ``reconciliation_reports`` — one row per ``reconcile_run()`` verdict:
  stage counts, unexplained variance, yield vs baseline, decision
  (``allow``/``block``), and the block reason.  ``decision = 'block'``
  rows are the promotion-blocking signal consumed by the publish gate.

Canonical chain: follows ``0027`` (data_sources_notion_tiers) so the
alembic graph remains a single linear head.

Revision ID: 20260904a
Revises: 0027
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260904a"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MD = None


def _metadata():
    """Lazily-shared MetaData so ForeignKey references resolve across tables
    created in the same migration."""
    global _MD
    if _MD is None:
        from sqlalchemy import MetaData
        _MD = MetaData()
    return _MD


def _ctine(name, *columns, **kwargs):
    """create_table IF NOT EXISTS (idempotent for canonical chain)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(name):
        return
    from sqlalchemy import Table
    md = _metadata()
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
    _ctine(
        "pipeline_count_baseline",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("discovered", sa.Integer(), server_default="0"),
        sa.Column("fetched", sa.Integer(), server_default="0"),
        sa.Column("parsed", sa.Integer(), server_default="0"),
        sa.Column("transformed", sa.Integer(), server_default="0"),
        sa.Column("rejected", sa.Integer(), server_default="0"),
        sa.Column("quarantined", sa.Integer(), server_default="0"),
        sa.Column("published", sa.Integer(), server_default="0"),
        sa.Column("duplicate_suppressed", sa.Integer(), server_default="0"),
        sa.Column("yield_ratio", sa.Numeric(8, 4)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    _cidx_if_not_exists(
        "ix_pcb_source_recorded",
        "pipeline_count_baseline",
        ["source_id", "recorded_at"],
    )

    _ctine(
        "reconciliation_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("counts", sa.JSON()),
        sa.Column("variance", sa.Integer(), server_default="0"),
        sa.Column("variance_explained", sa.Boolean(), server_default=sa.true()),
        sa.Column("unexplained_reasons", sa.JSON()),
        sa.Column("yield_ratio", sa.Numeric(8, 4)),
        sa.Column("baseline_yield_p10", sa.Numeric(8, 4)),
        sa.Column("baseline_yield_p50", sa.Numeric(8, 4)),
        sa.Column("abrupt_yield_change", sa.Boolean(), server_default=sa.false()),
        sa.Column("decision", sa.Text(), nullable=False, server_default="allow"),
        sa.Column("promotion_allowed", sa.Boolean(), server_default=sa.true()),
        sa.Column("block_reason", sa.Text()),
        sa.Column("schema_version", sa.Text(), server_default="v1"),
        sa.UniqueConstraint("report_id"),
    )
    _cidx_if_not_exists(
        "ix_recon_reports_source",
        "reconciliation_reports",
        ["source_id", "checked_at"],
    )
    _cidx_if_not_exists(
        "ix_recon_reports_decision", "reconciliation_reports", ["decision"]
    )


def downgrade() -> None:
    op.drop_index("ix_recon_reports_decision", table_name="reconciliation_reports")
    op.drop_index("ix_recon_reports_source", table_name="reconciliation_reports")
    op.drop_table("reconciliation_reports")
    op.drop_index("ix_pcb_source_recorded", table_name="pipeline_count_baseline")
    op.drop_table("pipeline_count_baseline")
