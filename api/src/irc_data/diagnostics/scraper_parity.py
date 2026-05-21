"""Parity diagnostic: legacy vs Firecrawl row counts per source.

Read-only. Used during the 14-day parallel-run window before a bespoke
scraper is retired (Task A6 in the extensive-results plan).

Output is a per-day rollup of:

  - legacy_rows  : count(*) WHERE transport='legacy'
  - firecrawl_rows: count(*) WHERE transport='firecrawl'
  - distinct_events: count(DISTINCT event_name)

Run via ``irc-data parity-report --source isora`` (registered in cli.py).
"""

from __future__ import annotations

import re

import click
from sqlalchemy import text

from irc_data.db.connection import get_engine


_INTERVAL_PATTERN = re.compile(r"^\s*\d+\s+(day|days|hour|hours|week|weeks)\s*$",
                               re.IGNORECASE)


@click.command(name="parity-report")
@click.option("--source", required=True,
              help="race_results.source value to filter on (e.g. isora)")
@click.option("--since", default="14 days",
              help="Look-back window as a Postgres interval literal "
                   "(e.g. '14 days', '7 days', '24 hours'). Default: '14 days'.")
def parity_report(source: str, since: str) -> None:
    """Print per-day legacy vs firecrawl row counts for one source."""
    if not _INTERVAL_PATTERN.match(since):
        raise click.BadParameter(
            f"--since must look like '14 days' or '7 days', got {since!r}"
        )

    eng = get_engine()
    # The interval has to be inlined (Postgres doesn't bind INTERVAL :param
    # against a string parameter). It's pre-validated by the regex above
    # against a strict whitelist, so injection isn't a risk.
    sql = text(f"""
        SELECT
          DATE(created_at) AS day,
          COUNT(*) FILTER (WHERE transport = 'legacy') AS legacy_rows,
          COUNT(*) FILTER (WHERE transport = 'firecrawl') AS firecrawl_rows,
          COUNT(*) FILTER (WHERE transport IS NULL) AS untagged_rows,
          COUNT(DISTINCT event_name) AS distinct_events
        FROM race_results
        WHERE source = :source
          AND created_at >= NOW() - INTERVAL '{since}'
        GROUP BY DATE(created_at)
        ORDER BY day DESC;
    """)

    with eng.connect() as conn:
        rows = list(conn.execute(sql, {"source": source}))

    if not rows:
        click.echo(f"no rows for source={source!r} in the last {since}")
        return

    click.echo(
        f"{'day':<12}  {'legacy':>8}  {'firecrawl':>10}  "
        f"{'untagged':>9}  {'events':>7}"
    )
    for r in rows:
        click.echo(
            f"{str(r.day):<12}  "
            f"{r.legacy_rows:>8}  "
            f"{r.firecrawl_rows:>10}  "
            f"{r.untagged_rows:>9}  "
            f"{r.distinct_events:>7}"
        )
