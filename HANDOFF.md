# Sail Ratings Monorepo — Cutover Handoff

> **Read me first.** This document is a self-contained brief from the
> previous Claude Code session, which built this monorepo from two
> separate repos and brought the dev services up here. There are six
> known open items below; pick them off in order.
>
> The full design rationale lives at
> `/home/irc-data/.claude/plans/groovy-stargazing-alpaca.md`. Read it
> if you need context on *why* the layout looks like this.

---

## Where you are

You're at `/home/irc-data/code/sailratings/`. Layout:

```
sailratings/
├── web/        ← Next.js 16 frontend (was smcleodau/sailratings repo)
├── api/        ← Python/FastAPI backend (was smcleodau/irc-data repo)
├── .claude/    ← Claude config (project-level)
└── HANDOFF.md  ← This file
```

`web/` runs on port **4200** (Cloudflare tunnel → `dev.sailratings.com`).
`api/` runs on port **4100** (Cloudflare tunnel → `api-dev.sailratings.com`).
Postgres on `localhost:5433` (database `irc_data`, user/password `irc`/`irc`).

Old repos still on disk at `/home/irc-data/code/irc-{frontend,data}/` —
safety-net for ~one week then delete. Do **not** push to those repos.

---

## Current state of services (verified at handoff time)

Both services are running, launched manually (NOT via systemd):

```bash
ss -tlnp | grep -E ':(4100|4200)'
# Should show two LISTEN entries
```

- API process: `uvicorn irc_data.api.app:app --host 0.0.0.0 --port 4100 --reload`
  with cwd `/home/irc-data/code/sailratings/api`, sourced `/home/irc-data/.env`
- Web process: `next start -p 4200` with cwd `/home/irc-data/code/sailratings/web`

Smoke-test commands (these all returned 200 at handoff):

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://dev.sailratings.com/
curl -s -o /dev/null -w "%{http_code}\n" https://dev.sailratings.com/ratings
curl -s -o /dev/null -w "%{http_code}\n" https://dev.sailratings.com/sitemap.xml
curl -s --max-time 5 https://api-dev.sailratings.com/v1/health | python3 -m json.tool | head
curl -s "https://api-dev.sailratings.com/v1/search?q=sun%20fish" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['results']),'results')"
```

If anything fails before you start work, restart the services:

```bash
# API
pkill -f 'uvicorn irc_data.api.app' 2>/dev/null
setsid nohup /home/irc-data/code/sailratings/api/start-api.sh > /home/irc-data/logs/api.log 2>&1 < /dev/null & disown

# Web
pkill -f 'next.*4200' 2>/dev/null
cd /home/irc-data/code/sailratings/web && setsid nohup ./node_modules/.bin/next start -p 4200 > /tmp/sailratings.log 2>&1 < /dev/null & disown
```

---

## Open items, in priority order

### 1. DNS for `api.dev.sailratings.com` (latent — will break on next frontend rebuild)

**Problem.** `next.config.ts` defaults to `https://api.dev.sailratings.com/v1`
(dot form), but only `api-dev.sailratings.com` (dash form) has a public DNS
record. The currently-running frontend works because the build is from
before the commit that introduced the dot-form default; the dot-form URL
is baked in nowhere yet. The next `npm run build` will switch to the dot
form and browser-side API calls will fail with DNS errors.

**Pick one fix:**

(a) Add the DNS record. Stuart manages DNS in Cloudflare. Add `api.dev`
as a CNAME pointing to whichever tunnel/proxy the existing `api-dev`
record points at, OR add an additional ingress rule on the cloudflared
tunnel for `api.dev.sailratings.com` (it's already there, just needs DNS).

(b) Change the default to the working hostname. In `web/next.config.ts`,
the `API_BASES` object has:

```ts
dev: "https://api.dev.sailratings.com/v1",
```

Change to:

```ts
dev: "https://api-dev.sailratings.com/v1",
```

The latter is the path of least resistance. Stuart's instinct may favour
the former (cleaner subdomain hierarchy: `dev.sailratings.com` +
`api.dev.sailratings.com`). Ask if unsure.

**Verification:** rebuild the frontend, look at what URL is baked into the
new chunks:

```bash
cd /home/irc-data/code/sailratings/web && npm run build
grep -ohE 'https://api[^"]+' .next/static/chunks/*.js | sort -u | head
```

Then restart the web service. Should still return 8 boats for "sun fish".

---

### 2. Reinstall the crontab from the new location

**Problem.** Current crontab still points at `/home/irc-data/code/irc-data`
(the old backend repo). The cron jobs work right now because that
directory still exists with its venv, but it's a foot-gun: when the old
dir is deleted in one week, every scheduled job breaks silently.

**Do:**

```bash
crontab /home/irc-data/code/sailratings/api/crontab.txt
crontab -l | grep IRC_DATA_DIR   # should show: IRC_DATA_DIR=/home/irc-data/code/sailratings/api
```

Then wait for the next scheduled job to run (top of the hour for the
SailSys results scrape; or `09:00 UTC` for the health check). Tail
`/home/irc-data/logs/` to confirm it lands.

If you want to test immediately, manually invoke one of the cron commands:

```bash
cd /home/irc-data/code/sailratings/api && /home/irc-data/code/sailratings/api/.venv/bin/irc-data health-check
```

Note that the venv here was COPIED from the old repo and its shebangs
patched. Most things work via system python's user-level site-packages
(see start-api.sh for the pattern: `/usr/bin/python3 -m uvicorn …`). The
venv is mostly used for its binaries (irc-data, alembic, pytest).

---

### 3. Update the systemd unit (requires sudo)

**Problem.** `/etc/systemd/system/sailratings-frontend.service` still has:

```
WorkingDirectory=/home/irc-data/code/irc-frontend
ExecStart=/usr/bin/npx next start -p 4200
```

If the box reboots, systemd will try to start `next start` in the old
directory and fail.

**Constraint.** The `irc-data` user does NOT have sudo edit access to
systemd unit files. `sudo -l` shows only:
- `systemctl {start,stop,restart,status,reload} cloudflared-sailing` (NOPASSWD)
- `systemctl daemon-reload` (NOPASSWD)
- A couple of cat/tee on cloudflared configs

So Stuart needs to `sudoedit` the unit himself. Tell him:

```bash
# Stuart runs:
sudoedit /etc/systemd/system/sailratings-frontend.service
# Change WorkingDirectory to: /home/irc-data/code/sailratings/web
# Optionally change ExecStart to: /usr/bin/node /home/irc-data/code/sailratings/web/.next/standalone/server.js
# (cleaner than next start; matches output: "standalone" in next.config.ts)

# Then YOU (or Stuart) can run:
sudo systemctl daemon-reload    # NOPASSWD, you can do this
sudo systemctl restart sailratings-frontend.service  # not NOPASSWD; Stuart only
```

After this, you can also kill the manually-launched next-server process
and let systemd take over.

There is no systemd unit for the API yet. If you want one, write
`/etc/systemd/system/sailratings-api.service` (Stuart drops it in via
sudo) modelled on the frontend's unit, with `WorkingDirectory=/home/irc-data/code/sailratings/api`
and `ExecStart=/home/irc-data/code/sailratings/api/start-api.sh`. Lower
priority — the manual launch survives until reboot.

---

### 4. Wire 1Password CLI (op-run) for env-var injection

**Problem.** Both services currently `source /home/irc-data/.env` to get
secrets. Stuart wants secrets out of files and into 1Password, with
`op run --env-file=<template>` resolving them at process start. This is
the same mechanism Railway will use on prod push.

**State of play:**

- `op` CLI v2.31.1 is installed at `/home/irc-data/.local/bin/op`.
- Service account token works against vault "Sail Ratings" at
  `foxleyfarm.1password.com`. The vault has at least two items:
  `Google Workspace` and `Sail Ratings`.
- Two env templates already exist at `/home/irc-data/code/.env.dev.template`
  and `/home/irc-data/code/.env.prod.template`. Right now they have
  empty values (e.g. `ANTHROPIC_API_KEY=`). They need to be rewritten
  with `op://Sail Ratings/<item>/<field>` references.

**The service account token** is not stored anywhere on disk yet. Before
running any `op` command:

```bash
export OP_SERVICE_ACCOUNT_TOKEN='<ask Stuart for the token, or copy from a previous session>'
op whoami  # should say SERVICE_ACCOUNT, foxleyfarm.1password.com
op vault list  # should show "Sail Ratings"
```

**Stuart still needs to confirm the 1P item structure.** I was blocked
by auto-mode classifier from inspecting the "Sail Ratings" item's
fields (it treated it as credential exploration before approval). The
likely convention is: one item per environment ("Sail Ratings - Dev" and
"Sail Ratings - Production" — neither exists yet), with each env var as
a field on the item, field name matching the env var name. Confirm with
Stuart before creating items.

**What to do once the convention is confirmed:**

1. Read every secret from `/home/irc-data/.env` (current source of truth).
2. Create 1P items via `op item create` for dev and prod.
3. Rewrite the two templates at `/home/irc-data/code/` with `op://`
   references. Move them to the new monorepo root or keep at parent dir
   — Stuart's call.
4. Change `start-api.sh` to wrap in `op run`:
   ```bash
   # New start-api.sh:
   #!/bin/bash
   set -euo pipefail
   cd /home/irc-data/code/sailratings/api
   export PYTHONPATH=src
   exec /home/irc-data/.local/bin/op run \
     --env-file=/home/irc-data/code/.env.dev.template \
     -- /usr/bin/python3 -m uvicorn irc_data.api.app:app --host 0.0.0.0 --port 4100 --reload
   ```
   The `OP_SERVICE_ACCOUNT_TOKEN` env var still needs to be set in the
   environment that runs `op run`. For systemd: `EnvironmentFile=/etc/sailratings/op-token`
   (Stuart creates that root-only file via sudo). For manual launch: in
   `~/.zshrc` as `export OP_SERVICE_ACCOUNT_TOKEN=...`.
5. Same shape for the frontend on `next start`.
6. Test the API still starts and serves health.
7. Delete `/home/irc-data/.env`.

This is the largest open item. Probably ~30 min of careful work once
Stuart's confirmed the item structure.

---

### 5. Init git, commit, push the monorepo to `smcleodau/sailratings`

**Problem.** `/home/irc-data/code/sailratings/` is not a git repo yet. The
GitHub remote `smcleodau/sailratings` is still pointing at the
*frontend*'s previous history.

**Approved plan (from `groovy-stargazing-alpaca.md`):**

- Fresh start (single initial commit). Archive old repos as reference.
- Force-push to `smcleodau/sailratings`.
- Stuart manually archives `smcleodau/irc-data` on GitHub via Settings →
  Archive.

**Before pushing, write safety tags on both old repos so the pre-merge
history is recoverable:**

```bash
cd /home/irc-data/code/irc-frontend
git tag pre-monorepo-frontend-main main
git tag pre-monorepo-frontend-develop develop
git push origin --tags

cd /home/irc-data/code/irc-data
git tag pre-monorepo-backend-main main
git tag pre-monorepo-backend-develop develop
git push origin --tags
```

**Then init + push the monorepo:**

```bash
cd /home/irc-data/code/sailratings
git init
git checkout -b main
git remote add origin git@github.com:smcleodau/sailratings.git
# Add a sensible root .gitignore + README + CLAUDE.md FIRST — see item 6
git add .
git commit -m "Initial monorepo: web + api"
git push --force-with-lease origin main
```

**This is destructive on the GitHub remote.** Stuart said in earlier
guidance he prefers to drive destructive migrations himself given
artefacts. Don't run the force-push without explicit "go" from him.

---

### 6. Root-level monorepo housekeeping

Once you've taken care of the items above, the monorepo could use:

- **Root `README.md`** — currently absent. Brief: this is the SailRatings
  platform monorepo; `web/` is the Next.js frontend, `api/` is the
  Python backend; see each subdirectory's CLAUDE.md for stack-specific
  commands.
- **Root `CLAUDE.md`** — currently absent. Update the existing
  `/home/irc-data/code/CLAUDE.md` to reflect the new monorepo layout,
  or write a new one at the monorepo root. Subproject CLAUDE.md files
  in `web/` and `api/` already exist and are mostly accurate — just
  update the "see also `../CLAUDE.md`" lines to point at the new root.
- **Root `.gitignore`** — currently absent. Consolidate the two
  subprojects' excludes:
  ```
  # Python
  .venv/
  __pycache__/
  *.pyc
  .pytest_cache/

  # Node
  node_modules/
  .next/
  *.tsbuildinfo

  # Env files (NEVER commit)
  .env
  .env.*
  !.env.*.example
  !.env.*.template

  # Logs
  /logs/
  *.log

  # OS
  .DS_Store
  ```
- **Move `web/.claude/agents/sailing-marketing-writer.md` to
  `.claude/agents/`** at monorepo root so the agent is available from
  anywhere in the tree, not just when cwd is `web/`. Verify after move
  that the agent still loads via `Agent` tool. (The current `web/.claude/`
  is a leftover from the old repo root.)
- **Consolidate `.claude/hooks.json`.** Both `web/.claude/hooks.json`
  and `api/.claude/hooks.json` are auto-commit hooks. They've been
  path-fixed in this cutover but should probably be a single root-level
  hook that auto-commits the entire monorepo, OR removed entirely if
  Stuart doesn't want the auto-commit behaviour.

---

## Constraints to keep in mind

- **User**: `irc-data` (Linux user). Don't try to rename or migrate.
- **Sudo**: only NOPASSWD for `systemctl {start,stop,restart,status,
  reload,daemon-reload}` of `cloudflared-sailing`, plus
  `systemctl daemon-reload` globally, plus `cat`/`tee` on cloudflared
  configs. **No edit access** to systemd unit files — Stuart must
  `sudoedit` those.
- **Postgres**: connection unchanged, still at `localhost:5433`, db
  `irc_data`, user `irc`, password `irc`. `DATABASE_URL` env var lives
  in `~/.env`.
- **Cloudflare tunnel**: ingress config at `~/.cloudflared/config.yml`
  is port-based, not path-based — no edit needed for the monorepo move.
- **No CI/CD anywhere** — neither old repo had GitHub Actions. The
  monorepo doesn't either yet. That's a Phase 7 follow-up.
- **The `data/` directory in `api/`** is 6,782 files (~most are cert
  PDFs). It comes along into the monorepo and into the git push.
  Consider externalising to S3 later (Phase 7).

---

## Reference

- **Full plan**: `/home/irc-data/.claude/plans/groovy-stargazing-alpaca.md`
- **Previous Claude session memory**: `/home/irc-data/.claude/projects/-home-irc-data-code-irc-frontend/memory/`
  — relevant entries:
  - `feedback_migrations.md` — Stuart prefers to drive destructive
    migrations himself given artefacts
  - `feedback_testing.md` — never present frontend without Playwright
    testing first
  - `feedback_design_process.md` — use Gemini + frontend-design for
    design work
- **Sailing marketing agent**: lives at `web/.claude/agents/sailing-marketing-writer.md`
  — Opus model, used for all `/ratings`, `/fleet`, `/results` page copy
  in the last session.
- **Old repo locations**: `/home/irc-data/code/irc-frontend/` and
  `/home/irc-data/code/irc-data/`. Both repos remain on `develop`
  branches; do NOT push to them — point new work at this monorepo
  once item #5 is done.

---

## Suggested order

1. (item #1) DNS or next.config.ts fix — trivial, prevents next rebuild from breaking
2. (item #2) Reinstall crontab — one command, prevents cron drift
3. (item #6) Root housekeeping (README + CLAUDE.md + .gitignore + move agent) — staging for the push
4. (item #5) Git init + push to GitHub — destructive, do with Stuart watching
5. (item #4) 1Password wiring — biggest piece, needs Stuart's input on item structure
6. (item #3) systemd unit edit + restart — needs Stuart's sudo; can be saved for last

Don't try to do item #4 (1Password) before #5 (push) — you want the
monorepo on GitHub before you start fiddling with credential injection,
in case anything needs rolling back.
