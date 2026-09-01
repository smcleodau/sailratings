"""Shared test fixtures for the source framework tests."""

import pytest
import httpx

from irc_data.sources.models import DataSource
from irc_data.sources.http_client import PolicyAwareHttpClient, RateLimiter, STANDARD_USER_AGENT
from irc_data.sources.registry import get_source


@pytest.fixture
def approved_source() -> DataSource:
    """Return an approved source for testing."""
    return get_source("sailsys")


@pytest.fixture
def hold_source() -> DataSource:
    """Return a source on hold (ClubSpot)."""
    return get_source("clubspot")


@pytest.fixture
def disabled_source() -> DataSource:
    """Return a disabled source."""
    src = get_source("sailsys")
    src.enabled = False
    return src


@pytest.fixture
def stale_source() -> DataSource:
    """Return a source with a stale policy version."""
    src = get_source("sailsys")
    src.policy_version = "stale-version"
    return src


@pytest.fixture
def irc_certs_source() -> DataSource:
    """Return the irc-certs source."""
    return get_source("irc-certs")


@pytest.fixture
def no_rate_limit_client():
    """Return a PolicyAwareHttpClient with zero delay (for fast tests)."""
    inner = httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": STANDARD_USER_AGENT},
    )
    return PolicyAwareHttpClient(
        client=inner,
        rate_limiter=RateLimiter(min_delay=0.0, jitter=0.0),
    )


def make_mock_transport(handler):
    """Create an httpx.MockTransport from a handler function."""
    return httpx.MockTransport(handler)
