"""Continuous operation of the data-collection source estate (DP-06-05).

This package turns collection from a demo into a continuously-operated
service.  It provides the **soak test + failure drill** harness named by the
issue's verification criterion ("Soak test and failure drill artifacts
pass"), built from the operational components the earlier tickets shipped:

* **Temporal schedule + backoff** — DP-06-04 / OPS-01-02:
  :mod:`irc_data.temporal.schedules.registry` (one schedule per enabled +
  approved source, interval derived from the register cadence,
  ``ScheduleOverlapPolicy.SKIP``) and the workflow/activity retry policies
  derived from the register ``retry_policy`` field
  (``docs/SCHEDULING-POLICY.md`` §4).
* **Source disable** — the kill switch (SCHEDULING-POLICY §6):
  ``data_sources.enabled = FALSE`` pauses the Temporal schedule (never
  deletes it) and makes ``SourceRunWorkflow`` fail fast with the
  non-retryable ``SourceDisabledError`` at the register gate.
* **Health alert** — OPS-01-04: :mod:`irc_data.scrape_watchdog` detects a
  stale source within one evaluation interval, raises exactly one alert
  (email + ``watchdog_alerts`` row + admin banner), respects the 4 h
  cooldown, and closes the alert on recovery.
* **Checkpoint backup** — DP-01-03 ``AdapterCheckpointV1`` state, exported
  to / imported from a versioned backup directory so collection can resume
  after state loss.
* **Reparse** — DP-02-04 replay: reparsing from the raw lake is
  idempotent; the consumer view does not change and no duplicate
  publication follows.

The deliverable contract is :class:`OpsSoakReportV1` — a signed
(HMAC-SHA256) report recording seven consecutive scheduled cycles within
SLO and a deliberate source-failure drill that alerts and recovers without
duplicate publication.  The operational runbook lives at
``docs/RUNBOOK-DP-06-05.md``.
"""

from irc_data.operations.contracts import (
    OPS_SOAK_SCHEMA_VERSION,
    SIGNING_KEY_ENV,
    SOAK_ARTIFACT_IDS,
    CycleResultV1,
    CycleStatus,
    OpsSoakReportV1,
    sign_report,
    verify_report_signature,
)
from irc_data.operations.soak import (
    DEFAULT_CYCLES,
    SoakConfig,
    SourceOpsSoak,
    run_soak,
)

__all__ = [
    "OPS_SOAK_SCHEMA_VERSION",
    "SIGNING_KEY_ENV",
    "SOAK_ARTIFACT_IDS",
    "CycleResultV1",
    "CycleStatus",
    "OpsSoakReportV1",
    "sign_report",
    "verify_report_signature",
    "DEFAULT_CYCLES",
    "SoakConfig",
    "SourceOpsSoak",
    "run_soak",
]
