"""E2E verification API for the AD-01-06 Scrapers health page.

Run by Playwright's ``webServer`` before the frontend starts. Serves the
real ``admin`` router's scrapers endpoints (GET /v1/admin/scrapers and
GET /v1/admin/scrapers/{source}/runs) on :4102 against a freshly-seeded
SQLite ledger fixture (see ``admin_scrapers_seed.py``).

Everything is self-contained — no external DB, Temporal, or Clerk keys
required, so `npm run test` works from a clean checkout.

    python e2e_tests/fixtures/admin_scrapers_api.py          # listens on 4102
    PW_SCRAPERS_API_PORT=4203 python e2e_tests/fixtures/admin_scrapers_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make the API package importable (api/src) and this directory importable
# regardless of the caller's CWD (Playwright runs us from e2e_tests/).
FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "api" / "src"))
sys.path.insert(0, str(FIXTURE_DIR))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from admin_scrapers_seed import ADMIN_PASSWORD, seed_admin_scrapers  # noqa: E402
from irc_data.api.deps import get_db  # noqa: E402
from irc_data.api.routers import admin as admin_module  # noqa: E402

PORT = int(os.environ.get("PW_SCRAPERS_API_PORT", "4102"))


def build_app() -> FastAPI:
    tmpdir = tempfile.mkdtemp(prefix="ad0106_e2e_")
    db_path = Path(tmpdir) / "admin_scrapers.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    seed_admin_scrapers(engine)

    app = FastAPI(title="AD-01-06 E2E scrapers API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount only the two scrapers endpoints the page consumes — plus a
    # health probe for Playwright's webServer readiness check.
    app.include_router(admin_module.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine

    @app.get("/v1/health")
    def health():  # generic liveness probe
        return {"ok": True, "db": str(db_path)}

    # Playwright webServer readiness probe for the *scrapers* fixture. This
    # is deliberately distinct from /v1/health: the customers fixture also
    # serves /v1/health on an adjacent port, so a stray customers server
    # squatting on this port would satisfy a /v1/health probe and be
    # "reused" while serving the wrong endpoints (/admin/scrapers -> 404).
    # This ping proves the scrapers router is actually mounted here.
    @app.get("/v1/admin/scrapers/ping")
    def scrapers_ping():
        return {"ok": True, "fixture": "admin_scrapers", "db": str(db_path)}

    return app


def main() -> None:
    # The router reads ADMIN_PASSWORD at request time via _verify_admin.
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    admin_module.ADMIN_PASSWORD = ADMIN_PASSWORD

    app = build_app()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
