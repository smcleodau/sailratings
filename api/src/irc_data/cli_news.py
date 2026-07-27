"""CLI commands for the LLM news entity-extraction workflow (SPEC-14 Sec. 4).

Registered onto the top-level ``irc-data`` group as a ``news`` subgroup:

    irc-data news ingest --source scuttlebutt --url URL --title "..." [--body ...]
    irc-data news process [--limit 50]
    irc-data news process-one <news_id>
    irc-data news stats
"""

from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import text

console = Console()


def register_news_commands(cli) -> None:
    """Attach the ``news`` command group to the given Click group."""

    @cli.group(name="news")
    def news():
        """LLM news ingestion + entity extraction workflow (SPEC-14)."""
        pass

    @news.command(name="ingest")
    @click.option("--source", required=True, help="Feed/domain the article came from")
    @click.option("--title", required=True, help="Article title")
    @click.option("--url", "source_url", default=None, help="Article URL (dedup key)")
    @click.option("--body", "body_text", default=None, help="Article body text")
    @click.option("--author", default=None, help="Article author")
    @click.pass_context
    def news_ingest(ctx, source, title, source_url, body_text, author):
        """Ingest a news article into the unstructured raw_news table."""
        from irc_data.db.connection import get_engine
        from irc_data.news.workflow import ingest_news

        engine = get_engine() if ctx.obj is None else ctx.obj["engine"]
        nid = ingest_news(
            engine,
            source=source,
            title=title,
            body_text=body_text,
            source_url=source_url,
            author=author,
        )
        console.print(f"[green]Ingested article id={nid} (status=pending).[/green]")

    @news.command(name="process")
    @click.option("--limit", default=50, show_default=True, help="Max pending articles")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Show what would be extracted without calling the LLM (no-op flag for ops)",
    )
    @click.pass_context
    def news_process(ctx, limit, dry_run):
        """Run the extraction + linkage workflow over pending articles."""
        from irc_data.db.connection import get_engine
        from irc_data.news.workflow import process_pending

        engine = get_engine() if ctx.obj is None else ctx.obj["engine"]
        if dry_run:
            with engine.connect() as conn:
                n = conn.execute(
                    text("SELECT COUNT(*) FROM raw_news WHERE processing_status='pending'")
                ).scalar()
            console.print(f"[dim]{n} pending articles (dry-run; not processed).[/dim]")
            return

        console.print(f"[bold]Processing up to {limit} pending articles...[/bold]")
        outcomes = process_pending(engine, limit=limit)
        ok = sum(1 for o in outcomes if o.status == "processed")
        failed = sum(1 for o in outcomes if o.status == "failed")
        mentions = sum(len(o.matches) for o in outcomes)
        for o in outcomes:
            color = "green" if o.status == "processed" else "red"
            console.print(
                f"  [{color}]id={o.news_id} {o.status}[/{color}] "
                f"boats={len(o.matches)} "
                + (f"error={o.error}" if o.error else "")
            )
        console.print(
            f"\n[bold]Done:[/bold] {ok} processed, {failed} failed, "
            f"{mentions} boat mentions linked."
        )

    @news.command(name="process-one")
    @click.argument("news_id", type=int)
    @click.pass_context
    def news_process_one(ctx, news_id):
        """Run the workflow for a single article by id."""
        from irc_data.db.connection import get_engine
        from irc_data.news.workflow import process_news_article

        engine = get_engine() if ctx.obj is None else ctx.obj["engine"]
        outcome = process_news_article(engine, news_id)
        console.print_json(json.dumps(outcome.to_dict(), default=str))

    @news.command(name="stats")
    @click.pass_context
    def news_stats(ctx):
        """Show raw_news / boat_news_mentions counts and status breakdown."""
        engine = get_engine() if ctx.obj is None else ctx.obj["engine"]
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM raw_news")).scalar() or 0
            mentions = conn.execute(
                text("SELECT COUNT(*) FROM boat_news_mentions")
            ).scalar() or 0
            status_rows = conn.execute(
                text(
                    "SELECT processing_status, COUNT(*) FROM raw_news "
                    "GROUP BY processing_status ORDER BY processing_status"
                )
            ).fetchall()

        console.print(f"[bold]raw_news:[/bold] {total} articles")
        console.print(f"[bold]boat_news_mentions:[/bold] {mentions} links")
        table = Table(title="Status breakdown")
        table.add_column("Status", style="bold")
        table.add_column("Count", justify="right")
        for status, count in status_rows:
            table.add_row(status, str(count))
        console.print(table)
