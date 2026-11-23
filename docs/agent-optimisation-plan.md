# Cover-letter agent: optimisation plan (branch `cost_perform_analysis`)

**Status:** DRAFT v0.1, 2026-11-11. Research and analysis only: no app code, prompt or eval file
was changed, and no paid Gemini call or judge run was made. Every experiment below waits for the
owner's go-ahead.

**Goal:** letters that score higher with judge v1, and a lower cost per letter. Second goal: the
owner is learning agent-harness optimisation, so each lever says in plain words why it works.

**This replaces** the 2026-10-04 decision not to iterate on letter wording (CLAUDE.md, Writer v3).

**How to read the numbers.** Each number carries a label and a source:

- **[M]** measured: read from a file or a query in this session.
- **[E]** estimated: arithmetic on measured numbers (the sum is shown or obvious).
- **[G]** guess: my judgement, to be replaced by an experiment.

Sources, by short name:

| Short name | What it is |
|---|---|
| cost table | `evals/results/v2/cost-dev-agent-v1.md`, rebuilt 2026-11-11 with `letter_lab.py cost-report --set v2 dev-agent-v1` |
| usage | `llm_usage` rows of the run's 17 letter runs, in `evals/work/v2/eval.db` (read-only queries) |
| states | `evals/work/v2/runs/dev-agent-v1/states/*.json` (each run's plan, drafts and check results) |
| run.json | `evals/work/v2/runs/dev-agent-v1/run.json` |
| judge | `evals/work/v2/runs/dev-agent-v1/judge-v1-dev.json` (panel `v1-dev`), scored by `scripts/letter_points.py` |
| labels | `evals/work/v2/points-draft/labels.json` (the owner's points labels) |
| key | `evals/work/v2/reference/ads/*.json`, the owner-corrected answer key (17 dev ads only; test never opened) |
| loop report | `letter_lab.py loop-report --set v2 dev-agent-v1`, run 2026-11-11. Its output prints ad titles, so it was kept out of the repo; re-run the command to see it |
| Google | Google's pricing and model pages, read through web search on 2026-11-11 (see §5) |

Letters and the profile are never quoted here. Ads are named by key only.

---

## 1. Summary

1. **The harness and the judge disagree about what a letter must cover.** 13 of the 15 rows the
   judge counted as missed were rows the letter plan told the writer were optional [M]. This is
   the largest single cause of lost points (28% of all points lost, 34% on the relevant ads) [M].
2. **A cheap early step causes expensive late work.** `match_profile` (5% of the cost) marked 11
   of the answer key's 49 gaps as partly backed [M]. Rows like that triggered 5 of the run's 12
   revisions [M], about 15% of the run's spend [E], on a requirement the profile cannot meet.
3. **Thinking tokens are 70% of the cost** [M], and the writer's thinking alone is 59% [M]. More
   writer thinking did not go with a higher judge score on these 17 letters (correlation -0.26)
   [M], which proves nothing either way but says the level has never been tested.
4. **The agent loop buys nothing measurable.** It took the fixed workflow's path on 16 of 17
   letters (the 17th died on a rate-limit error), with 0 refusals, for 6% of the cost and 26% of
   the time [M].
5. **Several wording problems are caused by the writer prompt itself.** The four most common
   stock patterns are sentence shapes the prompt asks for, and two of them repeat words the
   prompt uses [M].
6. **At 20 letters a month the money is small.** About $6.30 a month after the trial [E]. Every
   cost lever together saves roughly $4.50 a month [G]. At that volume, quality and time are the
   better targets. At a hosted volume the same levers decide whether the product has a margin.

---

## 2. The baseline: where the money and time go

Run `dev-agent-v1`: agent engine, 17 dev letters, 2026-10-26. Judged by panel `v1-dev`.

| | Value | Source |
|---|---|---|
| Cost per letter | $0.258 (all 17), $0.287 (10 relevant) | [M] cost table, results file |
| Time per letter | 487 s mean, 416 s median, range 171 to 1,016 s | [M] run.json |
| Judge points | 7.13 mean (all 17), 7.15 (relevant), range 4.2 to 9.0 | [M] judge |
| Owner points | 7.31 mean (all 17), 7.33 (relevant) | [M] labels |
| Clean on return | 15 of 17 (one `failed` on a 429, one `budget_stopped`) | [M] run.json |
| Passed all checks on draft 1 | 8 of 17 | [M] states |

The starting facts in the brief hold for the totals. The split has moved since the older 15-ad
report: **revisions are 31% of the cost, not 15%**, because this run revised 0.7 times per letter
against 0.3 to 0.4 before [M].

### 2.1 Per step

Per letter, averaged over 17. "Thinking share" is the part of that step's cost that is thinking
tokens, which bill at the output price.

| Step | Model, thinking | Calls | $ / letter | Share | Thinking share | Seconds | What it buys |
|---|---|---|---|---|---|---|---|
| `generate_letter` | 3.1 Pro preview, high | 1.0 | $0.1178 | 46% | 79% | 143 | The letter. 7,747 thinking tokens for 999 tokens of output. |
| `revise_letter` | 3.1 Pro preview, high | 0.7 | $0.0795 | 31% | 75% | 53 | 12 revisions. 5 chased a row the key calls a gap, 2 fixed claims by changing 2% of the words, 2 only trimmed length, 3 fixed a real missing row (§2.3). |
| `check_claims` | 3.8 Flash, medium | 1.6 | $0.0307 | 12% | 49% | 73 | Caught 3 overstatements on 2 first drafts. Passed all 8 claims the judge later flagged as untrue (§3.2). |
| `orchestrate` | 3.8 Flash, medium | 10.0 | $0.0159 | 6% | 25% | 128 | Picks the next step. Same order as the fixed workflow on 16 of 17. |
| `match_profile` | 3.8 Flash, medium | 1.0 | $0.0123 | 5% | 62% | 40 | The evidence map. 86% of row statuses agree with the key (§2.3). |
| `check_requirements` | 3.1 Flash-Lite, low | 1.6 | $0.0019 | 1% | 23% | 12 | Blocks a draft that misses a must-cover row. Never looks at optional rows. |
| `style_lint` | code | 1.6 | $0 | 0% | n/a | 0 | Four "over 340 words" failures, each then fixed by a Pro revision. |
| **Total** | | | **$0.2581** | | **70%** | **487** | |

All [M]: cost table for cost, calls and seconds; usage for the thinking shares. `analyze_job`
is not in the run total because its result is cached per ad: it cost $0.010 per ad in stage 3
(49 calls, $0.489) [M]. A letter for a newly seen ad therefore costs about $0.27 [E].

By token type, across the run: thinking 69.6%, input 16.5%, output 13.8% [M usage]. So:

- Shrinking prompts can save at most about one sixth of the cost.
- Caching can only touch the input part, and only the repeated part of it.
- The writer's thinking ($0.153 a letter [M]) is the one large number.

### 2.2 What one more draft costs

| Drafts | Letters | Mean cost | Mean time | Judge points |
|---|---|---|---|---|
| 1 | 8 | $0.16 | 412 s | 6.57 |
| 2 | 6 | $0.29 | 550 s | 7.58 |
| 3 | 3 | $0.45 | 563 s | 7.72 |

[M] run.json and judge. **Do not read the points column as "revising helps".** The groups hold
different ads (5 of the 8 one-draft letters are for graduate programs or graduate roles), and
there are only 8, 6 and 3 letters, so they cannot be compared. What the table does show: the
one-draft letters hold 7 of the judge's 8 untrue claims and 7 of its 8 weak examples [M], so
passing all three checks on draft 1 does not predict a good judge score. The checks measure
something else.

### 2.3 What triggered the 12 revisions

| Trigger | Revisions | Was it real? |
|---|---|---|
| A must-cover row "missing", and the key says the row is a **gap** | 5 | No. `match_profile` called the row partial, so the plan required it. The writer rightly did not claim it, the check failed, and the loop paid a Pro revision to try again. Ads `seek-94890841`, `seek-95219353` (2), `seek-95220494` (2). |
| A must-cover row missing, and the key agrees it is backed | 3 | Yes. `seek-95100426`, `seek-95260505` (2, one also over length). |
| A claim the profile does not back | 2 | Yes: 3 overstated claims on 2 drafts, at least 2 of them a listed skill worded as experience. Each fix changed 2% of the words. |
| Over 340 words only | 2 | Yes, but a trim does not need the strong model at high thinking. |

[M] states joined to key. The three letters in the first row cost $0.32, $0.45 and $0.42, against
$0.16 for a one-draft letter [M]. `seek-95220494` spent all three drafts on its gap row, ended
`budget_stopped`, and still scored 9.0 with the judge, which did not count that row as missable.
Five revisions at about $0.114 each plus their re-checks is about $0.67 of the run's $4.39, or
15% [E].

`match_profile` against the key, on the 184 rows that are not eligibility rows [M]:

| | Count |
|---|---|
| Same status | 159 (86%) |
| Over-credited: key gap, pipeline partial | 11 (of 49 key gaps, 22%) |
| Over-credited: key partial, pipeline supported | 7 |
| Under-credited | 7 |

One caution: the key began as a `match_profile` output that the owner corrected, so agreement
may be a little flattered.

---

## 3. Where the judge points go

Judge v1 starts each letter at 10 and deducts per problem. The 17 letters lost 48.85 points in
all, 2.87 a letter [M judge]. The owner's labels give the same ranking (45.80 lost; missed rows
28%, weak example 16%, untrue 13%) [M labels].

| Problem | Points lost | Share | Instances | Letters | Traces to |
|---|---|---|---|---|---|
| Missed row | 13.50 | 28% | 15 | 8 | The letter plan (`guardrails.must_cover`), not the writer |
| Untrue | 8.00 | 16% | 8 | 6 | Writer's linking sentences, and `check_claims` passing them |
| Weak example | 6.00 | 12% | 8 | 6 | Unranked evidence from `match_profile`; writer adds a second, weaker example |
| No personal reason | 5.50 | 11% | 11 | 11 | Writer's opening rule; the profile holds nothing about what the candidate wants |
| Mix-up | 5.50 | 11% | 9 | 7 | Writer rule that builds paragraphs around the ad's topics |
| Generic | 5.10 | 10% | 16 | 16 | Writer's required "connecting sentence" |
| Stock phrasing | 4.75 | 10% | 13 | 13 | Writer prompt wording (sentence shapes it asks for) |
| Too long | 0.50 | 1% | 1 | 1 | n/a |

On the 10 relevant ads the picture is sharper: missed rows 34%, every other problem 11% or less
[M].

The tick counts in the brief (sounds_ai 13, generic 11 and so on) are the stage 7 ticks. The
points scheme counts differently: generic is present on 16 letters but costs only 0.3 each, so
it is common and cheap, while a missed row is rarer and costs 0.75 or 1.5.

### 3.1 Missed rows: the plan is narrower than the standard

The judge can charge a miss on any row that is backed (supported or partial), essential or
important, and headline or mention: 114 rows on the dev set. The harness requires only headline
rows and essential mentions. The rest it lists as "may use if it fits naturally".

| Plan category of the 114 missable rows | Rows | Judge-missed | Miss rate |
|---|---|---|---|
| Must cover | 58 | 2 | 3% |
| May use | 54 | 13 | 24% |
| Other | 2 | 0 | |

[M] states joined to key and judge. The writer and `check_requirements` do their job on what
they are given. The definition is the problem: "important mention" rows are optional to the
harness and chargeable to the judge. This was a deliberate choice ("a one-page letter can't name
15 things", `check_requirements.py`), made before the standard existed. The owner's own labels
agree with the judge here: 12 of the owner's 14 missed rows were may-use rows [M].

Fixing every may-use miss would be worth 10.5 points over 17 letters, 0.62 a letter [E]. That is
the upper bound, not a forecast.

### 3.2 Untrue: soft claims slip through both nets

All 8 flags are low-severity stretches, and every one sits in a letter whose final draft passed
`check_claims` [M]. Five of the 8 flagged sentences were not in the writer's own claims list at
all [M]. Most of the 8 are interpretive sentences: what an experience gave or developed in the
candidate, or a link drawn between an experience and a requirement. One is a statement about
the employer. The writer does not list these as claims because they read as interpretation. The
checker reads the whole letter on its own and still passed them; for 2 of the 5 it logged an
"undeclared but backed" warning, which never blocks [M states].

### 3.3 Wording: the prompt writes the template

| Pattern (name from `letter_metrics.STOCK_PHRASES`) | Hits | Letters | Where it comes from |
|---|---|---|---|
| "prepares me to/for" | 24 | 14 | The prompt asks for a sentence on "which part of the job it prepares the candidate for" |
| "This background/experience..." sentence opener | 21 | 13 | The required connecting sentence after each piece of evidence |
| "I recently completed my" opener | 16 | 16 | "Open with who the candidate is" |
| "carries over to" | 14 | 11 | The prompt's own example of honest partial framing uses these words |

[M] letters run through `letter_metrics.stock_phrases`; prompt text in `tools/generate.py`.
9 of the judge's 16 generic quotes are the "prepares me" sentence [M]. All 11 no-personal-reason
quotes are the opening "I want / I am drawn to / I am interested in" sentence [M].

Two general mechanisms are at work, and both will hold on unseen ads because neither is about
any ad:

- **Models repeat the words of their instructions.** An example phrase in a prompt becomes the
  phrase in the output. This is why workflow-v3's ban on old stock bridges produced new ones.
- **A required slot produces a filler for the slot.** Asking for one linking sentence per
  paragraph and one reason-for-applying sentence gets exactly those sentences, in one shape,
  in every letter.

The no-personal-reason problem has a second cause that no prompt can fix. The frozen eval
profile has an empty summary and no field for what the candidate wants [M eval.db: `summary` is
NULL]. The writer is told to state only what the profile backs, so the only material left for a
reason is the ad. The standard (item D4b) wants a reason tied to the candidate's own experience,
study or goals. Experience and study are in the profile. Goals are not.

Length: the prompt asks for 250 to 300 words and the letters average 319, with 16 of 17 over
300 [M judge `words`]. The judge does not charge length under 340 words, so this costs revisions
(four drafts over 340), not points.

---

## 4. Each step: is it earning its cost?

**`generate_letter` (46%).** Necessary, and the only place quality is made. The untested
decision is thinking at `high`. Thinking tokens per call ran from 3,352 to 12,297 with no
relation to the result [M]. The letter is about 300 words built from a plan that earlier steps
already made, so much of that thinking is the model re-deriving the plan. Cheapest route to the
same result: test `medium` (§7, E4).

**`revise_letter` (31%).** Overpriced for what it does. A revision re-sends the whole context to
the strongest model at high thinking to change 2 to 23% of the words (mean 10%) [M loop report].
Five of twelve should never have run (§2.3). The other seven are narrow edits: a reworded claim,
a trim, one added point. Cheapest route: stop the false triggers first (E1), then move edits to
the mid tier (E7).

**`check_claims` (12%).** Keep it, because untrue is the costliest problem in the points scheme
(-1.0 and -2.5). But its catch rate against the judge is poor: it found 3 overstatements on
first drafts and passed 8 claims the judge flagged on final ones. It also takes 73 s
a letter. Its output lists every claim including the supported ones (1,656 output tokens a call),
which is why it is slow. Fix its recall on soft claims (E6) before trying to make it cheaper.

**`orchestrate` (6% of cost, 26% of time).** Not earning its cost. Across three runs (agent-v1,
agent-v2-side, dev-agent-v1) the agent chose the workflow's order on 46 of 47 letters, with 0
refusals [M: `evals/results/agent-v1-loop.md`, `agent-v2-side-loop.md`, and this run's loop
report]. The guardrails leave it almost always one sensible move, so there is nothing for it
to decide. The owner chose the agent on 2026-10-03 for agent experience. My recommendation
is to make the workflow the default and keep the agent as the alternative: this optimisation
work is the stronger experience, and an orchestrator that never decides is not a good example
of one. See Decision D-A.

**`match_profile` (5%).** The best-value place to spend more, not less. Its errors are the most
expensive in the run because everything downstream trusts it: an over-credited gap becomes a
must-cover row, then a failed check, then a Pro revision. It also hands the writer two evidence
pointers per must-cover row on average, unranked (15 rows with one pointer, 31 with two, 16 with
three) [M states], and the writer uses the spare ones, which is the weak-example problem.

**`check_requirements` (1%).** Cheap and accurate on its list (the judge found 2 misses in 58
must-cover rows). The problem is the list.

**`analyze_job` ($0.010 per ad, cached).** Not examined: the answer key shares its rows, so the
key cannot grade it.

**The loop as a whole.** The design assumption was "check, then revise until the checks pass,
produces a better letter". The data does not support that as built: the checks agree with the
judge only loosely, and the largest causes of lost points (optional rows, soft claims, weak
examples, mix-ups, wording) are things no check looks at. Money goes to making drafts pass
checks, and the judge deducts for other things. The plan below moves effort to the front of the
pipeline (a correct evidence map and a plan that matches the standard) so the first draft is
right more often, and makes the back of the pipeline cheap.

---

## 5. Gemini facts, checked 2026-11-11

From Google's pages through web search. I could read search summaries of the pages, not the live
tables, so re-read the pricing page before changing `PRICES` in `client.py`.

| Fact | Value | Page |
|---|---|---|
| `gemini-3.1-pro-preview` | $2.00 in, $12.00 out per 1M (prompts up to 200k), cached input $0.20; batch $1.00 / $6.00 | ai.google.dev pricing; Agent Platform pricing |
| | Thinking levels LOW, MEDIUM, HIGH; default HIGH | ai.google.dev thinking; Vertex thinking |
| | Released 2026-02-19, still preview, no shutdown date announced; preview models can be retired on 2 weeks' notice | ai.google.dev deprecations |
| `gemini-3.8-flash` | $0.75 in, $3.75 out through 2026-12-31; **$1.50 / $7.50 from 2027-01-01** | Agent Platform pricing |
| | Thinking levels LOW, MEDIUM, HIGH; default MEDIUM; MINIMAL is an error | Developer's guide to Gemini 3.8 Flash |
| `gemini-3.1-flash-lite` | $0.25 in, $1.50 out; batch $0.125 / $0.75 | ai.google.dev pricing |
| Flex PayGo | 50% off standard, for work that can wait; global endpoint only; the supported list includes `gemini-3.1-pro-preview`; set with two request headers; slower and more throttled, no stated time limit | Flex PayGo page |
| Batch | 50% off, results later; a cache discount does not stack with it | Batch inference page |
| Implicit caching | On by default, 90% off cached tokens; documented minimum 4,096 tokens for 3.1 Pro (an older copy of the page; forum reports say real hits need more) | Context cache overview |
| Gemini 4 Argon / Carbon | Released 2026-10-13 and 2026-11-10; $2 / $10 until 2026-11-30, then **$4 / $20** | Agent Platform pricing; blog.google |

`client.PRICES` matches the three models the app uses. Two things changed since 2026-10-01:
Gemini 4 exists (dearer than 3.1 Pro from December, so not a cost lever), and Flex PayGo is a
half-price tier the plan had not considered.

Caching on this run: 3 of 29 writer calls had a cache hit, each about 4,024 tokens of a 7,300 to
7,700 token prompt; none of the other 242 calls did [M usage]. So
`future_work/gemini-prompt-caching.md` ("never hits") is out of date: it hits sometimes, on the
writer only.

---

## 6. Is the saving worth it?

After the trial, at the prices in §5 [E]: the Pro steps stay at $0.197, the Flash steps double
from $0.059 to $0.118, Flash-Lite stays at $0.002. **$0.317 per letter** for the run, about
$0.34 with `analyze_job` and scoring for a new ad.

| Volume | Letters / month | Cost now | Cost from 2027 | All cost levers (about -70% [G]) | Saved per year |
|---|---|---|---|---|---|
| Owner | 20 | $5.20 | $6.30 | $1.90 | about $53 |
| Hosted, 1,000 users at 20 each (illustrative) | 20,000 | $5,200 | $6,300 | $1,900 | about $53,000 |

Plainly: **for the owner's own use, no cost lever is worth more than a few hours of work for the
money.** $53 a year does not pay for a week of prompt experiments, each of which costs $4 to $8
in Gemini calls plus judge runs. The reasons to do the cost work anyway are (1) learning, which
is a stated goal, (2) time, since 487 s a letter is long and the same levers cut it, and (3) the
hosted case, where $6.30 a user a month in letter costs would eat most of a low-priced plan and
$1.90 would not.

So the order of work is: quality first, with the cost levers that are nearly free (D-A, E2)
taken on the way, and the costly-to-test cost levers (E4, E7) ridden on quality experiments that
are being judged anyway.

Each lever alone, at 2027 prices [E unless marked]:

| Lever | Saves per letter | Quality risk | Why it works |
|---|---|---|---|
| Flex PayGo on every letter call | about $0.16 (50%) | None expected: same model, same prompt | Google sells idle capacity at half price to callers who can wait. Letters are written in the background, so they can. |
| Workflow instead of agent | $0.032 (10%) and 128 s | None: same steps | Removes 10 model calls whose answer the code already knows. |
| No revisions for gap rows (E1) | about $0.04 | Positive | A correct evidence map stops the loop chasing a row the profile cannot meet. |
| Writer thinking `medium` | $0.03 to $0.06 [G] | Unknown: the test | The plan is made upstream; the writer may not need to re-derive it. |
| Edits on the mid tier | $0.04 to $0.06 [G] | Low | A narrow edit is an easier task than a first draft. |
| Reliable caching of the writer prompt | at most $0.02 | None | Only the repeated input, which is under 10% of the cost. |

---

## 7. Experiments, ranked

Ranked by expected gain against what it costs to find out. E1 to E3 are specified in full in §9.

| # | Change | Hypothesis | Expected effect | Cost to run | Pass / fail rule (fixed now) |
|---|---|---|---|---|---|
| D-A | Default engine `workflow` | The agent decides nothing | -$0.016 now, -$0.032 from 2027, -128 s; score unchanged | $0, a setting | A decision, not an experiment (§10) |
| E1 | `match_profile`: fewer gaps called partial | An explicit "same activity or not" test, or more thinking, cuts over-crediting | 11 over-credited gaps down to 5 or fewer; about 5 of 12 revisions gone (-15% cost); fewer stretches | about $1.30 to $1.70 Gemini, **no judge** | §9.1 |
| E2 | Flex PayGo | Background letters tolerate slower calls | -50% cost; slower by an unknown amount | about $0.40 Gemini, **no judge** | §9.2 |
| E3 | Replay harness, noise floor, and a "should address" plan tier | Requiring important backed mentions removes most missed rows without hurting the rest | Missed rows 15 down to 7 or fewer; +0.3 points a letter [G]; +$0.01 to $0.03 a letter | about $7.80 Gemini, 2 judge panels | §9.3 |
| E4 | Writer thinking `medium` (arm 2: 3.8 Flash at `high` as writer) | High thinking re-derives a plan it was given | -$0.03 to -$0.06 a letter; points within noise | about $3.90 Gemini per arm, 1 panel per arm | Mean points not below the control by more than the noise threshold T from E3; code gates 17 of 17; cost per letter down by at least $0.03. Arm 2 also gives a tested fallback if the preview model is retired. |
| E5 | Writer rules v4: four general rules (§8) | The prompt's own wording and slots cause the stock patterns, thin links, ad-only reasons, weak examples and mix-ups | Screen: stock hits and cross-letter repeats down by a third. Confirm: +0.5 points a letter [G] | Screen about $2 per rule (draft 1 only, code metrics, no judge); confirm about $3.90 + 1 panel | Screen, per rule: its own code measure improves and no other code measure worsens. Confirm, the bundle: mean points above the control by more than T; untrue count not up by more than 2. |
| E6 | `check_claims` replayed on the 17 judged letters | Telling the checker that "what an experience gave or taught me" is a claim raises recall | Catches 4 or more of the judge's 8 flags | about $0.35 per arm, **no judge** (the judged baseline is the labelled set) | At least 4 of 8 judge flags caught, and no more than 2 new failures among the 11 letters the judge found clean, in each of two repeat runs |
| E7 | Edits on the mid tier; length trims as their own small call | A narrow edit does not need the strongest model | -$0.04 to -$0.06 a letter | Replay of the 7 real revisions of this run, about $0.15, code checks only; then confirmed inside the next judged run | Every replayed edit fixes its failed check, breaks no other check, and changes at most 5 percentage points more of the words than the Pro revision of the same draft did |

Free analyses that need no go-ahead and are worth doing before E3: (a) count, per dev ad, how
many rows the new tier would add; (b) re-read `letter_metrics` C4 (98 phrases repeated across 3
or more letters) as the baseline for E5.

---

## 8. Writer rules proposed for E5, with the overfitting test

A rule is allowed only if it names a general failure and a reason it holds on ads never seen.
A rule that would only repair particular dev letters is out. None of these names an ad, an
employer or a phrase from a dev letter.

| Rule (in words; exact text to be written at go-ahead) | General failure it targets | Why it should hold on unseen ads | Its own measure |
|---|---|---|---|
| **R1. Say the link inside the evidence sentence; no separate "this prepares me" sentence. Remove example phrasings from the prompt.** | Models reuse instruction wording, and fill a required slot with one stock shape | The mechanism is about prompts, not ads. It was seen once already (workflow-v3 swapped old bridges for new ones) | C4 cross-letter repeats (list-free, so it cannot be gamed by swapping phrases); stock hits; judge `generic` |
| **R2. The reason for applying starts from something the candidate did or studied and says what in this role continues it. A fact about the employer alone is not a reason.** | Motivation built only from the ad is flattery any applicant could write | Every ad has a blurb to restate; every profile has experience to start from | `letter_metrics.motivation` ties (10 of 17 now); judge D4b (11 of 17 fail now) |
| **R3. One example per point: the strongest evidence the plan lists first. A second example only for a different point.** Needs the plan to order evidence (code: internship and thesis before coursework and unrelated jobs, by `experience_type` and dates). | With several pointers and no order, the model picks by word overlap with the ad, and pads with extras | Evidence strength belongs to the profile, not the ad | Judge `weak_example` (8 instances now); evidence pointers used per point |
| **R4. Each experience is told once, in one paragraph. Paragraphs are built around the candidate's experiences, each tied to the ad's work, not around the ad's topics.** | Organising by the ad's topics splits one experience across paragraphs and mixes several in one | A letter has 2 to 3 main experiences whatever the ad | Judge `mix_up` (9 instances now); code: paragraphs naming each experience |

Not proposed, and why: a banned list built from the stock-phrase regex. That list was made from
these 17 letters, so banning it improves the count by construction while the letters stay
formulaic in new words.

One product change sits outside the writer: a profile field for "what I want from my next role".
It is the only honest source for a personal reason beyond experience and study. The eval profile
is frozen, so this is the owner's call (§10, D-E).

---

## 9. The first three experiments, in full

### 9.0 Rules for every experiment

- **Judge:** always `grading_panel.py v2-run <tag> --judge v1` with `JUDGE_CLAUDE_BIN` set to the
  pinned binary. The standard and examples are not touched, and never while a judge run is going.
- **Split:** dev only. The test split and its key stay closed until the end.
- **Paired comparison:** two arms are compared ad by ad on the same 17 ads. Report the mean of
  the 17 differences, how many ads went up and down, and the relevant-10 mean beside it. No
  conclusion from one letter.
- **Judge noise, measured:** the same judge v1 on the same 17 letters, twice (`pts-cal-2` and
  `pts-check-1` against `v1-dev`): 11 letters scored the same, 6 moved, by up to 1.5; the paired
  differences have a standard deviation of 0.65 and the mean moved by 0.19 [M]. The moves were
  in missed rows (3 letters), untrue (2) and mix-up (1). So re-judging identical letters can move
  the dev mean by about 0.3.
- **Writer noise, not yet measured:** the writer runs at temperature 1.0, so the same prompt
  gives a different letter each time. E3's control arm measures this. Until then, my working
  figure is that one dev run with one judging cannot confirm a change in the mean smaller than
  about 0.5 to 0.7 points [E: paired standard deviation of 0.8 to 1.0 over 17 ads at 80% power].
- **Targeted measure first.** Each experiment names the one measure its change should move
  (missed rows, over-credited gaps, caught flags). That measure is far less noisy than the total.
  The total points are the guard: they must not fall.
- **Cost:** recorded from `llm_usage` with `cost-report`, per arm.

### 9.1 E1: `match_profile` against the answer key

**Why first:** it costs about a dollar, needs no judge and no writer, and its target is measured
today.

- **Change.** No letter is written. For the 17 dev ads, run `match_profile` alone on the cached
  requirement rows and compare each row's status with the key.
  - Arm A (control): current prompt and thinking (`medium`), run twice. The two runs show the
    step's own run-to-run spread.
  - Arm B: `GEMINI_THINKING_MID=high`, current prompt, run twice. Environment variable only.
  - Arm C: prompt change, run twice. For a row it would mark partial, the model first writes one
    line naming the activity the ad asks for and the nearest activity in the profile, then
    decides: same activity = supported, a neighbouring activity that would transfer = partial, a
    different activity or only a shared topic = gap. Any example in the prompt is invented, not
    taken from a dev ad.
- **Why it should work.** The over-credited rows are cases where the profile shares a subject
  with the ad but not the activity. Making the model state both activities before it decides
  turns a vague similarity judgement into a comparison it can get right. This is the general
  "explain, then decide" pattern for a classifier.
- **Tooling.** `letter_lab.py analyze --set v2 --split dev --label <tag>` already runs
  `analyze_job` (cached, free) and `match_profile`. New: a small scorer that joins its output to
  the key and prints the three counts below (eval tooling, about 40 lines).
- **Measures** (184 non-eligibility rows): G = key gaps marked partial or supported (11 of 49
  now, from one run); U = key backed rows marked gap (2 of 135 now); S = rows with the same
  status (159 now).
- **Cost.** $0.0124 a call [M, mean of 68 calls], 17 calls a run, 6 runs: $1.27, about $1.70 if
  `high` doubles arm B [E].
- **Pass.** For arm B or C, averaged over its two runs: G of 5 or fewer, and U of 4 or fewer,
  and S not below arm A's, and arm A's mean G minus the arm's mean G is at least 4 rows and at
  least twice the gap between arm A's two runs. If both pass, take the cheaper.
- **Fail.** Keep the current step. Then the fallback is in `check_requirements`: a partial row
  that fails twice stops blocking (code only), which removes the wasted revisions without fixing
  the map.
- **Caveat.** The key started from a `match_profile` output, so it may favour the current
  prompt's habits. A pass is confirmed only at the end, on test.

### 9.2 E2: Flex PayGo

**Why second:** the largest cost cut available, and no quality question to judge.

- **Before spending anything.** Read the Flex PayGo page for three facts I could not confirm:
  whether `gemini-3.8-flash` and `gemini-3.1-flash-lite` are on the supported list, whether the
  trial's credits cover Flex usage, and any stated time limit.
- **Change** (app code, after go-ahead). In `client.py`: when `GEMINI_SERVICE_TIER=flex`, send
  the two headers from the Flex page on Gemini calls; default off. Add a request timeout (there
  is none today, and a call hung for hours in stage 6; one `generate_letter` call in this run
  took 926 s [M]). A flex factor on `PRICES` so `cost_usd` stays an honest estimate. The `small`
  tier keeps standard service if Flex does not support it.
- **Run.** Three dev ads with the workflow engine and Flex on: `seek-95158567`, `seek-94932262`,
  `seek-95219353` (a one-, two- and three-draft letter in the baseline). About $0.40 at standard
  prices, half that if Flex bills as stated [E].
- **Measures.** Seconds per call against this run's medians (generate 93 s, check_claims 29 s,
  match_profile 28 s) [M]; 429s and retries; failures; and the Cloud Billing line items the next
  day, which are the only proof of the discount.
- **Pass.** All three letters finish; no single call over 15 minutes; median time per letter no
  more than twice the baseline's 416 s; billing shows about half price. Then Flex becomes the
  setting for background letters, and the user-clicked Rewrite stays on standard so it is fast.
- **Fail.** Stay on standard. Revisit only for a hosted version.
- **Why no judge.** Flex is the same model and prompt on cheaper capacity. That is Google's
  description, taken on trust, and worth one sentence in the Decision log if a later judged run
  on Flex looks odd.

### 9.3 E3: replay, the noise floor, and the "should address" tier

**Why third:** it is the biggest quality bucket and it builds the tool and the yardstick every
later writer experiment needs.

- **Tooling: `letter_lab.py replay`** (eval tooling, after go-ahead).
  `replay --set v2 --split dev --from dev-agent-v1 --label <tag>` loads each saved state, keeps
  the job analysis and the evidence map exactly as they were, clears the drafts and budget, and
  runs the workflow engine from `generate_letter` on. Both arms therefore write from the same
  plan inputs, and a difference between them is the change and not a different `match_profile`
  result. This is the general trick for cheap experiments: freeze everything upstream of the
  step under test.
- **Arm A (control).** No change. Its letters against the baseline's (panel `v1-dev`) are an A/A
  test: same prompt, same plan, new sample. The 17 paired differences give the noise of writer
  plus judge together, as a standard deviation s. **T = 2 x s / sqrt(17)** is then the threshold
  for "a real change in the dev mean" in E3 to E5.
- **Arm B (the change).** In `guardrails`: a third list, SHOULD ADDRESS = rows that are backed
  (supported or partial), important, letter role `mention`, not left out and not backed only by
  a listed skill. Today these are in may-use. The writer's plan shows them under their own
  heading: name each in the ad's own words with its evidence, in a clause or one sentence, and
  group rows that share a theme. `check_requirements` checks them as it checks must-cover rows,
  and blocks when 2 or more are missing; one missing is a warning.
- **Why it should work.** The model follows the plan it is given: must-cover rows are missed 3%
  of the time and optional ones 24%. Moving a row from "optional" to "expected" is the most
  reliable instruction change there is, because it is checked afterwards.
- **Why it could hurt.** About 3 more rows per letter (54 over 17 ads, less the listing-only
  ones) [M] in a letter already at 319 words. Squeezed rows can turn into thin lines (generic),
  extra examples (weak example) or stretches (untrue). The guards below watch for exactly these.
- **Cost.** Per arm about $0.23 a letter (the baseline less `match_profile` and the
  orchestrator), $3.90 for 17 [E]; both arms $7.80. Two judge panels; the last one was $18.58
  notional on the owner's subscription for 17 letters [M eval-set plan, 2026-11-05].
- **Order.** Run both arms. Read the free code measures first (words, gates, stock hits,
  `check_requirements` results). Judge arm B, then arm A.
- **Primary measure.** Judge-missed rows over the 17 letters: 15 in the baseline.
- **Pass, all of:**
  1. Missed rows in arm B are half of arm A's or fewer, with at least 6 ads better and at most 1
     worse.
  2. Mean points: arm B is not below arm A by more than T. (Above arm A by more than T is a
     confirmed score gain, and is reported, but is not required: the expected +0.3 is likely
     under T.)
  3. Untrue instances in arm B are at most arm A's plus 2; weak examples plus mix-ups at most arm
     A's plus 3.
  4. No letter over 340 words; cost per letter at most arm A's plus $0.03.
- **Fail.** Keep must-cover as it is. If only guard 3 or 4 fails, try once more with the tier
  limited to supported rows (partial rows stay optional).
- **If T comes out above 1.0.** The dev set cannot resolve prompt-level changes with one
  judging. From then on, judge each arm twice and average, or decide writer experiments on
  targeted measures only.

---

## 10. Decisions for the owner

| | Decision | My recommendation |
|---|---|---|
| D-A | Default `letter_engine`: agent or workflow | **Workflow.** The agent took the same path on 46 of 47 letters. Keep the agent selectable. |
| D-B | Go-ahead for E1 (about $1.50, no judge) | Yes, first. |
| D-C | Go-ahead for E2 (about $0.40, a `client.py` change), and accepting slower background letters | Yes. Background letters on Flex, the Rewrite button on standard. |
| D-D | Go-ahead for E3 (about $7.80 and two judge panels), and whether "important backed mentions should be addressed" is the rule you want | Yes. It changes a rule you set on purpose, so it is yours: the standard already charges for these rows. |
| D-E | Add a "what I want from my next role" field to the profile | Later, after E5's rule R2 is measured without it. It changes the frozen profile, so it would need its own baseline. |
| D-F | Commit `evals/results/v2/cost-dev-agent-v1.md` (numbers only) | Yes. The loop report for this run prints ad titles, so I left it out of the repo. |

---

## 11. Ideas rejected

- **Batch inference:** the same 50% as Flex, but each of 5 to 9 calls would wait for a batch; Flex gives the discount on ordinary calls.
- **Explicit context caching:** input is 16.5% of the cost and the repeat within a run saves at most $0.02 a letter; not worth a cache lifecycle.
- **Engineering for implicit cache hits:** 3 hits in 29 writer calls; the orchestrator's 1,500-token prompts are under the 4,096 minimum. Keep the stable-first prompt order and do nothing else.
- **Gemini 4 Argon or Carbon as the writer:** $4 / $20 from 2026-12-01, dearer than 3.1 Pro.
- **Flash-Lite for the writer or for `check_claims`:** `check_claims` was moved off the small tier on 2026-10-03 because it passed real overclaims.
- **Lower thinking on `match_profile` or `check_claims`:** together 17% of the cost, and both have measured accuracy problems. If anything, they get more.
- **Dropping `check_claims`:** untrue is the costliest problem; fix what it misses (E6) instead.
- **Dropping `check_requirements`:** $0.002 a letter, and accurate on the list it is given.
- **A higher draft cap or more revisions:** a third draft costs $0.45 a letter and the checks it satisfies are not what the judge deducts for.
- **Writing 2 or 3 drafts and picking the best:** multiplies the most expensive call, and there is no selector that tracks the judge.
- **A banned-phrase list from the stock-phrase regex:** overfits by construction (§8).
- **Company research tool:** generic is low severity on 16 of 17 letters and the cause is the linking sentence, not too few employer facts. Stays in `future_work/`.
- **Voice toggle, learn-from-edits, framing and addressing:** none appears among the causes of lost points. Stay in `future_work/`.
- **Removing the claims list from the writer's output:** saves under $0.01 and the code pointer check depends on it.
- **Fine-tuning a model:** no data volume to support it.
- **Trimming the writer's input for cost:** input is 11% of the writer's cost. Narrowing what the writer sees is worth testing for quality (rule R3), not for money.

---

## Decision log

- **2026-11-11. Plan drafted (v0.1).** Research only. Branch `cost_perform_analysis` from main.
  The 2026-10-04 "don't iterate on letter wording" decision is withdrawn by the owner.
- **2026-11-11. Baseline cost table rebuilt** for `dev-agent-v1` itself:
  `evals/results/v2/cost-dev-agent-v1.md` (new, not committed). Differences from the older
  15-ad report: revisions are 31% of the cost (was 15%), `generate_letter` 46% (was 55%),
  thinking 70% (was 71%), time 487 s (was 229 s).
- **2026-11-11. Findings that change earlier notes.** (1) Implicit caching does hit sometimes
  (3 of 29 writer calls), so `future_work/gemini-prompt-caching.md` is out of date; not edited
  here. (2) Flex PayGo and Gemini 4 are new since `PRICES` was checked on 2026-10-01; `PRICES`
  itself is still right for the three models in use. (3) `match_profile` is the upstream cause
  of 5 of 12 revisions. (4) 13 of 15 judged missed rows were may-use rows.
- **2026-11-11. Not verified.** Flex support for 3.8 Flash and 3.1 Flash-Lite, and whether trial
  credits cover Flex. The live Google pricing tables (search summaries only). Writer run-to-run
  noise (E3 arm A measures it). Every [G] in this document.
