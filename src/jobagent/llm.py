"""LLM provider abstraction — Anthropic, Mistral, or a deterministic mock.

Application startup never requires a live key. Ranking uses a heuristic fallback
when the configured provider has no credentials.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "mistral": "mistral-small-latest",
    "mock": "mock",
}


class MissingLLMKey(ValueError):
    """Configured live provider has no API key."""


def llm_settings(config: dict) -> tuple[str, str]:
    llm = config.get("llm", {}) or {}
    provider = (llm.get("provider") or "anthropic").lower().strip()
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"Unknown llm.provider '{provider}'. Use 'anthropic', 'mistral', or 'mock'.")
    model = (llm.get("model") or "").strip() or DEFAULT_MODELS[provider]
    return provider, model


def provider_has_key(config: dict, provider: str | None = None) -> bool:
    if provider is None:
        provider, _ = llm_settings(config)
    if provider == "mock":
        return True
    api = config.get("api") or {}
    if provider == "anthropic":
        key = str(api.get("anthropic_key") or "").strip()
    elif provider == "mistral":
        key = str(api.get("mistral_key") or "").strip()
    else:
        return False
    return bool(key) and not key.startswith("YOUR_")


def _api_key(config: dict, provider: str) -> str:
    api = config.get("api", {})
    if provider == "anthropic":
        key = api.get("anthropic_key", "")
    else:
        key = api.get("mistral_key", "")
    if not key or str(key).startswith("YOUR_"):
        env_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "MISTRAL_API_KEY"
        raise MissingLLMKey(
            f"Missing {env_name} (or api.{provider}_key in settings). "
            f"llm.provider is '{provider}'."
        )
    return str(key)


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _complete_mock(system: str, user: str, max_tokens: int) -> str:
    hay = f"{system}\n{user}".lower()
    score = 50
    if "target titles: it manager" in hay or "target titles: infrastructure" in hay:
        score = 78
    if "target titles: telecommunications" in hay or "target titles: nurse" in hay:
        score = 18
    if "nurse" in hay and "job listing" in hay and "nurse" in hay.split("job listing", 1)[-1]:
        score = 88
    return json.dumps({"score": score, "reason": "Mock provider (deterministic, no network)."})


def _complete_anthropic(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


def _complete_mistral(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    try:
        from mistralai.client import Mistral
    except ImportError:
        from mistralai import Mistral

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    with Mistral(api_key=api_key) as client:
        response = client.chat.complete(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
        )
    return response.choices[0].message.content.strip()


def complete(config: dict, *, system: str, user: str, max_tokens: int = 1024) -> str:
    """Run a chat completion using the configured provider."""
    provider, model = llm_settings(config)
    if provider == "mock":
        return _complete_mock(system, user, max_tokens)

    api_key = _api_key(config, provider)
    retries = int(config.get("llm", {}).get("max_retries", 3))
    backoff = float(config.get("llm", {}).get("retry_backoff_seconds", 2.0))

    logger.debug("LLM request: provider=%s model=%s max_tokens=%s", provider, model, max_tokens)

    last_error = None
    completer: Callable[..., str] = _complete_anthropic if provider == "anthropic" else _complete_mistral
    for attempt in range(retries):
        try:
            return completer(api_key, model, system, user, max_tokens)
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            retryable = "429" in msg or "rate" in msg or "capacity" in msg or "timeout" in msg
            if not retryable or attempt >= retries - 1:
                raise
            wait = backoff * (2 ** attempt)
            logger.warning("LLM rate limited (attempt %s/%s), retrying in %.1fs", attempt + 1, retries, wait)
            time.sleep(wait)

    raise last_error  # pragma: no cover


def complete_json(config: dict, *, system: str, user: str, max_tokens: int = 256) -> dict:
    """Run a completion and parse the response as JSON."""
    text = complete(config, system=system, user=user, max_tokens=max_tokens)
    return _parse_json_response(text)
