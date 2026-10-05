# Learn-from-edits style notes

**Put off:** moved out of Phase 9 of `docs/cover-letter-loop-plan.md` into this file on
2026-10-04 (it was listed there since v0.3, 2026-10-01).
**Waiting on:** (1) the user's go-ahead to work on letter wording again (put off 2026-10-04);
(2) enough edited letters to learn from. Only letters the user has actually changed count.

## What

When the user edits a generated letter before sending it, the difference between
`cover_letters.generated_content` and `edited_content` says what they didn't like: phrases
they always delete, an opening they always rewrite, a tone they soften. Turn those edits into
short **style notes** (e.g. "never say 'I am excited to'", "keep the close to one sentence")
that the writer reads on the next letter, so the same fix isn't needed twice.

Related but separate: `future_work/voice-toggle-and-comparison.md` is about the writing sample
as a voice reference. This is a second source of style signal, learned from edits rather than
pasted. Kept as its own file because it has its own trigger (edited letters exist) and its own
data. If both are built, the voice comparison should test them together.

## Why it was put off

Wording work, put off by the user on 2026-10-04. There are also few edited letters yet, so
there is little to learn from.

## How (rough)

- **Collect.** Pairs of `generated_content` / `edited_content` where status is `edited` or
  `final` and the text actually differs. `edited_content` is never written by the pipeline
  (Phase 8 design choice 5), so every edit is the user's own.
- **Summarise.** A periodic small- or mid-tier call (through `app/llm/client.py`, with a
  `task=` label) over the diffs that proposes a few notes. Code keeps only notes backed by
  edits in two or more letters, so one odd edit doesn't become a rule.
- **Confirm.** Show the proposed notes to the user in the profile editor; they accept,
  edit or reject each (the same pattern as the `ask_user` confirm step, Q11). Store accepted
  notes on the profile, e.g. in `profiles.preferences` or a new column (schema doc first).
- **Use.** The writer gets them next to the style skill. Banned phrases could also feed
  `banned_phrases.txt`-style checks in `style_lint`, so they are enforced, not just suggested.

## How to know it worked

- Fewer and smaller edits on later letters: measure the edit distance between generated and
  edited text over time (needs enough letters to mean anything).
- A before/after eval run graded by the blind Opus panel: no drop on any rubric item.
- Record it in the plan's Decision log.
