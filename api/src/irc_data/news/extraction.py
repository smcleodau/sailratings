"""LLM extraction of structured entities from sailing news articles.

Implements the extraction step of SPEC-14 Sec. 4 (Unstructured News Processing).
Article text is passed to the Sailing LLM with a strict JSON-schema prompt and
the response is parsed/validated into:

    {
      "mentioned_boats": ["WOXI", "Comanche"],
      "mentioned_events": ["Rolex Sydney Hobart"],
      "sentiment": "positive",
      "key_takeaway": "Comanche breaks race record."
    }

Provider model
--------------
The spec names the "Sailing LLM (GLM 5.2)" as the extractor. The configured
provider here is selectable via env so the workflow runs against whichever
model is provisioned in the environment:

* ``SAILING_LLM_PROVIDER`` - ``anthropic`` (default; this repo ships an
  Anthropic key and Claude client) or ``gemini`` / ``glm`` when provisioned.
* ``SAILING_LLM_MODEL`` - model id; defaults per provider.

Only the Anthropic path is wired by default (matching
``insights_service.py``); other providers raise a clear error so the caller
can configure them. The extraction function is also injectable in the
workflow so deterministic tests can supply a non-LLM extractor.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Strict extraction schema (SPEC-14 Sec. 4)
# ---------------------------------------------------------------------------

_VALID_SENTIMENTS = {"positive", "negative", "neutral"}


class NewsExtraction(BaseModel):
    """Validated LLM extraction result matching the SPEC-14 Sec. 4 schema."""

    mentioned_boats: list[str] = Field(default_factory=list)
    mentioned_events: list[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    key_takeaway: str = ""

    @field_validator("sentiment", mode="before")
    @classmethod
    def _normalize_sentiment(cls, v: Any) -> str:
        if v is None:
            return "neutral"
        v = str(v).strip().lower()
        # Map common LLM variants onto the three canonical buckets.
        if v in _VALID_SENTIMENTS:
            return v
        if v in {"pos", "good", "bullish", "favourable", "favorable"}:
            return "positive"
        if v in {"neg", "bad", "bearish", "unfavourable", "unfavorable"}:
            return "negative"
        return "neutral"

    @field_validator("mentioned_boats", "mentioned_events", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        """Accept strings, dicts with a name field, or lists of either."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            out: list[str] = []
            for item in v:
                if isinstance(item, str):
                    if item.strip():
                        out.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("boat_name") or item.get("name") or item.get("event")
                    if isinstance(name, str) and name.strip():
                        out.append(name.strip())
            return out
        return []

    @field_validator("key_takeaway", mode="before")
    @classmethod
    def _str_key_takeaway(cls, v: Any) -> str:
        return "" if v is None else str(v).strip()

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump()


@dataclass
class ExtractionResult:
    """Outcome of extracting entities from one article."""

    extraction: NewsExtraction
    model: str
    raw_response: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are the Sailing LLM entity extractor for the SailRatings platform. You read a sailing news article and extract structured entities into a strict JSON object.

Return ONLY a JSON object with exactly these keys and no extra commentary:

{
  "mentioned_boats": ["..."],
  "mentioned_events": ["..."],
  "sentiment": "positive | negative | neutral",
  "key_takeaway": "..."
}

Rules:
- mentioned_boats: specific racing yacht NAMES and/or SAIL NUMBERS mentioned as competing boats (e.g. "Comanche", "WOXI", "AUS1213"). Do NOT include class names (e.g. "TP52"), brands (e.g. "Rolex"), people, or generic nouns. Empty list if none.
- mentioned_events: named regattas/races/series the article references (e.g. "Rolex Sydney Hobart", "Fastnet Race"). Empty list if none.
- sentiment: overall sentiment of the article toward the sailing/racing outcome - one of "positive", "negative", "neutral".
- key_takeaway: a single concise sentence summarising the most important racing news. Empty string if unclear.
- Output must be valid JSON. No markdown fences, no trailing prose."""


def build_user_prompt(title: str, body_text: str) -> str:
    """Assemble the user message carrying the article text."""
    body = (body_text or "").strip()
    title = (title or "").strip() or "Untitled"
    return f"ARTICLE TITLE:\n{title}\n\nARTICLE BODY:\n{body[:30000]}"


# ---------------------------------------------------------------------------
# JSON parsing + repair
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip())


def _extract_first_json_object(text: str) -> str | None:
    """Return the substring of the first balanced {...} object, or None."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_extraction(raw: str | None) -> NewsExtraction:
    """Parse and validate the LLM's raw text response into NewsExtraction.

    Tolerant of markdown fences and surrounding prose. Raises ValueError if no
    JSON object can be located or it fails Pydantic validation.
    """
    if not raw or not raw.strip():
        raise ValueError("empty LLM response")

    cleaned = _strip_fences(raw)

    # Try direct parse first, then fall back to extracting the first {...}.
    candidates: list[str] = []
    if cleaned.startswith("{"):
        candidates.append(cleaned)
    obj = _extract_first_json_object(cleaned)
    if obj:
        candidates.append(obj)

    last_err: Exception | None = None
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
            continue
        try:
            return NewsExtraction.model_validate(data)
        except ValidationError as e:
            last_err = e
            continue

    raise ValueError(f"could not parse extraction JSON: {last_err}")


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    # The spec names "GLM 5.2" as the Sailing LLM; wired when a provider/key
    # is provisioned for it.
    "glm": "glm-5.2",
}


def _provider() -> str:
    return os.environ.get("SAILING_LLM_PROVIDER", "anthropic").lower()


def _model_name(provider: str) -> str:
    return os.environ.get("SAILING_LLM_MODEL") or _DEFAULT_MODELS.get(provider, "")


# Type of an injectable extractor (used for deterministic tests).
ExtractorFn = Callable[[str, str], ExtractionResult]


# ---------------------------------------------------------------------------
# Default LLM-backed extractor
# ---------------------------------------------------------------------------


def extract_with_llm(title: str, body_text: str) -> ExtractionResult:
    """Call the configured Sailing LLM and return a validated ExtractionResult.

    Raises RuntimeError if the provider is not configured. On a parse failure
    the result is returned with ``error`` set rather than raising, so the
    workflow can record the failure on the article.
    """
    provider = _provider()
    model = _model_name(provider)

    try:
        if provider == "anthropic":
            raw, used_model = _call_anthropic(title, body_text, model)
        elif provider == "gemini":
            raw, used_model = _call_gemini(title, body_text, model)
        elif provider == "glm":
            raw, used_model = _call_glm(title, body_text, model)
        else:
            raise RuntimeError(f"unknown SAILING_LLM_PROVIDER={provider!r}")
    except Exception as e:  # network / auth / rate-limit
        return ExtractionResult(
            extraction=NewsExtraction(), model=model, error=f"LLM call failed: {e}"
        )

    try:
        extraction = parse_extraction(raw)
    except ValueError as e:
        return ExtractionResult(
            extraction=NewsExtraction(),
            model=used_model,
            raw_response=raw,
            error=str(e),
        )
    return ExtractionResult(extraction=extraction, model=used_model, raw_response=raw)


def _call_anthropic(title: str, body_text: str, model: str) -> tuple[str, str]:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)
    if not model:
        model = _DEFAULT_MODELS["anthropic"]
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(title, body_text)}],
    )
    text = "".join(
        block.text  # type: ignore[attr-defined]
        for block in resp.content
        if getattr(block, "type", None) == "text"
    )
    return text, model


def _call_gemini(title: str, body_text: str, model: str) -> tuple[str, str]:
    import google.generativeai as genai  # type: ignore

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    genai.configure(api_key=api_key)
    model = model or "gemini-2.5-flash"
    schema = {
        "type": "object",
        "properties": {
            "mentioned_boats": {"type": "array", "items": {"type": "string"}},
            "mentioned_events": {"type": "array", "items": {"type": "string"}},
            "sentiment": {"type": "string"},
            "key_takeaway": {"type": "string"},
        },
        "required": ["mentioned_boats", "mentioned_events", "sentiment", "key_takeaway"],
    }
    m = genai.GenerativeModel(
        model,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
        system_instruction=EXTRACTION_SYSTEM_PROMPT,
    )
    resp = m.generate_content(build_user_prompt(title, body_text))
    return resp.text, model


def _call_glm(title: str, body_text: str, model: str) -> tuple[str, str]:
    """Call a GLM (Zhipu) chat endpoint via OpenAI-compatible API.

    Wired when ``SAILING_LLM_PROVIDER=glm`` and ``GLM_API_KEY`` is set. This is
    the spec's named Sailing LLM (GLM 5.2); the path uses the OpenAI-compatible
    chat-completions surface Zhipu exposes, via httpx to avoid a hard dep.
    """
    import httpx

    api_key = os.environ.get("GLM_API_KEY")
    base_url = os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    if not api_key:
        raise RuntimeError("GLM_API_KEY not configured")
    model = model or "glm-5.2"
    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=60.0,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(title, body_text)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 1024,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"], model
