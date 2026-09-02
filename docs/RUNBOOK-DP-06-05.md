# DP-06-05 — Operational Runbook: Continuous Source Collection

| | |
|---|---|
| **Issue** | DP-06-05 — Schedule incremental collection and operational runbook |
| **Goal** | Operate the source continuously rather than as a demo |
| **Verification** | `irc-data ops-soak` (soak test + failure drill), `api/scripts/verify_dp_06_05.py` |
| **Acceptance** | ① Seven consecutive scheduled cycles complete within SLO ② A deliberate source failure alerts and recovers without duplicate publication |
| **On completion** | The DP-00 bridge track retires |
| **Components** | DP-06-04 schedule/backoff · OPS-01-02 registry · OPS-01-04 watchdog · DP-01-03 checkpoints · DP-02-04 replay |

This runbook is the operational half of DP-06-05. The *mechanism* (Temporal
schedule, backoff, kill switch, health alert, checkpoint backup, reparse) is
code; this document is how an operator runs, verifies, and recovers the
continuously-collected source estate.

The deliverable that *proves* this runbook works is the signed soak report
produced by `irc-data ops-soak`. Where a procedure below has a soak-check,
the check name is given in `«…»` — the soak will fail (exit 2) if the
control regresses.

---

## 1. What "continuous operation" means here

Each governed source in the Data Source Register (`data_sources`) that is
`enabled` **and** `legal_status = 'approved'` has exactly one Temporal
schedule `source-<slug>`. The schedule fires the `SourceRunWorkflow` on the
register cadence. There is no implicit scheduling: the register is the single
source of truth.

| Control | Where it lives | Behaviour |
|---|---|---|
| **Schedule** | `irc_data.temporal.schedules.registry.ScheduleRegistry` | One schedule per enabled+approved source. Register add→create, cadence change→update, disable→**pause** (never delete), re-enable→unpause. `«disable_pauses_schedule»` `«schedule_preserved_not_deleted»` |
| **Overlap** | `SchedulePolicy(overlap=SKIP)` | One in-flight run per source — a slow run never double-fires. |
| **Backoff** | Register `retry_policy` → Temporal `RetryPolicy` (SCHEDULING-POLICY §4) | Failed run retries up to `max_attempts`; delay before attempt *n* is `backoff_seconds[n-1]`. Legacy rows fall back to 10 s → ×2 → max 10 min, 3 attempts. |
| **Kill switch** | `irc_data.sources.gate.CollectionGate` + register `enabled` | `enabled=FALSE` (or domain disable, global `COLLECTION_ENABLED=false`, quarantine, takedown) halts collection at the next gate evaluation. `«gate_refuses_when_disabled»` `«run_fails_fast_when_disabled»` |
| **Health alert** | `irc_data.scrape_watchdog.run_watchdog` (OPS-01-04) | Detects a quiet source within one interval (15 min), sends one alert, honours the 4 h cooldown, closes on recovery. `«watchdog_detects_breach»` `«exactly_one_alert_sent»` |
| **Checkpoint backup** | DP-01-03 `AdapterCheckpointV1` | Collection progress is exportable/restorable so resume is idempotent. `«restore_round_trips»` `«resume_produces_no_refetch»` |
| **Reparse** | DP-02-04 replay | Reparsing from the raw lake is idempotent — no duplicate publication. `«reparse_no_duplicate_publication»` |

---

## 2. Operating the soak test (the acceptance evidence)

Run the full soak + failure drill:

```bash
cd api
PYTHONPATH=src irc-data ops-soak --cycles 7 --out /home/irc-data/logs/dp06-soak.json
# or, without the installed entry point:
PYTHONPATH=src python3 -m irc_data.cli ops-soak --cycles 7
```

Tune it:

| Flag | Default | Meaning |
|---|---|---|
| `--cycles` | 7 | Consecutive scheduled cycles (the acceptance count) |
| `--slo-seconds` | 30 | Per-cycle SLO budget |
| `--cadence` | `30min` | Register cadence driving the schedule interval |
| `--staleness-budget-hours` | 2.0 | Watchdog budget the failure drill breaches |
| `--pages` | 3 | Synthetic pages per cycle |
| `--work-dir` | temp | Keep the DB/checkpoint backups for inspection |
| `--out` | stdout | Write the signed report JSON |

**Exit status:** `0` = passed, `2` = any cycle breached SLO or any
failure-drill check failed.

**Signing.** Set `DP06_SOAK_SIGNING_KEY` to sign with a known key;
otherwise a fresh key is generated per run and only its id is recorded.
Verify a report:

```bash
PYTHONPATH=src python3 -c "
from irc_data.operations import OpsSoakReportV1, verify_report_signature
import sys
rep = OpsSoakReportV1.from_json(open(sys.argv[1]).read())
print('valid' if verify_report_signature(rep, KEY) else 'TAMPERED')
" /home/irc-data/logs/dp06-soak.json
```

The two acceptance criteria are the `passed_acceptance_criteria` block:
`seven_consecutive_cycles_within_slo` and
`failure_alerts_and_recovers_without_duplicate_publication`.

---

## 3. Reading a failed cycle

A cycle is `failed` when it raised (`error` non-empty) **or** exceeded
`slo_seconds` (`within_slo = false`). Triage:

1. **`error = SourceDisabledError` / `SourceNotApprovedError`** — the
   register row was disabled or un-approved mid-run. This is the kill switch
   working, not a bug. See §5.
2. **`error = ConnectionError` / timeout** — the source is down or
   rate-limiting. The schedule's backoff policy retries; if attempts are
   exhausted the run is marked failed in `source_runs` and the source becomes
   eligible for a staleness alert (§4).
3. **`within_slo = false` with no error** — the cycle succeeded but ran long.
   Widen `--slo-seconds` only after checking whether the source genuinely got
   slower (new pagination, a blocking WAF) — the SLO is the signal, not the
   problem.

---

## 4. Health alert → recovery (the watchdog loop)

The staleness watchdog (`irc-data scrape-watchdog`, cron every 15 min)
compares each active source's last successful `ingestion_log` row against its
`staleness_budget_hours`.

* **Breach** → one consolidated alert email + a `watchdog_alerts` row
  (`status='active'`) + the admin banner on `/justin/scrapers`.
  `«watchdog_detects_breach»`
* **Cooldown** — the same source is not re-alerted for `cooldown_hours`
  (default 4 h), even while it remains stale. `«cooldown_suppresses_duplicate_alert»`
  `«exactly_one_email_total»`
* **Recovery** — when the source records a success back within budget, the
  open alert row is closed (`status='recovered'`, `recovered_at` set) and a
  recovery email is sent. `«recovery_closes_alert»` `«no_open_alert_after_recovery»`

Alert history is retained in `watchdog_alerts` — never deleted by the
watchdog — so the incident trail is always inspectable.

---

## 5. Kill switch: disable / takedown / re-enable

**Disable (any of these halts scheduling *and* in-flight collection):**

1. `UPDATE data_sources SET enabled = FALSE WHERE slug = '<slug>';` — per-source
2. a `domain_disables` row for the source's domain (includes subdomains)
3. `COLLECTION_ENABLED=false` — global
4. monitor/incident quarantine (`quarantine_until`)
5. an operator takedown request

Effects (all verified by the soak): the Temporal schedule is **paused, never
deleted** (run history preserved), and the next `resolve_source` /
`SourceRunWorkflow` raises the non-retryable `SourceDisabledError`.

**Takedown acknowledgement window** is `kill_switch_ack_hours` (default 4 h)
per source — see SCHEDULING-POLICY §6.

**Re-enable** only after written approval from Stuart McLeod. Set
`enabled = TRUE`; the schedule registry unpauses on its next reconciliation
tick (≤ 5 min) and existing captures are quarantined to
`data/raw/quarantine/<slug>/` per policy.

---

## 6. Checkpoint backup & restore

Each adapter persists a DP-01-03 `AdapterCheckpointV1` (completed URLs +
content hashes + resume pointer). To back up / restore:

```bash
# Backup: copy the live checkpoint into the versioned backup dir.
cp data/checkpoints/<slug>.json data/checkpoint_backups/<slug>-$(date -u +%Y%m%dT%H%M%SZ).json

# Restore: copy back; the next collect() resumes without refetching.
cp data/checkpoint_backups/<slug>-<ts>.json data/checkpoints/<slug>.json
```

The soak proves the round-trip (`«restore_round_trips»`) and that a resumed
adapter does **not** refetch completed pages (`«resume_produces_no_refetch»`).

---

## 7. Reparse (idempotent replay)

To reparse a source from the raw lake (e.g. after a parser fix), replay its
captured artifacts through the gate + promotion path. Replay is idempotent:
the consumer view is content-keyed, so re-running publishes nothing new and
the promotion ledger is unchanged. The soak replays twice and asserts the
consumer view and publication count are identical
(`«reparse_consumer_view_unchanged»` `«reparse_no_duplicate_publication»`).

---

## 8. Incident runbook

| Symptom | Likely cause | Action |
|---|---|---|
| Watchdog email "N scrapers stale" | Cron stopped, source down, or rate-limited | Check `/justin/scrapers`; tail `/home/irc-data/logs/`; run the scraper manually; if the source is genuinely down, it self-recovers and the alert auto-closes |
| Same source re-alerts every 15 min | Cooldown regression (should be 4 h) | Inspect `watchdog_alerts.cooldown_until`; the soak's `«cooldown_suppresses_duplicate_alert»` catches this |
| Schedule vanished | Bug — schedules are **paused**, never deleted | Re-run `ScheduleRegistry.sync_from_register`; check `source_schedule_state` mirror |
| Consumer rows doubled after reparse | Replay idempotency broken | Stop; the soak's `«reparse_no_duplicate_publication»` must be green before resuming replays |
| Cycle exceeded SLO repeatedly | Source got slower / WAF / pagination growth | See §3.3; widen SLO only with a recorded reason |

**Escalation:** Stuart McLeod (`stuart@sailratings.com`). Legal/takedown
matters follow SOURCE-POLICY §5 and the `kill_switch_ack_hours` window.

---

## 9. Retiring the DP-00 bridge track

DP-06-05 is the last DP-06 ticket. When its soak passes and is signed:

* the DP-00 interim CLI-scraper bridge (`_dispatch_table` in
  `irc_data/temporal/ledger/activities.py`) is superseded by governed
  DP-01 adapters behind `SourceRunWorkflow`;
* the legacy `crontab.txt` scrape entries are retired in favour of the
  Temporal schedules (the run-ledger + watchdog keep their signals);
* mark the DP-00 bridge track **retired** on the programme board.

Until the soak is green in production-shaped conditions, keep the DP-00
bridge as the fallback path.

---

## 10. Verification artifacts

* `irc-data ops-soak` → signed `OpsSoakReportV1` (this runbook's checks).
* `api/scripts/verify_dp_06_05.py` — evidence generator: schedule
  reconciliation, backoff policy, kill-switch gate, watchdog
  alert/recovery, checkpoint backup round-trip, idempotent reparse, and the
  full signed soak. Exit 0 when every artifact passes.
* `api/tests/operations/test_ops_soak.py` — the verification suite.
