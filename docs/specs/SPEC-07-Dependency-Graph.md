# SPEC-07: Dependency-Aware Scheduling

## 1. Overview
Currently, `notion_poller.py` fetches all issues marked "Ready for Agent" and throws them into the Temporal queue simultaneously. This leads to race conditions (e.g., an agent building a frontend component before the backend API is merged). We need the poller to respect a Directed Acyclic Graph (DAG) using Notion's relation properties.

## 2. Technical Implementation

### 2.1 Notion Setup
- Ensure the "Issues" database has a Relation property named `Blocked By` linking to itself.

### 2.2 Updating the Poller (`api/src/irc_data/temporal/orchestrator/notion_poller.py`)
- When querying for "Ready for Agent" issues, the poller must parse the `Blocked By` property array.
- **Evaluation Logic:**
  - If `Blocked By` is empty: The issue is truly unblocked. Dispatch to Temporal.
  - If `Blocked By` is NOT empty: The poller must query the status of every parent issue ID in that array.
  - If ALL parent issues have the status `Done` or `Merged`: Dispatch to Temporal.
  - If ANY parent issue is still `To Do`, `In Progress`, `In Review`, or `Failed`: Skip the issue. Do not dispatch it to Temporal.

### 2.3 Logging
- The poller must log heavily when it skips an issue due to dependencies (e.g., `INFO: Skipping [EPIC-10] Stripe Integration because it is blocked by [EPIC-09] Clerk Auth`).

## 3. Acceptance Criteria
- [ ] `notion_poller.py` successfully reads the `Blocked By` property.
- [ ] Issues with incomplete upstream dependencies are safely ignored.
- [ ] Once an upstream dependency transitions to `Done`, the next run of the poller successfully picks up the unblocked child issue.
