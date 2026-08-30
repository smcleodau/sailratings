# SPEC-15: Ephemeral CI/CD Sandboxes (The "Test Kitchen")

## 1. Overview
Agents currently run inside isolated Git Worktrees, but they lack a live environment to execute end-to-end tests before pushing code. We need an ephemeral sandbox for every agent task to boot the stack and verify functionality.

## 2. Architecture

### 2.1 `docker-compose.test.yml`
Create a lightweight docker-compose file specifically for agent worktrees:
- **db:** Postgres database.
- **api:** FastAPI backend (mounted to the worktree's `api/` directory).
- **web:** Next.js frontend (mounted to the worktree's `web/` directory).

### 2.2 Temporal Activity (`run_ephemeral_sandbox`)
- Add a new activity to `TaskExecutionWorkflow` right before `create_pull_request`.
- The activity will execute a bash script inside the specific Git worktree:
  1. Boot the stack: `docker-compose -f docker-compose.test.yml up -d`
  2. Run migrations: `alembic upgrade head`
  3. Seed test data.
  4. Execute tests: `pytest` and `npx playwright test`.
- **Feedback Loop:** If the tests fail, the activity does NOT crash the workflow. Instead, it extracts the failing test logs and feeds them back into the OpenHands agent with a prompt like: *"Your code failed the CI tests. Here are the logs. Please fix the code."* The agent resumes its loop.

## 3. Acceptance Criteria
- [ ] Agents can successfully spin up isolated Docker stacks inside their worktrees.
- [ ] Failing tests block PR creation and force the agent to fix the code.
- [ ] Docker containers are aggressively cleaned up during the `teardown_worktree` activity to prevent port exhaustion on the host.
