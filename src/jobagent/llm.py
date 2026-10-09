"""LLM providers — OpenRouter, Anthropic, Mistral, or a deterministic mock.

Ranking tries the configured providers in order. A 429 waits for a bounded
Retry-After and is tried once more, then that provider cools down and the next
configured provider with a key is used. A cycle stops calling providers after
its call or time cap and the remaining matches use the keyword heuristic.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODELS = {
    "openrouter": "openrouter/free",
    "anthropic": "claude-sonnet-4-20250514",
    "mistral": "mistral-small-latest",
    "mock": "mock",
}
_PROVIDER_KEYS = {
    "openrouter": "openrouter_key",
    "anthropic": "anthropic_key",
    "mistral": "mistral_key",
}
_DEFAULT_ORDER = ("openrouter", "mistral", "anthropic")

_cooldowns: dict[str, float] = {}


class MissingLLMKey(ValueError):
    """No configured live provider has an API key."""


class LlmBudgetExceeded(RuntimeError):
    """This cycle has used its LLM call or time cap."""


class RateLimitError(RuntimeError):
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


@dataclass
class LlmCycleStats:
    calls: int = 0
    rate_limits: int = 0
    failovers: int = 0
    fallbacks: int = 0
    seconds: float = 0.0
    last_provider: str = ""
    last_model: str = ""


_STATS = LlmCycleStats()


def begin_llm_cycle() -> LlmCycleStats:
    """Reset the per-cycle counters. Cooldowns keep running until they expire."""
    global _STATS
    _STATS = LlmCycleStats()
    return _STATS


def llm_cycle_stats() -> LlmCycleStats:
    return _STATS


def clear_provider_cooldowns() -> None:
    _cooldowns.clear()


def log_llm_cycle_summary() -> None:
    stats = _STATS
    logger.info(
        "LLM cycle summary — calls=%s rate_limits=%s failovers=%s fallbacks=%s seconds=%.1f",
        stats.calls,
        stats.rate_limits,
        stats.failovers,
        stats.fallbacks,
        stats.seconds,
    )


def configured_provider_order(config: dict) -> list[str]:
    """Ordered provider names from settings. Mock short-circuits the list."""
    llm = config.get("llm") or {}
    raw = llm.get("providers")
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace("\n", ",").split(",")]
    ordered: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            name = str(item or "").lower().strip()
            if name and name not in ordered:
                ordered.append(name)
    if not ordered:
        primary = str(llm.get("provider") or "openrouter").lower().strip() or "openrouter"
        ordered = [primary]
        for name in _DEFAULT_ORDER:
            if name not in ordered:
                ordered.append(name)
    if ordered[0] == "mock":
        return ["mock"]
    return [name for name in ordered if name in DEFAULT_MODELS and name != "mock"]


def model_for(config: dict, provider: str) -> str:
    llm = config.get("llm") or {}
    models = llm.get("models") if isinstance(llm.get("models"), dict) else {}
    specific = str((models or {}).get(provider) or "").strip()
    if specific:
        return specific
    shared = str(llm.get("model") or "").strip()
    primary = str(llm.get("provider") or "").lower().strip()
    if provider == "openrouter":
        if shared and (primary in {"", "openrouter"} or "/" in shared):
            return shared
        return DEFAULT_MODELS["openrouter"]
    if provider == primary and shared:
        return shared
    return DEFAULT_MODELS.get(provider, provider)


def llm_settings(config: dict) -> tuple[str, str]:
    """Primary route: the first configured provider that has a key."""
    order = configured_provider_order(config)
    if not order:
        raise ValueError("No LLM providers configured. Use openrouter, mistral, anthropic, or mock.")
    if order == ["mock"]:
        return "mock", "mock"
    for name in order:
        if provider_has_key(config, name):
            return name, model_for(config, name)
    return order[0], model_for(config, order[0])


def provider_has_key(config: dict, provider: str | None = None) -> bool:
    if provider is None:
        provider, _ = llm_settings(config)
    if provider == "mock":
        return True
    dest = _PROVIDER_KEYS.get(provider or "")
    if not dest:
        return False
    key = str((config.get("api") or {}).get(dest) or "").strip()
    return bool(key) and not key.upper().startswith("YOUR_")


def llm_form_view(config: dict | None = None) -> dict[str, str]:
    """Settings display for the ranking model and caps. No secrets."""
    cfg = config or {}
    llm = cfg.get("llm") or {}
    order = configured_provider_order(cfg)
    model = str((llm.get("models") or {}).get("openrouter") or "").strip() if isinstance(llm.get("models"), dict) else ""
    if not model:
        model = model_for(cfg, "openrouter")
    limits = _limits(cfg)

    def shown(value: float) -> str:
        if value == int(value):
            return str(int(value))
        return str(value)

    return {
        "model": model,
        "providers": ", ".join(order),
        "max_llm_calls": shown(limits["calls"]),
        "max_llm_seconds": shown(limits["seconds"]),
    }


def _limits(config: dict) -> dict[str, float]:
    llm = config.get("llm") or {}

    def number(name: str, default: float) -> float:
        raw = llm.get(name)
        if raw is None or str(raw).strip() == "":
            return float(default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    return {
        "calls": max(0.0, number("max_llm_calls", 40)),
        "seconds": max(0.0, number("max_llm_seconds", 90)),
        "backoff": max(0.0, number("max_backoff_seconds", 8)),
        "cooldown": max(0.0, number("cooldown_seconds", 120)),
        "retry_backoff": max(0.0, number("retry_backoff_seconds", 2)),
    }


def _cooling(provider: str) -> bool:
    return time.monotonic() < _cooldowns.get(provider, 0.0)


def _cool(provider: str, seconds: float) -> None:
    _cooldowns[provider] = time.monotonic() + max(0.0, seconds)


def _api_key(config: dict, provider: str) -> str:
    dest = _PROVIDER_KEYS.get(provider)
    key = str((config.get("api") or {}).get(dest or "") or "").strip()
    if not key or key.upper().startswith("YOUR_"):
        raise MissingLLMKey(f"Missing API key for llm provider '{provider}'.")
    return key


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def parse_retry_after(header: str | None) -> float | None:
    if header is None:
        return None
    text = str(header).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bounded_wait(retry_after: float | None, limits: dict[str, float]) -> float:
    wait = limits["retry_backoff"] if retry_after is None else float(retry_after)
    return max(0.0, min(wait, limits["backoff"]))


def _budget_blocked(limits: dict[str, float]) -> bool:
    return _STATS.calls >= limits["calls"] or _STATS.seconds >= limits["seconds"]


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


def _as_rate_limit(exc: Exception) -> RateLimitError | None:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status != 429 and "429" not in text and "rate limit" not in text:
        return None
    header = None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    if headers:
        header = headers.get("retry-after") or headers.get("Retry-After")
    return RateLimitError(parse_retry_after(header))


def _complete_anthropic(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        limited = _as_rate_limit(exc)
        if limited is not None:
            raise limited from None
        raise
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
    try:
        with Mistral(api_key=api_key) as client:
            response = client.chat.complete(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
            )
    except Exception as exc:
        limited = _as_rate_limit(exc)
        if limited is not None:
            raise limited from None
        raise
    return response.choices[0].message.content.strip()


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content or "").strip()


def _complete_openrouter(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    try:
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": model, "messages": messages, "max_tokens": max_tokens},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError("openrouter request failed") from None
    if response.status_code == 429:
        raise RateLimitError(parse_retry_after(response.headers.get("Retry-After")))
    if response.status_code >= 400:
        raise RuntimeError(f"openrouter http {response.status_code}")
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("openrouter response was not a chat completion") from None
    text = _message_text(content)
    if not text:
        raise RuntimeError("openrouter response was empty")
    return text


_COMPLETERS = {
    "openrouter": _complete_openrouter,
    "anthropic": _complete_anthropic,
    "mistral": _complete_mistral,
}


def _attempt(provider: str, api_key: str, model: str, system: str, user: str, max_tokens: int, limits: dict[str, float]) -> str:
    """One provider. A 429 waits at most max_backoff_seconds, then is tried once more."""
    completer = _COMPLETERS[provider]
    last_limit: RateLimitError | None = None
    for attempt in (1, 2):
        if _budget_blocked(limits):
            raise LlmBudgetExceeded()
        started = time.monotonic()
        _STATS.calls += 1
        try:
            text = completer(api_key, model, system, user, max_tokens)
            _STATS.seconds += time.monotonic() - started
            return text
        except RateLimitError as exc:
            _STATS.seconds += time.monotonic() - started
            _STATS.rate_limits += 1
            last_limit = exc
            if attempt == 2:
                raise
            wait = _bounded_wait(exc.retry_after, limits)
            if _STATS.seconds + wait > limits["seconds"]:
                raise
            logger.warning(
                "LLM provider %s rate limited (attempt %s/2), waiting %.1fs",
                provider,
                attempt,
                wait,
            )
            time.sleep(wait)
            _STATS.seconds += wait
        except Exception:
            _STATS.seconds += time.monotonic() - started
            logger.warning("LLM provider %s failed", provider)
            raise
    raise last_limit or RateLimitError()


def complete(config: dict, *, system: str, user: str, max_tokens: int = 1024) -> str:
    """Chat completion using the first healthy configured provider that has a key."""
    order = configured_provider_order(config)
    if order == ["mock"]:
        _STATS.last_provider = "mock"
        _STATS.last_model = "mock"
        return _complete_mock(system, user, max_tokens)

    limits = _limits(config)
    if _budget_blocked(limits):
        raise LlmBudgetExceeded()

    candidates = [name for name in order if provider_has_key(config, name) and not _cooling(name)]
    if not candidates:
        if _budget_blocked(limits):
            raise LlmBudgetExceeded()
        if any(provider_has_key(config, name) for name in order):
            raise RuntimeError("all configured LLM providers failed")
        raise MissingLLMKey("No configured LLM provider has an API key.")

    saw_failure = False
    last_error: Exception | None = None
    for provider in candidates:
        if saw_failure:
            _STATS.failovers += 1
        model = model_for(config, provider)
        try:
            text = _attempt(provider, _api_key(config, provider), model, system, user, max_tokens, limits)
        except LlmBudgetExceeded:
            raise
        except Exception as exc:
            last_error = exc
            saw_failure = True
            _cool(provider, limits["cooldown"])
            logger.warning("LLM provider %s cooling down for %.0fs", provider, limits["cooldown"])
            continue
        _STATS.last_provider = provider
        _STATS.last_model = model
        return text
    if isinstance(last_error, LlmBudgetExceeded) or _budget_blocked(limits):
        raise LlmBudgetExceeded()
    raise RuntimeError("all configured LLM providers failed")


def complete_json(config: dict, *, system: str, user: str, max_tokens: int = 256) -> dict:
    """Run a completion and parse the response as JSON."""
    text = complete(config, system=system, user=user, max_tokens=max_tokens)
    return _parse_json_response(text)
