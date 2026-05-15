"""Audit + queue tables for data-cleanup operations.

Holds reversal snapshots for every destructive cleanup operation:
boat merges, design class merges, attribute nulls, design canonical
sweeps, not-dupe records, cert reclassifications/re-parses, and the
manual dupe review queue.

All tables are zero-row on a fresh database. They fill only when the
matching cleanup script runs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "boat_merges",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("winner_id", sa.Integer(), nullable=False),
        sa.Column("loser_id", sa.Integer(), nullable=False),
        sa.Column("cluster_key", sa.Text(), nullable=False),
        sa.Column("loser_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_boat_merges_loser", "boat_merges", ["loser_id"])

    op.create_table(
        "design_class_merges",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("winner_id", sa.Integer(), nullable=False),
        sa.Column("loser_id", sa.Integer(), nullable=False),
        sa.Column("canonical", sa.Text(), nullable=False),
        sa.Column("loser_row", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )

    op.create_table(
        "design_class_attr_nulls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("nulled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("design_id", sa.Integer(), nullable=False),
        sa.Column("canonical", sa.Text(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=False),
    )

    op.create_table(
        "boats_design_canonical_sweep",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("swept_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("boat_id", sa.Integer(), nullable=False),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
    )

    op.create_table(
        "boat_not_dupe",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cluster_key", sa.Text(), nullable=False),
        sa.Column("boat_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
    )

    op.create_table(
        "cert_sym_asym_reclassify",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("reclassified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cert_id", sa.Integer(), nullable=False),
        sa.Column("old_sym_slu", sa.Numeric(6, 2)),
        sa.Column("old_sym_sle", sa.Numeric(6, 2)),
        sa.Column("old_sym_sf", sa.Numeric(6, 2)),
        sa.Column("old_sym_shw", sa.Numeric(6, 2)),
    )

    op.create_table(
        "cert_reparse_disp_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cert_id", sa.Integer(), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("new_displacement", sa.Numeric(8, 1)),
        sa.Column("new_draft", sa.Numeric(6, 2)),
        sa.Column("pdf_path", sa.Text()),
        sa.Column("note", sa.Text()),
    )

    op.create_table(
        "cert_reparse_lwp_dlr",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cert_id", sa.Integer(), nullable=False),
        sa.Column("new_lwp", sa.Numeric(6, 2)),
        sa.Column("new_dlr", sa.Numeric(6, 2)),
    )

    op.create_table(
        "dupe_review_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("cluster_id", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("boat_id", sa.Integer(), nullable=False),
        sa.Column("boat_name", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("sail_number", sa.Text()),
        sa.Column("design", sa.Text()),
        sa.Column("year_built", sa.Integer()),
        sa.Column("race_results", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latest_activity", sa.Date()),
        sa.Column("owner", sa.Text()),
        sa.Column("cluster_size", sa.Integer(), nullable=False),
        sa.Column("why", sa.Text()),
        sa.Column("verdict", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("verdict_note", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Text()),
    )
    op.create_index("ix_dupe_review_cluster", "dupe_review_queue", ["cluster_id"])
    op.create_index("ix_dupe_review_tier_verdict", "dupe_review_queue", ["tier", "verdict"])


def downgrade() -> None:
    for t in (
        "dupe_review_queue",
        "cert_reparse_lwp_dlr",
        "cert_reparse_disp_draft",
        "cert_sym_asym_reclassify",
        "boat_not_dupe",
        "boats_design_canonical_sweep",
        "design_class_attr_nulls",
        "design_class_merges",
        "boat_merges",
    ):
        op.drop_table(t)
