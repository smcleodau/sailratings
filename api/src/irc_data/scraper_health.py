"""Health monitoring for the four active scrapers (DP-00-02).

Goal: protect the data already flowing while everything else is built.

A single *cycle* of :func:`run_health_check` (wired to
``irc-data scraper-health``, run daily from cron) does, for each of the
four active sources:

1. **Probe** — one lightweight HTTP GET against the source's well-known
   entry URL (``fetch_success``). This is a read-only health probe; it
   imports nothing and does not touch the scrapers' code paths.
2. **Record counts** — current totals in the tables that source feeds,
   plus the records attributed to that source in the ingestion log.
3. **Last-success timestamp** — the most recent ``completed_at`` row in
   ``ingestion_log`` for the source.
4. **Run log** — one ``ingestion_log`` row per source per cycle with the
   outcome (``status`` = ``completed``/``failed``, ``error_message`` and
   a ``health_check`` metadata blob), so the daily checks are themselves
   observable in the same run log the scrapers write to.
5. **Alert within one cycle** — when a probe fails (e.g. a bad URL), an
   alert is emitted in the *same* cycle via the configured webhook
   (Discord/Slack) and/or email (Resend), and the cycle's exit status is
   non-zero. Alerting is best-effort: a failed notification never swallows
   the run-log rows already committed.

Scope guard: only the four sources in :data:`SOURCES` are checked — no
scope expansion of what these scrapers touch, and no functional changes
to the scrapers themselves.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

# ---------------------------------------------------------------------------
# The four active scrapers under health monitoring (DP-00-02 scope).
#
# ``source`` matches the ``ingestion_log.source`` value the corresponding
# scraper writes (see irc_data.cli / irc_data.scrapers.orc).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthCheckTarget:
    """One active scraper under health monitoring."""

    source: str            # ingestion_log.source value
    label: str             # human label for the report
    cadence_human: str     # how often the scraper is expected to run
    probe_url: str         # lightweight GET used to test fetch success
    # Tables whose row counts are attributed to this source in the report.
    # (table name, optional WHERE-clause source filter)
    count_tables: tuple[tuple[str, str | None], ...] = ()


SOURCES: list[HealthCheckTarget] = [
    HealthCheckTarget(
        source="topyacht",
        label="TopYacht",
        cadence_human="daily",
        probe_url="https://topyacht.net.au/results",
        count_tables=(("race_results", "topyacht"),),
    ),
    HealthCheckTarget(
        source="sailsys",
        label="SailSys",
        cadence_human="every 30 min",
        probe_url="https://app.sailsys.com.au",
        count_tables=(("race_results", "sailsys"),),
    ),
    HealthCheckTarget(
        source="irc_tcc",
        label="IRC TCC Listings",
        cadence_human="daily 06:00 UTC",
        probe_url="https://ircrating.org/irc-racing/online-tcc-listings/",
        count_tables=(("tcc_snapshots", None),),
    ),
    HealthCheckTarget(
        source="orc_api",
        label="ORC",
        cadence_human="daily 03:00 UTC",
        probe_url="https://data.orc.org/public/WPub.dll?action=activecerts&CountryId=AUS",
        count_tables=(("orc_certificates", None),),
    ),
]

# Environment variables for alert transports (all optional; when none are
# set the check still runs, logs, and exits non-zero on failure).
WEBHOOK_ENV = "SCRAPER_HEALTH_WEBHOOK_URL"  # falls back to WEBHOOK_URL
ALERT_EMAIL_ENV = "SCRAPER_HEALTH_ALERT_EMAIL"
RESEND_API_KEY_ENV = "RESEND_API_KEY"


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------


@dataclass
class SourceCheckResult:
    """Health outcome for one source in one cycle."""

    source: str
    label: str
    cadence_human: str
    probe_url: str
    fetch_success: bool
    http_status: int | None = None
    response_ms: float | None = None
    error: str | None = None
    record_counts: dict[str, int] = field(default_factory=dict)
    last_success_at: str | None = None  # ISO8601, from ingestion_log
    last_run_status: str | None = None
    log_id: int | None = None           # run-log row id written this cycle

    @property
    def healthy(self) -> bool:
        return self.fetch_success

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "cadence": self.cadence_human,
            "probe_url": self.probe_url,
            "fetch_success": self.fetch_success,
            "http_status": self.http_status,
            "response_ms": self.response_ms,
            "error": self.error,
            "record_counts": dict(self.record_counts),
            "last_success_at": self.last_success_at,
            "last_run_status": self.last_run_status,
            "log_id": self.log_id,
        }


@dataclass
class HealthCheckReport:
    """Outcome of one full health-check cycle across all four sources."""

    checked_at: str
    cycle_seconds: float = 0.0
    results: list[SourceCheckResult] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    alert_sent: bool = False
    alert_channels: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.healthy for r in self.results)

    @property
    def failures(self) -> list[SourceCheckResult]:
        return [r for r in self.results if not r.healthy]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "ok": self.ok,
            "cycle_seconds": self.cycle_seconds,
            "alert_sent": self.alert_sent,
            "alert_channels": list(self.alert_channels),
            "alerts": list(self.alerts),
            "sources": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Probe (async over httpx; the default transport is injectable for tests)
# ---------------------------------------------------------------------------


async def _default_probe(url: str, timeout: float) -> tuple[bool, int | None, float, str | None]:
    """GET *url* and report (fetch_success, http_status, elapsed_ms, error).

    Any non-2xx status, transport error, or timeout counts as a fetch
    failure. A 4xx/5xx is a failure even though the host answered, because
    the scraper's own fetch would fail the same way (``raise_for_status``).
    """
    # Import here so the module stays importable without the policy stack in
    # minimal environments; in production this is always available.
    from irc_data.sources.policy import ACTIVE_POLICY

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            follow_redirects=True,
            headers={"User-Agent": ACTIVE_POLICY.attribution.user_agent},
        ) as client:
            resp = await client.get(url)
        elapsed_ms = (time.monotonic() - start) * 1000
        if 200 <= resp.status_code < 300:
            return True, resp.status_code, elapsed_ms, None
        return False, resp.status_code, elapsed_ms, f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001 — any probe exception is a failure signal
        elapsed_ms = (time.monotonic() - start) * 1000
        return False, None, elapsed_ms, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# DB reads + run-log writes
# ---------------------------------------------------------------------------


def _read_last_success(conn: Connection, source: str) -> tuple[str | None, str | None]:
    """Return (last successful completed_at ISO, last run status) for source."""
    row = conn.execute(
        text("""
            SELECT MAX(completed_at) AS last_success
            FROM ingestion_log
            WHERE source = :s AND status = 'completed'
        """),
        {"s": source},
    ).fetchone()
    last_success = _iso(row.last_success) if row and row.last_success else None

    row2 = conn.execute(
        text("""
            SELECT status FROM ingestion_log
            WHERE source = :s
            ORDER BY started_at DESC, id DESC
            LIMIT 1
        """),
        {"s": source},
    ).fetchone()
    last_status = row2.status if row2 else None
    return last_success, last_status


def _read_counts(
    conn: Connection,
    target: HealthCheckTarget,
    *,
    existing_tables: set[str] | None = None,
) -> dict[str, int]:
    """Current row counts for the tables this source feeds.

    ``existing_tables`` (optional) lets callers/tests skip tables that are
    absent from a partial schema; when omitted, a missing table simply
    records a count of -1 rather than failing the cycle.
    """
    counts: dict[str, int] = {}
    for table, source_filter in target.count_tables:
        if existing_tables is not None and table not in existing_tables:
            continue
        try:
            if source_filter:
                n = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE source = :s"),
                    {"s": source_filter},
                ).scalar()
            else:
                n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            counts[table] = int(n or 0)
        except Exception:  # noqa: BLE001 — table may not exist in a minimal schema
            counts[table] = -1
    # Also attribute ingestion-log rows to the source.
    try:
        counts["ingestion_log_runs"] = int(conn.execute(
            text("SELECT COUNT(*) FROM ingestion_log WHERE source = :s"),
            {"s": target.source},
        ).scalar() or 0)
    except Exception:  # noqa: BLE001
        counts["ingestion_log_runs"] = -1
    return counts


def _write_run_log(
    conn: Connection,
    result: SourceCheckResult,
    now: datetime,
    *,
    dialect: str,
) -> int | None:
    """Insert one ingestion_log run row for this source's health check.

    Returns the new row id. Runs inside the caller's transaction.
    """
    status = "completed" if result.healthy else "failed"
    metadata = {
        "health_check": True,
        "probe_url": result.probe_url,
        "http_status": result.http_status,
        "response_ms": result.response_ms,
        "record_counts": result.record_counts,
        "last_success_at": result.last_success_at,
    }
    import json as _json

    params = {
        "source": result.source,
        "now": now,
        "status": status,
        "error": result.error,
        "meta": _json.dumps(metadata) if dialect == "sqlite" else metadata,
    }
    if dialect == "sqlite":
        conn.execute(
            text("""
                INSERT INTO ingestion_log
                    (source, started_at, completed_at, status, error_message, metadata)
                VALUES
                    (:source, :now, :now, :status, :error, :meta)
            """),
            params,
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
    else:
        row_id = conn.execute(
            text("""
                INSERT INTO ingestion_log
                    (source, started_at, completed_at, status, error_message, metadata)
                VALUES
                    (:source, :now, :now, :status, :error, :meta)
                RETURNING id
            """),
            params,
        ).scalar()
    return int(row_id) if row_id is not None else None


# ---------------------------------------------------------------------------
# Alerting (webhook + email; all best-effort, never raise)
# ---------------------------------------------------------------------------


def build_alert_message(report: HealthCheckReport) -> str:
    lines = [
        f"SailRatings scraper health: {len(report.failures)} source(s) failing "
        f"(checked {report.checked_at})",
    ]
    for r in report.failures:
        detail = r.error or (f"HTTP {r.http_status}" if r.http_status else "fetch failed")
        lines.append(f"- {r.label} ({r.source}): {detail} [probe {r.probe_url}]")
    return "\n".join(lines)


def _send_webhook(url: str, report: HealthCheckReport) -> bool:
    message = build_alert_message(report)
    if "discord" in url.lower():
        payload: dict[str, Any] = {
            "embeds": [{
                "title": "Scraper health alert",
                "description": message,
                "color": 0xFF0000,
            }]
        }
    else:  # Slack-style
        payload = {"text": message}
    resp = httpx.post(url, json=payload, timeout=10)
    return resp.status_code < 300


def _send_email(api_key: str, to_addr: str, report: HealthCheckReport) -> bool:
    import resend

    resend.api_key = api_key
    message = build_alert_message(report)
    rows = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>"
        f"<strong>{r.label}</strong><br/>"
        f"<span style='color:#777;font-size:12px'>{r.source} · {r.cadence_human}</span></td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee;color:#a00;"
        f"font-family:monospace'>{r.error or r.http_status or 'fetch failed'}</td></tr>"
        for r in report.failures
    )
    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:560px;margin:auto;color:#222">
      <h2 style="color:#0A2240">SailRatings scraper health alert</h2>
      <p>{len(report.failures)} of {len(report.results)} monitored sources failed
         their fetch probe in the latest health-check cycle
         ({report.checked_at}).</p>
      <table style="width:100%;border-collapse:collapse;margin:16px 0">
        <thead><tr style="background:#F4F1E8;text-align:left">
          <th style="padding:8px 12px">Source</th><th style="padding:8px 12px">Failure</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#777;font-size:12px">This alert fired within one cycle of the
      failure. Re-run <code>irc-data scraper-health</code> for a fresh status.</p>
    </div>
    """
    resend.Emails.send({
        "from": "SailRatings Health <health@sailratings.com>",
        "to": [to_addr],
        "subject": f"SailRatings scraper health — {len(report.failures)} source(s) failing",
        "html": html,
        "text": message,
    })
    return True


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------


async def run_health_check_async(
    engine: Engine,
    *,
    targets: list[HealthCheckTarget] | None = None,
    now: datetime | None = None,
    probe: Callable[[str, float], Awaitable[tuple[bool, int | None, float, str | None]]] | None = None,
    probe_timeout: float = 15.0,
    alert: bool = True,
    webhook_url: str | None = None,
    alert_email: str | None = None,
    existing_tables: set[str] | None = None,
) -> HealthCheckReport:
    """Run one health-check cycle over the four active scrapers.

    Parameters are injectable for tests: ``probe`` replaces the HTTP fetch,
    ``now`` fixes the cycle timestamp, and ``webhook_url`` / ``alert_email``
    override the environment-configured alert destinations.
    """
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    targets = list(targets) if targets is not None else list(SOURCES)
    probe_fn = probe or _default_probe

    report = HealthCheckReport(checked_at=now.isoformat())

    for target in targets:
        result = SourceCheckResult(
            source=target.source,
            label=target.label,
            cadence_human=target.cadence_human,
            probe_url=target.probe_url,
            fetch_success=False,
        )

        # 1. Probe (network — outside any DB transaction).
        ok, http_status, elapsed_ms, error = await probe_fn(target.probe_url, probe_timeout)
        result.fetch_success = bool(ok)
        result.http_status = http_status
        result.response_ms = round(elapsed_ms, 1)
        result.error = error

        # 2. Record counts + last-success; 3. run-log row. One transaction
        #    per source so a count query that hits a missing table can't roll
        #    back a run-log row we've already written for another source.
        dialect = engine.dialect.name
        with engine.begin() as conn:
            result.last_success_at, result.last_run_status = _read_last_success(conn, target.source)
            result.record_counts = _read_counts(conn, target, existing_tables=existing_tables)
            result.log_id = _write_run_log(conn, result, now, dialect=dialect)

        if not result.healthy:
            report.alerts.append(
                f"{target.label} ({target.source}) fetch failed: "
                f"{result.error or f'HTTP {result.http_status}'}"
            )

        report.results.append(result)

    report.cycle_seconds = round(time.monotonic() - started, 2)

    # 5. Alert within this cycle on any failure (best-effort).
    if report.failures and alert:
        _dispatch_alerts(report, webhook_url=webhook_url, alert_email=alert_email)

    return report


def _dispatch_alerts(
    report: HealthCheckReport,
    *,
    webhook_url: str | None,
    alert_email: str | None,
) -> None:
    """Fire every configured alert channel. Best-effort: never raises."""
    webhook_url = webhook_url or os.environ.get(WEBHOOK_ENV) or os.environ.get("WEBHOOK_URL")
    alert_email = alert_email or os.environ.get(ALERT_EMAIL_ENV)
    resend_key = os.environ.get(RESEND_API_KEY_ENV)

    if webhook_url:
        try:
            if _send_webhook(webhook_url, report):
                report.alert_sent = True
                report.alert_channels.append("webhook")
        except Exception:  # noqa: BLE001
            pass

    if alert_email and resend_key:
        try:
            if _send_email(resend_key, alert_email, report):
                report.alert_sent = True
                report.alert_channels.append("email")
        except Exception:  # noqa: BLE001
            pass


def run_health_check(engine: Engine, **kwargs: Any) -> HealthCheckReport:
    """Synchronous wrapper around :func:`run_health_check_async`."""
    import asyncio

    return asyncio.run(run_health_check_async(engine, **kwargs))


# ---------------------------------------------------------------------------
# Daily report rendering
# ---------------------------------------------------------------------------


def format_report(report: HealthCheckReport) -> str:
    """Render the daily per-source health report as plain text.

    One block per source: fetch success, record counts, last-success
    timestamp. Suitable for cron logs and CLI output.
    """
    lines = [
        f"Scraper health check — {report.checked_at} "
        f"({'OK' if report.ok else 'FAILING'}, {report.cycle_seconds:.1f}s)",
        "",
    ]
    for r in report.results:
        mark = "OK  " if r.healthy else "FAIL"
        lines.append(f"[{mark}] {r.label} ({r.source}) — expected {r.cadence_human}")
        lines.append(f"       fetch_success : {r.fetch_success}"
                     + (f" (HTTP {r.http_status}, {r.response_ms:.0f} ms)" if r.http_status else "")
                     + (f" — {r.error}" if r.error and r.healthy is False else ""))
        if r.error and r.healthy:
            lines.append(f"       note          : {r.error}")
        lines.append(f"       last_success  : {r.last_success_at or 'never on record'}")
        if r.last_run_status:
            lines.append(f"       last_run      : {r.last_run_status}")
        for table, n in r.record_counts.items():
            lines.append(f"       count {table:<18}: {n}")
        lines.append("")
    if report.alerts:
        lines.append(f"Alerts ({len(report.alerts)}):")
        for a in report.alerts:
            lines.append(f"  ! {a}")
        channels = ",".join(report.alert_channels) if report.alert_channels else "none configured"
        lines.append(f"Alert dispatched: {report.alert_sent} ({channels})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: Any) -> str:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


__all__ = [
    "SOURCES",
    "HealthCheckTarget",
    "SourceCheckResult",
    "HealthCheckReport",
    "run_health_check",
    "run_health_check_async",
    "format_report",
    "build_alert_message",
]
