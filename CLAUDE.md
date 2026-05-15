# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

SailRatings — a SaaS platform for IRC sailing boat performance analysis.
Monorepo with two subprojects:

- **`api/`** — Python 3.11 backend (FastAPI, scrapers, analysis, CLI).
  See [`api/CLAUDE.md`](api/CLAUDE.md).
- **`web/`** — Next.js 16 frontend.
  See [`web/CLAUDE.md`](web/CLAUDE.md).

Both subprojects use `develop` as the working branch. Production deploys
from `main` on Railway.

## Environment

- This machine is the **dev server**. Changes here are live at `dev.sailratings.com`.
- Production is on Railway at `sailratings.com` — auto-deploys from `main`.
- PostgreSQL: `localhost:5433`, db `irc_data`, user/password `irc`/`irc`.
- Cloudflare tunnel config: `~/.cloudflared/config.yml`.
- Environment variables: `op run --env-file=.env.dev.template` (1Password).

### Ports

| Service     | Port | Domain                          |
|-------------|------|---------------------------------|
| Backend API | 4100 | `api-dev.sailratings.com`       |
| Frontend    | 4200 | `dev.sailratings.com`           |

## Deployment workflow

1. Work on `develop` in this repo.
2. Commit and push — changes visible at `dev.sailratings.com`.
3. To promote: merge `develop` → `main`, push `main` — Railway auto-deploys (~2 min).
4. Rollback via Railway dashboard if needed.

## Operational notes

- Always commit and push before ending a session — every commit is a rollback point.
- The frontend build bakes in the API URL — rebuild after changing
  `next.config.ts` or `NEXT_PUBLIC_API_BASE`.
- Scraper rate limits default to 2.0s delay with jitter — respect source sites.
- `api/data/raw` is a symlink to `/home/irc-data/data-raw/` (externalised
  to keep ~1 GB of scraper output out of git). Code reads/writes through the
  symlink transparently. To restore on a fresh machine: create the target
  directory and re-create the symlink, then run scrapers to repopulate.

## Agents

Shared subagents live in `.claude/agents/`:
- `sailing-marketing-writer` (Opus model) — used for `/ratings`, `/fleet`, `/results` page copy.

## Cron

Schedule lives at [`api/crontab.txt`](api/crontab.txt). Install via:
```bash
crontab api/crontab.txt
```
Key jobs: ORC scrape daily 03:00 UTC, TCC daily 06:00, SailSys results every
30 min, cert discovery + parsing weekly (Sundays), health check daily 09:00.

## Database migrations

Alembic in `api/alembic/versions/`. Run:
```bash
cd api && source .venv/bin/activate
alembic upgrade head
```

## Plan archive

- `~/.claude/plans/groovy-stargazing-alpaca.md` — original monorepo design.
- `~/.claude/plans/2026-05-15-monorepo-cutover-execution.md` — current cutover plan.
