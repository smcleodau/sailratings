from types import SimpleNamespace

import pytest

from irc_data import llm


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LLM_MODEL", "LLM_MODEL_INSIGHTS", "LLM_EFFORT", "LLM_EFFORT_ADMIN_CHAT"):
        monkeypatch.delenv(k, raising=False)


def test_model_defaults_to_sonnet_and_is_overridable(monkeypatch):
    assert llm.model("insights") == llm.DEFAULT_MODEL == "claude-sonnet-5-5"
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5-5")
    assert llm.model("insights") == "claude-opus-5-5"
    monkeypatch.setenv("LLM_MODEL_INSIGHTS", "claude-haiku-5-5")
    assert llm.model("insights") == "claude-haiku-5-5"
    assert llm.model("report") == "claude-opus-5-5"


def test_effort_defaults_to_medium_and_is_overridable(monkeypatch):
    assert llm.effort("admin_chat") == "medium"
    monkeypatch.setenv("LLM_EFFORT_ADMIN_CHAT", "low")
    assert llm.effort("admin_chat") == "low"
    assert llm.effort("report") == "medium"


def test_missing_key_raises_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(llm.LLMNotConfigured, match="ANTHROPIC_API_KEY not configured"):
        llm.client()


def test_request_params_shape(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_INSIGHTS", "claude-haiku-5-5")
    p = llm.request_params(feature="insights", system="S", max_tokens=1234)
    assert p == {
        "model": "claude-haiku-5-5",
        "max_tokens": 1234,
        "system": "S",
        "output_config": {"effort": "medium"},
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }


def test_check_raises_on_refusal_and_passes_otherwise():
    ok = SimpleNamespace(stop_reason="end_turn")
    assert llm.check(ok) is ok
    refused = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(llm.LLMRefusal, match="cyber"):
        llm.check(refused)


def test_text_of_joins_only_text_blocks():
    msg = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="Hello "),
        SimpleNamespace(type="text", text="world"),
    ])
    assert llm.text_of(msg) == "Hello world"
