"""End-to-end verification for OPS-01-04 (staleness watchdog).

Simulates the acceptance scenario against an isolated SQLite database:

    Induced stale source alerts once, cooldown honoured, recovery clears.

Run with:  PYTHONPATH=src python3 scripts/verify_ops_01_04.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from irc_data.scrape_supervision import SourceConfig
from irc_data.scrape_watchdog import (
    ensure_watchdog_table,
    get_active_alerts,
    get_alert_history,
    run_watchdog,
)

T0 = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
SRC = SourceConfig(
    source="sailsys",
    label="SailSys (AU clubs)",
    cadence_human="every 30 min",
    run_within=timedelta(hours=2),
    data_within=timedelta(hours=24),
)

engine = create_engine("sqlite:///:memory:")
with engine.begin() as conn:
    conn.execute(text(
        "CREATE TABLE ingestion_log (id INTEGER PRIMARY KEY, source TEXT,"
        " started_at TIMESTAMP, completed_at TIMESTAMP, status TEXT)"
    ))
    conn.execute(text(
        "CREATE TABLE race_results (id INTEGER PRIMARY KEY, source TEXT,"
        " created_at TIMESTAMP)"
    ))
    ensure_watchdog_table(conn)

emails: list[str] = []
send = lambda subj, html: emails.append(subj)  # noqa: E731

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


print("OPS-01-04 verification: induced stale source, cooldown, recovery\n")

# -- 1. Induce a stale source: last success 5 h ago, budget 2 h ------------
with engine.begin() as conn:
    conn.execute(text(
        "INSERT INTO ingestion_log (source, started_at, completed_at, status)"
        " VALUES ('sailsys', :t, :t, 'completed')"
    ), {"t": T0 - timedelta(hours=5)})

r1 = run_watchdog(engine, now=T0, sources=[SRC], send_email=send)
check("stale source detected", len(r1.breaches) == 1,
      f"{len(r1.breaches)} breach(es)")
check("ONE alert raised", len(r1.alerts_sent) == 1 and len(emails) == 1,
      f"emails={len(emails)}")
with engine.begin() as conn:
    active = get_active_alerts(conn)
check("alert logged as active", len(active) == 1 and active[0]["status"] == "active",
      f"active={len(active)}")

# -- 2. Cooldown honoured: pass again 15 min later, still stale ------------
r2 = run_watchdog(engine, now=T0 + timedelta(minutes=15), sources=[SRC],
                  send_email=send)
check("cooldown honoured — no repeat alert", r2.alerts_sent == [] and len(emails) == 1,
      f"emails={len(emails)}")
check("breach still tracked in cooldown", len(r2.in_cooldown) == 1)
with engine.begin() as conn:
    hist = get_alert_history(conn)
check("no duplicate alert-log row", len(hist) == 1, f"history rows={len(hist)}")

# -- 3. Recovery clears the alert ------------------------------------------
t1 = T0 + timedelta(minutes=30)
with engine.begin() as conn:
    conn.execute(text(
        "INSERT INTO ingestion_log (source, started_at, completed_at, status)"
        " VALUES ('sailsys', :t, :t, 'completed')"
    ), {"t": t1})
    conn.execute(text(
        "INSERT INTO race_results (source, created_at) VALUES ('sailsys', :t)"
    ), {"t": t1})

r3 = run_watchdog(engine, now=t1, sources=[SRC], send_email=send)
check("recovery detected", len(r3.recoveries) == 1,
      f"recoveries={len(r3.recoveries)}")
check("recovery email sent", len(emails) == 2 and "recovered" in emails[1],
      f"emails={len(emails)}: {emails[-1] if emails else '-'}")
with engine.begin() as conn:
    active = get_active_alerts(conn)
    hist = get_alert_history(conn)
check("alert cleared", active == [], f"active={len(active)}")
check("alert history retained", len(hist) == 1 and hist[0]["status"] == "recovered"
      and hist[0]["recovered_at"] is not None,
      f"history={len(hist)} status={hist[0]['status']}")

print(f"\n{'=' * 60}\nRESULT: {passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
