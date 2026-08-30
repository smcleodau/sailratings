# SPEC-11: Sailing Intelligence Reports (New UI)

## 1. Overview
The Living Report is the core product of the SailRatings platform. It displays live intelligence on a specific boat, combining its design class data, active certificates (IRC/ORC), and real-time event feeds. This specification details the frontend component architecture, styling rules (using the "Paper" design system), and the expected backend data payloads.

## 2. Design System Application ("Paper")

All components must adhere strictly to the `globals.css` design system. DO NOT use generic Tailwind colors (e.g. `bg-blue-500`, `text-red-500`).

### 2.1 CSS Variables & Palette
- **Backgrounds:** `--color-white`, `--color-cream` (for panels), `--color-navy` (for inverted/hero sections).
- **Text:** `--color-charcoal` (primary), `--color-charcoal-light` (secondary), `--color-slate` (muted).
- **Accents:** `--color-brass` (primary interaction, highlights, dividers), `--color-brass-dark` (hover states).
- **Borders:** `--color-border`, `--color-border-light`.

### 2.2 Typography
- **Headings:** Use the `heading-display` or `heading-serif` classes (utilizing the *Soehne* font family).
- **Data/Numbers:** All rating numbers (e.g. `1.345`) and dates MUST use the `data-mono` class (*Roboto Mono*) with `font-feature-settings: "tnum"`.
- **Body:** Use `body-text` class.

### 2.3 Animations & Micro-interactions
- **Entry:** Components should mount using `.animate-in` or the staggered `.delay-X` classes.
- **Section Dividers:** Use the `.section-divider` class (which features the 45-degree rotated brass diamond) to separate major components within the report.

---

## 3. Component Architecture

The report UI will live at `web/src/app/boats/[boat_id]/page.tsx` and consist of the following React component tree:

### `LivingReportContainer.tsx`
The main wrapper component that fetches the boat data and coordinates the layout.
- **Layout:** Standard max-width container (`max-w-5xl mx-auto`).
- **Loading State:** Must use a skeleton loader that mimics the final layout, using the `.streaming-pulse` animation.

### `HeroHeader.tsx`
Displays the primary boat identity.
- **Background:** `--color-navy` with `--color-white` text.
- **Content:** Boat Name (`heading-display`), Sail Number (`data-mono`), and Canonical Design Class.
- **Accent:** A single `.drafting-needle` animation sitting beneath the boat name to imply live data monitoring.

### `CertificateCard.tsx`
Displays the current rating data.
- **Container:** A card utilizing the `.dossier-paper` background grain.
- **Data Points:**
  - Rating Type (e.g., "IRC")
  - TCC Value (e.g., "1.452") - Must use `.data-mono` and be styled significantly larger than surrounding text.
  - Issue Date / Expiry Date.
- **Animation:** Mount using the `.paper-land` and `.wax-band-in` sequence to look like a physical certificate being placed on a desk.

### `EventFeed.tsx`
A timeline list of recent events or scraped race results.
- **Layout:** Vertical timeline with a left border (`border-l border-var(--color-border)`).
- **Items:** Each event displays the Date (`data-mono`), Event Name (`heading-serif`), and Result/Status.
- **Animation:** Each item mounts using `.thinking-step` to fade in sequentially from the left.

---

## 4. API Integration (Backend Payloads)

The UI components will fetch their data from the FastAPI backend. The expected JSON payload from `GET /v1/boats/{boat_id}/report` is:

```json
{
  "boat_id": "uuid-string",
  "identity": {
    "name": "WILD OATS XI",
    "sail_number": "4343",
    "design_class": "Reichel Pugh 100"
  },
  "active_certificate": {
    "type": "IRC",
    "tcc": 1.954,
    "issue_date": "2026-01-15",
    "expiry_date": "2026-12-31",
    "status": "valid"
  },
  "recent_events": [
    {
      "date": "2025-12-26",
      "event_name": "Rolex Sydney Hobart Yacht Race",
      "result": "Line Honours",
      "source": "SailSys"
    }
  ]
}
```

## 5. Acceptance Criteria
- [ ] `page.tsx` is implemented and securely fetches data from the backend.
- [ ] `HeroHeader` correctly displays the boat identity on a Navy background.
- [ ] `CertificateCard` utilizes the `.paper-land` animation and renders the TCC in the `data-mono` font.
- [ ] `EventFeed` renders a left-bordered timeline with `.thinking-step` staggered animations.
- [ ] NO default Tailwind colors (blue-500, gray-200) are used; only `var(--color-*)` custom properties from `globals.css`.
- [ ] The loading state uses `.streaming-pulse`.
