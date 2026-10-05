# Letter framing and addressing

**Put off:** 2026-10-01 (plan Q7: "Dear Hiring Manager" for now, addressing and framing
tuned later); moved out of Phase 9 of `docs/cover-letter-loop-plan.md` into this file on
2026-10-04.
**Waiting on:** the user's go-ahead to work on letter wording again (put off 2026-10-04:
"don't iterate on letter wording now; polish later").

## What

Two related wording changes to the writer:

1. **Addressing.** Every letter opens "Dear Hiring Manager," (`app/llm/letter/tools/generate.py`,
   the writer rules; `app/llm/skills/cover_letter_style/SKILL.md`). When the ad names a contact
   ("Apply to Jane Smith", "for enquiries contact ..."), address them by name. For an
   agency-posted ad, consider "Dear Hiring Team" or the recruiter's name.
2. **Framing.** The writer is told it writes to "a hiring manager". Tune who it pictures and
   how: a small business owner vs a large company's HR screen vs a recruiter, using the ad's
   `tone` from `analyze_job`.

## Why it was put off

Wording work. The user decided on 2026-10-04 not to iterate on letter wording yet, after
writer v3. The letters are correct and safe without it.

## How (rough)

- `analyze_job` gains a `contact_name` field, taken from the ad verbatim and checked in code
  (the same rule as `employer_name` in `extract.py`: stored only if it appears in the ad).
  That changes `ANALYSIS_VERSION`, so cached checklists are rebuilt (one mid call per job).
- The writer opens with that name when present, otherwise "Dear Hiring Manager,".
  `check_claims` stage 1 should block a salutation name that isn't in the ad.
- Framing: one or two lines in the writer rules keyed off `tone` and whether the ad is
  agency-posted. Keep it small; the style skill is shared by prompt and lint.

## How to know it worked

- An eval run against the current writer (`letter_lab.py run`), graded by the blind Opus panel:
  no drop on any rubric item, and no invented salutation names (a code count).
- The user reads a handful of the named-contact letters.
- Record it in the plan's Decision log.
