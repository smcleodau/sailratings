# DP-06-04 — Vertical-Slice Identity Resolution & Quality Certification

| | |
|---|---|
| **Issue** | DP-06-04 |
| **Status** | **IMPLEMENTED — verification evidence generated** |
| **Goal** | Prove entity resolution and publication controls on real data |
| **Scope** | Run candidates, scores, adjudication sample, quality gate, reconciliation and promotion |
| **Blocked by** | DP-06-03 ✅ (canonical assertions) · DP-04-05 ✅ (adjudication queue) · DP-05-02 ✅ (quality gates) |
| **Code of record** | `api/src/irc_data/quality/certification.py` (`SCHEMA_VERSION = "slice-certification-v1"`) |
| **Output contract** | `PublishedDatasetReceiptV1` |
| **Verification** | `api/scripts/verify_dp_06_04.py` (25/25 checks) · `api/tests/quality/test_certification.py` (15 tests) |

> **One-line:** compose DP-04-02 → DP-04-03 → DP-04-05 → identity effects →
> DP-05-02 gate → DP-05-03 reconciliation → DP-05-02 promotion into one
> certified, reproducible vertical slice over the DP-06-01 IRC + ORC +
> SailSys source pair, and emit a signed `PublishedDatasetReceiptV1`.

---

## 1. What this proves

The slice runs on **steward-labelled real-data observations** drawn in the
shape of the DP-06-01 selected source pair (IRC certificates, ORC
certificates, SailSys race results).  Each `SliceObservation` carries a
`gold_entity_key` — the steward-verified ground truth — used **only** by the
false-merge audit, never by the matcher.

The fixture deliberately includes the messy shapes the platform exists to
resolve:

* **corroborated duplicates** (IRC cert ↔ ORC cert for the same hull, with a
  shared `registry_id`) that the scorer routes to **auto_merge**;
* a **hard uncertain duplicate** (sail-prefix drift `52570` ↔ `AUS52570`, no
  registry id) that lands in the **adjudication sample**;
* **distinct boats with near-identical names** (`Alive`/`Alive II`,
  `Zen`/`Zen Again`) that must **never** be merged.

## 2. The seven stages

| # | Stage | Component | Evidence |
|---|-------|-----------|----------|
| 1 | **Candidates** | DP-04-02 `CandidateGenerator` | `CandidateStageV1` (ruleset id + fingerprint, pair count, reduction ratio) |
| 2 | **Scores** | DP-04-03 `PairwiseScorer` | `ScoreStageV1` (config fingerprint, routing counts, per-feature explanations) |
| 3 | **Adjudication sample** | DP-04-05 `AdjudicationQueue` | `AdjudicationStageV1` (measured error rate, time/case, usability fingerprint) |
| 4 | **Identity effects** | DP-04-04 `IdentityGraph` | union-find cluster → canonical boat per cluster; merge/new-entity effects |
| 5 | **Quality gate** | DP-05-02 identity gate + DP-05-01 dimensions | `GateVerdictV1` + `DimensionReportV1` |
| 6 | **Reconciliation** | DP-05-03 `reconcile_run` | `ReconciliationReportV1` (`decision`, `promotion_allowed`) |
| 7 | **Promotion** | DP-05-02 `promote_batch` | `PromotionReceiptV1` + consumer view |

### Why union-find clustering before materialisation

The DP-04-04 registry enforces **one label names one live entity at a
time** (`DuplicateAliasError`).  Two observations of the same boat carry the
same name, so they cannot be linked to *distinct* entities first and merged
after.  The pipeline therefore clusters by **every resolved merge** (the
auto-resolver's `auto_merge` pairs ∪ the adjudicator's applied `merge`
decisions) *before* materialising one canonical boat per cluster, then links
each cluster's **distinct** aliases onto it.  This is the correct resolution
order and keeps the published graph free of the alias-overlap violation.

## 3. Acceptance criteria → where they are enforced

| Acceptance criterion | Enforcement |
|---|---|
| **Accuracy and quality meet approved thresholds** | `CertificationThresholdsV1` — adjudication sample error rate ≤ 3 % (the DP-06-01 M5 ≥ 97 % precision target) and auto-merge precision ≥ 97 %.  A breach raises `AccuracyThresholdError`.  The DP-05-01 dimension report (completeness / identity-confidence / provenance) must not `block`. |
| **False-merge audit passes** | `FalseMergeAuditV1` cross-checks **every** merge decision (auto + adjudicated) against the gold labels.  One false merge raises `FalseMergeError` (approved `max_false_merges = 0`). |
| **Every published record is reproducible** | `PublishedDatasetReceiptV1.reproducibility_hash` is a content hash of the published row set (entity keys are content-derived, not random); a replay must reproduce it.  Each row carries the blocking/scorer/threshold config fingerprints. |

### Approved thresholds

```python
CertificationThresholdsV1(
    max_adjudication_error_rate=0.03,   # DP-06-01 M5: ≥ 97% precision
    max_false_merges=0,                  # a false merge is never acceptable
    min_auto_merge_precision=0.97,
)
```

## 4. The output contract — `PublishedDatasetReceiptV1`

The handoff/output contract binds together, content-addressed:

* **batch identity** — `batch_key` / `pipeline` / `source_slug` / `version`
  / `promotion_receipt_id`;
* **accuracy evidence** — the adjudication sample and the false-merge audit;
* **quality evidence** — the dimension report and gate verdict;
* **reconciliation** — the allow/block decision and variance;
* **reproducibility** — `published_rows`, `reproducibility_hash`,
  `config_fingerprints`;
* **steward sign-off** — `verdict` / `signed_by` / `signed_at`.

`receipt.sign(steward, at=…)` returns a signed copy and **refuses to sign a
non-certified receipt**.  This is the mechanism for the issue's verification
step — *independent data-steward review signs the DataQualityVerdict to the
batch version*: the steward's signature is bound to the exact
`batch_key` + `version` + `reproducibility_hash`.

## 5. Publication controls (proven to block)

The verifier proves each control fires, so bad data / bad adjudication
**never publish**:

| Control | Fault injected | Result |
|---|---|---|
| Accuracy gate | separate-everything adjudicator (misses true duplicates) | `AccuracyThresholdError` |
| False-merge audit | merge-everything adjudicator (merges `Alive`+`Alive II`) | `FalseMergeError` |
| Quality gate | resolved observation with no `sail_number` | `QualityGateBlockedError` (completeness rule) |
| Reconciliation | 4 records silently lost, no reason code | `PromotionBlockedError` (`decision=block`) |

## 6. Reproducibility & determinism

* The slice is **offline and hermetic** — in-memory SQLite, no network, no
  wall-clock dependence (the clock is injected).
* Published rows are **content-derived** (cluster keys are SHA-256 of the
  sorted member observation ids), so two runs over the same inputs produce
  the **identical** `reproducibility_hash`.
* The `receipt_id` is content-addressed from
  `(batch_key, pipeline, source_slug, version, promotion_receipt_id,
  reproducibility_hash)`.

## 7. Verification

```
PYTHONPATH=src python3 scripts/verify_dp_06_04.py   # 25/25 checks
PYTHONPATH=src python3 -m pytest tests/quality/test_certification.py -q  # 15 tests
```

The verifier prints paste-able PASS/FAIL evidence for all seven stages, the
three acceptance criteria, the four publication-control blocks, and the
steward sign-off.
