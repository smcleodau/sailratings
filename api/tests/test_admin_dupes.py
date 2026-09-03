"""Transactional API tests for the duplicate-boats queue (AD-01-14).

Verification approach mirrors test_admin_overview.py: a hand-built fixture
database (in-memory SQLite) read and written through the real FastAPI app
with the DB dependency overridden.

The fixture pins the acceptance scenario:

  * Cluster ``FIFTH AVENUE|AUS`` (tier B, size 2): winner candidate boat
    17213 carries 551 race results, loser boat 18390 carries 146 — merging
    18390 into boats/17213 must leave exactly one boat with 697 race
    results, one ``boat_merges`` row per loser with a ``loser_snapshot``,
    and both queue rows MERGED with ``reviewed_by`` set.  Repeating the
    merge must return 409.
  * Cluster ``FOX BAT|GBR`` (tier D, size 3): the not-dupe path — writes
    one ``boat_not_dupe`` row and sets verdict NOT_DUPE on every queue row.
  * Cluster ``GREY GULL|NZL`` (tier B, size 2): the skip path — verdict
    SKIPPED, excluded from the pending queue, and a later triage pass can
    re-queue the same cluster by inserting fresh PENDING rows.

The merge SQL is dialect-portable so behaviour is identical on SQLite
(here) and Postgres (production/dev).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

# Acceptance numbers from the issue.
CLUSTER = "FIFTH AVENUE|AUS"
WINNER_ID = 17213
LOSER_ID = 18390
WINNER_RR = 551
LOSER_RR = 146
TOTAL_RR = WINNER_RR + LOSER_RR  # 697

NOT_DUPE_CLUSTER = "FOX BAT|GBR"
SKIP_CLUSTER = "GREY GULL|NZL"

ADMIN_HEADERS = {"Authorization": "Bearer test-secret"}


# ---------------------------------------------------------------------------
# Fixture DB — mirrors of the production tables the merge touches
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE boats (
    id INTEGER PRIMARY KEY,
    boat_name TEXT,
    sail_number TEXT,
    design TEXT,
    design_canonical TEXT,
    country TEXT,
    year_built INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE dupe_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    boat_id INTEGER NOT NULL,
    boat_name TEXT,
    country TEXT,
    sail_number TEXT,
    design TEXT,
    year_built INTEGER,
    race_results INTEGER NOT NULL DEFAULT 0,
    cert_count INTEGER NOT NULL DEFAULT 0,
    latest_activity DATE,
    owner TEXT,
    cluster_size INTEGER NOT NULL,
    why TEXT,
    verdict TEXT NOT NULL DEFAULT 'PENDING',
    verdict_note TEXT,
    reviewed_at TIMESTAMP,
    reviewed_by TEXT
);
CREATE TABLE boat_merges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    winner_id INTEGER NOT NULL,
    loser_id INTEGER NOT NULL,
    cluster_key TEXT NOT NULL,
    loser_snapshot TEXT NOT NULL
);
CREATE TABLE boat_not_dupe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cluster_key TEXT NOT NULL,
    boat_ids TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE race_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    event_name TEXT NOT NULL,
    event_date DATE,
    place INTEGER,
    UNIQUE(boat_id, event_name, event_date)
);
CREATE TABLE irc_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    cert_number TEXT,
    issue_date DATE,
    source TEXT
);
CREATE TABLE orc_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    ref_no TEXT,
    class_name TEXT
);
CREATE TABLE tcc_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    snapshot_date DATE,
    cert_year INTEGER,
    tcc NUMERIC(6,4),
    UNIQUE(boat_id, snapshot_date)
);
CREATE TABLE boat_identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    boat_name TEXT,
    sail_number TEXT,
    owner TEXT,
    source TEXT
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    email TEXT,
    status TEXT DEFAULT 'paid'
);
CREATE TABLE event_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    boat_id INTEGER REFERENCES boats(id),
    sail_number TEXT,
    boat_name TEXT
);
CREATE TABLE boat_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    event_type TEXT,
    event_date TIMESTAMP,
    payload TEXT
);
CREATE TABLE boat_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_domain TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL
);
CREATE TABLE boat_news_mentions (
    news_id INTEGER NOT NULL REFERENCES boat_news(id),
    boat_id INTEGER NOT NULL REFERENCES boats(id),
    confidence NUMERIC(3,2),
    PRIMARY KEY (news_id, boat_id)
);
CREATE TABLE insight_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    detail_level TEXT
);
CREATE TABLE boat_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    boat_id INTEGER REFERENCES boats(id),
    field_name TEXT,
    proposed_value TEXT
);
"""


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        _seed(conn)
    return eng


def _seed(conn) -> None:
    """The acceptance fixture: three pending clusters plus one resolved one."""
    boats = [
        # FIFTH AVENUE|AUS — the merge cluster
        (WINNER_ID, "Fifth Avenue", "6128", "Young 88", "AUS", None),
        (LOSER_ID, "Fifth Avenue", "6138", "Young 88", "AUS", None),
        # FOX BAT|GBR — the not-dupe cluster (genuinely different boats)
        (20101, "Fox Bat", "GBR1", "J/111", "GBR", 2015),
        (20102, "Fox Bat", "GBR2", "X-35", "GBR", 2008),
        (20103, "Fox Bat", "GBR3", "J/111", "GBR", 2019),
        # GREY GULL|NZL — the skip cluster
        (20201, "Grey Gull", "NZL 10", "Farr 40", "NZL", 2001),
        (20202, "Grey Gull", "NZL 11", "Farr 40", "NZL", 2001),
        # SEA MIST|AUS — an already-decided (NOT_DUPE) cluster; must never
        # appear in the pending list.
        (20301, "Sea Mist", "AUS 5", "Beneteau 40.7", "AUS", 2009),
        (20302, "Sea Mist", "AUS 6", "Beneteau 40.7", "AUS", 2011),
    ]
    for bid, name, sail, design, country, year in boats:
        conn.execute(
            text(
                "INSERT INTO boats (id, boat_name, sail_number, design, "
                "design_canonical, country, year_built) "
                "VALUES (:id, :name, :sail, :design, :canon, :country, :year)"
            ),
            {
                "id": bid,
                "name": name,
                "sail": sail,
                "design": design,
                "canon": design,
                "country": country,
                "year": year,
            },
        )

    # Race results: winner 551, loser 146 → 697 after the merge.
    for i in range(WINNER_RR):
        conn.execute(
            text(
                "INSERT INTO race_results (boat_id, event_name, event_date) "
                "VALUES (:b, :e, :d)"
            ),
            {"b": WINNER_ID, "e": f"Event {i}", "d": f"2025-01-{(i % 28) + 1:02d}"},
        )
    for i in range(LOSER_RR):
        conn.execute(
            text(
                "INSERT INTO race_results (boat_id, event_name, event_date) "
                "VALUES (:b, :e, :d)"
            ),
            {"b": LOSER_ID, "e": f"Loser Event {i}", "d": f"2024-02-{(i % 28) + 1:02d}"},
        )
    # Collision behaviour is exercised by the certificate / TCC / news-mention
    # rows below; race results move across wholesale so the post-merge count
    # is exactly 551 + 146 = 697.

    # Certificates: winner holds cert AUS-100 (2025), loser holds the same
    # number with an *older* issue date plus one unique cert → one
    # collision dropped, one re-pointed.
    conn.execute(
        text(
            "INSERT INTO irc_certificates (boat_id, cert_number, issue_date) "
            "VALUES (:b, 'AUS-100', '2025-06-01')"
        ),
        {"b": WINNER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO irc_certificates (boat_id, cert_number, issue_date) "
            "VALUES (:b, 'AUS-100', '2023-06-01')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO irc_certificates (boat_id, cert_number, issue_date) "
            "VALUES (:b, 'AUS-200', '2022-06-01')"
        ),
        {"b": LOSER_ID},
    )

    # TCC snapshots: one shared date (winner newer cert_year) + one unique.
    conn.execute(
        text(
            "INSERT INTO tcc_snapshots (boat_id, snapshot_date, cert_year, tcc) "
            "VALUES (:b, '2025-01-15', 2025, 1.012)"
        ),
        {"b": WINNER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO tcc_snapshots (boat_id, snapshot_date, cert_year, tcc) "
            "VALUES (:b, '2025-01-15', 2024, 1.011)"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO tcc_snapshots (boat_id, snapshot_date, cert_year, tcc) "
            "VALUES (:b, '2024-06-15', 2024, 1.009)"
        ),
        {"b": LOSER_ID},
    )

    # Simple-FK tables named by the AD-01-14 contract.
    conn.execute(
        text(
            "INSERT INTO boat_identities (boat_id, boat_name, sail_number, source) "
            "VALUES (:b, 'Fifth Avenue', '6138', 'topyacht')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO orders (boat_id, email) VALUES (:b, 'owner@example.com')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO event_entries (event_id, boat_id, sail_number, boat_name) "
            "VALUES (1, :b, '6138', 'Fifth Avenue')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO boat_events (boat_id, event_type) VALUES (:b, 'launched')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO orc_certificates (boat_id, ref_no, class_name) "
            "VALUES (:b, 'ORC-55', 'ORC')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO insight_cache (boat_id, detail_level) VALUES (:b, 'full')"
        ),
        {"b": LOSER_ID},
    )
    conn.execute(
        text(
            "INSERT INTO boat_corrections (boat_id, field_name, proposed_value) "
            "VALUES (:b, 'design', 'Young 88')"
        ),
        {"b": LOSER_ID},
    )
    # News mentions: winner + loser both mentioned in news 1 (collision →
    # loser's row deleted), loser alone in news 2 (re-pointed).
    for nid, url in ((1, "https://sail-world.com/1"), (2, "https://sail-world.com/2")):
        conn.execute(
            text(
                "INSERT INTO boat_news (id, source_domain, url, title) "
                "VALUES (:id, 'sail-world.com', :url, 'Article')"
            ),
            {"id": nid, "url": url},
        )
    conn.execute(
        text("INSERT INTO boat_news_mentions (news_id, boat_id) VALUES (1, :b)"),
        {"b": WINNER_ID},
    )
    conn.execute(
        text("INSERT INTO boat_news_mentions (news_id, boat_id) VALUES (1, :b)"),
        {"b": LOSER_ID},
    )
    conn.execute(
        text("INSERT INTO boat_news_mentions (news_id, boat_id) VALUES (2, :b)"),
        {"b": LOSER_ID},
    )

    # Queue rows.
    queue_rows = [
        (CLUSTER, "B", WINNER_ID, "Fifth Avenue", "AUS", "6128", WINNER_RR, 1, 2, "PENDING"),
        (CLUSTER, "B", LOSER_ID, "Fifth Avenue", "AUS", "6138", LOSER_RR, 2, 2, "PENDING"),
        (NOT_DUPE_CLUSTER, "D", 20101, "Fox Bat", "GBR", "GBR 1", 10, 0, 3, "PENDING"),
        (NOT_DUPE_CLUSTER, "D", 20102, "Fox Bat", "GBR", "GBR 2", 4, 0, 3, "PENDING"),
        (NOT_DUPE_CLUSTER, "D", 20103, "Fox Bat", "GBR", "GBR 3", 0, 0, 3, "PENDING"),
        (SKIP_CLUSTER, "B", 20201, "Grey Gull", "NZL", "NZL 10", 7, 0, 2, "PENDING"),
        (SKIP_CLUSTER, "B", 20202, "Grey Gull", "NZL", "NZL 11", 3, 0, 2, "PENDING"),
        ("SEA MIST|AUS", "B", 20301, "Sea Mist", "AUS", "AUS 5", 5, 0, 2, "NOT_DUPE"),
        ("SEA MIST|AUS", "B", 20302, "Sea Mist", "AUS", "AUS 6", 2, 0, 2, "NOT_DUPE"),
    ]
    for cid, tier, bid, name, country, sail, rr, certs, size, verdict in queue_rows:
        conn.execute(
            text(
                "INSERT INTO dupe_review_queue (cluster_id, tier, boat_id, "
                "boat_name, country, sail_number, design, year_built, "
                "race_results, cert_count, latest_activity, cluster_size, why, "
                "verdict, reviewed_by, reviewed_at) "
                "VALUES (:cid, :tier, :bid, :name, :country, :sail, 'X', 2001, "
                ":rr, :certs, '2025-06-01', :size, 'same_design_close_year_same_country', "
                ":verdict, "
                "CASE WHEN :verdict = 'PENDING' THEN NULL ELSE 'admin:seed' END, "
                "CASE WHEN :verdict = 'PENDING' THEN NULL ELSE CURRENT_TIMESTAMP END)"
            ),
            {
                "cid": cid,
                "tier": tier,
                "bid": bid,
                "name": name,
                "country": country,
                "sail": sail,
                "rr": rr,
                "certs": certs,
                "size": size,
                "verdict": verdict,
            },
        )


@pytest.fixture()
def client(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from irc_data.api import app as app_module
    from irc_data.api.deps import get_db
    from irc_data.api.routers import admin as admin_module

    monkeypatch.setattr(admin_module, "ADMIN_PASSWORD", "test-secret")

    app_module.app.dependency_overrides[get_db] = lambda: engine
    try:
        yield TestClient(app_module.app)
    finally:
        app_module.app.dependency_overrides.pop(get_db, None)


def _table_count(engine, table, where="1=1", params=None):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), params or {}
        ).scalar()


# ---------------------------------------------------------------------------
# GET /v1/admin/dupes
# ---------------------------------------------------------------------------


class TestListDupes:
    def test_requires_admin_auth(self, client):
        assert client.get("/v1/admin/dupes").status_code == 401

    def test_lists_pending_clusters_grouped_with_boats_by_evidence(self, client):
        resp = client.get("/v1/admin/dupes", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == "dupe-review-v1"
        assert body["pending_total"] == 3  # SEA MIST already decided

        by_id = {c["cluster_id"]: c for c in body["clusters"]}
        assert set(by_id) == {CLUSTER, NOT_DUPE_CLUSTER, SKIP_CLUSTER}

        cluster = by_id[CLUSTER]
        assert cluster["tier"] == "B"
        assert cluster["size"] == 2
        assert len(cluster["boats"]) == 2
        # Boats ordered by evidence: the 551-result boat leads, making it
        # the pre-selected merge target on the screen.
        assert [b["boat_id"] for b in cluster["boats"]] == [WINNER_ID, LOSER_ID]
        assert cluster["boats"][0]["race_results"] == WINNER_RR
        assert cluster["boats"][1]["race_results"] == LOSER_RR

        # Not-dupe cluster carries 3 boats, ordered by evidence.
        fox = by_id[NOT_DUPE_CLUSTER]
        assert [b["boat_id"] for b in fox["boats"]] == [20101, 20102, 20103]

    def test_filters_tier_size_country(self, client):
        resp = client.get("/v1/admin/dupes", params={"tier": "D"}, headers=ADMIN_HEADERS)
        assert {c["cluster_id"] for c in resp.json()["clusters"]} == {NOT_DUPE_CLUSTER}
        assert resp.json()["total"] == 1
        assert resp.json()["pending_total"] == 3  # unfiltered headline count

        resp = client.get("/v1/admin/dupes", params={"size": 3}, headers=ADMIN_HEADERS)
        assert {c["cluster_id"] for c in resp.json()["clusters"]} == {NOT_DUPE_CLUSTER}

        resp = client.get(
            "/v1/admin/dupes", params={"country": "NZL"}, headers=ADMIN_HEADERS
        )
        assert {c["cluster_id"] for c in resp.json()["clusters"]} == {SKIP_CLUSTER}

        resp = client.get(
            "/v1/admin/dupes",
            params={"tier": "B", "country": "AUS"},
            headers=ADMIN_HEADERS,
        )
        assert {c["cluster_id"] for c in resp.json()["clusters"]} == {CLUSTER}

    def test_cursor_pagination(self, client):
        first = client.get("/v1/admin/dupes", params={"limit": 2}, headers=ADMIN_HEADERS)
        assert first.status_code == 200
        body1 = first.json()
        assert len(body1["clusters"]) == 2
        assert body1["next_cursor"]

        second = client.get(
            "/v1/admin/dupes",
            params={"limit": 2, "cursor": body1["next_cursor"]},
            headers=ADMIN_HEADERS,
        )
        body2 = second.json()
        ids1 = {c["cluster_id"] for c in body1["clusters"]}
        ids2 = {c["cluster_id"] for c in body2["clusters"]}
        assert not (ids1 & ids2), "pages must not overlap"
        assert ids1 | ids2 == {CLUSTER, NOT_DUPE_CLUSTER, SKIP_CLUSTER}
        assert body2["next_cursor"] is None

    def test_invalid_cursor_is_400(self, client):
        resp = client.get(
            "/v1/admin/dupes", params={"cursor": "!!!"}, headers=ADMIN_HEADERS
        )
        assert resp.status_code == 400

    def test_meta_returns_filter_vocabulary(self, client):
        resp = client.get("/v1/admin/dupes/meta", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["tiers"] == ["B", "D"]
        assert body["sizes"] == [2, 3]
        assert set(body["countries"]) == {"AUS", "GBR", "NZL"}
        assert "different_design" in body["not_dupe_reasons"]


# ---------------------------------------------------------------------------
# POST …/merge — the acceptance scenario
# ---------------------------------------------------------------------------


class TestMerge:
    def test_merge_fifth_avenue_cluster(self, client, engine):
        """Merging FIFTH AVENUE|AUS into boats/17213 leaves one boat with
        697 race results, one boat_merges row per loser with a snapshot,
        both queue rows MERGED with reviewed_by set."""
        resp = client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID, "reviewed_by": "admin:alice"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verdict"] == "MERGED"
        assert body["winner_id"] == WINNER_ID
        assert body["loser_ids"] == [LOSER_ID]
        assert body["reviewed_by"] == "admin:alice"

        # Exactly one boat left, with 697 race results.
        assert _table_count(engine, "boats", "boat_name = 'Fifth Avenue'") == 1
        assert _table_count(engine, "race_results", f"boat_id = {WINNER_ID}") == TOTAL_RR
        assert _table_count(engine, "race_results", f"boat_id = {LOSER_ID}") == 0

        # Every contract-named FK table re-pointed.
        for table in (
            "boat_identities",
            "event_entries",
            "irc_certificates",
            "orc_certificates",
            "tcc_snapshots",
            "orders",
            "boat_events",
            "boat_news_mentions",
        ):
            assert _table_count(engine, table, f"boat_id = {LOSER_ID}") == 0, table

        # The unique cert + unique TCC snapshot moved across.
        assert _table_count(
            engine, "irc_certificates",
            f"boat_id = {WINNER_ID} AND cert_number = 'AUS-200'",
        ) == 1
        assert _table_count(engine, "tcc_snapshots", f"boat_id = {WINNER_ID}") == 2
        # News-mention collision resolved: winner keeps news 1, gains news 2.
        assert _table_count(
            engine, "boat_news_mentions", f"boat_id = {WINNER_ID}"
        ) == 2

        # One boat_merges row for the loser, with the full snapshot.
        with engine.connect() as conn:
            merges = conn.execute(
                text(
                    "SELECT winner_id, loser_id, cluster_key, loser_snapshot "
                    "FROM boat_merges WHERE cluster_key = :ck"
                ),
                {"ck": CLUSTER},
            ).mappings().all()
        assert len(merges) == 1
        merge = merges[0]
        assert merge["winner_id"] == WINNER_ID
        assert merge["loser_id"] == LOSER_ID
        snap = json.loads(merge["loser_snapshot"])
        assert snap["boat"]["id"] == LOSER_ID
        assert snap["boat"]["sail_number"] == "6138"
        # Collision extras preserved: the dropped older cert + TCC snapshot +
        # the duplicate news mention.
        kinds = {e["kind"] for e in snap["extras"]}
        assert "certificate_collision_dropped" in kinds
        assert "tcc_snapshot_collision_dropped" in kinds
        assert "boat_news_mentions_collision_dropped" in kinds

        # Both queue rows MERGED with reviewed_by + reviewed_at set.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT verdict, reviewed_by, reviewed_at FROM dupe_review_queue "
                    "WHERE cluster_id = :cid ORDER BY id"
                ),
                {"cid": CLUSTER},
            ).mappings().all()
        assert len(rows) == 2
        for r in rows:
            assert r["verdict"] == "MERGED"
            assert r["reviewed_by"] == "admin:alice"
            assert r["reviewed_at"] is not None

        # Audited to admin_edits.
        assert _table_count(
            engine, "admin_edits",
            "table_name = 'dupe_review_queue' AND column_name = 'verdict' "
            "AND new_value = 'MERGED'",
        ) == 2

    def test_repeating_merge_returns_409(self, client, engine):
        first = client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID},
            headers=ADMIN_HEADERS,
        )
        assert first.status_code == 200

        repeat = client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID},
            headers=ADMIN_HEADERS,
        )
        assert repeat.status_code == 409
        assert "already decided" in repeat.json()["detail"]

        # The cluster is gone from the pending queue too.
        listing = client.get("/v1/admin/dupes", headers=ADMIN_HEADERS).json()
        assert CLUSTER not in {c["cluster_id"] for c in listing["clusters"]}
        assert listing["pending_total"] == 2

    def test_merge_unknown_cluster_is_404(self, client):
        resp = client.post(
            "/v1/admin/dupes/clusters/NOPE%7CAUS/merge",
            json={"winner_id": 1},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    def test_merge_winner_outside_cluster_is_422(self, client, engine):
        resp = client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": 20101},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422
        # Nothing merged.
        assert _table_count(engine, "boats") == 9
        assert _table_count(engine, "boat_merges") == 0

    def test_merge_requires_admin_auth(self, client):
        resp = client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST …/not-dupe
# ---------------------------------------------------------------------------


class TestNotDupe:
    def test_not_dupe_writes_boat_not_dupe_and_verdict(self, client, engine):
        resp = client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json={
                "reason": "different_design",
                "note": "J/111 vs X-35 — different boats",
                "reviewed_by": "admin:bob",
            },
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["verdict"] == "NOT_DUPE"
        assert body["boat_ids"] == [20101, 20102, 20103]
        assert body["reason"] == "different_design"

        with engine.connect() as conn:
            nd = conn.execute(
                text(
                    "SELECT cluster_key, boat_ids, reason FROM boat_not_dupe "
                    "WHERE cluster_key = :ck"
                ),
                {"ck": NOT_DUPE_CLUSTER},
            ).mappings().all()
            rows = conn.execute(
                text(
                    "SELECT verdict, verdict_note, reviewed_by, reviewed_at "
                    "FROM dupe_review_queue WHERE cluster_id = :ck"
                ),
                {"ck": NOT_DUPE_CLUSTER},
            ).mappings().all()

        assert len(nd) == 1
        assert json.loads(nd[0]["boat_ids"]) == [20101, 20102, 20103]
        assert nd[0]["reason"] == "different_design"

        assert len(rows) == 3
        for r in rows:
            assert r["verdict"] == "NOT_DUPE"
            assert r["reviewed_by"] == "admin:bob"
            assert r["reviewed_at"] is not None
            assert "different_design" in r["verdict_note"]

        # No boats touched, nothing merged.
        assert _table_count(engine, "boats") == 9
        assert _table_count(engine, "boat_merges") == 0

    def test_not_dupe_repeat_is_409(self, client):
        payload = {"reason": "different_year"}
        first = client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json=payload,
            headers=ADMIN_HEADERS,
        )
        assert first.status_code == 200
        repeat = client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json=payload,
            headers=ADMIN_HEADERS,
        )
        assert repeat.status_code == 409

    def test_not_dupe_requires_reason(self, client):
        resp = client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json={},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST …/skip
# ---------------------------------------------------------------------------


class TestSkip:
    def test_skip_marks_cluster_and_hides_it(self, client, engine):
        resp = client.post(
            f"/v1/admin/dupes/clusters/{SKIP_CLUSTER}/skip",
            json={"note": "come back after the next TCC pass"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["verdict"] == "SKIPPED"

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT verdict, reviewed_by, reviewed_at FROM dupe_review_queue "
                    "WHERE cluster_id = :ck"
                ),
                {"ck": SKIP_CLUSTER},
            ).mappings().all()
        assert {r["verdict"] for r in rows} == {"SKIPPED"}
        assert all(r["reviewed_by"] for r in rows)
        assert all(r["reviewed_at"] is not None for r in rows)

        # No queue/boats side-effects beyond the verdict.
        assert _table_count(engine, "boats") == 9
        assert _table_count(engine, "boat_merges") == 0
        assert _table_count(engine, "boat_not_dupe") == 0

        listing = client.get("/v1/admin/dupes", headers=ADMIN_HEADERS).json()
        assert SKIP_CLUSTER not in {c["cluster_id"] for c in listing["clusters"]}

    def test_skipped_cluster_reappears_when_requeued(self, client, engine):
        """Skip is a deferral: a later triage pass inserts fresh PENDING rows
        for the same cluster and it returns to the queue."""
        client.post(
            f"/v1/admin/dupes/clusters/{SKIP_CLUSTER}/skip",
            json={},
            headers=ADMIN_HEADERS,
        )
        with engine.begin() as conn:
            for bid in (20201, 20202):
                conn.execute(
                    text(
                        "INSERT INTO dupe_review_queue (cluster_id, tier, boat_id, "
                        "boat_name, country, cluster_size, verdict) "
                        "VALUES (:ck, 'B', :bid, 'Grey Gull', 'NZL', 2, 'PENDING')"
                    ),
                    {"ck": SKIP_CLUSTER, "bid": bid},
                )
        listing = client.get("/v1/admin/dupes", headers=ADMIN_HEADERS).json()
        assert SKIP_CLUSTER in {c["cluster_id"] for c in listing["clusters"]}

    def test_skip_repeat_is_409(self, client):
        client.post(
            f"/v1/admin/dupes/clusters/{SKIP_CLUSTER}/skip",
            json={},
            headers=ADMIN_HEADERS,
        )
        repeat = client.post(
            f"/v1/admin/dupes/clusters/{SKIP_CLUSTER}/skip",
            json={},
            headers=ADMIN_HEADERS,
        )
        assert repeat.status_code == 409


# ---------------------------------------------------------------------------
# GET /v1/admin/dupes/history
# ---------------------------------------------------------------------------


class TestHistory:
    def test_history_lists_merges_and_not_dupes(self, client, engine):
        client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID, "reviewed_by": "admin:alice"},
            headers=ADMIN_HEADERS,
        )
        client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json={"reason": "name_coincidence", "reviewed_by": "admin:bob"},
            headers=ADMIN_HEADERS,
        )

        resp = client.get("/v1/admin/dupes/history", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        by_cluster = {e["cluster_key"]: e for e in entries}
        assert set(by_cluster) >= {CLUSTER, NOT_DUPE_CLUSTER}

        merged = by_cluster[CLUSTER]
        assert merged["kind"] == "merged"
        assert merged["winner_id"] == WINNER_ID
        assert merged["winner_name"] == "Fifth Avenue"
        assert merged["loser_id"] == LOSER_ID
        assert merged["loser_name"] == "Fifth Avenue"
        assert merged["loser_snapshot"]["boat"]["id"] == LOSER_ID

        nd = by_cluster[NOT_DUPE_CLUSTER]
        assert nd["kind"] == "not_dupe"
        assert nd["reason"] == "name_coincidence"
        assert nd["boat_ids"] == [20101, 20102, 20103]
        assert nd["reviewed_by"] == "admin:bob"

        # Newest first.
        ats = [e["at"] for e in entries]
        assert ats == sorted(ats, reverse=True)

    def test_history_kind_filter(self, client):
        client.post(
            f"/v1/admin/dupes/clusters/{CLUSTER}/merge",
            json={"winner_id": WINNER_ID},
            headers=ADMIN_HEADERS,
        )
        client.post(
            f"/v1/admin/dupes/clusters/{NOT_DUPE_CLUSTER}/not-dupe",
            json={"reason": "different_region"},
            headers=ADMIN_HEADERS,
        )
        merged_only = client.get(
            "/v1/admin/dupes/history", params={"kind": "merged"}, headers=ADMIN_HEADERS
        ).json()["entries"]
        assert {e["kind"] for e in merged_only} == {"merged"}
        nd_only = client.get(
            "/v1/admin/dupes/history", params={"kind": "not_dupe"}, headers=ADMIN_HEADERS
        ).json()["entries"]
        assert {e["kind"] for e in nd_only} == {"not_dupe"}

    def test_history_requires_admin_auth(self, client):
        assert client.get("/v1/admin/dupes/history").status_code == 401
