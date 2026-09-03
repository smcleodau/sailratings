"""Admin Customers zone — users / claims / orders / billing (PAY-01-10).

Endpoints (all behind the shared Bearer admin password):

    GET  /v1/admin/users                 q / plan / role / claims=pending /
                                         cursor pagination, from v_admin_users
    GET  /v1/admin/users/{id}            detail incl. boats, orders, claims
    POST /v1/admin/users/{id}/role       set role (customer | staff | admin)
    POST /v1/admin/claims/{id}/verify    verify a boat claim
    POST /v1/admin/claims/{id}/reject    reject a boat claim
    GET  /v1/admin/orders                every order with honest status
                                         (abandoned when no Stripe session)
    POST /v1/admin/orders/{id}/regenerate  re-run report generation
    GET  /v1/admin/billing               Stripe catalogue by lookup_key,
                                         promo codes, balance, last 20 charges
                                         (60 s cache)

Honest status rule for orders: a row whose Stripe checkout session was never
created (``stripe_session_id IS NULL``) is *abandoned* — the customer left
before paying. ``pending`` is reserved for rows that reached Stripe and may
still complete via webhook.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin", "Customers"])

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

USER_ROLES = ("customer", "staff", "admin")
PLAN_SLUGS = ("free", "skipper", "pro")
CLAIM_STATUSES = ("pending", "verified", "rejected")
ORDER_STATUSES = ("abandoned", "pending", "paid", "generated", "error")

_USERS_PAGE_SIZE = 50
_BILLING_CACHE_TTL_S = 60.0


# ── Auth ──────────────────────────────────────────────────────────────────


def _verify_admin(authorization: str | None) -> None:
    expected = f"Bearer {ADMIN_PASSWORD}"
    if not ADMIN_PASSWORD or not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Serialisation helpers ─────────────────────────────────────────────────


def _ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _money(amount_cents: Any, currency: Any) -> Optional[dict[str, Any]]:
    if amount_cents is None:
        return None
    return {
        "amount_cents": int(amount_cents),
        "currency": (currency or "usd").lower(),
    }


def _get(row: Any, key: str) -> Any:
    """Mapping- or attribute-style access (SQLite returns text for UUIDs)."""
    if isinstance(row, dict) or hasattr(row, "keys"):
        return row[key]
    return getattr(row, key)


def _user_row(row: Any) -> dict[str, Any]:
    stripe_customer_id = _get(row, "stripe_customer_id")
    return {
        "id": str(_get(row, "id")),
        "email": _get(row, "email"),
        "full_name": _get(row, "full_name"),
        "role": _get(row, "role"),
        "plan": _get(row, "plan"),
        "subscription_status": _get(row, "subscription_status"),
        "stripe_customer_id": stripe_customer_id,
        "boats_claimed": int(_get(row, "boats_claimed") or 0),
        "pending_claims": int(_get(row, "pending_claims") or 0),
        "reports_bought": int(_get(row, "reports_bought") or 0),
        "total_spend": _money(_get(row, "total_spend_cents"), _get(row, "last_order_currency")),
        "joined_at": _ts(_get(row, "joined_at")),
        "last_seen_at": _ts(_get(row, "last_seen_at")),
        "stripe_dashboard_url": (
            f"https://dashboard.stripe.com/customers/{stripe_customer_id}"
            if stripe_customer_id
            else None
        ),
    }


def _claim_row(row: Any) -> dict[str, Any]:
    return {
        "id": _get(row, "id"),
        "user_id": str(_get(row, "user_id")),
        "boat_id": _get(row, "boat_id"),
        "boat_name": _get(row, "boat_name"),
        "sail_number": _get(row, "sail_number"),
        "status": _get(row, "status"),
        "evidence": _get(row, "evidence"),
        "verified_by": _get(row, "verified_by"),
        "verified_at": _ts(_get(row, "verified_at")),
        "created_at": _ts(_get(row, "created_at")),
    }


def _effective_status(stored: str, stripe_session_id: str | None) -> str:
    """Honest status: an order that never reached Stripe is abandoned."""
    if stored == "pending" and not stripe_session_id:
        return "abandoned"
    return stored


def _order_row(row: Any) -> dict[str, Any]:
    stored = _get(row, "status")
    session_id = _get(row, "stripe_session_id")
    payment_intent = _get(row, "stripe_payment_intent")
    status = _effective_status(stored, session_id)
    order_token = _get(row, "order_token")
    user_id = _get(row, "user_id")
    return {
        "id": _get(row, "id"),
        "order_token": str(order_token),
        "status": status,
        "stored_status": stored,
        "email": _get(row, "email"),
        "user_id": str(user_id) if user_id else None,
        "boat_id": _get(row, "boat_id"),
        "boat_name": _get(row, "boat_name"),
        "amount": _money(_get(row, "amount_cents"), _get(row, "currency")),
        "stripe_session_id": session_id,
        "stripe_payment_intent": payment_intent,
        "stripe_dashboard_url": (
            f"https://dashboard.stripe.com/payments/{payment_intent}"
            if payment_intent
            else None
        ),
        "search_query": _get(row, "search_query"),
        "report_url": (
            f"/report/{order_token}" if status in ("paid", "generated") else None
        ),
        "created_at": _ts(_get(row, "created_at")),
        "paid_at": _ts(_get(row, "paid_at")),
        "report_generated_at": _ts(_get(row, "report_generated_at")),
        "email_sent_at": _ts(_get(row, "email_sent_at")),
    }


# ── Users ─────────────────────────────────────────────────────────────────


@router.get("/users")
def list_users(
    q: str | None = Query(default=None, description="Search email / name / boat"),
    plan: str | None = Query(default=None),
    role: str | None = Query(default=None),
    claims: str | None = Query(default=None, description="'pending' filters to pending claims"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=_USERS_PAGE_SIZE, ge=1, le=200),
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """List customers from v_admin_users with search, filters and cursor paging."""
    _verify_admin(authorization)

    if plan is not None and plan not in PLAN_SLUGS:
        raise HTTPException(status_code=422, detail=f"Unknown plan '{plan}'")
    if role is not None and role not in USER_ROLES:
        raise HTTPException(status_code=422, detail=f"Unknown role '{role}'")
    if claims is not None and claims != "pending":
        raise HTTPException(status_code=422, detail="claims must be 'pending'")

    clauses: list[str] = []
    params: dict[str, Any] = {}

    if q:
        clauses.append(
            """(
                lower(u.email) LIKE lower(:q)
                OR lower(COALESCE(u.full_name, '')) LIKE lower(:q)
                OR EXISTS (
                    SELECT 1 FROM boat_claims c
                    JOIN boats b ON b.id = c.boat_id
                    WHERE c.user_id = u.id
                      AND (lower(b.boat_name) LIKE lower(:q) OR lower(b.sail_number) LIKE lower(:q))
                )
            )"""
        )
        params["q"] = f"%{q}%"
    if plan:
        clauses.append("u.plan = :plan")
        params["plan"] = plan
    if role:
        clauses.append("u.role = :role")
        params["role"] = role
    if claims == "pending":
        clauses.append("u.pending_claims > 0")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT count(*) FROM v_admin_users u {where}"), params
        ).scalar_one()
        rows = (
            conn.execute(
                text(
                    f"""
                    SELECT * FROM v_admin_users u
                    {where}
                    ORDER BY u.joined_at DESC NULLS LAST, u.id
                    LIMIT :limit OFFSET :cursor
                    """
                ),
                {**params, "limit": limit, "cursor": cursor},
            )
            .mappings()
            .all()
        )

    users = [_user_row(r) for r in rows]
    next_cursor = cursor + limit if cursor + limit < int(total) else None
    return {"users": users, "next_cursor": next_cursor, "total": int(total)}


@router.get("/users/{user_id}")
def get_user(
    user_id: int,
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """Full customer dossier: profile, boats, orders, claims, money."""
    _verify_admin(authorization)

    with engine.connect() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM v_admin_users WHERE id = :id"),
                {"id": user_id},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        boats = (
            conn.execute(
                text(
                    """
                    SELECT c.id AS claim_id, c.status, c.evidence, c.verified_at,
                           c.created_at AS claimed_at,
                           b.id AS boat_id, b.boat_name, b.sail_number,
                           COALESCE(b.design_canonical, b.design) AS design,
                           b.country
                    FROM boat_claims c
                    JOIN boats b ON b.id = c.boat_id
                    WHERE c.user_id = :id
                    ORDER BY c.created_at DESC
                    """
                ),
                {"id": user_id},
            )
            .mappings()
            .all()
        )

        orders = (
            conn.execute(
                text(
                    """
                    SELECT o.*, b.boat_name
                    FROM orders o
                    LEFT JOIN boats b ON b.id = o.boat_id
                    WHERE o.user_id = :id OR lower(o.email) = lower(:email)
                    ORDER BY o.created_at DESC
                    """
                ),
                {"id": user_id, "email": _get(row, "email")},
            )
            .mappings()
            .all()
        )

        claims = (
            conn.execute(
                text(
                    """
                    SELECT c.*, b.boat_name, b.sail_number
                    FROM boat_claims c
                    JOIN boats b ON b.id = c.boat_id
                    WHERE c.user_id = :id
                    ORDER BY c.created_at DESC
                    """
                ),
                {"id": user_id},
            )
            .mappings()
            .all()
        )

    return {
        "user": _user_row(row),
        "boats": [
            {
                "claim_id": _get(b, "claim_id"),
                "boat_id": _get(b, "boat_id"),
                "boat_name": _get(b, "boat_name"),
                "sail_number": _get(b, "sail_number"),
                "design": _get(b, "design"),
                "country": _get(b, "country"),
                "status": _get(b, "status"),
                "evidence": _get(b, "evidence"),
                "claimed_at": _ts(_get(b, "claimed_at")),
                "verified_at": _ts(_get(b, "verified_at")),
            }
            for b in boats
        ],
        "orders": [_order_row(o) for o in orders],
        "claims": [_claim_row(c) for c in claims],
    }


class RoleUpdate(BaseModel):
    role: str


@router.post("/users/{user_id}/role")
def set_user_role(
    user_id: int,
    body: RoleUpdate,
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """Set a user's role (customer | staff | admin)."""
    _verify_admin(authorization)

    role = body.role.strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of {', '.join(USER_ROLES)}",
        )

    with engine.begin() as conn:
        updated = (
            conn.execute(
                text(
                    """
                    UPDATE users SET role = :role, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    RETURNING id, role
                    """
                ),
                {"role": role, "id": user_id},
            )
            .mappings()
            .first()
        )

    if not updated:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True, "user_id": str(updated["id"]), "role": updated["role"]}


# ── Claims ────────────────────────────────────────────────────────────────


class ClaimDecision(BaseModel):
    reviewer: str | None = None
    reason: str | None = None


def _decide_claim(
    claim_id: int,
    decision: str,
    body: ClaimDecision,
    engine: Engine,
) -> dict[str, Any]:
    with engine.begin() as conn:
        updated = (
            conn.execute(
                text(
                    """
                    UPDATE boat_claims
                    SET status = :status,
                        verified_by = :reviewer,
                        verified_at = CURRENT_TIMESTAMP,
                        evidence = CASE
                            WHEN CAST(:reason AS TEXT) IS NULL THEN evidence
                            WHEN evidence IS NULL THEN CAST(:reason AS TEXT)
                            ELSE evidence || ' — ' || CAST(:reason AS TEXT)
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND status = 'pending'
                    RETURNING id, user_id, boat_id, status
                    """
                ),
                {
                    "status": decision,
                    "reviewer": body.reviewer or "admin",
                    "reason": body.reason,
                    "id": claim_id,
                },
            )
            .mappings()
            .first()
        )

        if not updated:
            existing = (
                conn.execute(
                    text("SELECT status FROM boat_claims WHERE id = :id"),
                    {"id": claim_id},
                )
                .mappings()
                .first()
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Claim not found")
            raise HTTPException(
                status_code=409,
                detail=f"Claim already {_get(existing, 'status')}",
            )

    return {
        "ok": True,
        "claim": {
            "id": updated["id"],
            "user_id": str(updated["user_id"]),
            "boat_id": updated["boat_id"],
            "status": updated["status"],
        },
    }


@router.post("/claims/{claim_id}/verify")
def verify_claim(
    claim_id: int,
    body: ClaimDecision | None = None,
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """Verify a pending boat claim (the user owns / manages this boat)."""
    _verify_admin(authorization)
    return _decide_claim(claim_id, "verified", body or ClaimDecision(), engine)


@router.post("/claims/{claim_id}/reject")
def reject_claim(
    claim_id: int,
    body: ClaimDecision | None = None,
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """Reject a pending boat claim."""
    _verify_admin(authorization)
    return _decide_claim(claim_id, "rejected", body or ClaimDecision(), engine)


# ── Orders ────────────────────────────────────────────────────────────────


@router.get("/orders")
def list_orders(
    status: str | None = Query(default=None, description="Filter by effective status"),
    q: str | None = Query(default=None, description="Search email / boat / token"),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """All orders with honest status — 'abandoned' when no Stripe session."""
    _verify_admin(authorization)

    if status is not None and status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown status '{status}'")

    clauses: list[str] = []
    params: dict[str, Any] = {}

    if q:
        clauses.append(
            "(lower(o.email) LIKE lower(:q) OR lower(b.boat_name) LIKE lower(:q) OR lower(CAST(o.order_token AS TEXT)) LIKE lower(:q))"
        )
        params["q"] = f"%{q}%"

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    base = f"FROM orders o LEFT JOIN boats b ON b.id = o.boat_id {where}"

    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    f"SELECT o.*, b.boat_name {base} "
                    "ORDER BY o.created_at DESC, o.id"
                ),
                params,
            )
            .mappings()
            .all()
        )

    orders = [_order_row(r) for r in rows]

    if status:
        orders = [o for o in orders if o["status"] == status]

    total = len(orders)
    counts: dict[str, int] = {}
    for o in orders:
        counts[o["status"]] = counts.get(o["status"], 0) + 1

    page = orders[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < total else None
    return {
        "orders": page,
        "next_cursor": next_cursor,
        "total": total,
        "status_counts": counts,
    }


@router.post("/orders/{order_id}/regenerate")
def regenerate_order(
    order_id: int,
    authorization: str | None = Header(default=None),
    engine: Engine = Depends(get_db),
):
    """Re-run report generation for a paid order (PDF + email)."""
    _verify_admin(authorization)

    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    """
                    UPDATE orders
                    SET status = 'paid',
                        report_markdown = NULL,
                        report_analytics = NULL,
                        report_generated_at = NULL
                    WHERE id = :id
                      AND status IN ('paid', 'generated', 'error')
                      AND stripe_session_id IS NOT NULL
                    RETURNING id, order_token
                    """
                ),
                {"id": order_id},
            )
            .mappings()
            .first()
        )

        if not row:
            existing = (
                conn.execute(
                    text("SELECT status, stripe_session_id FROM orders WHERE id = :id"),
                    {"id": order_id},
                )
                .mappings()
                .first()
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Order not found")
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only paid orders can be regenerated "
                    f"(status={_effective_status(_get(existing, 'status'), _get(existing, 'stripe_session_id'))})"
                ),
            )

    # Fire-and-forget worker thread — mirrors checkout's BackgroundTasks path
    # without tying the admin HTTP call to a minutes-long PDF build.
    from irc_data.api.routers.checkout import _generate_and_deliver

    thread = threading.Thread(
        target=_generate_and_deliver,
        args=(engine, int(row["id"])),
        daemon=True,
        name=f"regen-order-{row['id']}",
    )
    thread.start()

    return {
        "ok": True,
        "order_id": int(row["id"]),
        "order_token": str(row["order_token"]),
        "status": "paid",
        "message": "Report regeneration started",
    }


# ── Billing ───────────────────────────────────────────────────────────────

_billing_cache_lock = threading.Lock()
_billing_cache: dict[str, Any] = {"fetched_at": 0.0, "payload": None}


def _sdk_get(obj: Any, key: str) -> Any:
    """Stripe StripeObject-safe accessor — SDK objects raise KeyError on
    missing keys via __getitem__ and their .get() collides with the internal
    HTTP GET, so go through getattr with a dict fallback."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return getattr(obj, key)
    except (AttributeError, KeyError):
        return None

def _lookup_key(price: Any, product: Any) -> Optional[str]:
    """Best-effort lookup_key for a price: Stripe's native lookup_key first,
    then price/product metadata, then a slug derived from the product name
    and billing interval (e.g. 'pro_monthly')."""
    try:
        key = _sdk_get(price, "lookup_key")
        if key:
            return key
        md = _sdk_get(price, "metadata") or {}
        key = md.get("lookup_key")
        if key:
            return key
        prod_md = _sdk_get(product, "metadata") or {}
        key = prod_md.get("lookup_key")
        if key:
            return key
        name = (_sdk_get(product, "name") or "").lower()
        recurring = _sdk_get(price, "recurring")
        interval = _sdk_get(recurring, "interval") if recurring else None
        tier = "pro" if "pro" in name else "skipper" if "skipper" in name else None
        if tier and interval:
            cadence = "annual" if interval == "year" else "monthly" if interval == "month" else interval
            return f"{tier}_{cadence}"
    except Exception:  # pragma: no cover - defensive against SDK shape drift
        pass
    return None


def _serialise_price(price: Any, product: Any) -> dict[str, Any]:
    recurring = _sdk_get(price, "recurring")
    return {
        "price_id": _sdk_get(price, "id"),
        "product_id": _sdk_get(price, "product"),
        "product_name": _sdk_get(product, "name"),
        "lookup_key": _lookup_key(price, product),
        "unit_amount": _money(_sdk_get(price, "unit_amount"), _sdk_get(price, "currency")),
        "recurring": (
            {
                "interval": _sdk_get(recurring, "interval"),
                "interval_count": _sdk_get(recurring, "interval_count"),
            }
            if recurring
            else None
        ),
        "active": bool(_sdk_get(price, "active") if _sdk_get(price, "active") is not None else True),
        "stripe_dashboard_url": f"https://dashboard.stripe.com/prices/{_sdk_get(price, 'id')}",
    }


def _fetch_billing() -> dict[str, Any]:
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

    products = {_sdk_get(p, "id"): p for p in stripe.Product.list(limit=100, active=True).auto_paging_iter()}
    prices = list(stripe.Price.list(limit=100, active=True).auto_paging_iter())
    promo = list(stripe.PromotionCode.list(limit=100, active=True).auto_paging_iter())
    balance = stripe.Balance.retrieve()
    charges = list(stripe.Charge.list(limit=20).auto_paging_iter())

    catalogue = [_serialise_price(p, products.get(_sdk_get(p, "product"))) for p in prices]
    catalogue.sort(key=lambda c: (c["lookup_key"] or "", c["price_id"]))

    promo_codes = []
    for pc in promo:
        try:
            coupon = _sdk_get(pc, "coupon")
            promo_codes.append(
                {
                    "code": _sdk_get(pc, "code"),
                    "active": bool(_sdk_get(pc, "active") if _sdk_get(pc, "active") is not None else True),
                    "percent_off": _sdk_get(coupon, "percent_off"),
                    "amount_off": _money(
                        _sdk_get(coupon, "amount_off"),
                        _sdk_get(coupon, "currency"),
                    ),
                    "times_redeemed": _sdk_get(pc, "times_redeemed"),
                    "expires_at": _sdk_get(pc, "expires_at"),
                }
            )
        except Exception:
            continue

    last_charges = []
    for ch in charges:
        last_charges.append(
            {
                "id": _sdk_get(ch, "id"),
                "amount": _money(_sdk_get(ch, "amount"), _sdk_get(ch, "currency")),
                "status": _sdk_get(ch, "status"),
                "paid": bool(_sdk_get(ch, "paid")),
                "refunded": bool(_sdk_get(ch, "refunded")),
                "description": _sdk_get(ch, "description"),
                "receipt_email": _sdk_get(ch, "receipt_email"),
                "customer_id": _sdk_get(ch, "customer"),
                "created": _sdk_get(ch, "created"),
                "stripe_dashboard_url": f"https://dashboard.stripe.com/payments/{_sdk_get(ch, 'id')}",
            }
        )

    return {
        "configured": True,
        "catalogue": catalogue,
        "promo_codes": promo_codes,
        "balance": {
            "available": [
                _money(_sdk_get(b, "amount"), _sdk_get(b, "currency"))
                for b in (_sdk_get(balance, "available") or [])
            ],
            "pending": [
                _money(_sdk_get(b, "amount"), _sdk_get(b, "currency"))
                for b in (_sdk_get(balance, "pending") or [])
            ],
        },
        "last_charges": last_charges,
    }


@router.get("/billing")
def get_billing(
    authorization: str | None = Header(default=None),
):
    """Stripe catalogue by lookup_key, promo codes, balance, last 20 charges.

    Cached for 60 s — the Stripe dashboard is the source of truth and the
    admin page shouldn't hammer the API on every refresh.
    """
    _verify_admin(authorization)

    if not os.environ.get("STRIPE_SECRET_KEY"):
        return {
            "configured": False,
            "catalogue": [],
            "promo_codes": [],
            "balance": {"available": [], "pending": []},
            "last_charges": [],
        }

    now = time.monotonic()
    with _billing_cache_lock:
        cached = _billing_cache["payload"]
        if cached is not None and now - _billing_cache["fetched_at"] < _BILLING_CACHE_TTL_S:
            return {**cached, "cached": True}

    try:
        payload = _fetch_billing()
    except stripe.StripeError as e:
        logger.error("Stripe billing fetch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")

    with _billing_cache_lock:
        _billing_cache["payload"] = payload
        _billing_cache["fetched_at"] = now

    return {**payload, "cached": False}
