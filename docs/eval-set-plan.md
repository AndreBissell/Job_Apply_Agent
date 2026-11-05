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
5. Define the metrics before any grading. **Done 2026-10-23: docs/letter-metrics-plan.md,
   approved by the owner.**
6. Generate letters with the current system (unedited letters are what gets graded).
   **Done 2026-10-27: run `dev-agent-v1`, 17 dev letters, $4.39 (Decision log).**
7. The user hand-grades the dev letters (no judge grades shown first, so nothing anchors them).
   **Done 2026-10-28, rechecked 2026-10-29: sheet S1674, 17/17 graded; relevant send-readiness
   2.30, 3 of 10 send-ready; sounds_ai is a count rule and no_personal_reason (D4b) replaced
   no_real_reason (Decision log).**
8. Calibrate the LLM judge on half the dev grades, check it on the other half, then freeze and
   version it. The judge runs on demand from a script with a choice of **backend** (Decision
   log, 2026-10-28): `claude-cli` (the owner's Claude subscription, headless `claude -p`; the
   default) or `vertex` (Claude Opus on Vertex AI through `app/llm/client.py`, billed). Both
   send the same prompt from one builder. Calibrate on `claude-cli`; then run `vertex` once on
   the check half and compare both with the owner's grades. Only if they agree equally well
   are they interchangeable. Every judge output and report names the backend that produced it.
   **Approach agreed 2026-10-29 (Decision log): split, derived ticks, two new rules, probes,
   pass bar.**
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
    (define the metrics before any grading). Done the same day, without touching set.json (it
    stays all 49): `letter_lab.py run|analyze --set v2 --split dev|test` reads split.json
    (refused if made from another MANIFEST.json); `--only` narrows within the split; a v2 `run`
    with neither is refused, so letters are never paid for on all 49; run.json records the
    split and `--resume` keeps it.
- 2026-10-23: **stage 5 metrics drafted** in docs/letter-metrics-plan.md (no code, no LLM calls),
  awaiting the owner's review. Why: v1's `would_send` sat at 0/15 on every panel, so it couldn't
  separate versions. Stage 7's owner grade is now a 4-level send-readiness (by edit effort) +
  "anything untrue?" + reason ticks, about 2 min per letter, plus A/B picks on the 10 relevant dev
  ads when comparing versions. Stage 8 calibrates the judge on half the 17 graded dev letters and
  checks it on the other half (that doc's §6). Coverage is measured against one fixed reference
  checklist per ad: stage 3's `checks.csv` plus the owner's labelling corrections, confirmed
  before use. An AI-screener metric is noted in future_work/ only (owner).
- 2026-10-23: **stage 5 approved** (owner), after M2's weights moved to 4/2/1 and 5 overlapping
  metrics were cut (22 remain; that doc's Decision log). Next: its §7 build step 1, the reference
  checklist, with the owner's go-ahead.
- 2026-10-26: **reference checklist built and frozen** (metrics plan §7 step 1; no LLM calls).
  `scripts/eval_reference.py` (propose / show / decide / build / verify; tests in
  tests/test_eval_reference.py) -> `evals/work/v2/reference/` (gitignored): one read-only JSON per
  split ad (31) + MANIFEST.json with hashes; `verify` checks them. Source: the stage-3 run in
  eval.db, checked row for row against checks.csv. 16 proposals, owner answered each in chat:
  10 accepted, 6 rejected; #12's and #168's comments confirmed as no change (the frozen profile
  doesn't back the skill). The labelling follow-ups below are corrected in the reference only,
  not in match_profile. Scenario tags are NOT moved by it: a tag describes the path the agent
  takes on its own analysis. Only #149 would move (3 -> 1, its one must-have gap was a duty).
  Totals before -> after: partial 130 -> 123, gap 92 -> 98, must-cover 110 -> 106, must-have
  gaps 20 -> 23; 376 -> 377 rows (one split). Details in the metrics plan's Decision log.
- 2026-10-27: **stage 6 done**: `letter_lab.py run --set v2 --split dev --engine agent --label
  dev-agent-v1` (production's engine and limits, no side outputs: they don't change the letter).
  17/17 letters, 0 errors, $4.39 ($0.258/letter, relevant $0.287; estimate was ~$3.90, since
  rate-limit retries meant more drafts and checks), 487 s/letter. Gemini 429s and 5xx all the way through; the first
  process hung for hours on one request after a 5xx (the client sets no request timeout), was killed
  and finished with `--resume`. 15/17 clean. Not clean: `seek-95220494` (tool-call limit, a
  must-cover years requirement the profile can't meet) and `seek-94890841` (check_claims hit 429
  three times on draft 2, so the run handed back draft 1, which misses one requirement). Both are
  kept as the system produced them. Code-only `report` on it: gates 17/17, G1 12/17, C3 ad echo 3%,
  profile copy 14%, sample copy 3%, C7 near-templates 0 (nearest distances 77-87%), C4 115 phrases,
  mostly the profile's own institution, employer and project names retold in every letter.
- 2026-10-28: **the judge gets two swappable backends** (owner; for stage 8, nothing built yet).
  Why: the owner wants the judge callable on demand, without a Claude Code session spawning
  subagents, and to choose each week between spare Claude subscription usage and Vertex credits.
  The v2 pipeline already passes everything through files (`v2-pack` -> `answers/<grader>/<lid>.json`
  -> `v2-check` / `v2-merge` / `v2-agreement`), so only the step that fills `answers/` changes.
  - **The judge gets the packet and the standard only, never the repo** (owner, 2026-10-29):
    `letter_metrics.STOCK_PHRASES` and `STOCK_LIMIT` define the owner's sounds_ai rule and must
    not reach it. The claude-cli backend runs from a folder holding just those files, with file
    tools limited to it.
  - **One new command**, `grading_panel.py v2-run <tag> --backend claude-cli|vertex`. One prompt
    builder pastes the standard, profile, packet and output format into the prompt, so the judge
    needs no file tools, and both backends get identical text.
  - **`claude-cli`** (default; the owner mostly uses this): one headless `claude -p` per packet, a
    fresh context each time, pinned to Opus, with Claude Code's own system prompt and tools kept
    out (check the exact flags before relying on it). It sits outside `app/llm/client.py` on
    purpose: the judge is eval tooling, not the app, as the subagent judge already was.
  - **`vertex`**: `AnthropicVertex` through `app/llm/client.py` (`llm_usage` rows, cost, budget
    guard). Before building: one test call (with the owner's go-ahead) to confirm the model is
    enabled and whether the trial credits cover partner models; and a decision on the $5 daily
    cap, which one ~$7 judge run would hit.
  - **Same prompt is not quite the same judge** (Claude Code's setup, thinking and effort settings
    differ), so: calibrate on `claude-cli`, run `vertex` once on the check half (~$3-4), compare
    both with the owner. Interchangeable only if both agree equally well; otherwise reports
    compare only runs graded on the same backend.
  - **Always say what produced a rating.** Every answer file, `judge-<tag>.json` and report
    records the backend and model next to the judge version, and owner grades stay labelled as
    the owner's. Keep the backend constant across runs being compared where possible.
  - Replaces "run as Claude Code subagents, so no Vertex spend" in docs/letter-metrics-plan.md §6
    item 7 (that doc to be updated with stage 8).
- 2026-10-28, final 2026-10-29: **stage 7 done**: the owner graded all 17 `dev-agent-v1` letters
  blind (sheet S1674, shuffled, run not named; no judge or code score shown per letter first; no
  LLM calls), then rechecked the grades with Claude the next day so the judge is calibrated on
  consistent ones. The owner gave grades in chat and Claude typed them into the sheet's CSV, read
  back per row. `grade-sheet --unblind S1674` -> `runs/dev-agent-v1/grades-owner.csv` (kept
  identical to `grading/S1674/grades-owner.csv`); `report --set v2 dev-agent-v1` ->
  evals/results/v2/dev-agent-v1.md.
  - **Final grades** (relevant 10 / all 17):

    | Measure | Relevant | All |
    |---|---|---|
    | Send-readiness mean (1/2/3/4 counts) | **2.30** (1/6/2/1) | 2.35 (1/10/5/1) |
    | Send-ready (3+) | **3 of 10** | 6 of 17 |
    | untrue | 0 | 1 |
    | generic | 6 | 11 |
    | missed_important | 8 | 11 |
    | weak_example | 5 | 9 |
    | sounds_ai (count rule) | 8 | 13 |
    | no_personal_reason | 5 | 11 |
    | too_long | 5 | 7 |

    The only 4 is `seek-94904560` (scenario 3). Send-readiness never changed in the rechecks.
  - **untrue**: Y on 1 letter, `seek-95085104` (not relevant): it puts a skill (AWS) inside an
    experience (the internship) that didn't use it. Each sentence on its own is backed, so
    check_claims, which checks one claim at a time, passed it (a "clean" run). The owner first
    marked 4; on a recheck 3 were cleared as badly worded "carries over to" bridges, not false.
    The report's fabrication-free row now shows the owner's count beside the judge's.
  - **missed_important, rechecked row by row (2026-10-29)** against the owner's answer key (each
    letter's essential and important rows, split into backed / gap / not-for-letter). Definition
    unchanged: a backed (supported or partial) essential or important row not addressed. A gap is
    never a miss and leaving one out is fine (the owner's gap-bridging idea is in
    future_work/gap-bridging-tick.md). New rule in grading-standard-v2 §4.1: a hard skill must be
    named; a trait must be named or shown by an example, and describing an activity alone doesn't
    show a trait. 6 letters N -> Y, 2 Y -> N (one ticked for gaps, one for nice-to-have rows);
    every Y note starts "Missed R_ (...)" so the judge can be checked row by row.
  - **sounds_ai is a count rule (owner, 2026-10-29).** A stock-phrase count showed every letter
    is built the same way: a profile fact, often pasted word for word, then a bridge sentence
    ("prepares me to" in 14 letters, "carries over to" in 11); "I am drawn to" and "exactly the
    kind of" come from the owner's own writing sample. The by-ear tick (14/17) didn't track the
    count (Y letters 10.9 hits, N 9.7), so the owner set the rule: Y when
    `letter_metrics.stock_phrases` (C5b) finds more than `STOCK_LIMIT` (8) hits, from 13 generic
    phrase patterns plus one per profile line pasted into 3+ letters (`recycled_runs`). By ear ->
    rule: `seek-95256463` and `seek-95084704` N -> Y; `seek-95205206`, `seek-95219353` and
    `seek-95364016` Y -> N. Three letters sit exactly at 8. Owner-confirmed 2026-10-29.
  - **no_real_reason became no_personal_reason (owner, 2026-10-29).** Every letter's motivation
    was mostly the ad's own description restated with enthusiasm (a program's line about itself
    presented as the place the candidate wants to start their career). D4a ("grounded in the ad")
    passes that, so no_real_reason was 0/17 and could teach the judge nothing. New checklist item
    **D4b** (19 items now; D4a unchanged): is a reason for applying tied to the candidate's own
    experience, study or goals? N needs the candidate's past to explain why they want something in
    the role ("Having worked on X, I want to understand Y"); experience offered only as a skill to
    apply is fit, not motivation. The tick `no_personal_reason` = D4b N. Claude proposed the 17
    values with `runs/dev-agent-v1/review-motivation-sounds-ai.md`; the owner confirmed them
    2026-10-29, changing one (`seek-95140202` Y -> N): 11 Y, N on `seek-95110025`,
    `seek-95219353`, `seek-95364016`, `seek-95140202`, `seek-95158567` and `seek-94904560`.
    `letter_metrics.motivation` (C5c) lists each motivation sentence with its share of words from
    the ad and the profile names it uses: a helper, not a verdict (it finds a profile name in
    10/17 letters, more than the 6 with a personal reason, because a name used as a skill to
    apply still counts for it).
  - **The judge never sees the sounds_ai list or limit**, so it is graded on hearing overuse, not
    on matching a list: the standard's sounds_ai says only "overuses stock or machine-sounding
    phrasing", and the counted phrases were taken out of its examples (D2c, D3a, D6b, a §4.1
    example). The list lives in code; the stage-8 runner must not give the judge the repo (see the
    backends entry).
  - **The 2 not-clean runs** (`seek-95220494`, `seek-94890841`) failed only on a years-of-experience
    row the run's matcher rated partial; the owner's key says gap (years judged against relevant
    paid work), so leaving it out was right and the revisions could not succeed (follow-up below).
    `seek-94890841` stays on draft 1 (the 429 case), as production would have handed it back.
  - **What the notes say, in the owner's words, summarised:** the weaker of two experiences used
    as the main example where the stronger one fits (5 letters); experiences mixed within and
    across paragraphs, so the letter reads as confused (5); a strong, fitting example underused
    (`seek-95084704`); the ad's most important skill named last (`seek-94890841`); requirements the
    profile lacks left out rather than framed as wanting to learn them (`seek-95219353`).
  - **Code checks after grading (owner, 2026-10-29; nothing the judge sees):**
    - G1's 5 failures were all a skill named 3+ times, none a skill list: the role's own core
      skills, or "AI" where the employer's or program's name contains it. G1 now flags 4+ mentions
      and skips mentions inside the employer's name or 3+ words in a row from the ad's title
      (`letter_metrics.name_phrases`): 12/17 -> 14/17 pass; the 3 failing name "AI" 4-6 times.
    - C4 also skips the profile's employers and institutions (word by word) and project titles
      (whole): 115 -> 98 phrases. A project described inside an experience's text isn't a name
      field, so it still shows (16 letters).
    - C7 unchanged, an alarm (none under 10%; distances 77-87%); repeated wording shows in C4.
    - The not-clean flag, G1 and C7 don't track the owner's main complaints (example choice and
      order, motivation); that gap is what the stage-8 judge is for.
  - Next: stage 8 (judge calibration, backends per the entry above), with the owner's go-ahead.
- 2026-10-29: **stage 8 approach agreed** (owner; no code yet, no LLM calls). Why it differs from
  docs/letter-metrics-plan.md §6: the stage-7 grades bunch at 2 (1/10/5/1 for grades 1-4), so a
  judge that answers 2 every time already meets "within ±1 on all but one" (16/17); and the 4, the
  1 and the one untrue letter are single letters, so neither half can test them.
  - **Split:** seeded, stratified by owner grade then relevance (not by scenario). `seek-94904560`
    (the 4), `seek-95110025` (the 1) and `seek-95085104` (untrue) are forced into calibration,
    where they are useful as examples: about 8 calibration / 9 check, with about 3 threes in the
    check half so the 2-vs-3 boundary is tested. Code refuses a check-half letter as an example.
    Made before any judge output exists.
  - **Derived ticks:** the judge still answers all six ticks (it makes it think them through), but
    the scorecard takes `missed_important` from its row answers (a backed essential or important
    row with `addressed` N), `no_personal_reason` = D4b N, and `sounds_ai` = C5b over
    `STOCK_LIMIT`. The judge's own sounds_ai is reported as an alarm, never calibrated on: when it
    hears AI on letters the list passes, the list needs new phrases (writer v3 already swapped old
    formulas for new ones once). Why: a tick can't contradict the judge's own row answers; missed
    rows are compared row by row (about 100+ rows, not 9 ticks); one definition, in code, as D6f's
    word count already is. Code-only motivation (C5c) was rejected: it can't tell a skill offered
    for the job from a motive (a profile name in 10/17 letters, 6 with a personal reason).
  - **Two new rules in the standard** (owner's notes; both shown by calibration-half letters):
    - *Experiences connect* (5 letters): D2d "do the experiences in each body paragraph connect?"
      (two are fine when they make one point, the same requirement or one building on the other;
      test: one sentence states the paragraph's point and every experience in it supports it;
      unrelated experiences side by side, or three or more that don't serve one point, = N) and
      D2e "is each experience told in one place?" (going back to one in a later paragraph after
      another = N). Added to §7.2's must-edit list as a paragraph rework (a 2). In D2, the custom
      group. Checklist 19 -> 21 items.
    - *Relevance before kind* (5 letters: a non-technical job used where the internship backs the
      same row): the strongest evidence is the most relevant to the row first, then the kind
      (technical work for a technical row beats non-technical paid work). M3's kind order can't see
      this; noted as its limit.
    Both are written into the standard at step 4 below.
  - **Calibration examples** go in a gitignored file under `evals/work/v2/calibration/` (they quote
    real letters; the repo is public), added to the prompt by the builder, with its hash in the
    judge version. Only generic rules go in the committed standard. Iterate with ONE grader on the
    calibration half, leave-one-out (a letter is never its own example); the check half is measured
    once with the full panel (A + B + adjudicator), and a second time only for a logged change
    justified on the calibration half.
  - **Probes:** *length* = about 30 words of filler on the 3 shortest check letters, staying ≤ 340
    (§6.6's +60 pushed every letter, 296-337 words, over the cap, so it tested the cap, not length
    bias); *untrue* = 3-4 kinds of claim planted in copies of check letters, including a skill put
    inside an experience that didn't use it (the one check_claims passed).
  - **Pass bar** (fixed now, before any check-half run; 9 check letters, so counts):
    send-readiness beats always-2 on exact matches, agrees on ready / not ready (3+ vs ≤ 2) on at
    least 7 of 9, and is within ±1 on at least 8 of 9; every planted claim caught and every
    judge-only untrue flag reviewed by the owner; at least 80% of the owner's missed rows also
    missed by the judge; D4b agrees on at least 7 of 9; no padded copy scores higher than its
    original. `generic`, `weak_example` and `too_long` are reported, not gated: with base rates
    around 60%, 9 letters can't separate a judge from chance. (`too_long` isn't a word count: Y on
    7 of the 11 letters graded 1-2, on none of the 6 graded 3+, all letters 296-337 words.)
  - **claude-cli isolation:** `--bare` needs an API key (no subscription login), so isolation comes
    from `--system-prompt`, `--tools ""`, `--setting-sources ""`, `--strict-mcp-config`,
    `--disable-slash-commands`, `--no-session-persistence`, `--json-schema` (the §9 shape), a full
    model id (not the `opus` alias) and a pinned `--effort`, run from an empty folder outside the
    repo. One probe call must show that no CLAUDE.md, memory or hooks reach it before it is used.
  - **Steps, each with the owner's go-ahead:** 1 `v2-run` backend + isolation probe; 2 `v2-split`;
    3 derived ticks + agreement report (always-2 baseline, ready / not ready, row-level misses);
    4 examples + the two rules; 5 calibration iterations; 6 probes; 7 check half; 8 freeze judge
    v1 (model id, effort, CLI version, standard and examples hashes); 9 `vertex` once on the check
    half (optional; test call and the $5 daily cap decided then); 10 judge all 17 dev letters.
- 2026-10-29: **stage 8 step 1 done: `grading_panel.py v2-run`** (backend `claude-cli`; procedure
  in evals/grading-panel.md, tests in tests/test_grading_panel_v2.py with a fake backend).
  - **Isolation probe** (3 tiny calls, an empty folder, a canary question about RTK, which only
    the owner's global CLAUDE.md mentions): with the flags above the judge had no tools and knew
    nothing of CLAUDE.md; a control call without `--setting-sources ""` quoted CLAUDE.md and
    RTK.md word for word, so the probe can see a leak. Two things still reach it: the account's
    email and the working folder's path; the runner uses a neutral, empty `%TEMP%\letter-judge`.
    Claude Code also makes a small side call on Haiku; the runner refuses an answer the judge
    model didn't give.
  - **The CLI on PATH (npm, 2.1.128) is too old** for `claude-opus-5-5` (the API asks for 2.1.280+)
    and is a `.cmd` shim, which the runner refuses (cmd.exe mangles the schema's quotes). Set
    `JUDGE_CLAUDE_BIN` to a newer `claude.exe` (the VS Code extension bundles 2.1.312), or update
    the npm install.
  - **Built:** `v2-run <tag> [--stage grade|adjudicate]`, one fresh `claude -p` per answer file,
    prompt on stdin, `--json-schema` for §9's shape (the answer comes back as `structured_output`),
    then `validate()`; a wrongly shaped answer is kept as `<lid>.rejected.json` and a fresh judge
    tries again (2 in all). Every answer file records the judge version (backend, model, effort,
    CLI version, standard and instructions hashes); `v2-merge` refuses a panel that mixes versions,
    and the merged file, the agreement report and `letter_lab.py report` name it. The standard's
    §9 now says "answer with valid JSON", not "write it with a tool". Pairs are not in `v2-run` yet
    (not needed in stage 8; add before the first comparison).
  - **One real run** on one calibration-half letter (`seek-94904560`, grader A only, a throwaway
    panel, deleted after): valid on the first try, 68 s, $0.28 notional (Claude Code's own figure;
    on the subscription it is usage, not a bill). At that rate the full 17-letter panel is about
    $10 notional and the check half on `vertex` about $6 rather than $3-4.
  - Next: step 2, `v2-split`, with the owner's go-ahead.
- 2026-10-29: **stage 8 steps 2-4 built; step 5 started** (owner: go as far as possible before
  feedback is needed). Procedure in evals/grading-panel.md ("Calibrating the judge").
  - **Step 2, the halves** (`v2-split dev-agent-v1`, seed 20261029, written once to
    `evals/work/v2/calibration/split.json`): calibration 8 = `seek-94904560` (the 4),
    `seek-95110025` (the 1), `seek-95085104` (untrue), `seek-95100426`, `seek-95140202`,
    `seek-95191929`, `seek-95220494`, `seek-95260505`; check 9 = `seek-94890841`, `seek-94932262`,
    `seek-95084704`, `seek-95158567`, `seek-95205206`, `seek-95219353`, `seek-95220951`,
    `seek-95256463`, `seek-95364016` (six 2s, three 3s; 5 relevant).
  - **Step 3**: `v2-merge` writes the derived ticks (`missed_important` from the rows,
    `no_personal_reason` from D4b; the judge's own six kept as `judge_ticks`; derived ticks are no
    longer cells to adjudicate). A missable row = supported or partial, essential or important,
    headline or mention (none of the owner's 15 missed rows is `implied`). `v2-agreement` scores
    the pass bar (scaled from 9 letters), reads the owner's missed rows from the "Missed R_" notes,
    and marks each tick derived / alarm / reported. `--half` on pack, run and agreement; one-grader
    check and merge (`--graders A`).
  - **Step 4**: D2d and D2e in the standard (§6, §7.2; checklist 21 items) and the relevance rule
    (§4.2, §7.3); §10 now says where the examples live. `v2-examples` drafted
    `calibration/examples.md` (the 8 calibration letters, their rows, the owner's grade, ticks
    without sounds_ai, and note); Claude wrote the 8 worked readings. **Not yet confirmed by the
    owner.** `v2-run` adds them (leave-one-out) and records their hash.
  - **Step 5, two one-grader runs on the calibration half** (tags `cal-bare`, `cal-ex1`; the
    second with the unconfirmed readings):

    | Measure (8 letters) | Bare standard | With the draft examples | Bar |
    |---|---|---|---|
    | Exact (always-2 gets 4) | 4 | 4 | more than 4 |
    | Ready / not ready | 6 | 5 | at least 6 |
    | Within ±1 | 8 | 7 | at least 7 |
    | Untrue flag caught (`seek-95085104`) | no | no | yes |
    | D4b | 7 | 7 | at least 6 |
    | Missed rows | 4/5 | 4/5 | 80% |
    | generic / weak_example / too_long agree | 3 / 3 / 4 | 6 / 2 / 3 | reported |
    | Grades given (1/2/3/4) | 0/7/1/0 | 0/4/4/0 | owner 1/4/2/1 |

    Bare, the judge grades almost every letter 2. With the examples it moves up and ticks
    `generic` like the owner, but it never gives a 1 or a 4, and it never flags the internship
    AWS claim. Leave-one-out means the only untrue example is never shown to the letter it is
    about, so only the planted claims (step 6) can test that. D2e caught both letters the owner
    called mixed; D2d said N on `seek-95100426`, which the owner didn't fault. One grader, one
    sample each: differences of a letter or two may be noise.
  - Waiting on the owner: confirm the readings, rule on one judge-only stretch flag, and decide
    whether §5 gets a rule for a skill placed in the wrong experience.
- 2026-10-29: **owner's answers, and calibration run `cal-ex2`** (A + B, confirmed examples).
  - Readings confirmed. On `seek-95110025` the owner did no help-desk work at either job: the
    reading now says so (both jobs are related at most). The answer key rates that row (R2)
    partial, with both jobs as evidence: a row for the checklist recheck, not changed now (the key
    is frozen). The judge-only stretch on `seek-95110025` ("evaluated applications throughout their
    development") is accepted: recorded in `calibration/accepted-untrue.json`, which
    `v2-agreement` always reads, and in that example's Untrue line. The blind grades file is left
    as graded (the split is pinned to its hash).
  - §5 gains "a skill put in the wrong experience" (check that experience's entry, read across
    sentences).
  - `cal-ex2` stopped on the subscription's 5-hour usage limit after 15 of 16 judges (grader A on
    `seek-95220494` missing). `v2-run` now stops at a usage limit instead of retrying. Grader B
    alone (preview panel `cal-ex2-b`): exact 4/8 (always-2 4), ready 5/8, within ±1 7/8, untrue
    caught 1 of 2 (the accepted stretch; the internship AWS claim still missed), D4b 6/8. B
    applied the new rule once more, on `seek-95140202` (a judge-only flag for the owner).
  - **The gaps are systematic, not noise:** A and B gave the same grade on every letter both
    answered, and three runs agree. `seek-95110025` (owner 1) and `seek-95260505` (owner 2) get a
    3: the judge sees the mixed experiences (D2e N) but counts regrouping them as a small edit.
    `seek-95100426` (owner 3) gets a 2: D2d N, although its two experiences make one point.
  - Resume after the reset: `v2-run cal-ex2`, then `v2-check`, `v2-disagreements`,
    `v2-run cal-ex2 --stage adjudicate`, `v2-merge`, `v2-agreement ... --half calibration`.
  - Usage: a one-grader pass on the calibration half is 8 judges; a full panel there is about 24;
    the check half with probes about 35. The judge shares the owner's subscription window with
    their own Claude Code sessions.
  - **Owner correction, `seek-95100426`:** `missed_important` Y -> N. R6 (integrations and
    automations between systems) has no experience behind it that really applies, so leaving it
    out is not a miss. Changed in both copies of grades-owner.csv (run folder and sheet S1674,
    still identical; the split's stored hash of the file is now stale, and the split itself is
    unaffected, since it uses only grades, untrue and relevance) and in that letter's example.
    **The answer key now says gap too** (owner, same day: nothing in the profile fills it, and a
    gap is never a miss). Recorded the way the 16 earlier calls were: an owner correction in
    `reference/inputs.json` (85:R6, the API and cloud work moved to `related_evidence`), `propose`
    (every earlier decision kept), `decide 85:R6=yes`. The key's freeze was lifted on purpose to
    rebuild it: backup in `evals/work/v2/reference.bak-20261029/`, old manifest and files removed,
    `build` and `verify` re-run. Only `seek-95100426.json` changed; totals partial 123 -> 122,
    gap 98 -> 99, M2 weight base 635 -> 633. Panels packed before this hold the old row.
  - **The owner's weighting goes in the standard** (§7.2, "How the candidate weighs the
    problems"), so the judge reasons the way the owner grades: (1) an untrue claim must come out
    and the passage be rebuilt: a fabricated claim costs two grades (a 2 at best), a stretch or an
    employer fact not in the ad one grade (an otherwise perfect letter is a 3; owner, same day); (2) a missed important requirement or a weak example usually keeps a letter below 4,
    and together, or when the fix reworks a paragraph or swaps the main example, makes it a 2;
    (3) generic, sounds AI, no personal reason and too long cost at most one grade on their own.
    It fits all 8 calibration grades (untrue: 2 and 1; missed or weak: 2, 2, 2; tier 3 only: 3, 3;
    none: 4). This **reverses** 2026-10-26's "one stretched sentence fixed in place is a light
    edit" for untrue claims; the other "not sendable" triggers are still counted by the edit they
    take (no automatic cap).
  - **Owner's last three calls (same day):**
    - The judge-only flag on `seek-95140202` (AI-assisted tools, in the
      thesis paragraph) is **rejected**: the owner uses AI-assisted development in every project,
      and the thesis itself is AI work. Not recorded as accepted, so it counts as a judge-only flag
      (TNR) if the judge raises it again. The frozen profile names AI-assisted tools only under the
      internship, so the judge can't know this: after the baseline, add it to the profile (with
      the follow-ups below).
    - **Mixed-up experiences are a small fix** (replaces Claude's proposed "regrouping is a
      paragraph rework"): the internship, then a project, then back to the internship would be a
      3 if it were the letter's only problem; alongside other edits it usually makes a 2. In the
      standard: §7.2's must-edit list now says "regroup them", and tier 3 of the weighting holds
      mix-ups; a 1 takes problems from all three tiers. The readings for `seek-95085104`,
      `seek-95110025` and `seek-95140202` now say why each is a 2 or a 1 without calling the
      mix-up heavy.
    - **D2d:** two or three experiences in one paragraph are fine when they are well aligned to
      the same point.
  - Built while waiting: `v2-probe` / `v2-probe-report` (step 6 tooling, with tests). The probes
    themselves are written only once the prompt has settled, so the check-half letters they copy
    can't steer it.
  - **Run `cal-ex3`** (grader A only, calibration half, everything above in; ~4 min): **FAIL**.
    Exact 4/8 (= always-2, as in every run so far), ready 6/8, ±1 8/8, untrue 1/2, D4b 6/8,
    missed rows 3/4 (one miss fails on 4 rows). The four grade misses, by cause:
    - detection: `seek-95110025` (owner 1, judge 2) — the accepted stretch read as "a light
      generalisation", the retail job not seen as a weak example, the jump-around not seen (D2d/D2e Y);
    - weighting: `seek-95260505` (2 vs 3) — miss + no personal reason judged a 3;
      `seek-95100426` (3 vs 2) — mix-up + no personal reason + formula bridges judged a 2, as
      the tier-3 text ("alongside other edits it usually makes it a 2") says;
    - strictness at the top: `seek-94904560` (4 vs 3) — a repeated "I want to / bring this"
      structure and a thin retail-job line counted as edits.
    - The owner's own ticks predict the owner's 8 grades exactly under an additive reading:
      start at 4, take one grade per tier hit (tier 3 counts once however many apply; fabricated
      two), floor at 2 unless fixing means rewriting most of it (then 1). Proposed to the owner;
      not applied.
    - Applied: §5 stretch bullet for a generalisation from single tasks to an ongoing practice
      (generic example; the owner accepted this kind of call on `seek-95110025`).
    - **Owner regrade:** `seek-94904560` 4 -> 3 after seeing the judge's edits (thin retail-job and
      student-team lines, a repeated "I want to / bring this" pattern): "definitely a 3". Both
      grades-owner.csv copies and the worked example updated; no ticks added. All-17 mean
      send-readiness 2.35 -> 2.29; send-ready count unchanged (a 3 is ready). The calibration half
      now has no 4. `cal-ex3` rescored: exact 5/8 (beats always-2), still FAIL on untrue TPR 1/2 and
      missed rows 3/4.
    - **Owner reversal:** `seek-95110025`'s "I have evaluated applications throughout their
      development" is NOT a stretch (it introduces the two testing examples after it). Removed from
      accepted-untrue.json (now empty); the worked example says Untrue N and keeps the 1 for the
      miss, the misfit retail-job example and the unfollowable mix-up. The §5 generalisation bullet
      added above is replaced by "A summary sentence is fine". The calibration half's only owner
      untrue flag is now `seek-95085104` (AWS work placed in the internship), which `cal-ex3`
      caught. Rescored: everything passes except missed rows 3/4 (`seek-95140202` R6, safety).
    - **Owner correction:** `seek-95140202` R6 ("Safety first, Quality always", key: partial) is
      NOT a miss: the letter covers the quality half, and nothing in the profile is safety
      experience. The key needed no change (already partial, quality backed). missed_important
      cleared in both grades-owner.csv copies (now 9/17), grade stays 2 (the retail-job example needs
      paragraph 4 reworked), worked example updated. Same rule as `seek-95100426`: the part the
      profile can't back is never a miss.
    - **`cal-ex3` rescored: PASS** on the calibration half (exact 5/8, ready 6/8, ±1 8/8, untrue
      1/1, D4b 6/8, missed rows 3/3). Still one grader on one run, and the examples and standard
      have changed since, so it is not yet a frozen-judge result.
    - **Additive weighting (owner: yes).** §7.2's weighting now reads: start at 4, one grade per
      tier hit (fabricated two; a tier counts once however many of its problems apply; mix-ups are
      tier 3), floor 2 unless fixing means rewriting most of the letter (then 1). §7.2's "recites
      the profile ... (a 2)" dropped; the tiers set the grade. `seek-95085104`'s reading explains
      the floor.
    - **Run `cal-ex4`** (A + B + adjudicator, calibration half, fresh answers, ~7 min): **PASS**.
      Exact 5/8, ready 7/8, ±1 8/8, untrue 1/1, D4b 6/8, missed rows 3/3, kappa 0.60; A/B agree
      on 7/8 grades. Left: `seek-95085104` 2 vs 1 and `seek-95110025` 1 vs 2 (both judges call the
      first one's mix-up a rewrite and the second's a regroup: the reverse of the owner);
      `seek-95191929` 2 vs 3 (the adjudicator ruled weak_example can't apply when every row is a
      gap). weak_example agrees 3/8, and 3 of the 4 owner-only ticks are "the retail job used where it
      shouldn't be". The rejected AI-assisted flag on `seek-95140202` recurs (TNR 6/7), as expected.
    - **Owner's example ranking** (replaces §4.2's two-line relevance rule): first, does the
      example show what it is offered for (if not, a *wrong* example); then (1) professional work
      in the job's field where the skill was used, (2) uni or personal work in the same field,
      (3) work outside the field that still shows the skill. A higher rung beats a lower one for
      the same point. `weak_example` now judges every point the letter makes, gap rows and
      no-row points included (the adjudicator's `seek-95191929` ruling is overturned). Both kinds
      stay one grade (tier 2); a wrong example is the worse kind, weighed between a 2 and a 1.
      Owner kept `seek-95191929` and `seek-95140202` at 2 (the latter would be a 3 with the
      internship as its example); readings updated.
    - **Run `cal-ex5`** (A + B + adjudicator, ~9 min): **PASS** again: exact 5/8, ready 6/8, ±1
      8/8, untrue 1/1, D4b 6/8, missed rows 3/3, kappa 0.45; A/B agree 7/8. `seek-95085104` now
      matches (2: both judges applied the floor). New misses: `seek-94904560` 3 vs 2 (the
      adjudicator called the thin retail-job and student-team lines after the internship "weaker
      examples", tier 2); `seek-95191929` still 2 vs 3 (both graders still reason from rows: "all
      rows are gaps", so no weak example); `seek-95110025` 1 vs 2 as before. weak_example agrees
      2/8 (was 3/8). Between runs about 2 letters change: the misses are near run-to-run noise.
    - **Owner regrade:** `seek-95110025` 1 -> **2**, and too_long withdrawn (309 words is in
      range). Its problems are two tiers (missed R5 + the help-desk example; the split internship
      story + retelling), and the fixes are contained, so the floor of 2 holds. The calibration
      half now has no 1. Worked example updated. Rescored (no LLM): `cal-ex4` exact 6/8 and
      `cal-ex5` exact 6/8 (always-2 now 5/8), both PASS; cal-ex5's only misses are now
      `seek-94904560` and `seek-95191929`.
    - **Owner kept `seek-94904560` at 3 and `seek-95191929` at 2**, and explained both misses.
      (a) A thin weaker line added after the best example for the same point is a sentence to cut
      (tier 3), not `weak_example`, which means the point's main example is the weaker one
      (§7.3). (b) The judges left 95191929's retail-job paragraph unticked because only the retail entry
      says "changing priorities". Owner: Agile sprint work with ongoing stakeholder feedback *is*
      adapting to changing priorities. A §4.2 rule saying so was added, then reverted the same
      day (owner): written to flip one letter around one profile entry, it overfits, and with
      leave-one-out a pass would only show the answer was written in. That reading lives in the
      95191929 worked example only. (a) stays in the standard: it defines the tick. Both worked
      examples updated.
- 2026-10-29: **stage 8 step 7: the check half, measured once (panel `check-v1`): FAIL.** Full
  panel (A + B + 8 adjudicators), judge claude-cli `claude-opus-5-5`, effort high, CLI 2.1.312,
  standard `375a2d5b`, instructions `fa815f03`, examples `eb8db6c2` (the final v1 text: §7.3's
  thin-line rule is in). Every answer valid first time; A/B agree on 8/9 grades.

  | Measure (9 letters) | Result | Bar | |
  |---|---|---|---|
  | Exact | 3/9 | more than always-2 (6/9) | fail |
  | Ready / not ready | 3/9 | at least 7 | fail |
  | Within ±1 | 9/9 | at least 8 | pass |
  | Untrue TPR | 0/0 (owner flagged none) | all | - |
  | D4b | 8/9 | at least 7 | pass |
  | Missed rows | 4/10 | 80% | fail |
  | Untrue TNR | 6/9 (3 judge-only flags to review) | reviewed | |

  Grades (owner vs judge): 2 vs 3 on `seek-94890841`, `seek-94932262`, `seek-95084704`,
  `seek-95220951`; 3 vs 2 on `seek-95256463`, `seek-95364016`; the other 3 agree. Ticks agree:
  generic 2/9, weak_example 4/9 (the judge ticked it on none; the owner on 5), too_long 6/9,
  sounds_ai 2/9 (alarm). Causes, from the answers (nothing changed):
  - **The judge's grades count only `must_cover` misses** ("every must-cover row is addressed, so
    only tier 3"), although §7.3 defines missed_important as a must-cover OR an essential or
    important backed row. On `seek-94890841` and `seek-95084704` its own rows say an important
    row is missed (the derived tick is Y), yet it grades 3.
  - **Partial rows are addressed too easily**: 6 of the owner's 10 missed rows (all but one
    important + partial) were counted addressed on a loosely related quote (cloud databases for a
    named-database row, a cross-functional team for international teams, failure testing for
    resilient integrations, delegating for investigating independently).
  - **The owner's three 3s each carry a tier-2 tick** (missed or weak), which §7.2's additive rule
    makes a 2. On `seek-95364016` the weak tick may be the thin add-on line §7.3 now calls tier 3;
    on `seek-95219353` (a missed listing-only row) and `seek-95256463` (a missed must-cover row
    plus a thin add-on) the rule gives 2. So a judge applying the rule perfectly to the owner's
    own ticks gets at most 7/9 exact here.
  - `seek-95364016`'s 2 comes from a stretch only the judge flagged (owner to review).
  - 14 checklist doubts (panels/check-v1/checklist-doubts.md); none change a score.
  - Not tuned on. Waiting on the owner: the judge-only untrue flags, and what to do about the fail.
- 2026-10-30: **idea, not decided: a points score replaces the 1-4 grade** (owner). A continuous
  0-10 score to one decimal: start at 10, deduct per problem found, each kind of problem with a
  min-max deduction and a severity; the judge only identifies problems and their severity, code
  does the sum. Why: the check half failed on the judge turning problems into a grade (it ignored
  its own missed important rows) as well as on spotting them; code-only scoring makes the first
  part consistent, and calibration becomes item by item instead of 9 grades. A first draft
  (fixed weights, from the owner's existing labels only, no LLM; gitignored
  `evals/work/v2/points-draft/`): grade-3 letters 6.8-10.0 (mean 8.5), grade-2 5.7-8.1 (mean
  6.9); a cut at 8.2 separates them on 16/17. Off: `seek-94904560` (10.0, owner 3: its regrade
  reasons were never ticked) and `seek-95256463` (6.8, owner 3). Labels not yet given: mix-ups,
  thin extra lines, wrong vs weaker examples. The design is to be worked through in a new session.
- 2026-11-03: **points scheme decided; labels frozen** (owner). Replaces the 1-4 grade from the 2026-10-30
  idea. The owner relabelled the 17 dev letters (`dev-agent-v1`): a gut score out of 10 (fit left out), then every
  problem as low or high under 8 metrics, then four reviews across all letters (generic, no personal reason,
  Claude's backup flags, the five check-v1 judge-only untrue flags: all rejected). Labels:
  evals/results/v2/points-labels-dev-agent-v1.json; rules: evals/results/v2/points-severity-guide.md (scrubbed
  copies of the gitignored evals/work/v2/points-draft/). Rules worth knowing: a wrong example is a missed row or
  untrue, never a weak example; the swap test (the weaker example carrying a point alone) is high even when it
  uses the ad's words; a listed skill named as a skill beside an experience is clean, worded as used there it is
  untrue; structure alone (order, repetition, a missing greeting) costs nothing; missed-row severity is code
  (must-cover = high); stock phrasing is code (9-11 hits low, 12+ high). Fit is a separate score from the
  answer key, not deducted. Values (low / high): start 10, floor 0, no cap; untrue -1.0 / -2.5; missed row
  -0.75 / -1.5 (matters most to a recruiter's screen); mix-up -0.5 / -1.5 (disruptive, leads to stretches); weak
  example, no personal reason, too long -0.5 / -1.0; generic -0.3 / -0.6 (partly charged by stock phrasing and no
  personal reason); stock phrasing -0.25 / -0.5 (a find-and-replace fix). Set by the owner from meaning; against
  the gut scores: mean gap 0.78, correlation 0.46 (an equal -0.5 / -1.0 draft gave 0.69, 0.52; a gut read can't see
  a missed row without the ad). Off by more than 1: `seek-94904560` (9.2 vs 6.9: uncharged structure, and a job
  the owner doesn't want), `seek-95260505` (6.2 vs 8.0), `seek-95219353` (8.9 vs 7.5), `seek-95205206` (7.7 vs
  6.5), `seek-94890841` (6.8 vs 7.9), `seek-95158567` (8.2 vs 7.1). Pass-bar idea: untrue high = not ready
  whatever the points (at -2.5 it alone leaves 7.5).
  Does fit explain the gap (owner's idea: the gut read was swayed by how well the profile fits the ad)? Only a
  little: gut vs fit r=0.16, gut vs match score 0.19, (gut - points) vs fit 0.28, points vs fit -0.14. A blend of
  90% points + 10% fit gives r=0.50 (from 0.46), within chance at 17 letters; `seek-95191929` (every row a gap)
  got gut 7.3. Jobs the owner wouldn't apply to: gut 0.5 under the points (0.1 for the others). Decision: fit
  stays a separate number beside the letter score, not blended (the letter score judges the writer, fit the
  matcher and the profile); the gut comparison is a sanity check, not a target to tune to. Untrue values come from meaning only (one
  untrue label in 17). The check half has been seen, so a re-measure there is weak evidence. Next: rewrite
  grading-standard-v2 §7/§9 as an issue list with low/high anchors (plus a stricter partial-row rule in §4.1),
  then code, then the pass bar on points before any judge run.
- 2026-11-04: **grading-standard-v2 §4.1 and §7 rewritten for the points scheme** (owner approved six decisions;
  §9's JSON, §8, §10, the header and §6's wording still to do). §7 is now an issue list: the judge finds problems and
  sets low / high by anchors, code sets missed-row and too-long levels and never shows the judge a point value.
  Decisions: (1) §4.1, every row: each word that narrows a requirement (named tool, scope, quality) must be answered
  by the quote, and a row asking for two things needs both. A partial row is addressed only when the quote names what
  the ad asks for and relates the evidence to it. Written from the six loose-quote rows of check-v1 (the judge noticed
  the missing part every time but counted "didn't overclaim" as addressed), so check-half results on it are weak
  evidence. (2) Mix-up low also covers a paragraph whose experiences don't serve its point, and an experience left and
  returned to within one paragraph (the guide's anchor matches). (3) Too long: the greeting, sign-off and ONE closing
  ask are not filler. (4) Untrue claims, missed rows, weak examples and mix-ups count per instance; weak examples and
  mix-ups charge only the two highest instances per letter (two highs = high + high). **Parked:** revisit the
  weak-example / mix-up low and high values and the cap for the best penalty formula, later. (5) Untrue high =
  fabricated, or a stretch / employer fact that is the ONLY support for a must-cover row's answer, the opening's claim
  of fit, or (employer fact) the reason for applying; anything else is low. (6) D3b is the "employer swap test", the
  weak-example rule the "example swap test". Also in §5: the listed-skill wording rule. Labels amended for per-instance
  counting (letters 3, 13, 16 split into instances, presence `owner`): points check mean gap 0.78 -> 0.81, r 0.46 ->
  0.45.
  - Same day, the rest of the standard: §9 is the new answer shape (`standard` "v2-points"; untrue claims carry a
    `level`; `problems` holds weak_examples / mix_ups lists, generic and no_personal_reason levels, and filler quotes;
    no grade, edits or ticks; an unaddressed row quotes the closest words with what's missing). §8's pairwise order is
    untrue, then missed rows, then the other problems, then the checklist. §10's examples are the owner's issue labels
    with a worked reading (which anchor, which test). §6 keeps all 21 items for now (D2d/D2e and D4a/D4b feed §7; the
    rest are report-only; cutting any is a later decision).
  - **Stage 8 restarts for points on the same split** (owner): calibrate on the 8, measure the 9 once, freeze judge v1
    (points), judge all 17. The check half isn't clean (check-v1 was read; §4.1 came from its rows; the calibration
    half has only 3-4 missed rows, all caught before, so it can't test §4.1), so its missed-row result is logged as
    weak and §4.1 is confirmed later by a rows-only owner spot-check on 5-6 letters from the first run after the
    freeze. Untrue has 1 low label in 17, so the planted-claim probes are its test. Next: code (answer schema +
    validation, scorer, A/B merge of issue lists, agreement per metric against labels.json, NOT grades-owner.csv,
    which misses `seek-95260505` R4), then the pass bar on points, then the worked examples.
  - **Code built (no LLM).** `scripts/letter_points.py` is the one scorer (the guide's values, cap 2 on weak examples
    and mix-ups, the code-set levels: stock 9-11 low / 12+ high, filler 1-2 low / 3+ high, over 340 words high). Both
    the owner's labels and the judge's answers go through it. `grading_panel.py` checks the §9 shape (an answer
    file without `"standard": "v2-points"` is refused). It checks quotes: a missing quote turns a Y row into N, and
    drops a claim, weak example, mix-up piece or filler sentence. It builds `letter_issues` (the judge's problems,
    plus missed rows with the answer key's level, too long from the filler count, and stock from
    `letter_metrics.stock_phrases` counted over the whole run, matching the labels' 17 hit counts exactly). The merged
    `judge-<tag>.json` holds `points`, `issues` and `by_metric`. `v2-agreement` reads labels.json.
    **A/B merge (Claude's design):** problem cells compare on what they cost, not their wording: untrue levels,
    weak-example and mix-up levels of the two charged instances, the generic and no-personal-reason levels, and the
    filler count's level. The adjudicator replaces a split list whole. Probes: a pad passes when the copy scores no
    more points than its original; a plant passes when it is labelled untrue. The worked examples are in a new file,
    `calibration/examples-points.md` (the 1-4 `examples.md` is kept). 2014 tests.
  - **Pass bar on points, fixed before any judge run (owner, 2026-11-04).** Written for 9 letters and scaled:
    1. points within ±1 of the owner's on at least n-1;
    2. a mean gap below the constant guess's (always the calibration half's mean owner points, 7.46, which gets a
       0.72 gap and ±1 on 7/9 on the check half, so (1) alone proves nothing);
    3. at least 80% of the owner's missed rows caught;
    4. every owner untrue flag caught, plus every planted claim in the probes.

    Weak example, mix-up, generic, no personal reason and too long are reported per letter, not gated (8-9 letters
    is too few). The correlation is reported only.
  - **Rules from the worked readings (owner, 2026-11-04):**
    - A link or signpost is never filler, however thin (a thin link is generic's problem): §7.8. Too long relabelled
      to none on `seek-95085104` (was low) and `seek-95140202` (was high): no filler sentence under the rule; points
      check 0.82, r 0.40.
    - In a partial row, naming the backed part in the ad's own words with its evidence addresses it; the two-part
      rule is for fully backed rows: §4.1. This matches the labels on `seek-95110025` R3 and `seek-95220494` R3.
    - When a letter gives more than one reason, the best one sets the no-personal-reason level: §7.7. This matches
      the `seek-95260505` label.
  - **Run `pts-cal-1`** (grader A, calibration half, examples leave-one-out, CLI 2.1.317, about 2 min per judge):
    **FAIL**. Within ±1 5/8 (bar 7), mean gap 1.39 (the guess 0.87), missed rows 3/4, untrue 1/1, r 0.60. Two letters
    held most of the gap (`seek-95085104` -3.3, `seek-95140202` -4.0). The causes:
    - mix-ups: every return of an experience was read as a split (D2e as written);
    - trait-list rows: every trait was asked for;
    - the "only support" untrue anchor was applied to real work in the wrong place;
    - one stretch the owner hadn't labelled;
    - `seek-95260505` R4: CI/CD was taken as automation.

    (A first start was stopped before any answer was saved, because the standard was edited mid-run; each judge
    reads the standard when its prompt is built.) **Owner decisions:**
    - Mix-up: using an experience again for a different requirement, telling a different part of it, is fine; D2e
      and §7.5 reworded. `seek-95110025`'s second mix-up and `seek-94932262`'s mix-up relabelled to none.
    - A trait-list row is addressed when one trait is named or shown (§4.1).
    - Real work in the wrong place is untrue low (§7.2).
    - The `seek-95140202` flag is accepted (untrue low, presence `judge-accepted`).

    Claude added §4.1's neighbouring-activity bullet from the owner's R4 label. Points check after the relabels:
    0.79, r 0.44.
  - **Run `pts-cal-2`** (same setup; standard `a1b0d9de`, examples `f78725e7`): **PASS** on the calibration half.
    | Measure | Result | Bar |
    |---|---|---|
    | Within ±1 | 7/8 | 7 |
    | Mean gap | 0.60 | the guess's 0.96 |
    | Missed rows | 4/4, no judge-only | 80% |
    | Untrue | 2/2, TNR 6/6 | all |
    | r | 0.90 | reported |

    Still apart, and only on ungated problems:
    - `seek-95085104` (-1.8): the judge adds a weak-example high (the student team, not the internship, for R4's MVP)
      and a second mix-up;
    - `seek-95191929` (+1.0): the judge misses the owner's weak-example high (the retail job alone for changing priorities);
    - `seek-95260505`: the judge finds no filler where the owner has one sentence.

    In-sample, so expected. The instructions are frozen here: the probes and the check half run on this exact judge
    version.
  - **Probes `probe-pts-1`** (copies of calibration letters; spec in gitignored `calibration/probes-pts-1.json`):
    - six plants: a gap claimed as had on a must-cover row, a gap claimed in a procurement ad, an inflated role
      (intern -> team lead), an invented result (3,500 users), an employer fact not in the ad (an award), and an
      invented certification;
    - two pads: two filler sentences each, staying under 340 words.

    A pad must score no more than its `pts-cal-2` original; a plant must be labelled untrue.
    **Result: all 8 pass** (panel `pts-probe-1`, the same judge version):
    - the five fabrications were all rated high;
    - the award was rated employer fact, low (an add-on to an existing reason, so §7.2's low is right);
    - both padded copies scored 0.5 below their originals, with the padding listed as filler.
  - **Check half, once: panel `pts-check-1`** (9 letters, graders A + B, then the adjudicator, then
    `v2-agreement --half check`; the same judge version as `pts-cal-2` and the probes). The run paused at the 5-hour
    subscription limit and was resumed after the reset with the version unchanged. A/B agreed on untrue, weak
    examples, mix-ups and no personal reason 9/9, rows 103/105, generic 7/9 and filler 8/9; 8 letters went to the
    adjudicator, mostly over checklist items. **Result: FAIL.**
    | Measure | Result | Bar |
    |---|---|---|
    | Within ±1 | 6/9 | 8; the guess gets 7/9 |
    | Mean gap | 0.84 | the guess's 0.76 |
    | Missed rows | 8/9, +2 judge-only | 80%: **pass** (check-v1 had 4/10) |
    | Untrue | no owner flags | the probes passed |
    | r | 0.62 | reported |

    The judge is harsher than the owner (mean 7.17 vs 7.74). The gap comes from:
    - judge-only generic lows on 3 letters;
    - judge-only untrue lows on 3 letters, two of them the `seek-95205206` flags the owner already rejected on
      2026-11-03;
    - 2 judge-only missed rows;
    - `seek-95364016` (-2.0: a stretch plus a weak example rated high where the owner has low).

    Not frozen. The owner reviews the judge-only flags and rows (label corrections are allowed; rule changes on the
    check half are not) and decides what comes next.
  - **Owner review of `pts-check-1`** (2026-11-04/05; working doc gitignored `calibration/review-pts-check-1.md`).
    It covered 20 items: every point where the owner's labels and the judge differed. Each item was decided from the
    frozen standard `a1b0d9de` as Change (take the judge's reading), Keep (the judge counts as wrong) or Rule question
    (the label stays, the judge counts as wrong, the rule goes on the next-version list). A reason accepted on one
    letter applies to every letter, calibration included. Each change is in gitignored `labels.json` with the owner's
    reason and its item number; the 2026-11-03 notes it overturns are marked superseded.
    - **Change (15):**
      - Untrue, stretch low, on `seek-95158567`, `seek-95205206` and `seek-95364016`. A summary or process line that
        the profile entry doesn't show is a stretch, even when it's true (§1, §5). This overturns the 2026-11-03
        rejections. The profile fix for `seek-95364016` (the thesis entry) waits until the profile is unfrozen.
      - Missed rows:
        - `seek-94890841` R10 is now missed: a neighbouring activity doesn't answer a row.
        - `seek-95084704` R2 is now missed: a partial row needs its evidence pointed at it.
        - `seek-94932262` R5 is now addressed, by the 2026-11-04 partial-row rule.
      - Weak examples:
        - `seek-95084704` loses its low: the second line shows a different part, so the delete test fails.
        - `seek-95364016` is split per point, high plus low: one sentence was an add-on for one point and the only
          example for another.
      - Mix-ups:
        - `seek-94890841` gains a low: a repeat that adds nothing.
        - `seek-95219353` gains a low: a return to an experience with new evidence.
        - `seek-95256463` loses its low: each line serves the paragraph's point.
      - Generic, low, on `seek-94932262` and `seek-95084704`: the links copy the ad's duty lines.
      - No personal reason, low, on `seek-95084704`: the ad restated, which is D4b's named N case.
      - Too long: `seek-95205206` loses its low; waffly isn't filler under §7.8.
    - **Keep (4):**
      - `seek-95205206` untrue: a loose paraphrase of the ad's role names, so clean.
      - `seek-95205206` weak example, high: the retail job alone carries customer needs, while the internship's
        client work never appears. The judge doesn't run the swap test when the stronger example is absent from the
        letter. It missed this the same way on `seek-95191929` in calibration.
      - `seek-95084704` mix-up, low.
      - `seek-94890841` generic, none: the links name work only this employer has.
    - **Rule question (1):** on `seek-95084704`, a sentence that answers an instruction in the ad isn't filler.
    - **Calibration consistency (2026-11-05):**
      - `seek-95085104` generic goes from none to low: item 17's pattern, duty-line links.
      - `seek-95110025` stays none: item 15's pattern, two links name this employer's own product.
      - Everything else already matched.
      - `pts-cal-2` re-scored: still PASS, within ±1 7/8, mean gap 0.60 -> 0.56 (the guess's 1.01).
    - **Result, re-scored with no judge runs: `pts-check-1` PASS.**
      | Measure | Before | After | Bar |
      |---|---|---|---|
      | Within ±1 | 6/9 | 9/9 | 8 |
      | Mean gap | 0.84 | 0.03 | the guess's (0.76 before, 0.79 after) |
      | Missed rows | 8/9 | 10/10, no judge-only | 80% |
      | Untrue | no owner flags | 3/3, TNR 6/6 | all |
    - **What this pass means.** 15 of the 20 items moved the owner's label to the judge's reading once the rule was
      pointed at. So the pass shows that the judge applies the written standard the way the owner reads it. It
      doesn't show that the judge matches the owner's first labels: that was 6/9. Fresh letters from the test split
      are the real test.
    - **Same points, different problems:** on two letters the totals match only because two different problems
      cost the same.
      - `seek-95084704`: the owner charges a mix-up low, the judge a filler low.
      - `seek-95205206`: the owner charges a weak-example high, the judge a second untrue low.

      Four Keeps and the rule question also stay as real disagreements under matching points.
    - **Reporting gap:** `v2-agreement` checks untrue per letter. A judge-only flag on a letter the owner also
      flagged doesn't show (`seek-95205206`). A per-flag check would catch it.
    - **Next-version list:** the rule questions are in `future_work/grading-standard-next-version.md`. They can
      only be tested on fresh letters. Next step (owner decision): freeze judge v1 and judge all 17.

## Follow-ups (not for this eval)

- From stage 7 (2026-10-28), not fixed yet:
  - DONE 2026-10-28, superseded 2026-10-29: the tick label "no real reason for wanting this job"
    read as "would I want this job"; it was reworded, then the tick itself became
    `no_personal_reason` (D4b; stage 7 entry).
  - DONE 2026-10-28: `report`'s fabrication-free row ignored the owner's `untrue`; it now adds
    "owner: n of N with nothing untrue" (`scorecard` `owner_checked` / `owner_true`).
  - check_claims passed the one owner-flagged untrue letter: a skill put inside an experience that
    didn't use it, across sentences (see the stage 7 entry). It checks claims one at a time.
  - `app/llm/client.py` sets no request timeout; a Gemini call hung for hours in stage 6.
  - The writer builds every paragraph the same way (a profile fact, often pasted word for word,
    then a stock bridge sentence) and restates the ad's blurb as the candidate's motivation; the
    writing sample's "I am drawn to" / "exactly the kind of" end up in 8-9 of 17 letters. Writer
    v3's "apply each piece of evidence" rule invites the bridge. Fix with the next wording
    go-ahead (owner: wording is parked); C5b and C5c give it numbers to beat.
  - `match_profile` rates years / seniority asks ("3+ years experience") as partial from an
    internship, which makes them must-cover; the owner's key rates them gap. The run then revises
    for a row it can never honestly meet (both not-clean runs in stage 6). Rate them against
    relevant paid work, as the key does.

- From the labels (2026-10-23), match_profile verdicts the owner disagreed with:
  - Years and seniority asks ("5+ years", "8+ years", "senior commercial") rated **partial**
    against an internship (#69, #72, #103, #162, #193). They read as gaps.
  - Non-technical customer service used as evidence for **technical / IT support** (#11, #186).
  - A duty of the job ("interrogate the employer's live pricing systems", #149) tagged as an
    essential the candidate is missing. The owner kept the tag, but duties in an ad's
    "accountabilities" list are not skills a candidate can bring.
  - The frozen profile undersells real skills in places (owner's comments in the sheet). Not for
    this eval: the profile stays frozen; update it after the baseline. One more from stage 8
    (2026-10-29): AI-assisted development is used in every project, not only the internship.

- The scorer bunches on a few values (of 112 usable ads, 27 at exactly 35 and 15 at exactly 60),
  which leaves the middle bands coarse. Changing it would move every score mid-build, and the
  scorer isn't what this eval measures; look at it after the baseline.
