"""LineageGraphV1 — the field-level lineage handoff / output contract (DP-02-05).

This module defines the core data structures for answering
"where did every displayed fact come from?"

The lineage chain traces a **canonical field** (the value the user sees,
e.g. ``tcc_snapshots.tcc = 0.985``) through any **transformations** (e.g.
raw ``"0.9850"`` → normalised ``0.985``) to the **extracted span** (the
exact text snippet and character offsets in the source document) and
finally to the **raw artifact** (the fetched ``RawArtifactV1`` / URL /
content hash / source slug / fetch timestamp).

Each link in the chain carries:
* **model_version** — the parser or LLM model that extracted the value.
* **rule_version** — the normalisation rule that transformed it.
* **confidence** — extraction confidence (0.0–1.0).

The graph is guaranteed to be **acyclic**.  If any edge is missing (e.g.
the raw artifact has been purged, or a parser didn't record provenance),
the graph's ``missing_edges`` list explicitly reports what is absent
and ``complete`` is ``False``.

Serialisation
-------------
All dataclasses support JSON round-trip (``to_dict`` / ``from_dict`` /
``to_json`` / ``from_json``) so they can cross Temporal activity
boundaries, be returned from API endpoints, or be persisted alongside
audit records.
"""

from __future__ import annotations

import enum
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------


class NodeType(str, enum.Enum):
    """Type of a node in the lineage chain.

    ``CANONICAL_FIELD``
        The displayed fact — the value the user sees in the UI.
        Example: ``tcc_snapshots.tcc = 0.985``.

    ``TRANSFORMED_VALUE``
        An intermediate value produced by a normalisation or
        transformation rule.  Example: raw ``"0.9850"`` → ``0.985``.

    ``EXTRACTED_SPAN``
        The exact text snippet extracted from the raw artifact,
        including character offsets.  Example: the substring
        ``"TCC: 0.985"`` at offset 1427–1438 in a PDF.

    ``RAW_ARTIFACT``
        The fetched raw artifact — URL, content hash, source slug,
        fetch timestamp, policy version.  This is the leaf of the
        chain (the origin of all data).
    """

    CANONICAL_FIELD = "canonical_field"
    TRANSFORMED_VALUE = "transformed_value"
    EXTRACTED_SPAN = "extracted_span"
    RAW_ARTIFACT = "raw_artifact"


class EdgeType(str, enum.Enum):
    """Type of a directed edge in the lineage chain.

    Edges point **from** the downstream (canonical) node **to** the
    upstream (raw) node — i.e. "this value came from that source".

    ``TRANSFORMED_TO``
        canonical_field → transformed_value.
        The canonical value was produced by transforming an
        intermediate value.

    ``DERIVED_FROM``
        transformed_value → extracted_span (or
        canonical_field → extracted_span when there is no
        intermediate transformation).
        The value was derived from a span in the source document.

    ``EXTRACTED_FROM``
        extracted_span → raw_artifact.
        The span was extracted from this specific raw artifact.

    ``MERGED_FROM``
        canonical_field → {multiple upstream nodes}.
        The canonical value was produced by merging multiple
        sources (e.g. combining IRC cert data with ORC data).

    ``ADJUDICATED_FROM``
        canonical_field → {conflicting upstream nodes}.
        The canonical value was chosen by an adjudication rule
        that resolved a conflict between disagreeing sources.
    """

    TRANSFORMED_TO = "transformed_to"
    DERIVED_FROM = "derived_from"
    EXTRACTED_FROM = "extracted_from"
    MERGED_FROM = "merged_from"
    ADJUDICATED_FROM = "adjudicated_from"


# ---------------------------------------------------------------------------
# LineageNodeV1
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LineageNodeV1:
    """A single node in the lineage chain.

    Every node has a ``node_id`` (unique within the graph), a ``node_type``,
    and the entity/field it represents.  Fields that are not applicable
    to a given node type are ``None``.

    Fields
    ------
    node_id
        Unique identifier within the graph (e.g. ``"node-0"``).
    node_type
        :class:`NodeType` — what kind of node this is.
    entity
        The database table or logical entity, e.g. ``"tcc_snapshots"``.
    field
        The field name, e.g. ``"tcc"``.  ``None`` for raw_artifact nodes.
    value
        The value at this node, as a string representation.
    record_id
        Primary key of the DB row this node refers to (if applicable).
    source_slug
        The ``data_sources.slug`` that produced this data.
    content_hash
        SHA-256 hex of the raw artifact (raw_artifact nodes only).
    url
        The source URL the artifact was fetched from.
    fetched_at
        ISO-8601 timestamp of the fetch.
    model_version
        Version of the parser/LLM that extracted the value.
    rule_version
        Version of the normalisation rule that transformed it.
    confidence
        Extraction confidence, 0.0–1.0.
    span_text
        The extracted text snippet (extracted_span nodes only).
    span_start
        Character offset of the span start in the raw artifact.
    span_end
        Character offset of the span end in the raw artifact.
    observed_at
        When this node was recorded (ISO-8601).
    """

    node_id: str
    node_type: str
    entity: str = ""
    field: str | None = None
    value: str | None = None
    record_id: int | None = None
    source_slug: str | None = None
    content_hash: str | None = None
    url: str | None = None
    fetched_at: str | None = None
    model_version: str | None = None
    rule_version: str | None = None
    confidence: float | None = None
    span_text: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    observed_at: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at is None:
            self.observed_at = _now_iso()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def is_raw_artifact(self) -> bool:
        """True if this node is a raw artifact (leaf of the chain)."""
        return self.node_type == NodeType.RAW_ARTIFACT.value

    @property
    def is_canonical(self) -> bool:
        """True if this node is the displayed canonical field."""
        return self.node_type == NodeType.CANONICAL_FIELD.value

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "entity": self.entity,
            "field": self.field,
            "value": self.value,
            "record_id": self.record_id,
            "source_slug": self.source_slug,
            "content_hash": self.content_hash,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "model_version": self.model_version,
            "rule_version": self.rule_version,
            "confidence": self.confidence,
            "span_text": self.span_text,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "observed_at": self.observed_at,
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineageNodeV1:
        return cls(
            node_id=d["node_id"],
            node_type=d["node_type"],
            entity=d.get("entity", ""),
            field=d.get("field"),
            value=d.get("value"),
            record_id=d.get("record_id"),
            source_slug=d.get("source_slug"),
            content_hash=d.get("content_hash"),
            url=d.get("url"),
            fetched_at=d.get("fetched_at"),
            model_version=d.get("model_version"),
            rule_version=d.get("rule_version"),
            confidence=d.get("confidence"),
            span_text=d.get("span_text"),
            span_start=d.get("span_start"),
            span_end=d.get("span_end"),
            observed_at=d.get("observed_at"),
            policy_version=d.get("policy_version"),
        )


# ---------------------------------------------------------------------------
# LineageEdgeV1
# ---------------------------------------------------------------------------


@dataclass
class LineageEdgeV1:
    """A directed edge in the lineage chain.

    Edges point from downstream (canonical) to upstream (raw):
    ``from_node`` is the consumer, ``to_node`` is the source.

    Fields
    ------
    from_node
        The ``node_id`` of the downstream (consuming) node.
    to_node
        The ``node_id`` of the upstream (source) node.
    edge_type
        :class:`EdgeType` — the relationship between the nodes.
    confidence
        Confidence of this particular edge (0.0–1.0).  ``None`` when
        the edge is deterministic (no uncertainty).
    """

    from_node: str
    to_node: str
    edge_type: str
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node": self.from_node,
            "to_node": self.to_node,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineageEdgeV1:
        return cls(
            from_node=d["from_node"],
            to_node=d["to_node"],
            edge_type=d["edge_type"],
            confidence=d.get("confidence"),
        )


# ---------------------------------------------------------------------------
# LineageGraphV1 — the handoff / output contract
# ---------------------------------------------------------------------------


@dataclass
class LineageGraphV1:
    """DP-02-05 handoff / output contract — field-level lineage graph.

    Represents the complete acyclic chain from a canonical field through
    transformations to the extracted span and raw artifact.

    When ``complete`` is ``True``, every node in the chain can be traced
    back to a :class:`RawArtifactV1` leaf.  When ``complete`` is ``False``,
    ``missing_edges`` contains a human-readable description of each gap.

    Fields
    ------
    canonical_field
        The entity.field that was queried, e.g. ``"tcc_snapshots.tcc"``.
    canonical_value
        The displayed value (string representation).
    record_id
        Primary key of the canonical record.
    nodes
        All nodes in the lineage chain, ordered from canonical (first)
        to raw artifact (last).
    edges
        Directed edges connecting the nodes.
    citation
        UI-safe citation string, generated from the graph.
    complete
        ``True`` iff the chain is fully traced to a raw artifact.
    missing_edges
        List of human-readable descriptions of missing edges.  Empty
        when ``complete`` is ``True``.
    schema_version
        Contract version (currently ``"1"``).
    generated_at
        ISO-8601 timestamp of when the graph was built.
    """

    canonical_field: str
    canonical_value: str | None
    record_id: int | None
    nodes: list[LineageNodeV1] = field(default_factory=list)
    edges: list[LineageEdgeV1] = field(default_factory=list)
    citation: str = ""
    complete: bool = False
    missing_edges: list[str] = field(default_factory=list)
    schema_version: str = "1"
    generated_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Graph validation
    # ------------------------------------------------------------------

    def is_acyclic(self) -> bool:
        """Return ``True`` iff the graph contains no cycles.

        Uses a DFS-based cycle detection on the edge set.
        """
        # Build adjacency list
        adj: dict[str, list[str]] = {}
        for edge in self.edges:
            adj.setdefault(edge.from_node, []).append(edge.to_node)

        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {}

        def dfs(node: str) -> bool:
            color[node] = GRAY
            for neighbour in adj.get(node, []):
                c = color.get(neighbour, WHITE)
                if c == GRAY:
                    return False  # back edge → cycle
                if c == WHITE and not dfs(neighbour):
                    return False
            color[node] = BLACK
            return True

        all_nodes = {n.node_id for n in self.nodes}
        for node_id in all_nodes:
            if color.get(node_id, WHITE) == WHITE:
                if not dfs(node_id):
                    return False
        return True

    def has_raw_artifact(self) -> bool:
        """Return ``True`` iff at least one raw artifact node is present."""
        return any(
            n.node_type == NodeType.RAW_ARTIFACT.value for n in self.nodes
        )

    def raw_artifact_nodes(self) -> list[LineageNodeV1]:
        """Return all raw-artifact (leaf) nodes in the graph."""
        return [
            n for n in self.nodes
            if n.node_type == NodeType.RAW_ARTIFACT.value
        ]

    def canonical_node(self) -> LineageNodeV1 | None:
        """Return the canonical-field node, or ``None`` if absent."""
        for n in self.nodes:
            if n.node_type == NodeType.CANONICAL_FIELD.value:
                return n
        return None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_field": self.canonical_field,
            "canonical_value": self.canonical_value,
            "record_id": self.record_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "citation": self.citation,
            "complete": self.complete,
            "missing_edges": list(self.missing_edges),
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineageGraphV1:
        return cls(
            canonical_field=d["canonical_field"],
            canonical_value=d.get("canonical_value"),
            record_id=d.get("record_id"),
            nodes=[LineageNodeV1.from_dict(n) for n in d.get("nodes", [])],
            edges=[LineageEdgeV1.from_dict(e) for e in d.get("edges", [])],
            citation=d.get("citation", ""),
            complete=d.get("complete", False),
            missing_edges=list(d.get("missing_edges", [])),
            schema_version=d.get("schema_version", "1"),
            generated_at=d.get("generated_at", _now_iso()),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> LineageGraphV1:
        return cls.from_dict(json.loads(s))
