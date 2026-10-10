"""Clerk -> Postgres webhook sync: users.deleted_at + clerk_events (AUTH-01-01)

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-04

* ``users.deleted_at`` — soft-delete marker set by the ``user.deleted``
  Clerk webhook.
* ``clerk_events`` — svix-id ledger; a redelivered webhook is detected by
  the primary key and acknowledged as a no-op.

Idempotent (IF NOT EXISTS) because the dev database is shared between
worktrees.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS clerk_events (
            id          text PRIMARY KEY,
            type        text,
            received_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS clerk_events")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS deleted_at")
