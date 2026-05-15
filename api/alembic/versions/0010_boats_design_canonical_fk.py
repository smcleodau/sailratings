"""boats_design_canonical_fk

Adds a FK from boats.design_canonical -> design_classes.name_canonical so
future writes can't silently drift away from the canonical name list.

The constraint is added as NOT VALID: it enforces all NEW writes but does
NOT block on existing orphans. A parallel sweep is cleaning up the ~1.2k
orphan rows; once that finishes an operator should run:

    ALTER TABLE boats VALIDATE CONSTRAINT fk_boats_design_canonical;

ON DELETE SET NULL: deleting a design_classes row blanks the boat's
canonical name (the design merge tool already runs deletes).
ON UPDATE CASCADE: renaming a design_classes.name_canonical updates all
linked boats.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "fk_boats_design_canonical"


def upgrade() -> None:
    # Alembic's op.create_foreign_key() does not expose NOT VALID, so we
    # use raw SQL. NOT VALID skips the up-front full-table scan and lets
    # the migration succeed even with existing orphan rows; the constraint
    # still applies to all subsequent INSERT/UPDATE.
    op.execute(
        f"""
        ALTER TABLE boats
        ADD CONSTRAINT {CONSTRAINT_NAME}
        FOREIGN KEY (design_canonical)
        REFERENCES design_classes(name_canonical)
        ON UPDATE CASCADE
        ON DELETE SET NULL
        NOT VALID
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE boats DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
