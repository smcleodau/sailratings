# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also: `../CLAUDE.md` for environment, deployment, and cross-project context.

## Project overview

Next.js frontend for SailRatings. A single-page conversion funnel: search for a boat, see a free AI-generated teaser analysis (streamed via SSE), purchase a premium report ($99 via Stripe), view the full report.

## Commands

```bash
npm run dev          # dev server on :3000 (hot reload)
npm run build        # production build (standalone output for Docker)
npm run start        # production server
npm run lint         # ESLint with next/core-web-vitals + typescript rules
```

After changes on dev server: `npm run build` then restart the Next.js process.

## Architecture

### Pages

- **`/`** (`src/app/page.tsx`) — Landing page. Hero with search autocomplete → boat card → streaming teaser → purchase CTA. This is the main page.
- **`/report/[token]`** (`src/app/report/[token]/page.tsx`) — Premium report view. Polls API until report is ready, displays analysis + recommendations table + RAI + rivals. Has PDF download link.
- **`/brand/[a-e]`** — A/B test pages for different hero images/messaging.
- **`/justin`** — Admin page, gated by `NEXT_PUBLIC_ENABLE_ADMIN=true` env var (middleware rewrites to `/` if disabled).

### Components

```
src/components/
├── Hero.tsx             # Hero section with background image + title
├── SearchBar.tsx        # Autocomplete input, debounced (250ms), calls GET /search
├── BoatCard.tsx         # Boat specs display (name, sail #, TCC, design, dimensions)
├── TeaserAnalysis.tsx   # Streams free analysis via SSE (POST /insights/ask, detail_level: "free")
├── PurchaseCTA.tsx      # Feature list + localized pricing + Stripe checkout button
├── ReportView.tsx       # Full premium report (markdown prose, recommendations, RAI, rivals)
├── ReportSurvey.tsx     # Post-purchase feedback form (score, newsletter, user type)
└── PageFlow.tsx         # Layout orchestrator
```

### API client

`src/lib/api.ts` — All backend communication. Base URL from `NEXT_PUBLIC_API_BASE` env var (baked at build time).

Key functions: `searchBoats()`, `getBoat()`, `streamInsights()` (async generator yielding SSE events), `createCheckoutSession()`, `getReport()`, `getReportPdfUrl()`, `submitSurvey()`

All pages use `"use client"` — fully client-rendered for interactive search and SSE streaming. SSE is consumed via `fetch().body.getReader()`.

### Styling

Tailwind CSS v4 with PostCSS. Pure Tailwind classes, no CSS modules.

Design tokens in `tailwind.config.ts`: navy (`#0A2240`), brass (`#C29B61`), cream (`#F4F1E8`), charcoal (`#2C2C2C`).

Typography: Söhne font family (self-hosted in `public/fonts/`, 8 weights). Data/numbers use Roboto Mono. Custom CSS classes in `globals.css`: `.heading-display`, `.body-text`, `.data-mono`, `.brand-wordmark`.

Aesthetic is premium/restrained — no emoji, no startup vibes. Targeting affluent yacht owners.

### Config files

- `next.config.ts` — `output: "standalone"` for Docker
- `tailwind.config.ts` — custom colors, fonts
- `.env.local` — `NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_ENABLE_ADMIN`
- `Dockerfile` — multi-stage build (node:22-alpine), serves on port 4200
- `src/middleware.ts` — blocks `/justin` unless admin enabled

### Utilities

`src/lib/currency.ts` — maps browser locale to currency code for localized Stripe pricing.
