# DP-05-03 — Reconciliation & Silent-Loss Detection

Detects pipelines that **succeed technically while losing records**.  Every
pipeline run reconciles its stage counts against a conservation invariant and
a per-source yield baseline; any *unexplained* shortfall or *abrupt yield
change* blocks promotion and alerts **within the same cycle**.

## Stage model

```
discovered → fetched → parsed → transformed → published
                                  │
                                  ├─→ rejected            (reason-coded)
                                  ├─→ quarantined         (reason-coded)
                                  └─→ duplicate_suppressed (reason-coded)
```

## Conservation invariant

```
fetched == parsed + rejected + quarantined + duplicate_suppressed
parsed  == published + rejected + quarantined + duplicate_suppressed
```

**Unexplained variance** is the sum of:

* **stage loss** — `max(0, fetched − (parsed + rejected + quarantined +
  duplicate_suppressed))` — records that entered but reached no accounted
  terminal state (e.g. a fetched page the parser silently dropped).
* **publish loss** — `max(0, parsed − (published + rejected + quarantined +
  duplicate_suppressed))` — records the parser emitted that never landed in
  the published table.

Any shortfall not attributed to a reason code is unexplained variance — the
signature of silent loss.

## Reason codes

| code | meaning |
|---|---|
| `duplicate_suppressed` | dropped because the record already exists |
| `schema_violation` | rejected: failed schema validation |
| `parse_error` | rejected: parser raised on the record |
| `policy_blocked` | rejected: robots/policy disallow |
| `out_of_scope` | rejected: outside the requested window |
| `quarantined_source` | quarantined: source incident open |
| `zero_yield` | parser produced 0 records from a non-empty page |

Records dropped under a known reason code are *explained*; anything else is
unexplained variance.  Unknown reason codes are surfaced in
`unexplained_reasons` for audit (numerically explained, but flagged).

## Yield baseline

Each run's yield (`published / discovered`) is appended to a trailing
per-source series (`pipeline_count_baseline`).  The reconciler reads the
**[p10, p50]** band of the trailing window (default 14 runs).  A run whose
yield falls below `0.5 × p10` is an **abrupt yield change** and blocks —
even when every dropped record is individually reason-coded.  The band is
enforced only once ≥ 3 samples exist, so a brand-new source is not blocked
on its first low-yield run.

## Contracts (handoff / output)

* **`PipelineCountsV1`** — the *input contract*: stage counts for one run
  plus a `reason_counts` ledger.  Produced by the pipeline stage and handed
  to `reconcile_run()`.
* **`ReconciliationReportV1`** — the *output contract*: variance, yield,
  baseline band, `decision` (`allow` / `block`), `promotion_allowed`, and
  `block_reason`.  Persisted to `reconciliation_reports` and returned to the
  caller.

## Behaviour

`reconcile_run(engine, counts)`:

1. computes unexplained variance (conservation invariant),
2. computes the run's yield and compares it against the trailing p10 floor,
3. decides `allow` vs `block`,
4. persists a `ReconciliationReportV1` row,
5. on `block`: opens/attaches a `silent_loss` `source_incident`, quarantines
   the source's publication (`publication_quarantine`), and fires the
   health-check webhook — all in the same cycle.

`assert_promotable(report)` raises `PromotionBlockedError` when
`promotion_allowed` is `False`.  The publish/promotion path calls this gate
before promoting a batch, so unexplained variance or an abrupt yield change
blocks promotion.

## API (admin)

```
POST /v1/admin/reconciliation/check              reconcile one run's counts
GET  /v1/admin/reconciliation/reports            recent reports (filterable)
GET  /v1/admin/reconciliation/reports/{run_id}   report for one run
GET  /v1/admin/reconciliation/baseline/{source}  trailing yield band
```

## CLI

```
irc-data reconcile check --source sailsys --run-id 42 \
    --discovered 10 --fetched 10 --parsed 10 --transformed 10 --published 10
irc-data reconcile reports --source sailsys --decision block
irc-data reconcile baseline sailsys
```

`check` exits 0 when promotion is allowed, 2 when blocked.

## Schema

Alembic migration `0028_reconciliation` (revision `20260904a`):

* `pipeline_count_baseline` — trailing yield series per source.
* `reconciliation_reports` — one row per reconcile verdict; `decision =
  'block'` rows are the promotion-blocking signal.

## Verification

`api/tests/test_reconciliation.py` uses *mutated fixtures* to simulate the
three silent-loss vectors and asserts each blocks + alerts in one cycle:

* **dropped pages** — `fetched > parsed`, no reason code → unexplained
  variance blocks.
* **parser zero-yield** — `fetched > 0, parsed == 0` → blocks.
* **duplicate suppression** — legitimate reason-coded dedup is *allowed*;
  a suppression count that doesn't add up *blocks*.
* **abrupt yield change** — a fully reason-coded but collapsed yield blocks
  on the p10 floor.
