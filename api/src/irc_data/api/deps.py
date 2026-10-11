"""Dependency injection for FastAPI routes."""

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from jwt import PyJWKClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.db.connection import get_engine

logger = logging.getLogger(__name__)


def get_db() -> Engine:
    """Return the shared SQLAlchemy engine."""
    return get_engine()


# ── Clerk authentication (PAY-01-08) ─────────────────────────────────────


@dataclass
class CallerIdentity:
    """The signed-in caller resolved from a Clerk session token."""

    clerk_user_id: str
    email: Optional[str] = None
    claims: dict[str, Any] = field(default_factory=dict)


# In-memory JWKS client cache keyed by JWKS URL.
_JWKS_CLIENTS: dict[str, PyJWKClient] = {}


def _clerk_pem_public_key() -> Optional[str]:
    """Return the Clerk instance PEM public key, if configured."""
    pem = os.environ.get("CLERK_PEM_PUBLIC_KEY")
    if pem:
        return pem.replace("\\n", "\n").strip()
    return None


def _clerk_jwks_client() -> Optional[PyJWKClient]:
    """Build a JWKS client from CLERK_JWKS_URL or CLERK_SECRET_KEY."""
    url = os.environ.get("CLERK_JWKS_URL")
    if not url:
        # Clerk encodes the frontend API domain in the *publishable* key
        # (pk_test_<base64 domain>). Secret keys are opaque random strings and
        # carry no domain, so deriving it from CLERK_SECRET_KEY always failed
        # and silently disabled auth — every signed-in caller fell through to
        # guest. Prefer the publishable key; keep the secret as a fallback for
        # any environment that does set a decodable one.
        domain = None
        for var, prefixes in (
            ("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", ("pk_test_", "pk_live_")),
            ("CLERK_SECRET_KEY", ("sk_test_", "sk_live_")),
        ):
            raw = os.environ.get(var)
            if not raw:
                continue
            body = raw
            for prefix in prefixes:
                body = body.removeprefix(prefix)
            try:
                candidate = (
                    base64.urlsafe_b64decode(body + "==").decode().rstrip("$")
                )
            except Exception:
                continue
            if candidate.endswith(("clerk.accounts.dev", "clerk.com")) or (
                "." in candidate and " " not in candidate
            ):
                domain = candidate
                break
        if not domain:
            logger.warning(
                "No decodable Clerk domain in NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY or "
                "CLERK_SECRET_KEY, and CLERK_JWKS_URL is unset; Clerk auth disabled"
            )
            return None
        url = f"https://{domain}/.well-known/jwks.json"
    if url not in _JWKS_CLIENTS:
        _JWKS_CLIENTS[url] = PyJWKClient(url, cache_keys=True)
    return _JWKS_CLIENTS[url]


def _fetch_clerk_user_email(user_id: str) -> Optional[str]:
    """Fetch the primary email from the Clerk Backend API (best effort)."""
    secret = os.environ.get("CLERK_SECRET_KEY")
    if not secret:
        return None
    try:
        resp = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        primary_id = data.get("primary_email_address_id")
        addresses = data.get("email_addresses") or []
        for addr in addresses:
            if addr.get("id") == primary_id:
                return addr.get("email_address")
        return addresses[0].get("email_address") if addresses else None
    except Exception:
        logger.debug("Clerk user fetch failed for %s", user_id, exc_info=True)
        return None


def verify_clerk_token(token: str) -> Optional[CallerIdentity]:
    """Verify a Clerk session JWT and return the caller identity.

    Returns ``None`` for invalid / expired tokens (callers treat the
    request as a guest) or when Clerk is not configured.
    """
    try:
        pem_key = _clerk_pem_public_key()
        if pem_key:
            claims = jwt.decode(
                token,
                key=pem_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        else:
            client = _clerk_jwks_client()
            if client is None:
                return None
            signing_key = client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key=signing_key.key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
    except jwt.PyJWTError as e:
        logger.debug("Clerk token verification failed: %s", e)
        return None

    user_id = claims.get("sub")
    if not user_id:
        return None

    email = (
        claims.get("email")
        or claims.get("email_address")
        or claims.get("primary_email_address")
    )
    if not email:
        # Session tokens often carry no email claim; fall back to the
        # Clerk Backend API so guest-order claiming still works.
        email = _fetch_clerk_user_email(user_id)

    return CallerIdentity(clerk_user_id=user_id, email=email, claims=claims)


def get_optional_identity(request: Request) -> Optional[CallerIdentity]:
    """FastAPI dependency: resolve the signed-in caller, or ``None``.

    Reads the ``Authorization: Bearer <token>`` header, falling back to
    Clerk's ``__session`` cookie. Any verification failure yields ``None``
    so public endpoints keep working for guests.
    """
    header = request.headers.get("Authorization") or ""
    token: Optional[str] = None
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip() or None
    if token is None:
        token = request.cookies.get("__session")
    if not token:
        return None
    return verify_clerk_token(token)


# ── Entitlements (PAY-01-01) ─────────────────────────────────────────────


class FeatureNotEntitled(HTTPException):
    """403 raised by :func:`require_feature`.

    Rendered as ``{"detail": <message>, "required_tier": <tier>}`` once
    :func:`install_entitlement_handlers` has been called on the app; without
    it the default handler still returns a valid 403 ``{"detail": <message>}``
    and the tier is also sent in the ``X-Required-Tier`` header.
    """

    def __init__(self, feature: str, required_tier: Optional[str]) -> None:
        self.feature = feature
        self.required_tier = required_tier
        headers = {"X-Required-Tier": required_tier} if required_tier else None
        super().__init__(
            status_code=403,
            detail=f"Your plan does not include '{feature}'",
            headers=headers,
        )


async def feature_not_entitled_handler(
    request: Request, exc: FeatureNotEntitled
) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"detail": exc.detail, "required_tier": exc.required_tier},
        headers=exc.headers,
    )


def install_entitlement_handlers(app: Any) -> None:
    """Register the flat ``{detail, required_tier}`` 403 body on ``app``."""
    app.add_exception_handler(FeatureNotEntitled, feature_not_entitled_handler)


def require_feature(feature: str):
    """FastAPI dependency factory: 401 anonymous, 403 when not entitled.

    ``boat_id`` is taken from the route's path parameters when present, so
    ``Depends(require_feature("report_view"))`` on ``/boats/{boat_id}/...``
    also honours per-boat ownership. Returns the caller's ``EntitlementV1``.
    """
    from irc_data.api.services import entitlements as ent_svc

    if feature not in ent_svc.ALL_FEATURES:
        raise ValueError(f"Unknown feature '{feature}'")

    def _dependency(
        request: Request,
        engine: Engine = Depends(get_db),
        identity: Optional[CallerIdentity] = Depends(get_optional_identity),
    ):
        if identity is None:
            raise HTTPException(status_code=401, detail="Not signed in")

        boat_id: Optional[int] = None
        raw_boat = request.path_params.get("boat_id")
        if raw_boat is not None:
            try:
                boat_id = int(raw_boat)
            except (TypeError, ValueError):
                boat_id = None

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id FROM users WHERE clerk_id = :c"
                    " AND deleted_at IS NULL AND deletion_completed_at IS NULL"
                ),
                {"c": identity.clerk_user_id},
            ).first()
            ent = (
                ent_svc.get_entitlements(conn, row[0])
                if row is not None
                else ent_svc.free_entitlements()
            )

        if not ent_svc.can(ent, feature, boat_id):
            raise FeatureNotEntitled(feature, ent_svc.required_tier(feature))
        return ent

    return _dependency
