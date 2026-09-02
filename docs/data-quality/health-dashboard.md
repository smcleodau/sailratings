# Data health dashboard & incident workflow (DP-05-04)

**Goal: turn quality failures into owned recovery work.**

`irc_data.quality.health` is the read/aggregate model over the quality
stack plus the owned-incident workflow on top of it.  It mounts in the
AD-01 admin console at `/admin/data-health` (API: `/v1/admin/data-health/*`).

## Dashboard signals

`get_health_dashboard(engine)` aggregates the six scope signals, per
governed source, in one call.  Every section reports an `availability`
flag — when an upstream stack's tables are absent the signal degrades to
`available=False` instead of breaking the dashboard.

| Signal | Source of truth | What it shows |
|---|---|---|
| **Source freshness** | `ingestion_log` via `db.run_ledger` (OPS-01-03) | last-run / last-success / last-new-data timestamps, stale flag vs the freshness budget, trailing-window runs/fails/rows |
| **Pipeline yields** | `reconciliation_reports` + `pipeline_count_baseline` (DP-05-03) | latest yield ratio, unexplained variance, `allow`/`block` decision, trailing [p10, p50] baseline band |
| **Quarantine** | `publication_quarantine` (DP-01-05/03) + `quality_quarantine` (DP-05-02) | active publication quarantines with reason/since; open gate-quarantine queue depth |
| **Lineage gaps** | `ingestion_log` LEFT JOIN `reconciliation_reports` | completed runs in the window with **no** reconciliation report — stage counts never checked against the conservation invariant |
| **Identity uncertainty** | `quality_batches` (identity gate, DP-05-02) | batches in `awaiting_promotion` (unreviewed identity effects) + `quarantined` (held merges/splits) |
| **SLO breaches** | `source_incidents` (open) + blocking `reconciliation_reports` in window | each breach carries the DP-05-01 SLO it burns against (rule id + target/window) |

## Incident workflow

`DataIncidentV1` (the output contract) is one owned unit of recovery
work:

```
open → acknowledged → mitigating → resolved
   ↖ (mitigating → acknowledged allowed; resolved is terminal)
```

* **Owner** — resolved from the DP-05-01 registry: the accountable owner
  of a *blocking* rule of the matching dimension on the incident's
  dataset (identity incidents always route to the identity owner).
  Escalation chains to the platform authority (SOURCE-POLICY).
* **Affected batches / consumers** — batch keys from the evidence, plus
  consumer views (`canonical_view:<dataset>`, `public:ratings` /
  `public:results` for publication-facing kinds).
* **Evidence** — refs back to the quality events that fired:
  `health_event_ids`, `reconciliation_report_ids`,
  `source_incident_id`, `quarantine_ids`, `batch_keys`, `run_id`,
  `verdict_ids`.  This is what makes the dashboard *reconcile* to the
  quality events.
* **Recommended action** — replay **or** policy:
  * `replay` — silent loss, parser-yield/record-count collapse: a
    ready-to-submit DP-02-04 `ReplayPlanV1` (idempotent `plan_id`,
    artifact filter scoped to the source's incident window).
  * `policy` — `quarantine_release` (confirm upstream change → release
    quarantine + rebaseline), `ownership_escalation` (freshness),
    `identity_review`, `slo_review`, `reconciliation_backfill`
    (lineage gaps).

Creation paths (all equivalent — synthetic verification incidents use
the same code path as detector-created ones):

* `create_incident()` — manual / synthetic.
* `create_incident_from_health_event()` — a material
  `SourceHealthEventV1` (DP-01-05).
* `create_incident_from_reconciliation()` — a blocking
  `ReconciliationReportV1` (DP-05-03).

Every creation fires the health-check webhook (same
`SOURCE_MONITOR_WEBHOOK_URL` convention as DP-01-05/DP-05-03) with the
incident id and the owner-who-must-ack, so alerting happens in the same
cycle as detection.  Alerting is best-effort and never blocks creation.

## Reconciliation to quality events

`reconcile_incidents_to_events(engine)` walks every incident and
resolves each evidence ref against its quality-event table.  An incident
with unresolved refs is `unreconciled` (or `partial`); one with no refs
at all is `no_evidence` — manual filings without evidence are visible
by construction.  Exposed at
`GET /v1/admin/data-health/incidents/reconcile`.

## API

All endpoints behind the admin credential (`Authorization: Bearer …`):

```
GET  /v1/admin/data-health/dashboard?window_days=7
GET  /v1/admin/data-health/incidents?status=active&source=…&kind=…
POST /v1/admin/data-health/incidents                # synthetic / manual
GET  /v1/admin/data-health/incidents/reconcile
GET  /v1/admin/data-health/incidents/{id}
POST /v1/admin/data-health/incidents/{id}/acknowledge   {actor, note?}
POST /v1/admin/data-health/incidents/{id}/mitigate      {actor, note?}
POST /v1/admin/data-health/incidents/{id}/resolve       {actor, resolution}
POST /v1/admin/data-health/incidents/{id}/notes         {actor, note}
```

## Schema

Alembic `0029_data_incidents` (chain: `20260904b → 20260905a`) creates
`data_incidents`.  `health.init_data_incident_tables(engine)` mirrors it
for SQLite tests.

## Verification

Synthetic incidents validate alerts, ownership, acknowledgement and
resolution:

```
cd api && pytest tests/quality/test_data_health.py tests/quality/test_data_health_api.py -q
```

* `TestIncidentWorkflow` — create → alert payload carries incident id +
  owner; owner assigned from the DP-05-01 registry; ack stamps actor +
  timestamp; resolve requires a resolution note; terminal state rejects
  further transitions.
* `TestDetectorIngestion` — material health events and blocking
  reconciliation reports become critical incidents with evidence and a
  replay/policy recommendation; non-material / allow decisions create
  nothing.
* `TestDashboard` — freshness staleness, yield blocks, quarantine,
  lineage gaps (closed once reconciled), identity uncertainty, and the
  incident queue counters.
* `TestReconcileToEvents` — detector incidents reconcile `ok` against
  their persisted quality events; ghost refs are `unreconciled`; bare
  manual incidents are `no_evidence`.
* `test_data_health_api.py` — the same flow end-to-end over HTTP with
  the admin credential enforced.
