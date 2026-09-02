# SailRatings Scheduling Policy — Cadence and Staleness Budgets per Source

**Version:** sched-v1.0
**Status:** Pending approval (approval required: Stuart McLeod)
**Authority:** Stuart McLeod, SailRatings founder (`stuart@sailratings.com`)
**Notion reference:** OPS-01-01 (`3ce37ffe-f467-8198-ad1a-c14d914fd7e6`)
**Code of record:** `api/src/irc_data/sources/scheduling.py`
(`SCHEDULING_POLICY`, `SCHEDULING_POLICY_VERSION = "sched-v1.0"`)
**Register schema:** alembic migration `20260903a`
(`api/alembic/versions/0027_scheduling_policy_fields.py`)
**Builds on:** `docs/SOURCE-POLICY.md` v1.0 (DP-01-02; collection window §4.3,
kill switch §7, takedown §5) and the interim collection policy (DP-00-01 §3.3)
**Companion:** `irc_data.scrape_supervision` (OPS-01-02/04) is the operational
precedent for the cadence/budget numbers below.

---

## 1. Purpose

**Goal: make "how often, how late is too late" explicit per source.**

The collection policy (SOURCE-POLICY.md) answers *whether* and *how* we may
collect. This policy answers *when*: every source in the Data Source Register
carries, as first-class register fields, its

* **cadence class** and **cadence** (how often it is collected),
* **staleness budget** (how late is too late before the watchdog alerts),
* **nightly window** (when in the day collection is permitted),
* **retry / backoff** policy (what happens when a run fails),
* **cooldown** (how long a source stays quiet after an alert or failure), and
* **kill-switch semantics** (how collection stops, and how fast a takedown is
  acknowledged).

There is no implicit scheduling: an active source without these values fails
register validation (`irc_data.sources.registry.validate_scheduling`) and the
scheduler/watchdog treat it as misconfigured.

---

## 2. Cadence Classes and Design Defaults

Every source belongs to exactly one cadence class (`cadence_class` register
field, CHECK-constrained). The class carries the design defaults; a source may
override them explicitly.

| Class | Meaning | Default cadence | Staleness budget | Retry/backoff | Cooldown |
|---|---|---|---|---|---|
| `daily_results` | Daily results platforms (race results that update daily in season) | `nightly` | **48 h** (2 d) | 3 attempts, backoff 10 m → 30 m → 2 h | 4 h |
| `weekly_certificates` | Weekly certificate / rating lists | `weekly` | **8 d (192 h)** — the design example from OPS-01-01 | 3 attempts, backoff 1 h → 4 h → 24 h | 4 h |
| `annual_identifiers` | Annual identifier / event lists | `annual` | **370 d (8 880 h)** | 1 attempt, backoff 24 h | 24 h |
| `manual` | Manual-trigger or decommissioned sources | `manual` | **10 y (87 600 h)** — effectively never alerts | 1 attempt, backoff 24 h | 24 h |

Rationale:

* A **48 h** budget for daily results tolerates one missed night without
  paging anyone, matching the operational precedent (`run_within` 30 h for
  the existing daily crons).
* The **8 d** budget for weekly certificate lists is the OPS-01-01 design
  example: one full weekly cycle plus one day of slack.
* **Annual** sources (370 d) and **manual** sources cannot meaningfully be
  "stale" on a nightly timescale; their budgets exist so the watchdog knows
  not to cry wolf (mirrors `optional=True` in `scrape_supervision`).

## 3. Register Fields (per source)

The following columns on `data_sources` are the OPS-01-01 register fields
(migration `20260903a`). Every **active** source (`enabled` AND
`legal_status = 'approved'`) **must** carry non-NULL values; validation is
enforced in code by `registry.validate_scheduling()` and at the schema level
by CHECK constraints:

| Field | Type | Meaning |
|---|---|---|
| `cadence_class` | text | One of the four classes in §2. |
| `cadence` | text | Concrete cadence (`nightly`, `30min`, `weekly`, …) consumed by the Temporal schedule registry. |
| `staleness_budget_hours` | double | Max hours since the last successful run before the source is stale. |
| `nightly_window_start` / `nightly_window_end` | text `HH:MM` | Per-source nightly collection window (§5). |
| `retry_policy` | jsonb | `{"max_attempts": int, "backoff_seconds": […]}` (§4). |
| `cooldown_hours` | double | Alert / re-run cooldown (§7). |
| `kill_switch_ack_hours` | int | Takedown acknowledgement window (§6). |

### 3.1 Per-source values (active register, sched-v1.0)

| Source | Class | Cadence | Staleness budget | Notes |
|---|---|---|---|---|
| `sailsys` | daily_results | 30min | **2 h** | Published feed exception (collection policy §4.3) |
| `sailing-news` | daily_results | hourly | **6 h** | RSS syndication |
| `topyacht` | daily_results | nightly | **30 h** | Daily 02:30 UTC cron |
| `irc-tcc` | daily_results | daily | **30 h** | Daily 06:00 UTC cron |
| `orc` | daily_results | daily | **30 h** | Daily 03:00 UTC cron |
| `yachtscoring`, `manage2sail`, `sailwave`, `sailracehq`, `yotbot` | daily_results | nightly | **48 h** | class default |
| `isora` | daily_results | weekly ops | **192 h** | Weekly Tue 11:00 UTC cron (8 d) |
| `rhkyc` | daily_results | weekly ops | **192 h** | Weekly Wed 10:00 UTC cron (8 d) |
| `irc-certs` | weekly_certificates | weekly | **192 h (8 d)** | the design example |
| `cowesweek`, `sydney-hobart` | annual_identifiers | annual | **8 880 h (370 d)** | manual annual events |
| `rorc` | manual | manual | **87 600 h (10 y)** | decommissioned legacy source |

Hold / unknown sources carry the same fields (class defaults) so a source
moves to `approved` without a schema or config change — only the legal-status
ruling changes.

## 4. Retry / Backoff Semantics

* A failed scheduled run retries up to `retry_policy.max_attempts` times.
* The delay before attempt *n* is `retry_policy.backoff_seconds[n-1]`
  (the last entry repeats when attempts exceed the sequence).
* The Temporal schedule registry
  (`irc_data.temporal.schedules.registry.ScheduleRegistry`) derives the
  workflow `RetryPolicy` directly from the register's `retry_policy` field;
  rows that predate the field fall back to the global default
  (10 s → ×2 backoff, max 10 min, 3 attempts) until backfilled.
* When attempts are exhausted the run is marked failed in the run ledger
  (`source_runs`) and the source becomes eligible for a staleness breach at
  the next watchdog evaluation.

## 5. Nightly Collection Window

Inherited unchanged from the collection policy (SOURCE-POLICY.md §4.3 /
interim DP-00-01 §3.3) and made explicit per source:

* Default window: **01:00–06:00** source-local time where the timezone is
  known, else UTC. Register default `nightly_window_start='01:00'`,
  `nightly_window_end='06:00'`.
* No daytime scraping except single-URL on-demand health checks.
* Documented exceptions keep their own cadence (`sailsys` 30-min feed).
* The window columns are per-source so a future geographically-sensitive
  source can shift its window without touching the global rule.

## 6. Cooldown and Kill-Switch Semantics

### 6.1 Cooldown

* After a staleness alert (or an exhausted retry sequence), a source is not
  re-alerted for `cooldown_hours` (default **4 h** — `DEFAULT_COOLDOWN_HOURS`
  in code, `DEFAULT_COOLDOWN_HOURS` in `irc_data.scrape_watchdog`).
* Alert state lives in `watchdog_alerts`; recovery closes the open alert row
  (OPS-01-04 behaviour, unchanged).

### 6.2 Kill switch

Kill-switch triggers (any of them halts scheduling *and* in-flight collection
for the source on the next gate evaluation):

1. `data_sources.enabled = FALSE` (per-source switch),
2. a `domain_disables` row for the source's domain (domain switch, includes
   subdomains),
3. `COLLECTION_ENABLED=false` (global switch),
4. monitor/incident quarantine (`quarantine_until`),
5. an operator takedown request.

Semantics:

* **Acknowledgement window:** a takedown must be actioned within
  `kill_switch_ack_hours` (**4 h**, SOURCE-POLICY.md §5) — the register field
  makes the window explicit per source.
* **Re-enable:** only after written approval from Stuart McLeod; existing
  captures are quarantined to `data/raw/quarantine/<slug>/`.
* The schedule registry *pauses* (never deletes) the Temporal schedule of a
  disabled source, preserving run history.

## 7. Watchdog Integration

* The staleness watchdog evaluates **every active source every 15 minutes**
  (`WATCHDOG_INTERVAL_MINUTES = 15`; the `scrape-watchdog` cron interval).
* A source is **stale** when the age of its last successful run exceeds its
  `staleness_budget_hours`; "never succeeded" is always stale.
* `irc_data.sources.scheduling.SCHEDULING_POLICY.spec_for(record)` resolves
  the per-source `CadenceSpec` the watchdog and scheduler consume, so the
  register — not the watchdog config — is the source of truth for budgets.

## 8. Register Validation (acceptance criterion)

`irc_data.sources.registry.validate_scheduling(engine, raise_on_error=True)`
validates that every **active** source carries values for all §3 fields.
It raises `SchedulingPolicyError` listing every missing/malformed field.
The verification suite
(`api/tests/sources/test_scheduling_policy.py`) asserts:

* every active seed source passes validation (zero failures), and
* a record missing any required field fails with a field-specific error.

Schema-level CHECK constraints (migration `20260903a`) reject invalid
`cadence_class` values, non-positive budgets/cooldowns, and malformed
`HH:MM` windows at the database layer.

## 9. Changelog

| Version | Date | Author | Summary |
|---|---|---|---|
| sched-v1.0 | 2026-09-03 | Lane Worker (for Stuart McLeod) | Initial scheduling policy (OPS-01-01): four cadence classes; per-source staleness budgets (8 d design example for weekly certificate lists); watchdog interval 15 min; cooldown 4 h; nightly window inherited from collection policy; retry/backoff, cooldown and kill-switch semantics as register fields; register validation enforcement. **Pending Stuart's approval.** |
