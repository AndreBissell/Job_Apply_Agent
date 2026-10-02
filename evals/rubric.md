# Cover-letter eval rubric

Pass/fail per item, not a 1–10 score: models grading their own output are
lenient on vague scales, and so are tired humans. A letter **passes** only if
every item passes. (docs/cover-letter-loop-plan.md §9)

## Checked by code (`app/llm/letter/rubric.py`)

| Item | Passes when |
|---|---|
| `em_dash_limit` | At most **1** `—` |
| `generic_phrase_limit` | At most **1** occurrence of anything on `app/llm/skills/cover_letter_style/banned_phrases.txt` (the same phrase twice counts as 2) |
| `within_word_limit` | ≤ 340 words (one page; aim is 250–300). Under 230 is noted, not failed |
| `no_placeholders` | No `[Company]`, `{name}`, `<role>`-style gaps |
| `has_sign_off` | Ends with a sign-off (Sincerely / Kind regards / Regards) |

## Graded by you (`evals/runs/<run>/grades.csv`, Y or N)

Read the letter next to the ad. Leave a cell blank if you haven't graded it yet;
the report counts blanks as "not graded", never as a pass.

| Column | Y when |
|---|---|
| `supported_musts_covered` | Every must-have in the ad that you can honestly back is addressed. Missing a must-have you *don't* have is fine (that's not a gap the letter should cover) |
| `no_unsupported_claims` | Every factual claim is true of you as your profile states it. Stretching counts as N: "presented to executives" when it was team leads, "led" when you contributed, a skill you only touched. Note the bad claim in `notes` |
| `specific_detail` | At least one detail specific to this company or role, not a sentence that would fit any ad |
| `would_send` | You'd send it after light edits (a few words or a sentence, not a rewrite) |

Use `notes` for anything that explains an N, or patterns you notice (e.g. "same
opening as the last three"). Notes are copied into the summary.

## The set

`scripts/letter_lab.py prepare` scores every snapshotted ad against your real
profile and picks up to 15 spread across score bands (≥85, 75–84, 50–74) so the
set covers strong matches and real gaps. Edit `evals/set.json` to swap ads in or
out. Keep the set fixed once you start comparing engines, or the numbers stop
being comparable.

## Workflow

```
python scripts/letter_lab.py snapshot       # ads from real.db + app.db -> evals/jobs/
python scripts/letter_lab.py prepare        # evals/eval.db (copy of your profile only), extract + score, pick set
python scripts/letter_lab.py run            # one letter per set job -> evals/runs/<run>/
# grade evals/runs/<run>/grades.csv
python scripts/letter_lab.py report <run>   # -> evals/results/<run>.md (committed)
```

Everything except `rubric.md` and `results/` is gitignored: it holds ad text,
your profile and letters written as you. `real.db` is only ever read.
