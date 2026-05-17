"""Rename `certificates` -> `irc_certificates` for clarity alongside `orc_certificates`.

The `certificates` table is IRC-only — it carries IRC-specific measurement
columns (bo, so, p, e, j, sym_*, asym_*, etc.) that don't exist on ORC
certificates. ORC certs live in a separate `orc_certificates` table.
Renaming makes the two tables symmetrically named and stops new readers
of the code from wondering whether `certificates` means "all certs" or
"IRC certs only".

Scope of this migration:
  - Rename the table.
  - Rename the primary key, unique constraint, FK constraint, and indexes
    so they no longer carry the legacy "certificates_" prefix.
  - Rename the id sequence.

Code-side impact this migration does NOT handle:
  - ~50 raw SQL `text("...FROM certificates...")` references in analytics
    routers, regression/temporal/performance/optimizer engines, parsers,
    and the CLI must be updated to `irc_certificates` in lockstep.
  - `db/models.py`: rename `Certificate.__tablename__` to `irc_certificates`
    (the Python class name can stay `Certificate` or be renamed to
    `IRCCertificate` — either is internally consistent).
  - `irc-data` CLI subcommand names that mention "certs" (parse-certs,
    scrape certs, rematch-certs) keep their CLI names — only internal SQL
    changes.

Safety pattern for live deploys (NOT applied here — dev-box workflow):
  If you ever need this to land while old code is still running in
  production, wrap the rename with a backwards-compatible view:
      CREATE VIEW certificates AS SELECT * FROM irc_certificates;
  Then drop the view in a follow-up migration once all callers have
  moved over. The current SailRatings dev-on-this-box workflow does not
  need this — code changes and migration land together.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-17
"""

from alembic import op


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename the table itself.
    op.rename_table("certificates", "irc_certificates")

    # Rename the primary key + its backing index.
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT certificates_pkey TO irc_certificates_pkey"
    )
    # Note: ALTER TABLE RENAME CONSTRAINT on a PK auto-renames the backing
    # index in modern Postgres (>= 12). The explicit ALTER INDEX is a no-op
    # if the index already followed, but keeps us safe on older versions.
    op.execute(
        "ALTER INDEX IF EXISTS certificates_pkey "
        "RENAME TO irc_certificates_pkey"
    )

    # Rename the unique constraint on cert_number + its backing index.
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT certificates_cert_number_key "
        "TO irc_certificates_cert_number_key"
    )
    op.execute(
        "ALTER INDEX IF EXISTS certificates_cert_number_key "
        "RENAME TO irc_certificates_cert_number_key"
    )

    # Rename the FK to boats.
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT certificates_boat_id_fkey "
        "TO irc_certificates_boat_id_fkey"
    )

    # Rename the boat_id lookup index.
    op.execute(
        "ALTER INDEX IF EXISTS idx_certs_boat "
        "RENAME TO idx_irc_certs_boat"
    )

    # Rename the id sequence.
    op.execute(
        "ALTER SEQUENCE IF EXISTS certificates_id_seq "
        "RENAME TO irc_certificates_id_seq"
    )


def downgrade() -> None:
    op.execute(
        "ALTER SEQUENCE IF EXISTS irc_certificates_id_seq "
        "RENAME TO certificates_id_seq"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_irc_certs_boat "
        "RENAME TO idx_certs_boat"
    )
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT irc_certificates_boat_id_fkey "
        "TO certificates_boat_id_fkey"
    )
    op.execute(
        "ALTER INDEX IF EXISTS irc_certificates_cert_number_key "
        "RENAME TO certificates_cert_number_key"
    )
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT irc_certificates_cert_number_key "
        "TO certificates_cert_number_key"
    )
    op.execute(
        "ALTER INDEX IF EXISTS irc_certificates_pkey "
        "RENAME TO certificates_pkey"
    )
    op.execute(
        "ALTER TABLE irc_certificates "
        "RENAME CONSTRAINT irc_certificates_pkey TO certificates_pkey"
    )
    op.rename_table("irc_certificates", "certificates")
