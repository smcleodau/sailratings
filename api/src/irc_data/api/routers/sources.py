"""Sources policy router — exposes the collection policy and source registry."""

from __future__ import annotations

from fastapi import APIRouter

from irc_data.sources.policy import get_policy_summary

router = APIRouter()


@router.get("/sources/policy")
def get_collection_policy():
    """Return the current collection policy and source registry.

    This endpoint is public (no auth) because the policy itself is a public
    document — it documents how SailRatings collects data responsibly.
    """
    return get_policy_summary()


@router.get("/sources")
def list_sources():
    """Return the source registry summary."""
    return get_policy_summary()
