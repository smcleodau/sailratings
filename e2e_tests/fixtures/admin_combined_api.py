"""Combined E2E verification API for the whole /admin surface.

Served by the *default* Playwright rig (see e2e_tests/playwright.config.ts)
on :4101 so every spec that runs under the default config — the PAY-01-10
Customers zone specs AND the AD-01-06 scrapers health spec — has its API
on the single base URL the frontend is pointed at
(``NEXT_PUBLIC_API_BASE``).

Background: the AD-01-06 scrapers spec originally ran only under its own
config (playwright.scrapers.config.ts), which started a scrapers-only
fixture API on :4102 and pointed the frontend at it. The default config
includes ``admin-scrapers.spec.ts`` in its ``testDir`` but only started
the customers API on :4101 — so a bare ``npx playwright test`` (what the
Gatekeeper runs) failed every scrapers test with ECONNREFUSED /404. This
combined fixture removes that split-brain:

  * one process, one port (4101), BOTH admin routers mounted —
    ``admin_customers`` (users / claims / orders / billing) and ``admin``
    (scrapers / discovery / firecrawl / chat …);
  * two independent SQLite fixture databases — the PAY-01-10 customers
    seed and the AD-01-06 scrapers ledger seed write disjoint schemas, so
    each router's ``get_db`` dependency is overridden per-route to the
    engine backed by its own seed (see ``dependency_overrides`` below);
  * the same fake "live" Stripe catalogue the PAY-01-10 spec asserts on.

The scrapers-dedicated config keeps working unchanged: it starts the
scrapers-only fixture (``admin_scrapers_api.py``) on :4102 and the
frontend on a *different* port (4203), and points the frontend at :4102
— so the scrapers spec passes identically under both configs.

    python e2e_tests/fixtures/admin_combined_api.py          # listens on 4101
    PW_API_PORT=4202 python e2e_tests/fixtures/admin_combined_api.py
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
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from admin_customers_seed import seed_admin_customers  # noqa: E402
from admin_scrapers_seed import seed_admin_scrapers  # noqa: E402
from irc_data.api.deps import get_db  # noqa: E402
from irc_data.api.routers import admin as admin_module  # noqa: E402
from irc_data.api.routers import admin_customers  # noqa: E402

# Both seeds use the same password; the combined app mirrors it into both
# routers' module-level ADMIN_PASSWORD (read at request time).
from admin_customers_seed import ADMIN_PASSWORD  # noqa: E402

PORT = int(os.environ.get("PW_API_PORT", "4101"))


def _make_engine(db_path: Path) -> Engine:
    # StaticPool: a single shared connection so the async route handlers
    # (uvicorn's anyio threadpool) never re-open the SQLite file mid-request.
    return create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def build_app() -> FastAPI:
    tmpdir = tempfile.mkdtemp(prefix="admin_e2e_")

    customers_engine = _make_engine(Path(tmpdir) / "admin_customers.db")
    seed_admin_customers(customers_engine)

    scrapers_engine = _make_engine(Path(tmpdir) / "admin_scrapers.db")
    seed_admin_scrapers(scrapers_engine)

    app = FastAPI(title="Combined E2E admin API (customers + scrapers)")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount both admin routers under /v1. Their paths are disjoint
    # (customers: users/claims/orders/billing; admin: scrapers/discovery/
    # firecrawl/chat/…), so there are no route collisions.
    app.include_router(admin_customers.router, prefix="/v1")
    app.include_router(admin_module.router, prefix="/v1")

    # Per-router database overrides: each seeded engine backs only the
    # routes of its own fixture. include_router copies the router's route
    # objects onto the app, so we match by URL path — the two routers own
    # disjoint paths (customers: users/claims/orders/billing; admin:
    # scrapers/discovery/firecrawl/chat/…), so path is an unambiguous key.
    customers_paths = {r.path for r in admin_customers.router.routes}
    scrapers_paths = {r.path for r in admin_module.router.routes}

    def _customers_db() -> Engine:
        return customers_engine

    def _scrapers_db() -> Engine:
        return scrapers_engine

    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        # Strip the /v1 mount prefix so we can match the router-relative
        # paths recorded in the *_paths sets.
        rel = path[len("/v1"):] if path.startswith("/v1") else path
        if rel in customers_paths:
            _override_route_db(route, _customers_db)
        elif rel in scrapers_paths:
            _override_route_db(route, _scrapers_db)

    @app.get("/v1/health")
    def health():  # generic liveness probe
        return {"ok": True, "db": str(Path(tmpdir))}

    # Playwright webServer readiness probe for the *combined* fixture. This
    # is deliberately distinct from /v1/health: this box also runs a live dev
    # API and a scrapers-only fixture on adjacent ports, and both serve
    # /v1/health. A stray server squatting on this port would satisfy a
    # /v1/health probe and be "reused" while serving the wrong data. This
    # ping proves the combined fixture (with the scrapers router mounted) is
    # the process actually serving here.
    @app.get("/v1/admin/scrapers/ping")
    def combined_ping():
        return {"ok": True, "fixture": "admin_combined", "db": str(Path(tmpdir))}

    return app


def _override_route_db(route, replacement) -> None:
    """Replace the ``get_db`` dependency on a single route's dependant."""
    from irc_data.api.deps import get_db as _get_db

    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return

    def _swap(dep):
        for sub in dep.dependencies:
            if sub.call is _get_db:
                sub.call = replacement
            _swap(sub)

    _swap(dependant)


def main() -> None:
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_e2e_fixture"
    admin_customers.ADMIN_PASSWORD = ADMIN_PASSWORD
    admin_module.ADMIN_PASSWORD = ADMIN_PASSWORD

    app = build_app()

    # Fake "live" Stripe catalogue (PAY-01-10 billing page asserts on it).
    from admin_customers_api import _List, _fake_stripe_payload

    fake = _fake_stripe_payload()
    from unittest.mock import patch

    with (
        patch.object(admin_customers.stripe.Product, "list", return_value=_List(fake["products"])),
        patch.object(admin_customers.stripe.Price, "list", return_value=_List(fake["prices"])),
        patch.object(admin_customers.stripe.PromotionCode, "list", return_value=_List(fake["promo"])),
        patch.object(admin_customers.stripe.Balance, "retrieve", return_value=fake["balance"]),
        patch.object(admin_customers.stripe.Charge, "list", return_value=_List(fake["charges"])),
    ):
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
