# pinned-masthead-flow — bench redesign, prompt rewrite, schema cleanup

18 commits. Three independent threads landed on one branch because they
share verification (every change either touches the bench UI or the
ingest path that feeds it). Each thread is reviewable in isolation by
filtering commits.

## Summary

- **Front-page bench rebuilt.** No more two-column "working" sidebar.
  Hero stays at the top of the page; below it a single-column report
  assembles itself: Thinking steps → §01 prose (SSE) → seven sealed
  §-cards revealing one at a time → sticky CTA rail once everything is
  on screen. The scroll-jolt regression is fixed (asserted by Playwright:
  `delta=0`). The §08 Action Plan card spans full width as the punchline.
- **Prompts handle IRC, ORC, or both.** Teaser + premium system prompts
  rewritten to switch vocabulary based on the rating system that
  actually rates the boat. New fabrication ban (no inventing races,
  fleet positions, or sister-boat counts not in the context). UX
  references updated to match the new "Thinking" section.
- **Schema cleanup (three migrations).** Migration 0012 renames
  `certificates` → `irc_certificates` for symmetry with `orc_certificates`.
  Migration 0013 promotes ten high-value fields from
  `orc_certificates.raw_data` to typed columns including the full VPP
  polar table (allowances) as JSONB. Migration 0014 drops the
  never-written `boats.current_*` columns (verified 100% NULL) and
  renames `irc_certificates.displacement` → `displacement_kg` (matching
  the convention used by `boats.displacement_kg` etc). All three
  applied + verified on dev: 3,809 IRC certs migrated cleanly, 9,886
  ORC rows backfilled with polars, 337 design-class rows in the
  refreshed materialised view.
- **Crawler ops.** Scheduled three dormant scrapers (sailracehq, isora,
  rhkyc) at weekly slots; removed the commented `rorc` line whose
  archive only covered 2007–2022; documented cowesweek, sydneyhobart,
  sailwave as on-demand inline in `api/crontab.txt`.

## What you can verify on dev right now

```
front=200    https://dev.sailratings.com/
api=200      https://api-dev.sailratings.com/v1/health   (status: healthy)
search=200   https://api-dev.sailratings.com/v1/search?q=sun+fish
boat=200     https://api-dev.sailratings.com/v1/boats/12330
```

Search "sun fish", pick a result. Hero stays; bench grows below; thinking
steps appear with brass ticks + `mm:ss.fff` audit timestamps; §01 prose
streams in below; seven sealed cards materialise one at a time over
~12s (paper-land + wax-band-fill + lock-press choreography); sticky
charcoal CTA rail slides in once everything is on screen.

Bench screenshots committed at `docs/bench-streaming.png`,
`docs/bench-sealed.png`, `docs/bench-cta.png`, plus the full slow-reveal
sequence at `docs/slow-reveal-T{01,09,17}s-full.png`.

## Commits by thread

### Bench redesign (10 commits)

The structural arc: extract → refactor → polish → restructure. Each step
was verified by Playwright before the next landed.

```
c2d15f0 feat(web): add PinnedMasthead component (sticky navy strip + masthead row)
d91e734 feat(web): add StickyCheckoutRail (persistent charcoal CTA bar)
c0c9246 refactor(web): rename StickyCheckoutRail handle->handleCheckout
f62097b feat(web): add SealedSectionGrid (brass-stamped 2-col sealed cards)
28a1359 refactor(web): rewrite TeaserAnalysis as two-column bench with new chrome
07ba901 fix(web): remove scrollIntoView on boat select; hide hero during bench
89482bc feat(web): pinned masthead + table-first reveal on report page
46f41e3 chore(web): delete dead PurchaseCTA.tsx (replaced by StickyCheckoutRail)
b5be258 test(web): add bench + report screenshot tests; assert no scroll jolt
ac0f53b polish(web): commanding masthead, full-height bench, Action Plan as punchline
4557ebb fix(web): keep Hero on front page; stream sealed sections continuously
9caaef9 design(web): wax-stamp choreography for sealed cards; surveyor-voice drafter
df39f9b restructure(web): kill the working column; thinking steps now lead the report
```

Key fixes from review:
- "screen moves down funny" → root-caused to `scrollIntoView` firing
  before bench content existed. Replaced with `window.scrollTo` to a
  fixed Y (`window.innerHeight - 96`) so the target is absolute, not a
  moving element. Asserted in `screenshot-search.mjs` (scrollY
  delta=0).
- "feels like a new page" → restored Hero rendering; bench grows below
  it on the same page.
- "streaming, not clumps" → sealed cards reveal continuously starting
  at T+5s during prose streaming, not all at `isDone`. CTA gated on
  `isDone && allSealedOut`.
- "no working column" → single-column report, Thinking section at the
  top of the bench (full width, larger typography, brass ticks +
  timestamps), then §01 prose, then sealed cards.
- "use frontend-design more" → wax-stamp choreography on each sealed
  card: paperLand → waxFill (clip-path) → lockPress. Drafting indicator
  rotates through five surveyor-voice phrases under a brass needle that
  sweeps every 2.6s.

### Prompt rewrite (1 commit)

```
ded57aa prompt(api): teaser + premium handle IRC/ORC/both; fabrication ban; UX refs
```

Teaser (`SYSTEM_PROMPT_TEASER`) and premium (`SYSTEM_PROMPT_PREMIUM`)
both rewritten in `api/src/irc_data/api/services/insights_service.py`:

- Audience: "IRC, ORC, or both" (was IRC-only).
- New "WHICH RATING SYSTEM" section teaches the model to switch
  vocabulary per boat. IRC boats get TCC + IRC framing. ORC boats get
  GPH/CDL/triple-numbers + VPP-aware language (the formula isn't
  secret). Dual-rated boats can compare.
- Race-results anchor is now conditional on race results actually being
  in the context. Explicit ban on fabrication (no invented races,
  fleet positions, sister-boat counts, or measurement values).
- NUMBERS section updated: references "Thinking section at the top of
  the bench" (was "working-steps panel"); adds ORC formatting rules
  (GPH 2dp, CDL 3dp) alongside the existing IRC 4dp rule.
- Teaser word cap raised 220 → 240 to accommodate dual-rated cases.

### Schema cleanup (5 commits, 3 migrations)

```
8d72c56 schema(api): draft migrations 0012 (rename) + 0013 (promote ORC VPP)
5d9f8b8 refactor(api): point all SQL + admin policy at `irc_certificates`
eff9e85 schema(api): wire ORCCertificate model + orc scraper to migration 0013 columns
1bd6748 ops(api): schedule sailracehq/isora/rhkyc; remove commented rorc
6fac766 schema(api): migration 0014 — drop dead boats.current_*, standardise IRC displacement
```

**Migration 0012 — rename `certificates` → `irc_certificates`** (applied
to dev). IRC-only table, sitting next to `orc_certificates`. The
asymmetric naming made new readers wonder if `certificates` meant "all
certs" or "IRC only". Migration renames the table, PK, unique constraint,
FK constraint, indexes, and id sequence. Code-side pair touches 10
files / 14 sites — every raw SQL `text("...FROM certificates...")`,
the SQLAlchemy `__tablename__`, the admin policy dict key, and three
admin/monitoring response keys (no frontend consumers verified).

**Migration 0013 — promote ten ORC fields** (applied to dev). The ORC
scraper had been storing the full RMS response in
`orc_certificates.raw_data` since the start, but several high-value
fields were never extracted to typed columns:

| Column            | What                                       |
|-------------------|--------------------------------------------|
| `allowances`      | Full VPP polar table (JSONB)               |
| `dynamic_allowance` | No-spinnaker TMF                         |
| `dspl_sailing`    | Sailing displacement (vs measurement)      |
| `imsl`            | Mast height above WL                       |
| `mb`              | Maximum beam                               |
| `aphd`/`apht`     | Appendage depth + type code (fin/bulb/etc) |
| `wss`             | Wetted surface area, sailing trim          |
| `tmf_offshore`/`tmf_inshore` | Time multiplication factors     |

Migration backfilled 9,886 rows from existing `raw_data` JSON in a
single defensive UPDATE; the scraper now populates these columns on
future ingest via `backfill_orc_details()`. Unblocks ORC-native speed
prediction, design-compare, and IRC↔ORC cross-rating without changing
any scraping behaviour.

**Migration 0014 — schema dedup** (applied to dev). Two wins from the
audit:
- Drop `boats.current_name`, `boats.current_sail_number`,
  `boats.current_flag`. Verified 100% NULL across all 9,384 rows. Never
  written. `boat_identities` is the real source of truth for historical
  name/sail/owner observations.
- Rename `irc_certificates.displacement` → `displacement_kg` (Numeric
  8,1 → 10,1) so the column name matches the convention used by
  `boats.displacement_kg`, `design_classes.nominal_displacement`, etc.
  The bare `displacement` name was ambiguous next to
  `orc_certificates.displacement`.

The materialised view `mv_within_class_stats` depends on the column;
the migration drops + recreates the view around the column changes
(view body captured verbatim from the running DB and parameterised on
the displacement column name for symmetric upgrade/downgrade). Refresh
runs daily via cron; manual refresh recommended after deploy.

**Crawler ops.** Three previously-dormant scrapers added to cron at
distinct slots:

| When (UTC) | Source | Why |
|---|---|---|
| Tue 10:00 | `sailracehq` | Replaces RORC for post-2023 UK offshore |
| Tue 11:00 | `isora` | Irish Sea Offshore Racing Association |
| Wed 10:00 | `rhkyc` | Hong Kong fleet, timezone-friendly |

Plus inline documentation of why `rorc`, `cowesweek`, `sydneyhobart`,
`sailwave` are intentionally unscheduled — preserves the rationale for
the next on-call.

## Deploy notes

Already done on dev:

- `api/.venv` editable install repointed to monorepo path (was pointing
  at the pre-cutover `/home/irc-data/code/irc-data/`). Pip pulled in
  five missing deps that had been silently absent (stripe, posthog,
  resend, scikit-learn, scipy).
- Migrations 0012 + 0013 applied. `alembic current` → 0013.
- API restarted. Health endpoint reports `{status: "healthy", counts:
  {... irc_certificates: 3809, orc_certificates: 13997 ...}}`.
- Crontab installed.

For production:

1. `cd api && .venv/bin/pip install -e .` to repoint venv.
2. `.venv/bin/alembic upgrade head` to run both migrations.
3. Restart the API process (`start-api.sh` if matching this dev
   pattern, or whatever your prod runner is).
4. Smoke-test the four endpoints listed at the top.
5. `crontab api/crontab.txt` (note: this is dev-box scheduling; prod
   may use a different mechanism).

## Test plan

- [ ] Hit `https://dev.sailratings.com/`, search "sun fish", pick a
      result. Confirm: Hero stays visible at top, smooth scroll to
      bench, Thinking steps stream in with brass ticks and `mm:ss.fff`
      timestamps, §01 prose streams in, sealed cards materialise one
      at a time over ~12s with the wax-stamp animation, sticky CTA
      rail slides in last.
- [ ] `curl https://api-dev.sailratings.com/v1/health` returns
      `{status: "healthy", counts: {irc_certificates: 3809, ...}}`.
- [ ] `SELECT COUNT(allowances) FROM orc_certificates` returns 9,886.
- [ ] `crontab -l | grep sailracehq` shows the new entry.
- [ ] Generate a test order and view the report at `/report/[token]` —
      confirm the pinned masthead appears identically to the bench,
      then "File compiled. Opening…" subline, then table-first reveal,
      then prose backfills, then RAI + rivals.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
