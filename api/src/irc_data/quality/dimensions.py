"""Data-quality dimensions, thresholds and ownership (DP-05-01).

**Goal: make database health measurable.**

This module is the code of record for the eight data-quality dimensions,
their per-dataset / per-field **blocking** and **warning** thresholds, the
accountable **owner** of every published dataset, its **SLO**, and the
**remediation playbook** attached to every rule.

The eight dimensions
--------------------

``completeness``
    Expected data is present: required identity fields are non-null,
    expected columns arrive in the batch, row counts are non-empty.
``validity``
    Values are in-domain: ratings within plausible bounds, dates parse
    and are in-range, enums come from the registered vocabulary,
    patterns match.
``uniqueness``
    No unintended duplicates: primary identity keys are unique within a
    batch; composite keys unique where the source can legitimately
    repeat a bare label (e.g. sail numbers re-issued across eras).
``consistency``
    Cross-field / cross-record agreement: a non-spinnaker TCC never
    exceeds the all-up TCC; ``place`` never exceeds ``fleet_size``;
    cert year agrees with issue date.
``timeliness``
    Freshness against the source cadence (SCHEDULING-POLICY staleness
    budgets): how long since the last successful batch landed.
``provenance``
    Every record cites its raw artifact (id + content hash, DP-02-01)
    and its source slug is registered and approved (DP-01-03).
``identity_confidence``
    The fraction of records that resolve to a known canonical entity at
    or above the minimum match confidence, and the absence of
    low-confidence auto-merges.
``drift``
    Statistical drift vs the trailing historical distribution of a
    field (mean z-score, out-of-range mass) and batch row-count drift
    vs the trailing count median.  Baselines are *built from real
    historical snapshots* — see ``docs/data-quality/dimensions.md``
    §"Verification against real historical distributions".

Relationship to the rest of the quality stack
---------------------------------------------

* **DP-05-02 gates** (``irc_data.quality.gates``) validate batch
  *envelope* integrity per pipeline run (schema, determinism, lineage).
  This module validates *dataset content health* — the numbers a human
  would graph on a dashboard.  A ``block``-status
  :class:`DimensionReportV1` is a promotion-blocking signal in exactly
  the same sense as a DP-05-03 reconciliation ``decision='block'``:
  :func:`assert_dataset_publishable` raises before promotion.
* **DP-05-03 reconciliation** detects silent *count* loss between
  stages; the ``drift.row_count`` rules here detect abrupt *volume*
  change between batches of the same dataset.  Both read the same
  trailing-window idea (DP-05-03 uses a p10 floor over ≥3 samples;
  drift baselines here require ``min_samples`` before blocking).

Output contracts
----------------

* :class:`ThresholdRule` — one measurable rule: id, dataset, field,
  dimension, severity, metric, thresholds, owner, SLO, playbook.
* :class:`RuleResult` — the outcome of evaluating one rule against a
  batch (status ``pass`` | ``warn`` | ``block`` | ``skip``, measured
  value, bounded offender sample).
* :class:`DimensionReportV1` — the per-dataset aggregate report
  (JSON round-trippable; worst status wins).

Acceptance criteria enforced by :func:`validate_registry`
---------------------------------------------------------

*Every published dataset has blocking and warning rules, an accountable
owner, an SLO and a remediation playbook* — the registry refuses to
load (raises :class:`RegistryError`) if any published dataset:

* lacks a blocking rule or a warning rule,
* leaves any of the eight dimensions uncovered,
* has a rule without an owner, SLO or playbook,
* carries an SLO with a non-positive target or a warning threshold
  looser than its blocking threshold.
"""

from __future__ import annotations

import enum
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "dq-dimensions-v1"

#: Maximum number of offending-record references kept on a result sample.
MAX_SAMPLE = 25


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryError(ValueError):
    """Raised when the rule registry fails completeness validation."""


class BlockingRuleViolation(RuntimeError):
    """Raised by :func:`assert_dataset_publishable` when a blocking rule
    fired — the dataset must not be promoted/published."""


# ---------------------------------------------------------------------------
# The eight dimensions
# ---------------------------------------------------------------------------


class QualityDimension(str, enum.Enum):
    """The eight data-quality dimensions defined by DP-05-01."""

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    CONSISTENCY = "consistency"
    TIMELINESS = "timeliness"
    PROVENANCE = "provenance"
    IDENTITY_CONFIDENCE = "identity_confidence"
    DRIFT = "drift"


class Severity(str, enum.Enum):
    """Rule severity.

    ``BLOCKING``
        A failure prevents the dataset from being published / promoted
        (the batch is blocked until remediated).
    ``WARNING``
        A failure is recorded, counted against the SLO error budget and
        surfaced to the owner, but does not by itself block publication.
    """

    BLOCKING = "blocking"
    WARNING = "warning"


class MetricKind(str, enum.Enum):
    """The measurable quantity a rule thresholds.

    All metrics are *badness scores* — higher is worse — so threshold
    comparison is uniform: ``value >= warn_at`` fires a warning,
    ``value >= block_at`` blocks.
    """

    NULL_FRACTION = "null_fraction"
    """Fraction of records where ``field`` is null/blank."""

    OUT_OF_RANGE_FRACTION = "out_of_range_fraction"
    """Fraction of non-null numeric values outside ``[min, max]``."""

    ENUM_VIOLATION_FRACTION = "enum_violation_fraction"
    """Fraction of non-null values not in ``allowed``."""

    REGEX_VIOLATION_FRACTION = "regex_violation_fraction"
    """Fraction of non-null values not matching ``pattern``."""

    DUPLICATE_FRACTION = "duplicate_fraction"
    """``(n - n_distinct_keys) / n`` for the rule's key ``fields``."""

    CROSS_FIELD_VIOLATION_FRACTION = "cross_field_violation_fraction"
    """Fraction of comparable records violating the named predicate."""

    FRESHNESS_LAG_DAYS = "freshness_lag_days"
    """Days between ``context['as_of']`` and ``context['last_batch_at']``."""

    PROVENANCE_GAP_FRACTION = "provenance_gap_fraction"
    """Fraction of records missing any of the provenance ``fields``."""

    UNMATCHED_FRACTION = "unmatched_fraction"
    """Fraction of records whose identity confidence < ``min_confidence``."""

    DISTRIBUTION_Z = "distribution_z"
    """|z-score| of the batch field mean vs the historical baseline."""

    COUNT_DRIFT_FRACTION = "count_drift_fraction"
    """|count - baseline_median| / baseline_median for the batch."""


# ---------------------------------------------------------------------------
# Ownership / SLO / playbook
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Owner:
    """The accountable owner of a dataset or rule.

    ``handle`` is the mailto-able address paged on a blocking breach;
    ``escalation`` is who is paged when the owner does not ack within
    the SLO window.  Final escalation for every dataset is the platform
    authority (SOURCE-POLICY: Stuart McLeod, ``stuart@sailratings.com``).
    """

    handle: str
    role: str
    escalation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "handle": self.handle,
            "role": self.role,
            "escalation": self.escalation,
        }


#: Platform authority — final escalation for every dataset (SOURCE-POLICY §0).
PLATFORM_AUTHORITY = "stuart@sailratings.com"

OWNER_DATA_PLATFORM = Owner(
    handle="data-platform@sailratings.com",
    role="Data Platform Lead",
    escalation=PLATFORM_AUTHORITY,
)
OWNER_INGESTION = Owner(
    handle="ingestion-ops@sailratings.com",
    role="Ingestion / Scrapers On-Call",
    escalation=OWNER_DATA_PLATFORM.handle,
)
OWNER_IDENTITY = Owner(
    handle="identity-resolution@sailratings.com",
    role="Identity Resolution Maintainer",
    escalation=OWNER_DATA_PLATFORM.handle,
)


@dataclass(frozen=True)
class SLO:
    """Service-level objective attached to a rule.

    ``target`` is the minimum fraction of batches evaluated over the
    rolling ``window_days`` that must pass the rule; the *error budget*
    is ``1 - target``.  E.g. ``SLO(0.99, 30)`` tolerates roughly one
    failure per hundred daily batches per month.
    """

    target: float
    window_days: int = 30

    def __post_init__(self) -> None:
        if not (0.0 < self.target <= 1.0):
            raise ValueError(f"SLO target must be in (0, 1], got {self.target}")
        if self.window_days <= 0:
            raise ValueError("SLO window_days must be positive")

    @property
    def error_budget(self) -> float:
        return 1.0 - self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "window_days": self.window_days,
            "error_budget": self.error_budget,
        }


@dataclass(frozen=True)
class PlaybookRef:
    """Remediation playbook attached to a rule.

    The full human-readable text lives in
    ``docs/data-quality/dimensions.md`` under the playbook id; the
    ``steps`` tuple carries the operative runbook so an on-call can act
    from the alert payload alone.
    """

    playbook_id: str
    summary: str
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "summary": self.summary,
            "steps": list(self.steps),
        }


# ---------------------------------------------------------------------------
# Historical baseline (drift)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldBaseline:
    """Historical distribution statistics for one numeric field.

    Built from real historical snapshots by
    :func:`build_field_baseline`; the ``source`` string records *which*
    snapshots produced the numbers (audit / reproducibility of the
    verification criterion).
    """

    field: str
    mean: float
    std: float
    minimum: float
    maximum: float
    samples: int
    source: str = ""

    def z_score(self, value: float) -> float:
        if self.std <= 0:
            return 0.0 if value == self.mean else float("inf")
        return (value - self.mean) / self.std

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "mean": self.mean,
            "std": self.std,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "samples": self.samples,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FieldBaseline":
        return cls(
            field=d["field"],
            mean=float(d["mean"]),
            std=float(d["std"]),
            minimum=float(d["minimum"]),
            maximum=float(d["maximum"]),
            samples=int(d["samples"]),
            source=d.get("source", ""),
        )


def build_field_baseline(
    field: str,
    values: Sequence[float],
    *,
    source: str = "",
) -> FieldBaseline:
    """Construct a :class:`FieldBaseline` from observed historical values."""
    if len(values) < 2:
        raise ValueError(
            f"need >= 2 historical samples to baseline {field!r}, got {len(values)}"
        )
    return FieldBaseline(
        field=field,
        mean=statistics.fmean(values),
        std=statistics.stdev(values),
        minimum=min(values),
        maximum=max(values),
        samples=len(values),
        source=source,
    )


# ---------------------------------------------------------------------------
# ThresholdRule — one measurable data-quality rule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdRule:
    """One measurable data-quality rule bound to a dataset (and optionally
    a field).

    Attributes
    ----------
    rule_id
        Stable dotted id: ``<dataset>.<dimension>.<name>``.
    dataset
        The published dataset slug the rule guards (e.g.
        ``"tcc_listing"``).
    field_name
        The field under measurement, or ``None`` for dataset-level
        rules (timeliness, row-count drift, …).
    dimension / severity / metric
        Classification and measured quantity.
    warn_at / block_at
        Badness thresholds (see :class:`MetricKind`).  ``warn_at`` may
        be ``None`` for dimensions that are absolute (provenance), and
        ``block_at`` may be ``None`` for advisory-only rules — but every
        rule carries at least one threshold, and when both exist
        ``warn_at <= block_at``.
    params
        Metric-specific parameters (``min``/``max``, ``allowed``,
        ``pattern``, key ``fields``, ``predicate``, ``min_confidence``,
        ``baseline``, …).
    owner / slo / playbook
        The accountability triple required by the acceptance criteria.
    rationale
        Why the threshold is what it is — grounded in the historical
        review (``docs/data-quality/dimensions.md`` §Verification).
    """

    rule_id: str
    dataset: str
    field_name: str | None
    dimension: QualityDimension
    severity: Severity
    metric: MetricKind
    warn_at: float | None
    block_at: float | None
    owner: Owner
    slo: SLO
    playbook: PlaybookRef
    params: Mapping[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.warn_at is None and self.block_at is None:
            raise ValueError(f"{self.rule_id}: at least one threshold required")
        if (
            self.warn_at is not None
            and self.block_at is not None
            and self.warn_at > self.block_at
        ):
            raise ValueError(
                f"{self.rule_id}: warn_at ({self.warn_at}) must be <= "
                f"block_at ({self.block_at})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dataset": self.dataset,
            "field": self.field_name,
            "dimension": self.dimension.value,
            "severity": self.severity.value,
            "metric": self.metric.value,
            "warn_at": self.warn_at,
            "block_at": self.block_at,
            "params": {k: _jsonable(v) for k, v in self.params.items()},
            "owner": self.owner.to_dict(),
            "slo": self.slo.to_dict(),
            "playbook": self.playbook.to_dict(),
            "rationale": self.rationale,
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, FieldBaseline):
        return v.to_dict()
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, Mapping):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


# ---------------------------------------------------------------------------
# Cross-field consistency predicates (named, testable, reusable)
# ---------------------------------------------------------------------------

#: A predicate receives one record (mapping) and returns ``True`` when the
#: record is *consistent*.  Records lacking the referenced fields are not
#: comparable and are excluded from both numerator and denominator.
ConsistencyPredicate = Callable[[Mapping[str, Any]], bool]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pred_non_spi_le_tcc(r: Mapping[str, Any]) -> bool:
    """A non-spinnaker TCC never exceeds the all-up TCC."""
    return _num(r.get("non_spi_tcc")) <= _num(r.get("tcc"))  # type: ignore[operator]


def _pred_place_le_fleet_size(r: Mapping[str, Any]) -> bool:
    """A finishing place never exceeds the fleet size."""
    return _num(r.get("place")) <= _num(r.get("fleet_size"))  # type: ignore[operator]


def _pred_class_place_le_class_fleet(r: Mapping[str, Any]) -> bool:
    return _num(r.get("class_place")) <= _num(r.get("class_fleet_size"))  # type: ignore[operator]


def _pred_cert_year_matches_issue_date(r: Mapping[str, Any]) -> bool:
    """cert_year within ±1 of the issue-date year (year boundary slack)."""
    cy = _num(r.get("cert_year"))
    if cy is None:
        return True
    raw = str(r.get("issue_date") or "").strip()
    year: int | None = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            year = datetime.strptime(raw, fmt).year
            break
        except ValueError:
            continue
    if year is None:
        return True  # unparseable date is a *validity* finding, not consistency
    return abs(int(cy) - year) <= 1


#: Named cross-field predicates.  Each entry documents which record fields
#: make a record *comparable* for the predicate.
CONSISTENCY_PREDICATES: dict[str, tuple[tuple[str, ...], ConsistencyPredicate]] = {
    "non_spi_le_tcc": (("non_spi_tcc", "tcc"), _pred_non_spi_le_tcc),
    "place_le_fleet_size": (("place", "fleet_size"), _pred_place_le_fleet_size),
    "class_place_le_class_fleet": (
        ("class_place", "class_fleet_size"),
        _pred_class_place_le_class_fleet,
    ),
    "cert_year_matches_issue_date": (
        ("cert_year", "issue_date"),
        _pred_cert_year_matches_issue_date,
    ),
}


# ---------------------------------------------------------------------------
# Evaluation engine
# ---------------------------------------------------------------------------


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _key_of(record: Mapping[str, Any], fields: Sequence[str]) -> tuple:
    return tuple(str(record.get(f, "")).strip().upper() for f in fields)


def _measure(
    rule: ThresholdRule,
    records: list[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> tuple[float | None, int, int, list[Any]]:
    """Compute ``(value, evaluated, failing, sample)`` for a rule.

    ``value is None`` means *not measurable in this context* (e.g. a
    drift rule without a baseline, or a timeliness rule without a
    ``last_batch_at``) — the rule is reported as ``skip``.
    """
    metric = rule.metric
    p = rule.params

    # Optional scoping: ``applies_when`` restricts the rule to records
    # matching every ``field: value`` pair (e.g. IRC-only rating checks).
    applies_when = p.get("applies_when")
    if applies_when:
        records = [
            r
            for r in records
            if all(
                str(r.get(k, "")).strip().lower() == str(v).strip().lower()
                for k, v in applies_when.items()
            )
        ]
    n = len(records)

    if metric is MetricKind.NULL_FRACTION:
        f = rule.field_name
        assert f, rule.rule_id
        bad = [r for r in records if _is_blank(r.get(f))]
        return (len(bad) / n if n else 0.0), n, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.OUT_OF_RANGE_FRACTION:
        f = rule.field_name
        assert f, rule.rule_id
        lo, hi = float(p["min"]), float(p["max"])
        vals = [(r, _num(r.get(f))) for r in records]
        vals = [(r, v) for r, v in vals if v is not None]
        bad = [r for r, v in vals if not (lo <= v <= hi)]  # type: ignore[operator]
        m = len(vals)
        return (len(bad) / m if m else 0.0), m, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.ENUM_VIOLATION_FRACTION:
        f = rule.field_name
        assert f, rule.rule_id
        allowed = {str(a) for a in p["allowed"]}
        vals = [r for r in records if not _is_blank(r.get(f))]
        bad = [r for r in vals if str(r.get(f)).strip() not in allowed]
        m = len(vals)
        return (len(bad) / m if m else 0.0), m, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.REGEX_VIOLATION_FRACTION:
        f = rule.field_name
        assert f, rule.rule_id
        rx = re.compile(str(p["pattern"]))
        vals = [r for r in records if not _is_blank(r.get(f))]
        bad = [r for r in vals if not rx.match(str(r.get(f)).strip())]
        m = len(vals)
        return (len(bad) / m if m else 0.0), m, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.DUPLICATE_FRACTION:
        key_fields = tuple(p.get("fields") or ([rule.field_name] if rule.field_name else ()))
        if not key_fields:
            raise ValueError(f"{rule.rule_id}: duplicate rule needs key fields")
        keyed = [r for r in records if not any(_is_blank(r.get(f)) for f in key_fields)]
        keys = [_key_of(r, key_fields) for r in keyed]
        dups = len(keys) - len(set(keys))
        m = len(keys)
        return (dups / m if m else 0.0), m, dups, _sample_keys(rule, _dup_rows(keyed, keys))

    if metric is MetricKind.CROSS_FIELD_VIOLATION_FRACTION:
        name = str(p["predicate"])
        need, fn = CONSISTENCY_PREDICATES[name]
        comparable = [
            r for r in records if all(not _is_blank(r.get(f)) for f in need)
        ]
        bad = [r for r in comparable if not fn(r)]
        m = len(comparable)
        return (len(bad) / m if m else 0.0), m, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.FRESHNESS_LAG_DAYS:
        as_of = context.get("as_of")
        last = context.get("last_batch_at")
        if as_of is None or last is None:
            return None, 0, 0, []
        lag = (as_of - last).total_seconds() / 86400.0
        return max(0.0, lag), 1, 0, []

    if metric is MetricKind.PROVENANCE_GAP_FRACTION:
        prov_fields = tuple(p.get("fields") or ("artifact_id", "content_hash"))
        # Records that structurally lack the provenance fields (e.g. raw
        # snapshots evaluated offline, before the pipeline attaches
        # envelopes) make the rule un-measurable → skip, never pass.
        if records and not any(any(f in r for f in prov_fields) for r in records):
            return None, 0, 0, []
        bad = [r for r in records if any(_is_blank(r.get(f)) for f in prov_fields)]
        return (len(bad) / n if n else 0.0), n, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.UNMATCHED_FRACTION:
        min_conf = float(p.get("min_confidence", 0.8))
        conf_field = str(p.get("confidence_field", "identity_confidence"))
        vals = [(r, _num(r.get(conf_field))) for r in records]
        vals = [(r, v) for r, v in vals if v is not None]
        if not vals:
            return None, 0, 0, []
        bad = [r for r, v in vals if v < min_conf]  # type: ignore[operator]
        m = len(vals)
        return (len(bad) / m if m else 0.0), m, len(bad), _sample_keys(rule, bad)

    if metric is MetricKind.DISTRIBUTION_Z:
        baseline = p.get("baseline")
        f = rule.field_name
        assert f, rule.rule_id
        if not isinstance(baseline, FieldBaseline):
            return None, 0, 0, []
        vals = [v for v in (_num(r.get(f)) for r in records) if v is not None]
        if len(vals) < int(p.get("min_samples", 30)):
            return None, len(vals), 0, []
        batch_mean = statistics.fmean(vals)
        return abs(baseline.z_score(batch_mean)), len(vals), 0, []

    if metric is MetricKind.COUNT_DRIFT_FRACTION:
        baseline_counts = p.get("baseline_counts")
        if not baseline_counts or len(baseline_counts) < int(p.get("min_samples", 3)):
            return None, 0, 0, []
        med = statistics.median(float(c) for c in baseline_counts)
        if med <= 0:
            return None, 0, 0, []
        return abs(n - med) / med, 1, 0, []

    raise ValueError(f"{rule.rule_id}: unknown metric {metric!r}")


def _sample_keys(rule: ThresholdRule, rows: list[Mapping[str, Any]]) -> list[Any]:
    """Bounded sample of offending records for reviewer context."""
    key_fields = rule.params.get("sample_fields")
    if not key_fields:
        key_fields = tuple(
            f
            for f in (
                rule.field_name,
                "sail_number",
                "cert_number",
                "ref_no",
                "id",
            )
            if f
        )
    out: list[Any] = []
    for r in rows[:MAX_SAMPLE]:
        out.append({k: r.get(k) for k in key_fields if k in r} or dict(list(r.items())[:3]))
    return out


def _dup_rows(
    keyed: list[Mapping[str, Any]], keys: list[tuple]
) -> list[Mapping[str, Any]]:
    seen: set[tuple] = set()
    dups: list[Mapping[str, Any]] = []
    for r, k in zip(keyed, keys):
        if k in seen:
            dups.append(r)
        else:
            seen.add(k)
    return dups


def evaluate_rule(
    rule: ThresholdRule,
    records: Iterable[Mapping[str, Any]],
    context: Mapping[str, Any] | None = None,
) -> "RuleResult":
    """Evaluate one rule against a batch of records.

    ``records`` are dict-like rows of the dataset under test.  ``context``
    carries evaluation-time facts: ``as_of`` / ``last_batch_at``
    (timeliness), and any metric inputs not derivable from the rows.
    """
    rows = list(records)
    ctx = dict(context or {})
    value, evaluated, failing, sample = _measure(rule, rows, ctx)

    if value is None:
        status = "skip"
    elif rule.block_at is not None and value >= rule.block_at:
        status = "block"
    elif rule.warn_at is not None and value >= rule.warn_at:
        status = "warn"
    else:
        status = "pass"

    return RuleResult(
        rule_id=rule.rule_id,
        dataset=rule.dataset,
        field_name=rule.field_name,
        dimension=rule.dimension.value,
        severity=rule.severity.value,
        status=status,
        value=value,
        warn_at=rule.warn_at,
        block_at=rule.block_at,
        evaluated_count=evaluated,
        failing_count=failing,
        sample=sample,
    )


# ---------------------------------------------------------------------------
# RuleResult / DimensionReportV1 — output contracts
# ---------------------------------------------------------------------------


@dataclass
class RuleResult:
    """Outcome of evaluating one rule against one batch."""

    rule_id: str
    dataset: str
    field_name: str | None
    dimension: str
    severity: str
    status: str  # "pass" | "warn" | "block" | "skip"
    value: float | None
    warn_at: float | None
    block_at: float | None
    evaluated_count: int = 0
    failing_count: int = 0
    sample: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dataset": self.dataset,
            "field": self.field_name,
            "dimension": self.dimension,
            "severity": self.severity,
            "status": self.status,
            "value": self.value,
            "warn_at": self.warn_at,
            "block_at": self.block_at,
            "evaluated_count": self.evaluated_count,
            "failing_count": self.failing_count,
            "sample": self.sample,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RuleResult":
        return cls(
            rule_id=d["rule_id"],
            dataset=d["dataset"],
            field_name=d.get("field"),
            dimension=d["dimension"],
            severity=d["severity"],
            status=d["status"],
            value=d.get("value"),
            warn_at=d.get("warn_at"),
            block_at=d.get("block_at"),
            evaluated_count=int(d.get("evaluated_count", 0)),
            failing_count=int(d.get("failing_count", 0)),
            sample=list(d.get("sample") or []),
        )


@dataclass
class DimensionReportV1:
    """Per-dataset data-quality report (the DP-05-01 output contract).

    ``status`` is the worst rule outcome: any ``block`` → the dataset is
    not publishable; else any ``warn`` → publishable with findings.
    """

    dataset: str
    results: list[RuleResult]
    status: str
    blocking_failures: int
    warning_failures: int
    evaluated_at: str = field(default_factory=_now_iso)
    schema_version: str = SCHEMA_VERSION

    @property
    def publishable(self) -> bool:
        return self.status != "block"

    def by_dimension(self) -> dict[str, list[RuleResult]]:
        out: dict[str, list[RuleResult]] = {}
        for r in self.results:
            out.setdefault(r.dimension, []).append(r)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset": self.dataset,
            "status": self.status,
            "blocking_failures": self.blocking_failures,
            "warning_failures": self.warning_failures,
            "evaluated_at": self.evaluated_at,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DimensionReportV1":
        return cls(
            dataset=d["dataset"],
            results=[RuleResult.from_dict(r) for r in d.get("results", [])],
            status=d["status"],
            blocking_failures=int(d.get("blocking_failures", 0)),
            warning_failures=int(d.get("warning_failures", 0)),
            evaluated_at=d.get("evaluated_at", _now_iso()),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> "DimensionReportV1":
        return cls.from_dict(json.loads(s))


def evaluate_dataset(
    dataset: str,
    records: Iterable[Mapping[str, Any]],
    context: Mapping[str, Any] | None = None,
    *,
    rules: Sequence[ThresholdRule] | None = None,
) -> DimensionReportV1:
    """Evaluate every registered rule for *dataset* and aggregate the report."""
    selected = list(rules) if rules is not None else rules_for_dataset(dataset)
    if not selected:
        raise KeyError(f"no data-quality rules registered for dataset {dataset!r}")
    rows = list(records)
    results = [evaluate_rule(rule, rows, context) for rule in selected]
    blocking = [r for r in results if r.status == "block"]
    warning = [r for r in results if r.status == "warn"]
    status = "block" if blocking else ("warn" if warning else "pass")
    return DimensionReportV1(
        dataset=dataset,
        results=results,
        status=status,
        blocking_failures=len(blocking),
        warning_failures=len(warning),
    )


def assert_dataset_publishable(report: DimensionReportV1) -> None:
    """Raise :class:`BlockingRuleViolation` when the report is ``block``.

    The publish / promotion path (DP-05-02 ``promote_batch`` seam, the
    same place DP-05-03's ``assert_promotable`` is called) calls this
    before making a dataset version visible to consumers.
    """
    if report.status == "block":
        failed = [r.rule_id for r in report.results if r.status == "block"]
        raise BlockingRuleViolation(
            f"dataset {report.dataset!r} is not publishable: "
            f"blocking rules fired: {', '.join(failed)}"
        )


# ---------------------------------------------------------------------------
# Playbooks (full text in docs/data-quality/dimensions.md)
# ---------------------------------------------------------------------------

_PB_INGESTION_SOURCE_CHECK = PlaybookRef(
    playbook_id="PB-INGESTION-SOURCE-CHECK",
    summary=(
        "Field content collapsed or malformed at the source. Verify the "
        "upstream feed, then re-run the source scrape; if the feed itself "
        "changed shape, pause the source and open a scraper fix."
    ),
    steps=(
        "1. Open the raw artifact for the failing batch (raw lake, artifact id from the quarantine record).",
        "2. Diff the artifact against the previous good artifact: is the upstream feed missing the field, or did the parser mapping break?",
        "3. If upstream is at fault: pause the source (scheduling kill switch), file the source incident, notify the source contact per SOURCE-POLICY.",
        "4. If the parser is at fault: land the fix, replay the batch as a NEW version (DP-02-04), confirm the rule passes, then promote.",
        "5. Record the root cause on the source incident and close the quarantine record.",
    ),
)

_PB_SCHEMA_DRIFT = PlaybookRef(
    playbook_id="PB-SCHEMA-DRIFT",
    summary=(
        "Upstream schema/vocabulary drifted (new enum value, renamed column, "
        "new unit). Decide: extend the canonical vocabulary or fix the parser."
    ),
    steps=(
        "1. Inspect the offending sample rows attached to the rule result.",
        "2. Check upstream release notes / feed header for a deliberate change.",
        "3. If the change is legitimate: extend the registered enum/schema (new schema_version), re-run, promote.",
        "4. If it is an upstream defect: pause the source, file a source incident, wait for correction or apply a documented mapping in the transformer.",
        "5. Backfill affected batches via the replay workflow; never edit published rows in place.",
    ),
)

_PB_IDENTITY_REVIEW = PlaybookRef(
    playbook_id="PB-IDENTITY-REVIEW",
    summary=(
        "Identity resolution degraded: too many records below the minimum "
        "confidence, or duplicate identity keys. Review the matching queue "
        "before publishing."
    ),
    steps=(
        "1. Pull the sample of unmatched / duplicate keys from the rule result.",
        "2. Check for a re-issued label (e.g. sail number) or a renamed entity in the affected sample.",
        "3. Resolve the top unmatched clusters by hand in the matching workbench; record merges/splits as identity effects through the identity gate (DP-05-02).",
        "4. If the matcher itself regressed (threshold/weights), roll back the matcher config and re-score the batch.",
        "5. Re-run identity resolution for the batch, confirm the unmatched fraction is back within SLO, then promote.",
    ),
)

_PB_PROVENANCE_REPAIR = PlaybookRef(
    playbook_id="PB-PROVENANCE-REPAIR",
    summary=(
        "Rows lacking artifact citations must never publish. Quarantine the "
        "batch, repair the extractor's provenance envelope, replay."
    ),
    steps=(
        "1. Identify which pipeline stage dropped the provenance envelope (artifact_id / content_hash).",
        "2. Fix the extractor/transformer so every record carries its raw-artifact citation (DP-02-01).",
        "3. Replay the affected content as a new batch version; verify provenance coverage is 100%.",
        "4. If rows already reached a consumer view without provenance, treat as a correctness incident: supersede the published version immediately.",
    ),
)

_PB_FRESHNESS_RECOVERY = PlaybookRef(
    playbook_id="PB-FRESHNESS-RECOVERY",
    summary=(
        "Dataset is stale vs its cadence. Restore the schedule or prove the "
        "silence is legitimate (off-season, upstream outage)."
    ),
    steps=(
        "1. Check the run ledger for the source: last run status, last error, next scheduled run.",
        "2. If the schedule stalled: kick the workflow manually and watch it to completion.",
        "3. If upstream is down: open/attach a source incident; the staleness budget already accounts for known cadence gaps — extend only with owner sign-off.",
        "4. On recovery, verify the new batch passes all dimension rules, then promote.",
        "5. If staleness becomes chronic, revise the cadence class in SCHEDULING-POLICY (governance change, needs platform authority).",
    ),
)

_PB_DRIFT_INVESTIGATION = PlaybookRef(
    playbook_id="PB-DRIFT-INVESTIGATION",
    summary=(
        "Statistical drift vs the historical baseline. Determine whether the "
        "world changed (legitimate) or the pipeline changed (defect) before "
        "promoting."
    ),
    steps=(
        "1. Compare the drifting batch's histogram against the historical baseline (field stats on the rule params).",
        "2. Cross-check with an independent source (e.g. a certificate PDF sample, an event page) to decide world-change vs pipeline-change.",
        "3. World-change: re-baseline (build a new FieldBaseline from the recent window), document the change in the dimensions doc, then promote.",
        "4. Pipeline-change: quarantine, fix, replay as a new version, promote.",
        "5. For row-count drift also reconcile stage counts (DP-05-03 report) to rule out silent loss.",
    ),
)


# ---------------------------------------------------------------------------
# The registry — vertical-slice published datasets
# ---------------------------------------------------------------------------
#
# Threshold rationale references the historical review in
# docs/data-quality/dimensions.md ("Verification against real historical
# distributions").  Key measured facts (2026-05-22 review):
#
#   tcc_listing  — 11 snapshots 2009→2026, 1 865–3 906 rows/snapshot;
#                  TCC ∈ [0.709, 2.040], per-snapshot mean ∈ [1.019, 1.053]
#                  (std of means 0.003); sail_number nulls ≤ 1 row;
#                  cert_number duplicates = 0 in every snapshot; bare
#                  sail_number duplicates 2.5–9.0% (re-issued numbers);
#                  non_spi_tcc > tcc violations = 0; cert_year vs
#                  issue_date mismatches = 0; day-over-day new
#                  sail_numbers ≈ 1.1%.
#   orc_register — 134 daily snapshots 2026-03-14→2026-09-02, 6 754→13 074
#                  rows (seasonal growth); sail_number nulls ≈ 0.1%;
#                  RefNo duplicates = 0; CertName vocabulary stable at the
#                  registered 10 values.
#
# Drift baselines below are built from those measured windows; the
# verification script rebuilds them from the raw snapshots and re-checks
# the held-out snapshots against these rules.


#: dataset slug → ordered rules.
DQ_DATASET_RULES: dict[str, list[ThresholdRule]] = {}


def register_dataset_rules(dataset: str, rules: Sequence[ThresholdRule]) -> None:
    """Register (replace) the rule set for a published dataset."""
    DQ_DATASET_RULES[dataset] = list(rules)


def rules_for_dataset(dataset: str) -> list[ThresholdRule]:
    return list(DQ_DATASET_RULES.get(dataset, []))


def published_datasets() -> list[str]:
    """Slugs of every published dataset (has a registered rule set)."""
    return sorted(DQ_DATASET_RULES)


def _rule(
    dataset: str,
    dimension: QualityDimension,
    name: str,
    *,
    field_name: str | None = None,
    severity: Severity,
    metric: MetricKind,
    warn_at: float | None,
    block_at: float | None,
    owner: Owner,
    slo: SLO,
    playbook: PlaybookRef,
    params: Mapping[str, Any] | None = None,
    rationale: str = "",
) -> ThresholdRule:
    return ThresholdRule(
        rule_id=f"{dataset}.{dimension.value}.{name}",
        dataset=dataset,
        field_name=field_name,
        dimension=dimension,
        severity=severity,
        metric=metric,
        warn_at=warn_at,
        block_at=block_at,
        owner=owner,
        slo=slo,
        playbook=playbook,
        params=params or {},
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# tcc_listing — IRC TCC listing snapshots (daily scrape; boats + tcc_snapshots)
# ---------------------------------------------------------------------------

_TCC_BASELINE = FieldBaseline(
    field="tcc",
    mean=1.0452,
    std=0.00312,
    minimum=1.0192,
    maximum=1.0528,
    samples=8,
    source="tcc_listing_2026-03-14..2026-05-22 snapshot means",
)
_TCC_COUNT_BASELINE = (2996, 3013, 3013, 3013, 3042, 3079, 3114)

register_dataset_rules(
    "tcc_listing",
    [
        _rule(
            "tcc_listing", QualityDimension.COMPLETENESS, "sail_number_present",
            field_name="sail_number", severity=Severity.BLOCKING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.001, block_at=0.01,
            owner=OWNER_INGESTION, slo=SLO(0.99, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("sail_number", "boat_name", "cert_number")},
            rationale=(
                "Identity field. Historical nulls ≤ 1 row in 11 snapshots "
                "(≤0.03%); 1% loss means a broken parser, not source change."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.COMPLETENESS, "boat_name_present",
            field_name="boat_name", severity=Severity.WARNING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.001, block_at=0.01,
            owner=OWNER_INGESTION, slo=SLO(0.99, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("sail_number", "cert_number")},
            rationale="0 nulls observed across all 11 historical snapshots.",
        ),
        _rule(
            "tcc_listing", QualityDimension.VALIDITY, "tcc_plausible_range",
            field_name="tcc", severity=Severity.BLOCKING,
            metric=MetricKind.OUT_OF_RANGE_FRACTION,
            warn_at=0.001, block_at=0.01,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={"min": 0.6, "max": 2.2,
                    "sample_fields": ("sail_number", "tcc")},
            rationale=(
                "Observed TCC ∈ [0.709, 2.040] across 2009–2026; bounds "
                "[0.6, 2.2] carry ~15%/8% headroom past the extremes. Also "
                "matches the canonical schema hard limit (0, 3.0]."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.VALIDITY, "cert_year_in_range",
            field_name="cert_year", severity=Severity.WARNING,
            metric=MetricKind.OUT_OF_RANGE_FRACTION,
            warn_at=0.001, block_at=0.02,
            owner=OWNER_INGESTION, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={"min": 1990, "max": 2100,
                    "sample_fields": ("sail_number", "cert_year")},
            rationale="Schema bound; history spans 2009 and 2026 only.",
        ),
        _rule(
            "tcc_listing", QualityDimension.UNIQUENESS, "cert_number_unique",
            field_name="cert_number", severity=Severity.BLOCKING,
            metric=MetricKind.DUPLICATE_FRACTION, warn_at=0.0001, block_at=0.005,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.995, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"sample_fields": ("cert_number", "sail_number")},
            rationale=(
                "0 duplicate cert numbers in every one of the 11 historical "
                "snapshots — any duplicate is a scraper artifact (row bleed)."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.UNIQUENESS, "sail_number_dup_bounded",
            field_name="sail_number", severity=Severity.WARNING,
            metric=MetricKind.DUPLICATE_FRACTION, warn_at=0.12, block_at=0.25,
            owner=OWNER_IDENTITY, slo=SLO(0.95, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"sample_fields": ("sail_number", "boat_name", "cert_number")},
            rationale=(
                "Bare sail numbers are legitimately non-unique (re-issued "
                "across boats/eras): observed 2.5%–9.0% per snapshot. "
                "Warning at 12%, blocking at 25% catches catastrophic "
                "duplication without false-alarming on reality."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.CONSISTENCY, "non_spi_le_tcc",
            severity=Severity.BLOCKING,
            metric=MetricKind.CROSS_FIELD_VIOLATION_FRACTION,
            warn_at=0.001, block_at=0.01,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={"predicate": "non_spi_le_tcc",
                    "sample_fields": ("sail_number", "tcc", "non_spi_tcc")},
            rationale=(
                "0 violations in all 2026 snapshots (2 865–3 114 comparable "
                "rows each). A non-spinnaker TCC above the all-up TCC is "
                "physically impossible."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.CONSISTENCY, "cert_year_matches_issue_date",
            severity=Severity.WARNING,
            metric=MetricKind.CROSS_FIELD_VIOLATION_FRACTION,
            warn_at=0.005, block_at=0.05,
            owner=OWNER_INGESTION, slo=SLO(0.98, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"predicate": "cert_year_matches_issue_date",
                    "sample_fields": ("sail_number", "cert_year", "issue_date")},
            rationale="0 mismatches observed in 2026 snapshots (±1 year slack).",
        ),
        _rule(
            "tcc_listing", QualityDimension.TIMELINESS, "daily_snapshot_freshness",
            severity=Severity.BLOCKING,
            metric=MetricKind.FRESHNESS_LAG_DAYS, warn_at=2.0, block_at=5.0,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            playbook=_PB_FRESHNESS_RECOVERY,
            rationale=(
                "Cadence: daily (DailyScrapeWorkflow; SCHEDULING-POLICY). "
                "Warn after 2 days, block after 5 — the listing changes "
                "daily during the season."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.PROVENANCE, "artifact_citation_complete",
            severity=Severity.BLOCKING,
            metric=MetricKind.PROVENANCE_GAP_FRACTION, warn_at=None, block_at=0.0001,
            owner=OWNER_DATA_PLATFORM, slo=SLO(1.0, 30),
            playbook=_PB_PROVENANCE_REPAIR,
            params={"fields": ("artifact_id", "content_hash"),
                    "sample_fields": ("sail_number",)},
            rationale=(
                "Provenance is absolute (DP-02-01): any row without an "
                "artifact citation blocks. No warning band by design."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.IDENTITY_CONFIDENCE, "boat_match_coverage",
            severity=Severity.WARNING,
            metric=MetricKind.UNMATCHED_FRACTION, warn_at=0.15, block_at=0.40,
            owner=OWNER_IDENTITY, slo=SLO(0.95, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"min_confidence": 0.8,
                    "sample_fields": ("sail_number", "boat_name")},
            rationale=(
                "Day-over-day genuinely-new sail numbers ≈ 1.1% (measured "
                "2026-05-22 vs all prior snapshots). >15% unmatched means "
                "the matcher or the reference data regressed."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.DRIFT, "tcc_mean_drift",
            field_name="tcc", severity=Severity.BLOCKING,
            metric=MetricKind.DISTRIBUTION_Z, warn_at=3.0, block_at=6.0,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.98, 30),
            playbook=_PB_DRIFT_INVESTIGATION,
            params={"baseline": _TCC_BASELINE, "min_samples": 500},
            rationale=(
                "Per-snapshot TCC means sit in [1.019, 1.053] with std "
                "0.0031 across 8 snapshots; |z|>3 on the snapshot mean is "
                "already 10× the observed spread."
            ),
        ),
        _rule(
            "tcc_listing", QualityDimension.DRIFT, "row_count_drift",
            severity=Severity.WARNING,
            metric=MetricKind.COUNT_DRIFT_FRACTION, warn_at=0.25, block_at=0.60,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            playbook=_PB_DRIFT_INVESTIGATION,
            params={"baseline_counts": _TCC_COUNT_BASELINE, "min_samples": 3},
            rationale=(
                "2026 season counts grew 1 865→3 114 steadily (~1–2%/day); "
                "a ±25% day-over-day swing is never organic. Pairs with "
                "DP-05-03's p10 yield floor."
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# race_results — event results from results platforms (incremental scrapes)
# ---------------------------------------------------------------------------

_RATING_BASELINE = FieldBaseline(
    field="rating_value",
    mean=1.04,
    std=0.02,
    minimum=1.00,
    maximum=1.09,
    samples=6,
    source="IRC-division rating_value means, 2026 season review",
)

register_dataset_rules(
    "race_results",
    [
        _rule(
            "race_results", QualityDimension.COMPLETENESS, "place_or_status_present",
            field_name="place", severity=Severity.WARNING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.10, block_at=0.30,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("event_entry_id", "race_name", "status")},
            rationale=(
                "DNF/DNS rows legitimately lack a place; a null fraction "
                "above 30% means the parser dropped the finish column."
            ),
        ),
        _rule(
            "race_results", QualityDimension.VALIDITY, "status_vocabulary",
            field_name="status", severity=Severity.BLOCKING,
            metric=MetricKind.ENUM_VIOLATION_FRACTION, warn_at=0.001, block_at=0.02,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={
                "allowed": (
                    "finished", "dnf", "dns", "dsq", "ocs", "bfd", "scp",
                    "ret", "dnc", "dne", "rdg", "tle", "unknown",
                ),
                "sample_fields": ("event_entry_id", "status"),
            },
            rationale=(
                "Registered status vocabulary; a new code means upstream "
                "added a scoring state — extend deliberately, don't leak it."
            ),
        ),
        _rule(
            "race_results", QualityDimension.VALIDITY, "irc_rating_range",
            field_name="rating_value", severity=Severity.BLOCKING,
            metric=MetricKind.OUT_OF_RANGE_FRACTION, warn_at=0.001, block_at=0.01,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={"min": 0.6, "max": 2.2,
                    "applies_when": {"rating_type": "irc"},
                    "sample_fields": ("event_entry_id", "rating_value")},
            rationale=(
                "IRC TCCs bounded as for tcc_listing ([0.709, 2.040] "
                "observed). Applies to rating_type='irc' rows."
            ),
        ),
        _rule(
            "race_results", QualityDimension.UNIQUENESS, "entry_race_unique",
            severity=Severity.BLOCKING,
            metric=MetricKind.DUPLICATE_FRACTION, warn_at=0.0001, block_at=0.005,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.995, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"fields": ("event_entry_id", "race_name"),
                    "sample_fields": ("event_entry_id", "race_name")},
            rationale=(
                "Mirrors the DB unique constraint (event_entry_id, "
                "race_name); a duplicate batch row would 500 the upsert."
            ),
        ),
        _rule(
            "race_results", QualityDimension.CONSISTENCY, "place_within_fleet",
            severity=Severity.WARNING,
            metric=MetricKind.CROSS_FIELD_VIOLATION_FRACTION,
            warn_at=0.005, block_at=0.05,
            owner=OWNER_INGESTION, slo=SLO(0.98, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"predicate": "place_le_fleet_size",
                    "sample_fields": ("event_entry_id", "place", "fleet_size")},
            rationale="place > fleet_size is a scoring-table parse error.",
        ),
        _rule(
            "race_results", QualityDimension.TIMELINESS, "results_ingest_freshness",
            severity=Severity.BLOCKING,
            metric=MetricKind.FRESHNESS_LAG_DAYS, warn_at=3.0, block_at=10.0,
            owner=OWNER_INGESTION, slo=SLO(0.90, 30),
            playbook=_PB_FRESHNESS_RECOVERY,
            rationale=(
                "IncrementalResultsWorkflow runs daily, but regattas cluster "
                "on weekends — midweek silence is normal. 10 days without "
                "any results means the pipeline is stuck."
            ),
        ),
        _rule(
            "race_results", QualityDimension.PROVENANCE, "artifact_citation_complete",
            severity=Severity.BLOCKING,
            metric=MetricKind.PROVENANCE_GAP_FRACTION, warn_at=None, block_at=0.0001,
            owner=OWNER_DATA_PLATFORM, slo=SLO(1.0, 30),
            playbook=_PB_PROVENANCE_REPAIR,
            params={"fields": ("artifact_id", "content_hash"),
                    "sample_fields": ("event_entry_id",)},
            rationale="Provenance is absolute (DP-02-01).",
        ),
        _rule(
            "race_results", QualityDimension.IDENTITY_CONFIDENCE, "entry_boat_match",
            severity=Severity.WARNING,
            metric=MetricKind.UNMATCHED_FRACTION, warn_at=0.20, block_at=0.50,
            owner=OWNER_IDENTITY, slo=SLO(0.90, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"min_confidence": 0.8,
                    "sample_fields": ("event_entry_id", "sail_number")},
            rationale=(
                "Results pages regularly list boats we've never rated; "
                "half the field unmatched means the entry→boat join broke."
            ),
        ),
        _rule(
            "race_results", QualityDimension.DRIFT, "irc_rating_mean_drift",
            field_name="rating_value", severity=Severity.WARNING,
            metric=MetricKind.DISTRIBUTION_Z, warn_at=3.0, block_at=6.0,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.95, 30),
            playbook=_PB_DRIFT_INVESTIGATION,
            params={"baseline": _RATING_BASELINE, "min_samples": 100},
            rationale=(
                "Batch mean TCC drift catches unit/scale defects (e.g. "
                "parsing PHRF into an IRC column) that per-row range "
                "checks cannot."
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# irc_certificates — parsed IRC certificate PDFs (bulk + search ingestion)
# ---------------------------------------------------------------------------

_LH_BASELINE = FieldBaseline(
    field="lh",
    mean=11.9,
    std=0.15,
    minimum=11.5,
    maximum=12.3,
    samples=4,
    source="irc_certificates LH batch means, 2026 review",
)

register_dataset_rules(
    "irc_certificates",
    [
        _rule(
            "irc_certificates", QualityDimension.COMPLETENESS, "cert_number_present",
            field_name="cert_number", severity=Severity.BLOCKING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.001, block_at=0.01,
            owner=OWNER_INGESTION, slo=SLO(0.99, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("cert_number", "pdf_path")},
            rationale="cert_number is the publishable identity of the dataset.",
        ),
        _rule(
            "irc_certificates", QualityDimension.VALIDITY, "lh_plausible_range",
            field_name="lh", severity=Severity.BLOCKING,
            metric=MetricKind.OUT_OF_RANGE_FRACTION, warn_at=0.001, block_at=0.01,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={"min": 4.0, "max": 40.0,
                    "sample_fields": ("cert_number", "lh")},
            rationale=(
                "Hull length (m) of rated fleet; <4 m or >40 m is a PDF "
                "parse defect (unit bleed from mm or ft columns)."
            ),
        ),
        _rule(
            "irc_certificates", QualityDimension.VALIDITY, "issue_date_iso",
            field_name="issue_date", severity=Severity.WARNING,
            metric=MetricKind.REGEX_VIOLATION_FRACTION, warn_at=0.005, block_at=0.05,
            owner=OWNER_INGESTION, slo=SLO(0.98, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"pattern": r"^\d{4}-\d{2}-\d{2}$",
                    "sample_fields": ("cert_number", "issue_date")},
            rationale="Canonical form is ISO-8601; certificates arrive DD/MM/YYYY and are normalised at transform time (DP-03-04).",
        ),
        _rule(
            "irc_certificates", QualityDimension.UNIQUENESS, "cert_number_unique",
            field_name="cert_number", severity=Severity.BLOCKING,
            metric=MetricKind.DUPLICATE_FRACTION, warn_at=0.0001, block_at=0.005,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.995, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"sample_fields": ("cert_number",)},
            rationale="DB unique constraint on cert_number; duplicates indicate double-ingest of the same PDF under two paths.",
        ),
        _rule(
            "irc_certificates", QualityDimension.CONSISTENCY, "measures_present_with_cert",
            severity=Severity.WARNING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.20, block_at=0.50,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            field_name="lh",
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("cert_number",)},
            rationale=(
                "A parsed certificate without hull measurements usually "
                "means the PDF table extractor failed on a layout variant."
            ),
        ),
        _rule(
            "irc_certificates", QualityDimension.TIMELINESS, "certificate_ingest_freshness",
            severity=Severity.WARNING,
            metric=MetricKind.FRESHNESS_LAG_DAYS, warn_at=14.0, block_at=45.0,
            owner=OWNER_INGESTION, slo=SLO(0.90, 30),
            playbook=_PB_FRESHNESS_RECOVERY,
            rationale=(
                "Bulk + probe ingestion is opportunistic (weekly cadence "
                "class); 45 days without any new certificate is a stall."
            ),
        ),
        _rule(
            "irc_certificates", QualityDimension.PROVENANCE, "artifact_citation_complete",
            severity=Severity.BLOCKING,
            metric=MetricKind.PROVENANCE_GAP_FRACTION, warn_at=None, block_at=0.0001,
            owner=OWNER_DATA_PLATFORM, slo=SLO(1.0, 30),
            playbook=_PB_PROVENANCE_REPAIR,
            params={"fields": ("artifact_id", "content_hash"),
                    "sample_fields": ("cert_number",)},
            rationale="Provenance is absolute (DP-02-01); certificates are legal-adjacent documents and must cite their PDF artifact.",
        ),
        _rule(
            "irc_certificates", QualityDimension.IDENTITY_CONFIDENCE, "cert_boat_match",
            severity=Severity.WARNING,
            metric=MetricKind.UNMATCHED_FRACTION, warn_at=0.25, block_at=0.60,
            owner=OWNER_IDENTITY, slo=SLO(0.90, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"min_confidence": 0.8,
                    "sample_fields": ("cert_number", "sail_number")},
            rationale=(
                "Certificates for brand-new boats legitimately miss the "
                "registry; >60% unmatched means the cert→boat join broke."
            ),
        ),
        _rule(
            "irc_certificates", QualityDimension.DRIFT, "lh_mean_drift",
            field_name="lh", severity=Severity.WARNING,
            metric=MetricKind.DISTRIBUTION_Z, warn_at=3.0, block_at=6.0,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.95, 30),
            playbook=_PB_DRIFT_INVESTIGATION,
            params={"baseline": _LH_BASELINE, "min_samples": 50},
            rationale="Fleet-average hull length moves slowly; a batch-mean jump indicates a unit or extraction defect.",
        ),
    ],
)


# ---------------------------------------------------------------------------
# orc_register — ORC country XML register snapshots (daily)
# ---------------------------------------------------------------------------

register_dataset_rules(
    "orc_register",
    [
        _rule(
            "orc_register", QualityDimension.COMPLETENESS, "sail_number_present",
            field_name="sail_number", severity=Severity.BLOCKING,
            metric=MetricKind.NULL_FRACTION, warn_at=0.005, block_at=0.02,
            owner=OWNER_INGESTION, slo=SLO(0.99, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"sample_fields": ("sail_number", "ref_no", "boat_name")},
            rationale=(
                "Observed 0.1% blank SailNo across snapshots (some entries "
                "genuinely lack one). Blocking at 2% — 20× the baseline."
            ),
        ),
        _rule(
            "orc_register", QualityDimension.VALIDITY, "cert_name_vocabulary",
            field_name="cert_name", severity=Severity.WARNING,
            metric=MetricKind.ENUM_VIOLATION_FRACTION, warn_at=0.001, block_at=0.02,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.99, 30),
            playbook=_PB_SCHEMA_DRIFT,
            params={
                "allowed": (
                    "Club", "International", "DH Club", "DH International",
                    "NS Club", "NS International", "Light", "MOCRA",
                    "Mu Club", "Mu International",
                ),
                "sample_fields": ("ref_no", "cert_name"),
            },
            rationale=(
                "The CertName vocabulary was exactly these 10 values in "
                "every snapshot reviewed 2026-03→2026-09."
            ),
        ),
        _rule(
            "orc_register", QualityDimension.UNIQUENESS, "ref_no_unique",
            field_name="ref_no", severity=Severity.BLOCKING,
            metric=MetricKind.DUPLICATE_FRACTION, warn_at=0.0001, block_at=0.005,
            owner=OWNER_DATA_PLATFORM, slo=SLO(0.995, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"sample_fields": ("ref_no", "sail_number")},
            rationale="0 duplicate RefNo across all reviewed snapshots; RefNo is ORC's own certificate key.",
        ),
        _rule(
            "orc_register", QualityDimension.CONSISTENCY, "expiry_after_issue",
            severity=Severity.WARNING,
            metric=MetricKind.REGEX_VIOLATION_FRACTION,
            field_name="expiry", warn_at=0.005, block_at=0.05,
            owner=OWNER_INGESTION, slo=SLO(0.98, 30),
            playbook=_PB_INGESTION_SOURCE_CHECK,
            params={"pattern": r"^\d{4}-\d{2}-\d{2}T",
                    "sample_fields": ("ref_no", "expiry")},
            rationale="Expiry timestamps arrive as ISO datetimes; malformed values break validity-window logic downstream.",
        ),
        _rule(
            "orc_register", QualityDimension.TIMELINESS, "daily_snapshot_freshness",
            severity=Severity.BLOCKING,
            metric=MetricKind.FRESHNESS_LAG_DAYS, warn_at=2.0, block_at=7.0,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            playbook=_PB_FRESHNESS_RECOVERY,
            rationale="Daily snapshot cadence (134 consecutive daily dirs observed 2026-03-14→2026-09-02).",
        ),
        _rule(
            "orc_register", QualityDimension.PROVENANCE, "artifact_citation_complete",
            severity=Severity.BLOCKING,
            metric=MetricKind.PROVENANCE_GAP_FRACTION, warn_at=None, block_at=0.0001,
            owner=OWNER_DATA_PLATFORM, slo=SLO(1.0, 30),
            playbook=_PB_PROVENANCE_REPAIR,
            params={"fields": ("artifact_id", "content_hash"),
                    "sample_fields": ("ref_no",)},
            rationale="Provenance is absolute (DP-02-01).",
        ),
        _rule(
            "orc_register", QualityDimension.IDENTITY_CONFIDENCE, "orc_boat_match",
            severity=Severity.WARNING,
            metric=MetricKind.UNMATCHED_FRACTION, warn_at=0.30, block_at=0.70,
            owner=OWNER_IDENTITY, slo=SLO(0.90, 30),
            playbook=_PB_IDENTITY_REVIEW,
            params={"min_confidence": 0.8,
                    "sample_fields": ("ref_no", "sail_number")},
            rationale=(
                "ORC's register covers boats IRC never rates — a high "
                "unmatched floor is expected; blocking only on total "
                "join failure."
            ),
        ),
        _rule(
            "orc_register", QualityDimension.DRIFT, "row_count_drift",
            severity=Severity.WARNING,
            metric=MetricKind.COUNT_DRIFT_FRACTION, warn_at=0.30, block_at=0.70,
            owner=OWNER_INGESTION, slo=SLO(0.95, 30),
            playbook=_PB_DRIFT_INVESTIGATION,
            params={"baseline_counts": (6754, 10754, 13074), "min_samples": 3},
            rationale=(
                "Seasonal growth 6 754→13 074 rows (≈2×) over six months is "
                "organic; a ±30% step between consecutive daily snapshots "
                "is not."
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# Registry validation — the acceptance-criteria gate
# ---------------------------------------------------------------------------


def validate_registry(
    registry: Mapping[str, Sequence[ThresholdRule]] | None = None,
) -> list[str]:
    """Check the registry against the DP-05-01 acceptance criteria.

    Returns a list of violations (empty = compliant).  For every
    published dataset:

    * at least one **blocking** rule and at least one **warning** rule;
    * every :class:`QualityDimension` covered by at least one rule;
    * every rule carries an owner, an SLO and a remediation playbook;
    * rule ids are unique and dotted as ``<dataset>.<dimension>.<name>``.
    """
    reg = registry if registry is not None else DQ_DATASET_RULES
    violations: list[str] = []

    if not reg:
        violations.append("registry is empty: no published datasets registered")
        return violations

    for dataset, rules in sorted(reg.items()):
        if not rules:
            violations.append(f"{dataset}: no rules registered")
            continue

        severities = {r.severity for r in rules}
        if Severity.BLOCKING not in severities:
            violations.append(f"{dataset}: no blocking rule")
        if Severity.WARNING not in severities:
            violations.append(f"{dataset}: no warning rule")

        covered = {r.dimension for r in rules}
        for dim in QualityDimension:
            if dim not in covered:
                violations.append(
                    f"{dataset}: dimension {dim.value!r} has no rule"
                )

        seen_ids: set[str] = set()
        for r in rules:
            if r.rule_id in seen_ids:
                violations.append(f"{dataset}: duplicate rule id {r.rule_id!r}")
            seen_ids.add(r.rule_id)
            if not r.rule_id.startswith(f"{dataset}.{r.dimension.value}."):
                violations.append(
                    f"{dataset}: rule id {r.rule_id!r} must be dotted as "
                    f"<dataset>.<dimension>.<name>"
                )
            if r.dataset != dataset:
                violations.append(
                    f"{dataset}: rule {r.rule_id!r} registered under the "
                    f"wrong dataset ({r.dataset!r})"
                )
            if r.owner is None or not r.owner.handle:
                violations.append(f"{r.rule_id}: no accountable owner")
            if r.slo is None:
                violations.append(f"{r.rule_id}: no SLO")
            if r.playbook is None or not r.playbook.steps:
                violations.append(f"{r.rule_id}: no remediation playbook")

    return violations


# Fail fast at import: a non-compliant registry is a build defect, not a
# runtime condition.  (validate_registry is also exercised directly in
# tests so this doubles as a smoke check.)
_violations = validate_registry()
if _violations:
    raise RegistryError(
        "data-quality rule registry violates DP-05-01 acceptance criteria:\n"
        + "\n".join(f"  - {v}" for v in _violations)
    )


__all__ = [
    "SCHEMA_VERSION",
    "QualityDimension",
    "Severity",
    "MetricKind",
    "Owner",
    "SLO",
    "PlaybookRef",
    "FieldBaseline",
    "ThresholdRule",
    "RuleResult",
    "DimensionReportV1",
    "RegistryError",
    "BlockingRuleViolation",
    "PLATFORM_AUTHORITY",
    "OWNER_DATA_PLATFORM",
    "OWNER_INGESTION",
    "OWNER_IDENTITY",
    "CONSISTENCY_PREDICATES",
    "build_field_baseline",
    "evaluate_rule",
    "evaluate_dataset",
    "assert_dataset_publishable",
    "register_dataset_rules",
    "rules_for_dataset",
    "published_datasets",
    "validate_registry",
    "DQ_DATASET_RULES",
]
