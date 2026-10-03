# Voice toggle + comparison

**Put off:** 2026-10-03, at the end of Phase 4 of `docs/cover-letter-loop-plan.md`.
**Waiting on:** the cover-letter agent being finished (Phases 5-6), so the comparison is
run on the final writer, not the one-shot stand-in.

## What

1. **Make the personalisation switchable.** The "personalisation" is the user's own
   writing (`profiles.writing_sample`, falling back to the profile's summary and
   experience text) passed to the writer as a voice reference. Today it's always on
   whenever the style guide is on. Add a switch so letters can be written with or
   without it.
2. **Compare the two.** Write letters for the same eval jobs with the voice on and off,
   then answer two questions:
   - **Does it personalise?** Do the voice-on letters sound more like the user and less
     like a generic AI letter?
   - **Does it hurt?** Do they cover fewer of the ad's requirements, add claims the
     profile doesn't support, run longer, or cost more?

## Why it was put off

Phase 4's early test, `oneshot-styled` (see `evals/results/styled-oneshot.md`), turned
on the style guide and the voice together, and the plan was for the user to hand-grade
those 9 letters. That grading was skipped: the one-shot writer will be replaced by the
agent anyway, so grading it now says little about the finished product. Comparing on
the finished agent is a better use of the grading effort.

## Where things are now

- `app/llm/letter/style.py`: `style_guide_prompt()` (the rules) and
  `voice_reference()` / `voice_prompt()` (the voice; trimmed to 1,500 words).
- `app/llm/cover_letter.py`: `_styled_system_prompt()` adds both, only when
  `styled=True`, and only from the eval harness. Production letters use neither.
- `scripts/letter_lab.py`: engines `oneshot` (neither) and `oneshot-styled` (both).
  There's no engine for "style guide without voice", so the two effects can't be
  separated yet.
- Run `styled-oneshot` (9 letters) exists, ungraded. Its `grades.csv` is empty.
- Known problem to look for: the styled letters copied stock lines from the writing
  sample word-for-word across unrelated letters ("the most relevant ... I can point
  to", "What I took from that project").

## How (rough)

- **The switch.** A preference such as `use_writing_sample` (default on) in
  `profiles.preferences`, and a checkbox next to the "Your writing" box in the profile
  editors. The writer adds `voice_prompt()` only when it's on. The style guide stays on
  either way, so the only difference between the two runs is the voice.
- **Eval engines.** Give `letter_lab.py` a voice-on and a voice-off variant of the
  final engine (e.g. `agent` and `agent-novoice`), run both on the same eval set, and
  build the side-by-side with `letter_lab.py read <run> --against <other-run>`.
- **What to measure:**
  - The existing rubric (`evals/rubric.md`): `supported_musts_covered`,
    `no_unsupported_claims`, `specific_detail`, `would_send`. If voice-on is worse on any
    of these, it hinders performance.
  - A new human-graded item, e.g. `sounds_like_me` (Y/N). The rubric has nothing that
    measures personalisation today.
  - A code check for copying: count 6+ word runs shared between the letter and
    `writing_sample`, and the same lines repeated across letters in one run. This
    catches the copying problem above without any hand-grading.
  - Cost, words, and `style_lint` results, already in the report.
- **Grading blind helps.** Shuffle the voice-on and voice-off letters and hide which is
  which before grading `sounds_like_me`.
- **Decide and log it.** Keep the voice on by default, make it opt-in, or drop it.
  Record the call in the plan's Decision log.

## Notes

- The eval set is small (9 ads, none scoring 75-84). Grow it before trusting a
  difference of one or two letters.
- The user said some of the writing sample is AI-assisted, which may weaken the "sounds
  like me" effect. Swapping in purely hand-written samples is a cheap variant to try.
