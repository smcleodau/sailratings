# SPEC-05-Human-In-The-Loop-Admin

## 1. System Architecture

The Human-In-The-Loop (HITL) Admin Portal is a specialized Next.js sub-application designed to interface securely with the Temporal backend and the core Postgres database.

### Core Components
- **Admin Frontend (Next.js):** Hosted under a protected `/admin/*` route within the main Next.js app. Utilizes the "Paper" design system for consistent styling.
- **Admin API (FastAPI):** A set of secure endpoints in the `irc_data` backend that handles authentication checks and exposes internal state.
- **Temporal Client Integration:** The backend API will use the `temporalio` client to query workflow states (e.g. Scraper health, Dead Letter Queue metrics) and send `Signals` to unblock suspended orchestrator tasks.
- **Identity Resolution Engine:** A backend module that queries the `Boat` and `Entity` tables for records flagged with an ambiguity confidence score (0.4-0.8).

## 2. Data Models / Schemas

### `IdentityMatchQueue`
A new table (or materialized view) to track pending ambiguous matches.
- `id`: UUID (Primary Key)
- `source_record_a_id`: UUID (Foreign Key to Boat/Entity)
- `source_record_b_id`: UUID (Foreign Key to Boat/Entity)
- `confidence_score`: Float (e.g., 0.65)
- `match_reasons`: JSONB (e.g., `{"name_similarity": 0.9, "sail_number_match": false}`)
- `status`: Enum (`PENDING`, `MERGED`, `REJECTED`)
- `resolved_by`: UUID (Foreign Key to Admin User, nullable)
- `resolved_at`: Timestamp (nullable)

### `TemporalTaskDLQ` (Virtual / API Schema)
Exposed via API from Temporal's task queue metrics.
- `workflow_id`: String
- `run_id`: String
- `error_type`: String (e.g., `BudgetExceeded`, `PlaywrightTestFailed`)
- `last_attempt_time`: Timestamp

## 3. API Endpoints & State Machine Integration

### Identity Resolution Endpoints
- `GET /api/v1/admin/resolution-queue`: Returns a paginated list of `IdentityMatchQueue` records with status `PENDING`.
- `POST /api/v1/admin/resolution-queue/{id}/merge`: Merges `source_record_b` into `source_record_a`, updates foreign keys, and marks queue status as `MERGED`.
- `POST /api/v1/admin/resolution-queue/{id}/reject`: Marks the records as distinct and sets queue status to `REJECTED`.

### Pipeline Monitoring Endpoints
- `GET /api/v1/admin/metrics/scrapers`: Returns health checks, recent run times, and success/fail counts for all registered Temporal scrape workflows.
- `GET /api/v1/admin/metrics/dlq`: Returns suspended workflows in the Dead Letter Queue.
- `POST /api/v1/admin/workflows/{workflow_id}/signal`: Sends a `human_intervention_signal` to Temporal to retry or unblock a suspended task.

## 4. Acceptance Criteria

1. **Authentication & Security:** 
   - The `/admin` route requires a valid Clerk/Stripe session with the `ADMIN` role. Unauthorized access redirects to `/`.
2. **Ambiguity Resolution Dashboard:**
   - Displays a side-by-side comparison UI for two records, highlighting differing fields (e.g., slightly different boat names or hull colors).
   - "Merge" and "Keep Separate" buttons trigger the respective API endpoints and instantly remove the item from the queue UI upon success.
3. **Pipeline Monitoring Dashboard:**
   - Visual indicators (e.g., Green/Red status lights) for each active scraper.
   - A dedicated DLQ table showing tasks that OpenHands agents or Scrapers failed to complete.
   - An "Unblock/Retry" action that successfully signals Temporal to resume a suspended workflow.
4. **Design System:**
   - Fully implemented using the "Paper" design system tokens (`#F3F1EC` bg, `#0C5F5C` primary, `#C92B12` errors).
