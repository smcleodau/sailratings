"""LineageService — builds LineageGraphV1 from provenance data (DP-02-05).

The service traces a canonical field through the lineage chain:

  canonical_field → transformed_value → extracted_span → raw_artifact

It queries two sources of provenance:

1. **Explicit provenance** — rows in the ``field_provenance`` table,
   written by parsers/extractors during ingestion.  Each row is a node
   with a ``parent_id`` linking it to its upstream source.

2. **Implicit provenance** — columns on existing tables that carry
   source information (e.g. ``race_results.source``, ``source_url``,
   ``raw_data``; ``irc_certificates.source``, ``source_url``,
   ``pdf_path``).  When no explicit provenance row exists, the service
   falls back to these columns to build a best-effort chain.

When the chain is broken (a node has no upstream), the service records
the gap in ``missing_edges`` and sets ``complete = False``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.lineage.citation import generate_citation
from irc_data.lineage.models import (
    EdgeType,
    LineageEdgeV1,
    LineageGraphV1,
    LineageNodeV1,
    NodeType,
)


# ---------------------------------------------------------------------------
# Entity registry — maps canonical entities to their source-provenance columns
# ---------------------------------------------------------------------------

#: Maps an entity table to the columns that carry implicit provenance.
#: When no explicit field_provenance rows exist, the service falls back
#: to these columns to build a best-effort lineage chain.
_ENTITY_PROVENANCE: dict[str, dict[str, str]] = {
    "tcc_snapshots": {
        # TCC snapshots are derived from IRC certificates.
        "join_table": "irc_certificates",
        "join_column": "boat_id",
        "source_column": "source",
        "url_column": "source_url",
        "artifact_column": "pdf_path",
        "raw_data_column": "raw_data",
    },
    "irc_certificates": {
        "source_column": "source",
        "url_column": "source_url",
        "artifact_column": "pdf_path",
        "raw_data_column": "raw_data",
    },
    "orc_certificates": {
        "source_column": None,  # ORC certs don't have a source column
        "url_column": None,
        "artifact_column": None,
        "raw_data_column": "raw_data",
    },
    "race_results": {
        "source_column": "source",
        "url_column": "source_url",
        "artifact_column": None,
        "raw_data_column": "raw_data",
    },
    "boats": {
        "source_column": None,
        "url_column": None,
        "artifact_column": None,
        "raw_data_column": None,
    },
}

#: Default model version when none is recorded.
_DEFAULT_MODEL_VERSION = "unknown"

#: Default rule version when none is recorded.
_DEFAULT_RULE_VERSION = "unknown"


class LineageService:
    """Builds :class:`LineageGraphV1` instances from provenance data.

    Parameters
    ----------
    engine
        SQLAlchemy engine for the platform database.
    """

    def __init__(self, engine: Engine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            from irc_data.db.connection import get_engine
            self._engine = get_engine()
        return self._engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_lineage(
        self,
        entity: str,
        field: str,
        record_id: int,
    ) -> LineageGraphV1:
        """Build the lineage graph for a single canonical field.

        Parameters
        ----------
        entity
            The database table / logical entity, e.g. ``"tcc_snapshots"``.
        field
            The field name, e.g. ``"tcc"``.
        record_id
            Primary key of the canonical record.

        Returns
        -------
        LineageGraphV1
            The complete (or partial) lineage graph.  Check ``.complete``
            to see if the chain was fully traced.
        """
        canonical_field = f"{entity}.{field}"

        # Try explicit provenance first
        nodes, edges, missing = self._query_explicit_provenance(
            entity, field, record_id
        )

        if not nodes:
            # Fall back to implicit provenance from existing table columns
            nodes, edges, missing = self._build_implicit_lineage(
                entity, field, record_id
            )

        # Determine if the chain is complete
        complete = bool(nodes) and any(
            n.node_type == NodeType.RAW_ARTIFACT.value for n in nodes
        ) and not missing

        # If we have nodes but still missing edges, keep missing messages
        if not missing and not complete and nodes:
            missing.append(
                "Chain does not reach a raw artifact node"
            )

        # Get the canonical value
        canonical_value = None
        for n in nodes:
            if n.node_type == NodeType.CANONICAL_FIELD.value:
                canonical_value = n.value
                break

        graph = LineageGraphV1(
            canonical_field=canonical_field,
            canonical_value=canonical_value,
            record_id=record_id,
            nodes=nodes,
            edges=edges,
            complete=complete,
            missing_edges=missing,
        )

        # Generate citation
        graph.citation = generate_citation(graph)

        return graph

    # ------------------------------------------------------------------
    # Explicit provenance (field_provenance table)
    # ------------------------------------------------------------------

    def _query_explicit_provenance(
        self,
        entity: str,
        field: str,
        record_id: int,
    ) -> tuple[list[LineageNodeV1], list[LineageEdgeV1], list[str]]:
        """Query the ``field_provenance`` table for an explicit chain.

        Walks the parent_id chain from the canonical node to the raw
        artifact, collecting nodes and edges.
        """
        nodes: list[LineageNodeV1] = []
        edges: list[LineageEdgeV1] = []
        missing: list[str] = []

        try:
            with self.engine.connect() as conn:
                # Find the canonical-field node
                rows = conn.execute(text("""
                    SELECT id, node_type, entity, field, value, record_id,
                           source_slug, content_hash, url, fetched_at,
                           model_version, rule_version, confidence,
                           span_text, span_start, span_end, observed_at,
                           parent_id, edge_type
                    FROM field_provenance
                    WHERE entity = :entity
                      AND field = :field
                      AND record_id = :record_id
                      AND node_type = 'canonical_field'
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {
                    "entity": entity,
                    "field": field,
                    "record_id": record_id,
                }).fetchall()

                if not rows:
                    return nodes, edges, missing

                # Walk the parent chain
                visited: set[int] = set()
                current_row = rows[0]
                node_counter = 0

                while current_row is not None:
                    row_id = current_row[0]
                    if row_id in visited:
                        # Cycle detected — break to maintain acyclic guarantee
                        missing.append(
                            f"Cycle detected at provenance node {row_id} "
                            f"(entity={current_row[2]}, field={current_row[3]})"
                        )
                        break
                    visited.add(row_id)

                    node_id = f"node-{node_counter}"
                    node = _row_to_node(current_row, node_id)
                    nodes.append(node)

                    parent_id = current_row[17]  # parent_id
                    edge_type = current_row[18]  # edge_type

                    if parent_id is not None:
                        # Fetch the parent (upstream) node
                        parent_rows = conn.execute(text("""
                            SELECT id, node_type, entity, field, value, record_id,
                                   source_slug, content_hash, url, fetched_at,
                                   model_version, rule_version, confidence,
                                   span_text, span_start, span_end, observed_at,
                                   parent_id, edge_type
                            FROM field_provenance
                            WHERE id = :parent_id
                        """), {"parent_id": parent_id}).fetchall()

                        if parent_rows:
                            parent_node_id = f"node-{node_counter + 1}"
                            parent_node = _row_to_node(parent_rows[0], parent_node_id)
                            nodes.append(parent_node)

                            # Edge: current → parent (downstream → upstream)
                            edge = LineageEdgeV1(
                                from_node=node_id,
                                to_node=parent_node_id,
                                edge_type=edge_type or EdgeType.DERIVED_FROM.value,
                                confidence=node.confidence,
                            )
                            edges.append(edge)

                            node_counter += 2
                            current_row = parent_rows[0]
                        else:
                            missing.append(
                                f"Parent provenance node {parent_id} not found "
                                f"(dangling parent_id on node {row_id})"
                            )
                            break
                    else:
                        # No parent — this should be the raw artifact (leaf)
                        if current_row[1] != NodeType.RAW_ARTIFACT.value:
                            missing.append(
                                f"Chain ends at node {row_id} "
                                f"(type={current_row[1]}) without reaching "
                                f"a raw artifact"
                            )
                        break

        except Exception as exc:
            # Table might not exist yet — fall back to implicit
            missing.append(f"field_provenance query failed: {exc}")

        return nodes, edges, missing

    # ------------------------------------------------------------------
    # Implicit provenance (existing table columns)
    # ------------------------------------------------------------------

    def _build_implicit_lineage(
        self,
        entity: str,
        field: str,
        record_id: int,
    ) -> tuple[list[LineageNodeV1], list[LineageEdgeV1], list[str]]:
        """Build a best-effort lineage chain from existing table columns.

        When no explicit ``field_provenance`` rows exist, we look at the
        canonical record's ``source``, ``source_url`` and related columns
        to construct a two-node chain: canonical_field → raw_artifact.
        """
        nodes: list[LineageNodeV1] = []
        edges: list[LineageEdgeV1] = []
        missing: list[str] = []

        prov = _ENTITY_PROVENANCE.get(entity)
        if prov is None:
            missing.append(
                f"No provenance configuration for entity '{entity}'"
            )
            return nodes, edges, missing

        # Fetch the canonical record
        record = self._fetch_record(entity, record_id)
        if record is None:
            missing.append(
                f"Record {record_id} not found in table '{entity}'"
            )
            return nodes, edges, missing

        canonical_value = str(record.get(field, "")) if record.get(field) is not None else None

        # Build canonical node
        canonical_node = LineageNodeV1(
            node_id="node-0",
            node_type=NodeType.CANONICAL_FIELD.value,
            entity=entity,
            field=field,
            value=canonical_value,
            record_id=record_id,
            rule_version=_DEFAULT_RULE_VERSION,
        )
        nodes.append(canonical_node)

        # Try to find source information
        source_slug = record.get(prov.get("source_column") or "__missing__")
        source_url = record.get(prov.get("url_column") or "__missing__")
        artifact_path = record.get(prov.get("artifact_column") or "__missing__")
        raw_data = record.get(prov.get("raw_data_column") or "__missing__")

        # If this entity has a join_table, follow it (e.g. tcc_snapshots → irc_certificates)
        join_table = prov.get("join_table")
        join_column = prov.get("join_column")
        if join_table and join_column and not source_url:
            join_id = record.get(join_column)
            if join_id is not None:
                join_record = self._fetch_record(join_table, join_id)
                if join_record:
                    source_slug = join_record.get("source") or source_slug
                    source_url = join_record.get("source_url") or source_url
                    artifact_path = join_record.get("pdf_path") or artifact_path
                    raw_data = join_record.get("raw_data") or raw_data
                else:
                    missing.append(
                        f"Join record not found: {join_table}.{join_column}={join_id}"
                    )

        # Determine if we can build a raw artifact node
        if source_url or source_slug or artifact_path:
            raw_node = LineageNodeV1(
                node_id="node-1",
                node_type=NodeType.RAW_ARTIFACT.value,
                entity=join_table or entity,
                source_slug=source_slug,
                url=source_url,
                content_hash=_extract_hash(raw_data) if raw_data else None,
                fetched_at=None,  # Not available in implicit mode
                model_version=_DEFAULT_MODEL_VERSION,
            )
            nodes.append(raw_node)

            edges.append(LineageEdgeV1(
                from_node="node-0",
                to_node="node-1",
                edge_type=EdgeType.EXTRACTED_FROM.value,
                confidence=None,  # Unknown in implicit mode
            ))

            # Try to add an intermediate extracted_span node
            if raw_data and isinstance(raw_data, dict):
                field_key = field
                if field_key in raw_data:
                    span_text = str(raw_data[field_key])
                    span_node = LineageNodeV1(
                        node_id="node-span",
                        node_type=NodeType.EXTRACTED_SPAN.value,
                        entity=join_table or entity,
                        field=field,
                        value=span_text,
                        span_text=span_text,
                        model_version=_DEFAULT_MODEL_VERSION,
                    )
                    # Insert between canonical and raw
                    nodes.insert(1, span_node)
                    edges[0] = LineageEdgeV1(
                        from_node="node-0",
                        to_node="node-span",
                        edge_type=EdgeType.DERIVED_FROM.value,
                    )
                    edges.append(LineageEdgeV1(
                        from_node="node-span",
                        to_node="node-1",
                        edge_type=EdgeType.EXTRACTED_FROM.value,
                    ))
        else:
            missing.append(
                f"No source provenance columns found on '{entity}' record "
                f"(source_url, source, or artifact path all null)"
            )

        return nodes, edges, missing

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_record(self, table: str, record_id: int) -> dict[str, Any] | None:
        """Fetch a single record by id from the given table."""
        # Whitelist table names to prevent SQL injection
        allowed = {
            "tcc_snapshots", "irc_certificates", "orc_certificates",
            "race_results", "boats", "event_entries",
        }
        if table not in allowed:
            return None

        try:
            with self.engine.connect() as conn:
                result = conn.execute(
                    text(f"SELECT * FROM {table} WHERE id = :rid"),
                    {"rid": record_id},
                )
                row = result.first()
                if row is None:
                    return None
                return dict(row._mapping)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Row → Node converter
# ---------------------------------------------------------------------------


def _row_to_node(row: Any, node_id: str) -> LineageNodeV1:
    """Convert a SQLAlchemy row (tuple) to a LineageNodeV1."""
    fetched_at = row[9]  # fetched_at (datetime or string or None)
    observed_at = row[16]  # observed_at

    # Handle both datetime objects and ISO-8601 strings
    if fetched_at is not None and not isinstance(fetched_at, str):
        fetched_at = fetched_at.isoformat()
    if observed_at is not None and not isinstance(observed_at, str):
        observed_at = observed_at.isoformat()

    return LineageNodeV1(
        node_id=node_id,
        node_type=row[1],  # node_type
        entity=row[2] or "",  # entity
        field=row[3],  # field
        value=str(row[4]) if row[4] is not None else None,  # value
        record_id=row[5],  # record_id
        source_slug=row[6],  # source_slug
        content_hash=row[7],  # content_hash
        url=row[8],  # url
        fetched_at=fetched_at if fetched_at else None,
        model_version=row[10],  # model_version
        rule_version=row[11],  # rule_version
        confidence=float(row[12]) if row[12] is not None else None,  # confidence
        span_text=row[13],  # span_text
        span_start=row[14],  # span_start
        span_end=row[15],  # span_end
        observed_at=observed_at if observed_at else None,
    )


def _extract_hash(raw_data: Any) -> str | None:
    """Try to extract a content hash from a raw_data dict."""
    if not isinstance(raw_data, dict):
        return None
    return raw_data.get("content_hash") or raw_data.get("sha256")
