"""CLI for the source monitor — ``irc-data source-monitor``.

Subcommands::

    irc-data source-monitor set-baseline <fixture-file> --source <id> --url <url>
    irc-data source-monitor check <fixture-file> --source <id> --url <url>
    irc-data source-monitor baselines
    irc-data source-monitor incidents [--source <id>]
    irc-data source-monitor release [--source <id>]

A *fixture file* is a local HTML / text file used as a stand-in for a
real HTTP fetch.  In production the Temporal ``monitor_source_health``
activity fetches the page itself and passes the body to
``check_source()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click


@click.group(name="source-monitor")
def source_monitor() -> None:
    """Source change & breakage detection (DP-01-05)."""


@source_monitor.command(name="set-baseline")
@click.argument("fixture", type=click.Path(exists=True, path_type=Path))
@click.option("--source", "source_id", required=True, help="Source identifier (e.g. sailsys)")
@click.option("--url", required=True, help="Canonical URL for the source page")
@click.option("--content-type", default="text/html", help="Expected content type")
def set_baseline_cmd(
    fixture: Path, source_id: str, url: str, content_type: str
) -> None:
    """Establish (or replace) the baseline for a source from a fixture file."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import (
        fingerprint_source,
        set_baseline,
    )

    content = fixture.read_text(encoding="utf-8", errors="replace")
    fp = fingerprint_source(content=content, content_type=content_type)
    engine = get_engine()
    set_baseline(engine, source_id, url, fp)
    click.echo(
        f"Baseline set for {source_id} {url}  "
        f"records={fp.record_count}  hash={fp.content_hash[:12]}…"
    )


@source_monitor.command(name="check")
@click.argument("fixture", type=click.Path(exists=True, path_type=Path))
@click.option("--source", "source_id", required=True, help="Source identifier")
@click.option("--url", required=True, help="Canonical URL for the source page")
@click.option("--content-type", default="text/html", help="Expected content type")
@click.option(
    "--parser-yield", type=int, default=None,
    help="Override parser yield (records extracted by downstream parser)",
)
def check_cmd(
    fixture: Path,
    source_id: str,
    url: str,
    content_type: str,
    parser_yield: int | None,
) -> None:
    """Check a fixture against the stored baseline.

    Exit code 0 = clean / harmless change.
    Exit code 2 = material deviation (publication quarantined, incident opened).
    """
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import check_source

    content = fixture.read_text(encoding="utf-8", errors="replace")
    engine = get_engine()

    # Load baseline content for diff-ratio computation.
    from irc_data.diagnostics.source_monitor import get_baseline
    baseline = get_baseline(engine, source_id, url)

    event = check_source(
        engine,
        source_id,
        url,
        content=content,
        content_type=content_type,
        parser_yield=parser_yield,
    )

    click.echo(
        f"status={event.status}  material={event.material}"
    )
    if event.deviations:
        click.echo(
            f"  deviations: {', '.join(event.deviations)}"
        )
    baseline_rc = event.baseline.get("record_count", 0)
    current_rc = event.current.get("record_count", 0)
    baseline_py = event.baseline.get("parser_yield", 0)
    current_py = event.current.get("parser_yield", 0)
    click.echo(
        f"  records: {current_rc} (baseline {baseline_rc})  "
        f"yield: {current_py} (baseline {baseline_py})  "
        f"diff_ratio: {event.diff_ratio:.4f}"
    )

    if event.quarantined:
        click.echo(
            f"  Publication quarantined. Incident #{event.incident_id} open.",
            err=True,
        )
        sys.exit(2)


@source_monitor.command(name="baselines")
def baselines_cmd() -> None:
    """List all stored baselines."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import list_baselines

    engine = get_engine()
    baselines = list_baselines(engine)

    if not baselines:
        click.echo("No baselines set.")
        return

    for b in baselines:
        hash_short = (b.get("content_hash") or "")[:12]
        click.echo(
            f"  {b['source_id']:<24} {b['url']:<50} "
            f"records={b.get('record_count', 0):<6} hash={hash_short}…"
        )


@source_monitor.command(name="incidents")
@click.option("--source", "source_id", default=None, help="Filter by source")
def incidents_cmd(source_id: str | None) -> None:
    """List source incidents (open and resolved)."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import list_incidents
    import json

    engine = get_engine()
    incidents = list_incidents(engine, source_id)

    if not incidents:
        click.echo("No incidents.")
        return

    for inc in incidents:
        devs_raw = inc.get("deviations")
        if isinstance(devs_raw, str):
            try:
                devs = json.loads(devs_raw)
            except (json.JSONDecodeError, TypeError):
                devs = []
        elif isinstance(devs_raw, list):
            devs = devs_raw
        else:
            devs = []
        devs_str = ",".join(devs) if devs else ""
        click.echo(
            f"  #{inc['id']}  {inc['source_id']:<24} "
            f"type={inc['incident_type']:<28} "
            f"status={inc['status']:<10} "
            f"deviations={devs_str}"
        )


@source_monitor.command(name="release")
@click.option("--source", "source_id", default=None, help="Release only this source")
def release_cmd(source_id: str | None) -> None:
    """Release publication quarantine(s) and resolve associated incidents."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import release_quarantine

    engine = get_engine()
    released = release_quarantine(engine, source_id)

    if source_id:
        click.echo(f"Released {released} quarantine(s) for {source_id}.")
    else:
        click.echo(f"Released {released} quarantine(s).")
