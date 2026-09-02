"""CLI for reconciliation & silent-loss detection — ``irc-data reconcile``.

Subcommands::

    irc-data reconcile check --source <id> --run-id <n> \\
        --discovered .. --fetched .. --parsed .. --transformed .. \\
        --rejected .. --quarantined .. --published .. --duplicate-suppressed ..
    irc-data reconcile reports [--source <id>] [--decision allow|block]
    irc-data reconcile baseline <source>

``check`` reconciles one run's stage counts.  Exit code 0 = promotion
allowed; exit code 2 = promotion blocked (unexplained variance or abrupt
yield change — quarantined + alerted in the same cycle).
"""

from __future__ import annotations

import click


@click.group(name="reconcile")
def reconcile() -> None:
    """Pipeline reconciliation & silent-loss detection (DP-05-03)."""


@reconcile.command(name="check")
@click.option("--source", "source_id", required=True, help="Source slug")
@click.option("--run-id", type=int, required=True, help="ingestion_log run id")
@click.option("--discovered", type=int, default=0)
@click.option("--fetched", type=int, default=0)
@click.option("--parsed", type=int, default=0)
@click.option("--transformed", type=int, default=0)
@click.option("--rejected", type=int, default=0)
@click.option("--quarantined", type=int, default=0)
@click.option("--published", type=int, default=0)
@click.option("--duplicate-suppressed", type=int, default=0)
@click.option(
    "--reason", "reasons", multiple=True,
    help="Reason-coded drop as code=count (repeatable), e.g. --reason parse_error=3",
)
def check_cmd(
    source_id, run_id, discovered, fetched, parsed, transformed,
    rejected, quarantined, published, duplicate_suppressed, reasons,
):
    """Reconcile one run's stage counts; block promotion on silent loss."""
    import sys

    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.reconciliation import (
        DECISION_BLOCK,
        PipelineCountsV1,
        reconcile_run,
    )

    reason_counts: dict[str, int] = {}
    for r in reasons:
        code, _, val = r.partition("=")
        try:
            reason_counts[code.strip()] = int(val)
        except ValueError:
            raise click.BadParameter(f"invalid --reason {r!r}; expected code=count")

    engine = get_engine()
    counts = PipelineCountsV1(
        run_id=run_id, source_id=source_id, discovered=discovered,
        fetched=fetched, parsed=parsed, transformed=transformed,
        rejected=rejected, quarantined=quarantined, published=published,
        duplicate_suppressed=duplicate_suppressed, reason_counts=reason_counts,
    )
    report = reconcile_run(engine, counts)

    click.echo(
        f"decision={report.decision}  promotion_allowed={report.promotion_allowed}  "
        f"variance={report.variance}  yield={report.yield_ratio:.3f}"
    )
    if report.baseline_yield_p10 is not None:
        click.echo(
            f"  baseline p10={report.baseline_yield_p10:.3f} "
            f"p50={report.baseline_yield_p50:.3f}  "
            f"abrupt_yield_change={report.abrupt_yield_change}"
        )
    if report.decision == DECISION_BLOCK:
        click.echo(f"  BLOCKED: {report.block_reason}", err=True)
        click.echo("  Publication quarantined; alert fired.", err=True)
        sys.exit(2)


@reconcile.command(name="reports")
@click.option("--source", "source_id", default=None)
@click.option("--decision", type=click.Choice(["allow", "block"]), default=None)
@click.option("--limit", type=int, default=50)
def reports_cmd(source_id, decision, limit):
    """List recent reconciliation reports."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.reconciliation import list_reports

    engine = get_engine()
    reports = list_reports(engine, source_id=source_id, decision=decision, limit=limit)
    if not reports:
        click.echo("No reconciliation reports.")
        return
    for r in reports:
        click.echo(
            f"  run {r.run_id:<6} {r.source_id:<20} {r.decision:<6} "
            f"variance={r.variance:<4} yield={r.yield_ratio:.3f} "
            f"{r.block_reason or ''}"
        )


@reconcile.command(name="baseline")
@click.argument("source_id")
def baseline_cmd(source_id):
    """Show the trailing yield band for a source."""
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.reconciliation import get_yield_baseline

    engine = get_engine()
    b = get_yield_baseline(engine, source_id)
    if b is None:
        click.echo(f"No baseline for {source_id} yet.")
        return
    click.echo(
        f"{source_id}: samples={b['samples']}  p10={b['p10']:.3f}  "
        f"p50={b['p50']:.3f}  mean={b['mean']:.3f}"
    )
