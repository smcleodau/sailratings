"""field_provenance — field-level lineage tracking (DP-02-05)

Creates the ``field_provenance`` table that stores explicit lineage
edges linking canonical fields to raw artifacts via extracted spans.

Each row represents a single node in the lineage chain:

  canonical_field → transformed_value → extracted_span → raw_artifact

The ``parent_id`` column creates the directed edge (parent = upstream /
source).  Together, the rows form a DAG that the LineageService walks
to build a :class:`LineageGraphV1`.

Spec: SPEC-013, DP-02-05.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-15
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "field_provenance",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        # Node identity
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("entity", sa.String(128), nullable=False),
        sa.Column("field", sa.String(128), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("record_id", sa.Integer(), nullable=True),
        # Source / artifact linkage
        sa.Column("source_slug", sa.String(128), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        # Extraction metadata
        sa.Column("model_version", sa.String(64), nullable=True),
        sa.Column("rule_version", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        # Span (extracted_span nodes)
        sa.Column("span_text", sa.Text(), nullable=True),
        sa.Column("span_start", sa.Integer(), nullable=True),
        sa.Column("span_end", sa.Integer(), nullable=True),
        # Edge to parent (upstream node in the chain)
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("edge_type", sa.String(32), nullable=True),
        # Timestamps
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["field_provenance.id"],
            name="fk_field_provenance_parent",
        ),
        sa.CheckConstraint(
            "node_type IN ('canonical_field', 'transformed_value', "
            "'extracted_span', 'raw_artifact')",
            name="field_provenance_node_type_check",
        ),
        sa.CheckConstraint(
            "edge_type IS NULL OR edge_type IN "
            "('transformed_to', 'derived_from', 'extracted_from', "
            "'merged_from', 'adjudicated_from')",
            name="field_provenance_edge_type_check",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="field_provenance_confidence_check",
        ),
    )
    op.create_index(
        "ix_field_provenance_entity_field",
        "field_provenance",
        ["entity", "field"],
    )
    op.create_index(
        "ix_field_provenance_record_id",
        "field_provenance",
        ["record_id"],
    )
    op.create_index(
        "ix_field_provenance_parent_id",
        "field_provenance",
        ["parent_id"],
    )
    op.create_index(
        "ix_field_provenance_source_slug",
        "field_provenance",
        ["source_slug"],
    )
    op.create_index(
        "ix_field_provenance_content_hash",
        "field_provenance",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_field_provenance_content_hash", table_name="field_provenance")
    op.drop_index("ix_field_provenance_source_slug", table_name="field_provenance")
    op.drop_index("ix_field_provenance_parent_id", table_name="field_provenance")
    op.drop_index("ix_field_provenance_record_id", table_name="field_provenance")
    op.drop_index("ix_field_provenance_entity_field", table_name="field_provenance")
    op.drop_table("field_provenance")
