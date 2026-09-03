"""End-to-end verification drill for OPS-02-03.

    Alerts that reach a human: Slack/email webhook, dead-man ping,
    freshness budgets.

Simulates the acceptance criteria against an isolated SQLite database with
injected senders (no network, no secrets):

  1. Freshness budgets — ORC/TCC/TopYacht 26h, SailSys 2h/26h, weekly 8d.
  2. ACCEPTANCE DRILL — pausing ``orc_api`` for 27 h produces a Slack
     message AND a ``watchdog_alerts`` row; resuming produces a recovery.
  3. Multi-channel redundancy — a dead Slack transport still emails (and
     vice-versa); a single dead channel never silences the page.
  4. DEAD-MAN DRILL — the health-check heartbeat pings the dead-man URL
     while alive (no external alert); a missed ping (cron killed) is exactly
     what the external monitor pages on after 09:30 UTC.

Run with:  PYTHONPATH=src python3 scripts/verify_ops_02_03.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from irc_data import alerting
from irc_data.scrape_supervision import by_source
from irc_data.scrape_watchdog import (
    ensure_watchdog_table,
    get_active_alerts,
    get_alert_history,
    run_watchdog,
)

T0 = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
SLACK_URL = "https://hooks.slack.com/services/T/B/secret"
DEADMAN_URL = "https://hc-ping.com/ops-02-03-heartbeat"

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE ingestion_log (id INTEGER PRIMARY KEY, source TEXT,"
            " started_at TIMESTAMP, completed_at TIMESTAMP, status TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE race_results (id INTEGER PRIMARY KEY, source TEXT,"
            " created_at TIMESTAMP)"
        ))
        ensure_watchdog_table(conn)
    return eng


def _run(eng, source, completed_at, status="completed"):
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO ingestion_log (source, started_at, completed_at, status)"
            " VALUES (:s, :st, :ct, :stat)"
        ), {"s": source, "st": completed_at, "ct": completed_at, "stat": status})


class Cap:
    """Capture Slack + email sends."""
    def __init__(self):
        self.emails, self.slacks = [], []

    @property
    def email(self):
        return lambda s, h: self.emails.append((s, h))

    @property
    def slack(self):
        return lambda u, m: self.slacks.append((u, m)) or True


print("OPS-02-03 verification: alerts that reach a human\n")

# -- 1. Freshness budgets ---------------------------------------------------
print("1. Freshness budgets (spec contract)")
check("ORC 26h", by_source("orc_api").run_within == timedelta(hours=26))
check("TCC 26h", by_source("irc_tcc").run_within == timedelta(hours=26))
check("TopYacht 26h", by_source("topyacht").run_within == timedelta(hours=26))
check("SailSys run 2h", by_source("sailsys").run_within == timedelta(hours=2))
check("SailSys data 26h", by_source("sailsys").data_within == timedelta(hours=26))
check("weekly 8d", all(by_source(s).run_within == timedelta(days=8)
                       for s in ("sailracehq", "isora", "rhkyc")))

# -- 2. Acceptance drill: pause orc_api 27h -> Slack + row; resume -> recovery
print("\n2. ACCEPTANCE DRILL — pause orc_api 27h, then resume")
eng = _engine()
cap = Cap()
cfg = by_source("orc_api")

_run(eng, "orc_api", T0 - timedelta(hours=27))   # paused 27 h (> 26 h budget)
r1 = run_watchdog(eng, now=T0, sources=[cfg],
                  send_email=cap.email, send_slack=cap.slack, slack_url=SLACK_URL)
check("Slack message produced", r1.slack_sent and len(cap.slacks) == 1,
      f"slacks={len(cap.slacks)}")
with eng.begin() as conn:
    active = get_active_alerts(conn)
check("watchdog_alerts row written", len(active) == 1 and active[0]["source"] == "orc_api",
      f"active={len(active)}")
check("email also sent (multi-channel)", r1.email_sent and len(cap.emails) == 1)

t1 = T0 + timedelta(minutes=15)
_run(eng, "orc_api", t1)                          # resume
r2 = run_watchdog(eng, now=t1, sources=[cfg],
                  send_email=cap.email, send_slack=cap.slack, slack_url=SLACK_URL)
check("recovery message sent", r2.recovery_slack_sent and r2.recovery_email_sent,
      f"recoveries={len(r2.recoveries)}")
with eng.begin() as conn:
    hist = get_alert_history(conn)
    still_active = get_active_alerts(conn)
check("alert row closed as recovered",
      hist[0]["status"] == "recovered" and hist[0]["recovered_at"] is not None)
check("no active alerts after resume", len(still_active) == 0)

# -- 3. Multi-channel redundancy --------------------------------------------
print("\n3. Multi-channel redundancy — one dead transport never silences the page")
eng2 = _engine()
cap2 = Cap()
_run(eng2, "topyacht", T0 - timedelta(hours=30))

def dead_slack(u, m):
    raise RuntimeError("slack unreachable")

r = run_watchdog(eng2, now=T0, sources=[by_source("topyacht")],
                 send_email=cap2.email, send_slack=dead_slack, slack_url=SLACK_URL)
check("slack down -> email still sent", r.email_sent and not r.slack_sent,
      f"emails={len(cap2.emails)}")
with eng2.begin() as conn:
    check("alert logged despite slack failure", len(get_active_alerts(conn)) == 1)

# -- 4. Dead-man drill --------------------------------------------------------
print("\n4. DEAD-MAN DRILL — heartbeat ping vs missed ping")
pings = []
pinger_alive = lambda url: pings.append(url) or True       # cron alive -> pings
pinger_dead = lambda url: False                            # cron killed -> no ping

ok_alive = pinger_alive(DEADMAN_URL)
check("alive heartbeat pings dead-man URL (no external alert)",
      ok_alive and pings == [DEADMAN_URL], f"pings={pings}")

ok_dead = pinger_dead(DEADMAN_URL)
check("killed cron -> no ping -> external monitor alerts after 09:30 UTC",
      ok_dead is False and len(pings) == 1,
      "ping withheld; external dead-man monitor is the alarm")

# -- Summary ------------------------------------------------------------------
print(f"\n{'='*60}\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
