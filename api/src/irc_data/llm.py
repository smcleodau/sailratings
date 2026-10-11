"""The one place SailRatings talks to an LLM.

Every app feature that needs a model calls this module; nothing else builds a
client or names a model. Switching model is configuration, not code:

    LLM_MODEL                default model for every feature (claude-sonnet-5-5)
    LLM_MODEL_<FEATURE>      per-feature override, e.g. LLM_MODEL_ADMIN_CHAT
    LLM_EFFORT               default effort: low | medium | high | xhigh | max
    LLM_EFFORT_<FEATURE>     per-feature effort override

Features: insights, report, report_service, admin_chat, extractor, news.
Credentials come from ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

PROVIDER = "anthropic"
API_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
# max_tokens also covers adaptive thinking, so it is a ceiling, not a length
# control; response length is set by each feature's prompt.
DEFAULT_MAX_TOKENS = 16000

# Server-side refusal fallback: a classifier decline is retried on the model
# Anthropic recommends for that refusal category, inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

T = TypeVar("T", bound=BaseModel)


class LLMNotConfigured(RuntimeError):
    """ANTHROPIC_API_KEY is missing."""


class LLMRefusal(RuntimeError):
    """The model (and any fallback) declined the request."""


def model(feature: str) -> str:
    return os.environ.get(f"LLM_MODEL_{feature.upper()}") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def effort(feature: str) -> str:
    return os.environ.get(f"LLM_EFFORT_{feature.upper()}") or os.environ.get("LLM_EFFORT") or DEFAULT_EFFORT


def client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMNotConfigured("ANTHROPIC_API_KEY not configured")
    return _client()


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def request_params(*, feature: str, system: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict[str, Any]:
    """Shared request parameters. Callers add ``messages`` (and ``tools``)."""
    return {
        "model": model(feature),
        "max_tokens": max_tokens,
        "system": system,
        "output_config": {"effort": effort(feature)},
        "betas": [_FALLBACK_BETA],
        "fallbacks": "default",
    }


def check(message: Any) -> Any:
    """Raise on a refusal; return the message otherwise."""
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise LLMRefusal(f"model declined the request (category: {category})")
    return message


def text_of(message: Any) -> str:
    return "".join(b.text for b in message.content if b.type == "text").strip()


def respond(*, system: str, user: str, feature: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> Any:
    """One non-streaming call; returns the full message (text, usage, model)."""
    msg = client().beta.messages.create(
        **request_params(feature=feature, system=system, max_tokens=max_tokens),
        messages=[{"role": "user", "content": user}],
    )
    return check(msg)


def complete(*, system: str, user: str, feature: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    return text_of(respond(system=system, user=user, feature=feature, max_tokens=max_tokens))


def stream(*, system: str, user: str, feature: str, max_tokens: int = DEFAULT_MAX_TOKENS):
    """Streaming call. Use as ``with llm.stream(...) as s:`` then ``s.text_stream``
    and ``llm.check(s.get_final_message())``."""
    return client().beta.messages.stream(
        **request_params(feature=feature, system=system, max_tokens=max_tokens),
        messages=[{"role": "user", "content": user}],
    )


def parse(*, system: str, user: str, output_format: type[T], feature: str,
          max_tokens: int = DEFAULT_MAX_TOKENS) -> T:
    """Structured output validated against ``output_format``."""
    msg = check(client().beta.messages.parse(
        **request_params(feature=feature, system=system, max_tokens=max_tokens),
        messages=[{"role": "user", "content": user}],
        output_format=output_format,
    ))
    if msg.parsed_output is None:
        raise ValueError(f"no structured output (stop_reason={msg.stop_reason})")
    return msg.parsed_output
