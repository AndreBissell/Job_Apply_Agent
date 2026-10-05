# Blind Opus grading, panel 4: workflow-v2 vs workflow-v3

2026-10-04. workflow-v3 changes only the shared writer prompt (`generate.py`'s
`_WRITER_RULES`, one line in `revise.py`'s task, `SKILL.md`'s shape): build each body
paragraph around a piece of the employer's work from the ad, pick one or two profile
details instead of retelling the entry, add a connecting sentence that adds no new
facts, no stock bridges ("translates well", "carries over directly"), no "I am applying
for" opening, and close on a specific thing in the role instead of a stock close.
`style_lint` gained `stock_close` / `stock_bridge` warnings (warnings only). Same 15
ads, same engine (workflow), same guardrails and checks. A fresh blind Opus panel (2
graders + adjudicator, `evals/grading-standard.md` unchanged, procedure in
`evals/grading-panel.md`) graded the 30 letters.

## Panel reliability

| | |
|---|---|
| Grader A vs B, cells agreeing | 118/120 (2 adjudicated, both N: one generic-mission specific_detail, one would_send on three machine-written tells) |
| **Panel 4 vs panel 3 on workflow-v2** (same 15 letters) | **57/60** (musts 15/15, claims 13/15, detail 14/15, would_send 15/15) |

## Results (panel 4, 15 letters each)

| Item | workflow-v3 | workflow-v2 |
|---|---|---|
| supported_musts_covered | 15/15 | 15/15 |
| no_unsupported_claims | 12/15 | 11/15 |
| specific_detail | 13/15 | 13/15 |
| would_send | **0/15** | 0/15 |
| All four | 0/15 | 0/15 |

**No letter flipped would_send.** The specific_detail fails are the same 2 ads for
both versions (an unnamed employer with only a mission line, and a company tagline):
an ad problem.

## What changed, measured in code (free, `lint()` + an 8-gram copy count vs the profile text)

| | workflow-v3 | workflow-v2 |
|---|---|---|
| Letters ending on a stock close | **0/15** | 13/15 |
| Stock bridges ("translates well", "maps directly"...) | **0** | 11 |
| Body words in 8-word runs copied verbatim from the profile | **18%** | 36% |
| Avg words | 313 | 306 |

## Why every v3 letter still fails would_send (panel reasons)

The targeted habits are gone, and the panel no longer cites stock closes or bridges.
In their place the writer formed **new formulas**, which the standard's "stock phrasing in
more than one place / same awkward phrase repeated / three machine-written tells"
triggers catch:

- "prepares me to / preparing me to / equips me to" capability tails, often twice
  (cited on 9 letters): the connecting sentence the prompt asked for, written as a
  template.
- "exactly the kind of ... I want" (cited on 8 letters).
- Restating openers: "The role involves...", "Your team relies on..." (5 letters).
- Still some recital: a thesis or internship paragraph applied only in its last sentence,
  and bare skill lists (2 letters).
- Gap-led "While my X rather than Y" sentences came back on 2 letters (v2: 2, different
  ones); `style_lint`'s gap-led pattern misses the "rather than" form.

Two letters were close: grader B passed one (adjudicated N for three tells), and both
graders called another "closest to sendable" and failed it only on a repeated stock
phrase. Under v2, no letter was described that way.

## Claims (the risk of "apply it to the employer")

Not worse: 12 vs 11. But the v3 fails are different in kind:
- an invented action: "I have included a link to a brief two-minute video" (the ad
  asked for one; the writer said it was attached),
- an invented testing activity and an output-quality outcome,
- a degree discipline the profile doesn't state.
`check_claims` passed all three. Its stage-1 code check blocks invented URLs and emails,
not the claim that an attachment or link is included. First drafts that `check_claims`
blocked: 4/15 (v2 3/15), all fixed by a revision.

## Cost (`cost-workflow-v2-vs-workflow-v3.md`)

| | workflow-v3 | workflow-v2 |
|---|---|---|
| Avg cost / letter | $0.223 ($0.213 without the one-off `analyze_job` re-run) | $0.184 |
| Writer thinking tokens / first draft | 8,556 | 7,367 |
| Revisions | 7 | 6 |
| Avg time / letter | 171s | 145s |
| Clean (all 3 checks pass) | 15/15 | 15/15 |

## Reading

The prompt change did what it targeted (closes, bridges, verbatim recital halved) but
not what the panel grades: the very strict would_send bar now fails on the writer's
replacement formulas. Like for like it costs about 16% more per letter. It is kept as
the writer (it is no worse on any panel item and better on the measured habits); the
remaining wording work is deferred by the user's decision (2026-10-04: wording polish
later). Follow-ups: catch "I have included / attached" claims in code, extend the
gap-led pattern to "rather than", and treat repeated capability tails as a lint warning.
