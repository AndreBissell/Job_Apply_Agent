# Points scheme: severity guide (dev-agent-v1 labels)

Scrubbed copy of the gitignored evals/work/v2/points-draft/guide.md (no employer names from the work history, no
letter quotes). It holds the rules behind evals/results/v2/points-labels-dev-agent-v1.json; with that file and the
frozen eval set it is enough to rebuild the points judge. Letter numbers are sheet S1674 order.

Each problem is low or high. Point values are set AFTER the labelling pass. Fit is a separate score.

| Metric | Who decides presence / severity | Low | High |
|---|---|---|---|
| Untrue | judge-owner / owner | a stretch or an employer fact not in the ad, in a side sentence | fabricated, or a stretch the letter's main point rests on |
| Missed row | owner / code | an important row | a must-cover row (from the answer key, no override) |
| Weak example | owner / owner | the best example is used, then a lower-rung one is added for the same point that adds nothing (delete test) | the point's main example is the weaker one when a higher rung shows the same thing (swap test) |
| Generic (body) | owner / owner | the body ties to this employer's work in places, but thinly | the body would fit any employer with this title |
| No personal reason | owner / owner | the reason is specific to the ad, but not tied to the profile | the reason would fit any letter, OR it doesn't hold up when read (the ad fact isn't something a candidate could want, or the "because" doesn't follow) |
| Too long | owner / owner | 1-2 filler sentences (a sentence that adds no evidence, employer fact or reason) | 3+ filler sentences, or over 340 words |
| Mix-up | owner / owner | one experience split across two paragraphs | the reader can't follow which experience shows what |
| Stock phrasing | code / code | 9-11 hits | 12+ hits |
| Fit (separate score) | code | important + partial = low; important + gap and essential + partial = medium; essential + gap = high | |

Rules:
- A wrong example (doesn't show the point) is not a weak example: it is a missed row (the profile backs the
  row elsewhere) or untrue (the letter presents it as if it showed the point).
- A thin extra example line counts under weak example only, never also as filler.
- Weak example, swap test (owner, backup-flag review 2026-11-03): the weaker example alone carrying a point is high
  even when its wording matches the ad (the retail job named for the ad's 'changing priorities') and even when the
  stronger one is left out of the letter entirely (the thesis, for an ad row on understanding what AI does). The retail
  job AFTER the internship for the same point stays low (delete test). Letters 2, 10, 11, 15 are high; 3, 7 low.
- Untrue, a listed skill next to an experience (owner, 2026-11-03): the wording decides. Named as a skill the candidate
  has beside the experience ('... supported by my skills in <skill>', letter 11) = clean. Worded so it sounds used in
  an experience whose entry doesn't have it (a cloud platform from a uni project described as part of the internship's
  work, letter 9) = untrue (stretch).
- Structure on its own (repeated openings, the order of points, a missing greeting) costs no points (owner, 2026-11-03).
- Generic is about the body; the reason-for-applying sentence is judged only under no personal reason.
- Generic = does the body link the experience directly to THIS job's work (owner, 2026-11-03)? It is not
  about wording: AI-sounding or stock phrases count only under stock phrasing (code). None = direct, specific
  links; low = links in places, or thin ones; high = no real links, or links so vague they fit any job
  ("support your team", "assist with the daily tasks of your department").
- Gut score (out of 10) is given before the severities, from how the letter reads, fit left out.
- Origins: `owner-s7` (stage 7 label), `owner` (set in this pass), `code`, `claude-flag` (owner changed
  after Claude's backup flag).
