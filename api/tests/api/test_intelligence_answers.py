"""IN-01-02 — IntelligenceAnswerV1 query service (SPEC-25 §2).

The wrapped service calls (``queries.fetch_*``) use PostgreSQL-only SQL
(LATERAL, pg_trgm), so they are replaced with SQLite-backed stand-ins that
return the same response shapes. Everything else - provenance, freshness,
dataset version, quality status, incomplete rules, the router - runs for real
against an in-memory SQLite fixture.
"""
from __future__ import annotations

import statistics
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from irc_data.api.app import app
from irc_data.api.deps import get_db
from irc_data.api.schemas.intelligence import ConfidenceAssessmentV1, IntelligenceAnswerV1
from irc_data.api.services.intelligence import REGISTRY, evidence as ev, queries
from irc_data.api.services.report.facts_bundle import SECTION_ORDER

LATENCY_BUDGET_S = 0.300

BOATS = [
    # id, name, sail, design, country, updated_at, tcc
    (1, "Alpha", "GBR1", "J/109", "GBR", "2026-03-01 10:00:00", 1.020),
    (2, "Bravo", "GBR2", "J/109", "GBR", "2026-03-02 10:00:00", 1.021),
    (3, "Charlie", "GBR3", "J/109", "GBR", "2026-03-03 10:00:00", 1.019),
    (4, "Delta", "GBR4", "J/109", "GBR", "2026-03-04 10:00:00", 1.022),
    (5, "Echo", "GBR5", "J/109", "GBR", "2026-03-05 10:00:00", 1.018),
    (6, "Foxtrot", "FRA6", "One-Off", "FRA", "2026-03-06 10:00:00", None),
    (7, "Golf", "FRA7", None, "FRA", "2026-03-07 10:00:00", 0.98),
]


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boats (id INTEGER PRIMARY KEY, boat_name TEXT, sail_number TEXT, "
            "design TEXT, country TEXT, updated_at TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE tcc_snapshots (boat_id INTEGER, tcc REAL, snapshot_date TEXT)"
        ))
        for bid, name, sail, design, country, upd, tcc in BOATS:
            conn.execute(
                text("INSERT INTO boats VALUES (:i,:n,:s,:d,:c,:u)"),
                {"i": bid, "n": name, "s": sail, "d": design, "c": country, "u": upd},
            )
            if tcc is not None:
                conn.execute(
                    text("INSERT INTO tcc_snapshots VALUES (:i,:t,'2026-02-01')"),
                    {"i": bid, "t": tcc},
                )
    yield eng
    eng.dispose()


def _boat_row(engine, boat_id):
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT b.*, (SELECT tcc FROM tcc_snapshots t WHERE t.boat_id=b.id "
            "ORDER BY snapshot_date DESC LIMIT 1) AS tcc, "
            "(SELECT snapshot_date FROM tcc_snapshots t WHERE t.boat_id=b.id "
            "ORDER BY snapshot_date DESC LIMIT 1) AS snapshot_date FROM boats b WHERE b.id=:i"
        ), {"i": boat_id}).first()
    return dict(row._mapping) if row else None


@pytest.fixture()
def client(engine, monkeypatch):
    def fake_boat(eng, boat_id):
        row = _boat_row(eng, boat_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Boat not found")
        row["irc_tcc"] = row.pop("tcc")
        row["irc_snapshot_date"] = row.pop("snapshot_date")
        row["orc_gph"] = None
        row["orc_snapshot_date"] = None
        return row

    def fake_search(eng, q, limit, offset):
        with eng.connect() as conn:
            rows = conn.execute(
                text("SELECT id, boat_name, sail_number, design, country FROM boats "
                     "WHERE boat_name LIKE :p OR sail_number LIKE :p ORDER BY boat_name LIMIT :l OFFSET :o"),
                {"p": f"%{q}%", "l": limit, "o": offset},
            )
            results = [dict(r._mapping) for r in rows]
        return {"query": q, "total": len(results), "limit": limit, "offset": offset,
                "results": results, "suggestions": []}

    def fake_fleet(eng, design, sort, limit, offset):
        with eng.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(
                text("SELECT b.id, b.boat_name, b.sail_number, b.country, "
                     "(SELECT snapshot_date FROM tcc_snapshots t WHERE t.boat_id=b.id) AS rating_date "
                     "FROM boats b WHERE lower(b.design)=lower(:d) ORDER BY b.boat_name LIMIT :l OFFSET :o"),
                {"d": design, "l": limit, "o": offset})]
        if not rows:
            raise HTTPException(status_code=404, detail="Design not found")
        return {"design": design, "total": len(rows), "limit": limit, "offset": offset,
                "sort": sort, "results": rows}

    def fake_facts(eng, boat_id):
        row = _boat_row(eng, boat_id)
        sections = {s: {"boat_name": row["boat_name"], "design": row["design"]} if s == "s02_identity"
                    else {"ok": True} for s in SECTION_ORDER}
        engines = {"rai": {"rai": 1.5}, "design_model": None, "smart_boats": None, "fleet_wide_model": None}
        return {
            "schema_version": "ReportFactsV1",
            "boat": {"id": boat_id, "name": row["boat_name"], "sail_number": row["sail_number"],
                     "design": row["design"]},
            "sections": sections, "engines": engines,
            "facts_sha256": ev.canonical_sha({"sections": sections, "engines": engines}),
        }

    monkeypatch.setattr(queries, "fetch_boat", fake_boat)
    monkeypatch.setattr(queries, "fetch_search", fake_search)
    monkeypatch.setattr(queries, "fetch_design_fleet", fake_fleet)
    monkeypatch.setattr(queries, "fetch_report_facts", fake_facts)
    app.dependency_overrides[get_db] = lambda: engine
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# key -> (URL for a complete answer, URL with a missing/unusable input,
#         expected missing code, extra (URL, expected code) SPEC-25 §1.4 cases)
CASES = {
    "boat_profile.v1": (
        "/v1/intelligence/boats/1", "/v1/intelligence/boats/999", "boat_not_found",
        [("/v1/intelligence/boats/6", "no_current_certificate"),
         ("/v1/intelligence/boats/7", "no_design_class")],
    ),
    "rating_book_search.v1": (
        "/v1/intelligence/search?q=Alpha", "/v1/intelligence/search?q=", "q",
        [("/v1/intelligence/search?q=%20%20", "q")],
    ),
    "fleet_explorer.v1": (
        "/v1/intelligence/fleet/J%2F109", "/v1/intelligence/fleet/Nonexistent", "design_not_found",
        [("/v1/intelligence/fleet/One-Off", "class_too_small")],
    ),
    "report_facts.v1": (
        "/v1/intelligence/report-facts/1", "/v1/intelligence/report-facts/999", "boat_not_found",
        [("/v1/intelligence/report-facts/7", "no_design_class")],
    ),
}
QUERY_KEYS = sorted(CASES)


@pytest.mark.parametrize("key", QUERY_KEYS)
def test_shape_validates(client, key):
    ok_url = CASES[key][0]
    resp = client.get(ok_url)
    assert resp.status_code == 200, resp.text
    answer = IntelligenceAnswerV1.model_validate(resp.json())
    name, version = key.split(".")
    assert (answer.query, answer.version) == (name, version)
    assert answer.data
    assert answer.incomplete is False and answer.missing == []
    assert answer.provenance.quality_status == "ok"
    confidence = ConfidenceAssessmentV1.model_validate(answer.confidence)
    assert confidence.band in {"high", "medium", "low"}


@pytest.mark.parametrize("key", QUERY_KEYS)
def test_provenance_non_empty(client, key):
    ok_url = CASES[key][0]
    prov = IntelligenceAnswerV1.model_validate(client.get(ok_url).json()).provenance
    assert prov.dataset_version == "irc-canonical-20260307"
    assert len(prov.facts_sha) == 64
    assert prov.lineage and all(isinstance(r, str) and r for r in prov.lineage)
    assert prov.freshness_at is not None
    # deterministic: same data -> same hash
    again = IntelligenceAnswerV1.model_validate(client.get(ok_url).json()).provenance
    assert again.facts_sha == prov.facts_sha


@pytest.mark.parametrize("key", QUERY_KEYS)
def test_incomplete_on_missing_input(client, key):
    _, bad_url, code, extras = CASES[key]
    for url, expected in [(bad_url, code), *extras]:
        resp = client.get(url)
        assert resp.status_code == 200, resp.text
        answer = IntelligenceAnswerV1.model_validate(resp.json())
        assert answer.incomplete is True, url
        assert expected in answer.missing, url
        assert answer.provenance.quality_status == "incomplete"
        # an incomplete answer still carries a dataset version and lineage refs
        assert answer.provenance.dataset_version and answer.provenance.lineage


def test_unknown_query_404(client):
    assert sorted(REGISTRY) == QUERY_KEYS
    listed = client.get("/v1/intelligence/queries").json()["queries"]
    assert sorted(q["query"] for q in listed) == QUERY_KEYS
    # the generic runner serves registered queries ...
    ok = client.get("/v1/intelligence/query/boat_profile.v1?boat_id=1")
    assert ok.status_code == 200 and ok.json()["query"] == "boat_profile"
    # ... and 404s on unknown names and unknown versions
    for bad in ("no_such_query.v1", "boat_profile.v9", "boat_profile"):
        resp = client.get(f"/v1/intelligence/query/{bad}")
        assert resp.status_code == 404, bad


def test_latency_budget_p95_300ms(client):
    urls = [CASES[k][0] for k in QUERY_KEYS]
    for url in urls:  # warm
        assert client.get(url).status_code == 200
    samples = []
    for _ in range(40):
        for url in urls:
            t0 = time.perf_counter()
            assert client.get(url).status_code == 200
            samples.append(time.perf_counter() - t0)
    p95 = statistics.quantiles(samples, n=100)[94]
    assert p95 < LATENCY_BUDGET_S, f"p95={p95 * 1000:.1f}ms exceeds 300ms"
