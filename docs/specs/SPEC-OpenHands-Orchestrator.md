# Specification: OpenHands Orchestrator

## 1. System Architecture
The Orchestrator is a Python daemon leveraging the OpenHands Python SDK (`openhands.sdk` and `openhands.workspace`) to automate development.

### 1.1 The 24/7 Event Loop
- **Polling:** Checks Notion/GitHub APIs at predefined intervals.
- **Queueing:** Pushes actionable tasks into a message broker (Redis/RabbitMQ).
- **Dispatching:** Pops tasks from the queue based on API and compute limits.
- **Monitoring:** Subscribes to the OpenHands SDK `Conversation` event stream to detect `Complete`, `Error`, and `Awaiting_Input` states.

## 2. Git Worktree & Isolation Logic
- **Branch Creation:** For a new task, create a branch off `main` (e.g., `git branch feature/<task-id> main`).
- **Worktree Provisioning:** Use `git worktree add ../worktrees/<task-id> feature/<task-id>` to create an isolated directory without cloning the entire repository.
- **Workspace Mapping:** Spawn a `DockerWorkspace` via OpenHands SDK mapped strictly to the host directory `../worktrees/<task-id>`. This prevents the agent from modifying `main` or other agents' files.
- **Teardown:** Upon a successful GitHub merge, execute `git worktree remove` and delete the branch.

## 3. SDK Integration & Agent Provisioning
Using the OpenHands Python SDK, the Orchestrator will provision agents dynamically.

### Sample Implementation:
```python
from openhands.sdk import LLM
from openhands.sdk.agent import Agent
from openhands.workspace import DockerWorkspace

def spawn_agent(worktree_path: str, role_prompt: str):
    workspace = DockerWorkspace(host_dir=worktree_path)
    llm = LLM(model="anthropic/claude-3-5-sonnet")
    agent = Agent(llm=llm, workspace=workspace, system_prompt=role_prompt)
    return agent
```

### Agent Roles:
- **Spec Writer Agent:** Expands Notion/GitHub issues into technical markdown specs and acceptance criteria.
- **Scraper/Implementation Engineer:** Equipped with CLI tools, grep, and file editors to write the actual feature code and unit tests.
- **PR Reviewer Agent:** A read-only agent that analyzes branch diffs against `main` for QA and approvals.

## 4. Evaluation and Lifecycle
- **Execution & Handoff:** The Orchestrator runs the reasoning-action loop. When the Spec Writer completes, it hands the same worktree over to the Implementation Engineer.
- **Evaluation Engine:** Once the Implementation Engineer finishes, the Orchestrator triggers tests. If they fail, context goes back to the agent; if they pass, a PR is created.
- **Merging & Teardown:** The PR Reviewer agent evaluates the PR. On approval, the Orchestrator performs a merge, prunes the worktree, and grabs the next task from the queue.
