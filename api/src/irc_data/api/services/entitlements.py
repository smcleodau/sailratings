"""Entitlement service (PAY-01-01) — "can user U do F for boat B?".

Every gated route asks this one module. Tier state is derived **only** from
server-side truth, never from anything the client sends:

* ``subscriptions`` (Stripe mirror) with status in ``trialing/active/past_due``
* ``admin_entitlement_grants`` (unexpired; an admin override beats Stripe)
* ``orders`` (paid) and ``boat_claims`` (verified) for per-boat report access

Tier vocabulary (decided on the card, SPEC-23 §4): product tier names live in
exactly one place — :data:`PLAN_TO_TIER` — and map from the Stripe plan code
(the ``lookup_key`` prefix written by the webhook)::

    Crew      = free
    Skipper   = pro   (pro_monthly_gbp / pro_annual_gbp)
    Programme = team  (team_monthly_gbp, quantity = seats)  [enterprise too]

Tier constants are lower-case (``"crew" | "skipper" | "programme"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection

# ── Vocabulary: the single place tier names and plan codes meet ──────────

TIER_CREW = "crew"
TIER_SKIPPER = "skipper"
TIER_PROGRAMME = "programme"

#: Lowest → highest. Used for "highest tier wins" and ``required_tier``.
TIER_ORDER: tuple[str, ...] = (TIER_CREW, TIER_SKIPPER, TIER_PROGRAMME)

#: Stripe plan code (``subscriptions.plan`` / lookup_key prefix) → product tier.
#: ``premium`` is the legacy pre-SPEC-23 plan code still present in recorded
#: fixtures; it is treated as Skipper.
PLAN_TO_TIER: dict[str, str] = {
    "free": TIER_CREW,
    "pro": TIER_SKIPPER,
    "premium": TIER_SKIPPER,
    "team": TIER_PROGRAMME,
    "enterprise": TIER_PROGRAMME,
}

#: Canonical plan code reported for each tier (what ``EntitlementV1.plan`` holds
#: when the tier comes from a grant rather than a Stripe plan).
TIER_TO_PLAN: dict[str, str] = {
    TIER_CREW: "free",
    TIER_SKIPPER: "pro",
    TIER_PROGRAMME: "team",
}

#: Subscription statuses that keep paid access. ``past_due`` keeps access
#: (Stripe is still retrying); ``canceled``/``unpaid``/``incomplete*`` do not.
ACTIVE_SUBSCRIPTION_STATUSES: tuple[str, ...] = ("trialing", "active", "past_due")

#: Order statuses that mean the report was paid for.
PAID_ORDER_STATUSES: tuple[str, ...] = ("paid", "generated")

# ── Features ─────────────────────────────────────────────────────────────

FEATURE_REPORT_VIEW = "report_view"
FEATURE_SIMULATOR = "simulator"
FEATURE_FLEET_COMPARE = "fleet_compare"
FEATURE_PROGRAMME_PORTFOLIO = "programme_portfolio"

#: Features enabled by tier. Crew has none by tier: it can only open reports
#: for boats it owns (paid order or verified claim), see :func:`can`.
TIER_FEATURES: dict[str, frozenset[str]] = {
    TIER_CREW: frozenset(),
    TIER_SKIPPER: frozenset(
        {FEATURE_REPORT_VIEW, FEATURE_SIMULATOR, FEATURE_FLEET_COMPARE}
    ),
    TIER_PROGRAMME: frozenset(
        {
            FEATURE_REPORT_VIEW,
            FEATURE_SIMULATOR,
            FEATURE_FLEET_COMPARE,
            FEATURE_PROGRAMME_PORTFOLIO,
        }
    ),
}

ALL_FEATURES: frozenset[str] = frozenset().union(*TIER_FEATURES.values())

#: Verified-claim cap per tier; ``None`` = uncapped. Programme = 10 boats.
PROGRAMME_BOAT_CAP = 10
TIER_BOAT_CAP: dict[str, Optional[int]] = {
    TIER_CREW: None,
    TIER_SKIPPER: None,
    TIER_PROGRAMME: PROGRAMME_BOAT_CAP,
}

SOURCE_SUBSCRIPTION = "subscription"
SOURCE_GRANT = "grant"
SOURCE_FREE = "free"


@dataclass(frozen=True)
class EntitlementV1:
    """What a user is entitled to, computed server-side."""

    tier: str = TIER_CREW
    plan: str = "free"
    plan_status: str = "none"
    features: frozenset[str] = field(default_factory=frozenset)
    boat_cap: Optional[int] = None
    owned_report_boat_ids: tuple[int, ...] = ()
    source: str = SOURCE_FREE

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (features sorted for a stable payload)."""
        return {
            "tier": self.tier,
            "plan": self.plan,
            "plan_status": self.plan_status,
            "features": sorted(self.features),
            "boat_cap": self.boat_cap,
            "owned_report_boat_ids": list(self.owned_report_boat_ids),
            "source": self.source,
        }


class ClaimCapExceeded(Exception):
    """Raised when verifying another claim would exceed the tier's boat cap."""

    def __init__(self, cap: int, tier: str) -> None:
        super().__init__(f"{tier} tier is limited to {cap} verified boats")
        self.cap = cap
        self.tier = tier


# ── Pure helpers ─────────────────────────────────────────────────────────


def tier_for_plan(plan: Optional[str]) -> str:
    """Map a Stripe plan code to a product tier; unknown plans are Crew."""
    return PLAN_TO_TIER.get((plan or "").strip().lower(), TIER_CREW)


def _tier_rank(tier: str) -> int:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else 0


def required_tier(feature: str) -> Optional[str]:
    """Lowest tier whose feature set includes ``feature`` (``None`` if unknown)."""
    for tier in TIER_ORDER:
        if feature in TIER_FEATURES[tier]:
            return tier
    return None


def build_entitlement(
    *,
    tier: str,
    plan: str,
    plan_status: str,
    source: str,
    owned_report_boat_ids: tuple[int, ...] = (),
) -> EntitlementV1:
    return EntitlementV1(
        tier=tier,
        plan=plan,
        plan_status=plan_status,
        features=TIER_FEATURES[tier],
        boat_cap=TIER_BOAT_CAP[tier],
        owned_report_boat_ids=owned_report_boat_ids,
        source=source,
    )


def free_entitlements() -> EntitlementV1:
    """Entitlements for anyone we know nothing about (e.g. no users row)."""
    return build_entitlement(
        tier=TIER_CREW, plan="free", plan_status="none", source=SOURCE_FREE
    )


def can(
    ent: EntitlementV1, feature: str, boat_id: Optional[int] = None
) -> bool:
    """Can the holder of ``ent`` use ``feature`` (for ``boat_id``, if given)?

    ``report_view`` is also granted per boat: a boat the user has a paid order
    or a verified claim for opens regardless of tier.
    """
    if feature in ent.features:
        return True
    if feature == FEATURE_REPORT_VIEW and boat_id is not None:
        return boat_id in ent.owned_report_boat_ids
    return False


# ── DB-backed computation ────────────────────────────────────────────────


def _owned_report_boat_ids(conn: Connection, user_id: int) -> tuple[int, ...]:
    rows = conn.execute(
        text(
            """
            SELECT boat_id FROM orders
             WHERE user_id = :uid AND status IN :paid
            UNION
            SELECT boat_id FROM boat_claims
             WHERE user_id = :uid AND status = 'verified'
            ORDER BY boat_id
            """
        ).bindparams(bindparam("paid", expanding=True)),
        {"uid": user_id, "paid": list(PAID_ORDER_STATUSES)},
    ).all()
    return tuple(int(r[0]) for r in rows)


def _active_grant_tier(conn: Connection, user_id: int) -> Optional[str]:
    rows = conn.execute(
        text(
            """
            SELECT tier FROM admin_entitlement_grants
             WHERE user_id = :uid
               AND (expires_at IS NULL OR expires_at > now())
            """
        ),
        {"uid": user_id},
    ).all()
    tiers = [r[0] for r in rows if r[0] in TIER_ORDER]
    if not tiers:
        return None
    return max(tiers, key=_tier_rank)


def _subscription_state(
    conn: Connection, user_id: int
) -> tuple[Optional[tuple[str, str, str]], str]:
    """Return ``((tier, plan, status) of best live subscription | None, latest status)``."""
    rows = conn.execute(
        text(
            """
            SELECT plan, status FROM subscriptions
             WHERE user_id = :uid
             ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
            """
        ),
        {"uid": user_id},
    ).all()
    latest_status = (rows[0][1] or "none") if rows else "none"
    best: Optional[tuple[str, str, str]] = None
    for plan, status in rows:
        if status not in ACTIVE_SUBSCRIPTION_STATUSES:
            continue
        tier = tier_for_plan(plan)
        if best is None or _tier_rank(tier) > _tier_rank(best[0]):
            best = (tier, (plan or "").strip().lower() or TIER_TO_PLAN[tier], status)
    return best, latest_status


def get_entitlements(conn: Connection, user_id: int) -> EntitlementV1:
    """Compute the entitlements for ``user_id`` from the database.

    Precedence: unexpired admin grant > live subscription > free. A grant
    always wins (it is an explicit admin decision); an expired grant is
    ignored. Subscription states ``trialing``/``active``/``past_due`` keep
    access; anything else (``canceled`` …) drops to Crew.
    """
    owned = _owned_report_boat_ids(conn, user_id)
    sub, latest_status = _subscription_state(conn, user_id)

    grant_tier = _active_grant_tier(conn, user_id)
    if grant_tier is not None:
        return build_entitlement(
            tier=grant_tier,
            plan=TIER_TO_PLAN[grant_tier],
            plan_status="granted",
            source=SOURCE_GRANT,
            owned_report_boat_ids=owned,
        )

    if sub is not None:
        tier, plan, status = sub
        return build_entitlement(
            tier=tier,
            plan=plan,
            plan_status=status,
            source=SOURCE_SUBSCRIPTION,
            owned_report_boat_ids=owned,
        )

    return build_entitlement(
        tier=TIER_CREW,
        plan="free",
        plan_status=latest_status,
        source=SOURCE_FREE,
        owned_report_boat_ids=owned,
    )


def count_verified_claims(conn: Connection, user_id: int) -> int:
    return int(
        conn.execute(
            text(
                "SELECT count(*) FROM boat_claims"
                " WHERE user_id = :uid AND status = 'verified'"
            ),
            {"uid": user_id},
        ).scalar()
        or 0
    )


def assert_can_verify_claim(conn: Connection, user_id: int) -> None:
    """Raise :class:`ClaimCapExceeded` if one more verified claim breaks the cap.

    Call this before flipping a claim to ``verified``. Programme is capped at
    :data:`PROGRAMME_BOAT_CAP` verified boats (so the 11th is refused);
    Skipper and Crew carry no cap.
    """
    ent = get_entitlements(conn, user_id)
    if ent.boat_cap is None:
        return
    if count_verified_claims(conn, user_id) >= ent.boat_cap:
        raise ClaimCapExceeded(ent.boat_cap, ent.tier)
