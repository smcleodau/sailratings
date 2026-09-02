"""Contract tests for GET /v1/stats (OPS-02-11).

Verification approach: a *census fixture* — a hand-counted set of rows
loaded into an in-memory SQLite database — is served through the real
FastAPI app with the DB dependency overridden. The endpoint payload must
match the pg-style census counts exactly (acceptance: within 1%; the
fixture asserts exact equality, which is stronger).

Also asserted:

  * the census is produced by a **single SQL statement** (one round-trip),
  * responses are cached with a 10-minute TTL (second call is a cache hit,
    an expired TTL recomputes),
  * the payload carries per-domain last-updated timestamps derived from
    the tables' own audit columns.

The stats router uses dialect-portable SQL, so behaviour is identical on
SQLite (here) and Postgres (production).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from irc_data.api.routers import stats as stats_module


# ---------------------------------------------------------------------------
# Census fixture — the "pg census" the endpoint is contracted to match
# ---------------------------------------------------------------------------

#: Hand-counted census. If a fixture insert changes, change these numbers.
CENSUS = {
    "boats": 7,
    "tcc_snapshots": 5,
    "irc_certificates": 4,
    "orc_certificates": 3,
    "race_results": 11,
    "events": 3,
    "countries": 3,  # GBR, FRA, IRL (NULL and '' excluded)
    "designs": 4,  # J/111, J/109, First 36.7, Sun Fast 3200
    "sources": 2,
}

#: Acceptance tolerance from the issue (counts within 1% of the census).
ACCEPTANCE_TOLERANCE = 0.01

DDL = """
CREATE TABLE boats (
    id INTEGER PRIMARY KEY,
    boat_name TEXT,
    country TEXT,
    design TEXT,
    updated_at TEXT
);
CREATE TABLE tcc_snapshots (
    id INTEGER PRIMARY KEY,
    boat_id INTEGER,
    snapshot_date TEXT
);
CREATE TABLE irc_certificates (
    id INTEGER PRIMARY KEY,
    cert_number TEXT,
    scraped_at TEXT
);
CREATE TABLE orc_certificates (
    id INTEGER PRIMARY KEY,
    ref_no TEXT,
    created_at TEXT
);
CREATE TABLE race_results (
    id INTEGER PRIMARY KEY,
    created_at TEXT
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    name TEXT,
    updated_at TEXT
);
CREATE TABLE data_sources (
    id INTEGER PRIMARY KEY,
    slug TEXT,
    enabled INTEGER,
    updated_at TEXT
);
"""

BOATS = [
    # (boat_name, country, design, updated_at) — NULL/'' country & design on purpose
    ("Tonnerre", "GBR", "J/111", "2026-01-05T10:00:00+00:00"),
    ("Jagerbomb", "GBR", "J/109", "2026-01-06T10:00:00+00:00"),
    ("Belladonna", "FRA", "First 36.7", "2026-01-04T10:00:00+00:00"),
    ("Zephyr", "IRL", "J/111", "2026-01-03T10:00:00+00:00"),
    ("Mistral", None, "Sun Fast 3200", "2026-01-02T10:00:00+00:00"),
    ("Anonymous", "", None, "2026-01-01T10:00:00+00:00"),
    ("Old Timer", "GBR", None, "2025-12-31T10:00:00+00:00"),
]

TCC_SNAPSHOTS = [(1, "2025-06-01"), (1, "2026-01-01"), (2, "2026-01-01"),
                 (3, "2026-01-01"), (4, "2025-06-01")]

IRC_CERTIFICATES = [
    ("GBR-1001", "2026-01-06T09:00:00+00:00"),
    ("GBR-1002", "2026-01-07T09:00:00+00:00"),  # latest
    ("FRA-2001", "2026-01-05T09:00:00+00:00"),
    ("IRL-3001", None),  # never scraped — excluded from MAX
]

ORC_CERTIFICATES = [
    ("ORC-GBR-001", "2026-01-02T08:00:00+00:00"),
    ("ORC-FRA-001", "2026-01-03T08:00:00+00:00"),  # latest
    ("ORC-IRL-001", "2026-01-01T08:00:00+00:00"),
]

EVENTS = [
    ("RORC Cervantes Trophy", "2025-05-02T00:00:00+00:00"),
    ("Rolex Middle Sea Race", "2025-10-18T00:00:00+00:00"),  # latest
    ("Dun Laoghaire Regatta", "2025-07-10T00:00:00+00:00"),
]

DATA_SOURCES = [
    ("irc-tcc", 1, "2026-01-07T03:00:00+00:00"),  # latest
    ("orc-public", 1, "2026-01-06T03:00:00+00:00"),
]

EXPECTED_LAST_UPDATED = {
    "boats": "2026-01-06T10:00:00+00:00",
    "irc_certificates": "2026-01-07T09:00:00+00:00",
    "orc_certificates": "2026-01-03T08:00:00+00:00",
    "race_results": "2026-01-08T12:00:00+00:00",
    "events": "2025-10-18T00:00:00+00:00",
    "sources": "2026-01-07T03:00:00+00:00",
}


def _seed_census(engine) -> None:
    """Load the census fixture into the given engine."""
    with engine.begin() as conn:
        for statement in DDL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.execute(
            text("INSERT INTO boats (boat_name, country, design, updated_at)"
                 " VALUES (:n, :c, :d, :u)"),
            [{"n": n, "c": c, "d": d, "u": u} for n, c, d, u in BOATS],
        )
        conn.execute(
            text("INSERT INTO tcc_snapshots (boat_id, snapshot_date) VALUES (:b, :s)"),
            [{"b": b, "s": s} for b, s in TCC_SNAPSHOTS],
        )
        conn.execute(
            text("INSERT INTO irc_certificates (cert_number, scraped_at) VALUES (:c, :s)"),
            [{"c": c, "s": s} for c, s in IRC_CERTIFICATES],
        )
        conn.execute(
            text("INSERT INTO orc_certificates (ref_no, created_at) VALUES (:r, :c)"),
            [{"r": r, "c": c} for r, c in ORC_CERTIFICATES],
        )
        conn.execute(
            text("INSERT INTO race_results (created_at) VALUES (:c)"),
            # 11 finishes, latest exactly 2026-01-08T12:00:00Z
            [{"c": ts} for ts in [
                "2026-01-01T08:00:00+00:00",
                "2026-01-02T08:00:00+00:00",
                "2026-01-03T08:00:00+00:00",
                "2026-01-04T08:00:00+00:00",
                "2026-01-05T08:00:00+00:00",
                "2026-01-06T08:00:00+00:00",
                "2026-01-07T08:00:00+00:00",
                "2026-01-07T09:00:00+00:00",
                "2026-01-08T10:00:00+00:00",
                "2026-01-08T11:00:00+00:00",
                "2026-01-08T12:00:00+00:00",
            ]],
        )
        conn.execute(
            text("INSERT INTO events (name, updated_at) VALUES (:n, :u)"),
            [{"n": n, "u": u} for n, u in EVENTS],
        )
        conn.execute(
            text("INSERT INTO data_sources (slug, enabled, updated_at) VALUES (:s, :e, :u)"),
            [{"s": s, "e": e, "u": u} for s, e, u in DATA_SOURCES],
        )


@pytest.fixture()
def engine():
    """StaticPool-backed in-memory SQLite so TestClient requests share one db."""
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _seed_census(eng)
    return eng


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db

    stats_module.reset_stats_cache()
    app_module.app.dependency_overrides[get_db] = lambda: engine
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)
        stats_module.reset_stats_cache()


# ---------------------------------------------------------------------------
# Contract: counts match the census within 1% (fixture asserts exact match)
# ---------------------------------------------------------------------------


class TestCensusContract:
    def test_counts_match_census_fixture(self, client):
        resp = client.get("/v1/stats/")
        assert resp.status_code == 200
        body = resp.json()

        for key, expected in CENSUS.items():
            actual = body["counts"][key]
            # Flat legacy keys must agree with the structured view.
            assert body[key] == actual, f"{key}: flat key disagrees with counts view"
            # Acceptance criterion: within 1% of the census...
            assert abs(actual - expected) <= max(expected, 1) * ACCEPTANCE_TOLERANCE, (
                f"{key}: endpoint={actual} census={expected} drift > 1%"
            )
            # ...the fixture is exact.
            assert actual == expected, f"{key}: endpoint={actual} census={expected}"

    def test_census_runs_as_single_query(self, engine):
        statements: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def count_statements(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        payload = stats_module.compute_census(engine)

        assert len(statements) == 1, (
            f"census must be one round-trip, got {len(statements)} statements"
        )
        assert payload["counts"] == CENSUS

    def test_response_shape_and_last_updated(self, client):
        body = client.get("/v1/stats/").json()

        assert set(body["counts"]) == set(stats_module.COUNT_KEYS)
        assert set(body["last_updated"]) == {
            "boats", "irc_certificates", "orc_certificates",
            "race_results", "events", "sources",
        }
        for domain, expected in EXPECTED_LAST_UPDATED.items():
            assert body["last_updated"][domain] == expected, (
                f"{domain}: last_updated={body['last_updated'][domain]!r} expected={expected!r}"
            )
        assert body["cache_ttl_seconds"] == 600
        assert body["generated_at"]

    def test_empty_database_returns_zeros_not_500(self, client, engine):
        with engine.begin() as conn:
            for table in ("race_results", "orc_certificates", "irc_certificates",
                          "tcc_snapshots", "boats", "events", "data_sources"):
                conn.execute(text(f"DELETE FROM {table}"))
        stats_module.reset_stats_cache()

        resp = client.get("/v1/stats/")
        assert resp.status_code == 200
        body = resp.json()
        assert all(v == 0 for v in body["counts"].values())
        assert all(v is None for v in body["last_updated"].values())


# ---------------------------------------------------------------------------
# Cache behaviour: 10-minute TTL, single recomputation per window
# ---------------------------------------------------------------------------


class TestStatsCache:
    def test_second_request_is_cache_hit(self, client, engine):
        first = client.get("/v1/stats/").json()
        # Mutate the DB underneath: a cached endpoint must NOT see it.
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM boats"))

        second = client.get("/v1/stats/").json()
        assert second == first
        assert second["counts"]["boats"] == CENSUS["boats"]

    def test_expired_ttl_recomputes(self, engine):
        stats_module.reset_stats_cache()
        fresh = stats_module.get_stats_cached(engine, ttl=0.0)
        assert fresh["counts"] == CENSUS

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM boats"))

        recomputed = stats_module.get_stats_cached(engine, ttl=0.0)
        assert recomputed["counts"]["boats"] == 0
        assert recomputed["generated_at"] >= fresh["generated_at"]
