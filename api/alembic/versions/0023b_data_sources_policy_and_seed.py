"""data_sources, source_incidents, domain_disables (DP-01-01 / DP-01-02)

Creates the source registry tables referenced by the responsible-collection
policy (``docs/INTERIM-POLICY.md``) and SPEC-012 §2, §6:

* ``data_sources``         — approved source registry with policy version
* ``source_incidents``     — structure / robots / hash-delta incidents
* ``domain_disables``      — emergency domain-level kill switch

Seeds the 11 interim-v0 source rows (9 approved, 2 on hold).

Canonical chain (DP-03-05): this revision was renumbered from the
duplicated id ``0023`` to the unique id ``20260830a`` so the alembic graph is
a single linear head.  ``data_sources`` itself is created by the preceding
``0023`` revision; this one is idempotent (``IF NOT EXISTS`` /
``ON CONFLICT``) so it converges databases that took either historical
branch.

Revision ID: 20260830a
Revises: 0023
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from typing import Sequence, Union


revision: str = "20260830a"
down_revision: Union[str, Sequence[str], None] = "0023"
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
    # data_sources — created by the preceding ``0023`` revision.  The
    # historical branch of this file also created it; guard with
    # CREATE TABLE IF NOT EXISTS so both lineages converge.  Only a minimal
    # subset of columns is guaranteed here; ``0023`` owns the full shape.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_sources (
            id SERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            category TEXT NOT NULL,
            adapter_class TEXT,
            policy_version TEXT NOT NULL DEFAULT 'interim-v0',
            legal_status TEXT NOT NULL DEFAULT 'unknown',
            robots_checked_at TIMESTAMPTZ,
            robots_disallow TEXT[],
            quarantine_until TIMESTAMPTZ,
            contact_email TEXT,
            notes TEXT,
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Bring the register up to the full DP-01-04 contract shape (idempotent).
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'data-platform'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS geography TEXT NOT NULL DEFAULT 'GLOBAL'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS terms_status TEXT NOT NULL DEFAULT 'unreviewed'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS robots_status TEXT NOT NULL DEFAULT 'unchecked'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS licensing TEXT NOT NULL DEFAULT 'unknown'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS access_method TEXT NOT NULL DEFAULT 'html_scrape'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS format TEXT NOT NULL DEFAULT 'html'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS change_detection TEXT NOT NULL DEFAULT 'content_hash'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 3")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS adapter_status TEXT NOT NULL DEFAULT 'planned'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS cadence TEXT NOT NULL DEFAULT 'nightly'")
    op.execute("ALTER TABLE data_sources ADD COLUMN IF NOT EXISTS quarantine_until TIMESTAMPTZ")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_data_sources_slug ON data_sources (slug)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_sources_status ON data_sources (legal_status, enabled)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_data_sources_category ON data_sources (category)"
    )

    # ------------------------------------------------------------------
    # source_incidents (policy flavour; the source_monitor flavour in
    # ``20260830b`` is a superset and is guarded to coexist)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_incidents (
            id SERIAL PRIMARY KEY,
            source_slug TEXT NOT NULL REFERENCES data_sources(slug) ON DELETE CASCADE,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            incident_type TEXT NOT NULL,
            previous_hash TEXT,
            current_hash TEXT,
            artifact_url TEXT,
            resolved_at TIMESTAMPTZ,
            notes TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_incidents_slug ON source_incidents (source_slug)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_incidents_type ON source_incidents (incident_type)"
    )

    # ------------------------------------------------------------------
    # domain_disables — emergency domain-level kill switch
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_disables (
            id SERIAL PRIMARY KEY,
            domain TEXT NOT NULL,
            disabled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reason TEXT,
            disabled_by TEXT,
            re_enabled_at TIMESTAMPTZ,
            active BOOLEAN NOT NULL DEFAULT true,
            CONSTRAINT uq_domain_disables_active UNIQUE (domain, active)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_domain_disables_domain ON domain_disables (domain)"
    )

    # ------------------------------------------------------------------
    # Seed data_sources (idempotent — skip rows that already exist)
    # ------------------------------------------------------------------
    for slug, name, url, category, status, notes in SEED_SOURCES:
        op.execute(
            sa.text(
                "INSERT INTO data_sources (slug, display_name, base_url, category, "
                "policy_version, legal_status, notes, enabled) "
                "VALUES (:slug, :name, :url, :cat, 'interim-v0', :status, :notes, true) "
                "ON CONFLICT (slug) DO NOTHING"
            ).bindparams(
                slug=slug, name=name, url=url, cat=category, status=status, notes=notes
            )
        )


def downgrade() -> None:
    # Only drop the objects this revision uniquely owns.  ``data_sources``
    # and (on the converged chain) ``source_incidents`` are owned by the
    # neighbouring ``0023`` / ``20260830b`` revisions and are left in place.
    op.execute("DROP INDEX IF EXISTS idx_domain_disables_domain")
    op.execute("DROP TABLE IF EXISTS domain_disables")
    op.execute("DROP INDEX IF EXISTS idx_source_incidents_type")
    op.execute("DROP INDEX IF EXISTS idx_source_incidents_slug")
