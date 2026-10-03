# Blind Opus grading, panel 3: workflow-v2 vs agent-v1

2026-10-03. Phase 7a: the agent (`app/llm/letter/agent.py`, a mid-tier orchestrator
choosing each tool) against the fixed workflow, on the same 15 ads, the same tools,
the same guardrails and the same writer. A fresh blind Opus panel (2 graders +
adjudicator, `evals/grading-standard.md` unchanged, procedure in `evals/grading-panel.md`)
graded the 30 letters with random ids and the engines hidden.

## Panel reliability

| | |
|---|---|
| Grader A vs B, cells agreeing | 118/120 (2 adjudicated, both N, both "daily" AI-tool-use frequency claims) |
| **Panel 3 vs panel 2 on workflow-v2** (same 15 letters, different sessions) | **57/60** (musts 14/15, claims 15/15, detail 13/15, would_send 15/15) |

## Results (panel 3, 15 letters each)

| Item | agent-v1 | workflow-v2 |
|---|---|---|
| supported_musts_covered | 15/15 | 15/15 |
| no_unsupported_claims | 12/15 | 13/15 |
| specific_detail | 12/15 | 12/15 |
| would_send | 0/15 | 0/15 |
| All four | 0/15 | 0/15 |

The specific_detail fails are the same 3 ads for both engines (generic mission or
boilerplate company text, or only restated duties): an ad problem, not an engine one.
The claim fails are writer wording, from the writer both engines share: a frequency the
profile doesn't state ("daily routine" / "daily workflow" of AI-tool use, one letter
per engine), "rigorous ... scalable software" on the internship (workflow-v2, the same
letter panel 2 failed), an invented purpose for the thesis evaluation (agent), and
"apply my skills in PHP" on a bare listing (agent). `check_claims` (mid) passed all of
them.

## Code side (`agent-v1-loop.md`, `cost-workflow-v2-vs-agent-v1.md`)

| | agent-v1 | workflow-v2 |
|---|---|---|
| Returned draft passes all 3 checks | 15/15 | 15/15 |
| ...on draft 1 | 10/15 | 9/15 |
| Same tool path as the workflow | **15/15** | n/a |
| Guardrail refusals | 0 | n/a |
| Runs that hit a limit | 0/15 | 0/15 |
| Revisions that dropped a must-cover item | 1/6 (restored on the next draft) | 0/6 |
| Avg cost / letter | $0.178 | $0.184 |
| Orchestrator cost / letter | $0.013 (8.6 calls, 7% of the run) | none |
| Avg time / letter | 182s | 145s |
| Orchestrator time / letter | 47s | none |

The agent's total cost came out lower only because the writer thought less in this
run (6,468 vs 7,367 thinking tokens per first draft), which is run-to-run noise in
the shared writer. Like for like, the agent costs about $0.013 and 37-47 s more per
letter.

## Reading

- **The agent chose exactly the workflow's path on every job**, with no refusals.
  The guardrails leave almost no real choices in letter writing (checks run once per
  draft, revise only after all three checks, one way to finish), and the system prompt
  describes that order, so a model choosing the steps reproduces the fixed sequence at
  extra cost and time.
- **Quality is the same within noise**: one item differs by one letter, and that
  difference is writer wording, not a step the agent took differently.
- **Recommendation: `letter_engine` defaults to `workflow`.** Keep the agent as an
  option; agency may pay off where there are real choices (side outputs, company
  research, which listings to apply for), not in a sequence the rules already fix.
- **The next lever is still the writer**, as panel 2 found: every would_send fail
  recites profile entries without applying them to the employer's work, often with a
  stock close. Two smaller follow-ups for the claims judge: frequency claims ("daily")
  and "apply my skills in X" on a listed-only skill both passed `check_claims`.
