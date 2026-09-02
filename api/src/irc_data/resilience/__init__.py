"""Load, resilience and disaster-recovery testing for the data plane (DP-05-05).

This package implements the *safe-operating-envelope* drill harness.
It drives a production-shaped synthetic load through the data plane —
raw-lake capture, run-ledger accounting, gated validation, explicit
promotion and replay/backfill — while injecting the fault and disaster
scenarios the issue calls for:

* **Load** — high artifact volume, backfills, concurrent adapters.
* **Resilience** — database outage and object-store outage, with
  measured recovery.
* **Disaster recovery** — full restore of published state by replaying
  from the raw lake, then an idempotency check that proves no duplicate
  publication follows the replay.

Every drill produces a :class:`DrillReportV1` — a signed report (HMAC-
SHA256 over the canonical payload) carrying the measured RPO / RTO and
throughput so the safe operating envelope is recorded, verifiable and
auditable.

Builds on
---------
* DP-02-02 — :mod:`irc_data.sources.raw_lake` (durable, hash-verified
  raw capture; the system of record replayed during restore).
* DP-02-04 — :mod:`irc_data.temporal.replay` (idempotent, resumable
  replay / backfill with explicit promotion).
* DP-05-01 — :mod:`irc_data.db.run_ledger` (per-run accounting).
* DP-05-02 — :mod:`irc_data.quality` (validation / quarantine /
  promotion gates; only ``promote_batch`` crosses the consumer line).
"""

from irc_data.resilience.contracts import (
    DRILL_SCHEMA_VERSION,
    DrillReportV1,
    ScenarioResultV1,
    ScenarioStatus,
    sign_report,
    verify_report_signature,
)
from irc_data.resilience.drill import (
    DEFAULT_ARTIFACT_VOLUME,
    DataPlaneDrill,
    DrillConfig,
    run_drill,
)

__all__ = [
    "DRILL_SCHEMA_VERSION",
    "DEFAULT_ARTIFACT_VOLUME",
    "DataPlaneDrill",
    "DrillConfig",
    "DrillReportV1",
    "ScenarioResultV1",
    "ScenarioStatus",
    "run_drill",
    "sign_report",
    "verify_report_signature",
]
