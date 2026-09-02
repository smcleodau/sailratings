"""CLI for the DP-05-05 data-plane drill (load / resilience / DR).

Usage
-----

Run the full drill and print the signed report as JSON::

    irc-data dr-drill

Write the signed report to a file and tune the load::

    irc-data dr-drill --volume 5000 --concurrency 8 --per-adapter 200 \
        --out report.json

The report is signed with the key in the ``DP05_DRILL_SIGNING_KEY``
environment variable when set; otherwise a fresh key is generated for
the run (the report records only the key id, never the key).

Exit status is ``0`` when the drill passes (overall status
``passed``), ``2`` when any scenario failed — mirroring the
reconciliation CLI's convention.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from irc_data.resilience.contracts import SIGNING_KEY_ENV
from irc_data.resilience.drill import DataPlaneDrill, DrillConfig

console = Console()


@click.command(name="dr-drill")
@click.option(
    "--volume",
    default=None,
    type=int,
    help="Synthetic artifact volume for the high-volume scenario "
    "(default: the harness's production-sized default).",
)
@click.option(
    "--concurrency",
    default=None,
    type=int,
    help="Number of concurrent adapters in the concurrency scenario.",
)
@click.option(
    "--per-adapter",
    default=None,
    type=int,
    help="Artifacts each concurrent adapter ingests.",
)
@click.option(
    "--backfill-batch",
    default=None,
    type=int,
    help="Max artifacts the backfill replay selects.",
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
    help="Directory for the drill's DB / raw lake (default: a temp dir).",
)
@click.option("--quiet", is_flag=True, help="Suppress the summary table.")
def dr_drill(
    volume: int | None,
    concurrency: int | None,
    per_adapter: int | None,
    backfill_batch: int | None,
    out: Path | None,
    work_dir: Path | None,
    quiet: bool,
) -> None:
    """Run the DP-05-05 load / resilience / disaster-recovery drill.

    Produces a signed report of the measured safe operating envelope.
    """
    key_env = os.environ.get(SIGNING_KEY_ENV)
    config_kwargs: dict = {}
    if volume is not None:
        config_kwargs["artifact_volume"] = volume
    if concurrency is not None:
        config_kwargs["concurrent_adapters"] = concurrency
    if per_adapter is not None:
        config_kwargs["per_adapter_volume"] = per_adapter
    if backfill_batch is not None:
        config_kwargs["backfill_batch"] = backfill_batch
    if work_dir is not None:
        config_kwargs["work_dir"] = work_dir
    if key_env:
        config_kwargs["signing_key"] = key_env.encode("utf-8")
        config_kwargs["signing_key_id"] = "env:" + SIGNING_KEY_ENV

    config = DrillConfig(**config_kwargs)

    with DataPlaneDrill(config) as drill:
        report = drill.run()

    report_json = report.to_json()
    if out is not None:
        out.write_text(report_json)
        console.print(f"[green]Signed report written to {out}[/green]")

    if not quiet:
        table = Table(title=f"DP-05-05 drill — {report.report_id}")
        table.add_column("Scenario", style="cyan")
        table.add_column("Status")
        table.add_column("Vol", justify="right")
        table.add_column("Throughput/s", justify="right")
        table.add_column("RPO s", justify="right")
        table.add_column("RTO s", justify="right")
        for s in report.scenarios:
            status = (
                "[green]passed[/green]"
                if s.status == "passed"
                else "[red]failed[/red]"
            )
            table.add_row(
                s.scenario,
                status,
                str(s.volume),
                f"{s.throughput_per_second:.2f}"
                if s.throughput_per_second is not None
                else "-",
                f"{s.rpo_seconds:.3f}" if s.rpo_seconds is not None else "-",
                f"{s.rto_seconds:.3f}" if s.rto_seconds is not None else "-",
            )
        console.print(table)
        console.print(
            f"overall={report.overall_status} "
            f"artifacts={report.artifact_volume} "
            f"agg_throughput={report.aggregate_throughput_per_second}/s "
            f"RPO={report.measured_rpo_seconds}s "
            f"RTO={report.measured_rto_seconds}s"
        )
        ac = report.passed_acceptance_criteria
        for name, ok in ac.items():
            mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"  {mark} {name}")

    # Always emit the signed JSON to stdout so it can be piped.
    if out is None:
        sys.stdout.write(report_json + "\n")

    sys.exit(0 if report.overall_status == "passed" else 2)
