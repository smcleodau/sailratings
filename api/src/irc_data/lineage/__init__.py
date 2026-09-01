"""Field-level lineage tracking (DP-02-05).

This package implements the Lineage API — the ability to answer
"where did every displayed fact come from?" for any canonical field
in the platform.

The core handoff / output contract is :class:`LineageGraphV1`, which
represents the complete acyclic chain from a canonical field (e.g.
``tcc_snapshots.tcc``) through any transformations to the extracted
span in the raw artifact, including model/rule version and confidence.

Public API
----------
* :class:`LineageGraphV1` — the output contract
* :class:`LineageNodeV1` — a single node in the lineage chain
* :class:`LineageEdgeV1` — a directed edge between nodes
* :class:`LineageService` — the service that builds lineage graphs
* :func:`generate_citation` — produce a UI-safe citation string
"""

from irc_data.lineage.models import (
    LineageEdgeV1,
    LineageGraphV1,
    LineageNodeV1,
    NodeType,
    EdgeType,
)
from irc_data.lineage.citation import generate_citation
from irc_data.lineage.service import LineageService

__all__ = [
    "LineageEdgeV1",
    "LineageGraphV1",
    "LineageNodeV1",
    "NodeType",
    "EdgeType",
    "LineageService",
    "generate_citation",
]
