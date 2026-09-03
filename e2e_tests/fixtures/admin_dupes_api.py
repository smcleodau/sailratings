"""E2E verification API for AD-01-14 — the duplicate-boats review queue.

Run by Playwright's ``webServer`` (see e2e_tests/playwright.dupes.config.ts)
before the frontend starts.  Serves the ``admin_dupes`` router on :4102
against a freshly-seeded temporary SQLite database carrying the acceptance
fixture from ``api/tests/test_admin_dupes.py``:

  * FIFTH AVENUE|AUS (tier B, size 2) — winner candidate 17213 with 551
    race results vs loser 18390 with 146 (697 combined),
  * FOX BAT|GBR (tier D, size 3) — the not-dupe path,
  * GREY GULL|NZL (tier B, size 2) — the skip path.

Everything is self-contained — no external DB, Stripe account or Clerk keys
required.

    python e2e_tests/fixtures/admin_dupes_api.py          # listens on 4102
    PW_API_PORT=4302 python e2e_tests/fixtures/admin_dupes_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make the API package importable (api/src) and the tests directory
# importable regardless of the caller's CWD (Playwright runs us from
# e2e_tests/).
FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "api" / "tests"))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from irc_data.api.deps import get_db  # noqa: E402
from irc_data.api.routers import admin_dupes  # noqa: E402
from test_admin_dupes import DDL, _seed  # noqa: E402

PORT = int(os.environ.get("PW_API_PORT", "4102"))
ADMIN_PASSWORD = "sailfast2026"


def build_app() -> FastAPI:
    tmpdir = tempfile.mkdtemp(prefix="ad0114_e2e_")
    db_path = Path(tmpdir) / "admin_dupes.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        _seed(conn)

    app = FastAPI(title="AD-01-14 E2E dupes API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin_dupes.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine

    @app.get("/v1/health")
    def health():  # Playwright webServer readiness probe
        return {"ok": True, "db": str(db_path)}

    @app.post("/v1/fixture/reset")
    def fixture_reset():
        """Re-seed the fixture so independent test runs start from the same
        pending queue (decisions mutate the DB across specs)."""
        with engine.begin() as conn:
            for table in (
                "dupe_review_queue", "boat_merges", "boat_not_dupe",
                "race_results", "irc_certificates", "orc_certificates",
                "tcc_snapshots", "boat_identities", "orders", "event_entries",
                "boat_events", "boat_news_mentions", "boat_news",
                "insight_cache", "boat_corrections", "boats",
            ):
                conn.execute(text(f"DELETE FROM {table}"))
            # admin_edits is created lazily by the merge routine — it only
            # exists after the first decision.
            exists = conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='admin_edits'"
                )
            ).fetchone()
            if exists:
                conn.execute(text("DELETE FROM admin_edits"))
            _seed(conn)
        return {"ok": True}

    return app


def main() -> None:
    # The router shares admin._verify_admin, which reads ADMIN_PASSWORD from
    # irc_data.api.routers.admin at request time — set both.
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    from irc_data.api.routers import admin as admin_module

    admin_module.ADMIN_PASSWORD = ADMIN_PASSWORD

    app = build_app()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
