"""Entitlements: admin_entitlement_grants + UNIQUE(user_id, boat_id) on boat_claims (PAY-01-01)

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-05

* ``admin_entitlement_grants`` — admin override of a user's tier. Tier names
  are the product vocabulary (crew/skipper/programme), mapped from Stripe
  plan codes in ``services/entitlements.py``.
* ``boat_claims`` had no UNIQUE(user_id, boat_id) (0027/0034 only index the
  columns). Any pre-existing duplicate pair is collapsed first (a verified
  row wins, then the oldest) so the constraint can be added.

Idempotent (IF NOT EXISTS / catalogue checks): the dev database is shared
between worktrees.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_entitlement_grants (
            id          bigserial PRIMARY KEY,
            user_id     bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier        text NOT NULL
                        CHECK (tier IN ('crew', 'skipper', 'programme')),
            reason      text,
            granted_by  text,
            expires_at  timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_entitlement_grants_user"
        " ON admin_entitlement_grants (user_id)"
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'uq_boat_claims_user_boat'
                   AND conrelid = 'boat_claims'::regclass
            ) THEN
                DELETE FROM boat_claims c
                 USING boat_claims d
                 WHERE c.user_id = d.user_id
                   AND c.boat_id = d.boat_id
                   AND c.id <> d.id
                   AND (
                       (d.status = 'verified' AND c.status <> 'verified')
                       OR (
                           (d.status = 'verified') = (c.status = 'verified')
                           AND d.id < c.id
                       )
                   );
                ALTER TABLE boat_claims
                    ADD CONSTRAINT uq_boat_claims_user_boat
                    UNIQUE (user_id, boat_id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE boat_claims DROP CONSTRAINT IF EXISTS uq_boat_claims_user_boat"
    )
    op.execute("DROP TABLE IF EXISTS admin_entitlement_grants")
