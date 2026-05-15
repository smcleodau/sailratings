# Frontend Brief: Boat Rating Optimisation Report — Landing + Purchase Flow

## The Product

A single-page funnel that gets a boat owner to type their boat name, see a free teaser analysis that hooks them, and pay $99 (localised currency) for the full optimisation report.

That's it. One flow. Search → teaser → pay → report.

## The Funnel

### Step 1: Search

A single search field, centre of page. Prominent. No clutter around it.

Headline above the search: **"What is your rating really costing you?"**

Subline: *"We've analysed 6,000+ IRC boats, 31,000 race results, and thousands of certificates. Find your boat and see what we found."*

- Autocomplete as the user types, hitting `GET /v1/search?q={input}`
- Results appear as a dropdown: **boat name**, sail number, design, country flag, current TCC
- User clicks their boat → scroll down or navigate to the teaser section

**API:** `GET /v1/search?q=CHILLI` returns `[{id: 12067, boat_name: "CHILLI PEPPER", sail_number: "GBR1663R", design: "Sunfast 3300", country: "GBR", current_tcc: 1.026}]`

### Step 2: Teaser (the hook)

Once a boat is selected, show:

**Boat identity card** at the top:
- Boat name (large), sail number, design class, country flag
- Current TCC displayed as a big number
- Fleet position: "X of Y in the {design} fleet" (from the boat detail endpoint)

**Free insight** — streams in below the identity card:
- Call `POST /v1/insights/ask` with `{"boat_id": <id>, "question": "Analyse this boat", "detail_level": "free"}`
- This returns an SSE stream. Display text as it arrives (typewriter effect or just appending).
- SSE format: each line is `data: {"type": "text", "data": "chunk of text"}` followed by `data: {"type": "done", ...}`
- The teaser is ~150 words — 2-3 punchy paragraphs from a professional rating advisor. It names specific findings, mentions actual numbers and points, and deliberately leaves the owner wanting more.

**This teaser is the entire conversion mechanism.** It must feel like someone who really knows their boat just told them something they didn't know. The LLM does this well — the system prompt is tuned for it.

### Step 3: Call to Action

Immediately below the teaser, no scrolling:

**"Get Your Full Rating Report"**

Price: **$99** (or local equivalent — detect from browser locale or IP: £79, €89, A$149, etc.)

What's included (short bullets):
- Ranked recommendations with estimated rating points to save
- Your measurements vs class leaders, lever by lever
- How the IRC rule trend affects your specific setup
- Head-to-head record against every boat you've raced
- Trial certificate suggestions to test before committing
- Expert AI analysis written by a professional rating advisor

A single "Buy Report" button. Stripe Checkout or similar — email + card, nothing else.

### Step 4: Report Delivery

After payment, show the full report on the same page (or a dedicated URL emailed to them).

- Call `POST /v1/insights/ask` with `{"boat_id": <id>, "question": "Full optimisation report. Where am I giving away points and what should I change first?", "detail_level": "premium"}`
- Stream the full 800-1000 word analysis
- Below the streamed text, show structured data cards from the analytics endpoints:

**Recommendations table** — from `GET /v1/analytics/boats/{id}/optimize`
Each row: what to change, category (declaration/sail/structural), current value, target, estimated points saved, feasibility, evidence strength

**Rivals** — from `GET /v1/analytics/boats/{id}/rivals`
Table: rival name, sail number, wins, losses, win rate, events together

**Racing performance** — from `GET /v1/analytics/boats/{id}/rai`
Card: RAI score, races analysed, wins, podiums, confidence interval

**Formula trend** — from `GET /v1/analytics/designs/{design}/drift`
Summary: how the rule has shifted, which dimensions changed, how this boat's configuration is affected

The report should also be downloadable/printable as a clean PDF.

## Design

**Audience**: Yacht racing owners. Affluent, 35-65, technically sharp, time-poor. They race weekends and midweek, they know what TCC means, they don't need it explained. They'll pay $99 without blinking if the teaser is good.

**Tone**: Premium, restrained, authoritative. This is a consultancy, not a startup. No "AI-powered" badges, no chatbot UI, no emoji. The analysis speaks for itself.

**Visual direction**:
- Dark navy background (#1a1f36) with white text for the hero/search section
- White background for the teaser and report sections
- Gold/amber (#c9a94e) for the CTA button and key numbers
- Serif typeface for the boat name and headings (authority)
- Clean sans-serif for body text
- Monospace/tabular figures for TCC numbers and tables
- Generous whitespace. Let the data breathe.

**One page, not a web app.** This is a landing page with a purchase flow, not a dashboard. No navigation bar with 8 links. No signup wall before the teaser. No account creation. Email at checkout is the only information collected.

## Technical Notes

- SSE streaming: use `EventSource` or `fetch` with a `ReadableStream` to consume the `/insights/ask` endpoint
- The teaser endpoint returns ~150 words in 3-5 seconds. Show a subtle loading state (pulsing dot, not a spinner) while it generates.
- Stripe Checkout handles currency localisation — pass the locale and Stripe shows the right currency
- For the PDF export: render the report HTML server-side and convert, or use a client-side library
- SEO: the search page should work without JS for Google. Individual boat pages can be pre-rendered if you want organic traffic (e.g., "/boats/GBR1663R" ranking for "CHILLI PEPPER IRC rating")
- The API has CORS enabled for all origins

## API Quick Reference

```
# Search (autocomplete)
GET /v1/search?q=CHILLI
→ [{id, boat_name, sail_number, design, country, current_tcc}]

# Boat detail (identity card)
GET /v1/boats/12067
→ {id, boat_name, sail_number, design, country, year_built, dimensions, latest_rating, ...}

# Free teaser (SSE stream)
POST /v1/insights/ask
{"boat_id": 12067, "question": "Analyse this boat", "detail_level": "free"}
→ SSE: data: {"type": "text", "data": "chunk..."} ... data: {"type": "done", ...}

# Premium report (SSE stream, after payment)
POST /v1/insights/ask
{"boat_id": 12067, "question": "Full optimisation report", "detail_level": "premium"}
→ SSE: same format, longer response

# Structured analytics (for data cards in the report)
GET /v1/analytics/boats/12067/optimize    → recommendations
GET /v1/analytics/boats/12067/rai         → racing performance
GET /v1/analytics/boats/12067/rivals      → head-to-head records
GET /v1/analytics/designs/Sunfast%203300/drift  → formula trend
```
