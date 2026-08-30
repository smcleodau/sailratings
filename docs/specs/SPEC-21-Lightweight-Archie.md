# SPEC-21: Lightweight Archie Orchestration & Evidence-Gated Handoffs

## 1. Overview
This specification details the implementation of the "Lightweight Archie" multi-agent pipeline using Temporal and OpenHands, replacing the monolithic TaskExecutionWorkflow. It explicitly enforces the "No False-Done" policy by requiring data-source-agnostic evidence injection via a `BoardOperator` interface.

## 2. Temporal Workflow: `EpicExecutionWorkflow`
This workflow replaces `TaskExecutionWorkflow`. It orchestrates the lifecycle of an Epic's issues through multiple specialized agents.

### 2.1 Workflow Steps
1. **Trigger:** Poller detects a Notion Issue in `Ready for Agent` status.
2. **Provision:** `provision_worktree` activity creates an isolated git worktree for the issue.
3. **Execution (Lane Worker):** `run_lane_worker_agent` activity is invoked. The OpenHands agent is tasked with writing code to satisfy the issue.
4. **E2E Testing:** `run_playwright_e2e_tests` activity runs to verify no regressions.
5. **Review (Gatekeeper):** `run_reviewer_agent` activity is invoked. The agent is explicitly denied code-writing tools. It reviews the diff and the issue board.
6. **Evaluation Gate:** 
   - If Reviewer PASSES: Proceed to `create_pull_request`.
   - If Reviewer FAILS (e.g., missing evidence): Loop back to `run_lane_worker_agent` with the Reviewer's critique injected into the prompt.

## 3. Specialized Agent Activities

### 3.1 Lane Worker (`run_lane_worker_agent`)
- **Model:** GLM-5.2 or equivalent coding model.
- **Tools:** Standard OpenHands tools (bash, file editor) + `BoardOperator` tools (`append_test_evidence`, `append_visual_evidence`).
- **System Prompt Requirements:** Must explicitly forbid the agent from completing its run without invoking the evidence tools. "A claim of 'it works' is not evidence."

### 3.2 Reviewer (`run_reviewer_agent`)
- **Model:** Claude 3.5 Sonnet or equivalent strong reasoning model (preferably cross-model from the worker).
- **Tools:** Read-only bash (git diff), `BoardOperator` read tools. No file editing.
- **System Prompt Requirements:** "You are the Adversarial Watchdog. Verify that the exact AC from the spec is met and that undeniable evidence (logs/screenshots) is posted to the Board. Reject if missing."

## 4. `BoardOperator` Interface (Data-Source Agnostic)

Create `api/src/irc_data/temporal/orchestrator/board_operator.py`.

```python
from abc import ABC, abstractmethod

class BoardOperator(ABC):
    @abstractmethod
    def append_test_evidence(self, issue_id: str, test_command: str, output: str) -> None:
        pass

    @abstractmethod
    def append_visual_evidence(self, issue_id: str, image_path: str) -> None:
        pass
        
    @abstractmethod
    def get_issue_content(self, issue_id: str) -> str:
        pass
```

### 4.1 `NotionAdapter` Implementation
Implement `NotionAdapter(BoardOperator)` that interacts with the Notion API using `SAILRATINGS_NOTION_TOKEN`.
- `append_test_evidence`: Appends a Notion `code` block with `language="bash"`.
- `append_visual_evidence`: Uploads image and appends an `image` block.
- `get_issue_content`: Fetches all blocks for the given `page_id` and parses text and image presence.

## 5. Security and Constraints
- **Resource Limits:** Docker containers must retain memory/CPU limits.
- **Worktree Isolation:** Lane Workers must only have access to their specific worktree.
- **Cross-Model Review:** The Reviewer should ideally use a different LLM family than the Lane Worker to prevent uniform hallucination.
