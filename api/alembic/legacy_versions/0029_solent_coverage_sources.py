"""OPS-02-14 — UK/Solent coverage: register Solent sources with policy checks.

Adds the three UK / Solent results sources to the governed ``data_sources``
register so the Temporal schedule registry and staleness watchdog treat them
as first-class, policy-checked sources:

* ``jog``                   — Junior Offshore Group (myjog.jog.org.uk).
                              Server-rendered per-race ``/raceresults/<uuid>``
                              pages keyed by ``?year=``; full IRC results.
* ``warsash-spring-series`` — Warsash SC Spring Series / Spring Championships
                              (Solent); results published as public Sailwave
                              files under ``sailwave.com/results/warsashsc``.
* ``hamble-winter-series``  — HRSC Hamble Winter Series; results published
                              publicly via HalSail (halsail.com Result pages),
                              collected through the discovery pipeline.

Each row carries the OPS-01-01 scheduling-policy fields (``cadence_class``,
``staleness_budget_hours``, nightly window, retry/backoff, cooldown,
kill-switch ack) so the register stays valid and the source can be scheduled
without a schema change.

Also idempotently adds the OPS-01-01 scheduling columns to ``data_sources``
(``cadence_class`` … ``kill_switch_ack_hours``) for databases that predate
``20260903a`` — the seed upsert references them.

Idempotent: ``INSERT … ON CONFLICT (slug) DO UPDATE`` and guarded
``ALTER TABLE … ADD COLUMN IF NOT EXISTS``.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
# 20260904b (quality gates) is the current linear head; hang OPS-02-14 off it.
down_revision: Union[str, Sequence[str], None] = "20260904b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_scheduling_columns() -> None:
    """Add the OPS-01-01 scheduling columns if absent (idempotent)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {c["name"] for c in insp.get_columns("data_sources")}
    cols = {
        "cadence_class": sa.Text(),
        "staleness_budget_hours": sa.Float(),
        "nightly_window_start": sa.Text(),
        "nightly_window_end": sa.Text(),
        "retry_policy": sa.dialects.postgresql.JSONB()
        if bind.dialect.name == "postgresql" else sa.JSON(),
        "cooldown_hours": sa.Float(),
        "kill_switch_ack_hours": sa.Integer(),
    }
    for name, typ in cols.items():
        if name not in existing:
            op.add_column("data_sources", sa.Column(name, typ, nullable=True))


_SOLENT_ROWS = [
    # slug, display_name, base_url, cadence, budget_hours
    ("jog", "JOG (Junior Offshore Group)", "https://myjog.jog.org.uk/results",
     "nightly", 48.0),
    ("warsash-spring-series", "Warsash Spring Series / Spring Championships",
     "https://warsashsc.org.uk/springseries/black-group-results/", "weekly", 192.0),
    ("hamble-winter-series", "Hamble Winter Series (HRSC)",
     "https://www.hamblewinterseries.com", "weekly", 192.0),
]

_RETRY = '{"max_attempts": 3, "backoff_seconds": [600, 1800, 7200]}'


def upgrade() -> None:
    _add_scheduling_columns()

    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    conflict = (
        "ON CONFLICT (slug) DO UPDATE SET "
        "legal_status = EXCLUDED.legal_status, "
        "cadence = EXCLUDED.cadence, "
        "cadence_class = EXCLUDED.cadence_class, "
        "staleness_budget_hours = EXCLUDED.staleness_budget_hours, "
        "nightly_window_start = EXCLUDED.nightly_window_start, "
        "nightly_window_end = EXCLUDED.nightly_window_end, "
        "retry_policy = EXCLUDED.retry_policy, "
        "cooldown_hours = EXCLUDED.cooldown_hours, "
        "kill_switch_ack_hours = EXCLUDED.kill_switch_ack_hours, "
        "updated_at = now()"
        if is_pg
        else ""
    )
    insert = (
        "INSERT INTO data_sources ("
        " slug, display_name, base_url, category, geography, tier,"
        " access_method, cadence, format, legal_status, terms_status,"
        " robots_status, licensing, adapter_status, priority,"
        " cadence_class, staleness_budget_hours, nightly_window_start,"
        " nightly_window_end, retry_policy, cooldown_hours,"
        " kill_switch_ack_hours, policy_version, enabled, notes)"
        " VALUES ("
        " :slug, :name, :url, 'results', 'GB', 'Tier 3: Niche/Local Events',"
        " 'html_scrape', :cadence, 'html', 'approved', 'reviewed',"
        " 'allowed', 'public_domain', 'unexplored', 3,"
        " 'daily_results', :budget, '01:00', '06:00', CAST(:retry AS "
        + ("jsonb" if is_pg else "json") + "), 4, 4,"
        " 'v1.0', true, :notes) "
        + conflict
    )

    notes = (
        "OPS-02-14 Solent coverage: registered for UK/Solent race results "
        "(Sun Fast 3300 / J/109 fleets). Publicly published results."
    )
    for slug, name, url, cadence, budget in _SOLENT_ROWS:
        if is_pg:
            op.execute(
                sa.text(insert).bindparams(
                    slug=slug, name=name, url=url, cadence=cadence,
                    budget=budget, retry=_RETRY, notes=notes,
                )
            )
        else:
            # SQLite path (tests): simple insert-or-ignore.
            op.execute(
                sa.text(
                    "INSERT OR IGNORE INTO data_sources ("
                    " slug, display_name, base_url, category, geography, tier,"
                    " access_method, cadence, format, legal_status, terms_status,"
                    " robots_status, licensing, adapter_status, priority,"
                    " cadence_class, staleness_budget_hours, nightly_window_start,"
                    " nightly_window_end, cooldown_hours,"
                    " kill_switch_ack_hours, policy_version, enabled, notes)"
                    " VALUES ("
                    f" '{slug}', '{name}', '{url}', 'results', 'GB',"
                    " 'Tier 3: Niche/Local Events', 'html_scrape',"
                    f" '{cadence}', 'html', 'approved', 'reviewed', 'allowed',"
                    " 'public_domain', 'unexplored', 3, 'daily_results',"
                    f" {budget}, '01:00', '06:00', 4, 4, 'v1.0', 1,"
                    f" '{notes}')"
                )
            )


def downgrade() -> None:
    for slug, *_ in _SOLENT_ROWS:
        op.execute(
            sa.text("DELETE FROM data_sources WHERE slug = :slug").bindparams(slug=slug)
        )
