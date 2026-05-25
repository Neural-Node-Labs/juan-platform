"""
C-02 · PROVIDER_RUNTIME
Resolves which LLM provider and API mode to use, authenticates, executes
the call, handles retries and one-shot fallback across 18+ providers.
"""
from __future__ import annotations

import os
import time
import random
from typing import Any

from logger import trace, warn, error as log_error  # noqa: F401
import logger

# ── Error codes ────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    def __init__(self, error_code: str, provider: str, message: str,
                 http_status: int = 0, retries: int = 0):
        super().__init__(message)
        self.error_code = error_code
        self.provider = provider
        self.http_status = http_status
        self.retries = retries

    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code,
            "provider": self.provider,
            "http_status": self.http_status,
            "retries": self.retries,
            "message": str(self),
        }


# ── Provider registry ─────────────────────────────────────────────────────────

# Maps provider name → (base_url, default_api_mode, env_key)
PROVIDER_REGISTRY: dict[str, tuple[str, str, str]] = {
    "anthropic":  ("https://api.anthropic.com/v1",           "anthropic_messages",  "ANTHROPIC_API_KEY"),
    "openai":     ("https://api.openai.com/v1",              "chat_completions",    "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1",           "chat_completions",    "OPENROUTER_API_KEY"),
    "groq":       ("https://api.groq.com/openai/v1",         "chat_completions",    "GROQ_API_KEY"),
    "mistral":    ("https://api.mistral.ai/v1",              "chat_completions",    "MISTRAL_API_KEY"),
    "cohere":     ("https://api.cohere.ai/v2",               "chat_completions",    "COHERE_API_KEY"),
    "together":   ("https://api.together.xyz/v1",            "chat_completions",    "TOGETHER_API_KEY"),
    "fireworks":  ("https://api.fireworks.ai/inference/v1",  "chat_completions",    "FIREWORKS_API_KEY"),
    "deepseek":   ("https://api.deepseek.com/v1",            "chat_completions",    "DEEPSEEK_API_KEY"),
    "xai":        ("https://api.x.ai/v1",                    "chat_completions",    "XAI_API_KEY"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta", "chat_completions", "GEMINI_API_KEY"),
    "ollama":     ("http://localhost:11434/v1",              "chat_completions",    ""),
    "lmstudio":   ("http://localhost:1234/v1",               "chat_completions",    ""),
    "custom":     ("",                                        "chat_completions",    "CUSTOM_API_KEY"),
}

MAX_RETRIES = 3


def _resolve_api_key(provider: str) -> tuple[str, str]:
    """Returns (api_key, key_source)."""
    _, _, env_key = PROVIDER_REGISTRY.get(provider, ("", "", ""))
    if env_key and os.environ.get(env_key):
        return os.environ[env_key], f"env:{env_key}"
    # Allow generic override
    generic = os.environ.get("JUAN_API_KEY", "")
    if generic:
        return generic, "env:JUAN_API_KEY"
    return "", "missing"


def _build_headers(provider: str, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _make_request(provider: str, api_mode: str, base_url: str,
                  api_key: str, messages: list, model: str,
                  max_tokens: int, stream: bool, tools: list | None) -> dict:
    """
    Actual HTTP call.  Returns raw response dict.
    Raises ProviderError on HTTP errors.
    """
    import urllib.request
    import json as _json

    if api_mode == "anthropic_messages":
        url = f"{base_url}/messages"
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
    else:
        # OpenAI-compatible chat completions
        url = f"{base_url}/chat/completions"
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools

    payload = _json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers=_build_headers(provider, api_key),
                                  method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return _json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        status = exc.code
        if status in (401, 403):
            raise ProviderError("AUTH_FAIL", provider,
                                f"Auth failed {status}: {body_text}", http_status=status)
        if status == 429:
            raise ProviderError("RATE_LIMITED", provider,
                                f"Rate limited: {body_text}", http_status=status)
        raise ProviderError("UPSTREAM_ERROR", provider,
                            f"HTTP {status}: {body_text}", http_status=status)
    except TimeoutError as exc:
        raise ProviderError("TIMEOUT", provider, "Request timed out") from exc


def _parse_response(provider: str, api_mode: str, raw: dict) -> dict:
    """Normalize provider response to out_schema."""
    if api_mode == "anthropic_messages":
        content_blocks = raw.get("content", [])
        text = " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        tool_calls = [b for b in content_blocks if b.get("type") == "tool_use"]
        usage_raw = raw.get("usage", {})
        return {
            "content": text,
            "role": "assistant",
            "finish_reason": raw.get("stop_reason", "stop"),
            "tool_calls": tool_calls,
            "reasoning": None,
            "usage": {
                "input_tokens":       usage_raw.get("input_tokens", 0),
                "output_tokens":      usage_raw.get("output_tokens", 0),
                "cache_read_tokens":  usage_raw.get("cache_read_input_tokens", 0),
                "cache_write_tokens": usage_raw.get("cache_creation_input_tokens", 0),
                "reasoning_tokens":   0,
            },
        }
    else:
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        usage_raw = raw.get("usage", {})
        return {
            "content": msg.get("content", ""),
            "role": "assistant",
            "finish_reason": choice.get("finish_reason", "stop"),
            "tool_calls": msg.get("tool_calls", []),
            "reasoning": msg.get("reasoning_content"),
            "usage": {
                "input_tokens":       usage_raw.get("prompt_tokens", 0),
                "output_tokens":      usage_raw.get("completion_tokens", 0),
                "cache_read_tokens":  usage_raw.get("prompt_cache_hit_tokens", 0),
                "cache_write_tokens": usage_raw.get("prompt_cache_miss_tokens", 0),
                "reasoning_tokens":   usage_raw.get("reasoning_tokens", 0),
            },
        }


# ── Main call ─────────────────────────────────────────────────────────────────

def call_provider(
    messages: list,
    model: str,
    provider: str,
    api_mode: str | None = None,
    max_tokens: int = 8000,
    stream: bool = False,
    fallback_model: dict | None = None,
    tools: list | None = None,
) -> dict:
    """
    C-02 entry point.  Returns out_schema dict or raises ProviderError.
    """
    reg = PROVIDER_REGISTRY.get(provider, PROVIDER_REGISTRY["openai"])
    base_url, default_mode, _ = reg
    resolved_mode = api_mode or default_mode
    api_key, key_source = _resolve_api_key(provider)

    trace("PROVIDER_RUNTIME", "provider_selected",
          provider=provider, api_mode=resolved_mode, key_source=key_source)

    last_exc: ProviderError | None = None

    for attempt in range(MAX_RETRIES):
        t0 = time.time()
        try:
            raw = _make_request(provider, resolved_mode, base_url,
                                api_key, messages, model,
                                max_tokens, stream, tools)
            result = _parse_response(provider, resolved_mode, raw)
            result["provider_used"] = provider
            result["fallback_used"] = False
            latency = round((time.time() - t0) * 1000)
            trace("PROVIDER_RUNTIME", "latency_ms",
                  time_to_first_token=latency, total_duration=latency)
            return result
        except ProviderError as exc:
            last_exc = exc
            if exc.error_code == "AUTH_FAIL":
                break  # no point retrying
            backoff = min(0.5 * (2 ** attempt) + random.random(), 30)
            trace("PROVIDER_RUNTIME", "retry_n",
                  attempt=attempt + 1, error_type=exc.error_code,
                  backoff_ms=round(backoff * 1000))
            time.sleep(backoff)

    # ── Fallback ──────────────────────────────────────────────────────────────
    if fallback_model:
        fb_provider = fallback_model.get("provider", provider)
        fb_model    = fallback_model.get("model", model)
        fb_reg      = PROVIDER_REGISTRY.get(fb_provider, reg)
        fb_base, fb_mode, _ = fb_reg
        fb_key, fb_key_src  = _resolve_api_key(fb_provider)
        trace("PROVIDER_RUNTIME", "fallback_activated",
              from_provider=provider, to_provider=fb_provider,
              trigger_reason=last_exc.error_code if last_exc else "retries_exhausted",
              http_status=last_exc.http_status if last_exc else 0)
        try:
            t0 = time.time()
            raw = _make_request(fb_provider, fb_mode, fb_base,
                                fb_key, messages, fb_model,
                                max_tokens, stream, tools)
            result = _parse_response(fb_provider, fb_mode, raw)
            result["provider_used"] = fb_provider
            result["fallback_used"] = True
            latency = round((time.time() - t0) * 1000)
            trace("PROVIDER_RUNTIME", "latency_ms",
                  time_to_first_token=latency, total_duration=latency)
            return result
        except ProviderError as exc:
            raise ProviderError("FALLBACK_EXHAUSTED", fb_provider,
                                f"Fallback also failed: {exc}",
                                http_status=exc.http_status,
                                retries=MAX_RETRIES) from exc

    raise last_exc or ProviderError("UPSTREAM_ERROR", provider,
                                    "All retries exhausted", retries=MAX_RETRIES)
