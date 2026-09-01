"""Golden lineage fixtures for DP-02-05 verification.

These fixtures cover the four lineage patterns required by the
acceptance criteria:

1. **Direct** — a value copied directly from the raw artifact to the
   canonical field with no transformation (e.g. sail number).

2. **Transformed** — a value that was normalised/transformed before
   becoming canonical (e.g. raw ``"0.9850"`` → canonical ``0.985``).

3. **Merged** — a canonical value produced by merging multiple sources
   (e.g. combining IRC certificate data with ORC certificate data).

4. **Adjudicated** — a canonical value chosen by an adjudication rule
   that resolved a conflict between disagreeing sources.

Each fixture returns a fully-built :class:`LineageGraphV1` that the
tests verify.
"""

from __future__ import annotations

from irc_data.lineage.models import (
    EdgeType,
    LineageEdgeV1,
    LineageGraphV1,
    LineageNodeV1,
    NodeType,
)


# ---------------------------------------------------------------------------
# 1. Direct lineage — canonical ← raw_artifact (no transformation)
# ---------------------------------------------------------------------------


def direct_lineage() -> LineageGraphV1:
    """A direct lineage chain: canonical_field ← raw_artifact.

    The value was copied verbatim from the source document — no
    transformation, no intermediate node.  The canonical field IS the
    extracted value.

    Example: a sail number ``"AUS 123"`` extracted from an IRC certificate
    PDF and stored directly in ``boats.sail_number``.
    """
    canonical = LineageNodeV1(
        node_id="node-0",
        node_type=NodeType.CANONICAL_FIELD.value,
        entity="boats",
        field="sail_number",
        value="AUS 123",
        record_id=1,
        rule_version="copy-v1",
    )
    span = LineageNodeV1(
        node_id="node-1",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="irc_certificates",
        field="sail_number",
        value="AUS 123",
        source_slug="irc-certs",
        span_text="AUS 123",
        span_start=245,
        span_end=251,
        model_version="pdfplumber-v0.10.4",
        confidence=0.99,
    )
    raw = LineageNodeV1(
        node_id="node-2",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="irc_certificates",
        source_slug="irc-certs",
        url="https://ircrating.org/pdfdirectory/cert/GBR12345.pdf",
        content_hash="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        fetched_at="2026-01-15T02:00:00+00:00",
        policy_version="interim-v0",
    )

    edges = [
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1",
            edge_type=EdgeType.DERIVED_FROM.value,
            confidence=0.99,
        ),
        LineageEdgeV1(
            from_node="node-1",
            to_node="node-2",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=0.99,
        ),
    ]

    return LineageGraphV1(
        canonical_field="boats.sail_number",
        canonical_value="AUS 123",
        record_id=1,
        nodes=[canonical, span, raw],
        edges=edges,
        complete=True,
        missing_edges=[],
    )


# ---------------------------------------------------------------------------
# 2. Transformed lineage — canonical ← transformed ← span ← raw
# ---------------------------------------------------------------------------


def transformed_lineage() -> LineageGraphV1:
    """A transformed lineage chain with an intermediate transformation.

    The raw text ``"TCC: 0.9850"`` was extracted from a PDF, then
    normalised by a rule (``tcc-normalise-v2``) that stripped the
    label and trailing zero to produce ``0.985``.

    Example: ``tcc_snapshots.tcc = 0.985`` derived from
    ``"TCC: 0.9850"`` in an IRC certificate PDF.
    """
    canonical = LineageNodeV1(
        node_id="node-0",
        node_type=NodeType.CANONICAL_FIELD.value,
        entity="tcc_snapshots",
        field="tcc",
        value="0.985",
        record_id=42,
        rule_version="tcc-normalise-v2",
    )
    transformed = LineageNodeV1(
        node_id="node-1",
        node_type=NodeType.TRANSFORMED_VALUE.value,
        entity="tcc_snapshots",
        field="tcc",
        value="0.9850",
        rule_version="tcc-normalise-v2",
        model_version="pdfplumber-v0.10.4",
        confidence=0.95,
    )
    span = LineageNodeV1(
        node_id="node-2",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="irc_certificates",
        field="tcc",
        value="TCC: 0.9850",
        source_slug="irc-certs",
        span_text="TCC: 0.9850",
        span_start=1427,
        span_end=1439,
        model_version="pdfplumber-v0.10.4",
        confidence=0.95,
    )
    raw = LineageNodeV1(
        node_id="node-3",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="irc_certificates",
        source_slug="irc-certs",
        url="https://ircrating.org/pdfdirectory/cert/GBR12345.pdf",
        content_hash="b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3",
        fetched_at="2026-01-15T02:00:00+00:00",
        policy_version="interim-v0",
    )

    edges = [
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1",
            edge_type=EdgeType.TRANSFORMED_TO.value,
            confidence=0.95,
        ),
        LineageEdgeV1(
            from_node="node-1",
            to_node="node-2",
            edge_type=EdgeType.DERIVED_FROM.value,
            confidence=0.95,
        ),
        LineageEdgeV1(
            from_node="node-2",
            to_node="node-3",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=0.95,
        ),
    ]

    return LineageGraphV1(
        canonical_field="tcc_snapshots.tcc",
        canonical_value="0.985",
        record_id=42,
        nodes=[canonical, transformed, span, raw],
        edges=edges,
        complete=True,
        missing_edges=[],
    )


# ---------------------------------------------------------------------------
# 3. Merged lineage — canonical ← {source A, source B}
# ---------------------------------------------------------------------------


def merged_lineage() -> LineageGraphV1:
    """A merged lineage chain from two sources.

    The canonical ``boats.loa`` (length overall) was produced by merging
    data from an IRC certificate PDF and an ORC certificate JSON API.
    Both sources contributed a value, and the canonical field stores
    the IRC value (higher priority).

    The graph has two branches converging on the canonical node.
    """
    canonical = LineageNodeV1(
        node_id="node-0",
        node_type=NodeType.CANONICAL_FIELD.value,
        entity="boats",
        field="loa",
        value="10.52",
        record_id=7,
        rule_version="merge-irc-priority-v1",
    )

    # Branch A: IRC certificate
    span_a = LineageNodeV1(
        node_id="node-1a",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="irc_certificates",
        field="lh",
        value="10.52",
        source_slug="irc-certs",
        span_text="LH: 10.52m",
        span_start=310,
        span_end=321,
        model_version="pdfplumber-v0.10.4",
        confidence=0.97,
    )
    raw_a = LineageNodeV1(
        node_id="node-2a",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="irc_certificates",
        source_slug="irc-certs",
        url="https://ircrating.org/pdfdirectory/cert/GBR12345.pdf",
        content_hash="c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
        fetched_at="2026-01-15T02:00:00+00:00",
        policy_version="interim-v0",
    )

    # Branch B: ORC certificate
    span_b = LineageNodeV1(
        node_id="node-1b",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="orc_certificates",
        field="loa",
        value="10.52",
        source_slug="orc",
        span_text='"loa": 10.52',
        span_start=1842,
        span_end=1856,
        model_version="json-parser-v1.2",
        confidence=1.0,
    )
    raw_b = LineageNodeV1(
        node_id="node-2b",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="orc_certificates",
        source_slug="orc",
        url="https://data.orc.org/public/WPub.dll/ERT/JSON?ref=GBR12345",
        content_hash="d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
        fetched_at="2026-01-16T02:00:00+00:00",
        policy_version="interim-v0",
    )

    edges = [
        # Branch A
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1a",
            edge_type=EdgeType.MERGED_FROM.value,
            confidence=0.97,
        ),
        LineageEdgeV1(
            from_node="node-1a",
            to_node="node-2a",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=0.97,
        ),
        # Branch B
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1b",
            edge_type=EdgeType.MERGED_FROM.value,
            confidence=1.0,
        ),
        LineageEdgeV1(
            from_node="node-1b",
            to_node="node-2b",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=1.0,
        ),
    ]

    return LineageGraphV1(
        canonical_field="boats.loa",
        canonical_value="10.52",
        record_id=7,
        nodes=[canonical, span_a, raw_a, span_b, raw_b],
        edges=edges,
        complete=True,
        missing_edges=[],
    )


# ---------------------------------------------------------------------------
# 4. Adjudicated lineage — canonical ← {source A (chosen), source B (rejected)}
# ---------------------------------------------------------------------------


def adjudicated_lineage() -> LineageGraphV1:
    """An adjudicated lineage chain resolving a conflict.

    Two sources disagreed on the value of ``boats.displacement_kg``:
    the IRC certificate says ``3200`` and the ORC certificate says
    ``3180``.  An adjudication rule (``adjudicate-irc-priority-v1``)
    chose the IRC value because IRC certificates take precedence for
    displacement.

    The graph has two branches converging on the canonical node,
    with the ``adjudicated_from`` edge type indicating conflict
    resolution.
    """
    canonical = LineageNodeV1(
        node_id="node-0",
        node_type=NodeType.CANONICAL_FIELD.value,
        entity="boats",
        field="displacement_kg",
        value="3200",
        record_id=7,
        rule_version="adjudicate-irc-priority-v1",
        confidence=0.90,
    )

    # Branch A: IRC certificate (chosen)
    span_a = LineageNodeV1(
        node_id="node-1a",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="irc_certificates",
        field="displacement_kg",
        value="3200",
        source_slug="irc-certs",
        span_text="Displacement: 3200 kg",
        span_start=560,
        span_end=580,
        model_version="pdfplumber-v0.10.4",
        confidence=0.92,
    )
    raw_a = LineageNodeV1(
        node_id="node-2a",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="irc_certificates",
        source_slug="irc-certs",
        url="https://ircrating.org/pdfdirectory/cert/GBR12345.pdf",
        content_hash="e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6",
        fetched_at="2026-01-15T02:00:00+00:00",
        policy_version="interim-v0",
    )

    # Branch B: ORC certificate (rejected)
    span_b = LineageNodeV1(
        node_id="node-1b",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="orc_certificates",
        field="displacement",
        value="3180",
        source_slug="orc",
        span_text='"displacement": 3180',
        span_start=2104,
        span_end=2126,
        model_version="json-parser-v1.2",
        confidence=1.0,
    )
    raw_b = LineageNodeV1(
        node_id="node-2b",
        node_type=NodeType.RAW_ARTIFACT.value,
        entity="orc_certificates",
        source_slug="orc",
        url="https://data.orc.org/public/WPub.dll/ERT/JSON?ref=GBR12345",
        content_hash="f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1",
        fetched_at="2026-01-16T02:00:00+00:00",
        policy_version="interim-v0",
    )

    edges = [
        # Branch A (chosen by adjudication)
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1a",
            edge_type=EdgeType.ADJUDICATED_FROM.value,
            confidence=0.90,
        ),
        LineageEdgeV1(
            from_node="node-1a",
            to_node="node-2a",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=0.92,
        ),
        # Branch B (rejected but still in the graph for auditability)
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1b",
            edge_type=EdgeType.ADJUDICATED_FROM.value,
            confidence=0.10,  # Low confidence — rejected
        ),
        LineageEdgeV1(
            from_node="node-1b",
            to_node="node-2b",
            edge_type=EdgeType.EXTRACTED_FROM.value,
            confidence=1.0,
        ),
    ]

    return LineageGraphV1(
        canonical_field="boats.displacement_kg",
        canonical_value="3200",
        record_id=7,
        nodes=[canonical, span_a, raw_a, span_b, raw_b],
        edges=edges,
        complete=True,
        missing_edges=[],
    )


# ---------------------------------------------------------------------------
# 5. Incomplete lineage — chain with a missing edge
# ---------------------------------------------------------------------------


def incomplete_lineage() -> LineageGraphV1:
    """A lineage chain with a missing edge (incomplete).

    The canonical field has a transformed value and an extracted span,
    but the raw artifact has been purged (no content hash / URL).  The
    ``missing_edges`` list explicitly reports the gap.
    """
    canonical = LineageNodeV1(
        node_id="node-0",
        node_type=NodeType.CANONICAL_FIELD.value,
        entity="tcc_snapshots",
        field="tcc",
        value="0.920",
        record_id=99,
        rule_version="tcc-normalise-v2",
    )
    transformed = LineageNodeV1(
        node_id="node-1",
        node_type=NodeType.TRANSFORMED_VALUE.value,
        entity="tcc_snapshots",
        field="tcc",
        value="0.9200",
        rule_version="tcc-normalise-v2",
        model_version="pdfplumber-v0.10.4",
        confidence=0.88,
    )
    span = LineageNodeV1(
        node_id="node-2",
        node_type=NodeType.EXTRACTED_SPAN.value,
        entity="irc_certificates",
        field="tcc",
        value="TCC: 0.9200",
        source_slug="irc-certs",
        span_text="TCC: 0.9200",
        span_start=892,
        span_end=905,
        model_version="pdfplumber-v0.10.4",
        confidence=0.88,
    )
    # No raw artifact node — it was purged

    edges = [
        LineageEdgeV1(
            from_node="node-0",
            to_node="node-1",
            edge_type=EdgeType.TRANSFORMED_TO.value,
            confidence=0.88,
        ),
        LineageEdgeV1(
            from_node="node-1",
            to_node="node-2",
            edge_type=EdgeType.DERIVED_FROM.value,
            confidence=0.88,
        ),
        # No edge to a raw artifact — missing!
    ]

    return LineageGraphV1(
        canonical_field="tcc_snapshots.tcc",
        canonical_value="0.920",
        record_id=99,
        nodes=[canonical, transformed, span],
        edges=edges,
        complete=False,
        missing_edges=[
            "Extracted span node-2 has no raw artifact (artifact purged or not recorded)",
        ],
    )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

ALL_GOLDEN_FIXTURES = [
    ("direct", direct_lineage),
    ("transformed", transformed_lineage),
    ("merged", merged_lineage),
    ("adjudicated", adjudicated_lineage),
    ("incomplete", incomplete_lineage),
]
