# Validation, Quarantine and Promotion Gates (DP-05-02)

> **Goal:** prevent bad batches from entering the canonical views.
>
> **Builds on:** DP-02-03 (extraction contract), DP-03-04
> (transformation contract), DP-02-04 (replay/backfill), DP-05-01 (run
> ledger — every gated ingest/validation/promotion is a ledger-visible
> run) and DP-05-03 (reconciliation — a `decision = 'block'` report is
> a promotion-blocking signal).
>
> **Code:** `api/src/irc_data/quality/` (`contracts`, `validators`,
> `gate_store`, `gates`) · `api/src/irc_data/api/routers/quality_gates.py`
> · migration `20260904b` (`0028_quality_gates`).
>
> **Verification:** `api/tests/quality/` — fault fixtures trigger each
> rule class and verify isolation.

---

## 1. The pipeline and its gates

Every payload that would change what a consumer sees must pass through
exactly one gate, keyed by the stage it protects:

```
 raw artifact ──► EXTRACTION gate ──► CANONICAL gate ──► IDENTITY gate ──► consumer view
                 (ExtractionBatchV1)  (TransformationBatchV1)  (IdentityEffectBatch)
```

* **Extraction gate** — validates
  `ExtractionBatchV1`: envelope identity, per-field provenance
  (locators cite the artifact id + content hash), determinism
  (batch id / extraction hash recompute from content), completeness
  (non-empty, typed records), value domain (unique sequential record
  indices).
* **Canonical gate** — validates
  `TransformationBatchV1`: envelope identity, determinism
  (transformation id / hash / per-assertion ids recompute),
  completeness (the assertion / reject partition is disjoint and every
  assertion identifies its transformer), provenance (complete lineage
  chains), value domain (every assertion payload re-validates against
  its registered output schema).
* **Identity gate** — validates an `IdentityEffectBatch` (merges /
  splits / new-entity / retract effects): effects are well-formed, no
  self-merges, no duplicate effects, churn bounded by a configurable
  threshold.

Every rule is registered under a stable dotted id
(`<gate>.<rule_class>.<name>`) and belongs to one **rule class**:
`schema`, `provenance`, `determinism`, `completeness`, `value_domain`,
`identity_effect`.  The class taxonomy is what the quarantine review UI
groups failures by, and what the fault-fixture suite proves coverage
against.

## 2. Batch versioning — retry/replay creates a new version

The store (`quality_batches`) keys batches by
`(pipeline, source_slug, version)` with a uniqueness constraint.
`ingest_batch_version()` always computes `version = max(existing) + 1`,
so:

* Re-ingesting the same content after a quarantine **never reuses the
  quarantined version** — it lands in `v = prior + 1`.
* Prior versions (and their quarantine records, verdicts and receipts)
  are retained for audit and never mutated in place.

This is the same "no in-place rewrite" discipline as DP-02-04's replay
store; the difference is that quality batches are the *canonical*
promotion path, not an offline reparse tool.

## 3. Lifecycle

```
pending ──► validating ──► quarantined        (any error-severity rule fired)
                     └──► awaiting_promotion ──► promoted ──► superseded
                                                   (explicit)   (next promotion)
```

* **Quarantine** attaches everything a reviewer needs: the
  `GateFinding` list (rule id, rule class, message, bounded sample of
  offending records) plus a bounded sample of the staged batch rows.
  Quarantine records are deterministic (`quarantine_id =
  sha256(pipeline|source|version|gate)[:16]`), so re-validating a
  quarantined batch is idempotent.
* **Promotion is explicit.**  `promote_batch()` is the *only* function
  that changes consumer-visible state.  It requires the
  `awaiting_promotion` status — a quarantined or pending batch raises
  `PromotionError`, and the admin endpoint surfaces that as `409`.
* **Partial publication cannot occur.**  Promotion happens in a single
  transaction: the new version is marked `promoted` and the previously
  promoted version is marked `superseded` atomically.  There is no
  interleaved state where consumers could see a half-published batch,
  and no in-place rewrite of the published state.

## 4. The consumer view — promoted versions only

Consumers never read `quality_batch_rows` directly.  The read model is:

```
get_consumer_view(pipeline, source_slug)
  → rows of the unique batch with status = 'promoted'
  → [] if nothing has ever been promoted
```

Pending, validating, quarantined, awaiting-promotion and superseded
versions are all invisible.  This is the enforcement point for the
acceptance criterion *"consumers see only promoted versions"* — the
suite proves a clean-but-unpromoted batch leaks nothing, and that a
quarantined retry never displaces the promoted version.

## 5. Admin API

All endpoints sit behind the admin credential
(`_verify_admin`) under `/v1/admin/quality/`:

| Endpoint | Purpose |
|---|---|
| `GET /batches` | Batch versions, filterable by pipeline / source / status |
| `GET /batches/{batch_key}` | Detail: staged rows, verdicts, quarantine, receipt |
| `GET /quarantine` | The open quarantine queue |
| `GET /quarantine/{quarantine_id}` | Failures + samples for one record |
| `POST /batches/{batch_key}/promote` | Explicit promotion (409 unless `awaiting_promotion`) |
| `GET /consumer-view` | What a consumer of `(pipeline, source)` sees today |

## 6. Handoff / output contracts

* `GateFinding` — one rule failure with a bounded sample.
* `QuarantineRecordV1` — the quarantine handoff (failures + sample
  rows + deterministic id).
* `GateVerdictV1` — the full validation report for a batch (every rule
  evaluated, pass/fail, counts).
* `PromotionReceiptV1` — proof of an explicit promotion, including the
  superseded version (retained, never deleted).

All four are JSON round-trippable so they can cross Temporal activity
boundaries or be dumped to fixtures.

## 7. Relationship to DP-05-03 (reconciliation)

The gates are *content* checks (is this batch well-formed, provenanced,
deterministic, in-domain?).  DP-05-03 adds *count* checks (did this
source produce an implausibly small yield vs its trailing baseline?).
A reconciliation `decision = 'block'` report is a promotion-blocking
signal: the publish path should refuse to promote a batch whose source
run was blocked, and the admin UI surfaces both side by side.  The two
mechanisms share the `quality_batches` promotion seam — reconciliation
blocks *before* promotion, gates quarantine *before* promotion, and
only `promote_batch()` crosses the line.

## 8. Verification

`api/tests/quality/`:

* `fixtures.py` — one clean fixture per gate plus a fault fixture for
  every rule class (`tests/quality/fixtures.py` documents which fault
  triggers which class).
* `test_quality_gates.py` — every fault fixture is driven through
  `ingest_validate_and_optionally_promote`; asserts the expected rule
  class fired, the batch was quarantined with samples + failures, a
  quarantined/pending batch cannot be promoted (`PromotionError`), a
  retry lands in a new version, and the consumer view only ever shows
  promoted rows.
* `test_quality_gates_api.py` — the same guarantees over the FastAPI
  surface (401 without admin auth, 409 promoting a quarantined batch,
  consumer view empty until explicit promotion).
