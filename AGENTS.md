# AGENTS.md — SailRatings monorepo notes

## Repo layout
- Monorepo at `/home/irc-data/code/sailratings/` with `api/` (Python 3.11 / FastAPI)
  and `web/` (Next.js). See `CLAUDE.md` / `api/CLAUDE.md` for full overview.
- Working branches: `develop` (dev server, live at dev.sailratings.com) and
  `main` (production, Railway auto-deploy). Worktrees live under `worktrees/`.

## Temporal orchestrator (`api/src/irc_data/temporal/orchestrator/`)
- NOTE: The `temporal/orchestrator/` directory (and several sibling files like
  `swarm.py`, `base_agent.py`, `board_operator.py`) currently exist ONLY as
  **untracked WIP files** in the `develop` checkout at
  `/home/irc-data/code/sailratings/api/src/irc_data/temporal/orchestrator/`.
  They are NOT committed to git and do NOT appear on `main` or in fresh
  `feature/*` worktrees. When a task references one of these files, edit it
  directly in the `develop` checkout (the only place it exists) unless the task
  clearly wants a new file created elsewhere.
- `workflows.py` (tracked on develop) defines the real Temporal workflows:
  `SprintManagerWorkflow` (takes `task_payload: dict` with a `description`
  key), `EpicExecutionWorkflow`, `TaskExecutionWorkflow`.
- `swarm.py` defines `SwarmOrchestratorWorkflow`, a signal/query chat loop
  that calls the `invoke_llm` activity and delegates to child workflows when
  the LLM emits an `<INVOKE_AGENT name="Sprint Manager">` tag.

## Conventions
- Tests via `pytest tests/ -v` (only meaningful on tracked code paths).
- Keep edits minimal; strip trailing whitespace in touched regions.
