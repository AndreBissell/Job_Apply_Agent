# Points scheme: severity guide (dev-agent-v1 labels)

Scrubbed copy of the gitignored evals/work/v2/points-draft/guide.md (no employer names from the work history, no
letter quotes). It holds the rules behind evals/results/v2/points-labels-dev-agent-v1.json; with that file and the
frozen eval set it is enough to rebuild the points judge. Letter numbers are sheet S1674 order.

Each problem is low or high. Point values were set after the labelling pass (see the end). Fit is a separate score.

| Metric | Who decides presence / severity | Low | High |
|---|---|---|---|
| Untrue | judge-owner / owner | every other stretch or employer fact not in the ad (a side sentence, beside true evidence, or a row that isn't must-cover) | fabricated, or a stretch / employer fact that is the ONLY support for a must-cover row's answer, the opening's main claim of fit, or (employer fact) the reason for applying |
| Missed row | owner / code | an important row | a must-cover row (from the answer key, no override) |
| Weak example | owner / owner | the best example is used, then a lower-rung one is added for the same point that adds nothing (delete test) | the point's main example is the weaker one when a higher rung shows the same thing (example swap test) |
| Generic (body) | owner / owner | the body ties to this employer's work in places, but thinly | the body would fit any employer with this title |
| No personal reason | owner / owner | the reason is specific to the ad, but not tied to the profile | the reason would fit any letter, OR it doesn't hold up when read (the ad fact isn't something a candidate could want, or the "because" doesn't follow) |
| Too long | owner / owner | 1-2 filler sentences (a sentence that adds no evidence, employer fact, reason or link; a link or signpost is never filler, however thin; the greeting, sign-off and ONE closing ask for an interview are not filler) | 3+ filler sentences, or over 340 words |
| Mix-up | owner / owner | one experience split across two paragraphs, or one paragraph holding experiences that don't serve its point, or an experience told, left for another and returned to within one paragraph (the reader can still follow) | the reader can't follow which experience shows what |
| Stock phrasing | code / code | 9-11 hits | 12+ hits |
| Fit (separate score) | code | important + partial = low; important + gap and essential + partial = medium; essential + gap = high | |

Rules:
- A wrong example (doesn't show the point) is not a weak example: it is a missed row (the profile backs the
  row elsewhere) or untrue (the letter presents it as if it showed the point).
- A thin extra example line counts under weak example only, never also as filler.
- Weak example, example swap test (owner, backup-flag review 2026-11-03): the weaker example alone carrying a point is high
  even when its wording matches the ad (the retail job named for the ad's 'changing priorities') and even when the
  stronger one is left out of the letter entirely (the thesis, for an ad row on understanding what AI does). The retail
  job AFTER the internship for the same point stays low (delete test). Letters 2, 10, 11, 15 are high; 3, 7 low.
- Untrue, a listed skill next to an experience (owner, 2026-11-03): the wording decides. Named as a skill the candidate
  has beside the experience ('... supported by my skills in <skill>', letter 11) = clean. Worded so it sounds used in
  an experience whose entry doesn't have it (a cloud platform from a uni project described as part of the internship's
  work, letter 9) = untrue (stretch).
- Counting (owner, 2026-11-03, standard rewrite): untrue claims, missed rows, weak examples and mix-ups count per
  instance (two thin add-on lines = two weak examples). Weak examples and mix-ups are capped per letter: only the
  two highest instances are charged (two highs = high + high). The low / high values and the cap are to be revisited
  for the best penalty formula (owner, 2026-11-04). Untrue
  claims and missed rows have no cap. Generic, no personal reason and too long are one per letter. Letters 3, 13 and 16 were relabelled into instances for it (2026-11-04).
- A row that asks for two things needs both; answering one part leaves it missed (owner, 2026-11-03) This is for rows the profile backs in full.
- A partial row (owner, 2026-11-04): naming the part the profile backs in the ad's own words, with its
  evidence, addresses it; the unbacked part needn't be named (claiming it would be a stretch). Letters 3
  (R3) and 5 (R3) were labelled this way.
- Filler (owner, 2026-11-04): a sentence that links the evidence to the ad's work, or names the requirement a
  paragraph answers, is never filler, however thin (a thin link is generic's problem); long sentences are not
  filler. Too long on letters 9 (was low) and 10 (was high) relabelled to none: no filler sentence under this
  rule. Too long is now a count: the judge lists the filler sentences and code sets the level.
- Mix-up (owner, 2026-11-04, after calibration run pts-cal-1): using an experience again in a later paragraph, telling a
  different part of it for a different requirement, is not a mix-up; the same part told in two places is. Letter 3's
  second mix-up and letter 14's mix-up relabelled to none.
- Trait rows (owner, 2026-11-04): a row listing several traits or soft skills is addressed when one of them is named or
  shown with evidence; the two-part rule is for hard skills and activities.
- Untrue (owner, 2026-11-04): real work put in the wrong experience is low, even on a must-cover row; the "only
  support" high is for stretches that inflate what was done. Letter 10 gained an untrue low: the judge's flag (the
  internship's tools told unnamed in the thesis paragraph), accepted (presence `judge-accepted`).
- A neighbouring activity is not the one asked for (letter 1, R4: delivering code through a CI/CD pipeline doesn't
  show building automations).
- Structure on its own (repeated openings, the order of points, a missing greeting) costs no points (owner, 2026-11-03).
- Generic is about the body; the reason-for-applying sentence is judged only under no personal reason.
- Generic = does the body link the experience directly to THIS job's work (owner, 2026-11-03)? It is not
  about wording: AI-sounding or stock phrases count only under stock phrasing (code). None = direct, specific
  links; low = links in places, or thin ones; high = no real links, or links so vague they fit any job
  ("support your team", "assist with the daily tasks of your department").
- Gut score (out of 10) is given before the severities, from how the letter reads, fit left out.
- Origins: `owner-s7` (stage 7 label), `owner` (set in this pass), `code`, `claude-flag` (owner changed
  after Claude's backup flag), `judge-accepted` (a judge flag the owner accepted).

## Point values (owner, 2026-11-03; set after the labels were frozen)

Start at 10, deduct per problem, floor 0, no cap on the lows (a cap matched the gut scores worse). Fit is not deducted.

| Metric | Low | High |
|---|---|---|
| Untrue | -1.0 | -2.5 |
| Missed row | -0.75 | -1.5 |
| Mix-up | -0.5 | -1.5 |
| Weak example, no personal reason, too long | -0.5 | -1.0 |
| Generic | -0.3 | -0.6 |
| Stock phrasing (code) | -0.25 | -0.5 |

Why (owner): missed rows matter most to a recruiter's screen; a bad mix-up is very disruptive and leads to
stretches; generic is partly charged already by stock phrasing and no personal reason; stock phrasing is a
find-and-replace fix.

Check against the owner's gut scores (17 dev letters): mean gap 0.78, correlation 0.46 (0.81, 0.45 after the 2026-11-04 per-instance relabel; 0.82, 0.40 after the too-long relabel; 0.79, 0.44 after the pts-cal-1 relabels), points 5.5-9.2 vs gut
5.6-8.4. The equal -0.5 / -1.0 draft matched better (0.69, 0.52); the owner chose these from meaning. A gut read can't
see a missed row without checking the ad, so the gut check underweights them. Untrue values are set from meaning only: one letter has an untrue label (low).
