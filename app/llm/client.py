"""LLM provider abstraction — the ONE place the model/provider is configured.

Every LLM call in the app goes through ``complete_json`` (structured extraction)
or ``complete_text`` (prose). The provider, model, key, and rate limit all come
from the environment (see CLAUDE.md "LLM Layer"), so swapping providers or models
is a config change, never an edit to extract.py / match.py / cover-letter code.

Current provider: Google Gemini (Flash-Lite) via the unified ``google-genai`` SDK.

Rate limiting + retries live here so callers don't reimplement them:
  * a simple in-process throttle keeps us under ``LLM_RPM`` requests/minute;
  * 429s and transient 5xx retry, preferring the server's ``retryDelay`` hint over
    fixed backoff — Gemini's free-tier "limit: 20" 429s are a PER-MINUTE limit that
    clears within ~60s (proven 2026-06-23), NOT a daily cap, even though the error's
    quotaId reads ``...PerDay...`` (see CLAUDE.md "LLM Layer");
  * only a 429 whose ``retryDelay`` says it won't clear soon (> ``_DAILY_RETRY_SECS``)
    is treated as a real daily exhaustion and raises ``DailyQuotaError`` to stop a
    batch cleanly. We classify on the server's own delay, NOT on the misleading
    "PerDay" quotaId substring (which fooled the old check into stopping on a
    recoverable per-minute 429).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Ensure .env is loaded even if this module is imported before app.db (which also
# calls load_dotenv). Idempotent — a no-op when the vars are already set.
load_dotenv()

# --- Config (read once at import; all from env, nothing hardcoded as truth) ---
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
# Accept either name: CLAUDE.md standardises on GEMINI_API_KEY, but Google AI
# Studio / older setups use GOOGLE_API_KEY. Prefer the explicit one.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

try:
    # Default 4 RPM (~15s spacing). The free-tier per-minute allowance is tight and
    # dips under peak load — a probe tripped on two calls only ~7s apart — so keep
    # bursts slow. The retryDelay-aware backoff below recovers from any residual trip.
    LLM_RPM = max(1, int(os.environ.get("LLM_RPM", "4")))
except ValueError:
    LLM_RPM = 4

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 4, 8)
# A quota 429 whose server-provided retryDelay exceeds this is treated as a genuine
# daily exhaustion (won't clear with a short wait) -> DailyQuotaError. Per-minute
# limits carry retryDelay ~50s, well under this, so they back off and retry instead.
_DAILY_RETRY_SECS = 300.0
# Cap any single backoff so one call can't block much longer than a minute.
_MAX_BACKOFF_SECS = 70.0
# Pull the seconds out of a Gemini 429's RetryInfo, e.g. "retryDelay: '50.6s'".
_RETRY_DELAY_RE = re.compile(r"retry[_-]?delay['\"\s:]+['\"]?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class LLMError(RuntimeError):
    """Base class for LLM-layer failures."""


class DailyQuotaError(LLMError):
    """Raised on a daily-quota 429 that a short retry won't clear — stop the batch."""


# ---------------------------------------------------------------------------
# In-process rate limiter: ensures >= (60 / LLM_RPM) seconds between calls.
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
# Gemini client (lazy singleton so import never fails when the key is absent)
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if LLM_PROVIDER != "gemini":
        raise LLMError(f"Unsupported LLM_PROVIDER={LLM_PROVIDER!r} (only 'gemini' is wired)")
    if not GEMINI_API_KEY:
        raise LLMError(
            "No API key — set GEMINI_API_KEY (or GOOGLE_API_KEY) in .env"
        )
    # Use the OS trust store (Windows cert store) for TLS. On this machine the AV
    # does TLS interception, so requests are re-signed by a local CA that lives in
    # the Windows store but NOT in certifi's bundle — without this, the HTTPS call
    # to Gemini fails CERTIFICATE_VERIFY_FAILED. No-op where truststore is absent.
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 — best-effort; fall back to default certs
        logger.debug("truststore not available; using default TLS trust store")
    from google import genai  # imported lazily; only needed for real calls

    _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _is_quota_429(exc: Exception) -> bool:
    """True if ``exc`` is a 429 / RESOURCE_EXHAUSTED rate-limit error.

    Note: we deliberately do NOT classify daily-vs-per-minute from the quotaId text.
    Gemini's per-minute 429s carry a ``...PerDay...`` quotaId, so that substring is a
    false signal — see ``_parse_retry_delay`` + ``_generate`` for how we tell them
    apart (by the server's own ``retryDelay``).
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    low = (str(getattr(exc, "message", "") or "") + " " + str(exc)).lower()
    return code == 429 or "429" in low or "resource_exhausted" in low or "resourceexhausted" in low


def _parse_retry_delay(exc: Exception) -> float | None:
    """Seconds from the 429's RetryInfo (``retryDelay``), or None if not present."""
    m = _RETRY_DELAY_RE.search(str(getattr(exc, "message", "") or "") + " " + str(exc))
    return float(m.group(1)) if m else None


def _is_transient_5xx(exc: Exception) -> bool:
    """True for transient server-side errors (503 high-demand, 500, etc.).

    Gemini's free tier returns 503 UNAVAILABLE under load — a short retry usually
    clears it, so treat it like a 429 for backoff (but never as a daily quota stop).
    """
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    low = (str(getattr(exc, "message", "") or "") + " " + str(exc)).lower()
    if code in (500, 502, 503, 504):
        return True
    return any(s in low for s in ("unavailable", "overloaded", "internal error", "503", "500"))


def _generate(system_prompt: str, user_content: str, config_kwargs: dict[str, Any]):
    """Single Gemini call with throttle + retry/backoff. Returns the raw response."""
    from google.genai import types

    client = _get_client()
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
        except Exception as exc:  # noqa: BLE001 — classify, then re-raise or retry
            last_exc = exc
            is_429 = _is_quota_429(exc)
            retry_delay = _parse_retry_delay(exc) if is_429 else None

            # Genuine daily exhaustion: the server says it won't clear for a long time.
            # Per-minute limits report retryDelay ~50s, so they fall through to retry.
            if is_429 and retry_delay is not None and retry_delay > _DAILY_RETRY_SECS:
                raise DailyQuotaError(
                    f"Gemini quota won't clear for {retry_delay:.0f}s ({exc}) — stopping."
                ) from exc

            retryable = is_429 or _is_transient_5xx(exc)
            if retryable and attempt < _MAX_RETRIES:
                # Honour the server's retryDelay (capped) when given; else exp backoff.
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
    # Exhausted retries on repeated transient failures.
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
    """Structured extraction: returns parsed JSON conforming to ``schema``.

    ``schema`` is passed as Gemini's ``response_schema`` (a Pydantic model class
    or a Schema dict) with ``response_mime_type='application/json'`` so the model
    is constrained to valid JSON. Returns the parsed dict; raises ``LLMError`` if
    the response can't be parsed as JSON.
    """
    import json

    resp = _generate(
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


def complete_text(
    system_prompt: str,
    user_content: str,
    temperature: float = 0.7,
) -> str:
    """Prose generation (e.g. cover letters). Returns the model's text output."""
    resp = _generate(system_prompt, user_content, {"temperature": temperature})
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise LLMError("Empty response from Gemini (no text)")
    return text
