# SPEC-17: The "Product Manager" Spec Generation Engine

## 1. Overview
Currently, Epics are broken down into granular issues manually, and `SPEC` documents are written by hand. To fully automate product development, we need an "AI Product Manager" that can take a high-level user goal, research the codebase, and generate the architectural blueprints for the worker agents.

## 2. Architecture

### 2.1 Trigger
- A human adds a new Epic to the Notion database with the status `Draft` and a short description (e.g., "We need a leaderboard showing the top 10 boats by RAI score.").
- The `notion_poller.py` detects the `Draft` status and triggers the `SpecGenerationWorkflow`.

### 2.2 `SpecGenerationWorkflow`
- Spawns an OpenHands agent with read-only access to the repository and the persona of an Expert Product Manager.
- **Task 1 (Research):** The agent explores the codebase (e.g., checks the DB schema to see how RAI scores are currently stored).
- **Task 2 (Draft Spec):** The agent authors `docs/specs/SPEC-XX-Leaderboard.md` detailing the exact UI components, database queries, and API endpoints required.
- **Task 3 (Breakdown):** The agent uses the `BoardOperator` API to create granular sub-issues under the Epic (e.g., Issue 1: DB Query, Issue 2: API Endpoint, Issue 3: Next.js UI).
  - **CRITICAL (Inline Links):** The agent MUST embed inline markdown links pointing to the spec file (`docs/specs/SPEC-XX.md`) within the description body of the issue it creates. This ensures the Lane Worker knows exactly where the architectural blueprint lives.
- **Task 4 (Dependencies):** The agent correctly links the issues using the `Blocked By` property (e.g., the UI is blocked by the API) to form a DAG.

### 2.3 Handoff
- The agent changes the status of the granular issues to `Ready for Agent`, passing the baton to the worker agents.

## 3. Acceptance Criteria
- [ ] The `SpecGenerationWorkflow` successfully turns a one-sentence Epic into a detailed Markdown `SPEC` document.
- [ ] The agent successfully creates granular board issues with correct `Blocked By` DAG relationships.
- [ ] The generated issues explicitly contain inline markdown links to the `.md` specification files.
