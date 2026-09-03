# EPIC-24: Ingestion Recovery & Source Operations

Get every data source running again, on one scheduler, with alerts that reach a person, and finish the Phase 0 data-hygiene list from the 26 Jul product-and-data plan. Spec: `docs/specs/SPEC-24-Ingestion-Recovery.md`.

Why: nothing ran between 27 Jul and 1 Sep 2026 and no alert fired. The DP-01/OPS-01 framework (adapter SDK, run ledger, watchdog, quarantine, raw capture, replay) is merged but has never executed — its tables are empty — while production ingestion still runs through legacy CLI cron jobs. Firecrawl spend is going to a news site with an RSS feed, and the parity cutover planned in May never happened.

## Issues (in order)
1. Ops: recover the dev-server crontab / op-run / systemd (HANDOFF items 2–4) and get ORC, TCC, TopYacht, ISORA, RHKYC, SailRaceHQ running tonight — SPEC-24 §2
2. Investigate SailSys "found > 0, new = 0" and capture per-club error text — SPEC-24 §2.4, §4
3. Watchdog + health-check must reach a human: Slack/email webhook, dead-man ping, freshness budgets — SPEC-24 §4
4. Temporal `DailyScrapeWorkflow` / `IncrementalResultsWorkflow` + one Schedule per `data_sources` row, mirrored in `source_schedule_state` — SPEC-24 §3
5. Migrate crontab lines to Temporal schedules one source at a time; every run writes `source_runs` — SPEC-24 §3
6. Firecrawl: news → RSS; parity gate; cut over ISORA + SailRaceHQ; Cowes Week per-race TCC follow; budget cap + vault key — SPEC-24 §5
7. Run the unrun backfills: TopYacht DNF, SailRaceHQ DNF, 163 garbage rows, decision on 21k hollow SailSys rows — SPEC-24 §6.1
8. Boat identity: Tier-S/A auto-merges with `loser_snapshot`; Tier-B/D to the admin queue — SPEC-24 §6.2
9. design_classes sweep + FK from `boats.design_canonical` — SPEC-24 §6.3
10. ORC VPP detail drain to completion + ORC in materialised views — SPEC-24 §6.4
11. `GET /v1/stats` real counts endpoint — SPEC-24 §6.5
12. IRC history reconstruction at scale (wayback + irc_backfill) — SPEC-24 §7
13. Cert parsing accuracy: close extend.ai audit buckets — SPEC-24 §7
14. UK/Solent coverage: schedule YachtScoring, discovery for Solent series + JOG — SPEC-24 §7

## Done when
SPEC-24 §8 acceptance holds for 7 consecutive days.
