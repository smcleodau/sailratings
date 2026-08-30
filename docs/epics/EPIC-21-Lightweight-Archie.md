# EPIC-21: Lightweight Archie Orchestration & Evidence-Gated Handoffs

## 1. Executive Summary
We are pivoting our factory architecture from a monolithic "do everything" agent to a **Lightweight Multi-Agent Pipeline** driven entirely by Temporal. Furthermore, we are instituting a fleet-wide, zero-tolerance **"No False-Done"** policy. No agent is allowed to declare an issue complete, flip a status, or merge a PR without explicitly posting verifiable evidence (screenshots, test outputs, execution traces) directly to the Issue tracker. Crucially, the agents must be **data-source-agnostic**. They operate against a generic `BoardOperator` interface, ensuring we can swap out the backend (e.g., Notion, Linear, Jira) without touching the agent logic.

## 2. The Agent Roster (Temporal Activities)
We will split `run_openhands_agent` into specialized, bounded roles within `activities.py`:

1. **Spec Writer (`run_spec_writer_agent`)**: 
   - Takes a generic Epic payload from the `BoardOperator`, researches the codebase, and drafts the definitive `PLAN` and Acceptance Criteria (AC).
   - Enforces the **Fix-not-build** rule (defaults to rewiring existing code rather than hallucinating new files).
2. **Lane Worker (`run_lane_worker_agent`)**: 
   - The coder. It runs on the isolated `git worktree`.
   - **Crucial:** It is strictly instructed that it cannot declare work finished. It must generate evidence (run tests, capture screenshots) and post it to the Board via the `BoardOperator`.
3. **Reviewer / Gatekeeper (`run_reviewer_agent`)**: 
   - The adversarial watchdog. It does not write code.
   - It reads the Coder's diff and *verifies* that the evidence posted to the Board actually satisfies the Spec Writer's AC.
   - If the evidence is missing or insufficient, it kicks the task back to the Lane Worker.

## 3. The "No False-Done" Protocol
This is the core law of the fleet. 

### 3.1 Data-Source-Agnostic Evidence Injection
We will build a generic `BoardOperator` interface for the OpenHands SDK that allows the Lane Worker to upload proof regardless of the backend:
- `post_test_evidence(issue_id: str, test_output: str)`
- `post_visual_evidence(issue_id: str, screenshot_path: str)` (Critical for any UI/UX tasks).
*The underlying implementation of this interface will initially use a `NotionAdapter`, but the agents only interact with the generic `BoardOperator`.*

### 3.2 The Temporal Workflow (`EpicExecutionWorkflow`)
The pipeline explicitly enforces the gates:
1. `provision_worktree`
2. `run_lane_worker_agent` -> (Worker codes and posts evidence via `BoardOperator`).
3. `run_playwright_e2e_tests` -> (System captures independent screenshots of the ephemeral sandbox).
4. `run_reviewer_agent` -> (Agent reviews the Board card. If there is no evidence, it throws an `ApplicationError("REJECTED: NO EVIDENCE")`).
5. Only if the Reviewer passes does it proceed to `create_pull_request`.

## 4. Acceptance Criteria
- [ ] `TaskExecutionWorkflow` is retired and replaced with a multi-step `EpicExecutionWorkflow`.
- [ ] Spec Writer, Lane Worker, and Reviewer are implemented as distinct Temporal activities with strict system prompts.
- [ ] A generic `BoardOperator` interface is built, with an initial `NotionAdapter` implementation.
- [ ] The Reviewer agent successfully blocks a pipeline execution if the Lane Worker fails to append evidence to the issue board.
