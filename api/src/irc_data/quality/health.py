"""Data health dashboard & incident workflow (DP-05-04 / SPEC-016).

**Goal: turn quality failures into owned recovery work.**

This module is the read/aggregate model over the quality stack and the
owned-incident workflow on top of it:

* **Dashboard** — :func:`get_health_dashboard` aggregates, per governed
  source, the signals the scope calls for:

  * **source freshness** — latest-run / latest-new-data timestamps from
    the run ledger (OPS-01-03) plus trailing-window run/fail/row
    aggregates;
  * **pipeline yields** — per-source reconciliation reports
    (DP-05-03): latest yield ratio, the trailing [p10, p50] baseline
    band, and unexplained-variance totals over the window;
  * **quarantine** — active publication quarantines (DP-01-05 /
    DP-05-03) plus open quality-gate quarantine queue depth
    (DP-05-02);
  * **lineage gaps** — un-reconciled pipeline runs in the window: runs
    that completed without a reconciliation report, so their stage
    counts were never checked against the conservation invariant;
  * **identity uncertainty** — batches sitting in
    ``awaiting_promotion`` at the identity gate (unreviewed identity
    effects) plus quarantined identity batches (low-confidence merges /
    splits held out of the canonical registry);
  * **SLO breaches** — open ``source_incidents`` (material deviations,
    silent loss) and blocking reconciliation decisions, each carrying
    the DP-05-01 SLO it burns against.

* **Incident workflow** — :class:`DataIncidentV1` (the **output
  contract**) with a state machine::

      open → acknowledged → mitigating → resolved
                            (any non-resolved state) → acknowledged

  Every incident carries an **owner** (from the DP-05-01 ownership
  registry), the **affected batches** and **affected consumers**, an
  **evidence** bundle (pointers back to the underlying quality events —
  health events, reconciliation reports, quarantine records, gate
  verdicts, ledger runs) and a **recommended action**: either a replay
  (a ready-to-submit :class:`ReplayPlanV1` for the DP-02-04 replay
  pipeline) or a policy action (quarantine release / rebaseline /
  ownership review).

* **Reconciliation to quality events** —
  :func:`reconcile_incidents_to_events` walks every incident and checks
  that its evidence refs resolve to real quality events, so the
  dashboard can never drift from what actually happened (acceptance
  criterion: "dashboard reconciles to quality events").

Incident creation from detectors
--------------------------------
Detectors (source monitor, reconciler, quality gates, identity review)
persist their own rows; this module *ingests* them:

* :func:`create_incident_from_health_event` — a material
  :class:`~irc_data.diagnostics.source_monitor.SourceHealthEventV1`
  (source breakage / structure change).
* :func:`create_incident_from_reconciliation` — a blocking
  :class:`~irc_data.diagnostics.reconciliation.ReconciliationReportV1`
  (silent loss / abrupt yield change).

Both auto-attach the owner, affected batches/consumers, evidence refs
and a recommended action, and fire the health-check webhook (the same
``SOURCE_MONITOR_WEBHOOK_URL`` convention as DP-01-05 / DP-05-03) so
the owner is alerted **with** the incident id in the same cycle.
Synthetic incidents (verification) use the same path — there is no
special-casing.

Portability
-----------
DB-agnostic: raw SQL via ``text()`` so the test suite runs against
in-memory SQLite and production against Postgres (Alembic migration
``0029_data_incidents``).  All cross-stack reads are defensive: a
missing table degrades that signal to ``available=False`` instead of
breaking the dashboard.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db import run_ledger
from irc_data.diagnostics import reconciliation as recon
from irc_data.diagnostics.source_monitor import (
    HEALTH_WEBHOOK_ENV,
    AlertTransport,
    _post_webhook,
)
from irc_data.quality import dimensions as dq
from irc_data.temporal.replay.contracts import ArtifactFilter, ReplayPlanV1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"

#: Env var carrying the health-check webhook URL (same convention as
#: DP-01-05 / DP-05-03).
INCIDENT_WEBHOOK_ENV = HEALTH_WEBHOOK_ENV

#: Default freshness budget (hours) when the source has no cadence
#: catalog entry: a source with no successful run inside this window is
#: *stale*; one that never landed new data inside it is flagged for
#: attention.  Matches the watchdog's broadest daily budget.
DEFAULT_FRESHNESS_BUDGET_HOURS = 26.0

#: Default look-back window for dashboard aggregates.
DEFAULT_WINDOW_DAYS = 7

# Incident status values (the workflow state machine).
STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_MITIGATING = "mitigating"
STATUS_RESOLVED = "resolved"

INCIDENT_STATUSES = (
    STATUS_OPEN,
    STATUS_ACKNOWLEDGED,
    STATUS_MITIGATING,
    STATUS_RESOLVED,
)

#: Legal forward transitions.  Resolution is a terminal transition from
#: any active state; ``open → mitigating`` is allowed (fast-path when
#: the owner starts work immediately, recording the ack implicitly).
_TRANSITIONS: dict[str, set[str]] = {
    STATUS_OPEN: {STATUS_ACKNOWLEDGED, STATUS_MITIGATING, STATUS_RESOLVED},
    STATUS_ACKNOWLEDGED: {STATUS_MITIGATING, STATUS_RESOLVED},
    STATUS_MITIGATING: {STATUS_ACKNOWLEDGED, STATUS_RESOLVED},
    STATUS_RESOLVED: set(),
}

# Severity.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

# Recommended-action kinds.
ACTION_REPLAY = "replay"
ACTION_POLICY = "policy"

# Incident kinds.
KIND_SOURCE_DEVIATION = "source_deviation"
KIND_SILENT_LOSS = "silent_loss"
KIND_QUARANTINE = "quarantine"
KIND_FRESHNESS = "freshness_breach"
KIND_LINEAGE_GAP = "lineage_gap"
KIND_IDENTITY_UNCERTAINTY = "identity_uncertainty"
KIND_SLO_BREACH = "slo_breach"
KIND_MANUAL = "manual"

#: incident_type strings on DP-01-05/DP-05-03 ``source_incidents`` rows
#: mapped to dashboard categories.
SOURCE_INCIDENT_CATEGORY = {
    "structure_change": KIND_SOURCE_DEVIATION,
    "fetch_error": KIND_SOURCE_DEVIATION,
    "content_type_change": KIND_SOURCE_DEVIATION,
    "record_count_collapse": KIND_SOURCE_DEVIATION,
    "parser_yield_collapse": KIND_SOURCE_DEVIATION,
    "hash_delta": KIND_SOURCE_DEVIATION,
    "silent_loss": KIND_SILENT_LOSS,
}

#: Incident kinds that burn the *dataset* SLO of the same name; the rest
#: burn the source-cadence (timeliness) SLO of the source's dataset.
_KIND_TO_DIMENSION = {
    KIND_SOURCE_DEVIATION: "validity",
    KIND_SILENT_LOSS: "completeness",
    KIND_QUARANTINE: "validity",
    KIND_FRESHNESS: "timeliness",
    KIND_LINEAGE_GAP: "provenance",
    KIND_IDENTITY_UNCERTAINTY: "identity_confidence",
    KIND_SLO_BREACH: "timeliness",
    KIND_MANUAL: "timeliness",
}

#: Heuristic mapping from a pipeline/source slug to the published
#: dataset whose SLO the incident burns against.  Anything unknown falls
#: back to the source slug itself (the registry lookup simply misses
#: and ownership falls back to the data-platform owner).
_SOURCE_TO_DATASET = {
    "tcc": "tcc_listing",
    "tcc_listing": "tcc_listing",
    "irc": "irc_certificates",
    "irc_certificates": "irc_certificates",
    "irc-certs": "irc_certificates",
    "orc": "orc_register",
    "orc_register": "orc_register",
    "sailsys": "race_results",
    "sailsys_results": "race_results",
    "race_results": "race_results",
    "results": "race_results",
}


class IncidentWorkflowError(ValueError):
    """Illegal incident workflow transition or unknown incident."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _as_dt(value: Any) -> datetime | None:
    """Coerce DB timestamp values (str or datetime) to aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    dt = _as_dt(value)
    return dt.isoformat() if dt else (str(value) if value is not None else None)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _table_exists(engine: Engine, table: str) -> bool:
    """True when ``table`` exists (SQLite + Postgres portable)."""
    with engine.connect() as conn:
        try:
            if conn.dialect.name == "sqlite":
                row = conn.execute(
                    text(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = :t"
                    ),
                    {"t": table},
                ).first()
            else:
                row = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).first()
            return row is not None
        except Exception:
            return False


def _new_incident_id() -> str:
    return f"inc-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# DataIncidentV1 — the output contract (handoff contract)
# ---------------------------------------------------------------------------


@dataclass
class DataIncidentV1:
    """One owned unit of recovery work (DP-05-04 output contract).

    ``affected_batches`` and ``affected_consumers`` answer *what* is hit;
    ``evidence`` carries pointers to the underlying quality events so the
    dashboard always reconciles back to them; ``recommended_action`` is
    either a replay plan or a policy action — never both absent for a
    detector-created incident.
    """

    incident_id: str
    kind: str
    severity: str
    status: str
    source_slug: str | None = None
    dataset: str | None = None
    title: str = ""
    summary: str = ""
    detected_at: str = ""
    acknowledged_at: str | None = None
    resolved_at: str | None = None
    owner: dict[str, Any] = field(default_factory=dict)
    acknowledged_by: str | None = None
    affected_batches: list[str] = field(default_factory=list)
    affected_consumers: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_action: dict[str, Any] = field(default_factory=dict)
    alert_sent_at: str | None = None
    notes: list[dict[str, str]] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DataIncidentV1":
        return cls(
            incident_id=d["incident_id"],
            kind=d.get("kind", KIND_MANUAL),
            severity=d.get("severity", SEVERITY_WARNING),
            status=d.get("status", STATUS_OPEN),
            source_slug=d.get("source_slug"),
            dataset=d.get("dataset"),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            detected_at=d.get("detected_at", ""),
            acknowledged_at=d.get("acknowledged_at"),
            resolved_at=d.get("resolved_at"),
            owner=dict(d.get("owner") or {}),
            acknowledged_by=d.get("acknowledged_by"),
            affected_batches=list(d.get("affected_batches") or []),
            affected_consumers=list(d.get("affected_consumers") or []),
            evidence=dict(d.get("evidence") or {}),
            recommended_action=dict(d.get("recommended_action") or {}),
            alert_sent_at=d.get("alert_sent_at"),
            notes=list(d.get("notes") or []),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, s: str) -> "DataIncidentV1":
        return cls.from_dict(json.loads(s))

    # -- workflow ----------------------------------------------------------

    def can_transition(self, new_status: str) -> bool:
        """True when ``status → new_status`` is a legal transition."""
        return new_status in _TRANSITIONS.get(self.status, set())


# ---------------------------------------------------------------------------
# Schema (SQLite-compatible, mirrors alembic 0029_data_incidents)
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS data_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    source_slug TEXT,
    dataset TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    detected_at TIMESTAMP NOT NULL,
    acknowledged_at TIMESTAMP,
    resolved_at TIMESTAMP,
    owner TEXT,
    acknowledged_by TEXT,
    affected_batches TEXT,
    affected_consumers TEXT,
    evidence TEXT,
    recommended_action TEXT,
    alert_sent_at TIMESTAMP,
    notes TEXT,
    schema_version TEXT DEFAULT 'v1',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_data_incidents_status
    ON data_incidents(status, detected_at);

CREATE INDEX IF NOT EXISTS ix_data_incidents_source
    ON data_incidents(source_slug, detected_at);
"""


def init_data_incident_tables(engine: Engine) -> None:
    """Create the ``data_incidents`` table (idempotent).

    On Postgres this is normally handled by the Alembic migration
    (``0029_data_incidents``).  This helper exists so tests can set up an
    in-memory SQLite schema without Alembic.
    """
    with engine.begin() as conn:
        for stmt in SCHEMA_SQL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))


# ---------------------------------------------------------------------------
# Ownership / recommendation helpers
# ---------------------------------------------------------------------------


def dataset_for_source(source_slug: str | None) -> str | None:
    """Best-effort map from a source slug to its published dataset."""
    if not source_slug:
        return None
    slug = source_slug.lower()
    if slug in _SOURCE_TO_DATASET:
        return _SOURCE_TO_DATASET[slug]
    # Prefix match: e.g. "sailsys_rhkyc" → "race_results".
    for prefix, dataset in sorted(
        _SOURCE_TO_DATASET.items(), key=lambda kv: -len(kv[0])
    ):
        if slug.startswith(prefix):
            return dataset
    return None


def _owner_for(kind: str, dataset: str | None) -> dq.Owner:
    """Resolve the accountable owner for an incident.

    Prefers the owner of a *blocking* rule of the matching dimension on
    the incident's dataset (DP-05-01 registry); falls back to the
    dataset's first registered rule owner, then to the data-platform
    owner.  Identity uncertainty always routes to the identity owner.
    """
    if kind == KIND_IDENTITY_UNCERTAINTY:
        return dq.OWNER_IDENTITY
    dimension = _KIND_TO_DIMENSION.get(kind, "timeliness")
    if dataset:
        rules = dq.rules_for_dataset(dataset)
        for rule in rules:
            if (
                rule.dimension.value == dimension
                and rule.severity == dq.Severity.BLOCKING
            ):
                return rule.owner
        if rules:
            return rules[0].owner
    return dq.OWNER_DATA_PLATFORM


def _slo_for(kind: str, dataset: str | None) -> dict[str, Any] | None:
    """The SLO this incident burns against (DP-05-01 registry)."""
    dimension = _KIND_TO_DIMENSION.get(kind, "timeliness")
    if dataset:
        for rule in dq.rules_for_dataset(dataset):
            if rule.dimension.value == dimension:
                return rule.slo.to_dict() | {"rule_id": rule.rule_id}
    return None


def _consumers_for(dataset: str | None, kind: str) -> list[str]:
    """Consumer views affected by an incident on ``dataset``."""
    consumers: list[str] = []
    if dataset:
        consumers.append(f"canonical_view:{dataset}")
    if kind in (KIND_SILENT_LOSS, KIND_SOURCE_DEVIATION, KIND_QUARANTINE):
        # Publication-facing consumers only see promoted batches, so a
        # quarantined/silent-loss source starves the public surface.
        if dataset == "race_results":
            consumers.append("public:results")
        elif dataset in ("tcc_listing", "irc_certificates", "orc_register"):
            consumers.append("public:ratings")
    return consumers


def build_recommended_action(
    engine: Engine,
    kind: str,
    source_slug: str | None,
    *,
    run_id: int | None = None,
    batch_keys: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the recommended replay-or-policy action for an incident.

    * **replay** when re-running the pipeline against stored raw
      artifacts can recover the loss (silent loss, parser-yield
      collapse, record-count collapse): the action embeds a ready
      :class:`ReplayPlanV1` (idempotent ``plan_id``) targeting the
      source's artifacts in the incident window.
    * **policy** when a human decision is required instead: quarantine
      release + rebaseline after an upstream site change (structure /
      content-type / fetch), dataset-SLO policy review, or ownership
      escalation for freshness breaches.
    """
    evidence = evidence or {}
    now = _now_utc()

    replay_kinds = {
        KIND_SILENT_LOSS,
        KIND_QUARANTINE,
    }
    replay_incident_types = {"record_count_collapse", "parser_yield_collapse"}
    incident_type = str(evidence.get("incident_type") or "")

    if source_slug and (
        kind in replay_kinds or incident_type in replay_incident_types
    ):
        plan = ReplayPlanV1(
            source_slug=source_slug,
            new_parser_version="current+fix",
            artifact_filter=ArtifactFilter(
                source_slug=source_slug,
                fetched_after=(now - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat(),
            ),
            created_by="data-health-incident-workflow",
            notes=(
                f"Replay recommended by incident analysis ({kind}"
                + (f", run_id={run_id}" if run_id is not None else "")
                + "). Re-parse stored raw artifacts with the fixed parser "
                  "into an isolated batch, compare, then promote."
            ),
        )
        return {
            "kind": ACTION_REPLAY,
            "summary": (
                f"Replay source {source_slug}: re-parse stored raw "
                "artifacts with the fixed parser into an isolated batch, "
                "review the comparison, then promote to recover the "
                "affected records."
            ),
            "replay_plan": plan.to_dict(),
        }

    # Policy actions.
    if kind == KIND_FRESHNESS:
        return {
            "kind": ACTION_POLICY,
            "policy": "ownership_escalation",
            "summary": (
                "Source is past its freshness budget. Check upstream "
                "availability; if the source is down or the cadence has "
                "changed, escalate to the dataset owner to adjust the "
                "schedule or retire the source per SOURCE-POLICY."
            ),
        }
    if kind == KIND_IDENTITY_UNCERTAINTY:
        return {
            "kind": ACTION_POLICY,
            "policy": "identity_review",
            "summary": (
                "Identity effects are awaiting review or were "
                "quarantined. Review the identity-gate queue, confirm "
                "or reject the merges/splits, then re-run identity "
                "resolution for the affected batches."
            ),
        }
    if kind == KIND_SLO_BREACH:
        return {
            "kind": ACTION_POLICY,
            "policy": "slo_review",
            "summary": (
                "SLO breach: the dataset is burning its error budget. "
                "Review the rule's playbook, remediate the underlying "
                "cause, and record a policy ruling if the threshold "
                "needs revisiting."
            ),
        }
    if kind == KIND_LINEAGE_GAP:
        return {
            "kind": ACTION_POLICY,
            "policy": "reconciliation_backfill",
            "summary": (
                "Pipeline runs completed without reconciliation "
                "reports. Wire the reconciler into the pipeline for "
                "these runs or backfill reconciliation so every run's "
                "stage counts are checked."
            ),
        }
    # Source deviations: a human must confirm the upstream change and
    # release quarantine + rebaseline.
    return {
        "kind": ACTION_POLICY,
        "policy": "quarantine_release",
        "summary": (
            "Confirm the upstream change is legitimate, fix the parser "
            "if needed, then release the publication quarantine and "
            "rebaseline the source fingerprint so checks stop alerting."
        ),
    }


# ---------------------------------------------------------------------------
# Alerting (same webhook convention as DP-01-05 / DP-05-03)
# ---------------------------------------------------------------------------


def send_incident_alert(
    incident: DataIncidentV1,
    webhook_url: str | None = None,
    *,
    transport: AlertTransport | None = None,
) -> bool:
    """Post an owner-tagged alert for a *new* incident.

    Discord/Slack-compatible payload carrying the incident id, owner
    (who must ack), severity, affected consumers and the recommended
    action.  Best-effort: alerting never raises and never blocks
    incident creation.
    """
    url = webhook_url or os.environ.get(INCIDENT_WEBHOOK_ENV, "")
    if not url:
        return False

    post = transport or _post_webhook
    owner = incident.owner.get("handle", "unassigned")
    title = f"Data incident {incident.incident_id}: {incident.title}"
    consumers = ", ".join(incident.affected_consumers) or "none recorded"
    action = incident.recommended_action.get("summary", "")
    lines = [
        f"*{title}*",
        f"*Kind:* {incident.kind}   *Severity:* {incident.severity}   "
        f"*Owner:* {owner} (must ack)",
        f"*Source:* {incident.source_slug or '—'}   "
        f"*Dataset:* {incident.dataset or '—'}",
        f"*Affected consumers:* {consumers}",
    ]
    if incident.affected_batches:
        lines.append(
            f"*Affected batches:* {', '.join(incident.affected_batches[:5])}"
        )
    if action:
        lines.append(f"*Recommended action:* {action}")

    if "discord" in url.lower():
        payload: dict[str, Any] = {
            "embeds": [
                {
                    "title": title,
                    "color": 0xFF0000
                    if incident.severity == SEVERITY_CRITICAL
                    else 0xFFA500,
                    "fields": [
                        {"name": "Owner", "value": owner, "inline": True},
                        {
                            "name": "Severity",
                            "value": incident.severity,
                            "inline": True,
                        },
                        {"name": "Kind", "value": incident.kind, "inline": True},
                        {
                            "name": "Recommended action",
                            "value": action[:1024] or "—",
                            "inline": False,
                        },
                    ],
                    "timestamp": incident.detected_at,
                }
            ]
        }
    else:
        payload = {"text": "\n".join(lines)}

    try:
        return bool(post(url, payload))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_incident(engine: Engine, incident: DataIncidentV1) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO data_incidents
                  (incident_id, kind, severity, status, source_slug, dataset,
                   title, summary, detected_at, acknowledged_at, resolved_at,
                   owner, acknowledged_by, affected_batches,
                   affected_consumers, evidence, recommended_action,
                   alert_sent_at, notes, schema_version)
                VALUES
                  (:incident_id, :kind, :severity, :status, :source_slug,
                   :dataset, :title, :summary, :detected_at, :acknowledged_at,
                   :resolved_at, :owner, :acknowledged_by, :affected_batches,
                   :affected_consumers, :evidence, :recommended_action,
                   :alert_sent_at, :notes, :schema_version)
                """
            ),
            {
                "incident_id": incident.incident_id,
                "kind": incident.kind,
                "severity": incident.severity,
                "status": incident.status,
                "source_slug": incident.source_slug,
                "dataset": incident.dataset,
                "title": incident.title,
                "summary": incident.summary,
                "detected_at": incident.detected_at,
                "acknowledged_at": incident.acknowledged_at,
                "resolved_at": incident.resolved_at,
                "owner": json.dumps(incident.owner, sort_keys=True),
                "acknowledged_by": incident.acknowledged_by,
                "affected_batches": json.dumps(incident.affected_batches),
                "affected_consumers": json.dumps(incident.affected_consumers),
                "evidence": json.dumps(incident.evidence, sort_keys=True, default=str),
                "recommended_action": json.dumps(
                    incident.recommended_action, sort_keys=True, default=str
                ),
                "alert_sent_at": incident.alert_sent_at,
                "notes": json.dumps(incident.notes, default=str),
                "schema_version": incident.schema_version,
            },
        )


def _row_to_incident(row: Any) -> DataIncidentV1:
    d = dict(row._mapping)
    return DataIncidentV1(
        incident_id=d["incident_id"],
        kind=d.get("kind", KIND_MANUAL),
        severity=d.get("severity", SEVERITY_WARNING),
        status=d.get("status", STATUS_OPEN),
        source_slug=d.get("source_slug"),
        dataset=d.get("dataset"),
        title=d.get("title", ""),
        summary=d.get("summary") or "",
        detected_at=_iso(d.get("detected_at")) or "",
        acknowledged_at=_iso(d.get("acknowledged_at")),
        resolved_at=_iso(d.get("resolved_at")),
        owner=_json_loads(d.get("owner"), {}),
        acknowledged_by=d.get("acknowledged_by"),
        affected_batches=_json_loads(d.get("affected_batches"), []),
        affected_consumers=_json_loads(d.get("affected_consumers"), []),
        evidence=_json_loads(d.get("evidence"), {}),
        recommended_action=_json_loads(d.get("recommended_action"), {}),
        alert_sent_at=_iso(d.get("alert_sent_at")),
        notes=_json_loads(d.get("notes"), []),
        schema_version=d.get("schema_version", SCHEMA_VERSION),
    )


def get_incident(engine: Engine, incident_id: str) -> DataIncidentV1 | None:
    """Fetch one incident by its public id."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT incident_id, kind, severity, status, source_slug,
                       dataset, title, summary, detected_at, acknowledged_at,
                       resolved_at, owner, acknowledged_by, affected_batches,
                       affected_consumers, evidence, recommended_action,
                       alert_sent_at, notes, schema_version
                FROM data_incidents WHERE incident_id = :iid
                """
            ),
            {"iid": incident_id},
        ).first()
    return _row_to_incident(row) if row else None


def list_incidents(
    engine: Engine,
    *,
    status: str | None = None,
    source_slug: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[DataIncidentV1]:
    """List incidents, newest first, optionally filtered."""
    clauses = []
    params: dict[str, Any] = {"lim": limit}
    if status is not None:
        if status == "active":
            clauses.append("status != :status")
            params["status"] = STATUS_RESOLVED
        else:
            clauses.append("status = :status")
            params["status"] = status
    if source_slug is not None:
        clauses.append("source_slug = :src")
        params["src"] = source_slug
    if kind is not None:
        clauses.append("kind = :kind")
        params["kind"] = kind
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT incident_id, kind, severity, status, source_slug,
                       dataset, title, summary, detected_at, acknowledged_at,
                       resolved_at, owner, acknowledged_by, affected_batches,
                       affected_consumers, evidence, recommended_action,
                       alert_sent_at, notes, schema_version
                FROM data_incidents
                {where}
                ORDER BY detected_at DESC, id DESC
                LIMIT :lim
                """
            ),
            params,
        ).fetchall()
    return [_row_to_incident(r) for r in rows]


# ---------------------------------------------------------------------------
# Incident creation (detector ingestion + synthetic)
# ---------------------------------------------------------------------------


def create_incident(
    engine: Engine,
    *,
    kind: str,
    title: str,
    severity: str = SEVERITY_WARNING,
    source_slug: str | None = None,
    dataset: str | None = None,
    summary: str = "",
    affected_batches: list[str] | None = None,
    affected_consumers: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    recommended_action: dict[str, Any] | None = None,
    notes: list[dict[str, str]] | None = None,
    detected_at: datetime | None = None,
    alert: bool = True,
    webhook_url: str | None = None,
    alert_transport: AlertTransport | None = None,
) -> DataIncidentV1:
    """Create a data incident, assign ownership and alert the owner.

    This is the single creation path — detector ingestion
    (:func:`create_incident_from_health_event`,
    :func:`create_incident_from_reconciliation`) and synthetic
    verification incidents both go through it, so alerts, ownership and
    evidence handling are identical for real and synthetic incidents.
    """
    detected = detected_at or _now_utc()
    dataset = dataset or dataset_for_source(source_slug)
    evidence = dict(evidence or {})

    owner = _owner_for(kind, dataset)
    slo = _slo_for(kind, dataset)
    if slo:
        evidence.setdefault("slo", slo)

    batches = list(affected_batches or [])
    if not batches and evidence.get("batch_keys"):
        batches = list(evidence["batch_keys"])
    consumers = list(affected_consumers or []) or _consumers_for(dataset, kind)

    action = recommended_action or build_recommended_action(
        engine,
        kind,
        source_slug,
        run_id=evidence.get("run_id"),
        batch_keys=batches,
        evidence=evidence,
    )

    incident = DataIncidentV1(
        incident_id=_new_incident_id(),
        kind=kind,
        severity=severity,
        status=STATUS_OPEN,
        source_slug=source_slug,
        dataset=dataset,
        title=title,
        summary=summary,
        detected_at=detected.isoformat(),
        owner=owner.to_dict(),
        affected_batches=batches,
        affected_consumers=consumers,
        evidence=evidence,
        recommended_action=action,
        notes=list(notes or []),
    )

    if alert:
        sent = send_incident_alert(
            incident, webhook_url, transport=alert_transport
        )
        if sent:
            incident.alert_sent_at = _now_iso()

    _persist_incident(engine, incident)
    return incident


def create_incident_from_health_event(
    engine: Engine,
    event: Any,
    *,
    alert: bool = True,
    webhook_url: str | None = None,
    alert_transport: AlertTransport | None = None,
) -> DataIncidentV1 | None:
    """Ingest a material source-monitor health event as an incident.

    ``event`` is a
    :class:`~irc_data.diagnostics.source_monitor.SourceHealthEventV1`
    (or a compatible mapping).  Non-material events do not create
    incidents — they are signal, not recovery work.
    """
    get = event.get if isinstance(event, dict) else lambda k, d=None: getattr(event, k, d)
    if not get("material", False):
        return None

    source_id = get("source_id")
    deviations = list(get("deviations") or [])
    incident_type = None
    # Classify via the same taxonomy the monitor writes on
    # ``source_incidents`` rows.
    from irc_data.diagnostics.source_monitor import _classify_incident_type

    incident_type = _classify_incident_type(deviations)
    evidence = {
        "health_event_ids": ([get("id")] if get("id") is not None else []),
        "deviations": deviations,
        "incident_type": incident_type,
        "source_incident_id": get("incident_id"),
        "quarantined": bool(get("quarantined", False)),
        "diff_ratio": get("diff_ratio"),
        "sample_records": get("sample_records"),
        "content_excerpt": get("content_excerpt"),
    }
    title = f"Source deviation on {source_id}: {', '.join(deviations) or 'material change'}"
    summary = (
        f"Material deviation detected on source {source_id} "
        f"({get('url') or 'no-url'}); publication quarantined "
        f"(source_incident #{get('incident_id')})."
    )
    return create_incident(
        engine,
        kind=KIND_SOURCE_DEVIATION,
        title=title,
        severity=SEVERITY_CRITICAL,
        source_slug=source_id,
        summary=summary,
        evidence=evidence,
        alert=alert,
        webhook_url=webhook_url,
        alert_transport=alert_transport,
    )


def create_incident_from_reconciliation(
    engine: Engine,
    report: Any,
    *,
    alert: bool = True,
    webhook_url: str | None = None,
    alert_transport: AlertTransport | None = None,
) -> DataIncidentV1 | None:
    """Ingest a blocking reconciliation report as a silent-loss incident.

    ``report`` is a
    :class:`~irc_data.diagnostics.reconciliation.ReconciliationReportV1`
    (or a compatible mapping).  ``allow`` decisions do not create
    incidents.
    """
    get = report.get if isinstance(report, dict) else lambda k, d=None: getattr(report, k, d)
    if get("decision", recon.DECISION_ALLOW) != recon.DECISION_BLOCK:
        return None

    source_id = get("source_id")
    counts = dict(get("counts") or {})
    evidence = {
        "reconciliation_report_ids": [get("report_id")],
        "run_id": get("run_id"),
        "variance": get("variance"),
        "unexplained_reasons": get("unexplained_reasons") or {},
        "yield_ratio": get("yield_ratio"),
        "baseline_yield_p10": get("baseline_yield_p10"),
        "abrupt_yield_change": bool(get("abrupt_yield_change", False)),
        "counts": counts,
        "block_reason": get("block_reason"),
    }
    title = (
        f"Silent loss suspected on {source_id}: "
        f"variance={get('variance')} yield={get('yield_ratio'):.2f}"
        if isinstance(get("yield_ratio"), (int, float))
        else f"Silent loss suspected on {source_id}: variance={get('variance')}"
    )
    summary = (
        f"Reconciliation blocked promotion for run {get('run_id')} on "
        f"{source_id}: {get('block_reason') or 'unexplained variance'}."
    )
    return create_incident(
        engine,
        kind=KIND_SILENT_LOSS,
        title=title,
        severity=SEVERITY_CRITICAL,
        source_slug=source_id,
        summary=summary,
        evidence=evidence,
        alert=alert,
        webhook_url=webhook_url,
        alert_transport=alert_transport,
    )


# ---------------------------------------------------------------------------
# Workflow: acknowledge / mitigate / resolve / note
# ---------------------------------------------------------------------------


def _transition(
    engine: Engine,
    incident_id: str,
    new_status: str,
    *,
    actor: str | None = None,
    note: str | None = None,
) -> DataIncidentV1:
    incident = get_incident(engine, incident_id)
    if incident is None:
        raise IncidentWorkflowError(f"incident {incident_id!r} not found")
    if new_status not in INCIDENT_STATUSES:
        raise IncidentWorkflowError(f"unknown status {new_status!r}")
    if not incident.can_transition(new_status):
        raise IncidentWorkflowError(
            f"illegal transition {incident.status} → {new_status} "
            f"for incident {incident_id}"
        )

    now = _now_iso()
    if note:
        incident.notes.append(
            {"at": now, "by": actor or "unknown", "note": note}
        )
    if new_status == STATUS_ACKNOWLEDGED:
        incident.acknowledged_at = now
        incident.acknowledged_by = actor or incident.acknowledged_by
        if not note:
            incident.notes.append(
                {
                    "at": now,
                    "by": actor or "unknown",
                    "note": "acknowledged; owner engaged",
                }
            )
    if new_status == STATUS_RESOLVED:
        incident.resolved_at = now
    incident.status = new_status

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE data_incidents
                SET status = :status,
                    acknowledged_at = :acknowledged_at,
                    acknowledged_by = :acknowledged_by,
                    resolved_at = :resolved_at,
                    notes = :notes,
                    updated_at = CURRENT_TIMESTAMP
                WHERE incident_id = :iid
                """
            ),
            {
                "status": incident.status,
                "acknowledged_at": incident.acknowledged_at,
                "acknowledged_by": incident.acknowledged_by,
                "resolved_at": incident.resolved_at,
                "notes": json.dumps(incident.notes, default=str),
                "iid": incident.incident_id,
            },
        )
    return incident


def acknowledge_incident(
    engine: Engine,
    incident_id: str,
    *,
    actor: str,
    note: str | None = None,
) -> DataIncidentV1:
    """The owner acknowledges the incident — the recovery work is claimed."""
    return _transition(
        engine, incident_id, STATUS_ACKNOWLEDGED, actor=actor, note=note
    )


def start_mitigation(
    engine: Engine,
    incident_id: str,
    *,
    actor: str,
    note: str | None = None,
) -> DataIncidentV1:
    """The owner starts executing the recommended action."""
    return _transition(
        engine, incident_id, STATUS_MITIGATING, actor=actor, note=note
    )


def resolve_incident(
    engine: Engine,
    incident_id: str,
    *,
    actor: str,
    resolution: str,
) -> DataIncidentV1:
    """Resolve the incident with a resolution note (required)."""
    if not resolution:
        raise IncidentWorkflowError("resolution note is required")
    return _transition(
        engine, incident_id, STATUS_RESOLVED, actor=actor, note=resolution
    )


def add_incident_note(
    engine: Engine,
    incident_id: str,
    *,
    actor: str,
    note: str,
) -> DataIncidentV1:
    """Append a note without changing the workflow state."""
    incident = get_incident(engine, incident_id)
    if incident is None:
        raise IncidentWorkflowError(f"incident {incident_id!r} not found")
    incident.notes.append(
        {"at": _now_iso(), "by": actor, "note": note}
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE data_incidents SET notes = :notes, "
                "updated_at = CURRENT_TIMESTAMP WHERE incident_id = :iid"
            ),
            {"notes": json.dumps(incident.notes, default=str), "iid": incident_id},
        )
    return incident


# ---------------------------------------------------------------------------
# Dashboard aggregation — the six scope signals
# ---------------------------------------------------------------------------


def _safe(label: str, fn: Callable[[], Any], default: Any) -> tuple[Any, bool]:
    """Run ``fn`` defensively; returns ``(result, available)``.

    Cross-stack reads must degrade gracefully: when an upstream table is
    absent (minimal test schema, partially-migrated dev DB) the signal
    reports ``available=False`` instead of breaking the dashboard.
    """
    try:
        return fn(), True
    except Exception:
        return default, False


def _source_incident_rows(
    engine: Engine, *, statuses: tuple[str, ...], window_start: datetime | None
) -> tuple[list[dict[str, Any]], bool]:
    def _q() -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        status_sql = ", ".join(f"'{s}'" for s in statuses)
        sql = (
            "SELECT id, source_id, url, incident_type, detected_at, "
            "       resolved_at, status, deviations "
            f"FROM source_incidents WHERE status IN ({status_sql})"
        )
        if window_start is not None:
            sql += " AND detected_at >= :ws"
            params["ws"] = window_start
        sql += " ORDER BY detected_at DESC"
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]

    return _safe("source_incidents", _q, [])


def _active_quarantines(engine: Engine) -> tuple[list[dict[str, Any]], bool]:
    def _q() -> list[dict[str, Any]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, source_id, incident_id, reason, created_at "
                    "FROM publication_quarantine WHERE status = 'active' "
                    "ORDER BY created_at DESC"
                )
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    return _safe("publication_quarantine", _q, [])


def _recon_block_rows(
    engine: Engine, window_start: datetime
) -> tuple[list[dict[str, Any]], bool]:
    def _q() -> list[dict[str, Any]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT report_id, run_id, source_id, checked_at, "
                    "       variance, yield_ratio, block_reason "
                    "FROM reconciliation_reports "
                    "WHERE decision = 'block' AND checked_at >= :ws "
                    "ORDER BY checked_at DESC"
                ),
                {"ws": window_start},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    return _safe("reconciliation_reports", _q, [])


def _recon_report_rows(
    engine: Engine, window_start: datetime
) -> tuple[list[dict[str, Any]], bool]:
    def _q() -> list[dict[str, Any]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT report_id, run_id, source_id, checked_at, "
                    "       variance, yield_ratio, decision, "
                    "       abrupt_yield_change "
                    "FROM reconciliation_reports "
                    "WHERE checked_at >= :ws "
                    "ORDER BY checked_at DESC"
                ),
                {"ws": window_start},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    return _safe("reconciliation_reports", _q, [])


def _health_event_counts(
    engine: Engine, window_start: datetime
) -> tuple[dict[str, dict[str, int]], bool]:
    """Per-source health-event counts by status over the window."""

    def _q() -> dict[str, dict[str, int]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT source_id, status, COUNT(*) AS n, "
                    "       SUM(CASE WHEN material THEN 1 ELSE 0 END) AS material_n "
                    "FROM source_health_events WHERE checked_at >= :ws "
                    "GROUP BY source_id, status"
                ),
                {"ws": window_start},
            ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            d = out.setdefault(r.source_id, {"events": 0, "material": 0})
            n = int(r.n)
            d["events"] += n
            d[r.status] = d.get(r.status, 0) + n
            d["material"] += int(r.material_n or 0)
        return out

    return _safe("source_health_events", _q, {})


def _batch_status_counts(engine: Engine) -> tuple[dict[str, int], bool]:
    def _q() -> dict[str, int]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT gate, status, COUNT(*) AS n FROM quality_batches "
                    "GROUP BY gate, status"
                )
            ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            out[f"{r.gate}:{r.status}"] = int(r.n)
        return out

    return _safe("quality_batches", _q, {})


def _gate_quarantine_counts(engine: Engine) -> tuple[dict[str, Any], bool]:
    def _q() -> dict[str, Any]:
        with engine.connect() as conn:
            by_source = conn.execute(
                text(
                    "SELECT source_slug, COUNT(*) AS n FROM quality_quarantine "
                    "WHERE status = 'open' GROUP BY source_slug"
                )
            ).fetchall()
            total = conn.execute(
                text(
                    "SELECT COUNT(*) FROM quality_quarantine "
                    "WHERE status = 'open'"
                )
            ).scalar()
        return {
            "open_total": int(total or 0),
            "open_by_source": {r.source_slug: int(r.n) for r in by_source},
        }

    return _safe("quality_quarantine", _q, {"open_total": 0, "open_by_source": {}})


def _lineage_gap_runs(
    engine: Engine, window_start: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """Completed pipeline runs in the window with no reconciliation report.

    These are the *lineage gaps*: the run finished, but its stage counts
    were never checked against the conservation invariant, so silent loss
    in that window would be invisible.
    """

    def _q() -> list[dict[str, Any]]:
        if not _table_exists(engine, "reconciliation_reports"):
            return []
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT il.id AS run_id, il.source, il.started_at,
                           il.records_found, il.records_new
                    FROM ingestion_log il
                    LEFT JOIN reconciliation_reports rr
                           ON rr.run_id = il.id
                    WHERE il.status = :completed
                      AND il.started_at >= :ws
                      AND rr.run_id IS NULL
                    ORDER BY il.started_at DESC
                    """
                ),
                {"completed": run_ledger.STATUS_COMPLETED, "ws": window_start},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    return _safe("ingestion_log", _q, [])


def _latest_reports_by_source(
    engine: Engine,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Latest reconciliation report per source (any age)."""

    def _q() -> dict[str, dict[str, Any]]:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.report_id, r.run_id, r.source_id, r.checked_at,
                           r.variance, r.yield_ratio, r.baseline_yield_p10,
                           r.baseline_yield_p50, r.decision,
                           r.abrupt_yield_change, r.block_reason
                    FROM reconciliation_reports r
                    JOIN (
                        SELECT source_id, MAX(checked_at) AS mx
                        FROM reconciliation_reports GROUP BY source_id
                    ) m ON m.source_id = r.source_id AND m.mx = r.checked_at
                    """
                )
            ).fetchall()
        return {r.source_id: dict(r._mapping) for r in rows}

    return _safe("reconciliation_reports", _q, {})


def _yield_baselines(engine: Engine) -> tuple[dict[str, dict[str, Any]], bool]:
    """Trailing yield bands for every source with baseline samples."""

    def _q() -> dict[str, dict[str, Any]]:
        with engine.connect() as conn:
            sources = conn.execute(
                text("SELECT DISTINCT source_id FROM pipeline_count_baseline")
            ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for row in sources:
            sid = row[0]
            baseline = recon.get_yield_baseline(engine, sid)
            if baseline:
                out[sid] = baseline
        return out

    return _safe("pipeline_count_baseline", _q, {})


def get_health_dashboard(
    engine: Engine,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate the data-health dashboard (DP-05-04).

    One call returns the six scope signals per source plus the active
    incident queue.  Every section reports whether its underlying source
    was *available* so a partially-deployed stack degrades instead of
    erroring.
    """
    now = now or _now_utc()
    window_start = now - timedelta(days=window_days)

    # -- raw signals ------------------------------------------------------
    ledger_rows, ledger_ok = _safe(
        "run_ledger",
        lambda: run_ledger.get_source_health_summary(
            engine, now=now, aggregate_days=window_days
        ),
        [],
    )
    health_counts, health_ok = _health_event_counts(engine, window_start)
    open_incidents, src_inc_ok = _source_incident_rows(
        engine, statuses=("open",), window_start=None
    )
    quarantines, quar_ok = _active_quarantines(engine)
    gate_q, gate_q_ok = _gate_quarantine_counts(engine)
    batch_counts, batch_ok = _batch_status_counts(engine)
    lineage_gaps, lineage_ok = _lineage_gap_runs(engine, window_start)
    latest_recon, recon_ok = _latest_reports_by_source(engine)
    window_recon, _ = _recon_report_rows(engine, window_start)
    yield_baselines, baseline_ok = _yield_baselines(engine)
    incidents = list_incidents(engine, status="active", limit=200)
    incidents_by_source: dict[str, int] = {}
    for inc in incidents:
        if inc.source_slug:
            incidents_by_source[inc.source_slug] = (
                incidents_by_source.get(inc.source_slug, 0) + 1
            )

    # -- per-source rollups -------------------------------------------------
    sources: dict[str, dict[str, Any]] = {}

    def _src(slug: str) -> dict[str, Any]:
        return sources.setdefault(
            slug,
            {
                "source": slug,
                "dataset": dataset_for_source(slug),
                "freshness": None,
                "latest_yield": None,
                "active_quarantine": False,
                "open_source_incidents": 0,
                "open_data_incidents": 0,
                "gate_quarantined_batches": 0,
                "lineage_gap_runs": 0,
            },
        )

    budget_s = DEFAULT_FRESHNESS_BUDGET_HOURS * 3600.0
    for row in ledger_rows:
        slug = row["source"]
        secs = row.get("seconds_since_last_run")
        last_completed = _as_dt(row.get("last_completed_at"))
        completed_age = (
            (now - last_completed).total_seconds() if last_completed else None
        )
        stale = completed_age is None or completed_age > budget_s
        _src(slug)["freshness"] = {
            "last_started_at": row.get("last_started_at"),
            "last_completed_at": row.get("last_completed_at"),
            "last_new_data_at": row.get("last_new_data_at"),
            "seconds_since_last_run": secs,
            "seconds_since_last_success": completed_age,
            "budget_seconds": budget_s,
            "stale": stale,
            f"runs_{window_days}d": row.get(f"runs_{window_days}d", 0),
            f"failed_{window_days}d": row.get(f"failed_{window_days}d", 0),
            f"rows_new_{window_days}d": row.get(f"rows_new_{window_days}d", 0),
        }

    for slug, counts in health_counts.items():
        s = _src(slug)
        s["health_events"] = counts

    for inc in open_incidents:
        s = _src(inc["source_id"])
        s["open_source_incidents"] += 1

    for q in quarantines:
        s = _src(q["source_id"])
        s["active_quarantine"] = True
        s["quarantine"] = {
            "incident_id": q.get("incident_id"),
            "reason": q.get("reason"),
            "since": _iso(q.get("created_at")),
        }

    for slug, n in gate_q["open_by_source"].items():
        _src(slug)["gate_quarantined_batches"] = n

    for slug, n in incidents_by_source.items():
        _src(slug)["open_data_incidents"] = n

    gaps_by_source: dict[str, int] = {}
    for g in lineage_gaps:
        gaps_by_source[g["source"]] = gaps_by_source.get(g["source"], 0) + 1
    for slug, n in gaps_by_source.items():
        _src(slug)["lineage_gap_runs"] = n

    # Latest yield per source (+ baseline band).
    for slug, rep in latest_recon.items():
        s = _src(slug)
        band = yield_baselines.get(slug)
        s["latest_yield"] = {
            "run_id": rep.get("run_id"),
            "checked_at": _iso(rep.get("checked_at")),
            "yield_ratio": rep.get("yield_ratio"),
            "variance": rep.get("variance"),
            "decision": rep.get("decision"),
            "abrupt_yield_change": bool(rep.get("abrupt_yield_change")),
            "block_reason": rep.get("block_reason"),
            "baseline_p10": (band or {}).get("p10"),
            "baseline_p50": (band or {}).get("p50"),
            "baseline_samples": (band or {}).get("samples"),
        }

    # Identity uncertainty: batches held at the identity gate.
    identity_awaiting = batch_counts.get("identity:awaiting_promotion", 0)
    identity_quarantined = batch_counts.get("identity:quarantined", 0)

    # SLO breaches: open source incidents + blocking reconciliations in window.
    slo_breaches: list[dict[str, Any]] = []
    for inc in open_incidents:
        cat = SOURCE_INCIDENT_CATEGORY.get(
            inc.get("incident_type") or "", KIND_SOURCE_DEVIATION
        )
        slo_breaches.append(
            {
                "source_incident_id": inc["id"],
                "report_id": None,
                "source": inc.get("source_id"),
                "kind": cat,
                "incident_type": inc.get("incident_type"),
                "detected_at": _iso(inc.get("detected_at")),
                "deviations": _json_loads(inc.get("deviations"), []),
                "slo": _slo_for(cat, dataset_for_source(inc.get("source_id"))),
            }
        )
    for rep in _recon_block_rows(engine, window_start)[0]:
        slo_breaches.append(
            {
                "report_id": rep.get("report_id"),
                "source": rep.get("source_id"),
                "kind": KIND_SILENT_LOSS,
                "detected_at": _iso(rep.get("checked_at")),
                "block_reason": rep.get("block_reason"),
                "variance": rep.get("variance"),
                "slo": _slo_for(
                    KIND_SILENT_LOSS, dataset_for_source(rep.get("source_id"))
                ),
            }
        )

    # -- overview ------------------------------------------------------------
    blocking_reports = [r for r in window_recon if r.get("decision") == "block"]
    overview = {
        "sources_tracked": len(sources),
        "sources_stale": sum(
            1
            for s in sources.values()
            if (s.get("freshness") or {}).get("stale")
        ),
        "sources_quarantined": sum(
            1 for s in sources.values() if s.get("active_quarantine")
        ),
        "open_source_incidents": len(open_incidents),
        "open_data_incidents": len(incidents),
        "unacknowledged_data_incidents": sum(
            1 for i in incidents if i.status == STATUS_OPEN
        ),
        "blocking_reconciliations_in_window": len(blocking_reports),
        "lineage_gap_runs_in_window": len(lineage_gaps),
        "gate_quarantine_open": gate_q["open_total"],
        "identity_awaiting_review": identity_awaiting,
        "identity_quarantined": identity_quarantined,
        "slo_breaches": len(slo_breaches),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now.isoformat(),
        "window_days": window_days,
        "overview": overview,
        "sources": sorted(sources.values(), key=lambda s: s["source"]),
        "identity_uncertainty": {
            "available": batch_ok,
            "awaiting_review_batches": identity_awaiting,
            "quarantined_batches": identity_quarantined,
            "consumer_impact": (
                "canonical entity registry / consumer views see only "
                "promoted identity effects"
            ),
        },
        "lineage_gaps": {
            "available": lineage_ok,
            "runs": [
                {
                    "run_id": g["run_id"],
                    "source": g["source"],
                    "started_at": _iso(g.get("started_at")),
                    "records_found": g.get("records_found"),
                    "records_new": g.get("records_new"),
                }
                for g in lineage_gaps
            ],
        },
        "slo_breaches": slo_breaches,
        "active_quarantines": [
            {
                "source": q.get("source_id"),
                "incident_id": q.get("incident_id"),
                "reason": q.get("reason"),
                "since": _iso(q.get("created_at")),
            }
            for q in quarantines
        ],
        "incidents": [i.to_dict() for i in incidents],
        "availability": {
            "run_ledger": ledger_ok,
            "source_health_events": health_ok,
            "source_incidents": src_inc_ok,
            "publication_quarantine": quar_ok,
            "quality_batches": batch_ok,
            "quality_quarantine": gate_q_ok,
            "reconciliation_reports": recon_ok,
            "yield_baselines": baseline_ok,
        },
    }


# ---------------------------------------------------------------------------
# Dashboard <-> quality-event reconciliation
# ---------------------------------------------------------------------------


def reconcile_incidents_to_events(
    engine: Engine, *, limit: int = 200
) -> dict[str, Any]:
    """Check every incident's evidence against the quality-event tables.

    The acceptance criterion — *dashboard reconciles to quality events* —
    means every incident on the board must trace back to real persisted
    events.  For each incident we resolve:

    * ``evidence.health_event_ids``          -> ``source_health_events.id``
    * ``evidence.reconciliation_report_ids`` -> ``reconciliation_reports.report_id``
    * ``evidence.source_incident_id``        -> ``source_incidents.id``
    * ``evidence.quarantine_ids``            -> ``quality_quarantine.quarantine_id``
    * ``evidence.batch_keys``                -> ``quality_batches.batch_key``

    An incident with *no* evidence refs at all is flagged
    (``no_evidence``) — detector-created incidents always carry refs, so
    a bare incident means someone filed it manually without evidence.
    """
    incidents = list_incidents(engine, limit=limit)
    checks: list[dict[str, Any]] = []
    ok_count = 0

    # Cache table availability once.
    availability = {
        t: _table_exists(engine, t)
        for t in (
            "source_health_events",
            "reconciliation_reports",
            "source_incidents",
            "quality_quarantine",
            "quality_batches",
        )
    }

    def _exists(sql: str, params: dict[str, Any]) -> bool:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).first() is not None

    for inc in incidents:
        ev = inc.evidence or {}
        resolved: list[str] = []
        missing: list[str] = []

        def _check(table: str, sql: str, value: Any) -> None:
            label = f"{table}:{value}"
            if not availability.get(table):
                missing.append(f"{label} (table unavailable)")
                return
            if _exists(sql, {"v": value}):
                resolved.append(label)
            else:
                missing.append(label)

        for eid in ev.get("health_event_ids") or []:
            _check(
                "source_health_events",
                "SELECT 1 FROM source_health_events WHERE id = :v",
                eid,
            )
        for rid in ev.get("reconciliation_report_ids") or []:
            _check(
                "reconciliation_reports",
                "SELECT 1 FROM reconciliation_reports WHERE report_id = :v",
                rid,
            )
        if ev.get("source_incident_id") is not None:
            _check(
                "source_incidents",
                "SELECT 1 FROM source_incidents WHERE id = :v",
                ev["source_incident_id"],
            )
        for qid in ev.get("quarantine_ids") or []:
            _check(
                "quality_quarantine",
                "SELECT 1 FROM quality_quarantine WHERE quarantine_id = :v",
                qid,
            )
        for bk in ev.get("batch_keys") or []:
            _check(
                "quality_batches",
                "SELECT 1 FROM quality_batches WHERE batch_key = :v",
                bk,
            )

        if resolved and not missing:
            status = "ok"
            ok_count += 1
        elif resolved:
            status = "partial"
        elif not resolved and not missing:
            status = "no_evidence"
        else:
            status = "unreconciled"

        checks.append(
            {
                "incident_id": inc.incident_id,
                "kind": inc.kind,
                "status": inc.status,
                "reconciliation": status,
                "resolved_refs": resolved,
                "missing_refs": missing,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": _now_iso(),
        "incidents_checked": len(checks),
        "reconciled": ok_count,
        "unreconciled": [c for c in checks if c["reconciliation"] != "ok"],
        "checks": checks,
    }


__all__ = [
    "ACTION_POLICY",
    "ACTION_REPLAY",
    "DEFAULT_FRESHNESS_BUDGET_HOURS",
    "DEFAULT_WINDOW_DAYS",
    "INCIDENT_STATUSES",
    "KIND_FRESHNESS",
    "KIND_IDENTITY_UNCERTAINTY",
    "KIND_LINEAGE_GAP",
    "KIND_MANUAL",
    "KIND_QUARANTINE",
    "KIND_SILENT_LOSS",
    "KIND_SLO_BREACH",
    "KIND_SOURCE_DEVIATION",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "SEVERITY_CRITICAL",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "STATUS_ACKNOWLEDGED",
    "STATUS_MITIGATING",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "DataIncidentV1",
    "IncidentWorkflowError",
    "acknowledge_incident",
    "add_incident_note",
    "build_recommended_action",
    "create_incident",
    "create_incident_from_health_event",
    "create_incident_from_reconciliation",
    "dataset_for_source",
    "get_health_dashboard",
    "get_incident",
    "init_data_incident_tables",
    "list_incidents",
    "reconcile_incidents_to_events",
    "resolve_incident",
    "send_incident_alert",
    "start_mitigation",
]
