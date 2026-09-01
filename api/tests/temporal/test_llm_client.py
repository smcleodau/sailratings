"""Unit tests for llm_client — LiteLLM gateway integration.

Tests verify:
  - Requests use LiteLLM base URL (not legacy worker-router)
  - Logical model aliases are used
  - Metadata propagates correctly
  - Legacy worker-router URLs are rejected at config validation
  - Secrets never appear in telemetry log output
"""

import logging
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_env(monkeypatch, base_url, api_key, model_hint=None):
    monkeypatch.setenv("LITELLM_BASE_URL", base_url)
    monkeypatch.setenv("LITELLM_API_KEY", api_key)
    if model_hint:
        monkeypatch.setenv("LITELLM_MODEL_HINT", model_hint)
    else:
        monkeypatch.delenv("LITELLM_MODEL_HINT", raising=False)


VALID_BASE_URL = "http://100.93.15.38:4000/v1"
VALID_API_KEY = "sk-test-sailratings-factory-key"


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class TestConfigValidation:
    def test_missing_base_url_raises(self, monkeypatch):
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.setenv("LITELLM_API_KEY", VALID_API_KEY)
        from irc_data.temporal.orchestrator import llm_client
        with pytest.raises(RuntimeError, match="LITELLM_BASE_URL is not set"):
            llm_client._validated_base_url()

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.setenv("LITELLM_BASE_URL", VALID_BASE_URL)
        monkeypatch.delenv("LITELLM_API_KEY", raising=False)
        from irc_data.temporal.orchestrator import llm_client
        with pytest.raises(RuntimeError, match="LITELLM_API_KEY is not set"):
            llm_client._validated_api_key()

    @pytest.mark.parametrize("bad_url", [
        "http://100.93.15.38:10006/api/worker-router",
        "http://internal/api/worker-router/v1",
        "http://100.93.15.38:10006/something",
    ])
    def test_legacy_worker_router_url_rejected(self, monkeypatch, bad_url):
        monkeypatch.setenv("LITELLM_BASE_URL", bad_url)
        from irc_data.temporal.orchestrator import llm_client
        with pytest.raises(RuntimeError, match="legacy worker-router"):
            llm_client._validated_base_url()

    def test_non_sk_api_key_rejected(self, monkeypatch):
        monkeypatch.setenv("LITELLM_BASE_URL", VALID_BASE_URL)
        monkeypatch.setenv("LITELLM_API_KEY", "martha-internal-v5")
        from irc_data.temporal.orchestrator import llm_client
        with pytest.raises(RuntimeError, match="LiteLLM virtual key"):
            llm_client._validated_api_key()

    def test_valid_config_accepted(self, monkeypatch):
        _set_env(monkeypatch, VALID_BASE_URL, VALID_API_KEY)
        from irc_data.temporal.orchestrator import llm_client
        assert llm_client._validated_base_url() == VALID_BASE_URL
        assert llm_client._validated_api_key() == VALID_API_KEY


# ---------------------------------------------------------------------------
# Model alias enforcement
# ---------------------------------------------------------------------------

class TestModelAliases:
    def test_default_alias_is_coding_fast(self, monkeypatch):
        monkeypatch.delenv("LITELLM_MODEL_HINT", raising=False)
        from irc_data.temporal.orchestrator import llm_client
        assert llm_client.get_model_hint() == "coding-fast"

    @pytest.mark.parametrize("alias", ["coding-fast", "coding-deep", "review-independent"])
    def test_valid_aliases_accepted(self, monkeypatch, alias):
        monkeypatch.setenv("LITELLM_MODEL_HINT", alias)
        from irc_data.temporal.orchestrator import llm_client
        assert llm_client.get_model_hint() == alias

    def test_unknown_alias_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL_HINT", "openai/glm-5.2")
        from irc_data.temporal.orchestrator import llm_client
        result = llm_client.get_model_hint()
        assert result == "coding-fast"

    def test_provider_model_name_rejected(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL_HINT", "gemini-pro")
        from irc_data.temporal.orchestrator import llm_client
        result = llm_client.get_model_hint()
        assert result == "coding-fast"  # falls back, not passed through


# ---------------------------------------------------------------------------
# Metadata propagation
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_all_fields_present(self):
        from irc_data.temporal.orchestrator import llm_client
        meta = llm_client.build_metadata(
            service="sailratings-factory",
            role="lane-worker",
            lane="implementation",
            workflow_id="wf-123",
            run_id="run-456",
            epic_id="DP-02",
            card_id="DP-02-01",
            attempt=1,
            model_hint="coding-fast",
        )
        assert meta["service"] == "sailratings-factory"
        assert meta["role"] == "lane-worker"
        assert meta["lane"] == "implementation"
        assert meta["workflow_id"] == "wf-123"
        assert meta["run_id"] == "run-456"
        assert meta["epic_id"] == "DP-02"
        assert meta["card_id"] == "DP-02-01"
        assert meta["attempt"] == 1
        assert meta["model_hint"] == "coding-fast"

    def test_optional_fields_omitted_when_none(self):
        from irc_data.temporal.orchestrator import llm_client
        meta = llm_client.build_metadata()
        assert "role" not in meta
        assert "workflow_id" not in meta
        assert meta["service"] == "sailratings-factory"


# ---------------------------------------------------------------------------
# Telemetry — no secrets or content logged
# ---------------------------------------------------------------------------

class TestTelemetry:
    def test_no_secrets_in_log_output(self, monkeypatch, caplog):
        _set_env(monkeypatch, VALID_BASE_URL, VALID_API_KEY)
        from irc_data.temporal.orchestrator import llm_client
        with caplog.at_level(logging.INFO, logger="irc_data.temporal.orchestrator.llm_client"):
            with llm_client.LLMTelemetry(role="test", model="coding-fast") as tel:
                tel.record_response(prompt_tokens=100, completion_tokens=50)
        log_text = caplog.text
        assert VALID_API_KEY not in log_text
        assert "sk-" not in log_text

    def test_start_end_events_logged(self, caplog):
        from irc_data.temporal.orchestrator import llm_client
        with caplog.at_level(logging.INFO, logger="irc_data.temporal.orchestrator.llm_client"):
            with llm_client.LLMTelemetry(role="reviewer", model="review-independent") as tel:
                tel.record_response(prompt_tokens=200, completion_tokens=80)
        assert "llm_request_start" in caplog.text
        assert "llm_request_end" in caplog.text
        assert "reviewer" in caplog.text

    def test_error_logged_on_exception(self, caplog):
        from irc_data.temporal.orchestrator import llm_client
        with caplog.at_level(logging.ERROR, logger="irc_data.temporal.orchestrator.llm_client"):
            try:
                with llm_client.LLMTelemetry(role="worker", model="coding-fast"):
                    raise ValueError("connection refused")
            except ValueError:
                pass
        assert "llm_request_error" in caplog.text
        assert "connection refused" in caplog.text


# ---------------------------------------------------------------------------
# get_async_client — uses LiteLLM URL not legacy router
# ---------------------------------------------------------------------------

class TestAsyncClient:
    def test_client_uses_litellm_base_url(self, monkeypatch):
        _set_env(monkeypatch, VALID_BASE_URL, VALID_API_KEY)
        from irc_data.temporal.orchestrator import llm_client
        client = llm_client.get_async_client()
        assert VALID_BASE_URL in str(client.base_url)

    def test_client_rejects_worker_router_url(self, monkeypatch):
        _set_env(monkeypatch, "http://100.93.15.38:10006/api/worker-router", VALID_API_KEY)
        from irc_data.temporal.orchestrator import llm_client
        with pytest.raises(RuntimeError, match="legacy worker-router"):
            llm_client.get_async_client()
