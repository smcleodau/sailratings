# DP-00-03 — Raw archival capture: Yacht Scoring + Manage2Sail

> Nightly raw archival capture of the two highest-yield unexplored results
> platforms — **Yacht Scoring** and **Manage2Sail** — as raw bytes plus a
> provenance envelope.  **No parsing** happens in this track.

| | |
|---|---|
| **Issue** | DP-00-03 |
| **Policy** | `v1.0` (DP-01-02; supersedes interim-v0 / DP-00-01) |
| **Sources** | `yachtscoring`, `manage2sail` |
| **Capture engine** | `api/src/irc_data/scrapers/raw_capture_ys_m2s.py` |
| **Workflow** | `api/src/irc_data/temporal/workflows/raw_capture_ys_m2s_workflow.py` |
| **Activities** | `api/src/irc_data/temporal/activities/raw_capture_ys_m2s_activities.py` |
| **Tests** | `api/tests/scrapers/test_raw_capture_ys_m2s.py` |
| **Verification** | `api/scripts/verify_dp_00_03.py` |
| **Schedule** | Nightly **02:30 UTC** (`crontab.txt`), inside the 01:00–06:00 window |

---

## Scope

Begin capturing the public Yacht Scoring and Manage2Sail race-results pages as
**raw archives**.  Every page is fetched, SHA-256 hashed, and stored unchanged
in the content-addressed raw object store with a provenance envelope.  No
parsing, no normalisation, no extraction — that is DP-02's job downstream.

## Fetch primitive (the DECISION)

Per the DP-00-03 decision, the fetch primitive is split by need:

* **Plain HTTP with conditional requests** is the **primary** primitive —
  cheaper, no Firecrawl credits.  Both platforms publish results pages whose
  URLs follow a stable, discoverable pattern, so the raw HTML is captured
  directly.  Repeat fetches send `If-None-Match` / `If-Modified-Since`
  (sourced from the prior night's `retrieval_events` rows); unchanged pages
  come back as **HTTP 304** no-ops.
* **Firecrawl (or equivalent) is reserved** for the **discovery / map** phase
  and for any JavaScript-rendered page plain HTTP cannot capture
  (`capture_url_rendered`).  Every provider call is budget-gated and logged
  through the OPS-01-05 crawl ledger (`crawl_telemetry` / `firecrawl_calls`).

**Every call is logged.**  Plain-HTTP page fetches are logged to
`firecrawl_calls` with `credits = 0` (no credit spent); discovery calls are
logged as `mode = 'map'`; rendered captures as `mode = 'scrape'`.  Raw byte
captures are additionally written to `retrieval_events` (the provenance
envelope audit log).

## Discovery (public index pages only)

Discovery is via **public index pages only** — we never enumerate event IDs by
brute force and never touch authenticated areas:

* Yacht Scoring: `https://www.yachtscoring.com/event_results_archive.cfm`
* Manage2Sail: `https://www.manage2sail.com/event`

Result links are extracted and filtered per source (result-page URL families
only; login / contact / `mailto:` / navigation links excluded).  The discovery
frontier is capped (`DEFAULT_MAX_DISCOVERY_PAGES = 200`; canary mode caps to
`CANARY_MAX_DISCOVERY_PAGES = 12`).

## Output contract (handoff)

Every fetched object produces a `ProvenanceRefV1` envelope:

```
RawArtifactV0 = bytes + SHA-256 + URL + fetch time + policy_version 'v1.0'
```

* Raw bytes → content-addressed `RawObjectStore` at `data/raw/<source>/`.
* Envelope → `retrieval_events` (append-only audit) + `raw_objects`
  (content-hash upsert).
* Run summary → `ingestion_log` via the `write_ledger_activity`.

## Idempotency

Re-running the same night is a no-op:

* Content-addressed store deduplicates identical bytes.
* Conditional requests turn unchanged pages into HTTP 304 no-ops.
* Prior content hashes are loaded so an unchanged page is never re-stored.

A rerun therefore **stores zero new raw objects** and fetches **zero
unchanged pages** (they return 304).  Verified by `verify_dp_00_03.py`.

## Politeness (interim-v0 §3, carried into v1.0)

* `robots.txt` fetched per host; **fail-closed** — a robots error stops the
  source run (`status = 'robots_error'`).  Disallowed paths are skipped.
* 1 request / 2 s + 1 s jitter, per domain.
* Nightly collection window **01:00–06:00**; out-of-window runs abort
  (`status = 'window_closed'`).
* Kill switch: `data_sources.enabled` re-checked before and during the run
  (`status = 'kill_switch'`).
* Hard caps: max 5,000 fetches / source / night; max 25 MB per object.

## Running it

Full nightly run (both sources):

```bash
irc-data scrape raw-capture --source dp-00-03
```

Single source:

```bash
irc-data scrape raw-capture --source yachtscoring
irc-data scrape raw-capture --source manage2sail
```

**Live canary night** (small, well inside rate caps):

```bash
irc-data scrape raw-capture --source dp-00-03 --canary
```

Manual / debug (skip the window check, explicit URLs, custom store):

```bash
irc-data scrape raw-capture --source yachtscoring \
    --no-window --url "https://www.yachtscoring.com/event_results_cumulative/12345"
```

The Temporal workflow `NightlyRawCaptureYsM2sWorkflow` is registered on the
`data-pipeline` worker and accepts params `max_fetches`, `enforce_window`,
`canary`, `max_discovery_pages`.

## Verification

Recorded fixture run (no live network) + idempotent-rerun proof:

```bash
PYTHONPATH=src python3 scripts/verify_dp_00_03.py
```

Proves: fetch→hash→store with envelope, crawl-ledger coverage (OPS-01-05),
politeness gates (window / kill switch / caps), **rerun fetches zero unchanged
pages** (HTTP 304), and canary discovery cap.  Unit tests:

```bash
PYTHONPATH=src python3 -m pytest tests/scrapers/test_raw_capture_ys_m2s.py -q
```
