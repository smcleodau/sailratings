# SPEC-01: Human-In-The-Loop Admin UI & Telemetry

## 1. Overview
This specification details the implementation for the "Human-in-the-Loop" (HITL) identity resolution and telemetry dashboard. 
Currently, identity matching (e.g. associating "Wild Oats XI" from an ORC cert to "WOXI" from a race result) is automated, but matches with a confidence score between `0.4` and `0.8` require manual review. 
Additionally, we need full OpenTelemetry integration to observe the OpenHands autonomous agents running inside our Temporal cluster.

The Admin UI will be built as an extension to the existing Next.js App Router in `web/src/app/admin/`, utilizing the "Paper" design system, and the backend routes will extend the existing FastAPI endpoints in `api/src/irc_data/api/routers/admin.py` and `admin_tables.py`.

## 2. File and Component Structure Modifications

### 2.1. Frontend Next.js (App Router)
The current `web/src/app/admin/page.tsx` is a monolithic 1100+ line file housing an SSE-based "Admin Chat" interface.
We will preserve this chat interface but restructure the admin shell to support sub-routes:

*   **`web/src/app/admin/layout.tsx` (Update):** Ensure the `ConversationSidebar` and navigation shell can correctly host nested child pages (e.g. `/admin/identity` and `/admin/telemetry`).
*   **`web/src/app/admin/identity/page.tsx` (New):** A new Next.js route dedicated to the Ambiguity Resolution Queue. It will fetch pending matches from the backend.
*   **`web/src/app/admin/identity/MatchCard.tsx` (New):** A visual component displaying the two conflicting identity records side-by-side with "Merge" and "Keep Separate" action buttons.
*   **`web/src/app/admin/telemetry/page.tsx` (New):** A dashboard displaying OpenTelemetry logs ingested from the OpenHands agents running in the Temporal Orchestrator. 

### 2.2. Backend FastAPI
*   **`api/src/irc_data/api/routers/admin.py` (Update):** Add new endpoints:
    *   `GET /admin/identity/pending`: Fetch matches where `0.4 <= confidence <= 0.8`.
    *   `POST /admin/identity/resolve`: Accept a resolution (`MERGE` or `SEPARATE`) and update the `IdentityMatch` database tables.
*   **`api/src/irc_data/temporal/orchestrator/observability.py` (Update):**
    *   Refactor the barebones `OpenHandsObservability` class to initialize the OpenTelemetry `SpanManager` and export traces to a configured `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`.

## 3. Infrastructure & Telemetry Setup
*   **TimescaleDB / OTEL Collector:** The Docker Compose stack will be updated to include an OpenTelemetry Collector. The collector will receive traces from `observability.py` and export them into the existing Postgres database (or a dedicated APM like SigNoz if chosen).
*   **Temporal Context:** The `SprintManagerWorkflow` and `TaskExecutionWorkflow` will inject the trace IDs so the Admin UI can correlate a specific Notion Issue to the exact bash commands an agent ran.

## 4. Acceptance Criteria
1.  Admin users navigating to `/admin/identity` can view a list of ambiguous matches.
2.  Clicking "Merge" executes an API call that successfully unifies the two canonical records in the backend database.
3.  The Temporal OpenHands agents emit OTEL traces, which can be viewed in the `/admin/telemetry` dashboard.
