"""Tests for the LineageService (DP-02-05).

Tests the service's ability to build lineage graphs from:
* Explicit provenance rows in the ``field_provenance`` table.
* Implicit provenance from existing table columns (source, source_url).

Uses an in-memory SQLite database so no Postgres is required.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from irc_data.lineage.models import EdgeType, LineageGraphV1, NodeType
from irc_data.lineage.service import LineageService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """Create an in-memory SQLite engine with the field_provenance table.

    Uses ``StaticPool`` so that all connections share the same underlying
    SQLite database (in-memory SQLite is otherwise per-connection).
    """
    eng = create_engine(
        "sqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # Create the field_provenance table (SQLite-compatible DDL)
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE field_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                entity TEXT NOT NULL,
                field TEXT,
                value TEXT,
                record_id INTEGER,
                source_slug TEXT,
                content_hash TEXT,
                url TEXT,
                fetched_at TEXT,
                model_version TEXT,
                rule_version TEXT,
                confidence REAL,
                span_text TEXT,
                span_start INTEGER,
                span_end INTEGER,
                observed_at TEXT DEFAULT (datetime('now')),
                parent_id INTEGER,
                edge_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """))

        # Also create a mock irc_certificates table for implicit tests
        conn.execute(text("""
            CREATE TABLE irc_certificates (
                id INTEGER PRIMARY KEY,
                boat_id INTEGER,
                cert_number TEXT,
                source TEXT,
                source_url TEXT,
                pdf_path TEXT,
                raw_data TEXT
            )
        """))

        # Create a mock tcc_snapshots table for implicit tests
        conn.execute(text("""
            CREATE TABLE tcc_snapshots (
                id INTEGER PRIMARY KEY,
                boat_id INTEGER,
                snapshot_date TEXT,
                tcc REAL
            )
        """))

    return eng


# ---------------------------------------------------------------------------
# Explicit provenance tests
# ---------------------------------------------------------------------------


def _insert_provenance_row(conn, **kwargs):
    """Insert a row into field_provenance and return the id."""
    cols = []
    vals = []
    for k, v in kwargs.items():
        cols.append(k)
        vals.append(v)
    placeholders = ", ".join(f":c_{c}" for c in cols)
    col_list = ", ".join(cols)
    params = {f"c_{c}": v for c, v in zip(cols, vals)}
    result = conn.execute(
        text(f"INSERT INTO field_provenance ({col_list}) VALUES ({placeholders})"),
        params,
    )
    return result.lastrowid


def test_explicit_direct_lineage(engine: Engine):
    """Service builds a complete chain from explicit provenance rows."""
    service = LineageService(engine)

    with engine.begin() as conn:
        # Insert raw artifact node
        raw_id = _insert_provenance_row(
            conn,
            node_type="raw_artifact",
            entity="irc_certificates",
            source_slug="irc-certs",
            url="https://ircrating.org/cert/GBR12345.pdf",
            content_hash="abc123",
            fetched_at="2026-01-15T02:00:00",
        )
        # Insert extracted span node (parent = raw)
        span_id = _insert_provenance_row(
            conn,
            node_type="extracted_span",
            entity="irc_certificates",
            field="sail_number",
            value="AUS 123",
            source_slug="irc-certs",
            span_text="AUS 123",
            span_start=245,
            span_end=251,
            model_version="pdfplumber-v0.10.4",
            confidence=0.99,
            parent_id=raw_id,
            edge_type="extracted_from",
        )
        # Insert canonical field node (parent = span)
        _insert_provenance_row(
            conn,
            node_type="canonical_field",
            entity="boats",
            field="sail_number",
            value="AUS 123",
            record_id=1,
            rule_version="copy-v1",
            parent_id=span_id,
            edge_type="derived_from",
        )

    graph = service.get_lineage("boats", "sail_number", 1)

    assert graph.canonical_field == "boats.sail_number"
    assert graph.canonical_value == "AUS 123"
    assert graph.record_id == 1
    assert graph.complete
    assert graph.has_raw_artifact()
    assert not graph.missing_edges
    assert len(graph.nodes) >= 3  # canonical, span, raw
    assert graph.is_acyclic()
    assert graph.citation  # Non-empty citation


def test_explicit_transformed_lineage(engine: Engine):
    """Service builds a transformed chain with intermediate node."""
    service = LineageService(engine)

    with engine.begin() as conn:
        raw_id = _insert_provenance_row(
            conn,
            node_type="raw_artifact",
            entity="irc_certificates",
            source_slug="irc-certs",
            url="https://ircrating.org/cert/GBR12345.pdf",
            content_hash="def456",
            fetched_at="2026-01-15T02:00:00",
        )
        span_id = _insert_provenance_row(
            conn,
            node_type="extracted_span",
            entity="irc_certificates",
            field="tcc",
            value="TCC: 0.9850",
            source_slug="irc-certs",
            span_text="TCC: 0.9850",
            span_start=1427,
            span_end=1439,
            model_version="pdfplumber-v0.10.4",
            confidence=0.95,
            parent_id=raw_id,
            edge_type="extracted_from",
        )
        transformed_id = _insert_provenance_row(
            conn,
            node_type="transformed_value",
            entity="tcc_snapshots",
            field="tcc",
            value="0.9850",
            rule_version="tcc-normalise-v2",
            parent_id=span_id,
            edge_type="derived_from",
        )
        _insert_provenance_row(
            conn,
            node_type="canonical_field",
            entity="tcc_snapshots",
            field="tcc",
            value="0.985",
            record_id=42,
            rule_version="tcc-normalise-v2",
            parent_id=transformed_id,
            edge_type="transformed_to",
        )

    graph = service.get_lineage("tcc_snapshots", "tcc", 42)

    assert graph.canonical_field == "tcc_snapshots.tcc"
    assert graph.canonical_value == "0.985"
    assert graph.complete
    assert graph.has_raw_artifact()
    assert len(graph.nodes) >= 4  # canonical, transformed, span, raw
    assert graph.is_acyclic()


def test_explicit_missing_edge(engine: Engine):
    """Service reports missing edge when parent_id is dangling."""
    service = LineageService(engine)

    with engine.begin() as conn:
        # Insert canonical node with a parent_id that doesn't exist
        _insert_provenance_row(
            conn,
            node_type="canonical_field",
            entity="boats",
            field="sail_number",
            value="AUS 999",
            record_id=99,
            parent_id=99999,  # Non-existent parent
            edge_type="derived_from",
        )

    graph = service.get_lineage("boats", "sail_number", 99)

    assert not graph.complete
    assert len(graph.missing_edges) > 0
    # The missing_edges should mention the parent
    missing_text = " ".join(graph.missing_edges).lower()
    assert "parent" in missing_text or "not found" in missing_text


# ---------------------------------------------------------------------------
# Implicit provenance tests
# ---------------------------------------------------------------------------


def test_implicit_lineage_from_irc_cert(engine: Engine):
    """Service falls back to implicit provenance from irc_certificates columns."""
    service = LineageService(engine)

    with engine.begin() as conn:
        # Insert a mock IRC certificate
        conn.execute(text("""
            INSERT INTO irc_certificates (id, boat_id, cert_number, source, source_url, pdf_path)
            VALUES (1, 1, 'GBR12345', 'irc-certs', 'https://ircrating.org/cert/GBR12345.pdf', '/data/certs/GBR12345.pdf')
        """))
        # Insert a mock tcc_snapshot that references the certificate via boat_id
        conn.execute(text("""
            INSERT INTO tcc_snapshots (id, boat_id, snapshot_date, tcc)
            VALUES (42, 1, '2026-01-15', 0.985)
        """))

    # No field_provenance rows exist → service should fall back to implicit
    graph = service.get_lineage("tcc_snapshots", "tcc", 42)

    assert graph.canonical_field == "tcc_snapshots.tcc"
    assert graph.canonical_value is not None
    # Should have found the source URL via the join
    assert graph.has_raw_artifact()
    raw_nodes = graph.raw_artifact_nodes()
    assert any(n.source_slug == "irc-certs" for n in raw_nodes)
    assert any(
        "ircrating.org" in (n.url or "") for n in raw_nodes
    )


def test_implicit_no_provenance(engine: Engine):
    """Service reports missing when no provenance is available."""
    service = LineageService(engine)

    # No data at all
    graph = service.get_lineage("tcc_snapshots", "tcc", 999)

    assert not graph.complete
    assert len(graph.missing_edges) > 0


def test_implicit_unknown_entity(engine: Engine):
    """Service reports missing for an unknown entity."""
    service = LineageService(engine)

    graph = service.get_lineage("unknown_table", "field", 1)

    assert not graph.complete
    assert len(graph.missing_edges) > 0


# ---------------------------------------------------------------------------
# Citation generation in service
# ---------------------------------------------------------------------------


def test_service_generates_citation(engine: Engine):
    """Service always generates a citation, even for incomplete graphs."""
    service = LineageService(engine)

    graph = service.get_lineage("tcc_snapshots", "tcc", 999)
    assert graph.citation  # Non-empty, even if it says "incomplete"
