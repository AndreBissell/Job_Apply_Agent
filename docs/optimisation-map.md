# Optimisation map: goals, issues, experiments

**What this is.** One page that links every known problem to the goals it hurts and the experiments
that might fix it. It is the index. The evidence and the full recipes stay in
[agent-optimisation-plan.md](agent-optimisation-plan.md) ("the plan"); each line here points to the
section that backs it.

**How to use it.** Pick an experiment from the register (§4), run it, then update its status and
result there. Add a new problem or idea as an issue card (§2) or an N-row (§4) first, so nothing is
run without a stated target.

Labels, the same as the plan: **[M]** measured, **[E]** arithmetic on measured numbers, **[G]** a
guess that an experiment should replace. Ads are named by key only; no letter or profile text here.

---

## 1. The three goals

Baseline: run `dev-agent-v1` (agent engine, 17 dev letters), judge v1 panel `v1-dev`.

| Goal | Baseline | Better means | Measured by |
|---|---|---|---|
| **Cost** | $0.258 a letter [M]; $0.317 at 2027 prices [E] | Lower $ a letter | `letter_lab.py cost-report` (from `llm_usage`) |
| **Score** | Judge 7.13 / 10 mean, 7.15 on the 10 relevant ads [M]; 2.87 points lost a letter | Higher mean points, no rise in untrue claims | `grading_panel.py v2-run <tag> --judge v1` |
| **Time** | 487 s a letter mean, 416 s median [M] | Lower seconds a letter | `cost-report`, run.json |

**Guards on every change:** untrue claims must not rise; the test split stays closed until the end;
a score change smaller than the noise threshold T (from E3's control run) is not a result.

**How the goals trade.** At 20 letters a month the whole cost is about $6 [E], so score comes first
and cost levers are taken when they are nearly free or ride on a judged run (plan §6). Time and cost
mostly move together, because 92% of the time is spent waiting on Gemini calls [M]. The exception is
Flex (E2): half the price, but slower.

---

## 2. Issues

Each card says which goals it hurts, what causes it, and which experiments target it. Goal tags:
**C** cost, **S** score, **T** time. A primary tag is in bold.

### At a glance

| # | Issue | Goals | Size | Experiments |
|---|---|---|---|---|
| I1 | Revisions | **C** **T** S | 31% of cost, ~1 to 2 min each | E1, N1, E7, N2 |
| I2 | Missed rows | **S** | 28% of points lost | E3 |
| I3 | Untrue claims | **S** | 16% of points lost | E6, E5 (R1) |
| I4 | Weak examples | **S** | 12% of points lost | E5 (R3) |
| I5 | No personal reason | **S** | 11% of points lost | E5 (R2), D-E |
| I6 | Mix-ups | **S** | 11% of points lost | E5 (R4) |
| I7 | Generic lines | **S** | 10% of points lost | E5 (R1) |
| I8 | Stock phrasing | **S** | 10% of points lost | E5 (R1) |
| I9 | Thinking tokens | **C** **T** | 70% of cost | E4, S-steps (§3) |
| I10 | Orchestrator overhead | **C** **T** | 6% of cost, 26% of time | D-A |
| I11 | Slow calls and long tails | **T** | 171 to 1,016 s a letter | D-A, E1, N3, N4 |
| I12 | Price and model risk | **C** | Flash doubles 2027-01-01; Pro is a preview | E2, E4 arm 2 |
| I0 | Measurement noise | blocks all S work | ±0.3 judge-only; writer noise unknown | E3 control, T1 |

### I1. Revisions — C, T, (S)

- **Size.** 12 revisions over 17 letters, 31% of the cost [M]. One revision with its re-checks is
  about $0.13 and about 2 minutes (2.5 with the agent) [E: revise 76 s + check_claims 44 s +
  check_requirements 8 s, plus orchestrator turns].
- **Causes** (plan §2.3, [M]):
  - 5 of 12: a row the answer key calls a **gap** was labelled partial by `match_profile`, so the
    plan required it, the writer rightly did not claim it, and the check failed. Wasted.
  - 3 of 12: a real missing must-cover row. Useful.
  - 2 of 12: an overstated claim, fixed by changing 2% of the words. Useful, but a small edit.
  - 2 of 12: over 340 words only. 4 drafts went over 340 in all; the prompt asks for 250 to 300
    and letters average 319 [M].
  - All 12 re-send the whole context to the strongest model at high thinking.
- **Not a cause:** "too much thinking causes hallucinations" has no support in this data; the
  untrue claims trace to linking sentences (I3). [G] at most.
- **Score side.** Revising does not reliably help the judge score: the checks a revision satisfies
  are not what the judge deducts for (plan §2.2, §4). Do not count fewer revisions as a score loss.
- **Experiments:** E1 (stop false gap triggers), N1 (stop length overshoot), E7 (cheaper edits),
  N2 (smaller revision context). Fallback if E1 fails: a partial row that fails twice stops
  blocking (code only, plan §9.1).

### I2. Missed rows — S

- **Size.** 15 rows on 8 letters, 13.5 points, 28% of all points lost; 34% on relevant ads [M].
- **Cause.** The plan is narrower than the standard. Important backed "mention" rows are "may use
  if it fits naturally" to the harness and chargeable to the judge. 13 of 15 misses were may-use
  rows; the owner's labels agree (12 of 14) [M] (plan §3.1).
- **Note.** `check_requirements` already reads the may-use rows and reports which are covered; it
  only blocks on must-cover rows [M, code]. So E3 is mostly a blocking-rule and prompt change.
- **Experiment:** E3 ("should address" tier). Upper bound +0.62 points a letter [E].

### I3. Untrue claims — S

- **Size.** 8 instances on 6 letters, 8 points, 16% [M]. All low-severity stretches; all passed
  `check_claims` (plan §3.2).
- **Causes.** Interpretive linking sentences ("what an experience gave me") that the writer does not
  list as claims (5 of 8 were not in its list) and the checker does not treat as claims [M].
- **Experiments:** E6 (checker recall), E5 R1 (fewer linking sentences to stretch in).

### I4 to I8. Writing problems — S

| Issue | Points lost | Cause | Rule |
|---|---|---|---|
| I4 Weak example | 6.0 (12%) | `match_profile` hands the writer 2 to 3 unranked evidence pointers per row; the writer pads with the weaker ones | R3: one example per point, the strongest first |
| I5 No personal reason | 5.5 (11%) | Opening rule; the profile has no "what I want" field | R2: start from what the candidate did or studied; D-E: a goals field (later) |
| I6 Mix-up | 5.5 (11%) | Paragraphs built around the ad's topics split one experience across paragraphs | R4: one experience per paragraph |
| I7 Generic | 5.1 (10%) | The required "connecting sentence" after each piece of evidence | R1: the link goes inside the evidence sentence |
| I8 Stock phrasing | 4.75 (10%) | The prompt's own wording and required slots ("prepares", "carries over to") | R1, and remove example phrasings |

All [M] (plan §3.3, §8). Experiment: E5, each rule screened alone on code measures, then the kept
rules judged together. No banned-phrase list: it would overfit (plan §8).

### I9. Thinking tokens — C, T

- **Size.** 70% of the cost; the writer's thinking alone 59% ($0.153 a letter) [M].
- **What we know.** On 17 letters, all at `high`, letters that thought more did not score higher
  (correlation -0.26) [M]. This says nothing about `medium`; it has never been tried.
- **Experiments:** E4 (writer), and a thinking sweep per step where a cheap measure exists (§3).

### I10. Orchestrator overhead — C, T

- **Size.** 10 calls a letter, $0.016 (6%), 128 s (26%) [M].
- **Cause.** The guardrails leave one sensible move; the agent picked the workflow's order on 46 of
  47 letters across three runs, 0 refusals [M].
- **Decision:** D-A (workflow as default). The owner chose the agent for experience on 2026-10-03.

### I11. Slow calls and long tails — T

- **Size.** 171 to 1,016 s a letter; one `generate_letter` call took 926 s; no request timeout in
  `client.py` [M].
- **Causes.** Orchestrator turns (I10), revision rounds (I1), `check_claims` writing out every
  claim with its evidence (1,656 output tokens, 44 s a call) [M].
- **Experiments:** D-A, E1, N3 (shorter checker output), N4 (request timeout, part of E2).

### I12. Price and model risk — C

- Flash prices double on 2027-01-01; the writer is a preview model that can be retired on 2 weeks'
  notice (plan §5).
- **Experiments:** E2 (Flex, half price), E4 arm 2 (a tested Flash writer as fallback).

### I0. Measurement noise — blocks every score experiment

- Re-judging the same 17 letters moved the mean by 0.19 and single letters by up to 1.5 [M]. Writer
  noise (temperature 1.0, Google's advice for Gemini 3) is not yet measured.
- **Fix:** E3's control arm gives T, the smallest dev-mean change that counts. T1 (the comparison
  tool) reports every run against it.
- **Rule:** if a step has an answer key or a labelled set, measure it against that, not the judge.

---

## 3. Every LLM call: model, thinking, context

Per letter, averaged over the 17 baseline letters [M: cost table, usage, code]. "Context" is what
the call is sent. "Cheap measure" is how to test a change to this step without a judge run.

| Step | Model, thinking | $ / letter (share) | s / letter | Tokens per call: in / out / thinking | Context it gets | Cheap measure |
|---|---|---|---|---|---|---|
| `generate_letter` | 3.1 Pro, high | $0.118 (46%) | 143 | 6,829 / 999 / 7,747 | Writer rules + style guide + writing sample; the whole profile; the whole ad; the ad analysis; the letter plan with evidence | **None.** Judge only. Code screens (words, stock hits, C4 repeats, gates) for a first cut |
| `revise_letter` | 3.1 Pro, high | $0.080 (31%) | 53 | 7,788 / 1,085 / 7,056 | Everything `generate` gets, plus the failed checks and the current draft; returns the whole letter and all claims | Replay the 7 real revisions; code checks (E7) |
| `check_claims` | 3.8 Flash, medium | $0.031 (12%) | 73 | 4,423 / 1,656 / 2,423 | The whole profile; the whole ad; the letter; the writer's claims list. Writes evidence and reasoning before each verdict, for every claim | The judge's 8 untrue flags and 11 clean letters (labelled set) |
| `orchestrate` | 3.8 Flash, medium | $0.016 (6%) | 128 | 1,546 / 9 / 105 | State summary, steps so far, tool menu | Same path as the workflow |
| `match_profile` | 3.8 Flash, medium | $0.012 (5%) | 40 | 2,487 / 759 / 2,026 | Requirement rows; the whole profile; profile facts | **Answer key** (G / U / S counts) |
| `check_requirements` | 3.1 Flash-Lite, low | $0.002 (1%) | 12 | 926 / 450 / 181 | Must-cover and may-use rows; the letter | Answer key + judge's missed rows |
| `analyze_job` | 3.8 Flash, medium | $0.010 a new ad (cached) | — | — | The ad | None: the key shares its rows |
| `style_lint` | code | $0 | 0 | — | The letter | — |

**Things the table shows:**

- The two writer calls are 77% of the cost, and they are the only steps with no cheap measure. So
  every writer change needs a judged run, and they get few, well-chosen experiments.
- The four steps below them have cheap measures, so a thinking sweep there costs cents.
- A step's true cost includes what it causes downstream. `match_profile` is 5% of the cost but
  triggered about 15% (I1). Making it cheaper and worse would raise the total.
- The writer gets the whole profile **and** the plan's evidence, so it sees the evidence twice
  and every unused profile row too. Narrowing that is a quality idea (R3), not a cost one: input is
  11% of the writer's cost [M].

### Per-step sweeps (S-rows in the register)

Priority = share of cost, then how cheap the measure is.

| ID | Step | Try | Measure | Notes |
|---|---|---|---|---|
| S1 | `generate_letter` | `medium`; Flash at `high` | Judge, paired, against T | This is E4 |
| S2 | `revise_letter` | Flash at `medium` / `high`; a separate trim call | Replayed revisions, code checks | This is E7 |
| S3 | `check_claims` | `low` / `high`, after E6 | Labelled set: flags caught, false alarms | The plan rejects going lower (§11): run only to confirm |
| S4 | `match_profile` | `low` / `high` | Answer key | `high` is E1 arm B; `low` only for the curve |
| S5 | `orchestrate` | `low` | Same path as workflow | Only if D-A keeps the agent |
| S6 | `check_requirements` | — | — | 1% of cost and accurate: leave |

---

## 4. Experiment register

Status: **idea** (not in the plan yet) · **planned** (in the plan) · **go-ahead** · **running** ·
**pass** · **fail** · **done**. Update the row when the status changes; put the full result in the
plan's Decision log and a one-line result here.

| ID | Experiment | Targets | Goals | Run cost | Judge? | Needs first | Status | Result |
|---|---|---|---|---|---|---|---|---|
| D-A | Workflow as the default engine | I10, I11 | C T | $0 | No | Owner decision | planned | |
| E1 | `match_profile`: fewer gaps called partial (more thinking; "same activity?" prompt) | I1 | C T | ~$1.50 | No (answer key) | — | planned | |
| E2 | Flex PayGo, plus a request timeout | I12, I11 | C (T worse) | ~$0.40 | No | Read the Flex page | planned | |
| E3 | Replay tool, control run (gives T), "should address" tier | I2, I0 | S | ~$7.80 + 2 panels | Yes | T1 helps | planned | |
| E4 | Writer thinking `medium`; Flash writer | I9, I12 | C T | ~$3.90 + 1 panel per arm | Yes | E3 (T, replay) | planned | |
| E5 | Writer rules R1 to R4 | I3 to I8 | S | ~$2 per rule screen; ~$3.90 + 1 panel to confirm | Confirm only | E3 | planned | |
| E6 | `check_claims` catches soft stretches | I3 | S | ~$0.35 per arm | No (labelled set) | — | planned | |
| E7 | Small edits on the mid tier; trims as their own call | I1 | C T | ~$0.15 | No, then inside a judged run | E1 | planned | |
| N1 | Writer's word target lowered so drafts land under 340 | I1 | C T | ~$1 (draft 1 only) | No (word counts) | — | idea | |
| N2 | Revision sends the draft, failed checks and the relevant plan rows, not the whole context | I1, I9 | C T | ~$0.15 (replay) | No, then inside a judged run | E7 | idea | |
| N3 | `check_claims` writes out only doubtful claims, not every claim | I11 | T C | ~$0.35 | No (labelled set) | E6 | idea | Risk: its evidence-before-verdict order is deliberate and may be what makes it accurate |
| N4 | Request timeout on Gemini calls | I11 | T | $0 | No | — | idea | Folded into E2 in the plan |
| S3 to S5 | Per-step thinking sweeps (§3) | I9 | C T | cents each | No | E6 for S3 | idea | |
| D-E | "What I want from my next role" profile field | I5 | S | — | — | E5 R2 measured first | planned | Changes the frozen profile |
| T1 | Checkpoints + comparison tool: runs side by side, points lost per problem, per-ad differences, against T; history of kept changes ([experiment-checkpoints-plan.md](experiment-checkpoints-plan.md)) | I0 | — | $0 (code) | — | — | done | Built 2026-11-25 ([plan](experiment-checkpoints-plan.md)): c0 = baseline (7.13 points, $0.2581 a letter), c1 = its judge-only repeat; judge noise T 0.31 points over 17 letters |

**Suggested order.** T1 and the free analyses, then E1, E2, E6, N1 (cheap, no judge), then E3
(biggest score bucket, and it makes T), then E4, E5, E7 and the N-rows on top of E3's replay tool.
