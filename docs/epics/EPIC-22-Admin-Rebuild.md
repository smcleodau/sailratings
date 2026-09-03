# EPIC-22: Admin Rebuild in the SailRatings design system

Rebuild `web/src/app/admin` as one product with a sidebar shell (Today · Data quality · Operations · Customers · Agents) on the SailRatings DS with a Dusk ground, driven by the data the platform already holds. Spec: `docs/specs/SPEC-22-Admin-Rebuild.md`. Prototype: https://claude.ai/code/artifact/a824629d-4bf3-4e9b-a73a-4c39978b68d9

Why now: the nightly scrapers have been stale since 26 July and nobody saw it; 174 duplicate-boat clusters are waiting for a verdict with no UI; specs SPEC-01/05 still point the factory at a retired design system and a confidence queue that does not exist.

## Issues
1. Admin shell: sidebar, topbar, Dusk tokens, typed `adminApi.ts`, remove Lucide from admin — SPEC-22 §1–2
2. API: `GET /v1/admin/overview` — SPEC-22 §3.1
3. Web: Today screen — SPEC-22 §3.1
4. API: duplicate-boat endpoints (list / merge / not-dupe / skip) + `boat_merges.loser_snapshot` — SPEC-22 §3.2
5. Web: Duplicate boats + Merge history screens — SPEC-22 §3.2
6. API: scrapers pause/resume/run + `source_schedule_state` join; fix SailSys empty `error_message` — SPEC-22 §3.3
7. Web: Scrapers screen — SPEC-22 §3.3
8. API + Web: Data health (`admin_metrics` nightly, tables census, completeness) — SPEC-22 §3.4
9. Web: migrate Corrections, Tables, Audit, Sources, Firecrawl, Data chat into the shell — SPEC-22 §3.5
10. Docs: mark SPEC-01, SPEC-05 superseded; close duplicate "[EPIC] Human-In-The-Loop Admin" rows in Notion

## Done when
SPEC-22 §5 acceptance passes on dev.sailratings.com/admin.
