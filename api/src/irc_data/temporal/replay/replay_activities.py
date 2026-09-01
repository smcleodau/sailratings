"""Temporal activities for replay / backfill (DP-02-04 / SPEC-013).

These activities implement the atomic operations that the
:class:`ReplayWorkflow` orchestrates:

* :func:`select_artifacts_activity` — query published artifacts by
  source / time / version.
* :func:`run_parser_activity` — run the new parser on a batch of
  artifacts into an isolated store (never touching published data).
* :func:`compare_batches_activity` — diff old vs new parsed outputs.
* :func:`promote_batch_activity` — explicitly promote a batch; old
  outputs are retained.

Each activity is self-contained and can be tested in isolation
without Temporal.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Callable

from temporalio import activity

from irc_data.temporal.replay.contracts import (
    ArtifactFilter,
    BatchStatus,
    ComparisonResult,
    PublicationReceiptV1,
    ReplayPlanV1,
)
from irc_data.temporal.replay.replay_store import (
    compare_batches,
    count_batch_artifacts,
    create_or_get_batch,
    get_batch,
    init_replay_tables,
    promote_batch,
    select_artifacts,
    store_parsed_output,
    update_batch_status,
)


# ---------------------------------------------------------------------------
# Activity: init tables
# ---------------------------------------------------------------------------


@activity.defn
async def init_replay_tables_activity(database_url: str = "") -> bool:
    """Ensure the replay tables exist."""
    from irc_data.db.connection import get_engine

    engine = get_engine(database_url) if database_url else get_engine()
    init_replay_tables(engine)
    return True


# ---------------------------------------------------------------------------
# Activity: create or get batch (idempotent)
# ---------------------------------------------------------------------------


@activity.defn
async def create_batch_activity(plan_dict: dict[str, Any]) -> dict[str, Any]:
    """Idempotently create a replay batch or return the existing one.

    This is the idempotency anchor.  If a batch with the same
    ``plan_id`` exists, it is returned with its current status.
    """
    from irc_data.db.connection import get_engine

    plan = ReplayPlanV1.from_dict(plan_dict)
    engine = get_engine()
    batch = create_or_get_batch(engine, plan)
    activity.logger.info(
        f"Replay batch: id={batch['id']}, plan_id={batch['plan_id']}, "
        f"status={batch['status']}"
    )
    return batch


# ---------------------------------------------------------------------------
# Activity: select artifacts
# ---------------------------------------------------------------------------


@activity.defn
async def select_artifacts_activity(
    plan_dict: dict[str, Any],
    batch_id: int,
    fetch_fn_name: str | None = None,
) -> list[dict[str, Any]]:
    """Select published artifacts matching the plan's filter.

    Returns a list of artifact dicts.  Each dict has ``artifact_url``,
    ``content_hash``, and optionally ``parsed_output`` and
    ``parser_version``.

    If ``fetch_fn_name`` is provided, it names a callable in
    ``replay_store`` to use instead of the default SQL query (for
    test fixtures).
    """
    from irc_data.db.connection import get_engine

    plan = ReplayPlanV1.from_dict(plan_dict)
    engine = get_engine()

    fetch_fn: Callable | None = None
    if fetch_fn_name:
        # Resolve a named fetch function (for test injection).
        import irc_data.temporal.replay.replay_store as rs

        fetch_fn = getattr(rs, fetch_fn_name, None)

    artifacts = select_artifacts(engine, plan.artifact_filter, fetch_fn=fetch_fn)
    activity.logger.info(
        f"Selected {len(artifacts)} artifacts for batch {batch_id}"
    )
    return artifacts


# ---------------------------------------------------------------------------
# Activity: run parser (into isolated batch)
# ---------------------------------------------------------------------------


@activity.defn
async def run_parser_activity(
    plan_dict: dict[str, Any],
    batch_id: int,
    artifacts: list[dict[str, Any]],
    parser_fn_name: str = "default_parser",
) -> dict[str, Any]:
    """Run the new parser on each artifact into the isolated batch.

    The parser function is resolved by name from the
    ``parser_registry``.  The default parser is a passthrough that
    returns the raw artifact content — real parsers extract structured
    data.

    Artifacts are stored in ``replay_artifacts`` (isolated from the
    published store).  The batch status is set to ``running`` before
    parsing and ``comparing`` after.

    Returns a summary dict with ``parsed``, ``errors``, ``total``.
    """
    from irc_data.db.connection import get_engine

    plan = ReplayPlanV1.from_dict(plan_dict)
    engine = get_engine()

    # Update status to running (if not already).
    batch = get_batch(engine, plan.plan_id)
    if batch and batch["status"] == BatchStatus.PENDING.value:
        update_batch_status(engine, batch_id, BatchStatus.RUNNING)

    parsed_count = 0
    error_count = 0

    parser_fn = _resolve_parser(parser_fn_name)

    for art in artifacts:
        artifact_url = art.get("artifact_url", "")
        content_hash = art.get("content_hash", "")
        old_parsed = art.get("parsed_output")

        try:
            new_parsed = parser_fn(art)
            store_parsed_output(
                engine,
                batch_id=batch_id,
                artifact_url=artifact_url,
                content_hash=content_hash,
                parsed_output=new_parsed,
                old_parsed_output=old_parsed,
                parse_status="parsed",
            )
            parsed_count += 1
        except Exception as exc:
            store_parsed_output(
                engine,
                batch_id=batch_id,
                artifact_url=artifact_url,
                content_hash=content_hash,
                parsed_output=None,
                old_parsed_output=old_parsed,
                parse_status="error",
                parse_error=str(exc),
            )
            error_count += 1
            activity.logger.warning(
                f"Parse error for {artifact_url}: {exc}"
            )

    # Update status to comparing.
    update_batch_status(engine, batch_id, BatchStatus.COMPARING)

    return {
        "parsed": parsed_count,
        "errors": error_count,
        "total": len(artifacts),
    }


# ---------------------------------------------------------------------------
# Activity: compare batches
# ---------------------------------------------------------------------------


@activity.defn
async def compare_batches_activity(
    batch_id: int,
) -> dict[str, Any]:
    """Compare old vs new parsed outputs for a batch.

    Returns the :class:`ComparisonResult` as a dict and updates the
    batch status to ``awaiting_approval``.
    """
    from irc_data.db.connection import get_engine

    engine = get_engine()
    result = compare_batches(engine, batch_id)

    # Transition to awaiting_approval.
    update_batch_status(engine, batch_id, BatchStatus.AWAITING_APPROVAL)

    activity.logger.info(
        f"Comparison for batch {batch_id}: {result.identical} identical, "
        f"{result.changed} changed, {result.added} new"
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Activity: count batch artifacts (for chunk-level resume)
# ---------------------------------------------------------------------------


@activity.defn
async def count_batch_artifacts_activity(batch_id: int) -> int:
    """Return the number of artifacts already stored in a batch.

    Used by :class:`BackfillWorkflow` to determine how many chunks
    have already been processed (resumability).
    """
    from irc_data.db.connection import get_engine
    from irc_data.temporal.replay.replay_store import count_batch_artifacts

    engine = get_engine()
    return count_batch_artifacts(engine, batch_id)


# ---------------------------------------------------------------------------
# Activity: promote batch
# ---------------------------------------------------------------------------


@activity.defn
async def promote_batch_activity(
    plan_dict: dict[str, Any],
    batch_id: int,
    promoted_by: str = "",
) -> dict[str, Any]:
    """Explicitly promote a batch to publication.

    The old published batch is retained (``status`` → ``superseded``).
    Returns the :class:`PublicationReceiptV1` as a dict.
    """
    from irc_data.db.connection import get_engine

    plan = ReplayPlanV1.from_dict(plan_dict)
    engine = get_engine()

    receipt = promote_batch(engine, batch_id, plan, promoted_by=promoted_by)
    activity.logger.info(
        f"Promoted batch {batch_id}: receipt_id={receipt.receipt_id}, "
        f"old_batch_id={receipt.old_batch_id}, old_retained={receipt.old_retained}"
    )
    return receipt.to_dict()


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------

# A simple registry of parser functions.  Real parsers are registered
# by their source adapter; the default is a passthrough.
_parser_registry: dict[str, Callable[[dict[str, Any]], Any]] = {}


def register_parser(name: str, fn: Callable[[dict[str, Any]], Any]) -> None:
    """Register a parser function by name."""
    _parser_registry[name] = fn


def _resolve_parser(name: str) -> Callable[[dict[str, Any]], Any]:
    """Resolve a parser function by name (falls back to default)."""
    if name in _parser_registry:
        return _parser_registry[name]
    return _default_parser


def _default_parser(artifact: dict[str, Any]) -> Any:
    """Default parser: returns the artifact's parsed_output as-is.

    This is a passthrough for testing.  Real parsers extract structured
    data from raw content.
    """
    return artifact.get("parsed_output")


# Register the default parser.
register_parser("default_parser", _default_parser)
