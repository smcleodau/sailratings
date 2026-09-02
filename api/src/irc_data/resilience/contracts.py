"""Contracts for the DP-05-05 load / resilience / disaster-recovery drill.

This module defines the **handoff / output contracts** for the data-plane
drill:

* :class:`ScenarioResultV1` — the outcome of one drill scenario (load,
  resilience or disaster-recovery): what was measured, whether the
  acceptance checks held, and the evidence gathered.
* :class:`DrillReportV1` — the *signed report* for a whole drill run.
  The report is the deliverable named by the issue's verification
  criterion ("Production-sized synthetic load plus restore drill
  produces signed report").

Signing
-------
The report is signed with HMAC-SHA256.  The signature covers the
*canonical* serialisation of every field except ``signature`` itself
(``_canonical_payload``), so the signature is stable regardless of dict
ordering or whitespace.  ``signing_key_id`` identifies which key signed
the report, so a verifier holding the same key can recompute and compare
(:func:`verify_report_signature`).

This mirrors the deterministic, hash-anchored discipline used by the
replay store (DP-02-04) and quality gates (DP-05-02): the report is a
*tamper-evident* statement of what was measured, not just a log line.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


DRILL_SCHEMA_VERSION = "v1"

# Env var that carries the HMAC signing key in production.  Tests and
# ad-hoc drills pass the key explicitly instead.
SIGNING_KEY_ENV = "DP05_DRILL_SIGNING_KEY"

#: The scenario identifiers, in the order the drill executes them.  Kept
#: as a module constant so the report can assert full coverage and the
#: docs/tests share one source of truth.
SCENARIO_IDS = (
    "high_volume_ingest",
    "backfill_under_load",
    "concurrent_adapters",
    "database_outage",
    "object_store_outage",
    "restore_and_replay",
)


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(payload: dict[str, Any]) -> str:
    """Canonical JSON: sorted keys, compact separators, no whitespace.

    This is the exact byte string the HMAC signature covers.  Using a
    canonical form means the signature survives a round-trip through
    ``to_dict`` / ``from_dict`` and any dict-ordering differences.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class ScenarioStatus(str, enum.Enum):
    """Outcome of one drill scenario."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass
class ScenarioResultV1:
    """The measured outcome of one drill scenario.

    Fields
    ------
    scenario
        Stable identifier, one of :data:`SCENARIO_IDS`.
    status
        ``passed`` iff every acceptance check in ``checks`` held.
    started_at / completed_at
        ISO-8601 timestamps bracketing the scenario.
    duration_seconds
        Wall-clock duration of the scenario.
    volume
        Number of artifacts / records the scenario pushed through the
        data plane (the "load" dimension).
    throughput_per_second
        Measured throughput: ``volume / duration_seconds``.  ``None``
        when the scenario does not have a meaningful throughput
        (e.g. an outage scenario that is about failure behaviour, not
        rate).
    rpo_seconds
        Recovery Point Objective measured for this scenario — the age
        of the oldest durable write that survived the fault.  ``0.0``
        means "no committed data was lost".  Only meaningful for the
        outage / restore scenarios.
    rto_seconds
        Recovery Time Objective measured for this scenario — wall-clock
        time from fault injection until the system was back to its
        pre-fault consumer-visible state.  Only meaningful for the
        outage / restore scenarios.
    checks
        Named acceptance checks and their boolean outcome.  A scenario
        passes iff every value is ``True``.
    metrics
        Free-form measured values (counts, timings) that back the
        checks — e.g. ``published_rows``, ``promotion_receipts``.
    evidence
        Human-readable evidence lines (what was asserted, what was
        observed) for the reviewer.
    error
        Failure detail when ``status == failed``.
    """

    scenario: str
    status: str = ScenarioStatus.PASSED.value
    started_at: str = field(default_factory=_now_iso)
    completed_at: str = field(default_factory=_now_iso)
    duration_seconds: float = 0.0
    volume: int = 0
    throughput_per_second: float | None = None
    rpo_seconds: float | None = None
    rto_seconds: float | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    error: str = ""
    schema_version: str = DRILL_SCHEMA_VERSION

    def finalise(self) -> "ScenarioResultV1":
        """Compute ``status`` from ``checks`` and stamp completion."""
        self.completed_at = _now_iso()
        self.status = (
            ScenarioStatus.PASSED.value
            if all(self.checks.values()) and not self.error
            else ScenarioStatus.FAILED.value
        )
        return self

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario": self.scenario,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "volume": self.volume,
            "throughput_per_second": self.throughput_per_second,
            "rpo_seconds": self.rpo_seconds,
            "rto_seconds": self.rto_seconds,
            "checks": dict(self.checks),
            "metrics": dict(self.metrics),
            "evidence": list(self.evidence),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScenarioResultV1":
        return cls(
            scenario=d["scenario"],
            status=d.get("status", ScenarioStatus.PASSED.value),
            started_at=d.get("started_at", _now_iso()),
            completed_at=d.get("completed_at", _now_iso()),
            duration_seconds=d.get("duration_seconds", 0.0),
            volume=d.get("volume", 0),
            throughput_per_second=d.get("throughput_per_second"),
            rpo_seconds=d.get("rpo_seconds"),
            rto_seconds=d.get("rto_seconds"),
            checks=dict(d.get("checks", {})),
            metrics=dict(d.get("metrics", {})),
            evidence=list(d.get("evidence", [])),
            error=d.get("error", ""),
            schema_version=d.get("schema_version", DRILL_SCHEMA_VERSION),
        )


@dataclass
class DrillReportV1:
    """DP-05-05 handoff contract — the *signed* drill report.

    This is the deliverable: a tamper-evident record of the safe
    operating envelope, produced by driving a production-shaped
    synthetic load plus a restore drill through the data plane.

    Fields
    ------
    report_id
        Unique identifier for this drill run.
    started_at / completed_at
        ISO-8601 timestamps bracketing the whole drill.
    duration_seconds
        Wall-clock duration of the whole drill.
    scenarios
        One :class:`ScenarioResultV1` per scenario in
        :data:`SCENARIO_IDS`.
    overall_status
        ``passed`` iff every scenario passed.
    artifact_volume
        Total number of synthetic artifacts the drill pushed through
        the data plane (the "production-sized synthetic load" figure).
    aggregate_throughput_per_second
        Sum of scenario volumes divided by total load-bearing time.
    measured_rpo_seconds / measured_rto_seconds
        The headline RPO / RTO measured by the restore drill.
    passed_acceptance_criteria
        The issue's acceptance criteria, mapped to a boolean each.
    signature
        HMAC-SHA256 hex digest over the canonical payload (every field
        except ``signature``).  Empty until :func:`sign_report` is
        called.
    signing_key_id
        Identifier of the key that produced ``signature``.
    """

    report_id: str
    started_at: str = field(default_factory=_now_iso)
    completed_at: str = field(default_factory=_now_iso)
    duration_seconds: float = 0.0
    scenarios: list[ScenarioResultV1] = field(default_factory=list)
    overall_status: str = ScenarioStatus.PASSED.value
    artifact_volume: int = 0
    aggregate_throughput_per_second: float = 0.0
    measured_rpo_seconds: float | None = None
    measured_rto_seconds: float | None = None
    passed_acceptance_criteria: dict[str, bool] = field(default_factory=dict)
    signature: str = ""
    signing_key_id: str = ""
    schema_version: str = DRILL_SCHEMA_VERSION

    # ------------------------------------------------------------------
    # Canonical payload + signing
    # ------------------------------------------------------------------

    def _canonical_payload(self) -> dict[str, Any]:
        """The exact dict the signature covers (``signature`` excluded)."""
        d = self.to_dict()
        d.pop("signature", None)
        return d

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "overall_status": self.overall_status,
            "artifact_volume": self.artifact_volume,
            "aggregate_throughput_per_second": self.aggregate_throughput_per_second,
            "measured_rpo_seconds": self.measured_rpo_seconds,
            "measured_rto_seconds": self.measured_rto_seconds,
            "passed_acceptance_criteria": dict(self.passed_acceptance_criteria),
            "signature": self.signature,
            "signing_key_id": self.signing_key_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DrillReportV1":
        return cls(
            report_id=d["report_id"],
            started_at=d.get("started_at", _now_iso()),
            completed_at=d.get("completed_at", _now_iso()),
            duration_seconds=d.get("duration_seconds", 0.0),
            scenarios=[ScenarioResultV1.from_dict(s) for s in d.get("scenarios", [])],
            overall_status=d.get("overall_status", ScenarioStatus.PASSED.value),
            artifact_volume=d.get("artifact_volume", 0),
            aggregate_throughput_per_second=d.get(
                "aggregate_throughput_per_second", 0.0
            ),
            measured_rpo_seconds=d.get("measured_rpo_seconds"),
            measured_rto_seconds=d.get("measured_rto_seconds"),
            passed_acceptance_criteria=dict(d.get("passed_acceptance_criteria", {})),
            signature=d.get("signature", ""),
            signing_key_id=d.get("signing_key_id", ""),
            schema_version=d.get("schema_version", DRILL_SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> "DrillReportV1":
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def sign_report(report: DrillReportV1, key: bytes, *, key_id: str = "") -> str:
    """Sign *report* in place with HMAC-SHA256 and return the signature.

    The signature covers the canonical payload (every field except
    ``signature``).  ``signing_key_id`` is set to *key_id* so verifiers
    know which key to use.
    """
    report.signing_key_id = key_id
    payload = _canonical_json(report._canonical_payload())
    report.signature = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return report.signature


def verify_report_signature(report: DrillReportV1, key: bytes) -> bool:
    """Return ``True`` iff *report*'s signature is valid under *key*.

    Recomputes the HMAC over the canonical payload and compares with
    :func:`hmac.compare_digest` (constant-time).  A report whose payload
    has been tampered with — any scenario result, any measured RPO /
    RTO, any acceptance criterion — fails verification.
    """
    if not report.signature:
        return False
    payload = _canonical_json(report._canonical_payload())
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, report.signature)
