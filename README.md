# SailRatings

SaaS platform for IRC sailing boat performance analysis. Monorepo containing
the customer-facing site, the rating-data backend, the AI insights service,
and the data-ingestion pipeline.

| Service       | Path    | Stack                       | Dev URL                          |
|---------------|---------|-----------------------------|----------------------------------|
| Frontend      | `web/`  | Next.js 16 / React 19 / TS  | https://dev.sailratings.com      |
| Backend / API | `api/`  | Python 3.11 / FastAPI       | https://api-dev.sailratings.com  |

PostgreSQL 16 runs on `localhost:5433` (dev DB `irc_data`, user/password `irc`/`irc`).

## Quick start

```bash
# API (port 4100)
cd api && ./start-api.sh

# Frontend dev server (port 3000)
cd web && npm run dev

# Frontend production build (port 4200)
cd web && ENVIRONMENT=dev npm run build && ./node_modules/.bin/next start -p 4200
```

## Layout

- **`web/`** — Next.js frontend. See [`web/CLAUDE.md`](web/CLAUDE.md).
- **`api/`** — Python backend, CLI, scrapers, analysis engines. See [`api/CLAUDE.md`](api/CLAUDE.md).
- **`.claude/agents/`** — shared subagents (e.g. `sailing-marketing-writer`).
- **`api/data/raw`** — symlink to `/home/irc-data/data-raw/` on the dev box;
  contains ~1 GB of raw scraper output (cert PDFs, CSVs). Regeneratable via the
  cron jobs in `api/crontab.txt`.

## Deployment

- **Dev** (this box): work on `develop`, commit, push — visible at `dev.sailratings.com`.
- **Production** (Railway): merge `develop` → `main`, push; Railway auto-deploys (~2 min).
- Rollback: Railway dashboard, or `git revert` + push.

## Environment

For local dev, secrets are sourced via 1Password CLI:

```bash
op run --env-file=.env.dev.template -- <command>
```

See `api/start-api.sh` for the injection pattern. Templates: `.env.dev.template`,
`.env.prod.template`.
