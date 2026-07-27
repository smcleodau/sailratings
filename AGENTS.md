# AGENTS.md — Repository memory for the SailRatings monorepo

Persistent context for AI agents working in this repo. Read at the start of
every session.

## Repo layout (worktrees)

This checkout is a **git worktree** under
`/home/irc-data/code/sailratings/worktrees/<uuid>`. The canonical repo (with
the full `.git`) lives at `/home/irc-data/code/sailratings`.

- The shared **virtualenv** lives at `/home/irc-data/code/sailratings/api/.venv`
  and has `irc-data` installed in **editable** mode pointing at the *parent*
  repo's `api/src`, NOT the worktree's `src`.
- Implication: when running pytest in a worktree, the parent editable install
  would otherwise shadow the worktree's code. `api/pyproject.toml` now sets
  `[tool.pytest.ini_options] pythonpath = ["src"]` so the worktree's `src`
  wins for tests. Run tests with:
  `cd api && /home/irc-data/code/sailratings/api/.venv/bin/python -m pytest tests/ -q`

## Architecture blueprints (MUST read before coding)

Task specs reference architectural blueprints at `docs/specs/SPEC-NN-*.md`.
NOTE: these spec files currently exist **untracked** in the *parent* repo at
`/home/irc-data/code/sailratings/docs/specs/` and are NOT present in worktrees
by default. To read a spec during a task, read it from the parent path, e.g.
`cat /home/irc-data/code/sailratings/docs/specs/SPEC-08-Sailing-AI.md`.

## API (Python 3.11, FastAPI) — `api/`

- Package root: `api/src/irc_data/` (setuptools `where=["src"]`, entry point
  `irc-data = irc_data.cli:cli`).
- AI insights/reports:
  - `api/src/irc_data/api/services/insights_service.py` — streaming Claude
    (SSE), holds `SYSTEM_PROMPT_PREMIUM` / `SYSTEM_PROMPT_TEASER`
    (report-section-specific, IRC-secret-aware, facts-disciplined).
  - `api/src/irc_data/api/services/report_service.py` — generates the paid
    premium report markdown + structured analytics.
  - `api/src/irc_data/api/services/report/prompts.py` — SPEC-08 "Technical
    Expert & Tactician" persona (`SYSTEM_PROMPT_EXPERT`) + domain-knowledge
    loader (`load_domain_knowledge`, `build_expert_system_prompt`). Loads
    `docs/domain/sailing-knowledge.md`.
  - `api/src/irc_data/api/routers/admin.py` — holds `ADMIN_SYSTEM_PROMPT`
    (data-quality assistant for non-technical co-founder Justin).
- Analysis engines under `api/src/irc_data/analysis/`:
  `regression.py` (E1 sensitivity), `temporal.py` (E2 IRC drift),
  `performance.py` (E3 RAI + head-to-head), `optimizer.py` (E4 ranked recs),
  `design_compare.py` (E5). All explicitly frame IRC as a **secret** rule —
  coefficients are "consistent with" / fleet patterns, never known weights.
- DB: PostgreSQL at `localhost:5433`, db `irc_data`, user/pass `irc`/`irc`.
  ORM in `db/models.py`; raw SQL via `text()` for analytics. Alembic in
  `api/alembic/versions/`.
- `api/data/raw` is a symlink to `/home/irc-data/data-raw/` (~1 GB, externalised).

## Conventions for prompts / domain content

- IRC = secret/empirical; ORC = open/VPP-based. ALWAYS name which system a
  number belongs to. Never claim to know the IRC formula — frame as empirical
  fleet regressions.
- "Facts Contract": only cite numbers present in the input JSON/Dataclass;
  never invent statistics. TCC to 4 decimals, GPH to 2, CDL to 3; signed deltas.
- Knowledge base lives at `docs/domain/sailing-knowledge.md` and is injected
  into the LLM context via the loader.

## Web (Next.js 16) — `web/`

Frontend. See `web/CLAUDE.md`. Build bakes in API URL — rebuild after changing
`next.config.ts` or `NEXT_PUBLIC_API_BASE`.

## Deployment

- Dev server is this machine; `develop` branch → `dev.sailratings.com`.
- Production on Railway from `main` → `sailratings.com`.
- Ports: backend API 4100 (`api-dev.sailratings.com`), frontend 4200
  (`dev.sailratings.com`). Cloudflare tunnel config at `~/.cloudflared/config.yml`.
- Always commit+push before ending a session (each commit is a rollback point).
