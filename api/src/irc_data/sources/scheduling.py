"""Scheduling policy — cadence classes and per-source cadence/budget fields (OPS-01-01).

Goal: make *how often* and *how late is too late* explicit per source.

This module is the code of record for the scheduling policy in
``docs/SCHEDULING-POLICY.md`` (``sched-v1.0``).  It defines:

* :class:`CadenceClass` — the three cadence classes (``daily_results`` /
  ``weekly_certificates`` / ``annual_identifiers``) plus a ``manual`` class,
  each carrying the design defaults for staleness budget and retry/backoff.
* :class:`CadenceSpec` — the resolved, fully-explicit per-source scheduling
  contract consumed by the watchdog (OPS-01-04), the schedule registry
  (OPS-01-02) and the retry primitives.
* :class:`SchedulingPolicy` / :data:`SCHEDULING_POLICY` — the platform-wide
  policy singleton: watchdog interval (15 min), cooldown (4 h), nightly
  window (inherited from the collection policy, ``docs/SOURCE-POLICY.md``
  §4.3), kill-switch semantics, and register validation.
* :func:`validate_source_scheduling` — the register validator.  Every
  *active* source (``enabled`` and ``legal_status = 'approved'``) MUST carry
  values for cadence class, cadence, staleness budget, nightly window,
  retry/backoff, cooldown and the kill-switch acknowledgement window.
  Missing / malformed values are a hard validation failure.

Design defaults (SCHEDULING-POLICY.md §2): staleness budget 8 d for weekly
certificate lists; watchdog interval 15 min; alert cooldown 4 h; nightly
window 01:00–06:00 (source-local where known, else UTC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from typing import Any, Iterable, Mapping

from irc_data.sources.policy import (
    POLICY_AUTHORITY,
    POLICY_AUTHORITY_EMAIL,
)

# ---------------------------------------------------------------------------
# Policy identity / global constants
# ---------------------------------------------------------------------------

SCHEDULING_POLICY_VERSION = "sched-v1.0"
"""Version string of the scheduling policy (docs/SCHEDULING-POLICY.md)."""

SCHEDULING_POLICY_AUTHORITY = POLICY_AUTHORITY
"""The human authority whose approval gates this policy (Stuart McLeod)."""

SCHEDULING_POLICY_STATUS = "pending-approval"
"""Approval status of ``sched-v1.0``.  Acceptance requires Stuart's approval;
until then the policy is defined, enforced in code, and marked pending."""

WATCHDOG_INTERVAL_MINUTES = 15
"""How often the staleness watchdog evaluates every active source (cron)."""

DEFAULT_COOLDOWN_HOURS = 4
"""Default alert / re-run cooldown per source, in hours."""

DEFAULT_NIGHTLY_WINDOW: tuple[str, str] = ("01:00", "06:00")
"""Default nightly collection window, inherited from the collection policy
(``docs/SOURCE-POLICY.md`` §4.3: 01:00–06:00 source-local where the timezone
is known, else UTC)."""

TAKEDOWN_ACK_WINDOW_HOURS = 4
"""Kill-switch / takedown acknowledgement window (SOURCE-POLICY.md §5)."""

DEFAULT_MAX_ATTEMPTS = 3
"""Default retry attempts before a scheduled run is abandoned."""

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SchedulingPolicyError(Exception):
    """Raised when register scheduling fields are missing or invalid."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        msg = "Scheduling policy validation failed"
        if self.errors:
            msg += ": " + "; ".join(self.errors)
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Cadence classes
# ---------------------------------------------------------------------------


class CadenceClass(str, Enum):
    """Cadence classes defined by the scheduling policy (OPS-01-01 §2).

    ``DAILY_RESULTS``
        Daily results platforms — nightly (or faster) collection of race
        results that update daily during the season.
    ``WEEKLY_CERTIFICATES``
        Weekly certificate / rating lists — ratings and certificates that
        change on roughly a weekly cycle.
    ``ANNUAL_IDENTIFIERS``
        Annual identifier / event lists — sources whose meaningful content
        changes annually (annual regattas, identifier lists).
    ``MANUAL``
        No scheduled cadence — decommissioned or manual-trigger sources.
        Exempt from staleness alerting but still required to carry explicit
        values (the fields document *why* there is no cadence).
    """

    DAILY_RESULTS = "daily_results"
    WEEKLY_CERTIFICATES = "weekly_certificates"
    ANNUAL_IDENTIFIERS = "annual_identifiers"
    MANUAL = "manual"


#: Design defaults per cadence class (SCHEDULING-POLICY.md §2).
CADENCE_CLASS_DEFAULTS: Mapping[CadenceClass, Mapping[str, Any]] = {
    CadenceClass.DAILY_RESULTS: {
        "cadence": "nightly",
        "staleness_budget_hours": 48,
        "retry_max_attempts": 3,
        "retry_backoff_seconds": (600, 1800, 7200),
        "cooldown_hours": 4,
    },
    CadenceClass.WEEKLY_CERTIFICATES: {
        "cadence": "weekly",
        # Design example from the OPS-01-01 scope: "how late is too late"
        # for a weekly certificate list is 8 days (one cycle + 1 day slack).
        "staleness_budget_hours": 8 * 24,
        "retry_max_attempts": 3,
        "retry_backoff_seconds": (3600, 14400, 86400),
        "cooldown_hours": 4,
    },
    CadenceClass.ANNUAL_IDENTIFIERS: {
        "cadence": "annual",
        "staleness_budget_hours": 370 * 24,
        "retry_max_attempts": 1,
        "retry_backoff_seconds": (86400,),
        "cooldown_hours": 24,
    },
    CadenceClass.MANUAL: {
        "cadence": "manual",
        "staleness_budget_hours": 10 * 365 * 24,
        "retry_max_attempts": 1,
        "retry_backoff_seconds": (86400,),
        "cooldown_hours": 24,
    },
}


def classify_cadence(cadence: str | None) -> CadenceClass:
    """Classify a register ``cadence`` string into its :class:`CadenceClass`.

    The mapping is deterministic and conservative: unknown strings fall back
    to ``DAILY_RESULTS`` so a typo fails *loudly* at validation time (the
    resulting spec will almost certainly violate the register's expectations)
    rather than silently scheduling nothing.
    """
    key = (cadence or "").strip().lower()
    if key in ("manual", "decommissioned", "off", "annual-manual"):
        return CadenceClass.MANUAL
    if key in ("annual", "yearly", "365d", "370d"):
        return CadenceClass.ANNUAL_IDENTIFIERS
    if key in ("weekly", "7d", "fortnightly", "14d"):
        return CadenceClass.WEEKLY_CERTIFICATES
    # hourly / 30min / nightly / daily / quarterhourly / compact durations
    return CadenceClass.DAILY_RESULTS


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Per-source retry/backoff contract (OPS-01-01 §4).

    ``backoff_seconds`` is the delay sequence between attempts, e.g.
    ``(600, 1800, 7200)`` = 10 min, 30 min, 2 h.
    """

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: tuple[int, ...] = (600, 1800, 7200)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if not self.backoff_seconds:
            raise ValueError("backoff_seconds must not be empty")
        if any(s <= 0 for s in self.backoff_seconds):
            raise ValueError("backoff_seconds entries must be positive")

    def delay_for_attempt(self, attempt: int) -> int:
        """Return the backoff delay (seconds) before retry *attempt* (1-based).

        The last configured delay repeats when attempts exceed the sequence.
        """
        idx = min(max(attempt - 1, 0), len(self.backoff_seconds) - 1)
        return self.backoff_seconds[idx]

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_seconds": list(self.backoff_seconds),
        }

    @classmethod
    def from_value(cls, value: Any) -> "RetryPolicy":
        """Coerce a mapping / RetryPolicy into a :class:`RetryPolicy`."""
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                max_attempts=int(value.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
                backoff_seconds=tuple(
                    int(s) for s in value.get("backoff_seconds", (600, 1800, 7200))
                ),
            )
        raise TypeError(f"Cannot build RetryPolicy from {type(value).__name__}")


# ---------------------------------------------------------------------------
# CadenceSpec — the resolved per-source scheduling contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CadenceSpec:
    """Fully-explicit scheduling contract for one source (OPS-01-01 §3).

    Produced by :meth:`SchedulingPolicy.spec_for`; consumed by the watchdog
    (staleness budget), the schedule registry (cadence + window) and the
    retry layer (retry policy, cooldown).
    """

    slug: str
    cadence_class: CadenceClass
    cadence: str
    staleness_budget_hours: float
    nightly_window_start: time
    nightly_window_end: time
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS
    watchdog_interval_minutes: int = WATCHDOG_INTERVAL_MINUTES
    policy_version: str = SCHEDULING_POLICY_VERSION

    def is_stale(self, last_success_age_hours: float | None) -> bool:
        """True when *last_success_age_hours* exceeds the staleness budget.

        ``None`` (no successful run on record) is always stale.
        """
        if last_success_age_hours is None:
            return True
        return last_success_age_hours > self.staleness_budget_hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "cadence_class": self.cadence_class.value,
            "cadence": self.cadence,
            "staleness_budget_hours": self.staleness_budget_hours,
            "nightly_window": (
                f"{self.nightly_window_start:%H:%M}"
                f"-{self.nightly_window_end:%H:%M}"
            ),
            "retry_policy": self.retry_policy.to_dict(),
            "cooldown_hours": self.cooldown_hours,
            "watchdog_interval_minutes": self.watchdog_interval_minutes,
            "policy_version": self.policy_version,
        }


# ---------------------------------------------------------------------------
# Kill-switch semantics
# ---------------------------------------------------------------------------


class KillSwitchTrigger(str, Enum):
    """What tripped the kill switch (SCHEDULING-POLICY.md §6)."""

    SOURCE_DISABLED = "source_disabled"   # data_sources.enabled = FALSE
    DOMAIN_DISABLED = "domain_disabled"   # domain_disables row
    GLOBAL_ENV = "global_env"             # COLLECTION_ENABLED=false
    QUARANTINE = "quarantine"             # monitor / incident quarantine
    TAKEDOWN = "takedown"                 # operator takedown request


@dataclass(frozen=True)
class KillSwitchPolicy:
    """Kill-switch semantics as a policy block (OPS-01-01 §6).

    Any trigger halts scheduling *and* in-flight collection for the source
    on the next gate evaluation.  Takedowns must be acknowledged within
    :attr:`ack_window_hours`; re-enable requires written approval from the
    policy authority.
    """

    ack_window_hours: int = TAKEDOWN_ACK_WINDOW_HOURS
    re_enable_requires: str = POLICY_AUTHORITY
    contact: str = POLICY_AUTHORITY_EMAIL
    halt_in_flight: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ack_window_hours": self.ack_window_hours,
            "re_enable_requires": self.re_enable_requires,
            "contact": self.contact,
            "halt_in_flight": self.halt_in_flight,
        }


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

#: Scheduling fields every *active* register row must carry (OPS-01-01 §3).
REQUIRED_SCHEDULING_FIELDS: tuple[str, ...] = (
    "cadence_class",
    "cadence",
    "staleness_budget_hours",
    "nightly_window_start",
    "nightly_window_end",
    "retry_policy",
    "cooldown_hours",
    "kill_switch_ack_hours",
)


def parse_hhmm(value: Any) -> time | None:
    """Parse an ``HH:MM`` string (or ``time``) into a ``time``; None if bad."""
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        m = _HHMM_RE.match(value.strip())
        if m:
            return time(hour=int(m.group(1)), minute=int(m.group(2)))
    return None


def _validate_retry_policy(value: Any, slug: str, errors: list[str]) -> RetryPolicy | None:
    try:
        return RetryPolicy.from_value(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"{slug}: retry_policy invalid ({exc})")
        return None


def validate_source_scheduling(
    source: Any,
    *,
    require_when_inactive: bool = False,
) -> list[str]:
    """Validate the OPS-01-01 scheduling fields of one register record.

    *source* may be a ``DataSourceRecordV1``, an ORM ``DataSource`` row, or
    any object / mapping with the register attributes.

    Every **active** source (``enabled`` and ``legal_status == 'approved'``)
    must carry values for all :data:`REQUIRED_SCHEDULING_FIELDS`.  Inactive
    sources are exempt unless *require_when_inactive* is set (the register
    seed sets them anyway so a re-activated source is immediately valid).

    Returns a list of human-readable error strings; empty == valid.
    """
    get = source.get if isinstance(source, Mapping) else lambda k, d=None: getattr(source, k, d)
    slug = get("slug", "<unknown>") or "<unknown>"
    enabled = bool(get("enabled", True))
    legal_status = get("legal_status", "") or ""
    status_val = getattr(legal_status, "value", legal_status)

    active = enabled and status_val == "approved"
    if not active and not require_when_inactive:
        return []

    errors: list[str] = []

    # -- cadence class ---------------------------------------------------
    cadence_class_raw = get("cadence_class")
    cc_val = getattr(cadence_class_raw, "value", cadence_class_raw)
    if not cc_val:
        errors.append(f"{slug}: cadence_class is required")
    elif cc_val not in {c.value for c in CadenceClass}:
        errors.append(f"{slug}: cadence_class {cc_val!r} is not a valid cadence class")

    # -- cadence ---------------------------------------------------------
    if not (get("cadence") or "").strip():
        errors.append(f"{slug}: cadence is required")

    # -- staleness budget -------------------------------------------------
    budget = get("staleness_budget_hours")
    if budget is None:
        errors.append(f"{slug}: staleness_budget_hours is required")
    else:
        try:
            if float(budget) <= 0:
                errors.append(f"{slug}: staleness_budget_hours must be > 0")
        except (TypeError, ValueError):
            errors.append(f"{slug}: staleness_budget_hours {budget!r} is not a number")

    # -- nightly window ---------------------------------------------------
    start_raw = get("nightly_window_start")
    end_raw = get("nightly_window_end")
    start = parse_hhmm(start_raw)
    end = parse_hhmm(end_raw)
    if start is None:
        errors.append(f"{slug}: nightly_window_start {start_raw!r} missing or not HH:MM")
    if end is None:
        errors.append(f"{slug}: nightly_window_end {end_raw!r} missing or not HH:MM")

    # -- retry / backoff ---------------------------------------------------
    retry = get("retry_policy")
    if retry is None:
        errors.append(f"{slug}: retry_policy is required")
    else:
        _validate_retry_policy(retry, slug, errors)

    # -- cooldown ----------------------------------------------------------
    cooldown = get("cooldown_hours")
    if cooldown is None:
        errors.append(f"{slug}: cooldown_hours is required")
    else:
        try:
            if float(cooldown) <= 0:
                errors.append(f"{slug}: cooldown_hours must be > 0")
        except (TypeError, ValueError):
            errors.append(f"{slug}: cooldown_hours {cooldown!r} is not a number")

    # -- kill-switch ack window ---------------------------------------------
    ack = get("kill_switch_ack_hours")
    if ack is None:
        errors.append(f"{slug}: kill_switch_ack_hours is required")
    else:
        try:
            if float(ack) <= 0:
                errors.append(f"{slug}: kill_switch_ack_hours must be > 0")
        except (TypeError, ValueError):
            errors.append(f"{slug}: kill_switch_ack_hours {ack!r} is not a number")

    return errors


# ---------------------------------------------------------------------------
# SchedulingPolicy — the platform policy object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulingPolicy:
    """Platform scheduling policy (OPS-01-01 / SCHEDULING-POLICY.md).

    Carries the global knobs (watchdog interval, cooldown default, nightly
    window default, kill-switch semantics) and resolves per-source
    :class:`CadenceSpec` contracts from register records.
    """

    version: str = SCHEDULING_POLICY_VERSION
    status: str = SCHEDULING_POLICY_STATUS
    authority: str = SCHEDULING_POLICY_AUTHORITY
    watchdog_interval_minutes: int = WATCHDOG_INTERVAL_MINUTES
    default_cooldown_hours: int = DEFAULT_COOLDOWN_HOURS
    nightly_window: tuple[str, str] = DEFAULT_NIGHTLY_WINDOW
    kill_switch: KillSwitchPolicy = field(default_factory=KillSwitchPolicy)

    # ------------------------------------------------------------------
    # Per-source resolution
    # ------------------------------------------------------------------

    def spec_for(self, source: Any) -> CadenceSpec:
        """Resolve the :class:`CadenceSpec` for one register record.

        Explicit register values win; missing values fall back to the
        cadence-class design defaults so a partially-populated record still
        yields a usable (and policy-conformant) spec.  Use
        :func:`validate_source_scheduling` to enforce completeness where the
        acceptance criteria require it.
        """
        get = source.get if isinstance(source, Mapping) else lambda k, d=None: getattr(source, k, d)
        slug = get("slug", "<unknown>") or "<unknown>"

        cadence = (get("cadence") or "").strip()
        cadence_class_raw = get("cadence_class")
        cc_val = getattr(cadence_class_raw, "value", cadence_class_raw)
        cadence_class = (
            CadenceClass(cc_val) if cc_val in {c.value for c in CadenceClass}
            else classify_cadence(cadence)
        )
        defaults = CADENCE_CLASS_DEFAULTS[cadence_class]

        budget = get("staleness_budget_hours")
        cooldown = get("cooldown_hours")
        retry_raw = get("retry_policy")
        start = parse_hhmm(get("nightly_window_start")) or parse_hhmm(self.nightly_window[0])
        end = parse_hhmm(get("nightly_window_end")) or parse_hhmm(self.nightly_window[1])

        if retry_raw is not None:
            retry_policy = RetryPolicy.from_value(retry_raw)
        else:
            retry_policy = RetryPolicy(
                max_attempts=int(defaults["retry_max_attempts"]),
                backoff_seconds=tuple(int(s) for s in defaults["retry_backoff_seconds"]),
            )

        return CadenceSpec(
            slug=slug,
            cadence_class=cadence_class,
            cadence=cadence or str(defaults["cadence"]),
            staleness_budget_hours=(
                float(budget) if budget is not None else float(defaults["staleness_budget_hours"])
            ),
            nightly_window_start=start,  # type: ignore[arg-type]
            nightly_window_end=end,      # type: ignore[arg-type]
            retry_policy=retry_policy,
            cooldown_hours=(
                float(cooldown) if cooldown is not None else float(defaults["cooldown_hours"])
            ),
            watchdog_interval_minutes=self.watchdog_interval_minutes,
            policy_version=self.version,
        )

    # ------------------------------------------------------------------
    # Register validation (acceptance criterion: every active source has values)
    # ------------------------------------------------------------------

    def validate_register(
        self,
        sources: Iterable[Any],
        *,
        raise_on_error: bool = False,
    ) -> dict[str, list[str]]:
        """Validate scheduling fields across the register.

        Returns ``{slug: [errors...]}`` for sources with problems.  When
        *raise_on_error* is true and any source fails, raises
        :class:`SchedulingPolicyError` with the full error list.
        """
        failures: dict[str, list[str]] = {}
        for source in sources:
            errors = validate_source_scheduling(source)
            if errors:
                get = source.get if isinstance(source, Mapping) else lambda k, d=None: getattr(source, k, d)
                failures[get("slug", "<unknown>") or "<unknown>"] = errors
        if failures and raise_on_error:
            raise SchedulingPolicyError(
                err for errs in failures.values() for err in errs
            )
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "authority": self.authority,
            "watchdog_interval_minutes": self.watchdog_interval_minutes,
            "default_cooldown_hours": self.default_cooldown_hours,
            "nightly_window": list(self.nightly_window),
            "kill_switch": self.kill_switch.to_dict(),
            "cadence_class_defaults": {
                c.value: {
                    "cadence": d["cadence"],
                    "staleness_budget_hours": d["staleness_budget_hours"],
                    "retry_max_attempts": d["retry_max_attempts"],
                    "retry_backoff_seconds": list(d["retry_backoff_seconds"]),
                    "cooldown_hours": d["cooldown_hours"],
                }
                for c, d in CADENCE_CLASS_DEFAULTS.items()
            },
        }


#: The active scheduling policy.  Import this everywhere scheduling
#: enforcement is needed (watchdog, schedule registry, register validation).
SCHEDULING_POLICY = SchedulingPolicy()


__all__ = [
    "SCHEDULING_POLICY",
    "SCHEDULING_POLICY_VERSION",
    "SCHEDULING_POLICY_AUTHORITY",
    "SCHEDULING_POLICY_STATUS",
    "WATCHDOG_INTERVAL_MINUTES",
    "DEFAULT_COOLDOWN_HOURS",
    "DEFAULT_NIGHTLY_WINDOW",
    "TAKEDOWN_ACK_WINDOW_HOURS",
    "DEFAULT_MAX_ATTEMPTS",
    "REQUIRED_SCHEDULING_FIELDS",
    "CADENCE_CLASS_DEFAULTS",
    "CadenceClass",
    "CadenceSpec",
    "KillSwitchPolicy",
    "KillSwitchTrigger",
    "RetryPolicy",
    "SchedulingPolicy",
    "SchedulingPolicyError",
    "classify_cadence",
    "parse_hhmm",
    "validate_source_scheduling",
]
