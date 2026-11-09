# AI-screener metric ("communicated fit")

**Put off:** 2026-10-23, while defining the v2 letter metrics (docs/letter-metrics-plan.md,
Layer 4). The owner wants it done, but not as a core part of that task.

## What it is

Employers increasingly let software read applications first: it parses them, matches them
against the ad's requirements, then scores and ranks them (Workday HiredScore letter grades,
Oracle 0–5, seekAi shortlisting in some markets). This metric simulates that reader:

- A screener model reads **only the ad and the letter** (never the profile) and returns a 0–100
  fit score plus the requirements it saw evidenced, each with a quote from the letter.
- **Communicated fit** = screener score − the job's match score from the full profile. A large
  negative gap means the letter fails to say fit the candidate really has; a positive gap on a
  low-fit job suggests overselling (check it against M4 claim honesty).
- A by-product: the screener's list of requirements it saw evidenced vs the reference checklist,
  i.e. what a skim reader would miss.

## Why it stays secondary

LLM screeners prefer text written by their own model (67–82% self-preference; Xu, Li & Jiang
2025, arXiv 2509.00462). Optimising letters for an AI screener would push them towards AI-style
text, which human recruiters penalise. So:
- use **2 or more model families** and report each score plus their agreement (a majority vote
  across models cut self-preference by 60–71% in that study); also report *screener-visible
  coverage*, the requirements it saw evidenced against the reference checklist (evidence:
  docs/ai-recruitment-research.md B1–B2);
- at least one from a **different model family** from the Gemini writer (Claude/Opus as a subagent, or a model
  the writer doesn't use);
- report it next to the human-quality metrics, never as the headline or a target.

## How to do it

1. A prompt and JSON schema (score, evidenced requirements with quotes); quotes checked in code
   with `check_requirements.quote_in_letter`.
2. Run on a v2 run's letters; add a column to `letter_lab.py report`.
3. Sanity check: it should correlate with M1/M2 coverage; if it ranks letters very differently
   from the owner's send-readiness, that's a finding, not a bug.

Cost: about 1–2¢ per letter on an API model; nothing if run as Claude Code subagents.

**Depends on:** the v2 metrics being built and a graded baseline (eval-set stages 6–8).
