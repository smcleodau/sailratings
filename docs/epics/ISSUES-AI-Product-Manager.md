# ISSUES: EPIC-17 (AI Product Manager / Specification Agent)

The following issues must be ingested into Notion and executed sequentially by the worker agents to build the Specification Agent.

## ISSUE-17.1: Create Notion API Python Wrappers
**Description:** 
The OpenHands Specification Agent needs standard, robust tools to interact with Notion. Create a `notion_tools.py` utility module containing the following functions:
- `create_epic_issue(title: str, description: str, epic_id: str) -> str`: Creates a new issue in Notion linked to an Epic. Returns the new page ID.
- `link_issue_dependency(blocked_issue_id: str, blocking_issue_id: str)`: Updates the `Blocked By` property in Notion to establish the DAG.
- `update_epic_status(epic_id: str, status: str)`: Transitions the Epic from `Draft` to `In Progress` or `Spec Complete`.
**Acceptance Criteria:**
- Functions are strictly typed using `pydantic` or `beartype`.
- Functions handle API rate limits and network errors gracefully.
- Include basic unit tests mocking the Notion API.

## ISSUE-17.2: Build the `query_corporate_memory` OpenHands Tool
**Description:**
The Specification Agent needs memory to architect correctly. Create a custom OpenHands Python tool `query_corporate_memory(query: str) -> str` that connects to the `pgvector` database built in EPIC-19. 
- The tool must embed the input query using the standard embedding model.
- Execute a cosine similarity search against the `agent_transcripts` table.
- Return the top 3 results as formatted markdown strings (including the past lesson, exit codes, and diff context).
**Blocked By:** [Requires completion of EPIC-19 Vector DB setup, but for now can return dummy/mock data until EPIC-19 is complete].

## ISSUE-17.3: Expand `SprintManagerWorkflow` Persona & Prompting
**Description:**
Modify `api/src/irc_data/temporal/orchestrator/activities.py` to upgrade the `run_sprint_manager_agent` activity.
- Update the OpenHands initialization to explicitly inject the newly created `notion_tools` and `query_corporate_memory` tools.
- Overhaul the `system_prompt` to clearly instruct the agent to:
  1. Query corporate memory for architectural rules before writing.
  2. Write the detailed markdown SPEC to `docs/specs/`.
  3. Create atomic engineering issues using `create_epic_issue`.
  4. Link the issues sequentially using `link_issue_dependency`.
**Blocked By:** ISSUE-17.1, ISSUE-17.2

## ISSUE-17.4: Notion Poller Integration for "Needs Spec" Epics
**Description:**
Update `notion_poller.py` to actively look for Notion cards where `Type == Epic` and `Status == Needs Spec`. 
- When found, it must trigger the `SprintManagerWorkflow` passing the Epic ID and description as the payload.
- Ensure the poller does not infinitely trigger the workflow (e.g., transition the status to `Spec Writing In Progress` immediately upon triggering).
**Blocked By:** ISSUE-17.3
