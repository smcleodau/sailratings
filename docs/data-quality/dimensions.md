# DP-05-01 — Data Quality Dimensions, Thresholds and Ownership

> **Goal: make database health measurable.**
>
> **Code of record:** `api/src/irc_data/quality/dimensions.py`
> (`SCHEMA_VERSION = "dq-dimensions-v1"`).  The registry refuses to
> import if any published dataset lacks a blocking rule, a warning rule,
> an accountable owner, an SLO, or a remediation playbook.
> **Builds on:** DP-02-01 (raw artifacts / provenance envelopes),
> DP-03-04 (canonical transformation contract), DP-04-04 (identity
> resolution confidence), DP-05-02 (validate/quarantine/promote gates),
> DP-05-03 (reconciliation / silent-loss).
> **Verification:** `api/tests/quality/test_dimensions.py` (registry
> shape + engine semantics) and `api/scripts/verify_dp_05_01.py`, which
> reviews the vertical-slice rules against **real historical
> distributions** (§7).

---

## 1. The eight dimensions

Every published dataset is measured on eight dimensions.  Each rule
produces a *badness score* (higher = worse) and carries two thresholds:
**warning** (recorded, counted against the SLO error budget, owner
notified) and **blocking** (publication/promotion is refused — enforced
via `assert_dataset_publishable()` at the DP-05-02 promotion seam, the
same point DP-05-03's `assert_promotable()` is called).

| Dimension | Question it answers | Metric families |
|---|---|---|
| **completeness** | Is expected data present? | null fraction of identity fields |
| **validity** | Are values in-domain? | range, enum vocabulary, regex |
| **uniqueness** | Any unintended duplicates? | duplicate fraction on identity keys |
| **consistency** | Do fields agree with each other? | named cross-field predicates |
| **timeliness** | Is the dataset fresh vs its cadence? | freshness lag (days) vs staleness budget |
| **provenance** | Does every row cite its raw artifact? | gap fraction on `(artifact_id, content_hash)` |
| **identity_confidence** | Do records resolve to known entities? | unmatched / low-confidence fraction |
| **drift** | Has the distribution moved vs history? | mean z-score vs baseline; row-count drift |

Notes on deliberate asymmetries:

* **Provenance has no warning band.**  A row without an artifact
  citation must never publish (DP-02-01); any gap blocks.
* **Uniqueness is calibrated to the source.**  `cert_number` duplicates
  are never legitimate (0 observed in 11 TCC snapshots) so any duplicate
  warns and 0.5% blocks; bare `sail_number` duplicates are *expected*
  (numbers are re-issued across boats and eras — 2.5–9.0% observed), so
  that rule only warns at 12% and blocks at 25%.
* **Un-measurable ≠ pass.**  A rule that cannot be measured in the
  current context (no baseline, no run ledger, no identity resolver)
  reports `skip`, never `pass` — silence is not health.

## 2. Ownership matrix

| Owner | Role | Accountable for | Escalation |
|---|---|---|---|
| `data-platform@sailratings.com` | Data Platform Lead | canonical validity, uniqueness, consistency, drift rules; the dimension framework itself | `stuart@sailratings.com` (platform authority, SOURCE-POLICY §0) |
| `ingestion-ops@sailratings.com` | Ingestion / Scrapers On-Call | completeness, timeliness, source-vocabulary drift | `data-platform@sailratings.com` |
| `identity-resolution@sailratings.com` | Identity Resolution Maintainer | identity-confidence rules, duplicate-identity review | `data-platform@sailratings.com` |

Every rule in the registry carries one of these owners plus an SLO
(`target` fraction of passing batches over a rolling `window_days`;
error budget = 1 − target) and a playbook (§6).

## 3. Published datasets and their SLOs

| Dataset | Contents | Cadence (SCHEDULING-POLICY) | Freshness SLO (warn / block) | Owner |
|---|---|---|---|---|
| `tcc_listing` | IRC TCC listing snapshots → `boats`, `tcc_snapshots` | daily | 2 d / 5 d | ingestion-ops |
| `race_results` | Event results from results platforms | daily (incremental) | 3 d / 10 d | ingestion-ops |
| `irc_certificates` | Parsed IRC certificate PDFs | weekly class | 14 d / 45 d | ingestion-ops |
| `orc_register` | ORC country-register XML snapshots | daily | 2 d / 7 d | ingestion-ops |

Rule-level SLOs live on each `ThresholdRule` (99–100% pass targets over
a 30-day rolling window; provenance rules are SLO 100%).

## 4. Rule tables (vertical slice)

Full machine-readable form: `DQ_DATASET_RULES` in
`api/src/irc_data/quality/dimensions.py` (`ThresholdRule.to_dict()`).
39 rules across 4 datasets; every dimension covered per dataset.

### 4.1 `tcc_listing` (13 rules)

| Rule | Dim | Sev | Metric | Warn | Block | Historical basis |
|---|---|---|---|---|---|---|
| `completeness.sail_number_present` | comp | block | null frac | 0.1% | 1% | ≤1 blank row in 11 snapshots |
| `completeness.boat_name_present` | comp | warn | null frac | 0.1% | 1% | 0 blanks observed |
| `validity.tcc_plausible_range` | valid | block | out-of-range frac, [0.6, 2.2] | 0.1% | 1% | observed [0.709, 2.040], 2009→2026 |
| `validity.cert_year_in_range` | valid | warn | out-of-range frac, [1990, 2100] | 0.1% | 2% | schema bound |
| `uniqueness.cert_number_unique` | uniq | block | dup frac | 0.01% | 0.5% | 0 dups in every snapshot |
| `uniqueness.sail_number_dup_bounded` | uniq | warn | dup frac | 12% | 25% | observed 2.5–9.0% (re-issued numbers) |
| `consistency.non_spi_le_tcc` | cons | block | predicate violation frac | 0.1% | 1% | 0 violations in 2026 snapshots |
| `consistency.cert_year_matches_issue_date` | cons | warn | predicate violation frac | 0.5% | 5% | 0 mismatches (±1 y slack) |
| `timeliness.daily_snapshot_freshness` | time | block | lag days | 2 d | 5 d | daily cadence |
| `provenance.artifact_citation_complete` | prov | block | gap frac | — | any | absolute (DP-02-01) |
| `identity_confidence.boat_match_coverage` | ident | warn | unmatched frac (<0.8 conf) | 15% | 40% | ~1.1%/day genuinely-new sails |
| `drift.tcc_mean_drift` | drift | block | \|z\| of batch mean | 3σ | 6σ | snapshot means ∈ [1.019, 1.053], σ=0.0031 |
| `drift.row_count_drift` | drift | warn | count drift frac | 25% | 60% | steady 1 865→3 114 growth |

### 4.2 `race_results` (9 rules)

| Rule | Dim | Sev | Metric | Warn | Block | Basis |
|---|---|---|---|---|---|---|
| `completeness.place_or_status_present` | comp | warn | null frac `place` | 10% | 30% | DNF/DNS legitimately place-less |
| `validity.status_vocabulary` | valid | block | enum violation frac | 0.1% | 2% | registered status vocabulary |
| `validity.irc_rating_range` | valid | block | out-of-range frac [0.6, 2.2], `rating_type=irc` rows | 0.1% | 1% | same basis as TCC range |
| `uniqueness.entry_race_unique` | uniq | block | dup frac on `(event_entry_id, race_name)` | 0.01% | 0.5% | mirrors DB constraint |
| `consistency.place_within_fleet` | cons | warn | `place ≤ fleet_size` violation frac | 0.5% | 5% | scoring-table parse errors |
| `timeliness.results_ingest_freshness` | time | block | lag days | 3 d | 10 d | weekend-clustered regattas |
| `provenance.artifact_citation_complete` | prov | block | gap frac | — | any | absolute |
| `identity_confidence.entry_boat_match` | ident | warn | unmatched frac | 20% | 50% | unrated boats are normal |
| `drift.irc_rating_mean_drift` | drift | warn | \|z\| of batch mean | 3σ | 6σ | catches unit/column mix-ups |

### 4.3 `irc_certificates` (9 rules)

| Rule | Dim | Sev | Metric | Warn | Block | Basis |
|---|---|---|---|---|---|---|
| `completeness.cert_number_present` | comp | block | null frac | 0.1% | 1% | publishable identity |
| `validity.lh_plausible_range` | valid | block | out-of-range frac [4, 40] m | 0.1% | 1% | parse-defect band |
| `validity.issue_date_iso` | valid | warn | regex violation frac | 0.5% | 5% | ISO-8601 canonical form |
| `uniqueness.cert_number_unique` | uniq | block | dup frac | 0.01% | 0.5% | DB constraint |
| `consistency.measures_present_with_cert` | cons | warn | null frac `lh` | 20% | 50% | table-extractor failure signal |
| `timeliness.certificate_ingest_freshness` | time | warn | lag days | 14 d | 45 d | opportunistic ingestion |
| `provenance.artifact_citation_complete` | prov | block | gap frac | — | any | legal-adjacent documents |
| `identity_confidence.cert_boat_match` | ident | warn | unmatched frac | 25% | 60% | new boats legitimately absent |
| `drift.lh_mean_drift` | drift | warn | \|z\| of batch mean | 3σ | 6σ | unit-defect detection |

### 4.4 `orc_register` (8 rules)

| Rule | Dim | Sev | Metric | Warn | Block | Basis |
|---|---|---|---|---|---|---|
| `completeness.sail_number_present` | comp | block | null frac | 0.5% | 2% | observed ≈0.1% blanks |
| `validity.cert_name_vocabulary` | valid | warn | enum violation frac | 0.1% | 2% | exactly 10 values observed, stable 2026-03→09 |
| `uniqueness.ref_no_unique` | uniq | block | dup frac | 0.01% | 0.5% | 0 dups in 134 snapshots |
| `consistency.expiry_after_issue` | cons | warn | ISO-datetime regex violation frac | 0.5% | 5% | expiry drives validity windows |
| `timeliness.daily_snapshot_freshness` | time | block | lag days | 2 d | 7 d | 134 consecutive daily dirs |
| `provenance.artifact_citation_complete` | prov | block | gap frac | — | any | absolute |
| `identity_confidence.orc_boat_match` | ident | warn | unmatched frac | 30% | 70% | ORC covers boats IRC never rates |
| `drift.row_count_drift` | drift | warn | count drift frac | 30% | 70% | seasonal 2× growth is organic |

## 5. How evaluation works

```
records (dict rows) + context ──► evaluate_dataset(dataset, …)
                                    │  per rule: measure → warn_at? → block_at?
                                    ▼
                    DimensionReportV1(status = pass|warn|block,
                                      results[], publishable?)
                                    │
                 assert_dataset_publishable(report)  ← called at the
                 DP-05-02 promote seam, alongside DP-05-03's
                 assert_promotable()
```

* `context` carries `as_of` / `last_batch_at` (timeliness) and any
  pipeline-attached fields (provenance envelopes, identity confidence).
  Raw snapshots evaluated offline therefore `skip` timeliness /
  provenance / identity rules rather than faking a pass.
* Drift baselines are **data**: `FieldBaseline` built from the
  historical window (mean/std/min/max/n + a `source` string naming the
  snapshots), attached to the rule's params, re-built from raw snapshots
  by the verification script to prove they still match reality.
* `skip` rules never count against SLOs; `warn` consumes error budget;
  `block` refuses promotion via `BlockingRuleViolation`.

## 6. Remediation playbooks

Every rule references one of these (id + steps are carried on the rule
and rendered into alert payloads):

* **PB-INGESTION-SOURCE-CHECK** — field content collapsed or malformed
  at the source.  1. Open the raw artifact for the failing batch.
  2. Diff vs the previous good artifact: upstream feed or parser
  mapping?  3. Upstream at fault → pause the source (scheduling kill
  switch), file a source incident, notify the contact per
  SOURCE-POLICY.  4. Parser at fault → land fix, replay as a NEW version
  (DP-02-04), verify, promote.  5. Record root cause, close quarantine.
* **PB-SCHEMA-DRIFT** — upstream vocabulary/schema drifted.  1. Inspect
  the sample rows.  2. Check upstream release notes.  3. Legitimate →
  extend the registered enum/schema (new schema_version), re-run,
  promote.  4. Defect → pause source, file incident, apply a documented
  transformer mapping.  5. Backfill via replay; never edit published
  rows in place.
* **PB-IDENTITY-REVIEW** — identity resolution degraded.  1. Pull the
  unmatched / duplicate sample.  2. Check for re-issued labels or
  renames.  3. Hand-resolve top clusters in the matching workbench;
  record merges/splits through the identity gate (DP-05-02).
  4. Matcher regression → roll back config, re-score.  5. Re-run,
  confirm unmatched fraction within SLO, promote.
* **PB-PROVENANCE-REPAIR** — rows without artifact citations.
  1. Identify the stage that dropped the envelope.  2. Fix the
  extractor/transformer (DP-02-01).  3. Replay as a new version; verify
  100% coverage.  4. Rows already visible without provenance →
  correctness incident: supersede the published version immediately.
* **PB-FRESHNESS-RECOVERY** — dataset stale vs cadence.  1. Check the
  run ledger (last status/error/next run).  2. Schedule stalled → kick
  the workflow and watch it.  3. Upstream down → source incident;
  extend the budget only with owner sign-off.  4. On recovery verify
  all dimension rules, then promote.  5. Chronic staleness → revise the
  cadence class in SCHEDULING-POLICY (governance change).
* **PB-DRIFT-INVESTIGATION** — statistical drift vs baseline.
  1. Compare batch histogram vs baseline.  2. Cross-check an
  independent source to decide world-change vs pipeline-change.
  3. World-change → re-baseline from the recent window, document, then
  promote.  4. Pipeline-change → quarantine, fix, replay, promote.
  5. Row-count drift → also reconcile stage counts (DP-05-03).

## 7. Verification against real historical distributions

`api/scripts/verify_dp_05_01.py` (exit 0 = rules agree with reality):

**Method.**  Baselines are built from an *earlier* historical window;
rules are then evaluated against **held-out later snapshots** — the
production situation (thresholds tuned on history, applied to new data).
Fault injection (blanked identity fields, shifted means, duplicated
keys, collapsed row counts) must fire the expected blocking rule.

**Data reviewed (raw lake `data-raw/`):**

| Dataset | Window | Snapshots | Rows |
|---|---|---|---|
| `tcc_listing` | 2009-05-18 → 2026-05-22 | 11 CSV | 1 865–3 906 per snapshot |
| `orc_register` | 2026-03-14 → 2026-09-02 | 134 daily dirs | 6 754 → 13 074 (seasonal growth) |

**Measured facts the thresholds are grounded in** (full table in the
module docstring):

* TCC ∈ [0.709, 2.040]; per-snapshot means ∈ [1.0192, 1.0528],
  σ(means) = 0.0031 → drift rule blocks at |z| ≥ 6, i.e. a mean shift
  of ~0.02 — far below any conceivable organic move, far above noise.
* `cert_number` duplicates = 0 in all 11 snapshots → warn at any dup.
* Bare `sail_number` duplicates = 2.5–9.0% (re-issued numbers) → the
  uniqueness rule is deliberately loose (12%/25%).
* `non_spi_tcc > tcc` = 0 violations; cert-year vs issue-date = 0
  mismatches → consistency rules block at 1%.
* Day-over-day genuinely-new sail numbers ≈ 1.1% → identity warning at
  15% unmatched.
* ORC: blank SailNo ≈ 0.1%, RefNo duplicates = 0, CertName vocabulary
  exactly the registered 10 values across the whole window.

**Latest run:** all checks passed — held-out snapshots (tcc 2026-05-21,
2026-05-22; orc 2026-09-02) pass every measurable rule, offline-only
metrics `skip` correctly, and all five fault injections block.  (The
ORC row-count rule *warns* on the held-out September snapshot against a
March→June window: seasonal growth is real; the rule says
"investigate", which is the intended behaviour for a warning.)
