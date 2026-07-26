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

## 4. Component Design (Next.js & Paper Design System)

The UI must strictly adhere to the "Paper" design system tokens:
- **Background**: `#F3F1EC` (Paper Off-White)
- **Primary**: `#0C5F5C` (Deep Sea Green)
- **Secondary**: `#C92B12` (Warning Red / Urgent Actions)
- **Typography**: Inter/Outfit with heavy use of subtle borders (`#E5E0D8`) and drop shadows (`0 4px 6px rgba(0,0,0,0.05)`).

### `ResolutionDashboard`
The main view for resolving ambiguity.
- Layout: 2-column CSS Grid.
- Left Panel: Queue of pending matches (Infinite scroll).
- Right Panel: `ComparisonView` of the selected match.

### `ComparisonView`
A side-by-side diff component.
- Visuals: Highlights differing fields in soft yellow (`#FFF9C4`).
- Interactions: Keyboard shortcuts (Arrow Keys to navigate, `M` for Merge, `S` for Separate).
- Feedback: Optimistic UI updates to remove the card from the list instantly while the API resolves in the background.

## 5. Engineering Breakdown & Issues

This Epic is broken down into the following granular issues (to be added to the Notion Issues Database). Each issue handles a specific architectural layer of the Human-In-The-Loop system.

*   **[ISSUE-05-01] Identity Match Table Migration**: Create the Alembic/AeroSQL migration for the `IdentityMatchQueue` table in PostgreSQL.
*   **[ISSUE-05-02] FastAPI Resolution Endpoints**: Implement `GET /resolution-queue`, `POST /merge`, and `POST /reject` endpoints. Include Clerk JWT middleware validation for ADMIN role.
*   **[ISSUE-05-03] Next.js Admin Layout & Routing**: Setup the `/admin` protected route group layout. Implement Clerk `<Protect role="admin">` wrapper.
*   **[ISSUE-05-04] Paper Design System UI Shell**: Build the base Admin Navigation Sidebar and Header using the `#0C5F5C` primary color and `#F3F1EC` background.
*   **[ISSUE-05-05] ComparisonView React Component**: Build the side-by-side diff UI component for analyzing `IdentityMatchQueue` records.
*   **[ISSUE-05-06] Resolution Dashboard Integration**: Connect the `ComparisonView` to the `GET /resolution-queue` API using React Query or SWR for state management.
*   **[ISSUE-05-07] Keyboard Navigation & Optimistic UI**: Implement the `M`/`S` keyboard shortcuts and optimistic UI updates for the merge/reject actions.
*   **[ISSUE-05-08] Temporal Metrics Endpoints**: Implement FastAPI endpoints connecting to the `temporalio` client to fetch workflow health and DLQ metrics.
*   **[ISSUE-05-09] Temporal Signal Endpoint**: Implement the API endpoint to send a `human_intervention_signal` to blocked Temporal workflows.
*   **[ISSUE-05-10] Pipeline Monitoring Dashboard UI**: Build the React component displaying the visual health of scrapers (Green/Red indicators) and the DLQ table.

## 6. Acceptance Criteria

1. **Authentication & Security:** 
   - The `/admin` route requires a valid Clerk/Stripe session with the `ADMIN` role. Unauthorized access redirects to `/`.
2. **Ambiguity Resolution Dashboard:**
   - Displays a side-by-side comparison UI for two records, highlighting differing fields.
   - "Merge" and "Keep Separate" buttons (and keyboard shortcuts) trigger the respective API endpoints.
3. **Pipeline Monitoring Dashboard:**
   - Visual indicators for each active scraper.
   - A dedicated DLQ table showing suspended tasks.
   - An "Unblock/Retry" action that successfully signals Temporal.
4. **Design System:**
   - Fully implemented using the "Paper" design system tokens.
