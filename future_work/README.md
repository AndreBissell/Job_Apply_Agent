# Future work

This folder holds tasks we decided to put off. Each one came up while building something
else and wasn't important enough at that stage to do straight away. Instead of losing the
idea, it's written down here with enough context to pick it up later.

Nothing in here is planned or scheduled. An item is a note that says "worth doing,
not now", plus why it was put off and what doing it would involve.

## How to use it

- **One file per task.** Name it after the task, in kebab-case, e.g.
  `voice-toggle-and-comparison.md`.
- **Each file says:** what the task is, when and why it was put off, what it depends on,
  and roughly how to do it. Write it so someone with no memory of the conversation can
  start on it.
- **Add it to the index below** with one line.
- **When you start an item,** move it into the real plan (e.g.
  `docs/cover-letter-loop-plan.md`) or CLAUDE.md's CURRENT TASK, then delete the file
  here and its index line. If you decide not to do it, delete it and note why in the
  relevant plan's decision log.

## Index

| Task | Put off | Waiting on |
|---|---|---|
| [Voice toggle + comparison](voice-toggle-and-comparison.md): make the writing-sample personalisation switchable, then test whether it makes letters sound more like you without making them worse | 2026-10-03 | The cover-letter agent being finished (Phase 6+) |
| [check_claims leaks](check-claims-leaks.md): the fact-checker passes invented attachments ("I have included a link to a video"), "daily" frequency claims and "apply my skills in X" for a listed-only skill | 2026-10-04 | The user's go-ahead to tune letters again |
| [Company research](research-company.md): a `research_company` tool using Gemini with Google Search grounding for employer facts when the ad has few; recruiter-posted ads are the risk | 2026-10-01 (out of Phase 9 2026-10-04) | Nothing technical; an experiment, worth it if `specific_detail` keeps failing on thin ads |
| [Letter framing + addressing](letter-framing-and-addressing.md): address a contact the ad names instead of "Dear Hiring Manager", and tune who the writer pictures it is writing to | 2026-10-01 (out of Phase 9 2026-10-04) | The user's go-ahead to tune letter wording again |
| [Learn from edits](learn-from-edits.md): turn the user's edits to generated letters into confirmed style notes the writer reads next time | 2026-10-04 (out of Phase 9) | Wording go-ahead, and enough edited letters to learn from |
| [Quick Apply `user` keywords](quick-apply-user-keywords.md): licence, portfolio-link and interview-availability wordings slip past the keyword filter to layer 5; a plain `skill_yes_no` strategy | 2026-10-05 | Nothing; a judgement call on the patterns |
| [Quick Apply draft follow-ups](quick-apply-draft-followups.md): re-measure the "note for the candidate" prompt fix (~$0.19), per-part 9b view vs 9c draft on compound skills, padding, a no-call answer for no-trace skills | 2026-10-05 | Spend go-ahead; wording go-ahead for item 3 |
| [Gemini prompt caching](gemini-prompt-caching.md): `cached_tokens` is always 0, so the implicit prefix cache the prompts were ordered for never saves anything | 2026-10-04 | Nothing; do before the trial ends (~2026-12-30) |
