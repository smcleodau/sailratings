"""Health check and monitoring with webhook notifications.

Checks data freshness, DB connectivity, and disk usage.
Sends alerts to Discord/Slack webhooks when thresholds are breached.
"""

import os
import shutil
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Engine


def check_health(engine: Engine) -> dict:
    """Run all health checks and return a structured report.

    Returns a dict with status, checks, alerts, and counts.
    """
    now = datetime.now(timezone.utc)
    alerts = []
    checks = {}

    # --- Database connectivity ---
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        alerts.append(f"Database connection failed: {e}")

    if checks["db"] != "ok":
        return {"status": "critical", "checks": checks, "alerts": alerts, "counts": {}}

    with engine.connect() as conn:
        # --- Data counts ---
        counts = {}
        for table in ["boats", "tcc_snapshots", "certificates", "orc_certificates", "race_results"]:
            counts[table] = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()

        checks["counts"] = counts

        # --- ORC freshness ---
        latest_orc = conn.execute(text(
            "SELECT MAX(snapshot_date) FROM orc_snapshots"
        )).scalar()
        checks["orc_latest"] = latest_orc.isoformat() if latest_orc else None

        if latest_orc:
            hours_since_orc = (now.date() - latest_orc).days * 24
            checks["hours_since_orc"] = hours_since_orc
            if hours_since_orc > 26:
                alerts.append(f"ORC snapshot is {hours_since_orc}h old (threshold: 26h)")

        # --- IRC freshness ---
        latest_irc = conn.execute(text(
            "SELECT MAX(snapshot_date) FROM tcc_snapshots"
        )).scalar()
        checks["irc_latest"] = latest_irc.isoformat() if latest_irc else None

        if latest_irc:
            days_since_irc = (now.date() - latest_irc).days
            checks["days_since_irc"] = days_since_irc
            if days_since_irc > 8:
                alerts.append(f"IRC TCC snapshot is {days_since_irc} days old (threshold: 8)")

        # --- Scraper failures in last 24h ---
        failures = conn.execute(text("""
            SELECT source, error_message, started_at
            FROM ingestion_log
            WHERE status = 'failed'
              AND started_at > :since
            ORDER BY started_at DESC
        """), {"since": now - timedelta(hours=24)}).fetchall()

        if failures:
            for f in failures:
                alerts.append(f"Scraper '{f.source}' failed at {f.started_at}: {f.error_message}")
            checks["failed_scrapers_24h"] = len(failures)
        else:
            checks["failed_scrapers_24h"] = 0

        # --- Stale running jobs (stuck for >2h) ---
        stale = conn.execute(text("""
            SELECT id, source, started_at
            FROM ingestion_log
            WHERE status = 'running'
              AND started_at < :since
        """), {"since": now - timedelta(hours=2)}).fetchall()

        if stale:
            for s in stale:
                alerts.append(f"Scraper '{s.source}' (id={s.id}) has been running since {s.started_at}")
            checks["stale_jobs"] = len(stale)

    # --- Disk usage ---
    try:
        usage = shutil.disk_usage("/")
        pct = usage.used / usage.total * 100
        checks["disk_usage_pct"] = round(pct, 1)
        if pct > 80:
            alerts.append(f"Disk usage is {pct:.0f}% (threshold: 80%)")
    except Exception:
        pass

    status = "ok" if not alerts else "warning"
    return {
        "status": status,
        "checks": checks,
        "alerts": alerts,
        "counts": counts,
        "timestamp": now.isoformat(),
    }


def send_webhook(url: str, report: dict) -> bool:
    """Send a health check report to a Discord or Slack webhook.

    Formats the report as an embed (Discord) or attachment (Slack).
    Returns True if sent successfully.
    """
    if not url:
        return False

    alerts = report.get("alerts", [])
    status = report.get("status", "unknown")
    counts = report.get("counts", {})

    color = 0x00FF00 if status == "ok" else 0xFF0000  # Green or Red

    if "discord" in url.lower():
        # Discord webhook format
        embed = {
            "title": f"Sailing Data Health: {status.upper()}",
            "color": color,
            "fields": [
                {"name": "Boats", "value": str(counts.get("boats", "?")), "inline": True},
                {"name": "ORC Certs", "value": str(counts.get("orc_certificates", "?")), "inline": True},
                {"name": "Race Results", "value": str(counts.get("race_results", "?")), "inline": True},
            ],
            "timestamp": report.get("timestamp"),
        }
        if alerts:
            embed["fields"].append({
                "name": "Alerts",
                "value": "\n".join(f"- {a}" for a in alerts[:5]),
                "inline": False,
            })

        payload = {"embeds": [embed]}
    else:
        # Slack webhook format
        text_parts = [f"*Sailing Data Health: {status.upper()}*"]
        text_parts.append(f"Boats: {counts.get('boats', '?')} | ORC: {counts.get('orc_certificates', '?')} | Results: {counts.get('race_results', '?')}")
        if alerts:
            text_parts.append("*Alerts:*")
            for a in alerts[:5]:
                text_parts.append(f"  - {a}")
        payload = {"text": "\n".join(text_parts)}

    try:
        resp = httpx.post(url, json=payload, timeout=10)
        return resp.status_code < 300
    except Exception:
        return False
