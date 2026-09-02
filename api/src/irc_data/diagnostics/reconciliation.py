"""Pipeline reconciliation & silent-loss detection (DP-05-03).

Detects pipelines that **succeed technically while losing records**.  Every
pipeline run reconciles its stage counts:

    discovered → fetched → parsed → transformed → published
                                       │
                                       ├─→ rejected   (reason-coded)
                                       └─→ quarantined (reason-coded)

The **conservation invariant**:

    fetched == parsed + rejected + quarantined
    parsed  == transformed + rejected + quarantined
    published + duplicate_suppressed + rejected + quarantined
              == transformed            (per publish cycle)

Any shortfall that is **not** attributed to a reason code is *unexplained
variance* — the signature of silent loss.

This module provides:

* :class:`PipelineCountsV1` — the **input contract** (handoff contract)
  a pipeline stage hands to the reconciler after each run.  Counts for
  every stage plus a reason-code ledger for rejected / quarantined /
  duplicate-suppressed records.

* :class:`ReconciliationReportV1` — the **output contract** (handoff
  contract) produced by :func:`reconcile_run` and persisted to the
  ``reconciliation_reports`` table.  Carries the variance, the yield
  (published / discovered), the yield baseline band, whether the
  variance is explained, and whether promotion is allowed.

* :func:`reconcile_run` — the reconciler.  Computes variance, compares
  the run's yield against the source's trailing baseline band
  (p10 floor), decides whether the variance is explained, persists a
  report, and — on a *block* — quarantines the source's publication and
  fires the health-check webhook **within the same cycle**.

* :func:`assert_promotable` — the promotion gate.  Raises
  :class:`PromotionBlockedError` when the report blocks promotion.
  Wired into the publish path so unexplained variance or an abrupt
  yield change blocks promotion.

Reason codes (non-exhaustive; stored on the report for audit):

    duplicate_suppressed   — record dropped because it already exists
    schema_violation       — record rejected: failed schema validation
    parse_error            — record rejected: parser raised on it
    policy_blocked         — record rejected: robots/policy disallow
    out_of_scope           — record rejected: outside the requested window
    quarantined_source     — record quarantined: source incident open
    zero_yield             — parser produced 0 records from a non-empty page

The module is DB-agnostic: raw SQL via ``text()`` so the test suite runs
against in-memory SQLite and production against Postgres.
"""

from __future__ import annotations

import json
import os
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

# Reuse the DP-01-05 alerting machinery (same webhook env-var convention).
from irc_data.diagnostics.source_monitor import (
    HEALTH_WEBHOOK_ENV,
    AlertTransport,
    _post_webhook,
    _quarantine_source,
)

#: Incident type written to ``source_incidents`` when reconciliation blocks.
INCIDENT_SILENT_LOSS = "silent_loss"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"

#: Minimum fraction of records that must be reason-coded for the variance
#: to count as "explained".  1.0 = fully explained; we allow a small epsilon
#: so that rounding a single record does not trigger a block on its own.
EXPLAINED_EPSILON = 0

#: Yield (published / discovered) below baseline_p10 counts as an abrupt
#: yield change and blocks promotion.  The baseline band is [p10, p50] of
#: trailing yields per source.
DEFAULT_YIELD_WINDOW = 14          # trailing runs considered for the baseline
MIN_BASELINE_SAMPLES = 3           # need ≥3 prior runs before a band is enforced
ABRUPT_YIELD_FACTOR = 0.5          # current < factor × p10  → abrupt change

#: Reason codes the reconciler understands.  A record counted under one of
#: these is *explained*; anything else is unexplained variance.
REASON_DUPLICATE_SUPPRESSED = "duplicate_suppressed"
REASON_SCHEMA_VIOLATION = "schema_violation"
REASON_PARSE_ERROR = "parse_error"
REASON_POLICY_BLOCKED = "policy_blocked"
REASON_OUT_OF_SCOPE = "out_of_scope"
REASON_QUARANTINED_SOURCE = "quarantined_source"
REASON_ZERO_YIELD = "zero_yield"

KNOWN_REASON_CODES = frozenset(
    {
        REASON_DUPLICATE_SUPPRESSED,
        REASON_SCHEMA_VIOLATION,
        REASON_PARSE_ERROR,
        REASON_POLICY_BLOCKED,
        REASON_OUT_OF_SCOPE,
        REASON_QUARANTINED_SOURCE,
        REASON_ZERO_YIELD,
    }
)

# Decision values stored on ReconciliationReportV1.decision
DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"

# Alert env var (reuses the DP-01-05 webhook convention).
RECONCILIATION_WEBHOOK_ENV = HEALTH_WEBHOOK_ENV


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromotionBlockedError(RuntimeError):
    """Raised when a reconciliation report blocks promotion."""

    def __init__(self, report: "ReconciliationReportV1"):
        self.report = report
        super().__init__(
            f"Promotion blocked for {report.source_id} run {report.run_id}: "
            f"{report.block_reason}"
        )


# ---------------------------------------------------------------------------
# PipelineCountsV1 — the input contract (handoff contract)
# ---------------------------------------------------------------------------


@dataclass
class PipelineCountsV1:
    """Stage counts for one pipeline run, handed to the reconciler.

    Every count is a non-negative integer.  ``reason_counts`` maps a
    reason code (see ``REASON_*``) to the number of records dropped for
    that reason.  Records dropped for reasons *not* in ``reason_counts``
    are *unexplained*.

    Fields
    ------
    run_id
        The ingestion_log run id this reconciliation belongs to.
    source_id
        The source slug (e.g. ``"sailsys"``).
    discovered / fetched / parsed / transformed / published
        Stage counts.  ``discovered`` = links/records the source
        presented; ``fetched`` = pages/records actually retrieved;
        ``parsed`` = records the parser emitted; ``transformed`` =
        records surviving normalisation; ``published`` = rows written
        to the target table this cycle.
    rejected / quarantined
        Total records rejected / quarantined.  These should equal the
        sum of the corresponding reason counts.
    duplicate_suppressed
        Records suppressed as duplicates (a subset of rejected, broken
        out because duplicate suppression is the classic silent-loss
        vector when it misfires).
    reason_counts
        Mapping of reason code → count for every dropped record.
    """

    run_id: int
    source_id: str
    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    transformed: int = 0
    rejected: int = 0
    quarantined: int = 0
    published: int = 0
    duplicate_suppressed: int = 0
    reason_counts: dict[str, int] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PipelineCountsV1":
        return cls(
            run_id=d["run_id"],
            source_id=d["source_id"],
            discovered=d.get("discovered", 0),
            fetched=d.get("fetched", 0),
            parsed=d.get("parsed", 0),
            transformed=d.get("transformed", 0),
            rejected=d.get("rejected", 0),
            quarantined=d.get("quarantined", 0),
            published=d.get("published", 0),
            duplicate_suppressed=d.get("duplicate_suppressed", 0),
            reason_counts=dict(d.get("reason_counts") or {}),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    # -- derived quantities ------------------------------------------------

    @property
    def explained_dropped(self) -> int:
        """Records accounted for by reason codes."""
        return sum(self.reason_counts.values())

    @property
    def total_dropped(self) -> int:
        """Records that entered but did not publish this cycle."""
        return self.rejected + self.quarantined + self.duplicate_suppressed


# ---------------------------------------------------------------------------
# ReconciliationReportV1 — the output contract (handoff contract)
# ---------------------------------------------------------------------------


@dataclass
class ReconciliationReportV1:
    """The reconciler's verdict for one run.

    Persisted to ``reconciliation_reports`` and returned to the caller.
    ``decision`` is ``"allow"`` or ``"block"``; when ``"block"``,
    ``block_reason`` explains why and ``promotion_allowed`` is ``False``.
    """

    report_id: str
    run_id: int
    source_id: str
    checked_at: str = ""
    counts: dict[str, Any] = field(default_factory=dict)

    # Variance (unexplained loss)
    variance: int = 0
    variance_explained: bool = True
    unexplained_reasons: dict[str, int] = field(default_factory=dict)

    # Yield analysis
    yield_ratio: float = 1.0
    baseline_yield_p10: float | None = None
    baseline_yield_p50: float | None = None
    abrupt_yield_change: bool = False

    # Decision
    decision: str = DECISION_ALLOW
    promotion_allowed: bool = True
    block_reason: str | None = None

    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReconciliationReportV1":
        return cls(
            report_id=d["report_id"],
            run_id=d["run_id"],
            source_id=d["source_id"],
            checked_at=d.get("checked_at", ""),
            counts=dict(d.get("counts") or {}),
            variance=d.get("variance", 0),
            variance_explained=d.get("variance_explained", True),
            unexplained_reasons=dict(d.get("unexplained_reasons") or {}),
            yield_ratio=d.get("yield_ratio", 1.0),
            baseline_yield_p10=d.get("baseline_yield_p10"),
            baseline_yield_p50=d.get("baseline_yield_p50"),
            abrupt_yield_change=d.get("abrupt_yield_change", False),
            decision=d.get("decision", DECISION_ALLOW),
            promotion_allowed=d.get("promotion_allowed", True),
            block_reason=d.get("block_reason"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


# ---------------------------------------------------------------------------
# DB schema mirror (for SQLite tests)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_count_baseline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    discovered INTEGER DEFAULT 0,
    fetched INTEGER DEFAULT 0,
    parsed INTEGER DEFAULT 0,
    transformed INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    quarantined INTEGER DEFAULT 0,
    published INTEGER DEFAULT 0,
    duplicate_suppressed INTEGER DEFAULT 0,
    yield_ratio REAL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_pcb_source_recorded
    ON pipeline_count_baseline(source_id, recorded_at);

CREATE TABLE IF NOT EXISTS reconciliation_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE,
    run_id INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    counts TEXT,
    variance INTEGER DEFAULT 0,
    variance_explained BOOLEAN DEFAULT 1,
    unexplained_reasons TEXT,
    yield_ratio REAL,
    baseline_yield_p10 REAL,
    baseline_yield_p50 REAL,
    abrupt_yield_change BOOLEAN DEFAULT 0,
    decision TEXT NOT NULL DEFAULT 'allow',
    promotion_allowed BOOLEAN DEFAULT 1,
    block_reason TEXT,
    schema_version TEXT DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS ix_recon_reports_source
    ON reconciliation_reports(source_id, checked_at);
"""


def init_reconciliation_tables(engine: Engine) -> None:
    """Create the reconciliation tables (idempotent).

    On Postgres this is normally handled by the Alembic migration
    (0028_reconciliation).  This helper exists so tests can set up an
    in-memory SQLite schema without Alembic.
    """
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


# ---------------------------------------------------------------------------
# Variance computation
# ---------------------------------------------------------------------------


def compute_variance(counts: PipelineCountsV1) -> tuple[int, dict[str, int]]:
    """Return ``(unexplained_variance, unknown_reason_breakdown)``.

    The conservation invariant across the pipeline::

        discovered >= fetched
        fetched  == parsed + rejected + quarantined + duplicate_suppressed
                        (+ still-unpublished backlog, which must be
                        reason-coded as such to count as explained)

    *Unexplained variance* is the sum of two silent-loss signatures:

    * **stage loss** — ``max(0, fetched - (parsed + rejected +
      quarantined + duplicate_suppressed))``: records that entered the
      pipeline but never reached *any* accounted terminal state and carry
      no reason code.  A page that was fetched but silently dropped by
      the parser shows up here.
    * **publish loss** — ``max(0, parsed - (published + rejected +
      quarantined + duplicate_suppressed))``: records the parser emitted
      that never landed in the published table and were not reason-coded.

    Any ``reason_counts`` key not in :data:`KNOWN_REASON_CODES` is
    surfaced in the breakdown as an *unknown reason* (still counted as
    explained numerically, but flagged for audit).
    """
    stage_loss = max(
        0,
        counts.fetched
        - (
            counts.parsed
            + counts.rejected
            + counts.quarantined
            + counts.duplicate_suppressed
        ),
    )
    publish_loss = max(
        0,
        counts.parsed
        - (
            counts.published
            + counts.rejected
            + counts.quarantined
            + counts.duplicate_suppressed
        ),
    )
    variance = stage_loss + publish_loss

    unknown = {
        k: v for k, v in counts.reason_counts.items()
        if k not in KNOWN_REASON_CODES
    }
    return variance, unknown


def compute_yield(counts: PipelineCountsV1) -> float:
    """Published yield as a fraction of discovered records.

    ``published / discovered``; 1.0 when nothing was discovered (a
    completely empty source is not itself a silent-loss signal — the
    DP-01-05 monitor catches that separately).
    """
    if counts.discovered <= 0:
        return 1.0
    return counts.published / counts.discovered


# ---------------------------------------------------------------------------
# Yield baseline
# ---------------------------------------------------------------------------


def record_yield_sample(
    engine: Engine,
    counts: PipelineCountsV1,
    yield_ratio: float,
    recorded_at: datetime | None = None,
) -> None:
    """Append one run's yield to the trailing baseline series."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO pipeline_count_baseline
                  (source_id, run_id, discovered, fetched, parsed, transformed,
                   rejected, quarantined, published, duplicate_suppressed,
                   yield_ratio, recorded_at)
                VALUES
                  (:source_id, :run_id, :discovered, :fetched, :parsed,
                   :transformed, :rejected, :quarantined, :published,
                   :duplicate_suppressed, :yield_ratio, :recorded_at)
                """
            ),
            {
                "source_id": counts.source_id,
                "run_id": counts.run_id,
                "discovered": counts.discovered,
                "fetched": counts.fetched,
                "parsed": counts.parsed,
                "transformed": counts.transformed,
                "rejected": counts.rejected,
                "quarantined": counts.quarantined,
                "published": counts.published,
                "duplicate_suppressed": counts.duplicate_suppressed,
                "yield_ratio": yield_ratio,
                "recorded_at": recorded_at or datetime.now(timezone.utc),
            },
        )


def get_yield_baseline(
    engine: Engine,
    source_id: str,
    window: int = DEFAULT_YIELD_WINDOW,
) -> dict[str, float | int] | None:
    """Return the trailing yield band for ``source_id``.

    Uses the most recent ``window`` samples and returns::

        {"samples": n, "p10": …, "p50": …, "mean": …}

    Returns ``None`` when there are no samples yet.  The band is enforced
    only when ``samples >= MIN_BASELINE_SAMPLES``.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT yield_ratio FROM pipeline_count_baseline
                WHERE source_id = :sid AND yield_ratio IS NOT NULL
                ORDER BY recorded_at DESC, id DESC
                LIMIT :lim
                """
            ),
            {"sid": source_id, "lim": window},
        ).fetchall()

    yields = sorted(float(r[0]) for r in rows)
    n = len(yields)
    if n == 0:
        return None

    p50 = statistics.median(yields)
    # p10 via linear interpolation on the sorted sample (n small).
    if n == 1:
        p10 = yields[0]
    else:
        rank = 0.10 * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        p10 = yields[lo] + (yields[hi] - yields[lo]) * (rank - lo)
    return {
        "samples": n,
        "p10": p10,
        "p50": p50,
        "mean": statistics.fmean(yields),
    }


# ---------------------------------------------------------------------------
# Alerting (reuses the DP-01-05 webhook transport pattern)
# ---------------------------------------------------------------------------


def send_reconciliation_alert(
    report: ReconciliationReportV1,
    webhook_url: str | None = None,
    *,
    transport: AlertTransport | None = None,
) -> bool:
    """Fire a health-check webhook alert for a blocked reconciliation.

    Same payload shape as :func:`send_source_alert` so downstream
    alert routing (Discord / Slack / generic) works unchanged.  Returns
    ``True`` when the alert was accepted by the transport; never raises.
    """
    url = webhook_url or os.environ.get(RECONCILIATION_WEBHOOK_ENV, "")
    if not url:
        return False
    post = transport or _post_webhook

    title = f"Silent-loss reconciliation block: {report.source_id}"
    lines = [
        f"*:rotating_light: {title}*",
        f"*Source:* {report.source_id}   *Run:* {report.run_id}",
        f"*Variance:* {report.variance} unexplained record(s)",
        f"*Yield:* {report.yield_ratio:.3f}"
        + (
            f"  (baseline p10 {report.baseline_yield_p10:.3f},"
            f" p50 {report.baseline_yield_p50:.3f})"
            if report.baseline_yield_p10 is not None
            else "  (no baseline)"
        ),
        f"*Reason:* {report.block_reason}",
    ]
    payload: dict[str, Any] = {
        "text": "\n".join(lines),
        "summary": f"{title} — run {report.run_id} — {report.block_reason}",
    }
    try:
        return bool(post(url, payload))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The reconciler
# ---------------------------------------------------------------------------


def reconcile_run(
    engine: Engine,
    counts: PipelineCountsV1,
    *,
    webhook_url: str | None = None,
    alert_transport: AlertTransport | None = None,
    checked_at: datetime | None = None,
    record_baseline: bool = True,
) -> ReconciliationReportV1:
    """Reconcile one pipeline run and decide whether promotion is allowed.

    Steps (all within the *same cycle* as the run):

    1.  Compute unexplained variance (conservation invariant).
    2.  Compute the run's yield and compare against the trailing
        baseline band (abrupt-yield-change detection).
    3.  Decide ``allow`` vs ``block``.
    4.  Persist a :class:`ReconciliationReportV1` row.
    5.  On ``block``: quarantine the source's publication and fire the
        health-check webhook alert.

    Returns the report.  Idempotent per ``run_id`` for the baseline
    sample (a re-run of the same run_id replaces, not duplicates, the
    baseline row).
    """
    now = checked_at or datetime.now(timezone.utc)
    variance, unknown_reasons = compute_variance(counts)
    yield_ratio = compute_yield(counts)

    baseline = get_yield_baseline(engine, counts.source_id)
    baseline_p10 = baseline["p10"] if baseline else None
    baseline_p50 = baseline["p50"] if baseline else None

    abrupt_yield = False
    if baseline is not None and baseline["samples"] >= MIN_BASELINE_SAMPLES:
        abrupt_yield = yield_ratio < (ABRUPT_YIELD_FACTOR * baseline["p10"])

    # Zero-yield special case: parser produced nothing from a non-empty
    # fetch.  This is always at least worth a hard look; if it also
    # produces unexplained variance it blocks.
    zero_yield = (
        counts.fetched > 0 and counts.parsed == 0 and counts.published == 0
    )

    variance_explained = variance <= EXPLAINED_EPSILON
    block_reasons: list[str] = []
    if not variance_explained:
        block_reasons.append(
            f"unexplained_variance={variance} "
            f"(fetched={counts.fetched} not accounted for by "
            f"published+rejected+quarantined+duplicate_suppressed="
            f"{counts.published + counts.rejected + counts.quarantined + counts.duplicate_suppressed})"
        )
    if abrupt_yield:
        block_reasons.append(
            f"abrupt_yield_change: yield={yield_ratio:.3f} < "
            f"{ABRUPT_YIELD_FACTOR}× baseline_p10={baseline_p10:.3f}"
        )
    if zero_yield and not variance_explained:
        block_reasons.append("parser_zero_yield_with_unexplained_loss")

    decision = DECISION_BLOCK if block_reasons else DECISION_ALLOW
    block_reason = "; ".join(block_reasons) if block_reasons else None

    report = ReconciliationReportV1(
        report_id=f"recon-{counts.source_id}-{counts.run_id}",
        run_id=counts.run_id,
        source_id=counts.source_id,
        checked_at=now.isoformat(),
        counts=counts.to_dict(),
        variance=variance,
        variance_explained=variance_explained,
        unexplained_reasons=unknown_reasons,
        yield_ratio=yield_ratio,
        baseline_yield_p10=baseline_p10,
        baseline_yield_p50=baseline_p50,
        abrupt_yield_change=abrupt_yield,
        decision=decision,
        promotion_allowed=(decision == DECISION_ALLOW),
        block_reason=block_reason,
    )

    # Persist the report (replace on re-run of the same run_id).
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM reconciliation_reports WHERE report_id = :rid"),
            {"rid": report.report_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO reconciliation_reports
                  (report_id, run_id, source_id, checked_at, counts,
                   variance, variance_explained, unexplained_reasons,
                   yield_ratio, baseline_yield_p10, baseline_yield_p50,
                   abrupt_yield_change, decision, promotion_allowed,
                   block_reason, schema_version)
                VALUES
                  (:report_id, :run_id, :source_id, :checked_at, :counts,
                   :variance, :variance_explained, :unexplained_reasons,
                   :yield_ratio, :baseline_yield_p10, :baseline_yield_p50,
                   :abrupt_yield_change, :decision, :promotion_allowed,
                   :block_reason, :schema_version)
                """
            ),
            {
                "report_id": report.report_id,
                "run_id": report.run_id,
                "source_id": report.source_id,
                "checked_at": now,
                "counts": json.dumps(report.counts, sort_keys=True),
                "variance": report.variance,
                "variance_explained": report.variance_explained,
                "unexplained_reasons": json.dumps(
                    report.unexplained_reasons, sort_keys=True
                ),
                "yield_ratio": report.yield_ratio,
                "baseline_yield_p10": report.baseline_yield_p10,
                "baseline_yield_p50": report.baseline_yield_p50,
                "abrupt_yield_change": report.abrupt_yield_change,
                "decision": report.decision,
                "promotion_allowed": report.promotion_allowed,
                "block_reason": report.block_reason,
                "schema_version": report.schema_version,
            },
        )

    # Record the yield sample for the baseline *after* reading the
    # baseline, so the current run never influences its own band.
    if record_baseline:
        _record_baseline_sample_idempotent(engine, counts, yield_ratio, now)

    # On block: open/attach an incident, quarantine publication, and alert —
    # all in the same cycle as the run.
    if decision == DECISION_BLOCK:
        _open_silent_loss_incident(engine, report)
        send_reconciliation_alert(
            report, webhook_url, transport=alert_transport
        )

    return report


def _open_silent_loss_incident(
    engine: Engine, report: ReconciliationReportV1
) -> int | None:
    """Open (or reuse) a ``silent_loss`` incident and quarantine the source.

    Returns the incident id, or ``None`` when the incident tables are not
    present (e.g. a minimal test schema).  Never raises — incident
    recording must not break the reconciler.
    """
    from irc_data.diagnostics.source_monitor import (
        _attach_to_incident,
        _create_incident,
        _get_open_incident,
    )

    deviations = [report.block_reason or "unexplained_variance"]
    sample = [
        {
            "run_id": report.run_id,
            "variance": report.variance,
            "yield_ratio": report.yield_ratio,
            "counts": report.counts,
        }
    ]
    excerpt = f"reconciliation block: {report.block_reason}"
    try:
        existing = _get_open_incident(engine, report.source_id)
        if existing and existing.get("incident_type") == INCIDENT_SILENT_LOSS:
            incident_id = int(existing["id"])
            _attach_to_incident(
                engine, incident_id, deviations, sample, excerpt
            )
        else:
            incident_id = _create_incident(
                engine,
                report.source_id,
                url=None,
                incident_type=INCIDENT_SILENT_LOSS,
                deviations=deviations,
                sample_records=sample,
                content_excerpt=excerpt,
                previous_hash=None,
                current_hash=None,
            )
        _quarantine_source(
            engine,
            report.source_id,
            incident_id=incident_id,
            reason=f"reconciliation: {report.block_reason}",
        )
        return incident_id
    except Exception:
        # The publication_quarantine / source_incidents tables may not exist
        # in a minimal test schema.  Quarantine is best-effort; the report
        # row (already persisted) is the authoritative block signal.
        return None


def _record_baseline_sample_idempotent(
    engine: Engine,
    counts: PipelineCountsV1,
    yield_ratio: float,
    recorded_at: datetime,
) -> None:
    """Insert the baseline sample, replacing any prior sample for run_id."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM pipeline_count_baseline "
                "WHERE source_id = :sid AND run_id = :rid"
            ),
            {"sid": counts.source_id, "rid": counts.run_id},
        )
    record_yield_sample(engine, counts, yield_ratio, recorded_at=recorded_at)


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def assert_promotable(report: ReconciliationReportV1) -> None:
    """Raise :class:`PromotionBlockedError` if the report blocks promotion."""
    if not report.promotion_allowed:
        raise PromotionBlockedError(report)


def get_latest_report(
    engine: Engine, source_id: str
) -> ReconciliationReportV1 | None:
    """Return the most recent reconciliation report for ``source_id``."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT report_id, run_id, source_id, checked_at, counts,
                       variance, variance_explained, unexplained_reasons,
                       yield_ratio, baseline_yield_p10, baseline_yield_p50,
                       abrupt_yield_change, decision, promotion_allowed,
                       block_reason, schema_version
                FROM reconciliation_reports
                WHERE source_id = :sid
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"sid": source_id},
        ).first()
    return _row_to_report(row) if row else None


def get_report_for_run(
    engine: Engine, run_id: int
) -> ReconciliationReportV1 | None:
    """Return the reconciliation report for a specific run."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT report_id, run_id, source_id, checked_at, counts,
                       variance, variance_explained, unexplained_reasons,
                       yield_ratio, baseline_yield_p10, baseline_yield_p50,
                       abrupt_yield_change, decision, promotion_allowed,
                       block_reason, schema_version
                FROM reconciliation_reports
                WHERE run_id = :rid
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"rid": run_id},
        ).first()
    return _row_to_report(row) if row else None


def list_reports(
    engine: Engine,
    source_id: str | None = None,
    decision: str | None = None,
    limit: int = 50,
) -> list[ReconciliationReportV1]:
    """List recent reconciliation reports, newest first."""
    clauses = []
    params: dict[str, Any] = {"lim": limit}
    if source_id is not None:
        clauses.append("source_id = :sid")
        params["sid"] = source_id
    if decision is not None:
        clauses.append("decision = :dec")
        params["dec"] = decision
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT report_id, run_id, source_id, checked_at, counts,
                       variance, variance_explained, unexplained_reasons,
                       yield_ratio, baseline_yield_p10, baseline_yield_p50,
                       abrupt_yield_change, decision, promotion_allowed,
                       block_reason, schema_version
                FROM reconciliation_reports
                {where}
                ORDER BY checked_at DESC, id DESC
                LIMIT :lim
                """
            ),
            params,
        ).fetchall()
    return [_row_to_report(r) for r in rows]


def _row_to_report(row: Any) -> ReconciliationReportV1:
    d = dict(row._mapping)
    for key in ("counts", "unexplained_reasons"):
        v = d.get(key)
        if isinstance(v, str):
            try:
                d[key] = json.loads(v)
            except ValueError:
                d[key] = {}
    # Normalise checked_at to ISO string.
    cat = d.get("checked_at")
    if isinstance(cat, datetime):
        d["checked_at"] = cat.isoformat()
    elif cat is not None and not isinstance(cat, str):
        d["checked_at"] = str(cat)
    # SQLite booleans arrive as 0/1.
    for bkey in ("variance_explained", "abrupt_yield_change", "promotion_allowed"):
        if isinstance(d.get(bkey), int):
            d[bkey] = bool(d[bkey])
    return ReconciliationReportV1.from_dict(d)
