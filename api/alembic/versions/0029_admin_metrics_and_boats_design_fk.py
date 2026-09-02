"""OPS-02-09 — design_classes sweep evidence store + boats.design_canonical FK.

Two deliverables, one revision:

1. ``admin_metrics`` table
   The durable ops-evidence store the acceptance criteria require: the
   merge/null/backfill sweep writes the ``boats.design_canonical`` NULL rate
   (and orphan counts) here *before* and *after* each run, so the change is
   auditable from the database itself (sample baseline quoted in the issue:
   design 64% NULL at spec-writing time; measured 47.4% at implementation).

2. ``fk_boats_design_canonical`` — ensure + VALIDATE
   Revision 0010 added this FK as ``NOT VALID`` (enforce new writes, skip the
   full-table scan) with the instruction to validate it once the orphan
   sweep finished.  This revision closes that loop *idempotently*:
     * if the constraint was dropped (fresh DBs that skipped 0010's raw SQL
       still converge), it is re-added with the same definition
       (``NOT VALID`` first, so a dirty table can't abort the deploy);
     * ``VALIDATE CONSTRAINT`` runs only when at least one row would
       violate it — on a dirty table we skip validation and record a
       ``fk_validation = skipped_orphans`` row in ``admin_metrics`` so the
       failure is visible in the metrics stream rather than a red deploy.
   On the dev DB (0 orphans) the constraint is VALID after this migration.

Idempotent: every statement is guarded (``IF NOT EXISTS`` / pg_catalog
checks); re-running is a no-op beyond a fresh ``admin_metrics`` evidence row
from the sweep runner (not from this migration).

Downgrade drops only the objects this revision owns: the ``admin_metrics``
table.  The FK is left in place — dropping it would silently weaken the
integrity the sweep established; an operator who truly wants the pre-0029
state can ``ALTER TABLE boats DROP CONSTRAINT fk_boats_design_canonical``
by hand (this mirrors 0010's own downgrade, which never validated).

Revision ID: 0029
Revises: 0026
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "fk_boats_design_canonical"


# ---------------------------------------------------------------------------
# SQL building blocks
# ---------------------------------------------------------------------------

_CREATE_ADMIN_METRICS = """
CREATE TABLE IF NOT EXISTS admin_metrics (
    id          BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metric      TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT '',
    phase       TEXT NOT NULL DEFAULT '',
    value_num   DOUBLE PRECISION,
    value_text  TEXT,
    meta        JSONB
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric ON admin_metrics (metric)",
    "CREATE INDEX IF NOT EXISTS ix_admin_metrics_recorded_at ON admin_metrics (recorded_at)",
    (
        "CREATE INDEX IF NOT EXISTS ix_admin_metrics_metric_scope "
        "ON admin_metrics (metric, scope, phase, recorded_at DESC)"
    ),
]

# Re-add the FK (as NOT VALID) only if it is missing entirely.
_ENSURE_FK = f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = '{CONSTRAINT_NAME}'
          AND conrelid = 'boats'::regclass
    ) THEN
        ALTER TABLE boats
        ADD CONSTRAINT {CONSTRAINT_NAME}
        FOREIGN KEY (design_canonical)
        REFERENCES design_classes(name_canonical)
        ON UPDATE CASCADE
        ON DELETE SET NULL
        NOT VALID;
    END IF;
END
$$
"""

# Validate only when zero rows would violate it; otherwise record the skip
# in admin_metrics so the failure mode is observable, not a broken deploy.
_VALIDATE_FK = f"""
DO $$
DECLARE
    n_orphans bigint;
    already_valid boolean;
BEGIN
    SELECT convalidated INTO already_valid
    FROM pg_constraint
    WHERE conname = '{CONSTRAINT_NAME}'
      AND conrelid = 'boats'::regclass;

    IF already_valid THEN
        RETURN;
    END IF;

    SELECT COUNT(*) INTO n_orphans
    FROM boats b
    WHERE b.design_canonical IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM design_classes dc
          WHERE dc.name_canonical = b.design_canonical
      );

    IF n_orphans = 0 THEN
        ALTER TABLE boats VALIDATE CONSTRAINT {CONSTRAINT_NAME};
    ELSE
        INSERT INTO admin_metrics (metric, scope, phase, value_num, value_text, meta)
        VALUES (
            'boats.design_canonical.fk_validation',
            '{CONSTRAINT_NAME}',
            'skipped',
            n_orphans,
            'skipped_orphans',
            jsonb_build_object(
                'reason', 'orphan rows still present',
                'orphan_boats', n_orphans
            )
        );
    END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(_CREATE_ADMIN_METRICS)
    for stmt in _INDEXES:
        op.execute(stmt)
    op.execute(_ENSURE_FK)
    op.execute(_VALIDATE_FK)


def downgrade() -> None:
    # Only drop what this revision created.  The FK stays: it may have been
    # validated by data work outside this migration, and silently dropping a
    # validated constraint on downgrade would destroy integrity guarantees.
    op.execute("DROP TABLE IF EXISTS admin_metrics")
