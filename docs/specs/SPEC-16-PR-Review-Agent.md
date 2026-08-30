# SPEC-16: The "Senior Engineer" PR Review Agent

## 1. Overview
To achieve complete autonomy, PRs opened by worker agents must be reviewed and merged automatically without a human bottleneck. We will create a specialized Temporal workflow that acts as a strict Senior Engineer.

## 2. Architecture

### 2.1 Webhook Listener
- Create a FastAPI endpoint `POST /v1/webhooks/github` that listens for `pull_request` events (specifically `opened` or `synchronize`).

### 2.2 `PRReviewWorkflow`
- When the webhook fires, it triggers the `PRReviewWorkflow` in Temporal.
- The workflow provisions an OpenHands agent with a strict system prompt:
  > "You are a ruthless Senior Architect reviewing a Pull Request. You must enforce the 'Paper' CSS design system. You must check for SQL injection vulnerabilities. You must check for strict Typing. Read the git diff. If the code is perfect, output the exact string: [APPROVE_MERGE]. If it has flaws, output a detailed code review."
- The workflow feeds the agent the output of `git diff origin/main...feature_branch`.

### 2.3 Execution & Handoff
- If the agent outputs `[APPROVE_MERGE]`, the Temporal workflow executes `gh pr merge --merge`.
- If the agent rejects it, the workflow executes `gh pr review --request-changes -b "<agent_feedback>"`.
- The original worker agent (who opened the PR and is still waiting in its own loop) will detect the requested changes, fix the code, push a new commit, and the cycle repeats.

## 3. Acceptance Criteria
- [ ] A dedicated `PRReviewWorkflow` successfully triggers on new PRs.
- [ ] The agent correctly identifies intentional flaws (e.g. using `bg-blue-500` instead of `--color-navy`) and blocks the merge.
- [ ] Flawless PRs are merged to `main` automatically.
