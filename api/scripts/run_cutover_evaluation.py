#!/usr/bin/env python3
"""Run Firecrawl Cutover Evaluation.

Retests sources, computes gate metrics, and prints the cutover readiness report.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from sqlalchemy import text

# Add 'src' to PYTHONPATH automatically
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
except ImportError:
    # Safe fallback if rich is not available (though it is a project dependency)
    class Console:
        def print(self, *args, **kwargs):
            print(*args, **kwargs)
    Table = None
    box = None

from irc_data.db.connection import get_engine


def run_command(cmd: list[str], cwd: Path) -> bool:
    """Run a shell command and stream output."""
    console = Console()
    console.print(f"[bold blue]Running command:[/bold blue] {' '.join(cmd)}")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        if process.stdout:
            for line in process.stdout:
                print(line, end="")
        process.wait()
        return process.returncode == 0
    except Exception as e:
        console.print(f"[bold red]Command failed to start:[/bold red] {e}")
        return False


def get_gate_metrics(engine):
    """Retrieve gate metrics for the last 14 days, excluding hollow legacy rows."""
    query = text("""
        SELECT
            source,
            COUNT(*)                                                          AS urls,
            ROUND(AVG(match_rate)::numeric, 3)                               AS mean_recall,
            ROUND(
                percentile_cont(0.10) WITHIN GROUP (ORDER BY match_rate)::numeric,
                3
            )                                                                AS p10_recall
        FROM firecrawl_diffs
        WHERE ran_at >= NOW() - INTERVAL '14 days'
          AND legacy_rows IS NOT NULL AND legacy_rows > 0
        GROUP BY source
        ORDER BY mean_recall DESC;
    """)
    with engine.connect() as conn:
        return conn.execute(query).fetchall()


def print_report(metrics):
    console = Console()
    
    console.print("\n[bold]========================================================================[/bold]")
    console.print("[bold cyan]                  FIRECRAWL CUTOVER READINESS REPORT                  [/bold cyan]")
    console.print("[bold]========================================================================[/bold]\n")
    
    if Table:
        table = Table(box=box.DOUBLE_EDGE, header_style="bold magenta")
        table.add_column("Source", style="cyan", width=12)
        table.add_column("URLs Sampled", justify="right", width=12)
        table.add_column("Mean Recall (Goal: ≥0.85)", justify="right", width=25)
        table.add_column("P10 Recall (Goal: ≥0.75)", justify="right", width=24)
        table.add_column("Gate Status", justify="center", width=20)
        
        for m in metrics:
            urls = m.urls
            mean = float(m.mean_recall)
            p10 = float(m.p10_recall)
            
            # Determine Gate Status
            is_green = (urls >= 20) and (mean >= 0.85) and (p10 >= 0.75)
            if is_green:
                status = "[green]PASS (Ready)[/green]"
            elif urls < 20:
                status = "[yellow]FAIL (Sample < 20)[/yellow]"
            elif mean < 0.85:
                status = "[red]FAIL (Mean < 0.85)[/red]"
            else:
                status = "[red]FAIL (P10 < 0.75)[/red]"
                
            mean_str = f"{mean:.3f} " + ("[green]✔[/green]" if mean >= 0.85 else "[red]✘[/red]")
            p10_str = f"{p10:.3f} " + ("[green]✔[/green]" if p10 >= 0.75 else "[red]✘[/red]")
            table.add_row(m.source, str(urls), mean_str, p10_str, status)
            
        console.print(table)
    else:
        # Fallback formatting
        for m in metrics:
            console.print(f"Source: {m.source}")
            console.print(f"  URLs Sampled: {m.urls}")
            console.print(f"  Mean Recall:  {m.mean_recall:.3f}")
            console.print(f"  P10 Recall:   {m.p10_recall:.3f}")
            console.print()
            
    console.print("\n[bold cyan]Actionable Recommendations:[/bold cyan]")
    
    for m in metrics:
        source = m.source
        urls = m.urls
        mean = float(m.mean_recall)
        p10 = float(m.p10_recall)
        
        console.print(f"\n[bold underline]{source.upper()}[/bold underline]:")
        
        if urls >= 20 and mean >= 0.85 and p10 >= 0.75:
            console.print(f"  [green]✔ CUTOVER APPROVED.[/green] Legacy scraper `{source}.py` can be safely retired.")
            console.print("  Follow the steps in [blue]api/docs/scrapers/firecrawl-cutover-runbook.md[/blue] to complete transition.")
        else:
            reasons = []
            if urls < 20:
                reasons.append(f"insufficient sample size ({urls}/20 URLs)")
            if mean < 0.85:
                reasons.append(f"mean recall ({mean:.3f}) below 0.85 floor")
            if p10 < 0.75:
                reasons.append(f"P10 recall ({p10:.3f}) below 0.75 floor")
                
            console.print(f"  [red]✘ CUTOVER HELD[/red] due to: {', '.join(reasons)}.")
            
            if source == "rhkyc":
                console.print("  [yellow]Recommendation:[/yellow] RHKYC has very high quality. Run with `--limit 20` to grow the sample and pass the gate.")
            elif source == "isora":
                console.print("  [yellow]Recommendation:[/yellow] Check the multi-class chunking. If P10 is still low, inspect the failure cases in `firecrawl_diffs` using `inspect_isora_diffs.py`.")
            elif source == "cowesweek":
                console.print("  [yellow]Recommendation:[/yellow] Investigate name-matching normalized differences. Many 'missing' boats might actually be named slightly differently in legacy data.")
            elif source == "sailracehq":
                console.print("  [yellow]Recommendation:[/yellow] Ensure JS-rendering is supported for Caribbean 600 or migrate complex pages manually.")


def main():
    ap = argparse.ArgumentParser(description="Evaluate sources for Firecrawl cutover.")
    ap.add_argument("--skip-scrapes", action="store_true", help="Only view the current gate report without executing new scrapes.")
    ap.add_argument("--limit-rhkyc", type=int, default=20, help="Number of URLs to sample for RHKYC (default: 20).")
    ap.add_argument("--limit-isora", type=int, default=10, help="Number of URLs to sample for ISORA (default: 10).")
    args = ap.parse_args()

    api_dir = Path(__file__).resolve().parent.parent
    venv_irc_data = api_dir / ".venv" / "bin" / "irc-data"
    
    if not venv_irc_data.exists():
        venv_irc_data = Path("irc-data") # fallback to path search
        
    console = Console()

    if not args.skip_scrapes:
        console.print("[bold cyan]Step 1: Running comparison scrapes to grow evaluation dataset...[/bold cyan]\n")
        
        # Run RHKYC
        cmd_rhkyc = [str(venv_irc_data), "firecrawl-diff", "--source", "rhkyc", "--limit", str(args.limit_rhkyc), "--days", "9999"]
        run_command(cmd_rhkyc, api_dir)
        
        # Run ISORA
        cmd_isora = [str(venv_irc_data), "firecrawl-diff", "--source", "isora", "--limit", str(args.limit_isora), "--days", "9999"]
        run_command(cmd_isora, api_dir)
        
    console.print("\n[bold cyan]Step 2: Calculating gate metrics from database...[/bold cyan]")
    try:
        engine = get_engine()
        metrics = get_gate_metrics(engine)
        if not metrics:
            console.print("[yellow]No gate evaluation metrics found in the database. Ensure comparisons have been run.[/yellow]")
        else:
            print_report(metrics)
    except Exception as e:
        console.print(f"[bold red]Database query failed:[/bold red] {e}")


if __name__ == "__main__":
    main()
