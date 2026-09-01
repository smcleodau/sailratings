"""Citation generation for lineage graphs (DP-02-05).

Produces a UI-safe citation string from a :class:`LineageGraphV1`.
The citation is designed to be displayed in the SailRatings UI
alongside any displayed fact, so users can see at a glance where the
value came from.
"""

from __future__ import annotations

from irc_data.lineage.models import EdgeType, LineageGraphV1, NodeType


def generate_citation(graph: LineageGraphV1) -> str:
    """Generate a human-readable, UI-safe citation from a lineage graph.

    The citation includes:
    * The source name (slug) of the raw artifact.
    * The URL (truncated if very long).
    * The fetch date.
    * The extraction model and confidence (if available).
    * A note if the chain is incomplete.

    Examples
    --------
    >>> graph = LineageGraphV1(
    ...     canonical_field="tcc_snapshots.tcc",
    ...     canonical_value="0.985",
    ...     record_id=42,
    ... )
    >>> generate_citation(graph)
    'Lineage incomplete: raw artifact not found'

    A complete graph with a single source:
    ``"Source: irc-certs | https://ircrating.org/cert/GBR12345.pdf
    (fetched 2026-01-15) | Extracted by pdfplumber-v0.10.4
    (confidence: 98.5%)"``
    """
    if not graph.nodes:
        return "Lineage incomplete: no provenance nodes recorded"

    raw_nodes = graph.raw_artifact_nodes()

    if not raw_nodes:
        # No raw artifact — check if there are extracted spans
        spans = [
            n for n in graph.nodes
            if n.node_type == NodeType.EXTRACTED_SPAN.value
        ]
        if spans:
            parts = []
            for span in spans:
                src = span.source_slug or "unknown source"
                url = _truncate_url(span.url) if span.url else "URL not recorded"
                model = span.model_version or "unknown parser"
                conf = _format_confidence(span.confidence)
                parts.append(
                    f"Source: {src} | {url} | Extracted by {model}{conf}"
                )
            citation = "; ".join(parts)
        else:
            citation = "Lineage incomplete: raw artifact not found"
    else:
        parts = []
        for raw in raw_nodes:
            src = raw.source_slug or "unknown source"
            url = _truncate_url(raw.url) if raw.url else "URL not recorded"
            fetched = _format_date(raw.fetched_at) if raw.fetched_at else "date not recorded"

            # Find the extraction metadata from the span that points to this artifact
            span_info = _find_span_for_artifact(graph, raw.node_id)
            model = span_info.model_version if span_info else None
            conf = _format_confidence(
                span_info.confidence if span_info else None
            )

            model_str = f" | Extracted by {model}{conf}" if model else ""
            parts.append(
                f"Source: {src} | {url} (fetched {fetched}){model_str}"
            )

        citation = "; ".join(parts)

    # Append merged/adjudicated note if applicable
    edge_types = {e.edge_type for e in graph.edges}
    if EdgeType.MERGED_FROM.value in edge_types:
        citation += " | Value merged from multiple sources"
    if EdgeType.ADJUDICATED_FROM.value in edge_types:
        citation += " | Value adjudicated (conflict resolved)"

    if not graph.complete:
        missing = "; ".join(graph.missing_edges) if graph.missing_edges else "chain incomplete"
        citation += f" | Warning: {missing}"

    return citation


def _truncate_url(url: str, max_len: int = 80) -> str:
    """Truncate a URL for display."""
    if len(url) <= max_len:
        return url
    return url[:max_len - 3] + "..."


def _format_date(iso_str: str | None) -> str:
    """Format an ISO-8601 timestamp as a human-readable date."""
    if not iso_str:
        return "date not recorded"
    # Take just the date part
    date_part = iso_str.split("T")[0] if "T" in iso_str else iso_str[:10]
    return date_part


def _format_confidence(conf: float | None) -> str:
    """Format a confidence value as a percentage string."""
    if conf is None:
        return ""
    pct = conf * 100 if conf <= 1.0 else conf
    return f" (confidence: {pct:.1f}%)"


def _find_span_for_artifact(
    graph: LineageGraphV1, artifact_node_id: str
):
    """Find the extracted_span node that points to the given raw artifact."""
    # Find edge: span → artifact (edge_type = extracted_from)
    for edge in graph.edges:
        if edge.to_node == artifact_node_id and edge.edge_type == EdgeType.EXTRACTED_FROM.value:
            for node in graph.nodes:
                if node.node_id == edge.from_node:
                    return node
    return None
