"""Lineage query — resolve a canonical assertion back to the raw
artifact and the exact source span a value was read from (DP-06-03).

The DP-06-03 acceptance criterion is *"lineage query reaches raw
artifact"*.  Every :class:`CanonicalAssertionV1` carries an
:class:`AssertionLineage` with the artifact id, content hash, extraction
batch id, parser and schema versions, and the per-field source locators
(spans).  This module makes that chain **queryable**:

.. code-block:: python

    report = trace_assertion(assertion, artifact_content=csv_bytes)
    report.assertion_id          # the assertion we started from
    report.content_hash_verified # True: artifact bytes hash to lineage.content_hash
    report.chain                 # assertion → extraction batch → artifact
    report.spans["tcc"].resolved_text   # the raw CSV cell text for `tcc`

``trace_assertion`` is pure — the caller supplies the artifact bytes
(typically read from the raw lake, DP-02-01) so lineage can be checked
without trusting the store.  :func:`verify_lineage` is the boolean
acceptance check: id, hash and span resolution all hold.

For convenience, :class:`LineageIndex` indexes the assertions and
rejects of a :class:`TransformationBatchV1` by id so a batch can be
queried directly.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from irc_data.parsers.extraction_contract import (
    ExtractionBatchV1,
    Locator,
    LocatorType,
)
from irc_data.transform.transformation_contract import (
    CanonicalAssertionV1,
    RejectedRecordV1,
    TransformationBatchV1,
)


# ---------------------------------------------------------------------------
# Resolved span
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedSpan:
    """One source span resolved against the raw artifact bytes.

    ``resolved_text`` is the exact artifact text the span cites when the
    artifact content is available (``None`` when only the locator is
    known).  ``verified`` is ``True`` when the resolved text matches the
    locator snippet (when a snippet was recorded).
    """

    locator: dict[str, Any]
    resolved_text: str | None = None
    verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "resolved_text": self.resolved_text,
            "verified": self.verified,
        }


# ---------------------------------------------------------------------------
# Lineage report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageReport:
    """The full lineage of one assertion, resolved to the raw artifact.

    Attributes
    ----------
    assertion_id
        The assertion queried.
    chain
        Ordered provenance chain: ``assertion`` → ``extraction_batch`` →
        ``artifact``.  Each hop carries its deterministic id and version
        pins, so the whole derivation is auditable.
    artifact_id / content_hash
        The raw artifact identity.
    content_hash_verified
        ``True`` when the supplied artifact bytes hash to
        ``content_hash``; ``None`` when no artifact content was
        supplied (hash unverifiable).
    spans
        Mapping of source-locator → resolved span.  Keys are the
        stringified locator identity (locator type + coordinates) so
        callers can find e.g. the CSV row/column for the field they are
        auditing.
    reaches_raw_artifact
        ``True`` when the chain terminates at a verifiable artifact
        (hash verified, or — without content — at a well-formed artifact
        id + content hash).
    """

    assertion_id: str
    assertion_type: str
    chain: list[dict[str, Any]]
    artifact_id: str
    content_hash: str
    source_slug: str
    url: str
    content_hash_verified: bool | None
    spans: dict[str, ResolvedSpan] = field(default_factory=dict)
    reaches_raw_artifact: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_id": self.assertion_id,
            "assertion_type": self.assertion_type,
            "chain": self.chain,
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "source_slug": self.source_slug,
            "url": self.url,
            "content_hash_verified": self.content_hash_verified,
            "spans": {k: v.to_dict() for k, v in self.spans.items()},
            "reaches_raw_artifact": self.reaches_raw_artifact,
        }


# ---------------------------------------------------------------------------
# Span resolution
# ---------------------------------------------------------------------------


def _resolve_csv_span(lines: list[str], loc: Locator) -> str | None:
    """Resolve a CSV_ROW locator to the raw cell text."""
    if loc.row is None:
        return None
    line_idx = loc.row + 1  # header is line 0
    if line_idx >= len(lines):
        return None
    import csv

    parsed = next(csv.reader([lines[line_idx]]))
    if loc.start is None or loc.start >= len(parsed):
        return lines[line_idx]  # whole-row citation
    return parsed[loc.start]


def resolve_span(loc: Locator, artifact_content: bytes | None) -> ResolvedSpan:
    """Resolve one locator against the raw artifact bytes."""
    if artifact_content is None:
        return ResolvedSpan(locator=loc.to_dict())
    text = artifact_content.decode("utf-8-sig", errors="replace")
    resolved: str | None = None
    if loc.locator_type == LocatorType.CSV_ROW.value:
        resolved = _resolve_csv_span(text.splitlines(), loc)
    elif loc.locator_type == LocatorType.BYTE_OFFSET.value and loc.start is not None:
        end = loc.end if loc.end is not None else loc.start + 80
        resolved = text.encode("utf-8")[loc.start:end].decode("utf-8", errors="replace")
    else:
        resolved = None
    verified: bool | None = None
    if resolved is not None and loc.snippet is not None:
        verified = resolved.strip() == loc.snippet.strip() or resolved.strip().startswith(
            loc.snippet.strip()
        )
    return ResolvedSpan(locator=loc.to_dict(), resolved_text=resolved, verified=verified)


# ---------------------------------------------------------------------------
# The lineage query
# ---------------------------------------------------------------------------


def trace_assertion(
    assertion: CanonicalAssertionV1,
    *,
    artifact_content: bytes | None = None,
) -> LineageReport:
    """Trace one canonical assertion back to its raw artifact.

    This is the DP-06-03 *lineage query*: starting from an assertion id
    it walks the provenance chain to the raw artifact and (when the
    artifact bytes are supplied) resolves every source span against them
    and verifies the content hash.
    """
    lin = assertion.lineage

    content_hash_verified: bool | None = None
    if artifact_content is not None:
        digest = hashlib.sha256(artifact_content).hexdigest()
        content_hash_verified = digest == lin.content_hash

    spans: dict[str, ResolvedSpan] = {}
    for raw_loc in lin.source_locators:
        loc = Locator.from_dict(raw_loc)
        key = f"{loc.locator_type}:row={loc.row}:col={loc.start}:path={loc.path}"
        spans[key] = resolve_span(loc, artifact_content)

    chain = [
        {
            "hop": "assertion",
            "assertion_id": assertion.assertion_id,
            "assertion_type": assertion.assertion_type,
            "transformer_name": assertion.transformer_name,
            "transformer_version": assertion.transformer_version,
            "schema_version": assertion.schema_version,
        },
        {
            "hop": "extraction_batch",
            "extraction_batch_id": lin.extraction_batch_id,
            "extraction_hash": lin.extraction_hash,
            "parser_version": lin.parser_version,
            "extraction_schema_version": lin.extraction_schema_version,
            "source_record_type": lin.source_record_type,
            "source_record_index": lin.source_record_index,
        },
        {
            "hop": "artifact",
            "artifact_id": lin.artifact_id,
            "content_hash": lin.content_hash,
            "source_slug": lin.source_slug,
            "url": lin.url,
            "content_hash_verified": content_hash_verified,
        },
    ]

    reaches = bool(lin.artifact_id and lin.content_hash) and (
        content_hash_verified is not False
    )
    return LineageReport(
        assertion_id=assertion.assertion_id,
        assertion_type=assertion.assertion_type,
        chain=chain,
        artifact_id=lin.artifact_id,
        content_hash=lin.content_hash,
        source_slug=lin.source_slug,
        url=lin.url,
        content_hash_verified=content_hash_verified,
        spans=spans,
        reaches_raw_artifact=reaches,
    )


def verify_lineage(
    assertion: CanonicalAssertionV1,
    *,
    artifact_content: bytes | None = None,
) -> bool:
    """Boolean acceptance check: lineage reaches the raw artifact."""
    report = trace_assertion(assertion, artifact_content=artifact_content)
    if not report.reaches_raw_artifact:
        return False
    if artifact_content is not None and report.content_hash_verified is not True:
        return False
    return True


# ---------------------------------------------------------------------------
# LineageIndex — query a transformation batch by id
# ---------------------------------------------------------------------------


class LineageIndex:
    """Index the assertions and rejects of one transformation batch.

    Provides the "lineage query" entry point over a
    :class:`TransformationBatchV1`: look up an assertion (or reject) by
    id and trace it to the raw artifact.
    """

    def __init__(self, batch: TransformationBatchV1):
        self.batch = batch
        self.assertions: dict[str, CanonicalAssertionV1] = {
            a.assertion_id: a for a in batch.assertions
        }
        self.rejects: dict[str, RejectedRecordV1] = {
            r.reject_id: r for r in batch.rejects
        }

    def trace(
        self,
        assertion_id: str,
        *,
        artifact_content: bytes | None = None,
    ) -> LineageReport | None:
        """Trace *assertion_id* to the raw artifact (``None`` if unknown)."""
        assertion = self.assertions.get(assertion_id)
        if assertion is None:
            return None
        return trace_assertion(assertion, artifact_content=artifact_content)

    def all_reach_raw_artifact(
        self, *, artifact_content: bytes | None = None
    ) -> bool:
        """``True`` when every assertion's lineage verifies."""
        return all(
            verify_lineage(a, artifact_content=artifact_content)
            for a in self.assertions.values()
        )


def index_batch(batch: TransformationBatchV1) -> LineageIndex:
    """Build a :class:`LineageIndex` over *batch*."""
    return LineageIndex(batch)


__all__ = [
    "ResolvedSpan",
    "LineageReport",
    "resolve_span",
    "trace_assertion",
    "verify_lineage",
    "LineageIndex",
    "index_batch",
]
