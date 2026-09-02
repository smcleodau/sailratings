"""Tests for the DP-00-04 raw capture job (Sailwave + sailing news).

Policy: interim-v0

Test categories:
  1. Envelope validation — CaptureItem carries the RawArtifactV0 contract
  2. Idempotency — second run stores zero new artifacts
  3. Kill switch / §2 gate — disabled and hold sources are never fetched
  4. Collection window — out-of-hours runs abort cleanly
  5. Politeness — max_fetches cap; conditional requests (304); size cap
  6. §2 hold sources — ClubSpot / Kwindoo are not collectable
  7. Sailwave discovery — .htm/.html/.blw links found; others ignored
  8. CaptureLedger — to_dict structure

All tests run without real network calls (httpx.MockTransport).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from irc_data.scrapers.raw_capture import (
    ADAPTER_VERSION,
    CaptureItem,
    CaptureLedger,
    DEFAULT_NEWS_FEEDS,
    DP_00_04_SOURCES,
    MAX_OBJECT_BYTES,
    SOURCE_SLUG_NEWS,
    SOURCE_SLUG_SAILWAVE,
    capture_news_feeds,
    capture_sailwave,
    capture_url,
    discover_sailwave_urls,
    is_source_collectable,
    is_url_allowed,
    list_approved_source_slugs,
    run_nightly,
)
from irc_data.sources.provenance import RawObjectStore
from irc_data.sources.robots import RobotsRules, parse_robots_txt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STUB_HTML = b"<html><body><h1>Race Results</h1></body></html>"
STUB_HTML_V2 = b"<html><body><h1>Race Results v2</h1></body></html>"
STUB_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Sailing News</title>
<item><title>Article 1</title><link>https://example.com/a1</link></item>
</channel></rss>"""


def _store(tmp_path: Path) -> RawObjectStore:
    return RawObjectStore(str(tmp_path / "raw_store"))


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


# ---------------------------------------------------------------------------
# 1. Envelope validation (RawArtifactV0 contract)
# ---------------------------------------------------------------------------


class TestEnvelope:
    """CaptureItem must carry bytes-hash + URL + fetch time + policy version."""

    def test_capture_url_produces_valid_envelope(self, tmp_path):
        store = _store(tmp_path)
        url = "https://sailwave.example.com/results/event1.htm"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=STUB_HTML,
                headers={"Content-Type": "text/html", "ETag": '"abc123"'},
            )

        client = _mock_client(handler)
        item, outcome = capture_url(client, store, url, SOURCE_SLUG_SAILWAVE)

        assert outcome == "new"
        assert item is not None
        # Envelope fields
        assert item.content_hash == hashlib.sha256(STUB_HTML).hexdigest()
        assert item.requested_uri == url
        assert item.resolved_uri == url
        assert item.status == 200
        assert item.policy_version == "interim-v0"
        assert item.adapter_version == ADAPTER_VERSION
        assert item.content_length == len(STUB_HTML)
        assert item.fetched_at  # ISO timestamp present
        assert item.object_location  # content-addressed path present
        assert item.etag == '"abc123"'
        # Bytes retrievable from the store by hash
        assert store.get(item.content_hash) == STUB_HTML

    def test_capture_item_to_dict_round_trip(self, tmp_path):
        store = _store(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        client = _mock_client(handler)
        item, _ = capture_url(client, store, "https://x.example.com/r.htm", SOURCE_SLUG_SAILWAVE)
        d = item.to_dict()
        for key in (
            "source_slug",
            "requested_uri",
            "resolved_uri",
            "status",
            "content_hash",
            "content_length",
            "fetched_at",
            "policy_version",
            "object_location",
            "adapter_version",
        ):
            assert key in d, f"missing envelope key: {key}"
        assert d["policy_version"] == "interim-v0"


# ---------------------------------------------------------------------------
# 2. Idempotency — second run stores zero new artifacts
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_fetches_zero_new(self, tmp_path):
        store = _store(tmp_path)
        urls = ["https://sw.example.com/r1.htm", "https://sw.example.com/r2.htm"]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger1 = capture_sailwave(
                store, urls=urls, enforce_window=False, check_kill_switch=False
            )

        # Both URLs have identical bytes → first is new, second is a content-dup
        assert ledger1.urls_new == 1
        assert ledger1.urls_unchanged == 1
        assert store.count() == 1  # content-addressed dedup → one object

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger2 = capture_sailwave(
                store, urls=urls, enforce_window=False, check_kill_switch=False
            )

        assert ledger2.urls_new == 0, "second run must store zero new artifacts"
        assert ledger2.urls_unchanged == 2
        assert store.count() == 1

    def test_changed_content_stored_as_new(self, tmp_path):
        store = _store(tmp_path)
        urls = ["https://sw.example.com/r1.htm"]
        state = {"body": STUB_HTML}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=state["body"], headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger1 = capture_sailwave(store, urls=urls, enforce_window=False, check_kill_switch=False)
        assert ledger1.urls_new == 1

        state["body"] = STUB_HTML_V2  # content changed
        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger2 = capture_sailwave(store, urls=urls, enforce_window=False, check_kill_switch=False)

        assert ledger2.urls_new == 1, "changed bytes must be stored as a new artifact"
        assert store.count() == 2

    def test_304_not_modified_is_clean_noop(self, tmp_path):
        store = _store(tmp_path)
        url = "https://sw.example.com/r1.htm"

        def handler(request: httpx.Request) -> httpx.Response:
            # Server honours If-None-Match → 304
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(
                200, content=STUB_HTML, headers={"Content-Type": "text/html", "ETag": '"v1"'}
            )

        client = _mock_client(handler)

        # First fetch stores the object
        item1, outcome1 = capture_url(client, store, url, SOURCE_SLUG_SAILWAVE)
        assert outcome1 == "new"
        assert store.count() == 1

        # Second fetch with cached etag → 304, no re-store
        item2, outcome2 = capture_url(
            client, store, url, SOURCE_SLUG_SAILWAVE, etag=item1.etag
        )
        assert outcome2 == "not_modified"
        assert item2 is None
        assert store.count() == 1


# ---------------------------------------------------------------------------
# 3. Kill switch / §2 gate
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_db_kill_switch_blocks_collection(self, tmp_path):
        store = _store(tmp_path)
        fetch_called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            fetch_called["n"] += 1
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_HTML)

        mock_engine = MagicMock()
        # data_sources.enabled = False for this slug
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            False,
            "approved",
        )

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_sailwave(
                store,
                urls=["https://sw.example.com/r1.htm"],
                enforce_window=False,
                check_kill_switch=True,
                db_engine=mock_engine,
            )

        assert ledger.status == "kill_switch"
        assert fetch_called["n"] == 0, "no HTTP fetch may happen when the kill switch is on"
        assert store.count() == 0


# ---------------------------------------------------------------------------
# 4. Collection window
# ---------------------------------------------------------------------------


class TestCollectionWindow:
    def test_outside_window_aborts(self, tmp_path):
        store = _store(tmp_path)
        with patch(
            "irc_data.scrapers.raw_capture.is_within_collection_window", return_value=False
        ):
            ledger = capture_sailwave(
                store,
                urls=["https://sw.example.com/r1.htm"],
                enforce_window=True,
                check_kill_switch=False,
            )
        assert ledger.status == "window_closed"
        assert store.count() == 0

    def test_within_window_proceeds(self, tmp_path):
        store = _store(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch(
            "irc_data.scrapers.raw_capture.is_within_collection_window", return_value=True
        ), patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_sailwave(
                store,
                urls=["https://sw.example.com/r1.htm"],
                enforce_window=True,
                check_kill_switch=False,
            )
        assert ledger.status in ("ok", "ok_with_errors")
        assert ledger.urls_new == 1


# ---------------------------------------------------------------------------
# 5. Politeness — caps, conditional requests, size cap
# ---------------------------------------------------------------------------


class TestPoliteness:
    def test_max_fetches_cap_respected(self, tmp_path):
        store = _store(tmp_path)
        urls = [f"https://sw.example.com/r{i}.htm" for i in range(20)]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_sailwave(
                store,
                urls=urls,
                max_fetches=3,
                enforce_window=False,
                check_kill_switch=False,
            )
        assert ledger.fetch_count <= 3

    def test_object_size_cap(self, tmp_path):
        store = _store(tmp_path)
        big = b"x" * (MAX_OBJECT_BYTES + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big)

        client = _mock_client(handler)
        item, outcome = capture_url(client, store, "https://x.example.com/big.htm", SOURCE_SLUG_SAILWAVE)
        assert outcome.startswith("too_large")
        assert item is None
        assert store.count() == 0

    def test_conditional_headers_sent(self, tmp_path):
        store = _store(tmp_path)
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["if_none_match"] = request.headers.get("if-none-match", "")
            seen["if_modified_since"] = request.headers.get("if-modified-since", "")
            return httpx.Response(200, content=STUB_HTML)

        client = _mock_client(handler)
        capture_url(
            client,
            store,
            "https://x.example.com/r.htm",
            SOURCE_SLUG_SAILWAVE,
            etag='"tag"',
            last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
        assert seen["if_none_match"] == '"tag"'
        assert seen["if_modified_since"] == "Wed, 01 Jan 2025 00:00:00 GMT"


# ---------------------------------------------------------------------------
# 6. §2 hold sources — ClubSpot / Kwindoo must never be fetched
# ---------------------------------------------------------------------------


class TestHoldSources:
    @pytest.mark.parametrize("slug", ["clubspot", "kwindoo"])
    def test_hold_sources_not_collectable(self, slug):
        assert is_source_collectable(slug) is False

    def test_blocked_unknown_source_not_collectable(self):
        # Unknown slugs are implicitly blocked (§2.3); with no registry record
        # the DB-free check fails open to *registry* membership.  An unknown
        # slug has no registry record → not approved → not collectable.
        assert is_source_collectable("totally-unknown-source") is False

    def test_approved_sources_listed(self):
        slugs = list_approved_source_slugs()
        assert SOURCE_SLUG_SAILWAVE in slugs
        assert SOURCE_SLUG_NEWS in slugs
        # hold sources are excluded
        assert "clubspot" not in slugs
        assert "kwindoo" not in slugs

    def test_run_nightly_rejects_hold_source(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            run_nightly("clubspot", store=store, enforce_window=False)


# ---------------------------------------------------------------------------
# 7. Sailwave discovery
# ---------------------------------------------------------------------------


class TestSailwaveDiscovery:
    def test_discovers_result_files_only(self):
        html = """
        <html><body>
          <a href="/results/2025/event1.htm">Event 1</a>
          <a href="/results/2025/event2.html">Event 2</a>
          <a href="/results/2025/series.blw">Series BLW</a>
          <a href="/about">About page</a>
          <a href="https://other.example.com/x.pdf">PDF</a>
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        client = _mock_client(handler)
        urls = discover_sailwave_urls(client, "https://sw.example.com/results")
        assert urls == [
            "https://sw.example.com/results/2025/event1.htm",
            "https://sw.example.com/results/2025/event2.html",
            "https://sw.example.com/results/2025/series.blw",
        ]

    def test_dedup_and_sort(self):
        html = """
        <html><body>
          <a href="/b.htm">B</a>
          <a href="/a.htm">A</a>
          <a href="/b.htm">B again</a>
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        client = _mock_client(handler)
        urls = discover_sailwave_urls(client, "https://sw.example.com/")
        assert urls == ["https://sw.example.com/a.htm", "https://sw.example.com/b.htm"]


# ---------------------------------------------------------------------------
# 8. robots.txt handling
# ---------------------------------------------------------------------------


class TestRobots:
    def test_disallowed_path_skipped(self, tmp_path):
        store = _store(tmp_path)
        robots = "User-agent: *\nDisallow: /private/\n"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text=robots)
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_sailwave(
                store,
                urls=[
                    "https://sw.example.com/results/ok.htm",
                    "https://sw.example.com/private/secret.htm",
                ],
                enforce_window=False,
                check_kill_switch=False,
            )

        assert ledger.urls_new == 1
        assert ledger.urls_skipped == 1

    def test_is_url_allowed_helper(self):
        rules = parse_robots_txt("User-agent: *\nDisallow: /admin\n")
        assert is_url_allowed("https://x.com/public/page.htm", rules) is True
        assert is_url_allowed("https://x.com/admin/panel", rules) is False

    def test_404_robots_allows_all(self, tmp_path):
        store = _store(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_sailwave(
                store,
                urls=["https://sw.example.com/r.htm"],
                enforce_window=False,
                check_kill_switch=False,
            )
        assert ledger.urls_new == 1


# ---------------------------------------------------------------------------
# 9. News feed capture
# ---------------------------------------------------------------------------


class TestNewsFeeds:
    def test_feed_captured_as_raw_xml(self, tmp_path):
        store = _store(tmp_path)
        feed_url = "https://news.example.com/feed"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200, content=STUB_FEED, headers={"Content-Type": "application/rss+xml"}
            )

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_news_feeds(
                store, feeds=[feed_url], enforce_window=False, check_kill_switch=False
            )

        assert ledger.status in ("ok", "ok_with_errors")
        assert ledger.urls_new == 1
        # Raw feed bytes are stored verbatim
        assert store.get(ledger.items[0].content_hash) == STUB_FEED

    def test_default_feeds_used_when_none_given(self, tmp_path):
        store = _store(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=STUB_FEED)

        with patch("irc_data.scrapers.raw_capture._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_news_feeds(
                store, enforce_window=False, check_kill_switch=False
            )
        assert ledger.urls_attempted == len(DEFAULT_NEWS_FEEDS)

    def test_news_kill_switch_blocks(self, tmp_path):
        store = _store(tmp_path)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            False,
            "approved",
        )
        ledger = capture_news_feeds(
            store,
            feeds=["https://news.example.com/feed"],
            enforce_window=False,
            check_kill_switch=True,
            db_engine=mock_engine,
        )
        assert ledger.status == "kill_switch"
        assert store.count() == 0


# ---------------------------------------------------------------------------
# 10. CaptureLedger structure
# ---------------------------------------------------------------------------


class TestLedger:
    def test_to_dict_structure(self):
        ledger = CaptureLedger(source_slug=SOURCE_SLUG_SAILWAVE)
        ledger.urls_new = 2
        ledger.add_error("https://x.com/r.htm", "timeout")
        ledger.finish("ok")
        d = ledger.to_dict()
        assert d["source_slug"] == SOURCE_SLUG_SAILWAVE
        assert d["policy_version"] == "interim-v0"
        assert d["urls_new"] == 2
        assert d["error_count"] == 1
        assert d["status"] == "ok"
        assert d["finished_at"] is not None
        assert "items" in d

    def test_errors_capped(self):
        ledger = CaptureLedger(source_slug=SOURCE_SLUG_NEWS)
        for i in range(50):
            ledger.add_error(f"https://x.com/{i}", "err")
        ledger.finish("ok")
        assert len(ledger.to_dict()["errors"]) == 20
