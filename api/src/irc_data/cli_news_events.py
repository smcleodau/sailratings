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
    @click.option("--feed", "feeds", multiple=True,
                  help="RSS/Atom feed URL to read (repeatable). Defaults to the "
                       "approved sailing-news feeds (incl. sailingscuttlebutt).")
    @click.option("--url", "legacy_url", default=None,
                  help="DEPRECATED: legacy Firecrawl seed URL. News now comes "
                       "from RSS raw-capture; this flag only works with "
                       "ALLOW_FIRECRAWL_NEWS=1 and spends crawl credits.")
    @click.option("--limit", type=int, default=5, help="Max articles to process")
    def scrape_news_cmd(feeds, legacy_url, limit):
        """Scrape sailing news via RSS and extract boat mentions (Firecrawl-free).

        Reads the approved sailing-news RSS/Atom feeds directly (plain httpx +
        stdlib XML parsing), fetches each new article's HTML, and extracts
        boat mentions with Gemini. No Firecrawl credits are spent on news
        domains (OPS-02-06).
        """
        import asyncio
        if legacy_url:
            # Explicit operator override of the deprecated path.
            from irc_data.scrapers.news import scrape_news_source
            asyncio.run(scrape_news_source(legacy_url, limit))
            return
        from irc_data.scrapers.news import scrape_news_rss
        stats = asyncio.run(
            scrape_news_rss(list(feeds) if feeds else None, limit)
        )
        console.print(
            f"[green]News ingest complete[/green] feeds={stats['feeds']} "
            f"seen={stats['articles_seen']} new={stats['articles_new']} "
            f"processed={stats['articles_processed']} "
            f"mentions_matched={stats['mentions_matched']}"
        )
