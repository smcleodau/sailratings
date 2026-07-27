# SPEC-05: Human-In-The-Loop Admin UI & Telemetry

## 1. Overview
The Admin Panel is a critical internal tool for managing the SailRatings data platform. It allows internal operators to resolve ambiguous boat identities (where the scraping pipeline isn't confident enough to auto-merge) and monitor the health of the nightly Temporal scraping workflows.

## 2. Design System Requirements
The Admin UI must feel like a natural extension of the core product. It MUST strictly adhere to the "Paper" design system defined in `web/src/app/globals.css`.
- **Layout:** Use a sidebar navigation (`--color-navy` background with `--color-white` text) and a main content area (`--color-cream` or `--color-white`).
- **Typography:** All data grids and confidence scores must use the `data-mono` font class. Headers should use `heading-serif` or `heading-display`.
- **Components:** Tables should have minimal borders (`border-var(--color-border-light)`), and primary action buttons (like "Approve Merge") should use the `--color-brass` accent.

## 3. Core Pages & Features

### 3.1 Identity Resolution Queue (`web/src/app/admin/identities/page.tsx`)
This page handles the "Human-in-the-loop" requirement for Boat Deduplication.
- **Backend API:** `GET /v1/admin/identities/ambiguous` (Returns a list of potential boat matches with a confidence score between `0.4` and `0.8`).
- **UI:** A data table displaying:
  - Source Boat (e.g., SailSys: "WOXI" / "4343")
  - Canonical Match Candidate (e.g., DB: "Wild Oats XI" / "4343")
  - Confidence Score (e.g., `0.75` colored yellow/orange)
- **Actions:** 
  - `POST /v1/admin/identities/merge` -> Approves the merge and assigns the SailSys record to the Canonical DB record.
  - `POST /v1/admin/identities/reject` -> Rejects the merge, forcing the system to treat them as distinct boats forever.

### 3.2 Telemetry & Scraper Health (`web/src/app/admin/telemetry/page.tsx`)
A dashboard monitoring the Nightly Batch Jobs.
- **Backend API:** `GET /v1/admin/telemetry/jobs` (Queries the Temporal Server via its SDK/API to get the status of recent workflows).
- **UI:** A grid of cards showing:
  - Scraper Name (e.g., "SailSys Nightly")
  - Last Run Status (Success / Failed) using `.data-mono` and color-coding (green/red).
  - Duration and Next Scheduled Run.

## 4. Acceptance Criteria
- [ ] Admin pages are built in Next.js and strictly use the "Paper" CSS variables (no default Tailwind colors).
- [ ] The Identity Resolution Queue successfully fetches ambiguous matches and allows an admin to approve or reject them.
- [ ] The Telemetry page successfully reports the status of the Temporal scraping workflows.
