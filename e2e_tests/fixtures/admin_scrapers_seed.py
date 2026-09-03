"""Shared seed fixture for the AD-01-06 Scrapers health page E2E rig.

Builds a temporary file-backed SQLite database with the exact ledger shape
the ``/admin/scrapers`` Playwright spec asserts against. Runs are written
through the OPS-01-03 ledger write path (``record_run``) so the fixture is
a truthful mirror of production writes:

  sailsys    healthy — completed run 30 min ago (found 12, new 3), one
             failure and one older completed run inside the 7-day window
             → runs/fails/rows = 3 / 1 / 8. race_results tap 30 min old
             → data: fresh.
  topyacht   cron breach — last success 5 days ago vs a 26 h budget
             → run: stale; tap 5 days dry → data: stale. The watchdog
             (OPS-01-04) has an ACTIVE run alert for it, which drives the
             page's "Cron health" banner.
  orc_api    never ran → run: never, data: n/a (writes no race_results).
  cowesweek  optional annual source → state: optional.
  ghost      uncatalogued — ledger rows but no registry entry → surfaced
             as "ghost (uncatalogued)".

Timestamps are relative to wall-clock now so the page's age columns render
deterministically ("30m ago" etc. within tolerance).

The schema mirrors the production tables the scrapers endpoints read
(``ingestion_log``, ``race_results``, ``watchdog_alerts``). It is
intentionally self-contained: the E2E API server
(``admin_scrapers_api.py``) only mounts the scrapers endpoints, so no
other tables are needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.run_ledger import record_run
from irc_data.scrape_watchdog import ensure_watchdog_table

ADMIN_PASSWORD = "sailfast2026"

SCHEMA_SQL = """
CREATE TABLE ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT DEFAULT 'running',
    records_found INTEGER,
    records_new INTEGER,
    records_updated INTEGER,
    error_message TEXT,
    metadata TEXT
);
CREATE TABLE race_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    event_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Fixture constants the spec asserts on.
SAILSYS_RUNS_7D = 3
SAILSYS_FAILED_7D = 1
SAILSYS_NEW_7D = 8
TOPYACHT_RUN_AGE = "5.0d"


def seed_admin_scrapers(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # sailsys — healthy: 2 completed (3 + 5 new) + 1 failed inside 7 days.
    record_run(
        engine, "sailsys", status="completed",
        records_found=12, records_new=3, records_updated=9,
        started_at=now - timedelta(minutes=30),
        completed_at=now - timedelta(minutes=30) + timedelta(seconds=42.5),
    )
    record_run(
        engine, "sailsys", status="failed",
        records_found=0, records_new=0,
        error_message="HTTP 503 from club site",
        started_at=now - timedelta(days=1),
        completed_at=now - timedelta(days=1) + timedelta(seconds=8),
    )
    record_run(
        engine, "sailsys", status="completed",
        records_found=10, records_new=5, records_updated=5,
        started_at=now - timedelta(days=2),
        completed_at=now - timedelta(days=2) + timedelta(seconds=31),
    )

    # topyacht — cron breach: last success 5 days ago (budget 26 h).
    record_run(
        engine, "topyacht", status="completed",
        records_found=7, records_new=7,
        started_at=now - timedelta(days=5),
        completed_at=now - timedelta(days=5) + timedelta(seconds=95),
    )

    # ghost — uncatalogued source (ledger rows, no registry entry).
    record_run(
        engine, "ghost", status="completed",
        records_found=4, records_new=1,
        started_at=now - timedelta(hours=3),
        completed_at=now - timedelta(hours=3) + timedelta(seconds=12),
    )

    # Data-tap rows: sailsys fresh (30 min), topyacht stale (5 days).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO race_results (source, event_date, created_at) "
                "VALUES (:src, :ev, :created)"
            ),
            [
                {
                    "src": "sailsys",
                    "ev": (now - timedelta(days=1)).date().isoformat(),
                    "created": now - timedelta(minutes=30),
                },
                {
                    "src": "topyacht",
                    "ev": (now - timedelta(days=6)).date().isoformat(),
                    "created": now - timedelta(days=5),
                },
            ],
        )

    # Watchdog alert log (OPS-01-04): one ACTIVE run alert for topyacht
    # (drives the "Cron health" banner), one recovered data alert for
    # sailsys (history only).
    with engine.begin() as conn:
        ensure_watchdog_table(conn)
        conn.execute(
            text(
                "INSERT INTO watchdog_alerts "
                "(alert_key, source, signal, label, cadence, reason, "
                " age_hours, budget_hours, status, first_seen_at, "
                " alerted_at, cooldown_until, recovered_at) "
                "VALUES (:key, :src, :signal, :label, :cadence, :reason, "
                "        :age, :budget, :status, :first, :alerted, "
                "        :cooldown, :recovered)"
            ),
            [
                {
                    "key": "topyacht",
                    "src": "topyacht",
                    "signal": "run",
                    "label": "TopYacht (AU/regattas)",
                    "cadence": "daily 02:30 UTC",
                    "reason": "cron stopped (no successful run)",
                    "age": 120.0,
                    "budget": 26.0,
                    "status": "active",
                    "first": now - timedelta(hours=2),
                    "alerted": now - timedelta(hours=2),
                    "cooldown": now + timedelta(hours=2),
                    "recovered": None,
                },
                {
                    "key": "sailsys:data",
                    "src": "sailsys",
                    "signal": "data",
                    "label": "SailSys (AU clubs) (no new data)",
                    "cadence": "every 30 min",
                    "reason": "no new race rows beyond seasonal lull",
                    "age": 30.0,
                    "budget": 26.0,
                    "status": "recovered",
                    "first": now - timedelta(days=3),
                    "alerted": now - timedelta(days=3),
                    "cooldown": None,
                    "recovered": now - timedelta(days=2),
                },
            ],
        )
