"""Shared test fixtures for the source framework tests."""

import dataclasses

import pytest
import httpx

from irc_data.sources.models import DataSource
from irc_data.sources.http_client import (
    HttpClient,
    PolicyAwareHttpClient,
    RateLimiter,
    STANDARD_USER_AGENT,
)
from irc_data.sources.policy import ACTIVE_POLICY
from irc_data.sources.registry import get_source


def _detached(slug: str, **overrides):
    """Return a detached copy of a registry source (no shared mutation)."""
    return dataclasses.replace(get_source(slug), **overrides)


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
    """Return a disabled source (detached — no registry mutation)."""
    return _detached("sailsys", enabled=False)


@pytest.fixture
def stale_source() -> DataSource:
    """Return a source with a stale policy version (detached)."""
    return _detached("sailsys", policy_version="stale-version")


@pytest.fixture
def irc_certs_source() -> DataSource:
    """Return the irc-certs source."""
    return get_source("irc-certs")


def _fast_policy():
    """ACTIVE_POLICY with zero rate-limit delay for fast tests."""
    return dataclasses.replace(
        ACTIVE_POLICY,
        rate=dataclasses.replace(
            ACTIVE_POLICY.rate, min_delay_seconds=0.0, jitter_seconds=0.0
        ),
    )


@pytest.fixture
def no_rate_limit_client():
    """Return an HttpClient with zero rate-limit delay (for fast tests)."""
    inner = httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": STANDARD_USER_AGENT},
    )
    return HttpClient(
        client=inner,
        policy=_fast_policy(),
        backoff=(0.001, 0.001, 0.001, 0.001),
    )


def make_mock_transport(handler):
    """Create an httpx.MockTransport from a handler function."""
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _isolated_render_evidence_dir(tmp_path, monkeypatch):
    """Keep render_page() screenshot evidence out of the real data dir.

    ``render_page()`` defaults its evidence directory to
    ``data/rendered_evidence`` under the current working directory; during
    tests that would leak artifacts into the repository.  Point the
    evidence dir at a per-test tmp dir instead.
    """
    monkeypatch.setenv("SAILRATINGS_RENDER_EVIDENCE_DIR", str(tmp_path))
