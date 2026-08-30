# ISSUES: EPIC-21 (Lightweight Archie & Evidence-Gated Handoffs)

The following granular engineering issues must be executed by the worker agents to build the Lightweight Archie pipeline.

## ISSUE-21.1: Build `BoardOperator` Interface and `NotionAdapter`
**Description:** 
The Lane Worker needs a way to push indisputable proof to the issue tracker, but it must be data-source-agnostic. Create `api/src/irc_data/temporal/orchestrator/board_operator.py`.
- Define an abstract base class `BoardOperator` with methods: `append_test_evidence(issue_id: str, test_command: str, output: str)` and `append_visual_evidence(issue_id: str, image_path: str)`.
- Implement `NotionAdapter(BoardOperator)` that fulfills this interface using the Notion API.
**Acceptance Criteria:**
- The OpenHands agents are only provided the generic `BoardOperator` tools.
- The `NotionAdapter` successfully posts markdown code blocks and images to Notion pages without the agent knowing Notion is the backend.

## ISSUE-21.2: Refactor `TaskExecutionWorkflow` into Multi-Agent Pipeline
**Description:**
We must replace the monolithic `TaskExecutionWorkflow` with an assembly line. Open `api/src/irc_data/temporal/orchestrator/workflows.py` and rewrite it.
- Rename to `EpicExecutionWorkflow`.
- The sequence must explicitly be: `provision_worktree` -> `run_lane_worker_agent` -> `run_playwright_e2e_tests` -> `run_reviewer_agent`.
- If the reviewer agent returns `False` (meaning the code failed or evidence is missing), the workflow must loop back to `run_lane_worker_agent`, appending the reviewer's critique to the prompt.
**Blocked By:** None

## ISSUE-21.3: Define Specialized Agent Activities
**Description:**
Split the single `run_openhands_agent` in `activities.py` into distinct profiles.
- **`run_lane_worker_agent`:** System prompt must explicitly mandate: *"You are a coder. You CANNOT finish your task without using the `append_test_evidence` or `append_visual_evidence` tool. A claim of 'it works' is not evidence. You must post real logs or screenshots via the BoardOperator."*
- **`run_reviewer_agent`:** System prompt must mandate: *"You are the Adversarial Watchdog. Do not write code. Review the git diff. Query the BoardOperator to check for pasted evidence. If there is no pasted evidence, you must REJECT the task."*
**Blocked By:** ISSUE-21.1, ISSUE-21.2
