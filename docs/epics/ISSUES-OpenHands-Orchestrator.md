# Issues: OpenHands Orchestrator

- **[Issue 1] Setup Orchestrator Event Loop Daemon**
  - Create the core Python daemon that continuously polls for new tasks.
  - Setup Redis/RabbitMQ queue integration for task management.
- **[Issue 2] Implement Task Ingestion Integrations**
  - Build webhook receivers or polling mechanisms for Notion and GitHub APIs.
  - Translate raw tickets into structured `Task` objects.
- **[Issue 3] Implement Git Worktree Provisioner**
  - Write utility scripts to create branches off `main`.
  - Implement `git worktree add` to create isolated environments.
  - Add cleanup logic (`git worktree remove`) for teardown after PR merge.
- **[Issue 4] Integrate OpenHands Python SDK (Agent Factory)**
  - Integrate `openhands.sdk` and `openhands.workspace`.
  - Implement spawning of `DockerWorkspace` mapped to specific worktrees.
  - Define instantiation logic for specific agent personas (Spec Writer, Implementation Engineer, PR Reviewer).
- **[Issue 5] Build Evaluation Engine & Handoff Logic**
  - Listen to `Conversation` event stream for agent state changes.
  - Implement testing suite execution upon agent task completion.
  - Hand off work from Spec Writer -> Implementation Engineer -> PR Reviewer based on event triggers.
