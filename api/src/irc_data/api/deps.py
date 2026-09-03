"""Dependency injection for FastAPI routes."""

import base64
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import jwt
from fastapi import Request
from jwt import PyJWKClient
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
        secret = os.environ.get("CLERK_SECRET_KEY")
        if not secret:
            return None
        try:
            domain = (
                base64.urlsafe_b64decode(
                    secret.removeprefix("sk_test_").removeprefix("sk_live_") + "=="
                )
                .decode()
                .rstrip("$")
            )
        except Exception:
            logger.warning("CLERK_SECRET_KEY is not a decodable Clerk key; auth disabled")
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
