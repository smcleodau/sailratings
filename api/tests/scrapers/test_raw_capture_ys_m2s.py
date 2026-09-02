"""Tests for the DP-00-03 raw capture job (Yacht Scoring + Manage2Sail).

Policy: v1.0

Test categories:
  1. Envelope validation — CaptureItem carries the RawArtifactV0 contract
  2. Idempotency — second run stores zero new artifacts (rerun fetches zero)
  3. Kill switch / §2 gate — disabled source is never fetched
  4. Collection window — out-of-hours runs abort cleanly
  5. Politeness — max_fetches cap; conditional requests (304); size cap
  6. Source gating — only DP-00-03 sources; unknown rejected
  7. Discovery — result links found from a public index page; others ignored
  8. Canary mode — discovery capped to a handful of pages
  9. CaptureLedger — to_dict structure; etag_cache carried but not serialised
 10. Conditional-request cache — persisted and reloaded (file round-trip)

All tests run without real network calls (httpx.MockTransport).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from irc_data.scrapers.raw_capture_ys_m2s import (
    ADAPTER_VERSION,
    CANARY_MAX_DISCOVERY_PAGES,
    DEFAULT_MAX_DISCOVERY_PAGES,
    DP_00_03_SOURCES,
    MAX_OBJECT_BYTES,
    SOURCE_SLUG_MANAGE2SAIL,
    SOURCE_SLUG_YACHTSCORING,
    CaptureItem,
    CaptureLedger,
    _m2s_is_results_link,
    _source_config,
    _ys_is_results_link,
    capture_source,
    capture_url,
    discover_result_urls,
    load_etag_file,
    run_nightly,
    save_etag_file,
)
from irc_data.scrapers.raw_capture import is_source_collectable
from irc_data.sources.provenance import RawObjectStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STUB_HTML = b"<html><body><h1>Yacht Scoring Results</h1></body></html>"
STUB_HTML_V2 = b"<html><body><h1>Yacht Scoring Results v2</h1></body></html>"

YS_INDEX_HTML = """
<html><body>
  <a href="https://www.yachtscoring.com/event_results_cumulative/12345">Event A results</a>
  <a href="https://www.yachtscoring.com/event_results.cfm?eid=678">Event B results</a>
  <a href="https://www.yachtscoring.com/about">About</a>
  <a href="https://www.yachtscoring.com/login">Login</a>
  <a href="mailto:info@yachtscoring.com">Email</a>
</body></html>
"""

M2S_INDEX_HTML = """
<html><body>
  <a href="https://www.manage2sail.com/event/regatta-2025">Regatta 2025</a>
  <a href="https://www.manage2sail.com/results/nationals">Nationals results</a>
  <a href="https://www.manage2sail.com/contact">Contact</a>
</body></html>
"""


def _store(tmp_path: Path) -> RawObjectStore:
    return RawObjectStore(str(tmp_path / "raw_store"))


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture(autouse=True)
def _no_polite_sleep(monkeypatch):
    """Neutralise the 2 s + jitter politeness sleep during tests.

    The capture loop calls ``_polite_sleep`` before every fetch; without this
    the multi-fetch tests would take minutes.  The politeness behaviour itself
    is exercised separately in the DP-00-04 suite.
    """
    import time as _time

    import irc_data.scrapers.raw_capture_ys_m2s as mod

    monkeypatch.setattr(mod, "_polite_sleep", lambda last, min_delay=0.0: _time.monotonic())


def _ys_index_handler(request: httpx.Request) -> httpx.Response:
    """Serves the YS public index, robots 404, and result pages."""
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(404)
    if "event_results_archive" in path:
        return httpx.Response(200, text=YS_INDEX_HTML, headers={"Content-Type": "text/html"})
    # result pages
    return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})


# ---------------------------------------------------------------------------
# 1. Envelope validation (RawArtifactV0 contract)
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_capture_url_produces_valid_envelope(self, tmp_path):
        store = _store(tmp_path)
        url = "https://www.yachtscoring.com/event_results_cumulative/12345"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=STUB_HTML,
                headers={"Content-Type": "text/html", "ETag": '"ys-abc"'},
            )

        client = _mock_client(handler)
        item, outcome = capture_url(client, store, url, SOURCE_SLUG_YACHTSCORING)

        assert outcome == "new"
        assert item is not None
        assert item.content_hash == hashlib.sha256(STUB_HTML).hexdigest()
        assert item.requested_uri == url
        assert item.status == 200
        assert item.policy_version == "v1.0"
        assert item.adapter_version == ADAPTER_VERSION
        assert item.content_length == len(STUB_HTML)
        assert item.fetched_at
        assert item.object_location
        assert item.etag == '"ys-abc"'
        assert store.get(item.content_hash) == STUB_HTML

    def test_capture_item_envelope_keys(self, tmp_path):
        store = _store(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        client = _mock_client(handler)
        item, _ = capture_url(client, store, "https://www.manage2sail.com/event/x", SOURCE_SLUG_MANAGE2SAIL)
        d = item.to_dict()
        for key in (
            "source_slug", "requested_uri", "resolved_uri", "status",
            "content_hash", "content_length", "fetched_at", "policy_version",
            "object_location", "adapter_version",
        ):
            assert key in d, f"missing envelope key: {key}"
        assert d["policy_version"] == "v1.0"


# ---------------------------------------------------------------------------
# 2. Idempotency — rerun stores zero new artifacts
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_stores_zero_new(self, tmp_path):
        store = _store(tmp_path)
        urls = [
            "https://www.yachtscoring.com/event_results_cumulative/1",
            "https://www.yachtscoring.com/event_results_cumulative/2",
        ]

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(_ys_index_handler)
            ledger1 = capture_source(
                SOURCE_SLUG_YACHTSCORING, store, urls=urls,
                enforce_window=False, check_kill_switch=False,
            )

        # Both pages return identical bytes → first new, second content-dup.
        assert ledger1.urls_new == 1
        assert ledger1.urls_unchanged == 1
        assert store.count() == 1

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(_ys_index_handler)
            ledger2 = capture_source(
                SOURCE_SLUG_YACHTSCORING, store, urls=urls,
                enforce_window=False, check_kill_switch=False,
            )

        assert ledger2.urls_new == 0, "rerun must store zero new artifacts"
        assert ledger2.urls_unchanged == 2
        assert store.count() == 1

    def test_changed_content_stored_as_new(self, tmp_path):
        store = _store(tmp_path)
        urls = ["https://www.yachtscoring.com/event_results_cumulative/9"]
        state = {"body": STUB_HTML}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, content=state["body"], headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger1 = capture_source(
                SOURCE_SLUG_YACHTSCORING, store, urls=urls,
                enforce_window=False, check_kill_switch=False,
            )
        assert ledger1.urls_new == 1

        state["body"] = STUB_HTML_V2
        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger2 = capture_source(
                SOURCE_SLUG_YACHTSCORING, store, urls=urls,
                enforce_window=False, check_kill_switch=False,
            )

        assert ledger2.urls_new == 1, "changed bytes must be stored as a new artifact"
        assert store.count() == 2

    def test_304_not_modified_is_clean_noop(self, tmp_path):
        store = _store(tmp_path)
        url = "https://www.yachtscoring.com/event_results_cumulative/5"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("if-none-match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(
                200, content=STUB_HTML,
                headers={"Content-Type": "text/html", "ETag": '"v1"'},
            )

        client = _mock_client(handler)
        item1, outcome1 = capture_url(client, store, url, SOURCE_SLUG_YACHTSCORING)
        assert outcome1 == "new"
        assert store.count() == 1

        item2, outcome2 = capture_url(
            client, store, url, SOURCE_SLUG_YACHTSCORING, etag=item1.etag
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
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            False,
            "approved",
        )

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store,
                urls=["https://www.yachtscoring.com/event_results_cumulative/1"],
                enforce_window=False, check_kill_switch=True, db_engine=mock_engine,
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
            "irc_data.scrapers.raw_capture_ys_m2s.is_within_collection_window",
            return_value=False,
        ):
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store,
                urls=["https://www.yachtscoring.com/event_results_cumulative/1"],
                enforce_window=True, check_kill_switch=False,
            )
        assert ledger.status == "window_closed"
        assert store.count() == 0

    def test_within_window_proceeds(self, tmp_path):
        store = _store(tmp_path)

        with patch(
            "irc_data.scrapers.raw_capture_ys_m2s.is_within_collection_window",
            return_value=True,
        ), patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(_ys_index_handler)
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store,
                urls=["https://www.yachtscoring.com/event_results_cumulative/1"],
                enforce_window=True, check_kill_switch=False,
            )
        assert ledger.status in ("ok", "ok_with_errors")
        assert ledger.urls_new == 1


# ---------------------------------------------------------------------------
# 5. Politeness — caps, conditional requests, size cap
# ---------------------------------------------------------------------------


class TestPoliteness:
    def test_max_fetches_cap_respected(self, tmp_path):
        store = _store(tmp_path)
        urls = [
            f"https://www.yachtscoring.com/event_results_cumulative/{i}"
            for i in range(20)
        ]

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(_ys_index_handler)
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store, urls=urls,
                max_fetches=3, enforce_window=False, check_kill_switch=False,
            )
        assert ledger.fetch_count <= 3

    def test_object_size_cap(self, tmp_path):
        store = _store(tmp_path)
        big = b"x" * (MAX_OBJECT_BYTES + 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=big)

        client = _mock_client(handler)
        item, outcome = capture_url(
            client, store, "https://www.yachtscoring.com/big", SOURCE_SLUG_YACHTSCORING
        )
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
            client, store, "https://www.manage2sail.com/event/x", SOURCE_SLUG_MANAGE2SAIL,
            etag='"tag"', last_modified="Wed, 01 Jan 2025 00:00:00 GMT",
        )
        assert seen["if_none_match"] == '"tag"'
        assert seen["if_modified_since"] == "Wed, 01 Jan 2025 00:00:00 GMT"


# ---------------------------------------------------------------------------
# 6. Source gating
# ---------------------------------------------------------------------------


class TestSourceGating:
    def test_run_nightly_rejects_unknown_source(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(ValueError):
            run_nightly("clubspot", store=store, enforce_window=False)

    def test_dp_00_03_sources_are_collectable(self):
        # Both DP-00-03 sources are approved in the registry seed.
        assert is_source_collectable(SOURCE_SLUG_YACHTSCORING) is True
        assert is_source_collectable(SOURCE_SLUG_MANAGE2SAIL) is True

    def test_source_config_only_for_dp_00_03(self):
        with pytest.raises(ValueError):
            _source_config("sailwave")
        cfg = _source_config(SOURCE_SLUG_YACHTSCORING)
        assert cfg.slug == SOURCE_SLUG_YACHTSCORING
        assert cfg.rendered is False  # plain-HTTP primitive per DECISION


# ---------------------------------------------------------------------------
# 7. Discovery (public index pages only)
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discovers_results_links_only(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=YS_INDEX_HTML, headers={"Content-Type": "text/html"})

        client = _mock_client(handler)
        config = _source_config(SOURCE_SLUG_YACHTSCORING)
        urls = discover_result_urls(client, config)

        # Results links found; about/login/mailto excluded.
        assert any("event_results_cumulative/12345" in u for u in urls)
        assert any("eid=678" in u for u in urls)
        assert not any("about" in u for u in urls)
        assert not any("login" in u for u in urls)
        assert not any("mailto" in u for u in urls)

    def test_discovery_respects_cap(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=YS_INDEX_HTML, headers={"Content-Type": "text/html"})

        client = _mock_client(handler)
        config = _source_config(SOURCE_SLUG_YACHTSCORING)
        urls = discover_result_urls(client, config, max_pages=1)
        assert len(urls) <= 1

    def test_m2s_discovery(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=M2S_INDEX_HTML, headers={"Content-Type": "text/html"})

        client = _mock_client(handler)
        config = _source_config(SOURCE_SLUG_MANAGE2SAIL)
        urls = discover_result_urls(client, config)
        assert any("/event/regatta-2025" in u for u in urls)
        assert any("/results/nationals" in u for u in urls)
        assert not any("contact" in u for u in urls)


# ---------------------------------------------------------------------------
# 8. Canary mode
# ---------------------------------------------------------------------------


class TestCanary:
    def test_canary_caps_discovery(self, tmp_path):
        store = _store(tmp_path)

        # Build an index with many results links.
        links = "".join(
            f'<a href="https://www.yachtscoring.com/event_results_cumulative/{i}">E{i}</a>'
            for i in range(50)
        )
        index_html = f"<html><body>{links}</body></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/robots.txt":
                return httpx.Response(404)
            if "event_results_archive" in path:
                return httpx.Response(200, text=index_html, headers={"Content-Type": "text/html"})
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store,
                canary=True, enforce_window=False, check_kill_switch=False,
            )

        # Canary discovery cap enforced (1 index fetch + ≤ cap page fetches).
        assert ledger.urls_attempted <= CANARY_MAX_DISCOVERY_PAGES
        assert ledger.fetch_count <= CANARY_MAX_DISCOVERY_PAGES + 1

    def test_full_run_uses_default_discovery_cap(self, tmp_path):
        store = _store(tmp_path)
        links = "".join(
            f'<a href="https://www.yachtscoring.com/event_results_cumulative/{i}">E{i}</a>'
            for i in range(500)
        )
        index_html = f"<html><body>{links}</body></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/robots.txt":
                return httpx.Response(404)
            if "event_results_archive" in path:
                return httpx.Response(200, text=index_html, headers={"Content-Type": "text/html"})
            return httpx.Response(200, content=STUB_HTML, headers={"Content-Type": "text/html"})

        with patch("irc_data.scrapers.raw_capture_ys_m2s._make_client") as m:
            m.return_value = _mock_client(handler)
            ledger = capture_source(
                SOURCE_SLUG_YACHTSCORING, store,
                canary=False, enforce_window=False, check_kill_switch=False,
            )

        assert ledger.urls_attempted <= DEFAULT_MAX_DISCOVERY_PAGES
        assert ledger.urls_attempted > CANARY_MAX_DISCOVERY_PAGES


# ---------------------------------------------------------------------------
# 9. CaptureLedger
# ---------------------------------------------------------------------------


class TestCaptureLedger:
    def test_to_dict_structure_and_etag_cache_not_serialised(self):
        ledger = CaptureLedger(source_slug=SOURCE_SLUG_YACHTSCORING)
        ledger.urls_attempted = 3
        ledger.urls_new = 2
        ledger.etag_cache = {"https://x": {"etag": '"t"'}}
        ledger.finish("ok")

        d = ledger.to_dict()
        for key in (
            "source_slug", "policy_version", "started_at", "finished_at",
            "status", "urls_attempted", "urls_fetched", "urls_new",
            "urls_unchanged", "urls_not_modified", "urls_skipped",
            "fetch_count", "bytes_downloaded", "error_count", "errors", "items",
        ):
            assert key in d, f"missing ledger key: {key}"
        assert "etag_cache" not in d
        assert d["source_slug"] == SOURCE_SLUG_YACHTSCORING
        assert d["policy_version"] == "v1.0"


# ---------------------------------------------------------------------------
# 10. Conditional-request cache file round-trip
# ---------------------------------------------------------------------------


class TestEtagCacheFile:
    def test_save_and_load_round_trip(self, tmp_path):
        cache = {
            "https://www.yachtscoring.com/event_results_cumulative/1": {"etag": '"a"'},
            "https://www.manage2sail.com/event/x": {
                "etag": '"b"',
                "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
            },
        }
        path = tmp_path / "sub" / ".etag_cache.json"
        save_etag_file(path, cache)
        loaded = load_etag_file(path)
        assert loaded == cache

    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_etag_file(tmp_path / "nope.json") == {}
