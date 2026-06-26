"""Self-test the configured LLM key end to end.

    python scripts/check_llm.py

Loads the same env/config the app uses (via app.llm.client), then:
  1. confirms a key is present,
  2. authenticates by listing models,
  3. does ONE tiny generateContent on the configured GEMINI_MODEL.

Prints a clear PASS/FAIL with the precise upstream error and a hint, so you can
verify a new key before running the real extraction. Makes at most one tiny
generation call, so it costs ~nothing against the free quota.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.llm import client  # noqa: E402  (loads .env + truststore config)


def main() -> int:
    key = client.GEMINI_API_KEY
    if not key:
        print("FAIL: no key — set GEMINI_API_KEY or GOOGLE_API_KEY in .env")
        return 1
    print(f"Provider : {client.LLM_PROVIDER}")
    print(f"Model    : {client.GEMINI_MODEL}")
    print(f"Key      : {key[:6]}… ({len(key)} chars)")

    try:
        client._get_client()  # applies truststore + builds the genai client
        gc = client._client
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not init client — {exc}")
        return 1

    # 1) Auth check — list models.
    try:
        models = list(gc.models.list())
        print(f"list()   : OK ({len(models)} models visible)")
    except Exception as exc:  # noqa: BLE001
        print(f"list()   : FAIL — {str(exc)[:200]}")
        print("\nThe key can't even authenticate. Create a fresh key at "
              "https://aistudio.google.com/apikey")
        return 1

    # 2) Generation check — one tiny call on the configured model.
    try:
        resp = gc.models.generate_content(
            model=client.GEMINI_MODEL, contents="Reply with the single word OK."
        )
        text = (getattr(resp, "text", "") or "").strip()
        print(f"generate : OK -> {text[:40]!r}")
        print("\nPASS — the LLM layer is good to go. Run: "
              "python scripts/run_extraction.py --job-id 4")
        return 0
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"generate : FAIL — {msg[:240]}")
        low = msg.lower()
        print()
        if "denied access" in low or "permission_denied" in low or "403" in low:
            print("Diagnosis: this project is DENIED generation access. Almost always an")
            print("account-eligibility issue, not the key. Try, in order:")
            print("  1. Use a PERSONAL @gmail.com account (not school/work/Workspace).")
            print("  2. Make sure that account is 18+ / age-verified.")
            print("  3. Create the key via 'Create API key in a NEW project'.")
        elif "resource_exhausted" in low or "429" in low or "quota" in low:
            print("Diagnosis: 429 quota. If this is a fresh project showing 0 quota, the")
            print("account likely has no free tier (same account fixes as above). If you")
            print("were just running many calls, wait for the per-minute window and retry.")
        elif "not found" in low or "404" in low:
            print(f"Diagnosis: model {client.GEMINI_MODEL!r} not available to this key.")
            print("Set GEMINI_MODEL in .env to one of the listed models and retry.")
        else:
            print("Diagnosis: unexpected error — see the message above.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
