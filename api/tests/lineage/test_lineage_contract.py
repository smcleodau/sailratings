"""Tests for the LineageGraphV1 contract and golden fixtures (DP-02-05).

Verifies:
* Each golden fixture is a valid acyclic lineage graph.
* Complete fixtures reach a raw artifact node.
* The incomplete fixture explicitly reports its missing edge.
* UI-safe citations are generated for each fixture.
* JSON round-trip preserves the graph.
"""

from __future__ import annotations

import json

import pytest

from irc_data.lineage.citation import generate_citation
from irc_data.lineage.models import (
    EdgeType,
    LineageGraphV1,
    LineageNodeV1,
    NodeType,
)

from tests.lineage.fixtures import (
    adjudicated_lineage,
    ALL_GOLDEN_FIXTURES,
    direct_lineage,
    incomplete_lineage,
    merged_lineage,
    transformed_lineage,
)


# ---------------------------------------------------------------------------
# Golden fixture tests — parametrised over all fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,fixture_fn", ALL_GOLDEN_FIXTURES)
def test_golden_fixture_is_acyclic(name: str, fixture_fn):
    """Every golden fixture must be an acyclic graph."""
    graph = fixture_fn()
    assert graph.is_acyclic(), f"Fixture '{name}' contains a cycle"


@pytest.mark.parametrize("name,fixture_fn", ALL_GOLDEN_FIXTURES)
def test_golden_fixture_has_nodes(name: str, fixture_fn):
    """Every golden fixture must have at least one node."""
    graph = fixture_fn()
    assert len(graph.nodes) > 0, f"Fixture '{name}' has no nodes"


@pytest.mark.parametrize("name,fixture_fn", ALL_GOLDEN_FIXTURES)
def test_golden_fixture_canonical_field(name: str, fixture_fn):
    """Every golden fixture must have a canonical-field node."""
    graph = fixture_fn()
    assert graph.canonical_node() is not None, (
        f"Fixture '{name}' has no canonical_field node"
    )


@pytest.mark.parametrize("name,fixture_fn", ALL_GOLDEN_FIXTURES)
def test_golden_fixture_citation(name: str, fixture_fn):
    """Every golden fixture must produce a non-empty citation."""
    graph = fixture_fn()
    graph.citation = generate_citation(graph)
    assert graph.citation, f"Fixture '{name}' produced an empty citation"


@pytest.mark.parametrize("name,fixture_fn", ALL_GOLDEN_FIXTURES)
def test_golden_fixture_json_roundtrip(name: str, fixture_fn):
    """Every golden fixture must survive JSON round-trip."""
    graph = fixture_fn()
    graph.citation = generate_citation(graph)
    json_str = graph.to_json()
    restored = LineageGraphV1.from_json(json_str)
    assert restored.canonical_field == graph.canonical_field
    assert restored.canonical_value == graph.canonical_value
    assert restored.record_id == graph.record_id
    assert len(restored.nodes) == len(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert restored.complete == graph.complete
    assert restored.missing_edges == graph.missing_edges


# ---------------------------------------------------------------------------
# Complete fixtures — must reach raw artifact
# ---------------------------------------------------------------------------


def test_direct_lineage_complete():
    """Direct lineage must be complete and reach a raw artifact."""
    graph = direct_lineage()
    assert graph.complete
    assert graph.has_raw_artifact()
    assert not graph.missing_edges
    assert len(graph.nodes) == 3  # canonical, span, raw
    assert len(graph.edges) == 2


def test_transformed_lineage_complete():
    """Transformed lineage must be complete with 4 nodes."""
    graph = transformed_lineage()
    assert graph.complete
    assert graph.has_raw_artifact()
    assert not graph.missing_edges
    assert len(graph.nodes) == 4  # canonical, transformed, span, raw
    assert len(graph.edges) == 3


def test_merged_lineage_complete():
    """Merged lineage must be complete with 2 branches."""
    graph = merged_lineage()
    assert graph.complete
    assert graph.has_raw_artifact()
    assert not graph.missing_edges
    # canonical + 2 spans + 2 raws = 5 nodes
    assert len(graph.nodes) == 5
    # 2 merged + 2 extracted = 4 edges
    assert len(graph.edges) == 4
    # Must have merged_from edges
    edge_types = {e.edge_type for e in graph.edges}
    assert EdgeType.MERGED_FROM.value in edge_types


def test_adjudicated_lineage_complete():
    """Adjudicated lineage must be complete with conflict resolution."""
    graph = adjudicated_lineage()
    assert graph.complete
    assert graph.has_raw_artifact()
    assert not graph.missing_edges
    assert len(graph.nodes) == 5
    assert len(graph.edges) == 4
    edge_types = {e.edge_type for e in graph.edges}
    assert EdgeType.ADJUDICATED_FROM.value in edge_types
    # The canonical value should be the IRC value (chosen)
    assert graph.canonical_value == "3200"


# ---------------------------------------------------------------------------
# Incomplete fixture — must report missing edge
# ---------------------------------------------------------------------------


def test_incomplete_lineage_reports_missing():
    """Incomplete lineage must explicitly report the missing edge."""
    graph = incomplete_lineage()
    assert not graph.complete
    assert len(graph.missing_edges) > 0
    assert not graph.has_raw_artifact()
    # The missing_edges should mention the raw artifact
    missing_text = " ".join(graph.missing_edges).lower()
    assert "raw artifact" in missing_text or "purged" in missing_text


# ---------------------------------------------------------------------------
# Acyclic guarantee — cycle detection
# ---------------------------------------------------------------------------


def test_acyclic_detection_simple_cycle():
    """A graph with a cycle must be detected as non-acyclic."""
    node_a = LineageNodeV1(
        node_id="a", node_type=NodeType.CANONICAL_FIELD.value,
        entity="test", field="x",
    )
    node_b = LineageNodeV1(
        node_id="b", node_type=NodeType.EXTRACTED_SPAN.value,
        entity="test", field="x",
    )
    graph = LineageGraphV1(
        canonical_field="test.x",
        canonical_value="1",
        record_id=1,
        nodes=[node_a, node_b],
        edges=[
            # a → b → a  (cycle)
            # Using from_node/to_node semantics
        ],
    )
    # Add edges that form a cycle
    from irc_data.lineage.models import LineageEdgeV1
    graph.edges = [
        LineageEdgeV1(from_node="a", to_node="b", edge_type="derived_from"),
        LineageEdgeV1(from_node="b", to_node="a", edge_type="derived_from"),
    ]
    assert not graph.is_acyclic()


def test_acyclic_detection_dag():
    """A proper DAG must be detected as acyclic."""
    node_a = LineageNodeV1(
        node_id="a", node_type=NodeType.CANONICAL_FIELD.value,
        entity="test", field="x",
    )
    node_b = LineageNodeV1(
        node_id="b", node_type=NodeType.EXTRACTED_SPAN.value,
        entity="test", field="x",
    )
    node_c = LineageNodeV1(
        node_id="c", node_type=NodeType.RAW_ARTIFACT.value,
        entity="test",
    )
    from irc_data.lineage.models import LineageEdgeV1
    graph = LineageGraphV1(
        canonical_field="test.x",
        canonical_value="1",
        record_id=1,
        nodes=[node_a, node_b, node_c],
        edges=[
            LineageEdgeV1(from_node="a", to_node="b", edge_type="derived_from"),
            LineageEdgeV1(from_node="b", to_node="c", edge_type="extracted_from"),
        ],
    )
    assert graph.is_acyclic()


# ---------------------------------------------------------------------------
# Citation content tests
# ---------------------------------------------------------------------------


def test_direct_citation_contains_source():
    """Direct lineage citation should mention the source slug."""
    graph = direct_lineage()
    graph.citation = generate_citation(graph)
    assert "irc-certs" in graph.citation


def test_transformed_citation_contains_model():
    """Transformed lineage citation should mention the model version."""
    graph = transformed_lineage()
    graph.citation = generate_citation(graph)
    assert "pdfplumber" in graph.citation.lower()


def test_merged_citation_mentions_merge():
    """Merged lineage citation should mention merging."""
    graph = merged_lineage()
    graph.citation = generate_citation(graph)
    assert "merged" in graph.citation.lower()


def test_adjudicated_citation_mentions_adjudication():
    """Adjudicated lineage citation should mention adjudication."""
    graph = adjudicated_lineage()
    graph.citation = generate_citation(graph)
    assert "adjudicated" in graph.citation.lower()


def test_incomplete_citation_warns():
    """Incomplete lineage citation should warn about missing edges."""
    graph = incomplete_lineage()
    graph.citation = generate_citation(graph)
    assert "warning" in graph.citation.lower() or "incomplete" in graph.citation.lower()


# ---------------------------------------------------------------------------
# Node/edge type validation
# ---------------------------------------------------------------------------


def test_node_types_are_valid():
    """All node types in fixtures must be from the NodeType enum."""
    valid_types = {nt.value for nt in NodeType}
    for name, fixture_fn in ALL_GOLDEN_FIXTURES:
        graph = fixture_fn()
        for node in graph.nodes:
            assert node.node_type in valid_types, (
                f"Fixture '{name}' has invalid node_type: {node.node_type}"
            )


def test_edge_types_are_valid():
    """All edge types in fixtures must be from the EdgeType enum."""
    valid_types = {et.value for et in EdgeType}
    for name, fixture_fn in ALL_GOLDEN_FIXTURES:
        graph = fixture_fn()
        for edge in graph.edges:
            assert edge.edge_type in valid_types, (
                f"Fixture '{name}' has invalid edge_type: {edge.edge_type}"
            )


def test_edge_endpoints_exist():
    """Every edge endpoint must reference an existing node."""
    for name, fixture_fn in ALL_GOLDEN_FIXTURES:
        graph = fixture_fn()
        node_ids = {n.node_id for n in graph.nodes}
        for edge in graph.edges:
            assert edge.from_node in node_ids, (
                f"Fixture '{name}': edge from_node '{edge.from_node}' not in nodes"
            )
            assert edge.to_node in node_ids, (
                f"Fixture '{name}': edge to_node '{edge.to_node}' not in nodes"
            )


# ---------------------------------------------------------------------------
# Confidence range validation
# ---------------------------------------------------------------------------


def test_confidence_in_range():
    """All confidence values must be in [0.0, 1.0]."""
    for name, fixture_fn in ALL_GOLDEN_FIXTURES:
        graph = fixture_fn()
        for node in graph.nodes:
            if node.confidence is not None:
                assert 0.0 <= node.confidence <= 1.0, (
                    f"Fixture '{name}': node '{node.node_id}' confidence "
                    f"{node.confidence} out of range [0, 1]"
                )
        for edge in graph.edges:
            if edge.confidence is not None:
                assert 0.0 <= edge.confidence <= 1.0, (
                    f"Fixture '{name}': edge confidence "
                    f"{edge.confidence} out of range [0, 1]"
                )
