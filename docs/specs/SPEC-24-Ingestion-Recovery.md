# SPEC-24: Ingestion Recovery & Source Operations

## 0. What the data says (dev DB snapshot 2 Sep 2026)
- `ingestion_log`: ORC last ran 26 Jul 01:00 (155 daily runs before that, all `completed`); TopYacht 26 Jul; RHKYC 22 Jul; ISORA 21 Jul; SailRaceHQ 14 Jul. **No source ran at all between 27 Jul and 1 Sep.** SailSys resumed on 2 Sep (76 runs, 55 `completed_with_errors` with an empty `error_message`, 6,726 rows found, 0 new).
- The DP-01/OPS-01 framework built by the factory (source adapter SDK, acquisition primitives, breakage detection + quarantine, run ledger, staleness watchdog, raw capture, replay/backfill) is merged but **not executing**: `source_runs`, `watchdog_alerts`, `source_incidents`, `raw_objects`, `retrieval_events`, `fact_assertions` all have 0 rows. Ingestion still goes through the legacy `irc-data scrape …` CLI on the dev server's crontab (`api/crontab.txt`).
- `crontab.txt` schedules a watchdog every 15 min and a health check daily with `--notify`; `watchdog_alerts` is empty and nobody was told the nightlies had stopped. Either cron is not installed from the new path (HANDOFF.md open items 2–4: crontab still pointed at the deleted `irc-data` dir, systemd unit, 1Password `op run`), or the commands fail before logging.
- `source_schedule_state` has 11 rows (schedule_id, cadence, paused) but only two Temporal workflows exist (`irc_pdf_workflow`, `raw_capture_workflow`); there is no Temporal `DailyScrapeWorkflow` despite the data-source register saying so.
- Firecrawl: 1,145 calls total; the last 400 were all `news.scraper` → sailingscuttlebutt.com on 26 Jul at 1 credit each. The parity baseline (docs/scrapers/firecrawl-parity-baseline-2026-05-21.md) found HTML sources ready (ISORA 0.95, SailRaceHQ 0.88, TopYacht 0.98), PDFs not (RHKYC quarantined), Cowes Week partial (no TCCs on points pages). Cron for Firecrawl ingestion was held; legacy and Firecrawl were meant to run in parallel behind a parity gate, then cut over. Neither has happened.
- The 26 Jul product-and-data plan (docs/superpowers/plans/2026-07-26-product-and-data-plan.md, uncommitted) lists Phase 0 "fix the mess" items that are still open: unrun DNF backfills, 163 garbage rows, 21k hollow SailSys rows, Tier-S/A boat merges, design_classes FK, ORC detail drain + matviews, Firecrawl parity, real counts endpoint.

## 1. Principle
One scheduler, one run ledger, one alert channel. A source is "working" only when its run appears in `source_runs`, its freshness is inside budget in the watchdog, and the admin Scrapers page (SPEC-22 §3.3) shows it green. Anything else is not working, whatever the code says.

## 2. Recovery (do first, on the dev server, by hand)
1. `crontab -l` — confirm whether the installed crontab points at `/home/irc-data/code/sailratings/api` (HANDOFF item 2). If not: `crontab api/crontab.txt`.
2. Run one nightly by hand with the cron environment: `cd api && bash -c "$OP_ENV irc-data scrape orc"` and read the log. Fix whatever fails (venv path, `op run` service account, `DATABASE_URL`).
3. Confirm `irc-data scrape-watchdog` writes to `watchdog_alerts` when a source is stale, and that `health-check --notify` reaches a human (webhook target must be a channel someone reads; record which in `docs/data-quality/data-source-register.md`).
4. Backfill the gap: ORC daily snapshot is idempotent — run it once; SailSys incremental cursor — check why 6,726 found → 0 new (cursor stuck vs nothing raced) before running `rematch-results`.
5. Commit HANDOFF.md with items 1–4 closed and the systemd unit in the repo.

## 3. Scheduling: cron → Temporal schedules
- Implement `DailyScrapeWorkflow(source)` and `IncrementalResultsWorkflow(source)` in `temporal/workflows/` wrapping the existing scraper entry points as activities (heartbeats, 1 retry, timeout per source from `data_sources.cadence`).
- One Temporal Schedule per `data_sources` row, id = `source_schedule_state.schedule_id`, paused flag mirrored both ways. Migrate the crontab lines one source at a time; delete the cron line only after two green Temporal runs. Keep `scrape-watchdog`, `health-check`, `refresh-views`, log cleanup on cron until the end, then move them too.
- Every activity run writes a `source_runs` row through the run ledger (OPS-01-03) and an `ingestion_log` row (until the admin reads `source_runs`, keep both).
- Admin `pause/resume/run` (SPEC-22 §3.3) calls the Temporal Schedule API.

## 4. Observability that reaches a person
- `scrape-watchdog` freshness budgets per source: ORC 26 h, TCC 26 h, SailSys 2 h, TopYacht 26 h, weekly sources 8 d. Alert → email + Slack webhook + `watchdog_alerts`; recovery message on return.
- `health-check --notify` posts the daily summary; failure of the health check itself must alert (dead-man switch: a healthchecks.io-style ping URL that pages if not hit by 09:30 UTC).
- Fix `completed_with_errors` with empty `error_message` (SailSys): capture per-club exception text.

## 5. Firecrawl: decide, gate, cut over
- Keep Firecrawl for: discovery of new result pages (`discover-and-ingest`, `seed-crawl`), HTML long-tail sources that passed parity (ISORA, SailRaceHQ), and entry-list discovery (SPEC-25). Do **not** use it for PDFs (RHKYC stays legacy) or for sources with a stable legacy scraper (SailSys, TopYacht, ORC API, TCC CSV).
- News scraper: 400 calls/day against one domain is the whole Firecrawl spend. Move sailingscuttlebutt (and any feed with RSS) to the `raw-capture --source sailing-news` RSS path; reserve Firecrawl for pages without feeds.
- Parity gate to cut a source over: 14 consecutive days where Firecrawl rows ≥ 95% of legacy rows and place-1 agreement ≥ 98% (`irc-data parity-report --source X`). Cut over = legacy cron/schedule paused, Firecrawl schedule enabled, `data_sources.adapter_class` updated.
- Cowes Week: extend the extractor to follow per-race detail pages for TCCs before it can pass the gate.
- Budget: `crawl_budget_settings` enforced (daily credit cap, hard stop, admin-visible on the Firecrawl page); `FIRECRAWL_API_KEY` in the 1Password vault, not `~/.env`.

## 6. Phase 0 data hygiene (from the 26 Jul plan; each is one issue)
1. Run the written-but-unrun backfills: TopYacht DNF (≈680 rows), SailRaceHQ DNF; delete the 163 empty-name rows; decide the 21k SailSys "finished, no place" rows.
2. Boat identity: apply Tier-S `- SEC` merges (37, dry-run clean) and auto-merge Tier-A; Tier-B/D go to the admin Duplicate boats queue (SPEC-22 §3.2). Record everything in `boat_merges` with `loser_snapshot`.
3. `design_classes`: finish merge/null/backfill sweep (`design_class_merges`, `boats_design_canonical_sweep`, `design_class_attr_nulls` are the working tables); add FK `boats.design_canonical → design_classes`.
4. ORC: finish the VPP detail drain (73% missing GPH/CDL/allowances on 26 Jul), then add ORC to the materialised views.
5. `GET /v1/stats` — real counts (boats, certs, results, events, countries, last updated) from one query; marketing copy reads from it.

## 7. Phase 1 (separate issues, after §2–§4 are green)
- IRC history reconstruction at scale: wayback TCC harvest + `irc_backfill` orchestrator, priority boats-with-results → GBR/AUS/IRL. KPI: ≥60% of boats raced in the last 24 months with ≥3 years of TCC history.
- Cert parsing accuracy: close the extend.ai audit buckets (spinnaker sym/asym, SER/nodisp, FL) — `cert_sym_asym_reclassify`, `cert_reparse_*` tables are the working set.
- UK/Solent coverage: schedule YachtScoring; discovery for HRSC/Hamble/Solent series + JOG.

## 8. Acceptance (epic)
- Every source in `data_sources` has a Temporal schedule, a `source_runs` row from its last run, and shows green on the admin Scrapers page for 7 consecutive days.
- A deliberately paused source triggers a watchdog alert to Slack/email within its freshness budget, and a recovery message when resumed.
- Firecrawl daily spend is capped and visible; at least ISORA and SailRaceHQ are Firecrawl-only with legacy paused; news ingestion uses RSS.
- `GET /v1/stats` counts match `_census` within 1%.
