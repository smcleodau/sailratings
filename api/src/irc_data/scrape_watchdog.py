"""Staleness watchdog with alerting, cooldown, and recovery (OPS-01-04).

Know within 15 minutes when any source has gone quiet.

This module is the engine behind the ``irc-data scrape-watchdog`` CLI,
which cron runs every 15 minutes. It:

1. **Staleness check vs budget** — reads :mod:`irc_data.scrape_supervision`
   ``SOURCES`` for expected cadences and queries ``ingestion_log`` (cron
   health) and ``race_results`` (data-tap freshness) for last activity.
2. **Alerting** — on a breach, sends one consolidated email via Resend and
   records the incident in the ``watchdog_alerts`` table. The admin banner
   ("Cron health: N sources not running") is rendered from the same
   supervision config on ``/admin/scrapers``.
3. **Cooldown** — a source that alerted is not re-alerted (or re-logged)
   for ``cooldown_hours`` (default 4 h), even while it remains stale.
4. **Recovery** — when a previously-alerted source comes back within
   budget, the open alert row is closed (``recovered_at`` set) and a
   recovery email is sent.
5. **Alert log** — every alert and recovery is retained in
   ``watchdog_alerts`` for history; never deleted by the watchdog.

The cooldown / alert-history state lives in Postgres (not a JSON file) so
it survives host rebuilds and is inspectable from the admin dashboard.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from irc_data.scrape_supervision import SOURCES, SourceConfig

DEFAULT_COOLDOWN_HOURS = 4

# --- Watchdog alert lifecycle states -------------------------------------
STATUS_ACTIVE = "active"        # breach ongoing, alert sent, within cooldown
STATUS_RECOVERED = "recovered"  # breach cleared; row retained as history


@dataclass
class Breach:
    """One source outside one of its freshness budgets."""

    alert_key: str          # unique identity: "<source>" or "<source>:data"
    source: str             # ingestion_log.source value
    signal: str             # "run" (cron health) or "data" (data tap)
    label: str
    cadence: str
    age_hours: float | None  # None => never succeeded
    budget_hours: float
    reason: str

    def age_str(self) -> str:
        return "never" if self.age_hours is None else f"{self.age_hours:.1f}h"


@dataclass
class WatchdogResult:
    """Outcome of one watchdog pass — returned for tests and CLI printing."""

    breaches: list[Breach] = field(default_factory=list)
    alerts_sent: list[Breach] = field(default_factory=list)
    in_cooldown: list[Breach] = field(default_factory=list)
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    email_sent: bool = False
    recovery_email_sent: bool = False
    skipped_send: bool = False  # dry-run or missing API key


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_watchdog_table(conn: Connection) -> None:
    """Create the alert-log table if missing.

    Alembic migration 0025 owns the DDL in production; this keeps the CLI
    and the test-suite (SQLite) self-sufficient, matching the pattern used
    by the source monitor.
    """
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS watchdog_alerts (
            id              INTEGER PRIMARY KEY,
            alert_key       TEXT NOT NULL,
            source          TEXT NOT NULL,
            signal          TEXT NOT NULL,
            label           TEXT,
            cadence         TEXT,
            reason          TEXT,
            age_hours       DOUBLE PRECISION,
            budget_hours    DOUBLE PRECISION,
            status          TEXT NOT NULL DEFAULT 'active',
            first_seen_at   TIMESTAMP NOT NULL,
            alerted_at      TIMESTAMP NOT NULL,
            cooldown_until  TIMESTAMP,
            recovered_at    TIMESTAMP,
            details         TEXT
        )
    """))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_watchdog_alerts_source "
        "ON watchdog_alerts (source)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_watchdog_alerts_status "
        "ON watchdog_alerts (status)"
    ))


# ---------------------------------------------------------------------------
# Staleness check vs budget
# ---------------------------------------------------------------------------

def compute_breaches(
    conn: Connection,
    now: datetime,
    sources: Iterable[SourceConfig] | None = None,
) -> list[Breach]:
    """Compare each configured source's last activity against its budget.

    Two signals per source (kept separate — see scrape_supervision docs):

    * run health  — gap since last successful ``ingestion_log`` row
    * data tap    — gap since last new ``race_results`` row (when configured)
    """
    sources = list(sources) if sources is not None else SOURCES

    rows = conn.execute(text("""
        SELECT source,
               MAX(started_at) AS last_started,
               MAX(completed_at) FILTER (WHERE status='completed') AS last_success
        FROM ingestion_log
        GROUP BY source
    """)).fetchall()
    data_rows = conn.execute(text("""
        SELECT source, MAX(created_at) AS last_new_data
        FROM race_results
        GROUP BY source
    """)).fetchall()
    by_src = {r.source: r for r in rows}
    by_src_data = {r.source: r for r in data_rows}

    breaches: list[Breach] = []
    for cfg in sources:
        if cfg.optional:
            continue
        r = by_src.get(cfg.source)
        last_success = _aware(r.last_success) if r and r.last_success else None

        # Run-cadence check — the cron-health signal
        if last_success is None:
            breaches.append(Breach(
                alert_key=cfg.source, source=cfg.source, signal="run",
                label=cfg.label, cadence=cfg.cadence_human,
                age_hours=None,
                budget_hours=cfg.run_within.total_seconds() / 3600,
                reason="no successful run on record",
            ))
        else:
            run_age = now - last_success
            if run_age > cfg.run_within:
                breaches.append(Breach(
                    alert_key=cfg.source, source=cfg.source, signal="run",
                    label=cfg.label, cadence=cfg.cadence_human,
                    age_hours=run_age.total_seconds() / 3600,
                    budget_hours=cfg.run_within.total_seconds() / 3600,
                    reason="cron stopped (no successful run)",
                ))

        # Data-tap check — only when configured (long budget that survives lulls)
        if cfg.data_within is not None:
            dr = by_src_data.get(cfg.source)
            last_new = _aware(dr.last_new_data) if dr and dr.last_new_data else None
            if last_new is not None:
                data_age = now - last_new
                if data_age > cfg.data_within:
                    breaches.append(Breach(
                        alert_key=f"{cfg.source}:data", source=cfg.source,
                        signal="data", label=cfg.label + " (no new data)",
                        cadence=cfg.cadence_human,
                        age_hours=data_age.total_seconds() / 3600,
                        budget_hours=cfg.data_within.total_seconds() / 3600,
                        reason="no new race rows beyond seasonal lull",
                    ))

    return breaches


def _aware(dt: datetime | str) -> datetime:
    """Normalise a DB timestamp to timezone-aware UTC.

    Postgres returns ``datetime``; SQLite (tests) returns strings — accept
    both so the watchdog logic is storage-agnostic.
    """
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Alert log queries
# ---------------------------------------------------------------------------

def get_active_alerts(conn: Connection) -> list[dict[str, Any]]:
    """Currently-raised (unrecovered) alert rows — powers the admin banner."""
    rows = conn.execute(text("""
        SELECT id, alert_key, source, signal, label, cadence, reason,
               age_hours, budget_hours, status, first_seen_at, alerted_at,
               cooldown_until, recovered_at
        FROM watchdog_alerts
        WHERE status = 'active'
        ORDER BY alerted_at DESC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


def get_alert_history(conn: Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Retained alert history — most recent first, alerts and recoveries."""
    rows = conn.execute(text("""
        SELECT id, alert_key, source, signal, label, cadence, reason,
               age_hours, budget_hours, status, first_seen_at, alerted_at,
               cooldown_until, recovered_at
        FROM watchdog_alerts
        ORDER BY alerted_at DESC
        LIMIT :limit
    """), {"limit": limit}).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _default_email_sender(subject: str, html: str) -> None:
    """Send via Resend. Raises if RESEND_API_KEY is not configured."""
    import resend

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not configured")
    resend.api_key = api_key
    alert_email = os.environ.get("ALERT_EMAIL", "stuart@stuartmcleod.me")
    resend.Emails.send({
        "from": "SailRatings Watchdog <reports@sailratings.com>",
        "to": [alert_email],
        "subject": subject,
        "html": html,
    })


def build_alert_email(breaches: list[Breach], cooldown_hours: int) -> tuple[str, str]:
    rows_html = "\n".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>"
        f"<strong>{b.label}</strong><br/>"
        f"<span style='color:#777;font-size:12px'>{b.source} · {b.cadence}</span></td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;color:#a00;font-family:monospace'>"
        f"{b.age_str()} / {b.budget_hours:.0f}h</td></tr>"
        for b in breaches
    )
    subject = f"SailRatings watchdog — {len(breaches)} scraper(s) stale"
    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:560px;margin:auto;color:#222">
      <h2 style="color:#0A2240">SailRatings scraper alert</h2>
      <p>The watchdog noticed {len(breaches)} scraper{'s' if len(breaches) != 1 else ''}
         outside their freshness budget.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead><tr style="background:#F4F1E8;text-align:left">
          <th style="padding:8px 12px">Source</th><th style="padding:8px 12px">Stale for / budget</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="color:#777;font-size:12px">Open the dashboard:
        <a href="https://dev.sailratings.com/justin/scrapers">/justin/scrapers</a></p>
      <p style="color:#777;font-size:12px">Cooldown: same source won't alert again for {cooldown_hours}h.</p>
    </div>
    """
    return subject, html


def build_recovery_email(recoveries: list[dict[str, Any]]) -> tuple[str, str]:
    rows_html = "\n".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>"
        f"<strong>{r['label'] or r['source']}</strong><br/>"
        f"<span style='color:#777;font-size:12px'>{r['source']} · {r.get('cadence') or ''}</span></td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;color:#1a7f37;font-family:monospace'>"
        f"recovered</td></tr>"
        for r in recoveries
    )
    subject = f"SailRatings watchdog — {len(recoveries)} scraper(s) recovered"
    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:560px;margin:auto;color:#222">
      <h2 style="color:#0A2240">SailRatings scraper recovery</h2>
      <p>{len(recoveries)} previously-stale scraper{'s' if len(recoveries) != 1 else ''}
         {'are' if len(recoveries) != 1 else 'is'} back within budget. Alerts cleared.</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead><tr style="background:#F4F1E8;text-align:left">
          <th style="padding:8px 12px">Source</th><th style="padding:8px 12px">Status</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="color:#777;font-size:12px">Full history:
        <a href="https://dev.sailratings.com/justin/scrapers">/justin/scrapers</a></p>
    </div>
    """
    return subject, html


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def run_watchdog(
    engine: Engine,
    *,
    now: datetime | None = None,
    cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
    dry_run: bool = False,
    send_email: Callable[[str, str], None] | None = _default_email_sender,
    sources: Iterable[SourceConfig] | None = None,
) -> WatchdogResult:
    """Run one watchdog pass: detect breaches, alert with cooldown, recover.

    ``send_email`` is injectable for tests; ``None`` disables sending while
    still exercising cooldown + alert-log writes.
    """
    now = now or datetime.now(timezone.utc)
    result = WatchdogResult()
    cooldown = timedelta(hours=cooldown_hours)

    with engine.begin() as conn:
        ensure_watchdog_table(conn)
        breaches = compute_breaches(conn, now, sources)
        result.breaches = breaches

        breached_keys = {b.alert_key for b in breaches}
        active = {a["alert_key"]: a for a in get_active_alerts(conn)}

        # --- Cooldown + alert logging -----------------------------------
        to_alert: list[Breach] = []
        for b in breaches:
            open_alert = active.get(b.alert_key)
            if open_alert is not None:
                cooldown_until = _aware(open_alert["cooldown_until"]) \
                    if open_alert.get("cooldown_until") else None
                if cooldown_until and now < cooldown_until:
                    # Cooldown honoured — one alert per breach per 4 h.
                    result.in_cooldown.append(b)
                    continue
                # Cooldown expired and still stale — re-alert on the same row.
                conn.execute(text("""
                    UPDATE watchdog_alerts
                    SET alerted_at = :now, cooldown_until = :until,
                        age_hours = :age, reason = :reason
                    WHERE id = :id
                """), {
                    "now": _naive(now), "until": _naive(now + cooldown),
                    "age": b.age_hours, "reason": b.reason,
                    "id": open_alert["id"],
                })
                to_alert.append(b)
            else:
                # First alert for this breach — log it.
                conn.execute(text("""
                    INSERT INTO watchdog_alerts
                        (alert_key, source, signal, label, cadence, reason,
                         age_hours, budget_hours, status, first_seen_at,
                         alerted_at, cooldown_until, details)
                    VALUES
                        (:key, :source, :signal, :label, :cadence, :reason,
                         :age, :budget, 'active', :now, :now, :until, :details)
                """), {
                    "key": b.alert_key, "source": b.source, "signal": b.signal,
                    "label": b.label, "cadence": b.cadence, "reason": b.reason,
                    "age": b.age_hours, "budget": b.budget_hours,
                    "now": _naive(now), "until": _naive(now + cooldown),
                    "details": json.dumps({"cadence": b.cadence}),
                })
                to_alert.append(b)

        # --- Recovery: previously-active alerts whose source is back ------
        recoveries: list[dict[str, Any]] = []
        for key, open_alert in active.items():
            if key not in breached_keys:
                conn.execute(text("""
                    UPDATE watchdog_alerts
                    SET status = 'recovered', recovered_at = :now
                    WHERE id = :id
                """), {"now": _naive(now), "id": open_alert["id"]})
                recoveries.append(open_alert)
        result.recoveries = recoveries

        result.alerts_sent = to_alert

    # --- Email (outside the txn — sending is not transactional) ----------
    if send_email is None or dry_run:
        result.skipped_send = True
        return result

    try:
        if to_alert:
            subject, html = build_alert_email(to_alert, cooldown_hours)
            send_email(subject, html)
            result.email_sent = True
        if recoveries:
            subject, html = build_recovery_email(recoveries)
            send_email(subject, html)
            result.recovery_email_sent = True
    except Exception:
        # A failed send must not lose the alert-log writes already committed;
        # the next pass (within cooldown) simply won't re-send. Surface the
        # error to the caller via skipped_send so cron logs show it.
        result.skipped_send = True
        raise

    return result


def _naive(dt: datetime) -> datetime:
    """Store timestamps as naive UTC for SQLite/Postgres compatibility."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
