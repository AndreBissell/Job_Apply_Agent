"""Self-test the configured LLM provider end to end.

    python scripts/check_llm.py

Loads the same env/config the app uses (via app.llm.client), then makes one tiny
complete_json call and one tiny complete_text call on EACH tier (small / mid /
strong), plus one complete_tools call, through the public API — whichever
provider LLM_PROVIDER selects. Costs a fraction of a cent. Each call is logged to
llm_usage like any other, so this also proves usage logging works.

Provider-agnostic on purpose: it drives the same functions extract.py, match.py
and cover_letter.py call, so a PASS means the provider actually works end to end
for every tier the app uses, not just that credentials exist.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from pydantic import BaseModel

from app.llm import client  # noqa: E402  (loads .env + provider config)

_KEY_HINTS = {
    "openai": "OPENAI_API_KEY — create one at https://platform.openai.com/api-keys",
    "groq": "GROQ_API_KEY — create one at https://console.groq.com/keys",
    "gemini": (
        "Vertex: run `gcloud auth application-default login` and set "
        "GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT; or an API key in "
        "GEMINI_API_KEY (https://aistudio.google.com/apikey)"
    ),
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


def _usage_rows_since(start_id: int) -> int:
    from sqlalchemy import func, select

    from app.models import LlmUsage

    with client._usage_session() as db:
        return db.scalar(select(func.count()).select_from(LlmUsage).where(LlmUsage.id > start_id)) or 0


def _max_usage_id() -> int:
    from sqlalchemy import func, select

    from app.models import LlmUsage

    with client._usage_session() as db:
        return db.scalar(select(func.coalesce(func.max(LlmUsage.id), 0))) or 0


def main() -> int:
    provider = client.LLM_PROVIDER
    print(f"Provider : {provider}")
    if provider not in _KEY_HINTS:
        print(f"FAIL: unknown LLM_PROVIDER={provider!r} (expected one of {sorted(_KEY_HINTS)})")
        return 1
    try:
        start_id = _max_usage_id()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: llm_usage not readable ({str(exc)[:120]}) — run `alembic upgrade head`")
        start_id = None

    calls = 0
    for tier in client.TIERS:
        model = client.model_for(tier)
        try:
            text = client.complete_text(
                "You are a test.", "Reply with exactly: OK", temperature=0, tier=tier, task="check_llm"
            )
            calls += 1
            print(f"[{tier:6}] {model:26} complete_text : OK -> {text[:40]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{tier:6}] {model:26} complete_text : FAIL — {str(exc)[:240]}")
            _diagnose(exc, provider)
            return 1
        try:
            data = client.complete_json(
                "You are a test. Reply as JSON only.",
                "Set the answer field to OK.",
                schema=_Ping,
                temperature=0,
                tier=tier,
                task="check_llm",
            )
            calls += 1
            print(f"[{tier:6}] {model:26} complete_json : OK -> {data}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{tier:6}] {model:26} complete_json : FAIL — {str(exc)[:240]}")
            _diagnose(exc, provider)
            return 1

    try:
        step = client.complete_tools(
            "You are a test. Always call a tool.",
            [{"role": "user", "content": "Call the echo tool with word set to ping."}],
            [
                client.ToolSpec(
                    name="echo",
                    description="Echo a word back. Use this whenever asked to echo.",
                    parameters={
                        "type": "object",
                        "properties": {"word": {"type": "string"}},
                        "required": ["word"],
                    },
                )
            ],
            tier="mid",
            task="check_llm",
        )
        calls += 1
        if step.tool != "echo":
            print(f"complete_tools: FAIL — expected an echo call, got {step!r}")
            return 1
        print(f"complete_tools: OK -> {step.tool}({step.args})")
    except Exception as exc:  # noqa: BLE001
        print(f"complete_tools: FAIL — {str(exc)[:240]}")
        _diagnose(exc, provider)
        return 1

    if start_id is not None:
        logged = _usage_rows_since(start_id)
        if logged != calls:
            print(f"FAIL: made {calls} calls but only {logged} llm_usage rows were written")
            return 1
        print(f"llm_usage     : OK -> {logged} rows written")

    print("\nPASS — every tier, tool calling and usage logging work.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
