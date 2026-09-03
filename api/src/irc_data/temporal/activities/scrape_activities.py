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
async def parse_certs() -> str:
    """Parse downloaded certificate PDFs into the database."""
    return run_cli_command(["parse-certs"])

@activity.defn
async def scrape_isora() -> str:
    """Scrape ISORA race results."""
    return run_cli_command(["scrape", "results", "--source", "isora"])

@activity.defn
async def scrape_rhkyc() -> str:
    """Scrape RHKYC race results."""
    return run_cli_command(["scrape", "results", "--source", "rhkyc"])

@activity.defn
async def scrape_sailracehq() -> str:
    """Scrape SailRaceHQ race results."""
    return run_cli_command(["scrape", "results", "--source", "sailracehq"])

@activity.defn
async def scrape_wayback() -> str:
    """Search Wayback Machine for historical IRC certificate PDFs."""
    return run_cli_command(["scrape", "wayback"])

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

@activity.defn
async def monitor_source_health() -> str:
    """Run the source monitor check for all baselined sources.

    Iterates over every row in ``source_baselines`` and re-fetches the
    canonical URL, comparing the result against the stored fingerprint.
    Material deviations quarantine publication and open incidents.
    """
    import json
    import httpx
    from irc_data.db.connection import get_engine
    from irc_data.diagnostics.source_monitor import (
        HEALTH_WEBHOOK_ENV,
        check_source,
        list_baselines,
    )

    engine = get_engine()
    baselines = list_baselines(engine)

    if not baselines:
        return "no baselines configured"

    # Health-check webhook for material deviations (SPEC-012 §6.2).
    webhook_url = os.environ.get(HEALTH_WEBHOOK_ENV) or os.environ.get("WEBHOOK_URL")

    results = []
    headers = {"User-Agent": "SailRatings/1.0 (+https://sailratings.com)"}

    for b in baselines:
        source_id = b["source_id"]
        url = b["url"]
        expected_ct = b.get("content_type") or "text/html"

        content = None
        fetch_success = True
        http_status = None
        content_type = expected_ct

        try:
            resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
            http_status = resp.status_code
            content_type = resp.headers.get("content-type", expected_ct)
            # Normalise content-type (strip charset etc.).
            content_type = content_type.split(";")[0].strip()
            if 200 <= resp.status_code < 300:
                content = resp.text
            else:
                fetch_success = False
        except Exception:
            fetch_success = False
            http_status = None

        event = check_source(
            engine,
            source_id,
            url,
            content=content,
            fetch_success=fetch_success,
            http_status=http_status,
            content_type=content_type,
            alert_webhook_url=webhook_url,
        )

        results.append({
            "source_id": source_id,
            "url": url,
            "status": event.status,
            "material": event.material,
            "quarantined": event.quarantined,
            "incident_id": event.incident_id,
        })

    material_count = sum(1 for r in results if r["material"])
    return json.dumps({
        "checked": len(results),
        "material_deviations": material_count,
        "results": results,
    })
