# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also: `../CLAUDE.md` for environment, deployment, and cross-project context.

## Project overview

Python backend for the SailRatings platform. Collects IRC/ORC sailing rating data from 15+ sources, performs statistical analysis, serves a FastAPI REST API, and generates AI-powered boat optimization reports.

## Commands

```bash
# API server
./start-api.sh                    # sources ~/.env, uvicorn on :4100 with --reload

# CLI (activate venv first: source .venv/bin/activate)
irc-data <command>                # entry point defined in pyproject.toml [project.scripts]

# Common CLI commands
irc-data scrape orc               # ORC ratings (daily)
irc-data scrape tcc               # IRC TCC listings (daily)
irc-data scrape certs --exhaustive # IRC certificate PDFs
irc-data scrape results --source sailsys --all-clubs
irc-data parse-certs              # parse downloaded certificate PDFs
irc-data match-boats              # cross-source identity resolution
irc-data rematch-results          # link unmatched race results to boats
irc-data seed-designs             # populate/update design classes
irc-data refresh-views            # refresh materialized views
irc-data health-check --notify    # monitoring with webhook
irc-data db-upgrade               # run alembic upgrade head
irc-data list [--country X] [--design Y]
irc-data show <sail_number>

# Database migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Tests
pytest tests/ -v

# Docker (alternative)
docker-compose up                 # postgres:5433 + api:4100 + caddy
```

## Architecture

### Source layout

```
src/irc_data/
├── api/
│   ├── app.py              # FastAPI app, CORS, router mounting
│   ├── deps.py             # get_db() → shared SQLAlchemy Engine
│   ├── routers/            # 13 routers, all mounted under /v1
│   │   ├── search.py       # GET /search — fuzzy search via pg_trgm
│   │   ├── boats.py        # GET /boats/{id} — detail + ratings
│   │   ├── analytics.py    # 5 analysis engines (sensitivity, drift, RAI, optimizer, design compare)
│   │   ├── insights.py     # POST /insights/ask — SSE streaming Claude analysis
│   │   ├── checkout.py     # POST /checkout/create-session — Stripe
│   │   ├── reports.py      # GET /reports/{token} — paid report + PDF download
│   │   ├── events.py       # race events & results
│   │   ├── designs.py      # design class data
│   │   ├── fleet.py        # fleet-wide statistics
│   │   ├── health.py       # system health & data freshness
│   │   ├── pipeline.py     # ingestion pipeline status
│   │   ├── surveys.py      # user feedback
│   │   └── admin.py        # admin operations
│   ├── services/           # insights_service (Claude streaming), report_service, pdf_service, email_service (Resend)
│   └── schemas/            # Pydantic request/response models
├── scrapers/               # 15+ source-specific scrapers
│   ├── base.py             # RateLimiter, async HTTP client, retry logic
│   ├── orc.py              # ORC API (data.orc.org)
│   ├── tcc_listing.py      # IRC TCC CSV from ircrating.org
│   ├── certificate_*.py    # IRC certificate PDF discovery & bulk download
│   ├── sailsys.py          # Australian SailSys race results
│   ├── rorc.py, cowesweek.py, sydneyhobart.py, sailracehq.py, sailwave.py, yachtscoring.py, rhkyc.py, isora.py, topyacht.py
│   ├── wayback.py          # Wayback Machine historical archive
│   └── result_base.py      # Base class for result scrapers
├── parsers/                # PDF cert parsing (pdfplumber), CSV parsing
├── analysis/               # 5 statistical engines
│   ├── regression.py       # Engine 1: measurement sensitivity (how TCC varies with each dimension)
│   ├── temporal.py         # Engine 2: IRC formula drift over time
│   ├── performance.py      # Engine 3: RAI (Racing Advantage Index), head-to-head
│   ├── optimizer.py        # Engine 4: which measurements to change for rating gain
│   └── design_compare.py   # Engine 5: cross-design class comparison
├── matching/               # Boat identity resolution, design matching, result linking
├── db/
│   ├── models.py           # SQLAlchemy ORM models (14 tables)
│   ├── connection.py       # Engine singleton, init_db() with Alembic
│   └── operations.py       # CRUD helpers
├── cli.py                  # Click CLI (entry point: irc-data)
└── config.py               # URLs, paths, rate limits, country codes
```

### Data flow

1. **Scrapers** collect raw data (PDFs, CSVs, HTML, JSON) → stored in `data/raw/`
2. **Parsers** extract structured data from raw files
3. **Matching** resolves boat identities across sources (same boat, different names/sail numbers)
4. **DB** stores everything in PostgreSQL (14 tables + materialized views)
5. **Analysis engines** compute statistics on demand from DB
6. **API** serves results to the frontend, streams Claude AI insights via SSE

### Key patterns

- DB access uses raw SQL with `text()` for analytics queries, ORM for CRUD
- `get_db()` dependency injection provides the shared SQLAlchemy Engine to all routers
- `DATABASE_URL` can be overridden via env var; Railway gives `postgresql://` which app.py auto-converts to `postgresql+psycopg://`
- Scraper rate limiting: 2.0s default delay with jitter, 3 retries
- Insights use two tiers: free (~150 words) and premium (~800-1000 words) via Claude API
- API docs at `/v1/docs` (Swagger) and `/v1/redoc`

### Database

Dev: `postgresql+psycopg://irc:irc@localhost:5433/irc_data`

Core tables: `boats` (central), `tcc_snapshots`, `certificates` (50+ measurement fields), `race_results`, `orc_certificates`, `orc_snapshots`, `boat_identities`, `design_classes`, `orders`, `insight_cache`, `ingestion_log`, `cert_probe_attempts`, `survey_responses`, `admin_conversations`

Migrations: `alembic/versions/` (0001–0007). pg_trgm extension enabled for fuzzy search.

### Cron schedule

Installed via `crontab crontab.txt`. ORC scrape daily 03:00 UTC, TCC daily 06:00, SailSys results every 30 min, cert discovery & parsing weekly (Sundays), designs monthly, health check daily 09:00.
