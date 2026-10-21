# Eval set: dev 20 / test 15 from real Seek ads (branch `eval-set-creation`)

**Goal:** a fixed set of real ads, tagged by scenario, to measure the cover-letter agent before
and after changes, at known cost. The dev set is tuned on; the test set is held out and run once.

## Stages

1. Scan real Seek ads into real.db (the user's browser, normal Scan Page / Scan more) until every
   scenario below has enough candidates, plus about 15 in reserve.
2. Freeze: snapshot the chosen ads (`letter_lab.py snapshot`) into the eval copy.
3. Tag each ad: scenario (from analyze_job + match_profile in the eval copy, confirmed by the
   user) and **Would I apply? Yes/No**.
4. Split: stratified by scenario, random within each scenario, into dev 20 / test 15, with
   near-duplicate ads kept to one side and used only when needed (rules in the Decision log,
   2026-10-21; `scripts/eval_split.py`).
5. Define the metrics before any grading.
6. Generate letters with the current system (unedited letters are what gets graded).
7. The user hand-grades the dev letters (no judge grades shown first, so nothing anchors them).
8. Calibrate the LLM judge on half the dev grades, check it on the other half, then freeze and
   version it.
9. Baseline scores. Main metric: the **relevant** score (Would apply = Yes ads); overall is
   secondary.

## Scenarios (dev / test)

| Scenario | Dev | Test | Rule |
|---|---|---|---|
| 1. Strong fit | 7 | 5 | Score 85+, no must-have gap, every nice-to-have supported. Partials allowed. |
| 2. Missing a nice-to-have | 3 | 2 | Score 85+, no must-have gap, at least one nice-to-have partial or gap. |
| 3. Missing a must-have | 4 | 3 | At least one must-have gap (ask_user fires). |
| 4. Listing-only skill | 2 | 2 | A requirement backed only by a listed skill (`guardrails.listing_only`). |
| ~~5. Screening questions~~ | 0 | 0 | Dropped 2026-10-20: no real ad had one (Decision log). |
| 6. Borderline | 2 | 2 | Score 70–84. |
| 7. Unrelated | 2 | 1 | Score under 40. |

Terms, as the agent's code defines them:
- **must-have gap** = `Requirement.needs_user`: importance essential, letter role headline or
  mention, status `gap`. This is what triggers ask_user.
- **partial** = some evidence, not full. It never triggers ask_user; the writer phrases the claim
  more carefully. Partials appear in nearly every ad, so they don't define a scenario. Each ad
  records its count of essential partials, so the report can split strong fits by it.
- **nice-to-have** = importance `nice_to_have`, any letter role except not_for_letter.

Where an ad fits more than one scenario, the first match wins in this order: 7, 85+ (3, 2 or 1
by the rules above), 6, 4, 3.

## Tracker

`python scripts/eval_pool.py` (read-only) sorts every usable ad in real.db (ad text over 200 characters, not closed,
scored) into these scenarios and prints have / need per scenario. For 85+ ads it reads the job's
latest letter run (its per-requirement verdicts); an ad with no run yet gets a text guess, marked
`?`. Runs from before the profile change (2026-10-20) are flagged. A skill counts as backed when it
is linked to an experience OR named in an experience's text, as match_profile cites sentences (React
is in the Rio Tinto text but not linked to it). These are provisional tags: the final tags come from
stage 3.

## Decision log

- 2026-10-20: set sizes dev 20 / test 15, from a scan of about 50 (about 15 in reserve).
  Scenario 1 weighted up (6 / 4) because the relevant score is the main metric.
- 2026-10-20: profile filled in with university projects (experiences 4–6, thesis updated), then
  frozen; the remaining listed skills stay as they are. Scores from before the change are stale.
- 2026-10-20: **strong fit relaxed** (owner): "every must-have" means no must-have **gap**;
  any number of partials is allowed. A cap on partials was rejected: partials don't change the
  agent's path, Gemini 3's temperature 1.0 makes the count vary between runs, and only one ad had
  none. The 85+ score is the strong-fit guard.
- 2026-10-20: **borderline checked before listing-only** (owner). Nearly every 70–84 developer ad
  also asks for a skill the profile only lists (TypeScript, JavaScript, C++), so with listing-only
  first, borderline read 0 while scenario 4 held 17. Borderline is a score band; listing-only
  still has plenty from the 60–69 ads.
- 2026-10-20: why 70–84 is thin (read from the scorer's reasons, not changed): a graduate profile
  scores 85+ on graduate roles and 55–68 on mid-level ones. Mid-level roles take a level gap
  ("clear level/scope gap" = 45–64 in `match.py`) and usually a missing core framework (Spring
  Boot, Angular, Node, PHP, Next.js). 70–84 needs a mid-level role on the profile's own stack, or a
  graduate role in a neighbouring field.

- 2026-10-20: **scenario 5 (screening questions) dropped** (owner). Of 116 real ads, none asks
  questions in its own text, which is all `answer_screening` reads. The two the tracker's regex
  caught were boilerplate ("only candidates that meet our client's selection criteria will be
  contacted", #12) and a pointer to Seek's form ("Please answer the pre-screening questions upon
  applying", #77). Seek employers ask in the Quick Apply form, which Phase 9 covers with its own
  eval (`scripts/screening_eval.py`). Government ads on Seek link out; their criteria are in the
  role description on the government's own site. Its 4 slots went to scenario 1 (the main metric)
  and scenario 3 (ask_user; plenty of ads). Known limit: `answer_screening` is untested on real
  ads; planting questions into real ads was rejected as it makes the set less real.

- 2026-10-20: the 14 ads scored before the profile change were re-scored (match only, no
  re-extraction; $0.03 with the scan's own scoring). Every 85+ ad held (#27 85 -> 88); most others
  moved 5 points or less; #53 Junior Business Analyst 55 -> 75 (into borderline). Pool: 145 usable.
- 2026-10-20: **candidates drawn** (stage 1 done; seed 20261020): every ad in scenarios 1, 2 and 6
  (10 + 9 + 6), and a seeded random sample of about double the target from 3 (10), 4 (8) and 7 (6).
  49 candidates via `scripts/eval_draw.py` (refuses to overwrite the sheet without `--force`) in
  `evals/candidates/candidates.csv` (gitignored: real ads) for the owner's
  Would-apply and scenario labels. Scenario 1 has 10 for 12 slots, but 1 + 2 together have 19 for
  17, and the final 1-vs-2 tags come from stage 3.
- 2026-10-21: **the 49 candidates frozen** (owner; no LLM calls) into `evals/sets/v2/` by
  `scripts/eval_freeze.py`: `ads/` (one JSON per ad, letter_lab's snapshot shape), `profile.db`
  (real.db with every job-side row removed and vacuumed: the frozen profile, since letters depend
  on it as much as on the ads, and `prepare` otherwise rebuilds it from the live real.db) and
  `MANIFEST.json` (source, alembic revision, git commit, seed, SHA-256 of every file). Files are
  read-only; `--verify` checks every hash and that the folder holds exactly the manifest's ads;
  `--add <id>` appends reserve ads and never rewrites one. A dropped ad stays in the pool: set
  membership (dev/test) decides what is used. The old 15-ad set (evals/jobs, eval.db, set.json)
  is untouched. Ads and profile.db are gitignored; only the manifest is committed. Checked: the
  ids match the sheet, no other ad's text is in the set, real.db unchanged. Found: two families
  of near-duplicates in the 49 (Hampshire #122/#124/#160 at 90%+ shared text, CIMIC #152/#153 at
  78%); keep each family on one side of the dev/test split, or keep one of each.
  Still to do: point letter_lab.py at the frozen set (build eval.db from its profile.db) with the
  dev/test change, then stage 3's paid analysis.
- 2026-10-21: **near-duplicate rules for the split** (owner), fixed before any letter or grade
  exists. Why: a family split across dev and test leaks (tuning on one twin partly tunes on the
  other, so the test score flatters the agent), and two twins on one side double-count one
  employer's template in a 35-ad set. `scripts/eval_split.py` applies them:
  1. **Family** = frozen ads whose texts are at least 60% alike (difflib ratio, word by word),
     joined transitively. At freezing: Hampshire #122/#124/#160 (87%+) and CIMIC #152/#153 (79%);
     the next pair was 48%, so 60% sits in a clear gap. (Word by word finds the same families as
     a character comparison in about 2 s, not about 5 minutes.) Reserve ads added later are
     checked against the whole set the same way.
  2. **Choose** ads scenario by scenario, scarcest first (fewest spares), seeded random within
     each; an ad whose family already has a chosen member is skipped, and used only if its
     scenario would otherwise fall short. Scarcest first means a family member goes to the
     scenario that needs it most (Hampshire #122 to scenario 1; #124 and #160 have spares).
  3. **Split:** a family with more than one chosen ad goes to dev, whole. The test set is the
     honest measure and has only 5 strong-fit slots, so it is kept as varied as possible.
     Everything else is a seeded random split within each scenario; a scenario that falls short
     splits in proportion to its dev:test target.
  4. Scenario 1 is short (10 candidates for 12 slots): a short targeted scan for strong fits
     comes first, so only one CIMIC ad is needed; the pair goes to dev only if it is still short.
  Inputs: the frozen ads, and the tags in evals/candidates/candidates.csv (`final_scenario`,
  filled at stage 3, overrides the draw's `scenario`; `scenario_ok` = N drops an ad). Seed
  **20261021**. The script prints the split for the owner's review and writes
  `evals/sets/v2/split.json` only with `--write`.

## Follow-ups (not for this eval)

- The scorer bunches on a few values (of 112 usable ads, 27 at exactly 35 and 15 at exactly 60),
  which leaves the middle bands coarse. Changing it would move every score mid-build, and the
  scorer isn't what this eval measures; look at it after the baseline.
