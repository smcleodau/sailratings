"""Tests for reconciliation & silent-loss detection (DP-05-03).

Verification approach: *mutated fixtures* simulate the three classic
silent-loss vectors and assert that each one blocks promotion and alerts
within one cycle:

  1. **Dropped pages** — pages were fetched but the parser silently
     dropped some (``fetched > parsed``, no reason code).  The
     conservation invariant flags the shortfall as unexplained variance.
  2. **Parser zero-yield** — the parser emits zero records from a
     non-empty fetch (``fetched > 0, parsed == 0``).
  3. **Duplicate-suppression error** — records are dropped as
     "duplicates" but the count doesn't add up, or a legitimately
     deduplicated run is verified as *explained* and allowed.

Abrupt-yield-change detection is exercised by seeding a healthy baseline
and then collapsing the run's yield below the p10 floor.

These tests run against an in-memory SQLite engine with hand-rolled
schema mirrors (``init_reconciliation_tables`` + ``init_monitor_tables``),
so no Postgres or Alembic state is required.  The data layer deliberately
uses portable SQL so behaviour is identical on Postgres in production.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from irc_data.diagnostics import reconciliation as R
from irc_data.diagnostics.reconciliation import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    KNOWN_REASON_CODES,
    MIN_BASELINE_SAMPLES,
    REASON_DUPLICATE_SUPPRESSED,
    REASON_PARSE_ERROR,
    PipelineCountsV1,
    PromotionBlockedError,
    ReconciliationReportV1,
    assert_promotable,
    compute_variance,
    compute_yield,
    get_latest_report,
    get_report_for_run,
    get_yield_baseline,
    init_reconciliation_tables,
    list_reports,
    reconcile_run,
)
from irc_data.diagnostics.source_monitor import (
    init_monitor_tables,
    is_source_quarantined,
    list_incidents,
    release_quarantine,
)


# ---------------------------------------------------------------------------
# Engine fixture — reconciliation + monitor tables
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_reconciliation_tables(eng)
    init_monitor_tables(eng)
    return eng


class AlertRecorder:
    """Captures webhook alerts without any network calls."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, url: str, payload: dict) -> bool:
        self.payloads.append({"url": url, "payload": payload})
        return True


@pytest.fixture()
def alerts():
    return AlertRecorder()


def _healthy(source: str, run_id: int, n: int = 10) -> PipelineCountsV1:
    """A fully-explained, 100 %-yield run."""
    return PipelineCountsV1(
        run_id=run_id, source_id=source,
        discovered=n, fetched=n, parsed=n, transformed=n, published=n,
    )


def _seed_baseline(engine, source: str, runs: int = 4, n: int = 10):
    """Record ``runs`` healthy runs so the yield baseline is established."""
    for i in range(1, runs + 1):
        reconcile_run(engine, _healthy(source, run_id=i, n=n))


# ---------------------------------------------------------------------------
# Contract round-trips
# ---------------------------------------------------------------------------


class TestContracts:
    def test_pipeline_counts_round_trip(self):
        c = PipelineCountsV1(
            run_id=7, source_id="sailsys", discovered=10, fetched=9, parsed=9,
            transformed=9, rejected=1, quarantined=0, published=8,
            duplicate_suppressed=1,
            reason_counts={REASON_DUPLICATE_SUPPRESSED: 1, REASON_PARSE_ERROR: 1},
        )
        assert PipelineCountsV1.from_dict(c.to_dict()) == c

    def test_report_round_trip(self):
        r = ReconciliationReportV1(
            report_id="recon-sailsys-7", run_id=7, source_id="sailsys",
            variance=2, variance_explained=False, decision=DECISION_BLOCK,
            promotion_allowed=False, block_reason="unexplained_variance=2",
        )
        assert ReconciliationReportV1.from_dict(r.to_dict()) == r

    def test_known_reason_codes_non_empty(self):
        assert REASON_DUPLICATE_SUPPRESSED in KNOWN_REASON_CODES
        assert REASON_PARSE_ERROR in KNOWN_REASON_CODES


# ---------------------------------------------------------------------------
# Variance & yield primitives
# ---------------------------------------------------------------------------


class TestVariance:
    def test_zero_variance_when_fully_explained(self):
        c = _healthy("s", 1, n=10)
        var, unknown = compute_variance(c)
        assert var == 0 and unknown == {}

    def test_stage_loss_flagged(self):
        # fetched 10, parsed 6 — 4 pages silently dropped, no reason code.
        c = PipelineCountsV1(run_id=1, source_id="s", fetched=10, parsed=6,
                             published=6)
        var, _ = compute_variance(c)
        assert var == 4

    def test_publish_loss_flagged(self):
        # parsed 10, published 7 — 3 records vanished after parsing.
        c = PipelineCountsV1(run_id=1, source_id="s", fetched=10, parsed=10,
                             published=7)
        var, _ = compute_variance(c)
        assert var == 3

    def test_reason_coded_drops_are_explained(self):
        # 3 rejected with a reason code -> no variance.
        c = PipelineCountsV1(run_id=1, source_id="s", fetched=10, parsed=10,
                             rejected=3, published=7,
                             reason_counts={REASON_PARSE_ERROR: 3})
        var, _ = compute_variance(c)
        assert var == 0

    def test_unknown_reason_codes_surfaced(self):
        c = PipelineCountsV1(run_id=1, source_id="s", fetched=10, parsed=10,
                             rejected=2, published=8,
                             reason_counts={"mystery_drop": 2})
        var, unknown = compute_variance(c)
        assert var == 0            # numerically explained
        assert unknown == {"mystery_drop": 2}   # but flagged for audit

    def test_yield(self):
        assert compute_yield(_healthy("s", 1, n=10)) == 1.0
        assert compute_yield(PipelineCountsV1(run_id=1, source_id="s",
                                              discovered=10, published=5)) == 0.5
        # Empty source is not itself a silent-loss signal.
        assert compute_yield(PipelineCountsV1(run_id=1, source_id="s",
                                              discovered=0)) == 1.0


# ---------------------------------------------------------------------------
# Mutation 1 — dropped pages
# ---------------------------------------------------------------------------


class TestDroppedPages:
    def test_dropped_pages_block_and_alert(self, engine, alerts):
        _seed_baseline(engine, "sailsys")
        # 10 fetched but only 6 parsed/published; 4 pages silently dropped.
        bad = PipelineCountsV1(run_id=99, source_id="sailsys",
                               discovered=10, fetched=10, parsed=6,
                               transformed=6, published=6)
        report = reconcile_run(
            engine, bad, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.decision == DECISION_BLOCK
        assert report.promotion_allowed is False
        assert report.variance == 4
        assert report.variance_explained is False
        assert "unexplained_variance" in report.block_reason
        # Same-cycle side effects.
        assert is_source_quarantined(engine, "sailsys") is True
        assert len(alerts.payloads) == 1
        # Promotion gate raises.
        with pytest.raises(PromotionBlockedError):
            assert_promotable(report)
        # Report persisted.
        assert get_report_for_run(engine, 99).decision == DECISION_BLOCK

    def test_dropped_pages_open_silent_loss_incident(self, engine, alerts):
        _seed_baseline(engine, "sailsys")
        bad = PipelineCountsV1(run_id=100, source_id="sailsys",
                               discovered=10, fetched=10, parsed=5,
                               transformed=5, published=5)
        reconcile_run(engine, bad, webhook_url="https://hooks.test/x",
                      alert_transport=alerts)
        incidents = list_incidents(engine, "sailsys")
        assert any(i["incident_type"] == "silent_loss" for i in incidents)


# ---------------------------------------------------------------------------
# Mutation 2 — parser zero-yield
# ---------------------------------------------------------------------------


class TestParserZeroYield:
    def test_zero_yield_blocks(self, engine, alerts):
        _seed_baseline(engine, "topyacht")
        # Parser produced nothing from a non-empty fetch.
        bad = PipelineCountsV1(run_id=50, source_id="topyacht",
                               discovered=8, fetched=8, parsed=0,
                               transformed=0, published=0)
        report = reconcile_run(
            engine, bad, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.decision == DECISION_BLOCK
        assert report.variance == 8          # all 8 fetched records lost
        assert report.yield_ratio == 0.0
        assert is_source_quarantined(engine, "topyacht") is True
        assert len(alerts.payloads) == 1


# ---------------------------------------------------------------------------
# Mutation 3 — duplicate suppression
# ---------------------------------------------------------------------------


class TestDuplicateSuppression:
    def test_legitimate_dedup_is_explained_and_allowed(self, engine, alerts):
        _seed_baseline(engine, "irc-certs", n=20)
        # 20 fetched, 20 parsed, 6 suppressed as *reason-coded* duplicates,
        # 14 published.  Fully explained -> allow, no alert, no quarantine.
        ok = PipelineCountsV1(
            run_id=60, source_id="irc-certs", discovered=20, fetched=20,
            parsed=20, transformed=20, duplicate_suppressed=6, published=14,
            reason_counts={REASON_DUPLICATE_SUPPRESSED: 6},
        )
        report = reconcile_run(
            engine, ok, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.decision == DECISION_ALLOW
        assert report.promotion_allowed is True
        assert report.variance == 0
        assert is_source_quarantined(engine, "irc-certs") is False
        assert alerts.payloads == []
        assert_promotable(report)  # must not raise

    def test_duplicate_suppression_error_blocks(self, engine, alerts):
        _seed_baseline(engine, "irc-certs", n=20)
        # Claims 6 duplicates suppressed, but the math doesn't add up:
        # parsed(20) - published(10) - dup(6) = 4 records unexplained.
        bad = PipelineCountsV1(
            run_id=61, source_id="irc-certs", discovered=20, fetched=20,
            parsed=20, transformed=20, duplicate_suppressed=6, published=10,
            reason_counts={REASON_DUPLICATE_SUPPRESSED: 6},
        )
        report = reconcile_run(
            engine, bad, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.decision == DECISION_BLOCK
        assert report.variance == 4
        assert is_source_quarantined(engine, "irc-certs") is True
        assert len(alerts.payloads) == 1


# ---------------------------------------------------------------------------
# Abrupt yield change
# ---------------------------------------------------------------------------


class TestAbruptYieldChange:
    def test_yield_collapse_below_p10_blocks(self, engine, alerts):
        # Baseline: steady 100% yield over several runs.
        _seed_baseline(engine, "orc", runs=5, n=10)
        baseline = get_yield_baseline(engine, "orc")
        assert baseline["samples"] == 5
        assert baseline["p10"] == pytest.approx(1.0)

        # Yield collapses to 20% — but fully reason-coded (legit rejects),
        # so variance alone would be explained; the *yield* signal blocks.
        collapsed = PipelineCountsV1(
            run_id=70, source_id="orc", discovered=10, fetched=10,
            parsed=10, transformed=10, rejected=8, published=2,
            reason_counts={REASON_PARSE_ERROR: 8},
        )
        report = reconcile_run(
            engine, collapsed, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.variance == 0                    # numerically explained
        assert report.abrupt_yield_change is True      # but yield collapsed
        assert report.decision == DECISION_BLOCK
        assert "abrupt_yield_change" in report.block_reason
        assert is_source_quarantined(engine, "orc") is True

    def test_baseline_not_enforced_with_too_few_samples(self, engine, alerts):
        # Fewer than MIN_BASELINE_SAMPLES prior runs -> no band enforced.
        _seed_baseline(engine, "newsource", runs=MIN_BASELINE_SAMPLES - 1, n=10)
        low = PipelineCountsV1(
            run_id=80, source_id="newsource", discovered=10, fetched=10,
            parsed=10, transformed=10, rejected=8, published=2,
            reason_counts={REASON_PARSE_ERROR: 8},
        )
        report = reconcile_run(
            engine, low, webhook_url="https://hooks.test/x",
            alert_transport=alerts,
        )
        assert report.abrupt_yield_change is False
        assert report.decision == DECISION_ALLOW


# ---------------------------------------------------------------------------
# Healthy run end-to-end
# ---------------------------------------------------------------------------


class TestHealthyRun:
    def test_healthy_run_allows_and_records_baseline(self, engine, alerts):
        report = reconcile_run(
            engine, _healthy("sailsys", run_id=1),
            webhook_url="https://hooks.test/x", alert_transport=alerts,
        )
        assert report.decision == DECISION_ALLOW
        assert report.promotion_allowed is True
        assert report.variance == 0
        assert is_source_quarantined(engine, "sailsys") is False
        assert alerts.payloads == []
        # Baseline sample recorded for the run.
        baseline = get_yield_baseline(engine, "sailsys")
        assert baseline is not None and baseline["samples"] == 1


# ---------------------------------------------------------------------------
# Report persistence & queries
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_latest_and_list_reports(self, engine):
        reconcile_run(engine, _healthy("sailsys", run_id=1))
        reconcile_run(engine, _healthy("sailsys", run_id=2))
        reconcile_run(
            engine,
            PipelineCountsV1(run_id=3, source_id="sailsys", discovered=10,
                             fetched=10, parsed=4, published=4),
        )
        latest = get_latest_report(engine, "sailsys")
        assert latest is not None and latest.run_id == 3
        all_reports = list_reports(engine, source_id="sailsys")
        assert len(all_reports) == 3
        blocks = list_reports(engine, source_id="sailsys", decision=DECISION_BLOCK)
        assert len(blocks) == 1 and blocks[0].run_id == 3

    def test_rerun_same_run_id_replaces_report(self, engine):
        reconcile_run(engine, _healthy("sailsys", run_id=5))
        reconcile_run(
            engine,
            PipelineCountsV1(run_id=5, source_id="sailsys", discovered=10,
                             fetched=10, parsed=5, published=5),
        )
        assert get_report_for_run(engine, 5).variance == 5
        assert len(list_reports(engine, source_id="sailsys")) == 1


# ---------------------------------------------------------------------------
# Quarantine release (operator recovery path)
# ---------------------------------------------------------------------------


class TestQuarantineLifecycle:
    def test_release_clears_quarantine(self, engine, alerts):
        _seed_baseline(engine, "sailsys")
        reconcile_run(
            engine,
            PipelineCountsV1(run_id=9, source_id="sailsys", discovered=10,
                             fetched=10, parsed=6, published=6),
            webhook_url="https://hooks.test/x", alert_transport=alerts,
        )
        assert is_source_quarantined(engine, "sailsys") is True
        released = release_quarantine(engine, "sailsys")
        assert released >= 1
        assert is_source_quarantined(engine, "sailsys") is False
