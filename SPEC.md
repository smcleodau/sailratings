# SailingRatings.com — Product Spec

## The Product

Owners of IRC-rated racing yachts search for their boat, get a free 150-word teaser analysis that hooks them with specific insights about their rating, then pay $99 (localised to their currency) for the full optimisation report.

One flow: search → teaser → pay → report. Not a dashboard. Not a web app. A landing page with a purchase flow.

## The API

Backend: https://api.sailingratings.com
OpenAPI docs: https://api.sailingratings.com/v1/docs
CORS: enabled for all origins.

### Endpoints

**Search (autocomplete)**
```
GET /v1/search?q={input}
→ {"results": [{"id": 12067, "boat_name": "CHILLI PEPPER", "sail_number": "GBR1663R", "design": "Sunfast 3300", "country": "GBR", "current_tcc": 1.026}, ...]}
```

**Boat detail**
```
GET /v1/boats/{id}
→ {id, boat_name, sail_number, design, country, year_built, dimensions, latest_rating: {tcc, non_spi_tcc, crew, ...}, total_results, total_certificates, ...}
```

**Free teaser (SSE stream)**
```
POST /v1/insights/ask
Content-Type: application/json
{"boat_id": 12067, "question": "Analyse this boat", "detail_level": "free"}
→ SSE stream: data: {"type": "text", "data": "..."} ... data: {"type": "done", ...}
```
~150 words, 3-5 seconds to stream.

**Premium report (SSE stream — after payment)**
```
POST /v1/insights/ask
{"boat_id": 12067, "question": "Full optimisation report. Where am I giving away rating and what should I change first?", "detail_level": "premium"}
→ Same SSE format, ~800-1000 words
```

**Structured analytics**
```
GET /v1/analytics/boats/{id}/optimize → recommendations table
GET /v1/analytics/boats/{id}/rai → racing performance index
GET /v1/analytics/boats/{id}/rivals → head-to-head rival data
```

## The Flow

### 1. Landing — The Search
- Headline: "What is your rating really costing you?"
- Subline: "We've analysed 6,000+ IRC boats, 31,000 race results, and thousands of certificates to find where your points are hiding."
- Search input with autocomplete (debounced /v1/search)
- Dropdown: boat name, sail number, design, country flag

### 2. Teaser — The Hook
- Identity card: boat name, sail number, design, country, current TCC
- Streaming teaser via SSE (free insights endpoint)
- Typewriter/append display with subtle pulse indicator

### 3. CTA — The Purchase
- "Get Your Full Rating Report" — $99
- Stripe Checkout with currency localisation (USD $99, GBP £79, EUR €89, AUD $149)
- Bullets: recommendations, measurements vs class, rule changes, head-to-head, trial certs
- Single button → Stripe Checkout, email only

### 4. Report — After Payment
- Streaming premium analysis (~800-1000 words)
- Data cards (parallel API calls):
  - Recommendations table (optimize endpoint)
  - Racing Performance / RAI (rai endpoint)
  - Rivals table (rivals endpoint)
- Printable / PDF exportable
- Email report link to customer

## Design

- Audience: Yacht racing owners, 35-65, affluent, technically sharp
- Tone: Premium, restrained, authoritative. Consultancy, not startup.
- No "AI-powered" badges. No chatbot bubbles. No emoji.
- Hero/search: Image background with white text (mesa.dev style)
- Below: warm cream/linen background (#FAF6F0)
- Accent: Gold/amber or copper for CTA, key numbers
- Serif for headings, clean sans for body, monospace for data
- Page border frame with corner brackets
- Photo: hero.jpg (4-sails racing yacht, vintage filter)

## Tech

- Next.js (SSR, SEO — boat pages indexable)
- SSE streaming via fetch + ReadableStream
- Stripe Checkout for payment
- Deploy to Cloudflare Pages or Vercel
- Domain: sailingratings.com

## Test Data

- "CHILLI PEPPER" (id 12067) — Sunfast 3300, GBR
- "SUN FISH" (id 12330) — Sunfast 3300, sail 3375
- "DIABLO-J" (GBR9205R) — 200+ race results
- "FOGGY DEW" (Sm1808) — Sunfast 3300, 155 results
