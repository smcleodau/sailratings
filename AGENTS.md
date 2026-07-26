# AGENTS.md — SailRatings monorepo

Persistent memory for AI agents working in this repo. See also `CLAUDE.md`,
`HANDOFF.md`, and `api/CLAUDE.md` / `web/CLAUDE.md` for stack-specific guidance.

## Repo layout

- `api/` — Python 3.11 FastAPI backend (src layout: `api/src/irc_data/`).
  Run via `api/start-api.sh` (uvicorn on :4100 with `--reload`).
- `web/` — Next.js frontend. Admin UI lives at `web/src/app/justin/`.
- Postgres dev DB: `postgresql+psycopg://irc:irc@localhost:5433/irc_data`.
  Readily queryable for exploration; treat as live data.
- Feature work: each task gets a git worktree under
  `worktrees/<branch>/`. The running dev server serves from the **main**
  checkout (`/home/irc-data/code/sailratings/api`), NOT the worktree, so
  worktree branch changes are isolated until merged/deployed.

## API patterns (FastAPI)

- All routers mounted under `/v1` in `api/src/irc_data/api/app.py`.
- DB access: raw SQL via `sqlalchemy.text()`, engine injected via
  `Depends(get_db)` (`api/src/irc_data/api/deps.py`). Always use
  `with engine.connect() as conn:` (or `engine.begin()` for writes).
- Admin endpoints (`/v1/admin/*`) are Bearer-gated. Pattern copied per
  router:
  ```python
  ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
  def _verify_admin(authorization: str | None) -> None:
      expected = f"Bearer {ADMIN_PASSWORD}"
      if not ADMIN_PASSWORD or not authorization or authorization != expected:
          raise HTTPException(status_code=401, detail="Unauthorized")
  ```
  Note: `ADMIN_PASSWORD` is read at **import** time, so tests must set the env
  var *before* importing the app.
- `_jsonable()` helper (Decimal/datetime/UUID → JSON) exists in `admin.py`
  and `admin_tables.py`; copy it in if a router returns raw DB rows.
- Existing admin surfaces: `admin.py` (chat/execute/conversations),
  `admin_tables.py` (table browser+editor), `corrections.py` (moderation
  queue), `admin_dashboard.py` (overview/revenue/recent-orders). Public
  ops data: `health.py`, `pipeline.py`.

## Testing

- Tests in `api/tests/`. Run with:
  ```bash
  cd api && source /home/irc-data/code/sailratings/api/.venv/bin/activate
  export DATABASE_URL='postgresql+psycopg://irc:irc@localhost:5433/irc_data'
  export ENVIRONMENT=local
  export ADMIN_PASSWORD=test-admin-secret
  PYTHONPATH=src python3 -m pytest tests/ -q
  ```
  **`PYTHONPATH=src` is required** — there's no editable install, so
  `import irc_data` fails without it.
- The venv lives at `/home/irc-data/code/sailratings/api/.venv` (shared with
  the dev server). Invoke its python via `python3` (not `python`).
- No conftest / no test DB fixture. Integration tests use FastAPI
  `TestClient` against the live dev DB and assert on *structure* (keys,
  types, status codes), not specific data values.
- `admin_tables.py` uses deprecated `Query(regex=...)`; use `pattern=` for
  new code.
