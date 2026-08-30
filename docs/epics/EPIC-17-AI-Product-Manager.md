# EPIC-17: The "AI Product Manager" (Specification Agent)

## 1. Executive Summary
Currently, our autonomous engineers (TaskExecutionWorkflow) require highly detailed, granular engineering issues to succeed. Hand-writing these specifications and breaking them into atomic tasks in Notion is a bottleneck. We must build a fully autonomous **AI Product Manager (Specification Agent)**. This agent will monitor Notion for new "Draft" Epics (e.g., "Build a global boat search page"), research the codebase, architect a technical solution, author a detailed `SPEC` markdown document, and inject atomic engineering issues into Notion with strict DAG dependencies.

Furthermore, this Specification Agent **must have memory**. It cannot architect solutions in a vacuum; it must query the "Corporate Memory Index" (pgvector) to ensure its specifications adhere to established patterns and avoid previously documented anti-patterns.

## 2. Core Responsibilities

1. **Intake & Triage**: Monitor the Notion database for Epics marked as `Draft` or `Needs Spec`.
2. **Context Gathering (RAG)**: 
   - Execute a `grep_search` or `view_file` over the codebase to understand current data models and UI patterns.
   - Execute `query_corporate_memory` (from EPIC-19) to recall architecture rules (e.g., "Always use `1Password` for secrets", "Use vanilla CSS instead of Tailwind").
3. **Specification Authoring**: Author a comprehensive Markdown document in `docs/specs/SPEC-XX-[Feature].md` detailing DB schema changes, API routes, and UI components.
4. **Issue Slicing**: Break the Epic down into 3-10 atomic engineering issues (e.g., "Create DB Migration", "Build API Endpoint", "Build Frontend Component").
   - **CRITICAL (Naming Convention):** All generated issues MUST have their titles prefixed with `[ISSUE]` (e.g., `[ISSUE] Create DB Migration`). Any new Epics created must be prefixed with `[EPIC]`.
   - **CRITICAL (Specification Property):** The agent MUST explicitly set the dedicated `Specification` database property (URL type) on every issue to point to the `docs/specs/SPEC-XX.md` file. This ensures programmatic access by downstream agents.
5. **DAG Dependency Mapping**: Inject these issues into the Notion database, explicitly linking them using the `Blocked By` relation so the Orchestrator executes them in the correct sequence.

## 3. Technical Architecture

### 3.1 The `SprintManagerWorkflow` (Temporal)
- Triggered by `notion_poller.py` when an Epic is in `Needs Spec`.
- Spawns an OpenHands Agent assigned the `Product Manager` persona.
- The Agent is given `write` access to `docs/specs/` but **read-only** access to `src/` to prevent the PM from writing production code.

### 3.2 Notion API Integration
- The Agent will use a specialized Python tool: `create_notion_issue(title, description, parent_epic_id, blocked_by_ids=[], spec_url="")`.
- The Agent must sequentially create issues, capturing the returned `Notion Page ID` to use as the `Blocked By` parameter for downstream issues.
- The Agent must populate the `Specification` URL property on the Notion page using the `spec_url` parameter.

### 3.3 Learning Loop Integration (The PM's Memory)
- Before writing the spec, the Orchestrator will inject a `Project Guidelines` prompt based on past successful PRs and architecture decisions pulled from the `pgvector` database.

## 4. Acceptance Criteria
- [ ] `SprintManagerWorkflow` successfully executes in Temporal.
- [ ] Agent successfully creates a well-formatted `.md` file in `docs/specs/`.
- [ ] Agent successfully creates multiple sub-issues in the Notion database linked to the parent Epic.
- [ ] Sub-issues correctly utilize the Notion `Blocked By` relation to enforce DAG execution.
- [ ] Agent correctly queries the `Corporate Memory Index` to enforce architectural consistency.
