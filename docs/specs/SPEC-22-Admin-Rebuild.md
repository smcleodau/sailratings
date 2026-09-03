# SPEC-22: Admin Rebuild (SailRatings design system)

Supersedes the UI sections of SPEC-01 and SPEC-05. Those specs describe a "Paper" (navy/brass/cream) admin and a 0.40–0.80 confidence-score identity queue. Neither exists: the design system is now the SailRatings DS (`--sr-*` tokens in `web/src/app/globals.css`), and identity resolution is boat-vs-boat clustering in `dupe_review_queue`, not result-vs-boat scoring.

Prototype (clickable, driven by a 2 Sep 2026 snapshot of the dev DB and the live Stripe account): https://claude.ai/code/artifact/a824629d-4bf3-4e9b-a73a-4c39978b68d9
Data findings that shaped it: project doc `claude/admin-data-findings.md` (Claude project "SAILRATINGS Website").

## 1. Design rules (non-negotiable)
- Ground: the admin uses a **Dusk ramp** as its page/surface colour so it reads as a different place from the public site. Tokens (add to `globals.css` under `.admin-theme`):
  `--dusk-950:#0B0917; --dusk-900:#110E22; --dusk-800:#181433; --dusk-700:#231D4A; --dusk-500:#7C6BD4; --dusk-300:#A99CE6; --dusk-200:#C4BBF0; --dusk-50:#EEEBFA;`
  `--surface-page:var(--dusk-950); --surface-card:var(--dusk-900); --surface-deep:var(--dusk-800); --surface-raised:var(--dusk-700);`
  Text: `#EDEBF5` body, `#AFA8CC` muted, `#8A83AE` labels. Hairlines `rgba(196,187,240,0.10)`.
- Signal `#FF4119` only on genuine rating values and on exactly one primary action per screen (e.g. "Merge into this"). Money is not a rating: use Starboard.
- Status: Starboard ok · Buoy attention/stale · `#F0806A` failed · Open Water info/source tag · Marine teal for agent/factory tags.
- Type: Saira Condensed 700/800 caps for page and section titles; Libre Franklin body; JetBrains Mono for every number, id, label, timestamp and table header.
- No icon set. Nav glyphs are typographic (◆ ≈ ↔ ± ▤ ▦ ↻ § ⌁ £ @ ›). Remove Lucide from admin pages as they are migrated.
- Empty states say what exists and what is needed. No sample data, ever.

## 2. Shell
- `web/src/app/admin/layout.tsx`: replace the top-tab `AdminNav` with a 232px left sidebar grouped into four zones: **Data quality**, **Operations**, **Customers**, **Agents**, plus **Today** at the top. Sidebar counts come from `GET /v1/admin/overview`.
- Topbar: global search (boat, sail number, cluster id, run id; `/` focuses), environment badge (dev/prod, DB), two health pills (SailSys live / nightly state).
- Keep Clerk gating in `middleware.ts`. `admin.sailratings.com/` → `/admin` (currently `/admin/swarm`).
- One typed client `web/src/lib/adminApi.ts`; pages never call `fetch` directly.

## 3. Screens and their data

### 3.1 Today — `GET /v1/admin/overview`
Returns, in one call:
```json
{ "sources": [{"source":"orc_api","last_run":"…","status":"completed","stale_days":38,"runs":155,"found":12336,"new":12,"last14":["completed",…]}],
  "today": {"runs":76,"by_status":{"completed":21,"completed_with_errors":55},"found":6726,"new":0},
  "runs_per_day": [["2026-07-05",4],…60],
  "dupes": {"pending_clusters":174,"pending_rows":409,"tiers":{"B":96,"D":78}},
  "corrections_pending": 0, "orders_7d": 0,
  "attention": [{"kind":"stale","title":"…","detail":"…","href":"/admin/scrapers","age":"38d"}] }
```
Attention rules (server-side): any nightly source with `stale_days > 2`; any source whose latest run is `failed`; SailSys day with `found > 0 and new == 0`; `dupes.pending_clusters > 0`; empty monitoring tables (`source_incidents`, `source_runs`, `watchdog_alerts`) while ingestion_log has runs.

### 3.2 Duplicate boats — replaces "Identity queue"
Source: `dupe_review_queue` grouped by `cluster_id`, verdict = 'PENDING'.
- `GET /v1/admin/dupes?tier=&size=&country=&cursor=` → clusters with boats ordered by evidence (`race_results + 20*cert_count` desc). Include per-boat: id, boat_name, sail_number, country, design, year_built, race_results, cert_count, latest_activity, owner.
- `POST /v1/admin/dupes/{cluster_id}/merge {"winner_id": 17213}` → in one transaction: call the existing merge routine used by the sail-number merge script for every other boat in the cluster (re-point `boat_identities`, `event_entries`, `irc_certificates`, `orc_certificates`, `tcc_snapshots`, `orders`, `boat_events`, `boat_news_mentions`; write `boat_merges` rows with `cluster_key`); set `verdict='MERGED', reviewed_by=<clerk email>, reviewed_at=now()` on all rows of the cluster; write `admin_edits`.
- `POST /v1/admin/dupes/{cluster_id}/not-dupe {"reason":"design_conflict|year_conflict|big_cluster_distinct|other","note":""}` → insert `boat_not_dupe (cluster_key, boat_ids, reason)`; set `verdict='NOT_DUPE'`.
- `POST /v1/admin/dupes/{cluster_id}/skip` → `verdict_note='skipped'`, moves to end of queue (order by `reviewed_at nulls first`).
- UI: one card per cluster, one column per boat (2–5), the most-evidenced boat highlighted and carrying the Signal button; footer with reason select + Not duplicates + Skip. Filters: tier, cluster size, country. Card animates out on decision; sidebar count decrements.
- Merge history page: `boat_merges` (recent 100, monthly counts) and `boat_not_dupe` (reason counts). Flag: there is no unmerge; do not build one in this epic, but record `loser` row snapshot in `boat_merges.loser_snapshot jsonb` (new column) so one can be built.

### 3.3 Scrapers — `GET /v1/admin/scrapers` (exists; extend)
Per source from `ingestion_log`: runs, last run, status, found/new, duration, last failure (time + message), last 14 statuses, `stale_days`, `new_30d`. Add:
- `source_schedule_state` join → `cadence`, `paused`, `schedule_id`.
- `POST /v1/admin/scrapers/{source}/pause` / `/resume` → flips `paused` and pauses/unpauses the Temporal schedule.
- `POST /v1/admin/scrapers/{source}/run` → starts the workflow once.
- Bug to fix in the same epic: SailSys runs write `status='completed_with_errors'` with `error_message=''`. Record the per-club error text.

### 3.4 Data health — new
- `GET /v1/admin/health/tables` → pg_stat row counts and sizes for every table; flag `rows=0`.
- `GET /v1/admin/health/completeness` → % non-null for `boats.design, design_canonical, country, year_built, builder, designer, loa, lwl, beam_max, displacement_kg`, computed nightly into `admin_metrics (metric text, value numeric, computed_at)` rather than on request.
- Events: % null venue, top organisers, sample of un-normalised names.

### 3.5 Corrections, Tables, Audit, Sources, Firecrawl, Data chat
Existing pages and endpoints; migrate into the shell and restyle. Corrections shows an honest empty state while `boat_corrections` is empty. Sources page adds the `source_schedule_state.paused` toggle and `data_sources.robots_checked_at`.

### 3.6 Customers zone
See SPEC-23.

## 4. Order of work
1. Shell + Today + Scrapers + Data health (no schema change except `admin_metrics`).
2. Duplicate boats + Merge history.
3. Migrate the existing pages.
4. Customers zone (SPEC-23).

## 5. Acceptance
- Every admin page renders inside the sidebar shell with Dusk ground; no Paper tokens, no Lucide, no Tailwind default colours.
- Today shows the real stale-source condition (as of 2 Sep 2026: ORC/TopYacht 38 days stale) without hard-coding.
- A merge decision in Duplicate boats writes `dupe_review_queue.verdict`, `boat_merges`, `admin_edits` in one transaction and is visible in Merge history.
- Scraper pause/resume changes the Temporal schedule and is reflected on reload.
