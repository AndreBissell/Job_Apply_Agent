# Blind Opus grading, panel 2: workflow-v1 vs workflow-v2

2026-10-03. The listed-skill fix (commit 907fe81) re-run as `workflow-v2` on the same
15 ads, then a fresh blind Opus panel (2 graders + adjudicator, same
`evals/grading-standard.md`, same procedure as `grading-opus-v1.md`) graded
workflow-v1 and workflow-v2 side by side: 30 letters, random ids, writers hidden.
This time each grader read the packet one job at a time, so every must-have list was
written before that job's letters were read.

**What changed between v1 and v2:** a must-have backed only by a skill listing is no
longer must-cover (it moves to may_use); the writer is told a listing backs "skills
in / knowledge of" and never experience, and not to lead with a gap; the claims judge
counts experience wording on a listed-only skill as overstated; style_lint warns on
gap-led sentences.

## Panel reliability

| | |
|---|---|
| Grader A vs B, cells agreeing | 111/120 (9 adjudicated, all N) |
| **Panel 2 vs panel 1 on workflow-v1** (same 15 letters, different sessions) | **57/60** (musts 15/15, claims 15/15, detail 13/15, would_send 14/15) |

The panel re-grades the same letters almost identically, so differences between v1
and v2 below are the letters, not grader noise.

## Results (panel 2, 15 letters each)

| Item | workflow-v2 | workflow-v1 |
|---|---|---|
| supported_musts_covered | 14/15 | 15/15 |
| no_unsupported_claims | **13/15** | 6/15 |
| specific_detail | 12/15 | 13/15 |
| would_send | 0/15 | 1/15 |
| All four | 0/15 | 1/15 |

Code side (`workflow-v2-loop.md`): 15/15 returned drafts pass all three checks (v1
14/15), 0/6 revisions dropped a must-cover item (v1 2/6), no run hit the cap,
$0.184/letter (v1 $0.194), 306 words on average. Gap-led sentences (style_lint's
detector): **0/15 letters** (v1 9/15).

## Reading

- **The fix did what it was for.** Unsupported claims fell from 9 letters to 2 (an invented
  "rigorous approach … scalable software" on the internship, and "a strong habit of
  integrating AI tools into a daily workflow" where the profile states no frequency), gap-led sentences went
  from 9 letters to 0, and revisions stopped dropping must-cover items.
- **`would_send` is still 0-1/15, now for one reason.** Every v2 fail is the
  candidate's "application" point: body paragraphs recite profile entries (the
  internship, the thesis) without saying how they apply to this employer's work, often
  with a generic close ("my background translates well to your team"). 3 also fail
  specific_detail. That is the next writer lever, not a claims or coverage problem.
- **Coverage 14/15:** one letter listed "knowledge of Java" without connecting the
  .NET work to it (the panel counts .NET as partial evidence for Java).
