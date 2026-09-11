"""LLM provider abstraction — the ONE place the model/provider is configured.

Every LLM call in the app goes through ``complete_json`` (structured extraction)
or ``complete_text`` (prose). The provider, model, key, and rate limit all come
from the environment (see CLAUDE.md "LLM Layer"), so swapping providers or models
is a config change, never an edit to extract.py / match.py / cover-letter code.

Supported providers (set LLM_PROVIDER in .env):
  * "openai" — OpenAI. Active default. Two models, split by task cost/quality:
    OPENAI_MODEL_SMALL (default gpt-5-nano) for complete_json — extraction and
    matching are structured, low-complexity tasks. OPENAI_MODEL_LETTER (default
    gpt-5-mini) for complete_text — cover letters are the one output a human
    actually reads, so they get the better model even though the volume-driven
    cost difference is negligible either way.
  * "groq"   — Groq Cloud. Dormant fallback (kept working, not required).
  * "gemini" — Google Gemini. Dormant fallback (kept working, not required).

Rate limiting + retries live here so callers don't reimplement them:
  * a simple in-process throttle keeps us under ``LLM_RPM`` requests/minute;
  * 429s and transient 5xx retry, preferring the server's retry-after hint;
  * only a 429 whose retry delay exceeds ``_DAILY_RETRY_SECS`` (5 min) is treated
    as a genuine daily exhaustion and raises ``DailyQuotaError`` to stop a batch.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Config (read once at import; all from env, nothing hardcoded as truth)
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai").lower()

# OpenAI (active default) — split by task, see module docstring.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL_SMALL = os.environ.get("OPENAI_MODEL_SMALL", "gpt-5-nano")
OPENAI_MODEL_LETTER = os.environ.get("OPENAI_MODEL_LETTER", "gpt-5-mini")

# Groq (dormant fallback)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Gemini (dormant fallback)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

try:
    LLM_RPM = max(1, int(os.environ.get("LLM_RPM", "8")))
except ValueError:
    LLM_RPM = 8

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 4, 8)
_DAILY_RETRY_SECS = 300.0
_MAX_BACKOFF_SECS = 70.0
_RETRY_DELAY_RE = re.compile(r"retry[_-]?delay['\"\s:]+['\"]?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class LLMError(RuntimeError):
    """Base class for LLM-layer failures."""


class DailyQuotaError(LLMError):
    """Raised on a daily-quota 429 that a short retry won't clear — stop the batch."""


# ---------------------------------------------------------------------------
# In-process rate limiter
# ---------------------------------------------------------------------------
_throttle_lock = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    min_interval = 60.0 / LLM_RPM
    with _throttle_lock:
        wait = min_interval - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


# ---------------------------------------------------------------------------
# Shared error helpers (work across both providers via duck typing)
# ---------------------------------------------------------------------------
def _is_quota_429(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    low = (str(getattr(exc, "message", "") or "") + " " + str(exc)).lower()
    return code == 429 or "429" in low or "resource_exhausted" in low or "resourceexhausted" in low


def _is_transient_5xx(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    low = (str(getattr(exc, "message", "") or "") + " " + str(exc)).lower()
    if code in (500, 502, 503, 504):
        return True
    return any(s in low for s in ("unavailable", "overloaded", "internal error", "503", "500"))


def _parse_gemini_retry_delay(exc: Exception) -> float | None:
    m = _RETRY_DELAY_RE.search(str(getattr(exc, "message", "") or "") + " " + str(exc))
    return float(m.group(1)) if m else None


def _parse_retry_after_header(exc: Exception) -> float | None:
    """Extract retry-after seconds from an OpenAI-compatible rate-limit response.

    Shared by Groq and OpenAI — both are httpx-based clients with standard
    retry-after / x-ratelimit-reset-requests headers.
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", {}) or {}
    ra = headers.get("retry-after") or headers.get("x-ratelimit-reset-requests")
    if ra is None:
        return None
    try:
        return float(ra)
    except (ValueError, TypeError):
        m = re.match(r"PT(\d+(?:\.\d+)?)S", str(ra), re.IGNORECASE)
        return float(m.group(1)) if m else None


def _inject_truststore() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        logger.debug("truststore not available; using default TLS trust store")


# ---------------------------------------------------------------------------
# Shared retry/backoff loop for OpenAI-compatible chat completion APIs
# (OpenAI's own SDK and Groq's OpenAI-compatible SDK share this call shape).
# ---------------------------------------------------------------------------
def _chat_completion_with_retry(
    *,
    provider_label: str,
    client: Any,
    model: str,
    system_prompt: str,
    user_content: str,
    config_kwargs: dict[str, Any],
):
    response_format = config_kwargs.get("response_format")

    call_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if "temperature" in config_kwargs:
        call_kwargs["temperature"] = config_kwargs["temperature"]
    if response_format:
        call_kwargs["response_format"] = response_format

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        try:
            return client.chat.completions.create(**call_kwargs)
        except Exception as exc:
            last_exc = exc
            is_429 = _is_quota_429(exc)
            retry_delay = _parse_retry_after_header(exc) if is_429 else None

            if is_429 and retry_delay is not None and retry_delay > _DAILY_RETRY_SECS:
                raise DailyQuotaError(
                    f"{provider_label} quota won't clear for {retry_delay:.0f}s — stopping."
                ) from exc

            retryable = is_429 or _is_transient_5xx(exc)
            if retryable and attempt < _MAX_RETRIES:
                if retry_delay is not None:
                    delay = min(retry_delay + 1.0, _MAX_BACKOFF_SECS)
                else:
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "%s %s (attempt %d/%d) — backing off %.1fs",
                    provider_label, "429" if is_429 else "transient 5xx",
                    attempt + 1, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise
    raise LLMError(f"{provider_label} call failed after {_MAX_RETRIES} retries: {last_exc}")


# ---------------------------------------------------------------------------
# OpenAI backend (active default)
# ---------------------------------------------------------------------------
_openai_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if not OPENAI_API_KEY:
        raise LLMError("No API key — set OPENAI_API_KEY in .env")
    from openai import OpenAI
    import httpx
    # Same AV TLS-interception workaround as Groq — see _get_groq_client.
    http_client = httpx.Client(verify=False)
    _openai_client = OpenAI(api_key=OPENAI_API_KEY, http_client=http_client)
    return _openai_client


def _generate_openai(
    system_prompt: str, user_content: str, model: str, config_kwargs: dict[str, Any]
):
    """Single OpenAI chat completion with throttle + retry/backoff.

    The GPT-5 family only supports the default temperature (1) — passing any
    other value is a 400 ("Unsupported value... Only the default (1) value is
    supported"). Drop it here rather than make every caller special-case it.
    """
    openai_kwargs = {k: v for k, v in config_kwargs.items() if k != "temperature"}
    return _chat_completion_with_retry(
        provider_label="OpenAI",
        client=_get_openai_client(),
        model=model,
        system_prompt=system_prompt,
        user_content=user_content,
        config_kwargs=openai_kwargs,
    )


# ---------------------------------------------------------------------------
# Groq backend (dormant fallback)
# ---------------------------------------------------------------------------
_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    if not GROQ_API_KEY:
        raise LLMError("No API key — set GROQ_API_KEY in .env")
    from groq import Groq
    import httpx
    # Groq uses httpx internally. On this machine the AV does TLS interception and
    # re-signs traffic with a local CA that certifi doesn't know about. truststore
    # patches ssl.SSLContext globally but doesn't affect httpcore's start_tls path
    # (which is what httpx uses). Passing verify=False to the httpx client bypasses
    # cert chain validation — acceptable here because the interception is by the
    # user's own AV on a local dev machine.
    http_client = httpx.Client(verify=False)
    _groq_client = Groq(api_key=GROQ_API_KEY, http_client=http_client)
    return _groq_client


def _generate_groq(system_prompt: str, user_content: str, config_kwargs: dict[str, Any]):
    """Single Groq chat completion with throttle + retry/backoff."""
    return _chat_completion_with_retry(
        provider_label="Groq",
        client=_get_groq_client(),
        model=GROQ_MODEL,
        system_prompt=system_prompt,
        user_content=user_content,
        config_kwargs=config_kwargs,
    )


# ---------------------------------------------------------------------------
# Gemini backend (fallback)
# ---------------------------------------------------------------------------
_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    if not GEMINI_API_KEY:
        raise LLMError("No API key — set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")
    _inject_truststore()
    from google import genai
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _generate_gemini(system_prompt: str, user_content: str, config_kwargs: dict[str, Any]):
    """Single Gemini call with throttle + retry/backoff."""
    from google.genai import types

    client = _get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt, **config_kwargs
    )

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL, contents=user_content, config=config
            )
        except Exception as exc:
            last_exc = exc
            is_429 = _is_quota_429(exc)
            retry_delay = _parse_gemini_retry_delay(exc) if is_429 else None

            if is_429 and retry_delay is not None and retry_delay > _DAILY_RETRY_SECS:
                raise DailyQuotaError(
                    f"Gemini quota won't clear for {retry_delay:.0f}s — stopping."
                ) from exc

            retryable = is_429 or _is_transient_5xx(exc)
            if retryable and attempt < _MAX_RETRIES:
                if retry_delay is not None:
                    delay = min(retry_delay + 1.0, _MAX_BACKOFF_SECS)
                else:
                    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "Gemini %s (attempt %d/%d) — backing off %.1fs",
                    "429" if is_429 else "transient 5xx",
                    attempt + 1, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            raise
    raise LLMError(f"Gemini call failed after {_MAX_RETRIES} retries: {last_exc}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def complete_json(
    system_prompt: str,
    user_content: str,
    schema: Any,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Structured extraction — returns parsed JSON conforming to ``schema``.

    For OpenAI/Groq: the JSON schema is appended to the system prompt and JSON
    mode is enabled via response_format. For Gemini: response_schema constrains
    decoding. In all cases the caller validates the result with
    ``schema.model_validate()``.
    """
    if LLM_PROVIDER in ("openai", "groq"):
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        enhanced_system = (
            system_prompt
            + f"\n\nYou MUST return valid JSON that exactly matches this schema:\n{schema_json}"
        )
        config_kwargs = {
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if LLM_PROVIDER == "openai":
            provider_label = "OpenAI"
            resp = _generate_openai(enhanced_system, user_content, OPENAI_MODEL_SMALL, config_kwargs)
        else:
            provider_label = "Groq"
            resp = _generate_groq(enhanced_system, user_content, config_kwargs)
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError(f"Empty response from {provider_label} (no JSON text)")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"{provider_label} returned non-JSON: {exc}: {text[:200]!r}") from exc

    if LLM_PROVIDER == "gemini":
        resp = _generate_gemini(
            system_prompt,
            user_content,
            {
                "temperature": temperature,
                "response_mime_type": "application/json",
                "response_schema": schema,
            },
        )
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise LLMError("Empty response from Gemini (no JSON text)")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini returned non-JSON: {exc}: {text[:200]!r}") from exc

    raise LLMError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")


def complete_text(
    system_prompt: str,
    user_content: str,
    temperature: float = 0.7,
) -> str:
    """Prose generation (e.g. cover letters). Returns the model's text output."""
    if LLM_PROVIDER == "openai":
        resp = _generate_openai(
            system_prompt, user_content, OPENAI_MODEL_LETTER, {"temperature": temperature}
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("Empty response from OpenAI (no text)")
        return text

    if LLM_PROVIDER == "groq":
        resp = _generate_groq(system_prompt, user_content, {"temperature": temperature})
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("Empty response from Groq (no text)")
        return text

    if LLM_PROVIDER == "gemini":
        resp = _generate_gemini(system_prompt, user_content, {"temperature": temperature})
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise LLMError("Empty response from Gemini (no text)")
        return text

    raise LLMError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")
