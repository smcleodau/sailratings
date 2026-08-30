# SPEC-02: OpenHands Orchestrator

## Overview
This specification details the final missing pieces to achieve a 24/7 autonomous build loop using the OpenHands Python SDK and Temporal Orchestrator. The current codebase has stubbed workflows (`workflows.py`, `activities.py`) but is missing critical functionality for full loop closure.

## Technical Architecture

### 1. Swarm Orchestrator (`swarm.py`) Implementation
Currently, `SwarmOrchestratorWorkflow` runs an LLM loop but only produces chat output.
- **Requirement:** Parse the `<INVOKE_AGENT name="...">` XML tags from the Orchestrator's LLM response.
- **Action:** If `<INVOKE_AGENT name="Sprint Manager">` is detected, trigger a `SprintManagerWorkflow` execution via Temporal's child workflow or `execute_workflow` capability. 
- **Action:** If an issue is ready, start `EpicExecutionWorkflow`.

### 2. Activity Completion (`activities.py`)
Currently, several critical activities are just `pass` stubs:
- **`run_playwright_e2e_tests(worktree_path: str)`:**
  - Must `chdir` into `worktree_path` and execute `npm run test:e2e` or `npx playwright test`.
  - Must return `False` if the exit code is non-zero, triggering the workflow to feedback the error to the Lane Worker agent.
- **`create_pull_request(worktree_path: str)`:**
  - Must use the GitHub CLI (`gh pr create`) or `PyGithub` to push the local worktree branch to origin and open a PR against `main`.
- **`notify_admin_hitl(details: dict)`:**
  - Must send an email or Slack notification, or simply log a high-priority warning to the console so the user knows manual intervention is required.
- **`route_to_dlq(details: dict)`:**
  - Save the failed task payload to a local SQLite database or JSON file in a `dlq/` directory for later retry.

### 3. Agent Tooling
- The OpenHands `Agent` in `run_lane_worker_agent` uses `LocalWorkspace`. It needs the `NotionAdapter` injected into its python environment or instructions on how to use `board_operator.py` so it can upload visual evidence.

## Security & Constraints
- All agents must execute within the isolated Git Worktrees (`provision_worktree` already handles this).
- Do not run E2E tests in the main `code/sailratings` directory; always execute inside the `worktree_path`.
