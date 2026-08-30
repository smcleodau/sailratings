"""data_sources, source_incidents, domain_disables (DP-01-01 / DP-01-02)

Creates the source registry tables referenced by the responsible-collection
policy (``docs/INTERIM-POLICY.md``) and SPEC-012 §2, §6:

* ``data_sources``         — approved source registry with policy version
* ``source_incidents``     — structure / robots / hash-delta incidents
* ``domain_disables``      — emergency domain-level kill switch

Seeds the 11 interim-v0 source rows (9 approved, 2 on hold).

Revision ID: 0023
Revises: aa0f8e0c178b
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from typing import Sequence, Union


revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "aa0f8e0c178b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_SOURCES = [
    ("sailsys", "SailSys", "https://app.sailsys.com.au", "results", "approved",
     "Australian race management; publicly published results"),
    ("topyacht", "TopYacht", "https://www.topyacht.net.au", "results", "approved",
     "Australian race management; publicly published results"),
    ("irc-tcc", "IRC TCC Listings", "https://ircrating.org", "ratings", "approved",
     "Published for racing administration; CSV download"),
    ("orc", "ORC", "https://data.orc.org", "ratings", "approved",
     "Published for racing administration; JSON API"),
    ("yachtscoring", "Yacht Scoring", "https://www.yachtscoring.com", "results", "approved",
     "US/international race results; publicly published"),
    ("manage2sail", "Manage2Sail", "https://manage2sail.com", "results", "approved",
     "European race management; publicly published results"),
    ("sailwave", "Sailwave", "https://www.sailwave.com", "results", "approved",
     "Results files publicly linked from club sites"),
    ("sailing-news", "Sailing News Feeds", "https://example.com/news", "news", "approved",
     "RSS/Atom feeds; explicitly published for syndication"),
    ("irc-certs", "IRC Certificate PDFs", "https://ircrating.org/pdfdirectory",
     "certificates", "approved",
     "Publicly accessible; core platform data (see INTERIM-POLICY §4)"),
    ("clubspot", "ClubSpot", "https://clubspot.com", "results", "hold",
     "Rights ruling pending; ToS review incomplete"),
    ("kwindoo", "Kwindoo", "https://www.kwindoo.com", "results", "hold",
     "Rights ruling pending; ToS review incomplete"),
]


def upgrade() -> None:
    # ------------------------------------------------------------------
    # data_sources
    # ------------------------------------------------------------------
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("adapter_class", sa.Text()),
        sa.Column("policy_version", sa.Text(), nullable=False, server_default=sa.text("'interim-v0'")),
        sa.Column("legal_status", sa.Text(), nullable=False, server_default=sa.text("'approved'")),
        sa.Column("robots_checked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("robots_disallow", sa.ARRAY(sa.Text())),
        sa.Column("quarantine_until", sa.TIMESTAMP(timezone=True)),
        sa.Column("contact_email", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "legal_status IN ('approved', 'hold', 'blocked')",
            name="ck_data_sources_legal_status",
        ),
    )
    op.create_index("idx_data_sources_slug", "data_sources", ["slug"])
    op.create_index("idx_data_sources_status", "data_sources", ["legal_status", "enabled"])

    # ------------------------------------------------------------------
    # source_incidents
    # ------------------------------------------------------------------
    op.create_table(
        "source_incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_slug", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("incident_type", sa.Text(), nullable=False),
        sa.Column("previous_hash", sa.Text()),
        sa.Column("current_hash", sa.Text()),
        sa.Column("artifact_url", sa.Text()),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.ForeignKeyConstraint(["source_slug"], ["data_sources.slug"], ondelete="CASCADE"),
    )
    op.create_index("idx_source_incidents_slug", "source_incidents", ["source_slug"])
    op.create_index("idx_source_incidents_type", "source_incidents", ["incident_type"])

    # ------------------------------------------------------------------
    # domain_disables — emergency domain-level kill switch
    # ------------------------------------------------------------------
    op.create_table(
        "domain_disables",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("reason", sa.Text()),
        sa.Column("disabled_by", sa.Text()),
        sa.Column("re_enabled_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("domain", "active", name="uq_domain_disables_active"),
    )
    op.create_index("idx_domain_disables_domain", "domain_disables", ["domain"])

    # ------------------------------------------------------------------
    # Seed data_sources
    # ------------------------------------------------------------------
    for slug, name, url, category, status, notes in SEED_SOURCES:
        op.execute(
            sa.text(
                "INSERT INTO data_sources (slug, display_name, base_url, category, "
                "policy_version, legal_status, notes, enabled) "
                "VALUES (:slug, :name, :url, :cat, 'interim-v0', :status, :notes, true)"
            ).bindparams(
                slug=slug, name=name, url=url, cat=category, status=status, notes=notes
            )
        )


def downgrade() -> None:
    op.drop_index("idx_domain_disables_domain", table_name="domain_disables")
    op.drop_table("domain_disables")
    op.drop_index("idx_source_incidents_type", table_name="source_incidents")
    op.drop_index("idx_source_incidents_slug", table_name="source_incidents")
    op.drop_table("source_incidents")
    op.drop_index("idx_data_sources_status", table_name="data_sources")
    op.drop_index("idx_data_sources_slug", table_name="data_sources")
    op.drop_table("data_sources")
