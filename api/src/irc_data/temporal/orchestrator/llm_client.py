"""Shared LiteLLM client for the sailratings factory orchestrator.

All LLM calls in the factory must go through this module.  It:
  - Validates that LITELLM_BASE_URL is set and does NOT point at the legacy
    worker-router, failing closed if misconfigured.
  - Builds an openai-compatible AsyncOpenAI client pointed at LiteLLM.
  - Enforces logical model aliases (never raw provider names).
  - Attaches structured metadata to every request.
  - Emits structured telemetry to the activity logger (no prompts/responses).
"""

import os
import time
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Logical model aliases — the only values callers should pass as `model`.
# LiteLLM maps these to the real provider model internally.
# ---------------------------------------------------------------------------
MODEL_CODING_FAST = "coding-fast"
MODEL_CODING_DEEP = "coding-deep"
MODEL_REVIEW_INDEPENDENT = "review-independent"

ALLOWED_ALIASES = {MODEL_CODING_FAST, MODEL_CODING_DEEP, MODEL_REVIEW_INDEPENDENT}

# Legacy URL fragments that must never appear in LITELLM_BASE_URL.
_LEGACY_URL_FRAGMENTS = [
    "/api/worker-router",
    "MAAS_ENDPOINT",
    "100.93.15.38:10006",
]


def _validated_base_url() -> str:
    """Return LITELLM_BASE_URL, raising if absent or pointing at legacy router."""
    url = os.environ.get("LITELLM_BASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "LITELLM_BASE_URL is not set. "
            "Set it to the LiteLLM gateway base URL (e.g. http://100.93.15.38:4000/v1)."
        )
    for fragment in _LEGACY_URL_FRAGMENTS:
        if fragment in url:
            raise RuntimeError(
                f"LITELLM_BASE_URL contains legacy worker-router URL fragment '{fragment}'. "
                "This service must not call the worker-router directly. "
                "Update LITELLM_BASE_URL to the LiteLLM gateway."
            )
    return url


def _validated_api_key() -> str:
    key = os.environ.get("LITELLM_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "LITELLM_API_KEY is not set. "
            "Provision a sailratings-factory virtual key in LiteLLM and store it in 1Password."
        )
    if not key.startswith("sk-"):
        raise RuntimeError(
            f"LITELLM_API_KEY does not look like a LiteLLM virtual key (expected 'sk-...'). "
            "Ensure you are using a LiteLLM virtual key, not a provider credential or legacy key."
        )
    return key


def get_async_client():
    """Return a configured AsyncOpenAI client pointing at LiteLLM."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url=_validated_base_url(),
        api_key=_validated_api_key(),
    )


def get_model_hint(default: str = MODEL_CODING_FAST) -> str:
    """Return the model alias from env or the supplied default."""
    hint = os.environ.get("LITELLM_MODEL_HINT", default).strip()
    if hint not in ALLOWED_ALIASES:
        log.warning(
            "LITELLM_MODEL_HINT='%s' is not a recognised alias %s — using '%s'.",
            hint, sorted(ALLOWED_ALIASES), default,
        )
        return default
    return hint


def build_metadata(
    *,
    service: str = "sailratings-factory",
    role: Optional[str] = None,
    lane: Optional[str] = None,
    workflow_id: Optional[str] = None,
    run_id: Optional[str] = None,
    epic_id: Optional[str] = None,
    card_id: Optional[str] = None,
    attempt: Optional[int] = None,
    model_hint: Optional[str] = None,
) -> dict:
    meta = {"service": service}
    if role:        meta["role"]        = role
    if lane:        meta["lane"]        = lane
    if workflow_id: meta["workflow_id"] = workflow_id
    if run_id:      meta["run_id"]      = run_id
    if epic_id:     meta["epic_id"]     = epic_id
    if card_id:     meta["card_id"]     = card_id
    if attempt is not None: meta["attempt"] = attempt
    if model_hint:  meta["model_hint"]  = model_hint
    return meta


class LLMTelemetry:
    """Context manager that logs request telemetry without touching content."""

    def __init__(self, *, role: str, model: str, card_id: Optional[str] = None):
        self.role = role
        self.model = model
        self.card_id = card_id
        self._start: float = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        log.info(
            "llm_request_start role=%s model=%s card=%s",
            self.role, self.model, self.card_id or "-",
        )
        return self

    def record_response(self, *, prompt_tokens: int = 0, completion_tokens: int = 0,
                        cached: bool = False, error: Optional[str] = None):
        latency_ms = int((time.monotonic() - self._start) * 1000)
        if error:
            log.error(
                "llm_request_error role=%s model=%s latency_ms=%d error=%s",
                self.role, self.model, latency_ms, error,
            )
        else:
            log.info(
                "llm_request_end role=%s model=%s latency_ms=%d "
                "prompt_tokens=%d completion_tokens=%d cached=%s",
                self.role, self.model, latency_ms,
                prompt_tokens, completion_tokens, cached,
            )

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.record_response(error=str(exc_val))
        return False
