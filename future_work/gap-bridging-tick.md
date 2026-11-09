# Gap-bridging tick: split `missed_important` in two

Put off 2026-10-29 (owner, during the stage-7 recheck of the v2 eval set).

## The idea

`missed_important` today means one thing: an essential or important requirement the profile backs
(supported or partial row, or a `must_cover` row) that the letter doesn't address. A gap is never a
miss, and leaving a gap out is fine (grading-standard-v2 §4.1).

The owner sees a second, separate failure: an essential or important requirement the candidate
doesn't have, where the letter could still say something honest about it (related software or
systems, or wanting to grow into it) and says nothing.

## What already exists

- The reference checklist (answer key) gives each gap row `related_evidence`: related experience
  that does not back the row.
- The v2 judge already records, per gap row, `none` / `related` / `claimed`. A new tick could map to
  "an essential or important gap row with related evidence, recorded `none`".

## Open questions (owner decisions)

1. Which gaps count: suggested, essential or important gap rows with `related_evidence`, ticked only
   when the letter bridges none of them (one good bridge is enough; a letter can't bridge ten).
2. Years / seniority asks ("3+ years experience"): in or out? Suggested out (leave-out is fine).
3. Does a bare "I want to learn X" count as a bridge? Suggested only when paired with something real;
   the standard's ban on gap-led sentences still holds.

## Cost

No LLM spend: a new entry in `grading_panel.TICKS`, the grading sheet, the standard's tick table and
§4.1, letter-metrics-plan §4. The owner rechecks every graded letter on the new tick. Expect the
current writer to fail it often: the eval runs use the `leave_out_gaps` policy.

## Waiting on

The v2 baseline (eval-set plan stage 9). Changing the standard after stage 8 means recalibrating the
judge, so do it with a deliberate re-freeze.
