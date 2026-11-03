# CLAUDE.md

Guidance for Claude Code when working in this repo.

## What this is

SailRatings — a SaaS platform for IRC sailing boat performance analysis.
Monorepo with two subprojects:

- **`api/`** — Python 3.11 backend (FastAPI, scrapers, analysis, CLI).
  See [`api/CLAUDE.md`](api/CLAUDE.md).
- **`web/`** — Next.js 16 frontend.
  See [`web/CLAUDE.md`](web/CLAUDE.md).

Both subprojects use `develop` as the working branch. Production runs
from `main` on this box (see Deployment workflow).

## Environment

- This machine runs **both dev and production** (since 2026-11-03; Railway is no longer used).
  - Dev: this checkout on `develop`, live at `dev.sailratings.com`.
  - Prod: worktree `/home/irc-data/code/sailratings-prod` on `main`, live at `sailratings.com`.
- PostgreSQL: `localhost:5433`, db `irc_data`, user/password `irc`/`irc` (shared by dev and prod).
- Cloudflare tunnels: dev on `/etc/cloudflared/sailing.yml` (system service `cloudflared-sailing`);
  prod on `~/.cloudflared/prod.yml` (user service `cloudflared-sailratings-prod`).
- Environment variables: 1Password Environments — dev `vzhxzxt7mgb4tolyepo5wqzcz4`, prod `ewoupn6p7qbyaxzrrda7tmhkr4`.

### Ports

| Service           | Port | Domain                                   | Service (systemd --user)  |
|-------------------|------|------------------------------------------|---------------------------|
| Dev backend API   | 4100 | `api-dev.sailratings.com`                | started by `api/start-api.sh` |
| Dev frontend      | 4200 | `dev.sailratings.com`                    | `sailing-web`             |
| Prod backend API  | 4300 | `api.sailratings.com`                    | `sailing-prod-api`        |
| Prod frontend     | 4400 | `sailratings.com`, `www.sailratings.com` | `sailing-prod-web`        |

## Deployment workflow

1. Work on `develop` in this repo.
2. Commit and push — changes visible at `dev.sailratings.com`.
3. Check for new migrations: `git diff --name-only origin/main origin/develop -- api/alembic/versions/`.
4. Promote: `git push origin origin/develop:refs/heads/main`.
5. Deploy: `scripts/prod/deploy.sh` (fast-forwards the prod worktree, runs migrations, rebuilds the
   site with prod settings, restarts both prod services, smoke-tests locally).
6. Rollback: `git -C /home/irc-data/code/sailratings-prod reset --hard <good-sha>` then re-run the
   build/restart steps of `deploy.sh` (or move `main` back and redeploy).

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
