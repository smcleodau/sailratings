# AGENTS.md

Persistent memory for OpenHands agent working in this repository (SailRatings
monorepo). Read at the start of every session.

## Repository shape

- Monorepo: `api/` (Python 3.11/12 FastAPI backend) + `web/` (Next.js frontend).
- See `CLAUDE.md`, `api/CLAUDE.md`, `web/CLAUDE.md` for stack detail.
- Branch model: `develop` is the working branch; `main` deploys to Railway.
  Feature work happens in `worktrees/<uuid>` branches off `main` or `develop`.

## Environment (dev box)

- Postgres: `localhost:5433`, db `irc_data`, user/pass `irc`/`irc`.
  (`DATABASE_URL` / `IRC_DATABASE_URL` = `postgresql+psycopg://irc:irc@localhost:5433/irc_data`)
- The shared dev DB is **live and shared**. Never run destructive migrations or
  TRUNCATE against `irc_data`. For DB-backed tests, create a throwaway DB
  (e.g. `irc_data_news_test`) and drop it when done.
- `pg_trgm` + `fuzzystrmatch` extensions are enabled (migration 0009) - use
  `similarity()` for fuzzy text matching against boats/identities.
- API: port 4100; Frontend: port 4200 (dev.sailratings.com / api-dev).

## Running things

- Python in a worktree: use **system `python3`** (3.12) with `PYTHONPATH=src`,
  NOT the main repo's `.venv` (its editable install points at
  `/home/irc-data/code/sailratings/api/src`, a different tree). `anthropic`,
  `sqlalchemy`, `pytest`, `alembic`, `fastapi` are all importable from system
  python3.
- Run tests: `cd api && PYTHONPATH=src python3 -m pytest tests/ -v`
- Apply migrations on a fresh DB:
  `IRC_DATABASE_URL=... PYTHONPATH=src python3 -c "from alembic...; command.upgrade(cfg,'head')"`
- CLI: `PYTHONPATH=src python3 -m irc_data.cli <command>` (entry point `irc-data`
  exists in the main venv but not necessarily installed in a worktree).
- API smoke via TestClient: set `config.DATABASE_URL` + `get_engine(test_url)`
  before importing routes, or set `DATABASE_URL` env.

## Architecture patterns (api)

- Alembic migrations in `api/alembic/versions/`, sequential numeric revisions
  (`revision = "00NN"`, `down_revision = "00NN-1"`). Chain from the latest
  revision present in YOUR branch.
- ORM models in `src/irc_data/db/models.py` (SQLAlchemy 2.0 `Mapped` style).
  Raw SQL via `text()` for analytics; ORM/`pg_insert` for upserts.
- DB access: `get_engine()` singleton; routers get it via `Depends(get_db)`.
  Use `with engine.begin() as conn:` for writes, `with engine.connect() as
  conn:` for reads (NEVER `engine.begin().execute(...)` - leaks connections).
- Routers mounted in `api/app.py` under `/v1`; schemas in `api/schemas/`.
- LLM calls: `anthropic` SDK + `ANTHROPIC_API_KEY` (see
  `api/services/insights_service.py`). Insights are streamed via SSE.
- CLI: `cli.py` is a Click group; subgroups registered via
  `register_*_commands(cli)` modules imported after the group def.

## LLM News Entity Extraction (SPEC-14 Sec. 4) - implemented here

The `irc_data.news` package implements the news entity-extraction workflow:
- `news/extraction.py` - LLM strict-JSON extraction of
  `{mentioned_boats, mentioned_events, sentiment, key_takeaway}`. Provider is
  configurable via `SAILING_LLM_PROVIDER` (default `anthropic`; `gemini`/`glm`
  wired when keys exist). `parse_extraction()` is robust to markdown fences +
  prose. The extractor is injectable for deterministic tests.
- `news/linking.py` - resolves `mentioned_boats` to canonical boats via
  sail-exact -> name-exact (boats + boat_identities) -> pg_trgm fuzzy, then
  writes `boat_news_mentions`.
- `news/workflow.py` - `ingest_news` -> `process_news_article` (extract->link)
  -> `process_pending` batch. Activity-style functions designed to map onto
  Temporal activities (the `temporal/` infra lives on `develop`, not main).
- Tables: `raw_news` (unstructured store) + `boat_news_mentions` (join),
  migration `0015_add_raw_news_and_mentions.py`. All datetimes TIMESTAMPTZ/UTC.
- CLI: `irc-data news ingest|process|process-one|stats`.
- API: `/v1/news/ingest`, `/v1/news`, `/v1/news/{id}`, `/v1/news/{id}/mentions`,
  `/v1/news/process-pending`, `/v1/boats/{id}/news`.

Note: a parallel `develop` effort created `boat_news`/`boat_news_mentions`
tables + a Gemini-based `scrapers/news.py` with a DIFFERENT extraction schema
(no events/sentiment/key_takeaway). This worktree's `raw_news` implementation
follows SPEC-14's named schema literally. Merge will need reconciliation.

## Gotchas

- The live DB may be ahead of a `main`-based worktree's migration chain (develop
  work applied out-of-band). Check `SELECT version_num FROM alembic_version`
  and `\dt` before assuming your migration's tables exist.
- Don't commit `.env`, `*.log`, or the `api/data/raw` symlink target.
- When the file_editor `create`/`str_replace` mangles unicode (em-dashes,
  section symbols) into mojibake, fix with a `python3` ascii-encode pass.
