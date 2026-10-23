# Eval set: dev 20 / test 15 from real Seek ads (branch `eval-set-creation`)

**Goal:** a fixed set of real ads, tagged by scenario, to measure the cover-letter agent before
and after changes, at known cost. The dev set is tuned on; the test set is held out and run once.

## Stages

1. Scan real Seek ads into real.db (the user's browser, normal Scan Page / Scan more) until every
   scenario below has enough candidates, plus about 15 in reserve.
2. Freeze: snapshot the chosen ads (`letter_lab.py snapshot`) into the eval copy.
3. Tag each ad: scenario (from analyze_job + match_profile in the eval copy, confirmed by the
   user) and **Would I apply? Yes/No**.
4. Split: stratified by scenario, random within each scenario, into dev 20 / test 15, at most
   one ad per near-duplicate family (rules in the Decision log, 2026-10-21 and 2026-10-23;
   `scripts/eval_split.py`). **Done 2026-10-23: dev 17 / test 14, no more ads.**
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
| 4. Listing-only skill | 2 | 2 | Score 60+, and a requirement backed only by a listed skill (`guardrails.listing_only`). Floor added 2026-10-22. |
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

- 2026-10-22: **letter_lab runs on the frozen set** (commit 5812588). `--set v2` on every command
  (default v1, unchanged). v2 reads only `evals/sets/v2` (verified first); working files in
  `evals/work/v2/` (eval.db, set.json, runs/; gitignored), summaries in `evals/results/v2/`.
  DATABASE_URL is re-pointed in `main()` after argparse and before any app/ import, and refuses if
  app.db is already on another DB. v2's `prepare` builds once and scores only unscored ads
  (`--only`, `--rebuild`): re-scoring frozen inputs only re-rolls noise. set.json = all 49 keys.
- 2026-10-22: **stage 3 analysis run** (owner approved; one-ad trial first, #12 at $0.0216).
  `prepare --set v2` + `analyze --set v2 --label stage3` on all 49: **$1.235** (extract + score
  $0.12, analyze_job $0.49, match_profile $0.62); 0 failures. The rule now lives once in
  `scripts/eval_tags.py` (using `Requirement.needs_user` and `guardrails.listing_only`);
  `eval_pool.py` uses it (output byte-identical before/after), `scripts/eval_tag.py` writes
  `final_scenario`, `eval_score`, `why` into candidates.csv (backup in evals/candidates/backups/;
  every other column checked unchanged). An ad that fits no scenario gets `none`, which
  `eval_split.py` leaves out (a blank would fall back to the draw's tag).
  - Remembered "no"s: **affect 0 ads** (written with `--decided-gaps count`, the owner's leaning).
  - Tags vs the draw: 17 of 49 changed. 2 -> 1: #47, #73, #77, #78. 1 -> 2: #27. -> 3: #149
    (2), #189 (1). 3 -> 4: #50, #51, #66, #141, #163; 6 -> 4: #168. -> 6: #65 (4), #197 (3).
    3 -> 7: #129, #193. No scenario: #121 (90 -> 60, the only ad to cross 85), #160 (78 -> 60),
    #165 (40). Score noise up to 30 points (#121); #168 75 -> 55, #197 60 -> 75, #51 55 -> 40.
  - Counts: 1: 12, 2: 4, 3: 4, 4: 13, 6: 6, 7: 7, none: 3.
  - **Decided (owner, same day): scenario 4 has a 60 floor**, as `eval_pool.py` had. Without it,
    nearly every low-scoring developer ad with a missing must-have also lists a skill, so 4
    swallowed them. Re-tagged with `eval_tag.py --write --decided-gaps count --listing-floor 60`:
    #50, #163, #168 -> 3; #51, #52, #66, #142, #143 -> none. Counts 1: 12, 2: 4, 3: 7, 4: 5,
    6: 6, 7: 7, none: 8.
  - Split dry run (no floor): dev 17 / test 14; short: 2 by 1, 3 by 3. Scenario 1 is full only
    by using both CIMIC twins and Hampshire #122; one per family leaves it at 10.
    With the floor: dev 19 / test 15, only 2 short by 1.
  - Strong-fit scan needed: **2** (score 85+, no must-have gap) so neither twin is needed, plus
    1 more 85+ ad with a nice-to-have gap for scenario 2. (Superseded by the labels, next entry.)
- 2026-10-23: **all 49 candidates labelled** (owner, in chat, one ad at a time; no LLM calls).
  `would_apply`, `scenario_ok` and `comment` are filled in candidates.csv; backup taken first,
  every other cell checked unchanged.
  - **What the two answers mean** (settled while labelling; use the same meaning for reserve ads):
    - *Would apply* = Y only if the owner would send it **hoping to get the job**, long shots
      included. An application sent only for the Centrelink count is N: its letter's quality
      doesn't matter to the owner, so it shouldn't count towards the relevant score. #69 and
      #163 were changed Y -> N under this rule; #50 stayed Y.
    - *Scenario OK* asks whether the ad's **scenario** is right (`eval_split.py` drops every N).
      The owner mostly answered a slightly different question: "are the gap / partial /
      supported verdicts right?". For scenarios 1-4 the two are nearly the same, since the
      scenario comes from those verdicts. They differ only when a verdict is wrong but the
      scenario still holds; those were answered Y with the mislabel in the comment (#12, #54,
      #72, #73, #103, #193, #197). The three N rows are all mislabels that change the tag:
      #11 (an essential support requirement is really a gap, so 3 not 2), #82 and #186 (an
      essential is really a gap, so the fit is below the 70-84 borderline band). Kept as N.
      For scenarios 6 and 7 the question was "is this score band a fair read of the fit?".
      For `none` ads only Would apply was asked.
  - Would apply = Y: 1: 9 of 12 (N: CIMIC #152 and #153, degree not on their list; #157, a
    sales role), 2: 4/4, 3: 3/7, 4: 4/5, 6: 5/6, 7: 0/7, none: 5/8. 30 of 49 in all.
  - Split dry run: dev 19 / test 14. Only scenario 2 is short (dev 2/3, test 1/2) after #11
    dropped out. Scenario 1 is full only with both CIMIC twins and Hampshire #122, and dev's 7
    strong fits include #152, #153 and #157, all Would apply = N: just 4 relevant ones.
  - ~~Scan needed now: 2 ads at 85+ with a nice-to-have gap, and about 3 strong fits the owner
    would really apply for.~~ Superseded the same day (next entry): no more ads.
- 2026-10-23: **the split is final: dev 17 / test 14, no more ads** (owner). Stage 1 is closed;
  the 49 frozen ads are the whole pool. Written to `evals/sets/v2/split.json` (gitignored).
  - **Why no scan:** the two shortfalls barely touch the main metric. Scenario 2 (3 ads: dev #46,
    #124; test #27) runs the same agent path as a strong fit (a nice-to-have gap never fires
    ask_user), and 3 or 5 ads is an anecdote either way. The Would apply = N strong fits only
    count in the secondary overall score.
  - **Near-duplicates are never doubled any more** (replaces rules 2 and 4 of 2026-10-21): at
    most one ad per family, even when its scenario falls short. Out: Hampshire #122 (its twin
    #124 is in scenario 2) and CIMIC #152 (twin #153 in). #160 was already out (`none`). So the
    "family goes to dev whole" rule no longer fires; the code keeps it as a guard.
  - **A short scenario keeps test at its target** while dev still has at least as many; else it
    splits in proportion as before. Scenario 1 (10 for 12): dev 5 / test 5. Scenario 2 (3 for
    5): dev 2 / test 1 (test 2 would leave dev 1).
  - **Pins** (owner): the seeded shuffle put #157 (N, a sales role) in test and #12 (Y) in dev.
    Pinned #12 to test and #157 to dev (`PINS` in `eval_split.py`, also in split.json), so test
    keeps 5 strong fits the owner would apply for. Done before any letter or grade exists, so it
    can't bias results. The final split equals the 2026-10-23 dry run minus #122 and #152.
  - Result: dev 17 (10 relevant): sc1 #73 #78 #153 #157 #191, sc2 #46 #124, sc3 #69 #168 #189
    #205, sc4 #79 #90, sc6 #65 #85, sc7 #135 #162. Test 14 (11 relevant): sc1 #12 #47 #54 #71
    #77, sc2 #27, sc3 #50 #149 #163, sc4 #60 #141, sc6 #53 #197, sc7 #193.
  - Short against the 20 / 15 target: scenario 1 dev by 2, scenario 2 dev by 1 and test by 1.
    Report per-scenario results for 1 and 2 with their counts; don't read much into scenario 2.
  - Every ad in both sets gets a letter and is graded; Would apply only picks which letters
    count in the main (relevant) score. The overall score uses all 31.
  - Dev 20 / test 11 was considered and rejected (owner): the only move that keeps test's
    relevant count (test's three N ads, #149 #163 #193, to dev) leaves test with no unrelated ad
    and one missing-a-must-have ad, the cases where a tuned writer most likely overclaims
    unseen, and each test letter would weigh ~9% instead of ~7%.
  - Next: point set.json at the split (dev / test) after the owner reviews the diff, then stage 5
    (define the metrics before any grading).

## Follow-ups (not for this eval)

- From the labels (2026-10-23), match_profile verdicts the owner disagreed with:
  - Years and seniority asks ("5+ years", "8+ years", "senior commercial") rated **partial**
    against an internship (#69, #72, #103, #162, #193). They read as gaps.
  - Non-technical customer service used as evidence for **technical / IT support** (#11, #186).
  - A duty of the job ("interrogate the employer's live pricing systems", #149) tagged as an
    essential the candidate is missing. The owner kept the tag, but duties in an ad's
    "accountabilities" list are not skills a candidate can bring.
  - The frozen profile undersells real skills in places (owner's comments in the sheet). Not for
    this eval: the profile stays frozen; update it after the baseline.

- The scorer bunches on a few values (of 112 usable ads, 27 at exactly 35 and 15 at exactly 60),
  which leaves the middle bands coarse. Changing it would move every score mid-build, and the
  scorer isn't what this eval measures; look at it after the baseline.
