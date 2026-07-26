import click
from rich.console import Console
from irc_data.db.connection import get_engine

console = Console()

def register_news_and_events_commands(cli):
    @cli.command(name="generate-boat-events")
    @click.option("--days", type=int, default=7, help="How many days back to scan")
    def generate_boat_events_cmd(days):
        """Generate boat_events from recent results and certs."""
        from irc_data.db.event_feed import generate_boat_events
        engine = get_engine()
        console.print(f"Generating boat events for the last {days} days...")
        stats = generate_boat_events(engine, days_back=days)
        console.print(f"[green]Added {stats['races_added']} race events and {stats['certs_added']} cert events.[/green]")

    @cli.command(name="scrape-news")
    @click.option("--url", default="https://www.sailingscuttlebutt.com/", help="Seed URL to map")
    @click.option("--limit", type=int, default=5, help="Max articles to process")
    def scrape_news_cmd(url, limit):
        """Scrape sailing news and extract boat mentions."""
        import asyncio
        from irc_data.scrapers.news import scrape_news_source
        asyncio.run(scrape_news_source(url, limit))
