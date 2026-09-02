"""Contracts for the DP-06-05 soak test + failure drill.

This module defines the **handoff / output contracts** for operating a
source continuously:

* :class:`CycleResultV1` — the measured outcome of one scheduled
  collection cycle: start/end timestamps, derived duration, whether it
  landed within the cycle SLO, and the ledger / publication counts it
  produced.
* :class:`OpsSoakReportV1` — the *signed report* for a whole soak +
  failure-drill run.  This is the deliverable named by the issue's
  verification criterion ("Soak test and failure drill artifacts pass")
  and the evidence for both acceptance criteria:

  1. **Seven consecutive scheduled cycles complete within SLO** —
     recorded as ``cycles`` (one :class:`CycleResultV1` per cycle) plus
     the aggregate ``cycles_within_slo`` count.
  2. **Deliberate source failure alerts and recovers without duplicate
     publication** — recorded as ``failure_drill`` (a named suite of
     boolean checks) covering: kill-switch disable ⇒ schedule paused +
     gate refusal; health alert raised on the next watchdog evaluation;
     checkpoint backup + verified restore; re-enable ⇒ recovery; and a
     post-recovery reparse that leaves the consumer view unchanged (no
     duplicate publication).

Signing
-------
The report is signed with HMAC-SHA256 over the *canonical* serialisation
of every field except ``signature`` itself — the same tamper-evident
discipline as the DP-05-05 drill report
(:mod:`irc_data.resilience.contracts`).  ``signing_key_id`` identifies
which key signed the report so a verifier holding the same key can
recompute and compare (:func:`verify_report_signature`).
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

OPS_SOAK_SCHEMA_VERSION = "v1"

#: Env var that carries the HMAC signing key in production.  Tests and
#: ad-hoc soaks pass the key explicitly instead.
SIGNING_KEY_ENV = "DP06_SOAK_SIGNING_KEY"

#: The artifact identifiers the soak produces, in the order the harness
#: executes them.  Module-level so the report can assert full coverage and
#: docs/tests share one source of truth.  These are the "soak test and
#: failure drill artifacts" the issue's verification criterion names.
SOAK_ARTIFACT_IDS = (
    "scheduled_cycles",   # the N-cycle soak (N = 7 for acceptance)
    "source_disable",     # kill switch: register disable pauses + refuses
    "health_alert",       # watchdog breach -> exactly one alert -> recovery
    "checkpoint_backup",  # export + loss + verified restore of checkpoint
    "reparse",            # idempotent reparse from the raw lake
    "failure_drill",      # the composed deliberate-failure drill
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


class CycleStatus(str, enum.Enum):
    """Outcome of one scheduled cycle / artifact."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass
class CycleResultV1:
    """The measured outcome of one scheduled collection cycle.

    Fields
    ------
    cycle
        1-based cycle number within the soak.
    source_slug
        The governed source the cycle ran.
    scheduled_at / completed_at
        ISO-8601 timestamps bracketing the cycle.  ``scheduled_at`` is
        when the schedule fired; ``completed_at`` when the ledger row was
        closed and publication (if any) committed.
    duration_seconds
        Wall-clock duration of the cycle.
    slo_seconds
        The cycle SLO budget this cycle was evaluated against.
    within_slo
        ``True`` iff ``duration_seconds <= slo_seconds``.
    status
        ``passed`` iff the cycle completed *and* landed within SLO.
    ledger_rows
        Run-ledger rows written by this cycle (must be exactly 1 for a
        single scheduled run — more indicates duplicate publication of
        the run record itself).
    records_new
        New consumer-visible records the cycle published.
    run_key
        The idempotency key for the run (``schedule:<slug>:<cycle>``).
    error
        Failure detail when ``status == failed``.
    """

    cycle: int
    source_slug: str
    status: str = CycleStatus.PASSED.value
    scheduled_at: str = field(default_factory=_now_iso)
    completed_at: str = field(default_factory=_now_iso)
    duration_seconds: float = 0.0
    slo_seconds: float = 0.0
    within_slo: bool = True
    ledger_rows: int = 0
    records_new: int = 0
    run_key: str = ""
    error: str = ""
    schema_version: str = OPS_SOAK_SCHEMA_VERSION

    def finalise(self) -> "CycleResultV1":
        """Compute ``status`` from ``within_slo`` / ``error`` and stamp it."""
        self.completed_at = _now_iso()
        self.status = (
            CycleStatus.PASSED.value
            if self.within_slo and not self.error
            else CycleStatus.FAILED.value
        )
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "cycle": self.cycle,
            "source_slug": self.source_slug,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "slo_seconds": self.slo_seconds,
            "within_slo": self.within_slo,
            "ledger_rows": self.ledger_rows,
            "records_new": self.records_new,
            "run_key": self.run_key,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CycleResultV1":
        return cls(
            cycle=int(d["cycle"]),
            source_slug=d["source_slug"],
            status=d.get("status", CycleStatus.PASSED.value),
            scheduled_at=d.get("scheduled_at", _now_iso()),
            completed_at=d.get("completed_at", _now_iso()),
            duration_seconds=float(d.get("duration_seconds", 0.0)),
            slo_seconds=float(d.get("slo_seconds", 0.0)),
            within_slo=bool(d.get("within_slo", True)),
            ledger_rows=int(d.get("ledger_rows", 0)),
            records_new=int(d.get("records_new", 0)),
            run_key=d.get("run_key", ""),
            error=d.get("error", ""),
            schema_version=d.get("schema_version", OPS_SOAK_SCHEMA_VERSION),
        )


@dataclass
class OpsSoakReportV1:
    """DP-06-05 handoff contract — the *signed* soak + failure-drill report.

    This is the deliverable: a tamper-evident record that the source ran
    continuously (seven consecutive scheduled cycles within SLO) and that a
    deliberate source failure was detected, alerted, and recovered without
    duplicate publication.

    Fields
    ------
    report_id
        Unique identifier for this soak run.
    source_slug
        The governed source under test.
    started_at / completed_at / duration_seconds
        Wall-clock bracket for the whole soak + drill.
    cycles
        One :class:`CycleResultV1` per scheduled cycle, in order.
    cycles_required
        The acceptance-criterion cycle count (7).
    cycle_slo_seconds
        The per-cycle SLO every cycle was evaluated against.
    cycles_within_slo
        How many cycles landed within SLO.
    consecutive_cycles_within_slo
        The longest run of consecutive within-SLO cycles (must equal
        ``cycles_required`` for the first acceptance criterion to hold).
    artifacts
        One status dict per artifact in :data:`SOAK_ARTIFACT_IDS`
        (``{"artifact": ..., "status": "passed"|"failed", "detail": ...}``).
    failure_drill
        Named boolean checks for the deliberate-failure drill (kill
        switch, alert, checkpoint backup, recovery, no duplicate
        publication).
    no_duplicate_publication
        Convenience roll-up of the drill's idempotency checks.
    overall_status
        ``passed`` iff every cycle passed and every drill check held.
    passed_acceptance_criteria
        The issue's two acceptance criteria, mapped to a boolean each.
    signature / signing_key_id
        HMAC-SHA256 signature over the canonical payload and the id of
        the key that produced it.  Empty until :func:`sign_report`.
    """

    report_id: str
    source_slug: str = ""
    started_at: str = field(default_factory=_now_iso)
    completed_at: str = field(default_factory=_now_iso)
    duration_seconds: float = 0.0
    cycles: list[CycleResultV1] = field(default_factory=list)
    cycles_required: int = 7
    cycle_slo_seconds: float = 0.0
    cycles_within_slo: int = 0
    consecutive_cycles_within_slo: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    failure_drill: dict[str, bool] = field(default_factory=dict)
    no_duplicate_publication: bool = False
    overall_status: str = CycleStatus.PASSED.value
    passed_acceptance_criteria: dict[str, bool] = field(default_factory=dict)
    signature: str = ""
    signing_key_id: str = ""
    schema_version: str = OPS_SOAK_SCHEMA_VERSION

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
            "source_slug": self.source_slug,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "cycles": [c.to_dict() for c in self.cycles],
            "cycles_required": self.cycles_required,
            "cycle_slo_seconds": self.cycle_slo_seconds,
            "cycles_within_slo": self.cycles_within_slo,
            "consecutive_cycles_within_slo": self.consecutive_cycles_within_slo,
            "artifacts": [dict(a) for a in self.artifacts],
            "failure_drill": dict(self.failure_drill),
            "no_duplicate_publication": self.no_duplicate_publication,
            "overall_status": self.overall_status,
            "passed_acceptance_criteria": dict(self.passed_acceptance_criteria),
            "signature": self.signature,
            "signing_key_id": self.signing_key_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OpsSoakReportV1":
        return cls(
            report_id=d["report_id"],
            source_slug=d.get("source_slug", ""),
            started_at=d.get("started_at", _now_iso()),
            completed_at=d.get("completed_at", _now_iso()),
            duration_seconds=float(d.get("duration_seconds", 0.0)),
            cycles=[CycleResultV1.from_dict(c) for c in d.get("cycles", [])],
            cycles_required=int(d.get("cycles_required", 7)),
            cycle_slo_seconds=float(d.get("cycle_slo_seconds", 0.0)),
            cycles_within_slo=int(d.get("cycles_within_slo", 0)),
            consecutive_cycles_within_slo=int(
                d.get("consecutive_cycles_within_slo", 0)
            ),
            artifacts=[dict(a) for a in d.get("artifacts", [])],
            failure_drill=dict(d.get("failure_drill", {})),
            no_duplicate_publication=bool(d.get("no_duplicate_publication", False)),
            overall_status=d.get("overall_status", CycleStatus.PASSED.value),
            passed_acceptance_criteria=dict(d.get("passed_acceptance_criteria", {})),
            signature=d.get("signature", ""),
            signing_key_id=d.get("signing_key_id", ""),
            schema_version=d.get("schema_version", OPS_SOAK_SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> "OpsSoakReportV1":
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def sign_report(report: OpsSoakReportV1, key: bytes, *, key_id: str = "") -> str:
    """Sign *report* in place with HMAC-SHA256 and return the signature.

    The signature covers the canonical payload (every field except
    ``signature``).  ``signing_key_id`` is set to *key_id* so verifiers
    know which key to use.
    """
    report.signing_key_id = key_id
    payload = _canonical_json(report._canonical_payload())
    report.signature = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return report.signature


def verify_report_signature(report: OpsSoakReportV1, key: bytes) -> bool:
    """Return ``True`` iff *report*'s signature is valid under *key*.

    Recomputes the HMAC over the canonical payload and compares with
    :func:`hmac.compare_digest` (constant-time).  A report whose payload
    has been tampered with — any cycle, any drill check, any acceptance
    criterion — fails verification.
    """
    if not report.signature:
        return False
    payload = _canonical_json(report._canonical_payload())
    expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, report.signature)
