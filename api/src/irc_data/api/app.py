"""FastAPI application for the sailing rating data platform."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Override DB URL from environment if set (for Docker / Railway)
db_url = os.environ.get("DATABASE_URL")
if db_url:
    # Railway gives postgresql:// but SQLAlchemy needs postgresql+psycopg://
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    import irc_data.config as config
    config.DATABASE_URL = db_url

app = FastAPI(
    title="Sailing Rating Data API",
    description=(
        "API for IRC and ORC sailing handicap rating data, race results, "
        "fleet analytics, and AI-powered boat insights."
    ),
    version="1.0.0",
    docs_url="/v1/docs",
    redoc_url="/v1/redoc",
    openapi_url="/v1/openapi.json",
)

# CORS — derived from ENVIRONMENT (override via CORS_ORIGINS).
from irc_data.env import CORS_ORIGINS  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routers
from irc_data.api.routers import adjudication, admin, admin_overview, admin_tables, analytics, boats, checkout, corrections, data_health, designs, events, fleet, health, insights, pipeline, quality_gates, reconciliation, reports, run_ledger, scrapers, search, sources, stats, surveys, swarm, what_if  # noqa: E402

app.include_router(swarm.router, prefix="/v1/swarm", tags=["Swarm"])
app.include_router(search.router, prefix="/v1", tags=["Search"])
app.include_router(boats.router, prefix="/v1", tags=["Boats"])
app.include_router(designs.router, prefix="/v1", tags=["Designs"])
app.include_router(fleet.router, prefix="/v1", tags=["Fleet"])
app.include_router(events.router, prefix="/v1", tags=["Events"])
app.include_router(analytics.router, prefix="/v1", tags=["Analytics"])
app.include_router(what_if.router, prefix="/v1", tags=["Analytics"])
app.include_router(insights.router, prefix="/v1", tags=["AI Insights"])
app.include_router(checkout.router, prefix="/v1", tags=["Checkout"])
app.include_router(reports.router, prefix="/v1", tags=["Reports"])
app.include_router(health.router, prefix="/v1", tags=["Health"])
app.include_router(pipeline.router, prefix="/v1", tags=["Pipeline"])
app.include_router(surveys.router, prefix="/v1", tags=["Surveys"])
app.include_router(admin.router, prefix="/v1", tags=["Admin"])
app.include_router(admin_overview.router, prefix="/v1", tags=["Admin"])
app.include_router(scrapers.router, prefix="/v1", tags=["Admin"])
app.include_router(run_ledger.router, prefix="/v1", tags=["Admin"])
app.include_router(reconciliation.router, prefix="/v1", tags=["Admin"])
app.include_router(adjudication.router, prefix="/v1", tags=["Admin"])
app.include_router(quality_gates.router, prefix="/v1", tags=["Admin"])
app.include_router(data_health.router, prefix="/v1", tags=["Admin"])
app.include_router(admin_tables.router, prefix="/v1")
app.include_router(corrections.router, prefix="/v1")
app.include_router(sources.router, prefix="/v1", tags=["Sources"])
app.include_router(stats.router, prefix="/v1")


@app.get("/", include_in_schema=False)
def root():
    return {
        "name": "Sailing Rating Data API",
        "version": "1.0.0",
        "docs": "/v1/docs",
    }
