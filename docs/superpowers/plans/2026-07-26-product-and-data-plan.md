# SailRatings — Product & Data Plan

**Date:** 26 July 2026
**Inputs:** New website design (Website / Mobile / Admin / Report screens), Sailing Rating Data Platform Business Plan (March 2026), full audit of the current repo (scrapers, schema, analysis engines, report V2).

---

## 1. The strategic shift this plan serves

Today the product is a **one-page funnel**: search → free teaser → $99 report. The new design and the business plan commit to something structurally different — a **membership platform**:

- **Crew (free)** — rating book search, boat pages, rating history, one teaser per boat. The SEO flywheel.
- **Skipper (~$290/yr)** — living optimisation report, what-if simulator, race-prep briefs, sail programme advisor, rival intelligence, the analyst, watch feed. One boat.
- **Programme** — everything ×10 boats, design comparator, fleet/club analytics, CSV export, priority requests. Sailmakers, designers, campaigns.
- **$99 single report** stays, credited to Skipper within 30 days.

**The core implication for data:** a one-off report tolerates batch data assembled at purchase time. A membership that promises *"always current"* reports, pre-race briefs, and a watch feed requires data that is **fresh, event-aware, and identity-clean** — continuously. The data plan below is organised around that bar, not around individual scrapers.

---

## 2. Product surface inventory (design screen → status)

| Screen (design) | Feature | Backend status | Gap |
|---|---|---|---|
| Home (1a) | Search-first funnel, six-capability pitch | ✅ `/v1/search` exists | Copy claims need correcting (see §7) |
| Boat profile — teaser & paywall (1g) | Free teaser + partial recommendations table + $99 CTA | ✅ mostly exists | Reveal partial rec table (top 3 moves) in free tier |
| Boat profile — Optimisation tab (1b) | Report as a **living page**: recs, RAI, rating history, rivals, ORC cross-ref | ⚠️ Report V2 generates once per order | Re-render on data change; page (not PDF) as primary artefact; tabbed profile (Overview / Optimisation / Sail programme / Rivals / History / Certificates) |
| Boat profile — Sail programme (1h) | Ranked purchases with cost + ΔTCC + feasibility | ⚠️ Engine 4 gives ΔTCC + feasibility | **Cost data** (indicative class sailmaker pricing) — new static dataset |
| Programme tier (1i) | Multi-boat portfolio, opportunity scores, CSV export, priority requests | ❌ | Accounts, portfolios, roll-up analytics, export |
| Ratings explorer (1c) | Filterable rating book, TCC distribution chart | ⚠️ data exists (`tcc_snapshots`) | Public explorer endpoint + pages |
| Fleet explorer (1d) | Design class cards, drift banner, 2-way compare | ⚠️ Engines 2 & 5 + matviews exist | Public pages; `design_classes` hygiene is the blocker (§4.1) |
| Pricing (1e) | 3 tiers + single report | ❌ | Stripe subscriptions (today: one-off Checkout only) |
| Member dashboard (1f) | Race-prep brief, analyst, what-if simulator, **watch feed** | ❌ mostly | See §3 — two genuinely new data products |
| Auth (1j) | Accounts, Google OAuth | ❌ | No accounts exist today (email-at-checkout only) |
| Checkout / confirmed / owned (1k–1l) | $99 flow | ✅ exists | "Already owned" state, credit-to-Skipper logic |
| Mobile (3a–3d) | Same features, PWA-shaped | ❌ | Responsive/PWA pass after web |
| Admin (2a–2c) | Scrapers / Discovery / Firecrawl | ✅ exists as `/justin` | Restyle only |
| Report (4a–4c) | Dual IRC+ORC report; **honest one-design case** (Cape 31) | ⚠️ V2 close | ORC section needs detail backfill done; one-design detection path (suppress regression when class spread < threshold) |

---

## 3. Feature → data requirements matrix

Four data products power everything. Everything in the design maps onto one of these.

### A. Certificate & rating spine (have: strong IRC-current, weak history, ORC incomplete)
Powers: boat pages, rating book, rating history, optimisation, simulator, drift, ORC cross-ref.

| Need | State |
|---|---|
| Current IRC TCC time series | ✅ daily `tcc_snapshots` |
| Parsed IRC cert measurements | ⚠️ 3,809 certs, **all 2024+**, 62% boat-linked; parser audit vs extend.ai in flight |
| IRC historical (pre-2024) | ❌ 0 certs; wayback TCC harvest + cert probe built but not run at scale. Target from plan: 8,000 certs, ≥80% linked, 12-month horizon |
| ORC certs + VPP detail | ⚠️ ~73% missing GPH/CDL/allowances; draining 500/night (~2 weeks left) |
| ORC in matviews / class stats | ❌ explicitly excluded — needed for dual-rated pages and design comparator |

### B. Results graph (have: deep AU + UK offshore, patchy elsewhere)
Powers: RAI, rivals, head-to-head, performance sections, "31,000 results" claims.

| Need | State |
|---|---|
| AU club racing | ✅ SailSys 30-min cadence (~213k rows) + TopYacht |
| UK/IRL offshore | ✅ RORC 2007–22, SailRaceHQ 2023+, ISORA, Cowes Week, Sydney–Hobart |
| Status integrity | ⚠️ TopYacht + SailRaceHQ DNF backfills **written, not run**; 163 garbage rows to delete; 21k SailSys "finished, no place" |
| Result→boat matching | ⚠️ `rematch-results` hourly, 6 strategies; quality depends on boat dedup (§4.1) |
| Solent/UK club racing, Med, US | ❌ biggest coverage gaps for the target customer (GBR = largest IRC market). YachtScoring scraper built, unscheduled |

### C. Event calendar & entries — **NEW data product**
Powers: race-prep briefs ("31 IRC entries · 3 of your rivals"), watch feed ("SUN FISH entered Myth of Malham"), pre-event content marketing (business plan's #1 use case to nail).

Nothing in the codebase collects **future** events or entry lists today; everything is retrospective results. Required:

- `events` + `event_entries` tables (event, date, venue, course, organiser, entry list with sail no/boat name/TCC, ingested pre-start).
- Entry-list scrapers: RORC/SailRaceHQ entry pages, CYCA/SailSys entrants, Cowes Week entries, major regatta sites. The Firecrawl discovery pipeline is the right chassis — extend `event_discovery` to classify *upcoming* events, not just results pages.
- Entries → boats matching (reuse identity machinery).
- **Forecast**: buy, don't scrape — one marine weather API (e.g. Open-Meteo marine / Meteomatics) keyed on venue + date, fetched at brief-generation time. Cheap, no new scraper.

### D. Change-detection & eventing layer — **NEW derived product**
Powers: watch feed, alerts, "always current" reports, rating-change emails (business plan Phase 2).

The raw signals all exist; there is no eventing layer:

- New/changed TCC per boat → already computable from `tcc_snapshots` (and `mv_tcc_drift`).
- New cert issued / re-certification → cert scraper diffs.
- New results for a followed boat or its rivals → ingestion already logs per-row (`ingest_events`).
- Rival entered an event → from data product C.
- Build: a `boat_events` feed table populated by ingestion hooks + a nightly differ; consumed by dashboard watch feed, email digests, and a "report is stale → re-render" trigger. This one table powers the most visible membership features.

---

## 4. The data plan

### Phase 0 — Fix the mess (2–3 weeks, prerequisite for everything)

The design sells precision ("every answer carries its evidence"). These are the known holes under that claim:

1. **Run the written-but-unrun backfills**: TopYacht DNF fix (680 rows), SailRaceHQ DNF fix + delete 163 empty-name rows.
2. **Boat identity**: apply the 37 Tier-S `- SEC` merges (dry-run already clean), auto-merge Tier-A of the 393 medium clusters, work the Tier-B review CSV. Duplicate boats silently split race histories — this directly corrupts RAI, rivals, and head-to-head, all headline Skipper features.
3. **Design classes**: finish the merge/null/backfill sweep; add the real FK from `boats.design_canonical` → `design_classes` (today it's a text join). Fleet explorer ships on top of this table.
4. **Finish ORC detail drain** (on autopilot, ~2 weeks) then **add ORC to the matviews** — required by design comparator and dual-rated profiles.
5. **Firecrawl parity**: fix boat-name normalisation in the extractor (the identified Cowes/ISORA failure mode), re-run baselines, cut over sources that pass the gate; stop paying double (legacy + Firecrawl in parallel).
6. Decide numbers hygiene: the design copy says "6,000+ boats · 31,000 results"; the DB holds ~260k result rows. Publish real, defensible counts from a single stats endpoint so marketing numbers can't drift from the DB.

### Phase 1 — Depth for the boats that pay (4–8 weeks, parallel tracks)

1. **IRC history reconstruction** (the moat; business plan risk #6): run the wayback TCC harvest + `irc_backfill` orchestrator at scale. Priority order: boats with race results on file → GBR/AUS/IRL fleets → everything else. KPI: % of *active* boats (raced in last 24 months) with ≥3 years of TCC history. Rating-history charts are a headline free-tier feature — this is what makes boat pages worth indexing.
2. **Cert parsing accuracy**: close out the extend.ai audit buckets (spinnaker sym/asym misclassification, SER/nodisp, FL fields). The simulator computes from these fields; wrong inputs = wrong ΔTCC in a paid product.
3. **UK/Solent results coverage**: the target customer is a Solent Sun Fast 3300 owner; SailSys gives us Sydney, not the Solent. Prioritise HRSC/Hamble/Solent series + JOG via the discovery pipeline, and schedule YachtScoring.
4. **Sail programme cost dataset**: small static table (design class × sail type → indicative price band), maintainable by hand, feeds the advisor. Explicitly label indicative.

### Phase 2 — New data products for membership (parallel with membership build)

1. **Events & entries pipeline** (data product C): schema, 3–5 entry-list scrapers for the events our users actually race (RORC calendar, Cowes Week, major Solent series, CYCA), forecast API integration. Gate for launching race-prep briefs.
2. **Eventing layer** (data product D): `boat_events` feed + ingestion hooks + nightly differ; wire to watch feed, email alerts, and report re-render triggers.
3. **Report as living page**: re-render report sections when a boat's `boat_events` shows material change (new cert, new results ≥N, class stats refresh). Cache per section; the V2 orchestrator already fails soft per section, so incremental refresh fits.
4. **One-design detection**: if class TCC spread < threshold or no lever has usable variance, produce the "honest low-headroom" report variant (Kestrel case in the design). This is a product-integrity feature — it prevents the worst review a data product can get.

### Phase 3 — Widen the funnel (business-plan alignment, months out)

1. **ORC-first geographic expansion**: ORC is open and already 32 countries deep here — the Med and Northern Europe fleets become searchable boat pages (SEO surface ~2–3× IRC's). The business plan's "ORC is the easy 80%" logic holds; most of the remaining work is presentation, since ingestion exists.
2. **PHRF (US)** per business plan Phase 4 — only after ORC surface is live.
3. **Owner-supplied certs + boat claiming**: accounts unlock "claim this boat" → upload own certificate PDF → parse through existing pipeline with `source='owner'` provenance. Improves the worst-covered boats precisely for the people who'll pay.
4. **Partnership track** (non-engineering, from business plan): ORC "approved analytics partner" approach first, RORC/YCF after credibility. Keep respecting the IRC constraints already coded in (public listings only, no formula reverse-engineering claims, "not affiliated" footer is already in the design).

---

## 5. Platform work the design demands (not data, but gating)

In dependency order:

1. **Accounts** (email + Google OAuth) — everything member-shaped hangs off this. Buy (Clerk/Auth0 per business plan) rather than build.
2. **Stripe subscriptions** alongside the existing one-off Checkout; $99-credit-to-Skipper logic; "already owned" state.
3. **Boat↔user linkage** (claimed boats, followed boats, Programme portfolios ×10).
4. **Simulator API**: expose Engine-1 class coefficients per design (`GET /v1/designs/{design}/model` → levers, β, R², N, valid ranges). The design's live TCC math is exactly a linear model client-side; always render the "estimate, not an official rating" disclaimer + R²/N provenance.
5. **Public explorer endpoints**: rating book (filter/sort/paginate), fleet/design pages. Mostly SQL over existing tables + matviews.
6. **Analyst**: the design's Q&A with evidence chips is the existing `insights_service` SSE + facts discipline, upgraded to cite (cert number, R², rule year) — the report V2 Facts machinery already produces those.
7. Mobile: responsive pass on the member app; PWA later. No native app.

**Sequencing note:** Phase 0 data work and Accounts/Subscriptions can run in parallel. Race-prep briefs (the most novel feature) should be **last** of the Skipper features to launch, because it's the only one gated on a brand-new scraping pipeline; everything else runs on data we already hold once Phase 0/1 is done.

---

## 6. What NOT to do (deliberate exclusions)

- **No betting/fantasy, insurer, broker products yet** — business plan Year-3 segments; nothing in the design needs them.
- **No GraphQL/Elasticsearch/Redis re-platform** from the business-plan architecture diagram — Postgres + pg_trgm + matviews are serving fine at this scale; revisit at 10× traffic.
- **Don't scrape weather** — API it.
- **Don't attempt live IRC TCC prediction** — the design's simulator is correctly framed as class-regression estimation; keep the "not an official rating" line everywhere (legal posture from business plan §8 depends on it).
- **Don't launch the Programme tier before fleet analytics are real** — it's 10× the scrutiny from professional users (sailmakers) on the exact aggregates most exposed to identity/dedup bugs.

---

## 7. Copy & integrity fixes flagged from the design

- "31,000 race results" vs ~260k rows in DB (and ~21k of those SailSys rows are hollow) — publish real counts via a stats endpoint (§4 Phase 0.6).
- "We track current IRC certificates only" (empty state) contradicts ORC dual-rated features elsewhere — update once ORC surface ships.
- Design admin says "Extractor: Claude Sonnet 4.5"; the code uses Gemini 2.5 — align copy or model choice.
- Pricing in design is USD-first ($290/yr, $99); current funnel is localised GBP/EUR/AUD — pick one anchor currency and keep the localisation.

---

## 8. Success metrics per phase

| Phase | Metric | Target |
|---|---|---|
| 0 | Duplicate-boat clusters outstanding | 0 Tier-S/A; Tier-B triaged |
| 0 | ORC certs with VPP detail | >95% |
| 0 | Firecrawl sources passing parity gate | ≥2 cut over, parallel runs stopped |
| 1 | Active boats with ≥3 yrs TCC history | ≥60% |
| 1 | IRC certs linked to a boat | ≥80% (plan target) |
| 1 | Solent/UK club results coverage | Sun Fast 3300 + J/109 fleets ≥1 season deep |
| 2 | Events with entry lists pre-start | Top 20 GBR/AUS events per season |
| 2 | Report staleness | Re-render within 24h of material change |
| 3 | Indexed boat pages | 20,000+ (business plan Year-3 coverage target) |
