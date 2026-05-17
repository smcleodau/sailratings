# Conversion Flow Redesign — The Pinned Masthead

**Date:** 2026-05-16
**Status:** Spec — pending Stuart's review before plan

## Goal

Make the SailRatings funnel (search → bench → CTA → checkout → report) feel smoother
end-to-end. Today the page jolts when a boat is picked because `scrollIntoView` fires
before the content has loaded, and the CTA is a single cliff at the bottom with no
mid-flow price anchor. The redesign holds the user steady with a pinned masthead and a
persistent CTA rail, while leaning into the streaming as a deliberate "surveyor at
work" moment rather than a wait to be hidden.

## What stays

- The cream-paper "Bench" visual language (radial dot background, brass hairlines, Söhne).
- The §1 prose + seven sealed sections concept — but the `█▓▒` redacted-sketch lines
  are replaced with brass-stamped title strips. The seal *is* the redaction.
- Inline-editable masthead fields (`MastheadField`) for design / year / designer / builder.
- The SSE working-steps stream and §1 prose stream. Same backend, same shape.
- Sticky pill on report page poll for confirmation; existing markdown-reveal pacing.

## What changes

### 1. Pinned masthead during bench

When a boat is picked, the hero shrinks to a 96-px navy strip (not scrolls) and the
masthead slides into a sticky chart-table row directly underneath it. Both stay pinned
to the top of the viewport for the entire bench experience. No `scrollIntoView` call.
No layout jump.

The masthead row contains, in one horizontal line:

```
ARIADNE  ·  GBR 4412R  ·  TCC 1.024  ·  Farr 45 · 2008 · Cookson
```

Inline-editable fields work as today. On viewports < 768 px, the row collapses to two
lines (boat name + TCC on top, design line beneath) and the hero strip shrinks to 64 px.

### 2. Two-column bench: working-log left, prose right

Below the pinned masthead, a single cream panel is the bench. It is split into a
two-column layout from the moment the panel appears:

- Left third: a **Working** log. Charcoal-on-cream, Roboto Mono, brass tick on each
  completed step, a brass spinner on the active step, optional sub-line in lighter
  charcoal for detail (`12,847 finishes indexed`, `8 sister boats located`). Each step
  carries a right-aligned timestamp in `mm:ss.fff` form (the small precision is
  deliberate — it reads as audit, not theatre).
- Right two-thirds: §1 prose ("Where she sits"). Streams in word-by-word at the SSE
  rate. Before any prose arrives, a thin pulsing brass underscore marks where text
  will land.

The log column scrolls *internally* if it exceeds the panel height — the outer
viewport never moves.

**The working log is permanent furniture.** It stays visible even after §1 finishes
streaming. This is deliberate per [[feedback-streaming-pacing]] — the deliberation is
the product, and we don't enumerate or count the steps; they appear, hold, tick,
roll. No "step N of M" anywhere.

On viewports < 768 px the layout becomes single-column: log on top (collapsible after
streaming completes), prose below.

### 3. Sealed sections as a 2-col grid below

Once §1 finishes streaming, seven section cards fade in below the bench panel in a
2-column grid. Each card:

- Brass-stamped title strip (`§2 · Where she leaks time`)
- A one-line dek (the existing `description` field on each `SECTIONS` entry, e.g.
  "How your TCC has moved across every IRC formula revision since you've owned the
  boat.")
- A brass band reading `Sealed pending order · §N of 8`

No `█▓▒` redacted-sketch lines. The seal is sufficient.

### 4. Persistent CTA rail

A sticky charcoal rail at the bottom of the viewport. Appears the moment §1 finishes
streaming (not before — the user must have read something before being asked to buy).
Once it appears, it stays.

Contents, left to right:

- Brass primary button: `Send me the file — {{currency-symbol}}{{price}}` with an
  ArrowRight icon.
- Brass-outline secondary button: `See a sample report (PDF, 14 pp)` — links to a
  pre-rendered sample PDF stored at `/samples/sail-ratings-sample.pdf` (new asset to
  ship).
- Charcoal hairline divider.
- Reassurance line (smaller, cream-tinted text): `60-day re-rate guarantee · UK VAT
  inc.`
- Optional far-right pill (only if data available): `5,200 certificates analysed · N
  reports issued this quarter` — placeholder copy; can be wired to a real count from
  the `orders` table on a future iteration.

The rail eats 72 px of vertical space when present.

### 5. Report payoff moment

The current report page (`/report/[token]`) shows the same pinned masthead, identical
position and component. Zero visual jump when the user lands from Stripe.

Below the masthead, a single brass line in Roboto Mono: `File compiled. Opening…` for
600 ms. Then the cream paper expands to full width and the **recommendation table
draws first**, top-down, brass rule above it. *After* the table is fully visible, the
prose backfills above and below it at the current 280 ms/paragraph rate.

Rationale: the buyer paid for the answer. Show the answer first; the prose justifies
it. This inverts the current behaviour (prose then table).

The RAI card and rivals table follow the table-first pattern: they draw immediately
once the recommendation table is done, then the prose finishes around them.

Polling (`/report/[token]/page.tsx` line 41) and the survey component stay as-is.

## Out of scope

- Mobile-specific deep redesign — the spec calls out responsive collapse points but
  doesn't redesign the mobile experience from scratch. If Stuart wants to push on
  mobile after this lands, separate spec.
- Stripe checkout page itself — we redirect to Stripe's hosted checkout, so the only
  changes are pre- and post-checkout on our side.
- Backend changes — the SSE step shape, the report generation, the data model. None
  of this requires touching `api/`.
- The dead `PurchaseCTA.tsx` component — delete it as part of the cleanup pass (task
  #5), but it's not on the new flow's critical path.

## Component touchpoints

- `web/src/app/page.tsx` — remove `scrollIntoView` effect; restructure to render the
  pinned hero-strip + masthead-row + bench layout. Pass boat detail directly.
- `web/src/components/TeaserAnalysis.tsx` — split the existing single-column bench
  into two-column working-log + prose. Replace the sealed-sections section to use
  brass strips instead of `█▓▒`. Remove the inline navy CTA block + bottom-right
  sticky pill (replaced by the new persistent rail). Remove the `RedactedSketch`
  component entirely.
- New component: `web/src/components/PinnedMasthead.tsx` — extracts the masthead +
  hero-strip + working-log so it can be reused on the report page.
- New component: `web/src/components/StickyCheckoutRail.tsx` — the persistent
  charcoal rail with price + sample + guarantee.
- New component: `web/src/components/SealedSectionGrid.tsx` — the 2-col grid of
  brass-stamped section cards.
- `web/src/components/ReportView.tsx` — invert the render order so the
  recommendations table draws first, then the prose backfills. Mount the pinned
  masthead at the top.
- `web/src/components/PurchaseCTA.tsx` — delete (dead code).
- New static asset: `web/public/samples/sail-ratings-sample.pdf` — a 14-page sample
  report PDF. (Stuart to provide or generate from a known boat.)
- `web/tests/screenshot-bench.mjs` — Playwright test that picks a boat, waits for
  pinned masthead to stick, waits for §1 to stream, screenshots the bench at three
  beats: stream-in-progress, sealed-sections-visible, post-scroll.

## Risk and trade-offs

- **Vertical real estate cost.** Sticky hero-strip (96 px) + masthead row (~56 px) +
  CTA rail (72 px) = ~224 px of chrome. On a 13" MacBook Air viewport (~720 px usable
  height) that leaves ~496 px for the actual bench panel. Acceptable but tight; we
  should verify with Playwright at 1280×800 and 1440×900 before merging.
- **Sample PDF is a new asset to ship.** Without it the "See a sample report" link
  has nothing to point at. We can launch with just the primary button if the sample
  isn't ready and add the secondary later — easy to gate.
- **The dek copy on each section card needs editing pass.** Current `description`
  strings on `SECTIONS` were written for the redacted-sketch context; some need
  rewrites for the new context. Dispatch `sailing-cta-writer` for this.

## Verification

Per [[feedback-testing]] — no claim of "ready" without Playwright screenshots. The
test plan:

1. Build the frontend, restart on port 4200.
2. Run `web/tests/screenshot-bench.mjs` to capture three beats (stream in progress,
   sealed sections visible, after scroll).
3. Run an updated `web/tests/screenshot-search.mjs` to confirm the search → bench
   transition is jump-free.
4. Run a new `web/tests/screenshot-report.mjs` to capture the report page payoff
   (table-first, then prose backfilling).
5. Visually inspect each screenshot. Fix anything that doesn't match the spec. Only
   then say it's ready.

## Open questions for Stuart

- The sample PDF — do you have one to drop in, or should I generate one from a known
  boat (e.g. Ariadne or whatever the demo boat is) and we treat that as a follow-up?
- Reassurance line copy — `60-day re-rate guarantee` is invented; if you don't
  actually offer a guarantee, swap to something true (`Delivered as a PDF in under a
  minute` / `One certificate, one report` / etc).
- Sample PDF link target — `/samples/sail-ratings-sample.pdf` is a placeholder.

Once you nod on the spec I'll invoke `writing-plans` to break this into the
implementation plan, then dispatch frontend-design to execute with sailing-cta-writer
on the copy.
