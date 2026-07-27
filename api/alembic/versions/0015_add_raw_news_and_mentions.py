"""Add `raw_news` + `boat_news_mentions` for LLM news entity extraction.

Implements SPEC-14 §4 (Unstructured News Processing). News articles are
ingested into an unstructured `raw_news` table, a workflow passes the
article text to the Sailing LLM to extract structured entities
(mentioned_boats, mentioned_events, sentiment, key_takeaway), and the
extracted `mentioned_boats` are fuzzy-matched against `boats` +
`boat_identities` and linked in the `boat_news_mentions` join table.

All datetime columns are `TIMESTAMP WITH TIME ZONE` and default to UTC
(`now()`), per SPEC-14 §2 (every datetime column normalized to UTC).

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_news",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.Text(), nullable=False, comment="Feed/domain the article came from"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True, comment="Article body passed to the LLM"),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Processing lifecycle: pending -> processing -> processed | failed
        sa.Column("processing_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        # Extraction output (SPEC-14 §4 extraction schema)
        sa.Column("extracted_entities", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mentioned_events", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sentiment", sa.Text(), nullable=True),
        sa.Column("key_takeaway", sa.Text(), nullable=True),
        sa.Column("extraction_model", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_news_status", "raw_news", ["processing_status"])
    op.create_index("ix_raw_news_source", "raw_news", ["source"])
    op.create_index("ix_raw_news_published", "raw_news", ["published_at"])
    op.create_unique_constraint("uq_raw_news_source_url", "raw_news", ["source_url"])

    op.create_table(
        "boat_news_mentions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("news_id", sa.Integer(), nullable=False),
        sa.Column("boat_id", sa.Integer(), nullable=False),
        sa.Column("mention_text", sa.Text(), nullable=True, comment="The boat string the LLM extracted"),
        sa.Column(
            "match_method",
            sa.Text(),
            nullable=False,
            comment="sail_exact | name_exact | trigram | identity",
        ),
        sa.Column("match_confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["news_id"], ["raw_news.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["boat_id"], ["boats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("news_id", "boat_id", name="uq_boat_news_mentions_news_boat"),
    )
    op.create_index("ix_boat_news_mentions_news", "boat_news_mentions", ["news_id"])
    op.create_index("ix_boat_news_mentions_boat", "boat_news_mentions", ["boat_id"])


def downgrade() -> None:
    op.drop_index("ix_boat_news_mentions_boat", table_name="boat_news_mentions")
    op.drop_index("ix_boat_news_mentions_news", table_name="boat_news_mentions")
    op.drop_table("boat_news_mentions")

    op.drop_constraint("uq_raw_news_source_url", "raw_news", type_="unique")
    op.drop_index("ix_raw_news_published", table_name="raw_news")
    op.drop_index("ix_raw_news_source", table_name="raw_news")
    op.drop_index("ix_raw_news_status", table_name="raw_news")
    op.drop_table("raw_news")
