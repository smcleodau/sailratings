"""OPS-02-10 — unit tests for ``backfill_orc_details`` (the ORC VPP detail
drain).

The live drain runs against the dev DB + data.orc.org; these tests mock the
HTTP fetch and the DB so the concurrency / field-mapping behaviour is
verified offline:

* every missing cert on the latest snapshot is attempted;
* GPH/CDL/allowances extracted from the RMS payload land on the row;
* certs whose RMS genuinely lacks GPH still get CDL/allowances written;
* failures are counted and logged, not fatal.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.scrapers import orc as orc_module


@pytest.fixture()
def sqlite_engine(monkeypatch):
    """In-memory SQLite standing in for orc_certificates + ingest_events."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE orc_certificates (
                id INTEGER PRIMARY KEY,
                snapshot_date TEXT,
                ref_no TEXT,
                gph NUMERIC,
                cdl NUMERIC,
                osn NUMERIC,
                triple_low NUMERIC,
                triple_med NUMERIC,
                triple_high NUMERIC,
                loa NUMERIC,
                displacement NUMERIC,
                draft NUMERIC,
                sail_area_upwind NUMERIC,
                sail_area_downwind NUMERIC,
                stability_index NUMERIC,
                builder TEXT,
                designer TEXT,
                year_built INTEGER,
                allowances TEXT,
                dynamic_allowance NUMERIC,
                dspl_sailing NUMERIC,
                imsl NUMERIC,
                mb NUMERIC,
                aphd NUMERIC,
                apht TEXT,
                wss NUMERIC,
                tmf_offshore NUMERIC,
                tmf_inshore NUMERIC,
                raw_data TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE ingest_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source TEXT, event_type TEXT, status TEXT,
                reference TEXT, reason TEXT, meta TEXT
            )
        """))
        # 3 certs missing CDL/allowances (the detail markers) on the latest
        # snapshot, 1 old-snapshot cert (must NOT be touched), 1 already
        # drained (has CDL + allowances + GPH).  Note the backlog is keyed on
        # cdl/allowances, not gph: APH certs legitimately have gph NULL but
        # drained detail, and must not be re-fetched forever.
        conn.execute(text("""
            INSERT INTO orc_certificates (id, snapshot_date, ref_no, gph, cdl, allowances)
            VALUES
              (1, '2026-09-02', 'REF_A', NULL, NULL, NULL),
              (2, '2026-09-02', 'REF_B', NULL, NULL, NULL),
              (3, '2026-09-02', 'REF_C', NULL, NULL, NULL),
              (4, '2026-07-26', 'REF_OLD', NULL, NULL, NULL),
              (5, '2026-09-02', 'REF_DONE', 600.0, 10.0, '{}')
        """))

    monkeypatch.setattr(
        "irc_data.db.connection.get_engine", lambda: eng,
    )
    # log_event writes to ingest_events via the same engine — keep it real
    # but point it at SQLite.  The JSONB CAST in the UPDATE is
    # Postgres-specific, so stub the allowances column write by patching
    # _JSON_FIELDS handling: simplest is to make the engine accept the SQL.
    return eng


class _FakeRMS:
    """Deterministic RMS payloads keyed by ref_no."""

    FULL = {
        "GPH": "547.4",
        "CDL": "12.688",
        "OSN": "600.0",
        "TND_Offshore_Low": "500.0",
        "Allowances": {"6": {"4": 700.0}},
        "Builder": "Beneteau",
        "Age_Year": "2005",
        "Area_Main": "40.0",
        "Area_Jib": "30.0",
    }
    NO_GPH = {  # cert type with no GPH but with CDL + allowances
        "CDL": "19.851",
        "Allowances": {"6": {"4": 800.0}},
    }

    def __init__(self):
        self.calls: list[str] = []

    async def __call__(self, client, ref_no):
        self.calls.append(ref_no)
        if ref_no == "REF_A":
            return dict(self.FULL)
        if ref_no == "REF_B":
            return dict(self.NO_GPH)
        if ref_no == "REF_C":
            return None  # fetch failure
        return None


def _patch_sqlite_update(monkeypatch):
    """The production UPDATE uses ``CAST(:k AS jsonb)`` (Postgres).  SQLite
    chokes on that syntax, so for the unit test rewrite the statement to a
    plain bind.  Behaviour under test (which fields get written) is
    unchanged."""
    import re
    from decimal import Decimal

    from sqlalchemy.engine import Connection

    orig = Connection.execute

    def _coerce(params):
        if isinstance(params, dict):
            return {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in params.items()
            }
        return params

    def patched(self, statement, parameters=None, **kw):
        sql = str(statement)
        sql = re.sub(r"CAST\(:([a-z_]+) AS jsonb\)", r":\1", sql)
        if sql != str(statement):
            statement = text(sql)
        return orig(self, statement, _coerce(parameters) or {}, **kw)

    monkeypatch.setattr(Connection, "execute", patched)


def test_backfill_drains_latest_snapshot_only(sqlite_engine, monkeypatch):
    _patch_sqlite_update(monkeypatch)
    fake = _FakeRMS()
    monkeypatch.setattr(orc_module, "fetch_certificate_rms", fake)

    stats = asyncio.run(orc_module.backfill_orc_details(limit=None, concurrency=3))

    assert stats["total_missing"] == 3, stats  # not the old snapshot, not REF_DONE
    assert stats["fetched"] == 2 and stats["errors"] == 1
    assert set(fake.calls) == {"REF_A", "REF_B", "REF_C"}

    with sqlite_engine.connect() as conn:
        a = conn.execute(text(
            "SELECT gph, cdl, allowances, raw_data FROM orc_certificates WHERE id=1"
        )).fetchone()
        b = conn.execute(text(
            "SELECT gph, cdl, allowances FROM orc_certificates WHERE id=2"
        )).fetchone()
        old = conn.execute(text(
            "SELECT gph FROM orc_certificates WHERE id=4"
        )).fetchone()
        done = conn.execute(text(
            "SELECT gph FROM orc_certificates WHERE id=5"
        )).fetchone()

    # REF_A: full RMS — GPH, CDL, allowances, raw payload all landed.
    assert float(a[0]) == 547.4
    assert float(a[1]) == 12.688
    assert a[2] is not None  # allowances JSON
    assert '"GPH"' in (a[3] or "")

    # REF_B: no GPH upstream — CDL + allowances still written, GPH stays NULL.
    assert b[0] is None
    assert float(b[1]) == 19.851
    assert b[2] is not None

    # Old snapshot and already-drained rows untouched.
    assert old[0] is None
    assert float(done[0]) == 600.0
