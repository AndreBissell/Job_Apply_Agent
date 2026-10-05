# check_claims leaks: invented attachments and frequency claims

**Put off:** 2026-10-04, after the Phase 7c audit of `docs/cover-letter-loop-plan.md`.
**Waiting on:** nothing technical. Put off by the user's 2026-10-04 decision not to tune
letter wording yet. This is a checker fix, not wording, but it changes what letters pass,
so it should be measured with an eval run.

## What

`check_claims` (`app/llm/letter/tools/check_claims.py`) is the fact-checker every draft must
pass. Blind Opus grading panels found claims it lets through:

1. **Invented attachments.** "I have included a link to a two-minute video" (panel opus-v4,
   the Maxum ad that asks for a video). The letter says something is attached or included
   that the user never provided. The code-only stage 1 already blocks invented *links and
   email addresses* (`invented_links`, added 2026-10-03), but not a sentence that only
   *claims* an attachment exists.
2. **Frequency claims.** "I use AI tools as part of my daily routine / daily workflow"
   (panel opus-v3, 2 letters) when the profile says the user used them, not how often.
   The model judge treats "daily" as harmless wording.
3. **"Apply my skills in X"** for a skill the profile only lists (no experience entry
   describing its use). A listing backs "skills in X", never applied experience.
4. Also seen once each (opus-v4): an invented testing activity/outcome, and a degree
   discipline the profile doesn't have.

## Why it was put off

Found while grading the writer v3 letters. The user decided on 2026-10-04 to stop iterating
on letters and build the pipeline (7c, Phase 8) first. All of these show up in roughly 1-3
of 15 eval letters.

## Where things are now

- Stage 1 (code, no LLM): `_stage1()` checks each declared claim's pointer resolves and
  blocks links/emails not in the profile.
- Stage 2 (mid-tier judge): `_SYSTEM_PROMPT` lists what counts as `overstated`. Frequency
  and attachments are not named there; listed-skill experience wording is, but "apply my
  skills in" slipped through.
- The writer's rules (`generate.py` `_WRITER_RULES`) already forbid all three, so these are
  cases where the writer broke a rule and the checker missed it.

## How (rough)

- **Attachments, in code (stage 1):** a pattern for "I have (included|attached|enclosed)
  ...", "please find attached", "see the attached", "link to my (video|portfolio)". Block it
  unless the profile text contains the thing. Cheap and deterministic, like `invented_links`.
- **Frequency, in the judge prompt:** name it under `overstated`: "a frequency, scale or
  routine (daily, regularly, every week) the source does not state". Possibly also a code
  warning for "daily|every day|routinely" next to a candidate claim.
- **"Apply my skills in X":** add the phrase to the listed-skill experience wording the
  judge already checks, and to the stage-1 sentence patterns when X maps to a `skill:`-only
  requirement (`guardrails.listing_only`).
- **Measure it:** re-run `letter_lab.py plant` with new plant types (an attachment claim, a
  "daily" frequency, "apply my skills in <listed skill>") and check the catch rate goes up
  without false alarms on clean letters. Then one `letter_lab.py run` to see revisions don't
  rise much (each extra failed check costs a strong-model revision, ~4c).
- Log the result in the plan's Decision log.
