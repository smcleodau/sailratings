"""PAY-01-01: entitlement service, ``require_feature`` dependency, tier matrix.

Runs against the real Postgres (see ``conftest.py``); skipped cleanly when no
database is reachable. Tier vocabulary under test: Crew = free, Skipper = pro,
Programme = team (``PLAN_TO_TIER`` in ``services/entitlements.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from irc_data.api.deps import (
    CallerIdentity,
    get_db,
    get_optional_identity,
    install_entitlement_handlers,
    require_feature,
)
from irc_data.api.services import entitlements as ent_svc
from irc_data.api.services.entitlements import (
    ClaimCapExceeded,
    assert_can_verify_claim,
    can,
    get_entitlements,
)

NOW = datetime.now(timezone.utc)

TIER_PLAN = {"crew": None, "skipper": "pro", "programme": "team"}


# ── helpers ───────────────────────────────────────────────────────────────


@pytest.fixture()
def boats(engine) -> list[int]:
    with engine.connect() as conn:
        ids = [r[0] for r in conn.execute(text("SELECT id FROM boats ORDER BY id LIMIT 12"))]
    if len(ids) < 12:
        pytest.skip("need at least 12 boats in the database")
    return ids


def make_user(conn, clerk_id: str = "user_ent") -> int:
    return conn.execute(
        text(
            "INSERT INTO users (clerk_id, email) VALUES (:c, :e) RETURNING id"
        ),
        {"c": clerk_id, "e": f"{clerk_id}@example.com"},
    ).scalar()


def add_subscription(conn, user_id: int, plan: str, status: str = "active") -> None:
    conn.execute(
        text(
            "INSERT INTO subscriptions (user_id, stripe_subscription_id, status, plan)"
            " VALUES (:u, :sid, :s, :p)"
        ),
        {"u": user_id, "sid": f"sub_ent_{user_id}_{plan}_{status}", "s": status, "p": plan},
    )


def add_grant(conn, user_id: int, tier: str, expires_at=None) -> None:
    conn.execute(
        text(
            "INSERT INTO admin_entitlement_grants (user_id, tier, reason, granted_by, expires_at)"
            " VALUES (:u, :t, 'test', 'admin@test', :x)"
        ),
        {"u": user_id, "t": tier, "x": expires_at},
    )


def add_claim(conn, user_id: int, boat_id: int, status: str = "verified") -> None:
    conn.execute(
        text(
            "INSERT INTO boat_claims (user_id, boat_id, status) VALUES (:u, :b, :s)"
        ),
        {"u": user_id, "b": boat_id, "s": status},
    )


def add_order(conn, user_id: int, boat_id: int, status: str) -> None:
    conn.execute(
        text(
            "INSERT INTO orders (boat_id, user_id, amount_cents, status)"
            " VALUES (:b, :u, 9900, :s)"
        ),
        {"b": boat_id, "u": user_id, "s": status},
    )


def ent_for(engine, user_id: int):
    with engine.connect() as conn:
        return get_entitlements(conn, user_id)


@pytest.fixture()
def seeded(db, boats):
    """Return ``make(tier) -> (user_id, owned_ids, unowned_ids)``."""

    def make(tier: str):
        with db.begin() as conn:
            uid = make_user(conn, f"user_{tier}")
            plan = TIER_PLAN[tier]
            if plan:
                add_subscription(conn, uid, plan)
            add_claim(conn, uid, boats[0], "verified")  # owned via verified claim
            add_order(conn, uid, boats[1], "paid")  # owned via paid order
            add_claim(conn, uid, boats[2], "pending")  # NOT owned
            add_order(conn, uid, boats[3], "pending")  # NOT owned
        return uid, [boats[0], boats[1]], [boats[2], boats[3], boats[4]]

    return make


# ── tier matrix: 3 tiers × 5 cases = 15 ──────────────────────────────────

MATRIX = {
    # tier: (report owned, report not owned, simulator, fleet_compare, portfolio)
    "crew": (True, False, False, False, False),
    "skipper": (True, True, True, True, False),
    "programme": (True, True, True, True, True),
}
CASES = ["report_owned", "report_not_owned", "simulator", "fleet_compare", "programme_portfolio"]


@pytest.mark.parametrize("tier", list(MATRIX))
@pytest.mark.parametrize("case", CASES)
def test_tier_matrix(engine, seeded, tier, case):
    uid, owned, unowned = seeded(tier)
    ent = ent_for(engine, uid)
    assert ent.tier == tier
    expected = MATRIX[tier][CASES.index(case)]

    if case == "report_owned":
        assert [can(ent, "report_view", b) for b in owned] == [expected] * 2
    elif case == "report_not_owned":
        assert [can(ent, "report_view", b) for b in unowned] == [expected] * 3
    else:
        assert can(ent, case) is expected


# ── subscription status handling ─────────────────────────────────────────


def test_past_due_keeps_access(engine, db):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "pro", "past_due")
    ent = ent_for(engine, uid)
    assert (ent.tier, ent.plan, ent.plan_status, ent.source) == (
        "skipper", "pro", "past_due", "subscription",
    )
    assert can(ent, "simulator")


def test_canceled_loses_access(engine, db):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "pro", "canceled")
    ent = ent_for(engine, uid)
    assert (ent.tier, ent.plan, ent.plan_status, ent.source) == (
        "crew", "free", "canceled", "free",
    )
    assert not can(ent, "simulator")


# ── admin grants ─────────────────────────────────────────────────────────


def test_grant_overrides_subscription(engine, db):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "pro", "active")
        add_grant(conn, uid, "programme", NOW + timedelta(days=30))
    ent = ent_for(engine, uid)
    assert (ent.tier, ent.source, ent.plan_status) == ("programme", "grant", "granted")
    assert ent.boat_cap == 10
    assert can(ent, "programme_portfolio")


def test_expired_grant_ignored(engine, db):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "pro", "active")
        add_grant(conn, uid, "programme", NOW - timedelta(days=1))
    ent = ent_for(engine, uid)
    assert (ent.tier, ent.source) == ("skipper", "subscription")
    assert not can(ent, "programme_portfolio")


# ── Programme boat cap ───────────────────────────────────────────────────


def test_programme_cap_10_enforced_on_11th_claim(engine, db, boats):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "team", "active")
        for b in boats[:10]:
            assert_can_verify_claim(conn, uid)  # 1st..10th are allowed
            add_claim(conn, uid, b, "verified")
        add_claim(conn, uid, boats[10], "pending")
        with pytest.raises(ClaimCapExceeded) as exc:
            assert_can_verify_claim(conn, uid)  # the 11th is refused
        assert exc.value.cap == 10
    ent = ent_for(engine, uid)
    assert ent.boat_cap == 10 and len(ent.owned_report_boat_ids) == 10


def test_cap_not_applied_to_skipper(engine, db, boats):
    with db.begin() as conn:
        uid = make_user(conn)
        add_subscription(conn, uid, "pro", "active")
        for b in boats[:11]:
            add_claim(conn, uid, b, "verified")
        assert_can_verify_claim(conn, uid)  # no raise: Skipper is uncapped
    ent = ent_for(engine, uid)
    assert ent.boat_cap is None and len(ent.owned_report_boat_ids) == 11


# ── HTTP surface ─────────────────────────────────────────────────────────


@pytest.fixture()
def gated_client(engine, db):
    """Tiny app with one route per gate, identity taken from ``X-Test-Clerk``."""
    app = FastAPI()
    install_entitlement_handlers(app)

    @app.get("/sim")
    def sim(ent=Depends(require_feature("simulator"))):
        return {"tier": ent.tier}

    @app.get("/boats/{boat_id}/report")
    def report(boat_id: int, ent=Depends(require_feature("report_view"))):
        return {"boat_id": boat_id}

    def _identity(request: Request):
        clerk = request.headers.get("X-Test-Clerk")
        return CallerIdentity(clerk_user_id=clerk, email=None) if clerk else None

    app.dependency_overrides[get_db] = lambda: engine
    app.dependency_overrides[get_optional_identity] = _identity
    with TestClient(app) as c:
        yield c


def test_require_feature_403_body_shape(gated_client, db, boats):
    with db.begin() as conn:
        uid = make_user(conn, "user_free")
        add_claim(conn, uid, boats[0], "verified")
    resp = gated_client.get("/sim", headers={"X-Test-Clerk": "user_free"})
    assert resp.status_code == 403
    assert resp.json() == {
        "detail": "Your plan does not include 'simulator'",
        "required_tier": "skipper",
    }
    # per-boat ownership flows through the path param
    ok = gated_client.get(f"/boats/{boats[0]}/report", headers={"X-Test-Clerk": "user_free"})
    assert ok.status_code == 200
    no = gated_client.get(f"/boats/{boats[5]}/report", headers={"X-Test-Clerk": "user_free"})
    assert no.status_code == 403 and no.json()["required_tier"] == "skipper"
    # entitled caller passes
    with db.begin() as conn:
        pid = make_user(conn, "user_paid")
        add_subscription(conn, pid, "pro", "active")
    assert gated_client.get("/sim", headers={"X-Test-Clerk": "user_paid"}).json() == {
        "tier": "skipper"
    }


def test_require_feature_401_anonymous(gated_client):
    resp = gated_client.get("/sim")
    assert resp.status_code == 401


def test_users_me_carries_entitlements(engine, db, boats):
    from irc_data.api.app import app

    with db.begin() as conn:
        uid = make_user(conn, "user_me")
        add_subscription(conn, uid, "team", "active")
        add_claim(conn, uid, boats[0], "verified")

    app.dependency_overrides[get_db] = lambda: engine
    app.dependency_overrides[get_optional_identity] = lambda: CallerIdentity(
        clerk_user_id="user_me", email="user_me@example.com"
    )
    try:
        with TestClient(app) as c:
            resp = c.get("/v1/users/me")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_optional_identity, None)

    assert resp.status_code == 200
    assert resp.json()["entitlements"] == {
        "tier": "programme",
        "plan": "team",
        "plan_status": "active",
        "features": sorted(ent_svc.TIER_FEATURES["programme"]),
        "boat_cap": 10,
        "owned_report_boat_ids": [boats[0]],
        "source": "subscription",
    }
