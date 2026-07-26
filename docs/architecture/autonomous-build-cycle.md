# Autonomous 24/7 Build Cycle Architecture

## Overview
This document outlines the architecture for an autonomous, continuous software development lifecycle using the OpenHands Python SDK. The system orchestrates multiple specialized AI agents working concurrently on isolated branches and git worktrees to handle epic loading, issue tracking, specification drafting, testing, merging, and completion.

## 1. Orchestrator Design
The **Orchestrator** is a Python-based daemon built around the `openhands.sdk` and `openhands.workspace` packages. It is responsible for task discovery, agent provisioning, environment isolation, and lifecycle management.

### Key Components:
- **Task Ingestion Loop:** A polling mechanism (or webhook receiver) that integrates with Notion and GitHub APIs. It reads Epics/Issues, prioritizes them, and translates them into structured `Task` objects.
- **Environment Provisioner:** Uses `git worktree add` to create lightweight, isolated checkout directories for each task without cloning the entire repository multiple times.
- **Agent Factory:** Utilizes the OpenHands Python SDK to programmatically spawn specialized agents based on the task requirements:
  ```python
  from openhands.sdk import LLM
  from openhands.sdk.agent import Agent
  from openhands.workspace import DockerWorkspace

  # Spin up an agent in an isolated workspace mapping to the git worktree
  workspace = DockerWorkspace(host_dir="/path/to/git/worktree")
  agent = Agent(llm=LLM(model="anthropic/claude-3-5-sonnet"), workspace=workspace)
  ```
- **Evaluation Engine:** Monitors agent progress via SDK events. Once an agent completes a task, the orchestrator triggers a testing suite. If tests pass, it proceeds to the PR phase; if they fail, the context is fed back to the agent for fixing.

## 2. Agent Roles
By leveraging the OpenHands SDK's flexible tool registries and custom prompts, we define specific agent personas:

- **Spec Writer Agent:** Takes raw Notion/GitHub issues and expands them into detailed technical specifications, writing Markdown files and defining acceptance criteria.
- **Scraper/Implementation Engineer:** The core coder. Equipped with CLI tools, grep, and file editors (`openhands.tools`). Focused on writing the actual feature code and unit tests.
- **PR Reviewer Agent:** A read-only agent that analyzes diffs against the `main` branch. It provides feedback, requests changes, or approves the code for merging.

## 3. Worktree and Git Management
To allow multiple agents to work concurrently without blocking each other:

1. **Branching Strategy:** For every new task, the Orchestrator creates a new branch off `main` (e.g., `feature/task-123`).
2. **Git Worktrees:** Instead of copying the repo, the Orchestrator runs `git worktree add ../worktrees/task-123 feature/task-123`. 
3. **Isolation:** Each OpenHands agent is assigned a `DockerWorkspace` mounted *only* to their specific worktree directory. This ensures they cannot interfere with `main` or other agents' files.
4. **Merging:** Once the Implementation Engineer finishes, the code is committed. The Orchestrator opens a PR. The PR Reviewer agent evaluates it. Upon approval, the Orchestrator performs a standard `git merge` or squash merge via the GitHub API, deletes the branch, and prunes the worktree (`git worktree remove`).

## 4. The 24/7 Loop
The system operates as a continuous, state-machine-driven loop:

1. **Poll & Queue:** Check Notion/GitHub every X minutes. Push new actionable items to a Redis/RabbitMQ queue.
2. **Dispatch:** For each available slot (based on API limits/compute), pop a task, create a worktree, and spawn the relevant agent (e.g., Spec Writer first).
3. **Execution & Event Streaming:** The OpenHands SDK runs the reasoning-action loop. The Orchestrator listens to the `Conversation` event stream to detect state changes (`Complete`, `Error`, `Awaiting_Input`).
4. **Handoff:** When the Spec Writer completes, the Orchestrator commits the specs and hands the same worktree over to the Implementation Engineer.
5. **Teardown:** Upon successful merge, the worktree is dismantled, and the Orchestrator immediately grabs the next task from the queue.
