# Rewrite letter + letter date (branch `eval-set-creation`)

**Goal:** after a profile change, rewrite an existing letter in one go from where the user
applies (the Overview's Keep Applying card and the Quick Apply panel on the ad), and record
in the database when each letter was last written.

Approved by the owner 2026-10-20 with every recommendation below (D1–D3).

## What already existed

- `POST /jobs/{id}/regenerate`: re-extracts and re-scores the job (`force=True`), then writes
  a letter with `production.generate_for` (the full pipeline while the letter loop is on).
  Nothing called it except a failed run's "Try again now".
- `cover_letters` had no "written at": `created_at` is the first write only, and `updated_at`
  also moves when the user saves edits.

## Steps

1. **`cover_letters.generated_at`** (migration). Backfill: the job's latest letter-writing call
   in `llm_usage` (`cover_letter` / `generate_letter` / `revise_letter`), else `created_at`.
2. **Set it on both write paths:** `production.land_letter` (pipeline) and `cover_letter.py`
   (one-shot). Saving edits never touches it.
3. **API:** `GET /jobs` rows gain `letter_written_at` and `letter_outdated`; `GET /jobs/{id}`'s
   `cover_letter` gains `generated_at` and `outdated`. Outdated = written before the profile's
   content last changed (`retention.profile_revised_at`).
4. **Rewrite = `/regenerate`**, with:
   - the job marked queued (`letter_bands`), so cards show "✍ Writing…" at once; a second
     click while it is queued does nothing (`{"status": "writing"}`);
   - applied jobs refused (409): their letter is part of the Centrelink record;
   - `discard_edits=true` (D1) clears the user's edits, but only once the new letter has landed,
     so a failed rewrite loses nothing.
5. **Keep Applying card:** "⟳ Rewrite", two clicks (the first shows the cost; with edits it says
   they will be replaced), then Writing… A "Written 14 Oct" line, amber "before profile
   update" when outdated.
6. **Quick Apply panel on the ad:** the same two-click Rewrite and written date; while writing it
   checks every 15 s and puts the new letter in the box when it lands. No button on applied jobs.
7. **Tests:** pytest for steps 1–4; e2e for the card and the panel (Seek and
   `chrome.tabs.create` stubbed).
8. **Docs:** schema doc (`generated_at`), CLAUDE.md line.

## Decisions (owner, 2026-10-20)

- **D1 Edits:** an explicit Rewrite replaces the user's edits (warned on the confirm click). Only
  this button clears them; every other regenerate still keeps edits.
- **D2 Writer:** the same as `/regenerate` (the full pipeline while the loop is on, ≈25¢, 3–4 min).
- **D3 Re-score:** kept (`force=True` re-extracts and re-scores against the current profile, <1¢).

Not in scope: a "rewrite all outdated" bulk button.

## Decision log

- 2026-10-20: plan approved (D1–D3 as recommended).
- 2026-10-20: built. Choices made on the way:
  - The cost on the confirm click comes from `llm_usage` (`letter_bands.rewrite_cost_estimate`:
    the mean cost per pipeline run, by `run_id`, over the latest 20; ≈23¢ on REAL), not a
    constant; with the letter loop off it is the one-pass estimate. Default 25¢ before 3 runs.
  - The card's written line and Rewrite sit under the company, not in the action row: a 200px
    card has no room for a third button there.
  - `letter_edited` / `edited` are true only when the edits differ from the generated text, so
    the confirm click only warns when something would really be lost.
  - The stub LLM (test-only) now answers `complete_text` from the same canned file, so the e2e
    lands a real rewrite on the worker without a provider.
  - Tests: pytest 1813 (new `tests/test_letter_rewrite.py`); e2e new
    `tests/e2e/letter_rewrite_e2e.py` 31/31; apply_sent 51, dashboard 199, sidebar_load 67,
    pipeline 86, quick_apply 124.

## Manual checks (in your own Chrome, REAL backend)

Start `python scripts/run_api.py real` first: it runs the migration on real.db (backup from
before this work: the scratchpad's `real.db.bak-2026-10-20`).

- [ ] Overview → Keep Applying: each card says "Written <date>"; letters from before 20 Oct say
      "before profile update" in amber.
- [ ] Rewrite on a card: the first click shows "Rewrite? ≈ 23¢" (or "Replaces your edits · ..."
      on one you edited); left alone it goes back; the second click turns the card "✍ Writing…".
- [ ] A few minutes later the card is back as "Written <today>", and ⧉ Letter copies the new text.
- [ ] Open ad on a card: the panel on the ad shows the date and ⟳ Rewrite; a rewrite there puts
      the new letter in the box when it lands.
- [ ] An applied job's ad: the panel has no Rewrite.
