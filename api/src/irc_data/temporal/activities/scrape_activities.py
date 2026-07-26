from temporalio import activity
import subprocess
import os

def run_cli_command(command: list[str]) -> str:
    """Helper to run irc-data CLI commands."""
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    
    result = subprocess.run(
        ["irc-data"] + command,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with code {result.returncode}\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    return result.stdout

@activity.defn
async def scrape_orc() -> str:
    """Daily full ORC snapshot."""
    return run_cli_command(["scrape", "orc"])

@activity.defn
async def match_boats_orc_only() -> str:
    """Match ORC certs to IRC boats."""
    return run_cli_command(["match-boats", "--orc-only"])

@activity.defn
async def scrape_orc_detail(limit: int = 500) -> str:
    """Backfill ORC details."""
    return run_cli_command(["scrape", "orc-detail", "--backlog", "--limit", str(limit)])

@activity.defn
async def refresh_views() -> str:
    """Refresh materialized views."""
    return run_cli_command(["refresh-views"])

@activity.defn
async def scrape_tcc() -> str:
    """Scrape IRC TCC listings."""
    return run_cli_command(["scrape", "tcc"])

@activity.defn
async def scrape_sailsys() -> str:
    """Scrape results from all SailSys clubs."""
    return run_cli_command(["scrape", "results", "--source", "sailsys", "--all-clubs"])

@activity.defn
async def rematch_results() -> str:
    """Rematch race results to boats."""
    return run_cli_command(["rematch-results"])

@activity.defn
async def scrape_topyacht() -> str:
    """Scrape TopYacht incrementally."""
    return run_cli_command(["scrape", "results", "--source", "topyacht", "--incremental", "--store"])

@activity.defn
async def scrape_certs_exhaustive() -> str:
    """Exhaustive 2-letter search for IRC certificates."""
    return run_cli_command(["scrape", "certs", "--exhaustive"])

@activity.defn
async def scrape_wayback_tcc() -> str:
    """Harvest historical TCC files from Wayback Machine."""
    return run_cli_command(["wayback-tcc"])

@activity.defn
async def discover_events() -> str:
    """Crawl for new events using Firecrawl."""
    return run_cli_command(["seed-crawl", "--aggregators"])

@activity.defn
async def generate_boat_events() -> str:
    """Generate the boat events feed from recent data."""
    return run_cli_command(["generate-boat-events"])

@activity.defn
async def scrape_boat_news() -> str:
    """Scrape boat news via Firecrawl and Claude."""
    return run_cli_command(["scrape-news"])
