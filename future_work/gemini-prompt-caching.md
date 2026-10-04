# Gemini prompt caching never hits

**Put off:** 2026-10-04, after the Phase 7c audit of `docs/cover-letter-loop-plan.md`.
**Waiting on:** nothing; a cost optimisation, not a bug. Worth doing before running past
the free trial (ends ~2026-12-30; Flash prices double 2027-01-01).

## What

Every Gemini call logs `cached_tokens` in `llm_usage` (from the response's
`cached_content_token_count`, `app/llm/client.py`). Across every call so far, in real.db
and the eval DB, it has been **0**. Plan §4 item 9 expected Gemini's *implicit* prefix
cache to hit on repeated calls in one run, and the prompts were ordered for it: the writer
and the reviser share an identical system prompt + context block (profile, ad, analysis,
letter plan) and only the final task differs (`tools/generate.py` `writer_context`,
`tools/revise.py`).

Cached input bills at 10% of the normal input price (`client.PRICES`). The writer's ~6-7k
input tokens per call are about 10% of a letter's cost (thinking tokens are most of it, and
caching doesn't touch those), so the saving is real but modest: roughly 1-2c a letter on
revisions, more if the agent's orchestrator turns (~2k input each, ~9 a letter) hit too.

## Why it was put off

Found in the cost reports (`evals/results/cost-*.md`). Letters work fine without it, and it
needs investigation rather than a known fix.

## Things to check (not verified, just the likely causes)

- **Does implicit caching apply here?** Check Google's current docs for implicit caching
  on Vertex AI: which models (is `gemini-3.1-pro-preview` or `3.8-flash` excluded as
  preview/new?), which locations (the app uses `GOOGLE_CLOUD_LOCATION=global`), and the
  minimum prefix length.
- **Is the prefix really identical?** Diff the exact request bodies of a generate and a
  revise call in one run. Anything that varies near the top (a timestamp, a re-ordered
  dict, a structured-output schema sent first) breaks the prefix.
- **Is the count read correctly?** Confirm `cached_content_token_count` is the right field
  for this SDK version, by logging one raw `usage_metadata`.
- **Explicit caching** as the alternative: create a cache for the per-run context block
  (profile + ad + analysis) once and reference it from the writer, reviser and checks. More
  code (a cache lifecycle and a TTL) for a predictable discount.

## How to know it worked

`python scripts/letter_lab.py cost-report <run>` shows input tokens per task; after a fix,
`llm_usage.cached_tokens` should be non-zero on revise calls and the per-letter cost should
drop. Record the before/after in the plan's Decision log.
