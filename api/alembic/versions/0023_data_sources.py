"""data_sources — governed global Data Source Register (DP-01-01)

Creates the ``data_sources`` table that makes collection breadth, value,
legality and health visible. Every collection job references an approved
source record and a policy decision before fetching.

See SPEC-012 §2 and docs/INTERIM-POLICY.md.

Revision ID: 0023
Revises: aa0f8e0c178b
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "aa0f8e0c178b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        # Governance / legality
        sa.Column("owner", sa.Text(), nullable=False, server_default="data-platform"),
        sa.Column("geography", sa.Text(), nullable=False, server_default="GLOBAL"),
        sa.Column("legal_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("terms_status", sa.Text(), nullable=False, server_default="unreviewed"),
        sa.Column("robots_status", sa.Text(), nullable=False, server_default="unchecked"),
        sa.Column("licensing", sa.Text(), nullable=False, server_default="unknown"),
        # Collection shape
        sa.Column("access_method", sa.Text(), nullable=False, server_default="html_scrape"),
        sa.Column("cadence", sa.Text(), nullable=False, server_default="nightly"),
        sa.Column("format", sa.Text(), nullable=False, server_default="html"),
        sa.Column("change_detection", sa.Text(), nullable=False, server_default="content_hash"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        # Adapter / health
        sa.Column("adapter_class", sa.Text(), nullable=True),
        sa.Column("adapter_status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Optional metadata
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("robots_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("robots_disallow", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("slug", name="data_sources_slug_key"),
        sa.CheckConstraint(
            "legal_status IN ('approved', 'hold', 'blocked', 'unknown')",
            name="data_sources_legal_status_check",
        ),
    )
    op.create_index("idx_data_sources_slug", "data_sources", ["slug"], unique=True)
    op.create_index("idx_data_sources_legal_status", "data_sources", ["legal_status"])
    op.create_index("idx_data_sources_category", "data_sources", ["category"])


def downgrade() -> None:
    op.drop_index("idx_data_sources_category", table_name="data_sources")
    op.drop_index("idx_data_sources_legal_status", table_name="data_sources")
    op.drop_index("idx_data_sources_slug", table_name="data_sources")
    op.drop_table("data_sources")
