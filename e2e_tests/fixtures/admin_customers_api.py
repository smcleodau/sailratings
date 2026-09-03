"""E2E verification API for the PAY-01-10 Admin Customers zone.

Run by Playwright's ``webServer`` (see e2e_tests/playwright.config.ts) before
the frontend starts.  Serves the ``admin_customers`` router on :4101 against
a freshly-seeded temporary SQLite database (5 users / 6 claims / 47 orders
incl. 37 abandoned — see ``admin_customers_seed.py``), with the Stripe SDK
patched to return the "live catalogue" the billing page asserts on:
``pro_monthly_gbp`` (£29) and ``pro_annual_gbp`` (£290/year) plus the
``LAUNCH20`` promo code.

Everything is self-contained — no external DB, Stripe account or Clerk keys
required, so `npm run test` works from a clean checkout.

    python e2e_tests/fixtures/admin_customers_api.py          # listens on 4101
    PW_API_PORT=4202 python e2e_tests/fixtures/admin_customers_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

from admin_customers_seed import ADMIN_PASSWORD, seed_admin_customers  # noqa: E402
from irc_data.api.deps import get_db  # noqa: E402
from irc_data.api.routers import admin_customers  # noqa: E402

PORT = int(os.environ.get("PW_API_PORT", "4101"))


# ── Fake "live" Stripe catalogue ────────────────────────────────────────────
# Mirrors _fake_stripe_payload() in api/tests/test_admin_customers.py.


def _fake_stripe_payload() -> dict:
    pro_monthly = SimpleNamespace(
        id="price_pro_m",
        product="prod_pro",
        currency="gbp",
        unit_amount=2900,
        recurring=SimpleNamespace(interval="month", interval_count=1),
        active=True,
        metadata={},
        lookup_key="pro_monthly_gbp",
    )
    pro_annual = SimpleNamespace(
        id="price_pro_y",
        product="prod_pro",
        currency="gbp",
        unit_amount=29000,
        recurring=SimpleNamespace(interval="year", interval_count=1),
        active=True,
        metadata={},
        lookup_key="pro_annual_gbp",
    )
    product_pro = SimpleNamespace(id="prod_pro", name="SailRatings Pro", metadata={})
    promo = SimpleNamespace(
        code="LAUNCH20",
        active=True,
        times_redeemed=3,
        expires_at=None,
        coupon=SimpleNamespace(percent_off=20.0, amount_off=None, currency=None),
    )
    charge = SimpleNamespace(
        id="ch_live_demo",
        amount=29000,
        currency="gbp",
        status="succeeded",
        paid=True,
        refunded=False,
        description="SailRatings Pro (annual)",
        receipt_email="alice.waters@example.com",
        customer="cus_demoAlice",
        created=1756900000,
    )
    balance = {
        "available": [{"amount": 123456, "currency": "gbp"}],
        "pending": [{"amount": 2900, "currency": "gbp"}],
    }
    return {
        "products": [product_pro],
        "prices": [pro_monthly, pro_annual],
        "promo": [promo],
        "balance": balance,
        "charges": [charge],
    }


class _List:
    """Mimics the Stripe ListObject surface used by the router."""

    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


def build_app() -> FastAPI:
    tmpdir = tempfile.mkdtemp(prefix="pay0110_e2e_")
    db_path = Path(tmpdir) / "admin_customers.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    seed_admin_customers(engine)

    app = FastAPI(title="PAY-01-10 E2E admin API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin_customers.router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: engine

    @app.get("/v1/health")
    def health():  # Playwright webServer readiness probe
        return {"ok": True, "db": str(db_path)}

    return app


def main() -> None:
    # The router reads ADMIN_PASSWORD / STRIPE_SECRET_KEY at import time and at
    # request time respectively — set both before serving.
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_e2e_fixture"
    admin_customers.ADMIN_PASSWORD = ADMIN_PASSWORD

    fake = _fake_stripe_payload()
    app = build_app()

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
