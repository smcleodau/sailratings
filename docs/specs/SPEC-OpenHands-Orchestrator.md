# Specification: OpenHands Orchestrator

## 1. System Architecture
The Orchestrator is a Python daemon leveraging the OpenHands Python SDK (`openhands.sdk` and `openhands.workspace`) and Temporal to automate development.

### 1.1 The 24/7 Event Loop (Temporal)
- **State Machine:** Temporal manages the entire agent lifecycle, providing robust retries, logging, and state tracking.
- **Polling:** A background task queries the Notion `Issues` database every 60 seconds for tasks in "Ready for Agent" status.
- **Queueing:** Translates Notion tasks into structured payloads and triggers the `TaskExecutionWorkflow` in Temporal.
- **Monitoring:** Subscribes to the OpenHands SDK `Conversation` event stream to detect `Complete`, `Error`, and `Awaiting_Input` states.

## 2. Git Worktree & Isolation Logic
- **Branch Creation:** For a new task, create a branch off `main` (e.g., `git branch feature/<task-id> main`).
- **Worktree Provisioning:** Use `git worktree add ../worktrees/<task-id> feature/<task-id>` to create an isolated directory without cloning the entire repository.
- **Workspace Mapping:** Spawn a `DockerWorkspace` via OpenHands SDK mapped strictly to the host directory `../worktrees/<task-id>`. This prevents the agent from modifying `main` or other agents' files.
- **Teardown:** Upon a successful GitHub merge, execute `git worktree remove` and delete the branch.

## 3. SDK Integration & Agent Provisioning
Using the OpenHands Python SDK, the Orchestrator will provision agents dynamically using a custom Docker image.

### 3.1 Custom Docker Environment
The custom Agent image will be built on `mcr.microsoft.com/playwright:v1.44.0-jammy` to provide native headless browser support for UI/UX testing, along with Python 3.12, Node.js, and pre-cached dependencies.

### 3.2 Agent Roles:
- **Spec Writer Agent:** Expands Notion/GitHub issues into technical markdown specs and acceptance criteria.
- **Implementation Engineer:** Equipped with CLI tools, grep, file editors, and Playwright to write and test feature code.
- **PR Reviewer Agent:** A read-only agent that analyzes branch diffs against `main` for QA and approvals.

## 4. Cost Monitoring, Observability, and Limits
- **Cost Telemetry:** An `observability.py` module intercepts the OpenHands `Conversation` stream to calculate input/output token usage in real-time.
- **Budget Limits:** Hard dollar limits (e.g., $5 max) and iteration caps (e.g., `max_iterations=30`) are strictly enforced per task to prevent runaway costs.
- **Circuit Breakers:** If an agent exceeds limits, the orchestrator immediately kills the instance.

## 5. Evaluation, Quality Assurance, and Lifecycle
- **GUI/UX Testing (Playwright):** For frontend changes, the Orchestrator executes Playwright E2E tests (`npx playwright test`) as a final gatekeeper before allowing a PR.
- **Failure Management & DLQ:** If tests fail repeatedly or a circuit breaker trips, Temporal routes the task to a Dead Letter Queue (DLQ).
- **Human-In-The-Loop (HITL):** Suspended DLQ tasks wait for a `human_intervention_signal` from an administrator (via the Admin Dashboard) for manual review or unblocking.
- **Merging & Teardown:** Upon test and PR Reviewer approval, the Orchestrator performs a merge, prunes the worktree, and grabs the next task.
