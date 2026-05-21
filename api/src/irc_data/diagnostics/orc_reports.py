"""ORC operator reports.

Each function returns rows ready for printing by the CLI; the formatting
lives in ``irc_data.cli``. Keep this module DB-only / Click-free so it
stays importable from tests and the future API.
"""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine, Row


def orphans_report(engine: Engine) -> tuple[Sequence[Row], Sequence[Row]]:
    """Return (by-country orphan counts, top recent match-failure reasons).

    The first list is the per-country count of ORC certs that have no
    ``boat_id`` (top 20 worst). The second is the top 10 most common
    reasons recorded into ``ingest_events`` over the past 7 days. The
    second list is empty until at least one ``match-boats`` pass has
    written orphan rows.
    """
    with engine.connect() as conn:
        by_country = conn.execute(
            text(
                """
                SELECT country_id, COUNT(*) AS orphans
                FROM orc_certificates
                WHERE boat_id IS NULL
                GROUP BY country_id
                ORDER BY orphans DESC
                LIMIT 20
                """
            )
        ).fetchall()
        recent_reasons = conn.execute(
            text(
                """
                SELECT reason, COUNT(*) AS n
                FROM ingest_events
                WHERE source = 'orc'
                  AND event_type = 'match'
                  AND status = 'orphan'
                  AND created_at > NOW() - INTERVAL '7 days'
                GROUP BY reason
                ORDER BY n DESC
                LIMIT 10
                """
            )
        ).fetchall()
    return by_country, recent_reasons


def detail_coverage_report(engine: Engine) -> Sequence[Row]:
    """Per-country GPH-coverage counts.

    For each country, how many ORC certs we have, how many have GPH
    (i.e. a backfill detail row), and how many are still missing. Sort
    by ``missing_detail`` descending so the worst-coverage country is at
    the top.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT country_id,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE gph IS NOT NULL) AS with_detail,
                       COUNT(*) FILTER (WHERE gph IS NULL)     AS missing_detail
                FROM orc_certificates
                GROUP BY country_id
                ORDER BY missing_detail DESC
                """
            )
        ).fetchall()
    return rows
