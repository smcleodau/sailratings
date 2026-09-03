"""DP-01-02 — stamp policy v1.0 onto data_sources (supersedes interim-v0).

``docs/SOURCE-POLICY.md`` v1.0 (approved 2026-09-02) supersedes the
interim-v0 policy (DP-00-01).  The responsible-collection gate enforces
``CURRENT_POLICY_VERSION`` on every ``data_sources`` row — a source whose
``policy_version`` does not match cannot be collected from (the adapter
cannot run without an approved policy version).

This migration therefore:

1. Stamps ``policy_version = 'v1.0'`` on every ``data_sources`` row so the
   registry satisfies the gate.
2. Records the v1.0 named rulings (SOURCE-POLICY.md §3) in each row's
   ``notes`` so the DB itself carries the audit trail:
     * ORC      — approved; public data.orc.org JSON API only
     * TopYacht — approved; public club-published results only
     * ClubSpot — hold; ToS restricts automated access
     * Kwindoo  — hold; ToS restricts automated access
     * irc-certs— approved; grey-area ruling with special conditions
3. Ensures the four ToS-restricted / grey-area sources carry the correct
   ``legal_status`` after the ruling (ClubSpot & Kwindoo stay ``hold``;
   ORC, TopYacht and irc-certs are ``approved``).

Also merges the repo's multiple 0025 alembic heads into a single head.

Note (PAY-01-07): the abandoned twin ``0026_canonical_merge_and_compat``
(down_revision ``20260526a``) and the other retired side branches live in
``alembic/legacy_versions/``; this file is the canonical ``0026`` revision,
parented on the canonical ``0025`` (fact_assertions).  Its DP-03-05
compatibility surface (``v1_*`` views + evidence tables) is carried forward
as idempotent ensures in ``0027_payments_auth``.

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
#
# The repo accumulated several parallel "0025" migration files
# (0025_crawl_budget, 0025_fact_assertions, 0025_schedule_registry,
# 0025_watchdog_alerts) which all declare the *same* revision id "0025"
# (mirroring the duplicated "0023"/"0024" ids earlier in the graph).  This
# revision chains off "0025"; like the earlier duplicated ids, it is applied
# via the idempotent ``alembic stamp`` / direct-execute path documented in
# 0025_schedule_registry.py.
revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# v1.0 rulings (docs/SOURCE-POLICY.md §3) — slug → (legal_status, ruling note)
_RULINGS = {
    "orc": (
        "approved",
        "v1.0 §3.3: approved — public data.orc.org JSON API only; "
        "ToS-restricted areas excluded",
    ),
    "topyacht": (
        "approved",
        "v1.0 §3.3: approved — public club-published results pages only",
    ),
    "clubspot": (
        "hold",
        "v1.0 §3.5: hold — ToS restricts automated access; rights ruling "
        "pending; discovery metadata only",
    ),
    "kwindoo": (
        "hold",
        "v1.0 §3.5: hold — ToS restricts automated access; rights ruling "
        "pending; discovery metadata only",
    ),
    "irc-certs": (
        "approved",
        "v1.0 §3.4/§6: approved — grey-area ruling; attribution header, "
        "personal-data redaction, no raw PDF redistribution, takedown path",
    ),
}

_NEW_VERSION = "v1.0"
_OLD_VERSION = "interim-v0"


def upgrade() -> None:
    sources = sa.table(
        "data_sources",
        sa.column("slug", sa.Text),
        sa.column("policy_version", sa.Text),
        sa.column("legal_status", sa.Text),
        sa.column("notes", sa.Text),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    # 1. Stamp every source row with the new approved policy version.
    op.execute(
        sources.update()
        .values(policy_version=_NEW_VERSION, updated_at=sa.func.now())
    )

    # 2. Record the v1.0 named rulings (status + audit note) per source.
    for slug, (status, note) in _RULINGS.items():
        op.execute(
            sources.update()
            .where(sources.c.slug == slug)
            .values(
                legal_status=status,
                notes=note,
                updated_at=sa.func.now(),
            )
        )


def downgrade() -> None:
    # Roll every row back to the superseded interim version.  (The gate will
    # then refuse to collect until a forward migration re-stamps v1.0 — which
    # is exactly the fail-closed behaviour the policy requires.)
    sources = sa.table(
        "data_sources",
        sa.column("policy_version", sa.Text),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        sources.update()
        .values(policy_version=_OLD_VERSION, updated_at=sa.func.now())
    )
