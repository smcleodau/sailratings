"""CLI for the DP-06-05 soak test + failure drill.

Usage
-----

Run the full soak + failure drill and print the signed report as JSON::

    irc-data ops-soak

Write the signed report to a file and tune the soak::

    irc-data ops-soak --cycles 7 --slo-seconds 30 --out report.json

The report is signed with the key in the ``DP06_SOAK_SIGNING_KEY``
environment variable when set; otherwise a fresh key is generated for the
run (the report records only the key id, never the key).

Exit status is ``0`` when the soak passes (overall status ``passed``),
``2`` when any cycle breached its SLO or any failure-drill check failed —
mirroring the DP-05-05 drill CLI's convention.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from irc_data.operations.contracts import SIGNING_KEY_ENV
from irc_data.operations.soak import SoakConfig, SourceOpsSoak

console = Console()


@click.command(name="ops-soak")
@click.option(
    "--cycles",
    default=None,
    type=int,
    help="Number of consecutive scheduled cycles (default: 7 — the "
    "acceptance-criterion count).",
)
@click.option(
    "--slo-seconds",
    default=None,
    type=float,
    help="Per-cycle SLO budget in seconds.",
)
@click.option(
    "--cadence",
    default=None,
    type=str,
    help="Register cadence for the source under test (e.g. 30min, nightly).",
)
@click.option(
    "--staleness-budget-hours",
    default=None,
    type=float,
    help="Watchdog staleness budget for the failure drill.",
)
@click.option(
    "--pages",
    default=None,
    type=int,
    help="Synthetic pages each cycle collects.",
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=None,
    help="Write the signed report JSON to this file.",
)
@click.option(
    "--work-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory for the soak's DB / checkpoint backups (default: temp dir).",
)
@click.option("--quiet", is_flag=True, help="Suppress the summary table.")
def ops_soak(
    cycles: int | None,
    slo_seconds: float | None,
    cadence: str | None,
    staleness_budget_hours: float | None,
    pages: int | None,
    out: Path | None,
    work_dir: Path | None,
    quiet: bool,
) -> None:
    """Run the DP-06-05 soak test + failure drill.

    Produces the signed report that evidences continuous operation: seven
    consecutive scheduled cycles within SLO, and a deliberate source failure
    that alerts and recovers without duplicate publication.
    """
    key_env = os.environ.get(SIGNING_KEY_ENV)
    config_kwargs: dict = {}
    if cycles is not None:
        config_kwargs["cycles"] = cycles
    if slo_seconds is not None:
        config_kwargs["cycle_slo_seconds"] = slo_seconds
    if cadence is not None:
        config_kwargs["cadence"] = cadence
    if staleness_budget_hours is not None:
        config_kwargs["staleness_budget_hours"] = staleness_budget_hours
    if pages is not None:
        config_kwargs["pages"] = pages
    if work_dir is not None:
        config_kwargs["work_dir"] = work_dir
    if key_env:
        config_kwargs["signing_key"] = key_env.encode("utf-8")
        config_kwargs["signing_key_id"] = "env:" + SIGNING_KEY_ENV

    config = SoakConfig(**config_kwargs)

    with SourceOpsSoak(config) as soak:
        report = soak.run()

    report_json = report.to_json()
    if out is not None:
        out.write_text(report_json)
        console.print(f"[green]Signed report written to {out}[/green]")

    if not quiet:
        table = Table(title=f"DP-06-05 soak — {report.report_id}")
        table.add_column("Cycle", style="cyan", justify="right")
        table.add_column("Status")
        table.add_column("Duration s", justify="right")
        table.add_column("SLO s", justify="right")
        table.add_column("Ledger", justify="right")
        table.add_column("New", justify="right")
        for c in report.cycles:
            status = (
                "[green]passed[/green]"
                if c.status == "passed"
                else "[red]failed[/red]"
            )
            table.add_row(
                str(c.cycle),
                status,
                f"{c.duration_seconds:.3f}",
                f"{c.slo_seconds:.0f}",
                str(c.ledger_rows),
                str(c.records_new),
            )
        console.print(table)

        atable = Table(title="Soak / failure-drill artifacts")
        atable.add_column("Artifact", style="cyan")
        atable.add_column("Status")
        atable.add_column("Detail")
        for a in report.artifacts:
            status = (
                "[green]passed[/green]"
                if a["status"] == "passed"
                else "[red]failed[/red]"
            )
            atable.add_row(a["artifact"], status, a["detail"])
        console.print(atable)

        console.print(
            f"overall={report.overall_status} "
            f"cycles_within_slo={report.cycles_within_slo}/{report.cycles_required} "
            f"consecutive={report.consecutive_cycles_within_slo} "
            f"no_duplicate_publication={report.no_duplicate_publication} "
            f"duration={report.duration_seconds}s"
        )
        for name, ok in report.passed_acceptance_criteria.items():
            mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"  {mark} {name}")

    # Always emit the signed JSON to stdout so it can be piped.
    if out is None:
        sys.stdout.write(report_json + "\n")

    sys.exit(0 if report.overall_status == "passed" else 2)
