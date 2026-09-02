# Load, Resilience and Disaster-Recovery Testing (DP-05-05)

> **Goal:** know the safe operating envelope before broad collection.
>
> **Builds on:** DP-02-02 (raw lake — the durable, hash-verified system
> of record), DP-02-04 (replay / backfill), DP-05-01 (run ledger) and
> DP-05-02 (validation / quarantine / promotion gates).
>
> **Code:** `api/src/irc_data/resilience/` (`contracts`, `drill`, `cli`).
>
> **Verification:** `api/tests/resilience/test_dr_drill.py` — a
> production-shaped synthetic load plus a restore drill produces a
> **signed report** (`DrillReportV1`).

---

## 1. What this is

The drill harness drives a synthetic load through the *real* data-plane
seams — raw-lake capture, run-ledger accounting, gated validation and
explicit promotion, and replay/backfill — while injecting the fault and
disaster scenarios the issue names.  It answers one question: **how much
load can the data plane take, and what happens when the database, the
object store, or the whole operational store fails?**

The output is a single artefact: a signed `DrillReportV1` recording the
measured throughput, RPO and RTO, and a per-criterion pass/fail.  The
signature (HMAC-SHA256 over the canonical payload) makes the report
tamper-evident — any edit to a measured value invalidates it.

The drill is self-contained: it stands up its own SQLite engine, raw
lake and published store in a working directory, so it runs in CI or a
scratch environment without touching production state.  Every store
layer uses portable SQL, so the measured behaviour carries over to
Postgres in production.

## 2. The scenarios

| # | Scenario | What it proves | Measured |
|---|---|---|---|
| 1 | `high_volume_ingest` | High artifact volume flows through capture → ledger → gate → promotion with no loss and no duplicates | throughput/s |
| 2 | `backfill_under_load` | A DP-02-04 replay over the published corpus is idempotent (`plan_id` → same batch) and correct, while live ingest continues | throughput/s |
| 3 | `concurrent_adapters` | N adapter loops in parallel: the ledger records every run exactly once, every row is promoted, nothing lost or double-counted | throughput/s |
| 4 | `database_outage` | On DB loss, writes fail **safely** (raise, no partial rows); on restore, pre-outage state is intact and writes resume | RTO, RPO=0 |
| 5 | `object_store_outage` | On raw-lake loss, `store()` raises leaving no partial object/index; on restore every pre-outage object verifies | RTO, RPO=0 |
| 6 | `restore_and_replay` | Destroy the operational DB, rebuild it by replaying from the raw lake; published data **and** per-field provenance survive; a second replay causes **no duplicate publication** | RTO, RPO=0, replay throughput |

A scenario never raises on an expected fault — it records named boolean
checks and passes iff every check held.

## 3. The consumer-view model

The DP-05-02 store keys batches by `(pipeline, source_slug, version)`;
the consumer view shows the promoted batch for a `(pipeline, source)`
pair and promotion supersedes the prior version.  To accumulate one
consumer-visible row per artifact, the drill gives **each artifact its
own pipeline key** `drill.extraction.<source>.<i>`, and defines a
source's published state as the union of consumer views across its keys.

Provenance — the raw artifact id + content hash a row cites — is read
from the field locators inside each staged record, exactly as the
extraction gate enforces.  The restore drill asserts that provenance set
is identical before and after recovery.

## 4. RPO / RTO definitions used

* **RPO (recovery point objective)** — the data-loss window.  The raw
  lake is the system of record: every artifact is durably, hash-verified
  and atomic *before* it is gated/promoted.  Destroying the operational
  database loses nothing committed, so the measured RPO is `0`.
* **RTO (recovery time objective)** — wall-clock time from fault
  injection until the pre-fault consumer-visible state is restored.
  For the restore drill this is the time to replay the corpus from raw.
* **Throughput** — artifacts/second, per load-bearing scenario and
  aggregated across the drill.

## 5. The signed report

`DrillReportV1` carries: `report_id`, wall-clock window, one
`ScenarioResultV1` per scenario, `overall_status`, total
`artifact_volume`, `aggregate_throughput_per_second`, the headline
`measured_rpo_seconds` / `measured_rto_seconds`, and
`passed_acceptance_criteria`.

`sign_report(report, key)` sets `signature` to the HMAC-SHA256 of the
canonical payload (every field except `signature`) and records
`signing_key_id`.  `verify_report_signature(report, key)` recomputes and
compares in constant time.  The signature survives a JSON round-trip, so
the report can be shipped to the issue board or stored as an artefact.

## 6. Acceptance criteria → evidence mapping

| Acceptance criterion | Where it's proven |
|---|---|
| Published data and provenance survive recovery | scenario 6 checks `published_data_survives`, `provenance_survives` |
| RPO/RTO and throughput are measured | scenario 6 `rpo_seconds` / `rto_seconds`; scenarios 1–3 & 6 `throughput_per_second`; report `aggregate_throughput_per_second` |
| No duplicate publication follows replay | scenario 6 check `no_duplicate_publication` (second replay leaves consumer rows and promotion ledger unchanged) |

## 7. Running it

```
# Full drill, signed report to stdout (JSON)
irc-data dr-drill

# Tune the load and write the report to a file
irc-data dr-drill --volume 5000 --concurrency 8 --per-adapter 200 \
    --out report.json
```

The signing key comes from the `DP05_DRILL_SIGNING_KEY` env var when
set; otherwise a fresh key is generated per run (the report records only
the key id, never the key).  Exit status is `0` on pass, `2` on any
scenario failure — matching the reconciliation CLI's convention.

## 8. Verification

`api/tests/resilience/test_dr_drill.py`:

* every scenario in `SCENARIO_IDS` is present and passes;
* each load scenario asserts no loss / no duplicate / full ledger
  accounting;
* each outage scenario asserts fail-safe behaviour and intact recovery;
* the restore drill asserts published-data and provenance survival, no
  duplicate publication on re-replay, and a measured RPO=0 / RTO;
* the report asserts all acceptance criteria pass, the signature
  verifies under the drill key, detects tampering, survives JSON
  round-trip, and rejects a wrong key.
