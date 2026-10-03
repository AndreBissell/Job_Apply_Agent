"""LLM provider abstraction — the ONE place the model/provider is configured.

Every LLM call in the app goes through ``complete_json`` (structured extraction),
``complete_text`` (prose) or ``complete_tools`` (tool calling). The provider,
models, credentials and rate limit all come from the environment, so swapping
providers or models is a config change, never an edit to extract.py / match.py /
cover-letter code. Callers name a *tier*, never a model.

Tiers (see docs/cover-letter-loop-plan.md §3):
  * ``small``  — cheap structured work: quick-screen, extract, match, checks.
  * ``mid``    — orchestration, job analysis, evidence mapping.
  * ``strong`` — the prose a human actually reads: cover letters, revisions.

Supported providers (set LLM_PROVIDER in .env):
  * "gemini" — Google Gemini via Vertex AI (ADC, no key) or an API key. Active
    default. GEMINI_MODEL_SMALL / _MID / _STRONG map the tiers.
  * "openai" — dormant fallback. small -> OPENAI_MODEL_SMALL; mid and strong ->
    OPENAI_MODEL_LETTER.
  * "groq"   — dormant fallback (JSON + text only, no tool calling).

Cross-cutting behaviour that lives here so callers don't reimplement it:
  * an in-process throttle keeps us under ``LLM_RPM`` requests/minute;
  * 429s and transient 5xx retry, preferring the server's retry-after hint; only
    a 429 whose delay exceeds ``_DAILY_RETRY_SECS`` (5 min) is a genuine daily
    exhaustion and raises ``DailyQuotaError``;
  * every call is logged to ``llm_usage`` (tokens + estimated USD);
  * ``mid`` / ``strong`` calls are refused with ``BudgetExceededError`` once the
    daily or total USD cap in ``profiles.preferences`` is reached. ``small``
    keeps running so scanning still works.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Config (read once at import; all from env, nothing hardcoded as truth)
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

Tier = Literal["small", "mid", "strong"]
TIERS: tuple[str, ...] = ("small", "mid", "strong")

# OpenAI (dormant fallback) — small, and one better model for mid + strong.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL_SMALL = os.environ.get("OPENAI_MODEL_SMALL", "gpt-5-nano")
OPENAI_MODEL_LETTER = os.environ.get("OPENAI_MODEL_LETTER", "gpt-5-mini")

# Groq (dormant fallback)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

# Gemini (active). GEMINI_MODEL is the pre-tier name for the small model; still
# honoured so an older .env keeps working.
GEMINI_MODEL_SMALL = (
    os.environ.get("GEMINI_MODEL_SMALL") or os.environ.get("GEMINI_MODEL") or "gemini-3.1-flash-lite"
)
GEMINI_MODEL_MID = os.environ.get("GEMINI_MODEL_MID", "gemini-3.8-flash")
GEMINI_MODEL_STRONG = os.environ.get("GEMINI_MODEL_STRONG", "gemini-3.1-pro-preview")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
# Vertex AI via Application Default Credentials — no API key. Needed when the
# Cloud organization disallows API keys (the $300 trial project does).
GEMINI_USE_VERTEX = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")

# Gemini 3 thinking level per tier — thinking tokens are billed as output, so this
# is the biggest cost lever. Override with GEMINI_THINKING_SMALL / _MID / _STRONG.
_THINKING_DEFAULTS = {"small": "low", "mid": "medium", "strong": "high"}

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


class BudgetExceededError(LLMError):
    """Raised before a mid/strong call when the daily or total USD cap is reached.

    Raised *before* any request is made, so nothing is spent. Callers treat it
    like ``DailyQuotaError`` (stop and back off); ``small`` calls are never
    blocked by it.
    """


# ---------------------------------------------------------------------------
# Tiers -> models
# ---------------------------------------------------------------------------
def _check_tier(tier: str) -> str:
    if tier not in TIERS:
        raise LLMError(f"Unknown tier {tier!r} (expected one of {TIERS})")
    return tier


def model_for(tier: str, provider: str | None = None) -> str:
    """The model a tier resolves to under ``provider`` (default: the active one)."""
    _check_tier(tier)
    provider = provider or LLM_PROVIDER
    if provider == "gemini":
        return {"small": GEMINI_MODEL_SMALL, "mid": GEMINI_MODEL_MID, "strong": GEMINI_MODEL_STRONG}[tier]
    if provider == "openai":
        return OPENAI_MODEL_SMALL if tier == "small" else OPENAI_MODEL_LETTER
    if provider == "groq":
        return GROQ_MODEL
    raise LLMError(f"Unknown LLM_PROVIDER={provider!r}")


def _thinking_level(tier: str) -> str:
    level = os.environ.get(f"GEMINI_THINKING_{tier.upper()}", _THINKING_DEFAULTS[tier]).lower()
    return level if level in ("minimal", "low", "medium", "high") else _THINKING_DEFAULTS[tier]


# ---------------------------------------------------------------------------
# Prices — USD per 1M tokens: (input, output, cached input). The ONE place.
# Gemini figures are the Gemini API list prices checked 2026-10-01 (prompts
# <=200k tokens; Pro's >200k tier is not modelled). Vertex AI bills close to but
# not exactly these, so treat the logged USD as an estimate and compare it to
# Cloud Billing. 3.6-3.8 Flash are intro prices that double on 2027-01-01 (the
# trial ends first); 3.1 and 3.5 stay flat. Thinking tokens bill as output.
# An unknown model logs cost 0 and a warning rather than failing the call.
# ---------------------------------------------------------------------------
PRICES: dict[str, tuple[float, float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.5-flash": (1.50, 9.00, 0.15),
    "gemini-3.6-flash": (0.75, 3.75, 0.075),
    "gemini-3.7-flash": (0.75, 3.75, 0.075),
    "gemini-3.8-flash": (0.75, 3.75, 0.075),
    "gemini-3.1-pro-preview": (2.00, 12.00, 0.20),
    # Dormant OpenAI fallback (approximate).
    "gpt-5-nano": (0.05, 0.40, 0.005),
    "gpt-5-mini": (0.25, 2.00, 0.025),
}


@dataclass
class Usage:
    """Token counts for one call. ``input_tokens`` includes ``cached_tokens``;
    ``output_tokens`` excludes ``thinking_tokens`` (both bill at the output rate)."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0


def estimate_cost_usd(model: str, usage: Usage) -> float:
    price = PRICES.get(model)
    if price is None:
        logger.warning("No price for model %r — logging cost as 0", model)
        return 0.0
    p_in, p_out, p_cached = price
    fresh_in = max(0, usage.input_tokens - usage.cached_tokens)
    total = (
        fresh_in * p_in
        + usage.cached_tokens * p_cached
        + (usage.output_tokens + usage.thinking_tokens) * p_out
    )
    return total / 1_000_000


def _usage_from_gemini(resp: Any) -> Usage:
    m = getattr(resp, "usage_metadata", None)
    if m is None:
        return Usage()
    g = lambda name: int(getattr(m, name, None) or 0)  # noqa: E731
    return Usage(
        input_tokens=g("prompt_token_count"),
        output_tokens=g("candidates_token_count"),
        thinking_tokens=g("thoughts_token_count"),
        cached_tokens=g("cached_content_token_count"),
    )


def _usage_from_openai(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    completion = int(getattr(u, "completion_tokens", 0) or 0)
    reasoning = int(getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
    cached = int(getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
    return Usage(
        input_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
        output_tokens=max(0, completion - reasoning),
        thinking_tokens=reasoning,
        cached_tokens=cached,
    )


def _usage_session():
    """A DB session for usage logging / the budget guard (patched in tests)."""
    from app.db import SessionLocal

    return SessionLocal()


def _record_usage(
    *,
    task: str,
    tier: str,
    model: str,
    usage: Usage,
    duration_ms: int,
    job_id: int | None,
    match_id: int | None,
    run_id: int | None,
) -> None:
    """Write one ``llm_usage`` row. Never raises — logging must not fail a call
    that already succeeded (and already cost money)."""
    try:
        from app.models import LlmUsage

        with _usage_session() as db:
            db.add(
                LlmUsage(
                    task=task,
                    tier=tier,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thinking_tokens=usage.thinking_tokens,
                    cached_tokens=usage.cached_tokens,
                    cost_usd=estimate_cost_usd(model, usage),
                    duration_ms=duration_ms,
                    job_id=job_id,
                    match_id=match_id,
                    run_id=run_id,
                )
            )
            db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Could not record llm_usage for task %r", task, exc_info=True)


def _check_budget(tier: str) -> None:
    """Refuse a mid/strong call once a USD cap is hit. ``small`` is never blocked.

    Fails *open* if the usage table can't be read (e.g. migration not applied):
    the RPM throttle still bounds spend, and a broken guard shouldn't take the
    whole pipeline down.
    """
    if tier == "small":
        return
    try:
        from app.llm.usage import budget_status

        with _usage_session() as db:
            status = budget_status(db)
    except Exception:  # noqa: BLE001
        logger.warning("Budget check unavailable — allowing the call", exc_info=True)
        return
    if status["blocked"]:
        raise BudgetExceededError(status["reason"])


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


# A dropped connection carries no status code (httpx RemoteProtocolError /
# ConnectError / ReadError). Seen live 2026-10-03: "Server disconnected without
# sending a response" mid-eval, which crashed the run instead of retrying.
_TRANSIENT_NETWORK_ERRORS = ("RemoteProtocolError", "ConnectError", "ReadError", "ReadTimeout",
                             "ConnectTimeout", "ConnectionResetError", "ConnectionAbortedError")
_TRANSIENT_NETWORK_TEXT = ("server disconnected", "connection reset", "connection aborted",
                           "remote end closed connection")


def _is_transient_5xx(exc: Exception) -> bool:
    """A 5xx, or a dropped connection: worth retrying with backoff."""
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    low = (str(getattr(exc, "message", "") or "") + " " + str(exc)).lower()
    if code in (500, 502, 503, 504):
        return True
    if type(exc).__name__ in _TRANSIENT_NETWORK_ERRORS or any(s in low for s in _TRANSIENT_NETWORK_TEXT):
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
# Shared retry/backoff wrapper for OpenAI-compatible APIs (OpenAI's own SDK and
# Groq's OpenAI-compatible SDK share this error/retry-after shape).
# ---------------------------------------------------------------------------
def _with_retry(provider_label: str, call_fn):
    """Run ``call_fn()`` with throttle + retry/backoff. ``call_fn`` takes no args."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        try:
            return call_fn()
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

    return _with_retry(provider_label, lambda: client.chat.completions.create(**call_kwargs))


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


def _generate_openai_structured(system_prompt: str, user_content: str, model: str, schema: Any):
    """Structured extraction via OpenAI's native Structured Outputs (strict schema).

    Uses ``chat.completions.parse`` with the Pydantic model as ``response_format``
    instead of describing the schema in the prompt + plain JSON mode: on
    gpt-5-nano, the text-described-schema approach was observed returning the
    JSON *schema itself* (its ``$defs``/``properties`` wrapper) instead of an
    instance of it — a live failure on job extraction, not a hypothetical one.
    Structured Outputs constrains decoding at the API level so that can't happen.
    """
    client = _get_openai_client()
    return _with_retry(
        "OpenAI",
        lambda: client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format=schema,
        ),
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
# Gemini backend (active)
# ---------------------------------------------------------------------------
_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    _inject_truststore()
    from google import genai
    if GEMINI_USE_VERTEX:
        if not GOOGLE_CLOUD_PROJECT:
            raise LLMError("Vertex mode needs GOOGLE_CLOUD_PROJECT in .env")
        # Credentials come from ADC (`gcloud auth application-default login`).
        _gemini_client = genai.Client(
            vertexai=True, project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION
        )
        return _gemini_client
    if not GEMINI_API_KEY:
        raise LLMError("No API key — set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env")
    _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _gemini_config(
    system_prompt: str, model: str, tier: str, temperature: float | None, **extra: Any
):
    """Build the request config for ``model``.

    Gemini 3 guidance is to leave temperature at its default of 1.0 (lowering it
    can cause looping on complex tasks), so it is dropped there and the thinking
    level — set per tier — is the quality/cost dial instead. Older models keep
    the caller's temperature and have no thinking level.
    """
    from google.genai import types

    kwargs: dict[str, Any] = dict(extra)
    if model.startswith("gemini-3"):
        kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=types.ThinkingLevel(_thinking_level(tier).upper())
        )
    elif temperature is not None:
        kwargs["temperature"] = temperature
    return types.GenerateContentConfig(system_instruction=system_prompt, **kwargs)


def _generate_gemini(model: str, contents: Any, config: Any):
    """Single Gemini call with throttle + retry/backoff."""
    client = _get_gemini_client()

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        _throttle()
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
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
@dataclass(frozen=True)
class ToolSpec:
    """A provider-neutral tool definition. ``parameters`` is a JSON-schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolStep:
    """What the model did on one ``complete_tools`` turn: a tool call, or text."""

    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None


def _logged_call(
    *,
    tier: str,
    task: str,
    job_id: int | None,
    match_id: int | None,
    run_id: int | None,
    call,
    usage_of,
):
    """Budget-check, run ``call()``, then log its usage. Returns the raw response."""
    _check_tier(tier)
    _check_budget(tier)
    start = time.monotonic()
    resp = call()
    _record_usage(
        task=task,
        tier=tier,
        model=model_for(tier),
        usage=usage_of(resp),
        duration_ms=int((time.monotonic() - start) * 1000),
        job_id=job_id,
        match_id=match_id,
        run_id=run_id,
    )
    return resp


def complete_json(
    system_prompt: str,
    user_content: str,
    schema: Any,
    temperature: float = 0.1,
    *,
    tier: Tier = "small",
    task: str = "complete_json",
    job_id: int | None = None,
    match_id: int | None = None,
    run_id: int | None = None,
) -> dict[str, Any]:
    """Structured extraction — returns parsed JSON conforming to ``schema``.

    For Gemini: ``response_schema`` constrains decoding. For OpenAI: native
    Structured Outputs (chat.completions.parse) — see
    ``_generate_openai_structured`` for why. For Groq: the JSON schema is
    appended to the system prompt and JSON mode is enabled. The caller validates
    the result with ``schema.model_validate()`` regardless of provider.

    ``task`` / ``job_id`` / ``match_id`` / ``run_id`` only label the ``llm_usage`` row.
    """
    model = model_for(tier)
    ids = dict(tier=tier, task=task, job_id=job_id, match_id=match_id, run_id=run_id)

    if LLM_PROVIDER == "gemini":
        config = _gemini_config(
            system_prompt, model, tier, temperature,
            response_mime_type="application/json", response_schema=schema,
        )
        resp = _logged_call(
            **ids, call=lambda: _generate_gemini(model, user_content, config),
            usage_of=_usage_from_gemini,
        )
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise LLMError("Empty response from Gemini (no JSON text)")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Gemini returned non-JSON: {exc}: {text[:200]!r}") from exc

    if LLM_PROVIDER == "openai":
        resp = _logged_call(
            **ids, call=lambda: _generate_openai_structured(system_prompt, user_content, model, schema),
            usage_of=_usage_from_openai,
        )
        message = resp.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMError(f"OpenAI refused: {message.refusal}")
        if message.parsed is None:
            raise LLMError("Empty response from OpenAI (no parsed structured output)")
        return message.parsed.model_dump(mode="json")

    if LLM_PROVIDER == "groq":
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        enhanced_system = (
            system_prompt
            + f"\n\nYou MUST return valid JSON that exactly matches this schema:\n{schema_json}"
        )
        config_kwargs = {
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        resp = _logged_call(
            **ids, call=lambda: _generate_groq(enhanced_system, user_content, config_kwargs),
            usage_of=_usage_from_openai,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("Empty response from Groq (no JSON text)")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Groq returned non-JSON: {exc}: {text[:200]!r}") from exc

    raise LLMError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")


def complete_text(
    system_prompt: str,
    user_content: str,
    temperature: float = 0.7,
    *,
    tier: Tier = "strong",
    task: str = "complete_text",
    job_id: int | None = None,
    match_id: int | None = None,
    run_id: int | None = None,
) -> str:
    """Prose generation (e.g. cover letters). Returns the model's text output."""
    model = model_for(tier)
    ids = dict(tier=tier, task=task, job_id=job_id, match_id=match_id, run_id=run_id)

    if LLM_PROVIDER == "gemini":
        config = _gemini_config(system_prompt, model, tier, temperature)
        resp = _logged_call(
            **ids, call=lambda: _generate_gemini(model, user_content, config),
            usage_of=_usage_from_gemini,
        )
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise LLMError("Empty response from Gemini (no text)")
        return text

    if LLM_PROVIDER == "openai":
        resp = _logged_call(
            **ids,
            call=lambda: _generate_openai(system_prompt, user_content, model, {"temperature": temperature}),
            usage_of=_usage_from_openai,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("Empty response from OpenAI (no text)")
        return text

    if LLM_PROVIDER == "groq":
        resp = _logged_call(
            **ids,
            call=lambda: _generate_groq(system_prompt, user_content, {"temperature": temperature}),
            usage_of=_usage_from_openai,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("Empty response from Groq (no text)")
        return text

    raise LLMError(f"Unknown LLM_PROVIDER={LLM_PROVIDER!r}")


def complete_tools(
    system_prompt: str,
    messages: list[dict[str, str]],
    tools: list[ToolSpec],
    *,
    tier: Tier = "mid",
    require_tool: bool = True,
    task: str = "complete_tools",
    job_id: int | None = None,
    match_id: int | None = None,
    run_id: int | None = None,
) -> ToolStep:
    """One tool-calling turn: returns the tool the model chose (name + JSON args),
    or its text if it answered instead.

    ``messages`` is a list of ``{"role": "user" | "assistant", "content": str}``
    text turns. The agent loop is stateless by design (the orchestrator is handed
    a fresh state *summary* each step, plan §5.5), so tool results travel inside
    that summary rather than as provider-specific function-response turns — which
    also means no thought-signature replay is needed. ``require_tool`` forces a
    tool call (Gemini mode ANY / OpenAI tool_choice "required").
    """
    if not tools:
        raise LLMError("complete_tools needs at least one tool")
    if not messages:
        raise LLMError("complete_tools needs at least one message")
    model = model_for(tier)
    ids = dict(tier=tier, task=task, job_id=job_id, match_id=match_id, run_id=run_id)

    if LLM_PROVIDER == "gemini":
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.parameters
            )
            for t in tools
        ]
        mode = (
            types.FunctionCallingConfigMode.ANY
            if require_tool
            else types.FunctionCallingConfigMode.AUTO
        )
        config = _gemini_config(
            system_prompt, model, tier, None,
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=mode)
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        resp = _logged_call(
            **ids, call=lambda: _generate_gemini(model, contents, config),
            usage_of=_usage_from_gemini,
        )
        calls = getattr(resp, "function_calls", None) or []
        if calls:
            return ToolStep(tool=calls[0].name, args=dict(calls[0].args or {}))
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise LLMError("Empty response from Gemini (no tool call, no text)")
        return ToolStep(text=text)

    if LLM_PROVIDER == "openai":
        oa_tools = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }
            for t in tools
        ]
        oa_messages = [{"role": "system", "content": system_prompt}, *messages]
        oa_client = _get_openai_client()
        resp = _logged_call(
            **ids,
            call=lambda: _with_retry(
                "OpenAI",
                lambda: oa_client.chat.completions.create(
                    model=model,
                    messages=oa_messages,
                    tools=oa_tools,
                    tool_choice="required" if require_tool else "auto",
                ),
            ),
            usage_of=_usage_from_openai,
        )
        message = resp.choices[0].message
        if getattr(message, "tool_calls", None):
            fn = message.tool_calls[0].function
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise LLMError(f"OpenAI returned non-JSON tool args: {exc}") from exc
            return ToolStep(tool=fn.name, args=args)
        text = (message.content or "").strip()
        if not text:
            raise LLMError("Empty response from OpenAI (no tool call, no text)")
        return ToolStep(text=text)

    raise LLMError(f"complete_tools is not supported for LLM_PROVIDER={LLM_PROVIDER!r}")
