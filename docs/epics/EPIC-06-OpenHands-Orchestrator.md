# EPIC-06: OpenHands Orchestrator

## Description
Develop a 24/7 autonomous build cycle orchestrator using the OpenHands Python SDK. This orchestrator will automate the entire software development lifecycle, from task ingestion to PR merging, leveraging multiple specialized AI agents working concurrently on isolated git worktrees.

## Goals
- Establish a continuous, state-machine-driven event loop.
- Integrate with Notion and GitHub for task ingestion and queue management.
- Implement isolated agent environments using Git Worktrees and DockerWorkspaces.
- Utilize the OpenHands Python SDK to programmatically spawn specialized agent roles (Spec Writer, Implementation Engineer, PR Reviewer).

## Acceptance Criteria
- Orchestrator can ingest a task, spawn a worktree, and trigger a Spec Writer agent.
- Orchestrator successfully hands off the task to the Implementation Engineer.
- Orchestrator facilitates the PR creation and assigns the PR Reviewer agent.
- Successful tasks are merged, and worktrees are cleaned up autonomously.
