# EPIC-02: OpenHands Orchestrator - Issues

This document breaks down the OpenHands Orchestrator EPIC into granular, actionable issues for the Lane Worker agents to implement.

## [ISSUE] Implement Swarm Orchestrator Parsing
**Specification:** [EPIC-02-OpenHands-Orchestrator.md](file:///home/irc-data/code/sailratings/docs/epics/EPIC-02-OpenHands-Orchestrator.md)
**Status:** Ready for Agent
**Type:** Issue
**Blocked By:** None
**Description:**
In `api/src/irc_data/temporal/orchestrator/swarm.py`, update the `SwarmOrchestratorWorkflow`'s `run` method. Currently, it loops and gets a response from the LLM, but just appends it to chat history. 
You must use a regex or string matching to find `<INVOKE_AGENT name="...">` in the `response`.
If the agent is "Sprint Manager", use `await workflow.execute_child_workflow("SprintManagerWorkflow", {"description": "Groom the board"})`.
If the agent is "Spec Writer", etc., dispatch appropriately. (For now, focus on Sprint Manager integration).

## [ISSUE] Implement Playwright E2E Tests Activity
**Specification:** [EPIC-02-OpenHands-Orchestrator.md](file:///home/irc-data/code/sailratings/docs/epics/EPIC-02-OpenHands-Orchestrator.md)
**Status:** Ready for Agent
**Type:** Issue
**Blocked By:** None
**Description:**
In `api/src/irc_data/temporal/orchestrator/activities.py`, the `run_playwright_e2e_tests` activity is a dummy stub that returns `True`.
Update it to use `asyncio.create_subprocess_shell` to execute `cd {worktree_path}/web && npm install && npx playwright test`.
If the process returns a non-zero exit code, capture the stderr/stdout, and return `False`.
Ensure you handle timeouts gracefully (the activity has a 15m timeout set in `workflows.py`, but the subprocess should be allowed to run).

## [ISSUE] Implement Create PR Activity
**Specification:** [EPIC-02-OpenHands-Orchestrator.md](file:///home/irc-data/code/sailratings/docs/epics/EPIC-02-OpenHands-Orchestrator.md)
**Status:** Ready for Agent
**Type:** Issue
**Blocked By:** Implement Playwright E2E Tests Activity
**Description:**
In `api/src/irc_data/temporal/orchestrator/activities.py`, the `create_pull_request` activity is a `pass` block.
Implement it to use the GitHub CLI (`gh`).
1. Execute `git -C {worktree_path} push -u origin HEAD`
2. Execute `gh pr create --title "Automated PR from OpenHands" --body "Evidence verified." --head <branch_name> --repo irc-data/sailratings` using `asyncio.create_subprocess_shell`.
(Assume `gh` is authenticated on the host).

## [ISSUE] Implement Dead Letter Queue Routing
**Specification:** [EPIC-02-OpenHands-Orchestrator.md](file:///home/irc-data/code/sailratings/docs/epics/EPIC-02-OpenHands-Orchestrator.md)
**Status:** Ready for Agent
**Type:** Issue
**Blocked By:** None
**Description:**
In `api/src/irc_data/temporal/orchestrator/activities.py`, update `route_to_dlq`.
Instead of just logging, it should append the failed task JSON into a local file `dlq.jsonl` in `/home/irc-data/code/sailratings/worktrees/dlq.jsonl` so we don't lose failed tasks.
