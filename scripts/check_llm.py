"""Self-test the configured LLM key end to end.

    python scripts/check_llm.py

Loads the same env/config the app uses (via app.llm.client), then makes one
tiny complete_text call and one tiny complete_json call through the public
API — whichever provider/model LLM_PROVIDER selects. Costs ~nothing.

Provider-agnostic on purpose: it drives the same complete_json/complete_text
functions extract.py, match.py, and cover_letter.py call, so a PASS here means
the configured provider actually works end to end, not just that a key exists.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from pydantic import BaseModel

from app.llm import client  # noqa: E402  (loads .env + provider config)

_KEY_HINTS = {
    "openai": "OPENAI_API_KEY — create one at https://platform.openai.com/api-keys",
    "groq": "GROQ_API_KEY — create one at https://console.groq.com/keys",
    "gemini": "GEMINI_API_KEY (or GOOGLE_API_KEY) — create one at https://aistudio.google.com/apikey",
}


class _Ping(BaseModel):
    answer: str


def _diagnose(exc: Exception, provider: str) -> None:
    low = str(exc).lower()
    print()
    if "no api key" in low:
        print(f"Diagnosis: no key configured. Set {_KEY_HINTS[provider]}")
    elif "authentication" in low or "401" in low or "invalid_api_key" in low or "permission" in low:
        print(f"Diagnosis: key rejected. Check {_KEY_HINTS[provider]}")
    elif "429" in low or "resource_exhausted" in low or "quota" in low or "rate" in low:
        print("Diagnosis: rate-limited or out of quota. Check the provider's dashboard "
              "for billing/tier, or wait and retry.")
    elif "not found" in low or "404" in low or "does not exist" in low or "model" in low:
        print("Diagnosis: the configured model may not be available to this key/account. "
              "Check the *_MODEL env var against the provider's current model list.")
    else:
        print("Diagnosis: unexpected error — see the message above.")


def main() -> int:
    provider = client.LLM_PROVIDER
    print(f"Provider : {provider}")
    if provider not in _KEY_HINTS:
        print(f"FAIL: unknown LLM_PROVIDER={provider!r} (expected one of {sorted(_KEY_HINTS)})")
        return 1

    try:
        text = client.complete_text("You are a test.", "Reply with exactly: OK", temperature=0)
        print(f"complete_text : OK -> {text[:40]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"complete_text : FAIL — {str(exc)[:240]}")
        _diagnose(exc, provider)
        return 1

    try:
        data = client.complete_json(
            "You are a test. Reply as JSON only.",
            "Set the answer field to OK.",
            schema=_Ping,
            temperature=0,
        )
        print(f"complete_json : OK -> {data}")
    except Exception as exc:  # noqa: BLE001
        print(f"complete_json : FAIL — {str(exc)[:240]}")
        _diagnose(exc, provider)
        return 1

    print("\nPASS — the LLM layer is good to go. Run: python scripts/run_extraction.py --job-id 4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
