# The cover-letter agent, explained

*Written 2026-10-04 for the project owner, as a learning and reference document. It describes
the code as it stands in the working tree (including the uncommitted writer-prompt edits for
workflow-v3 that another session is evaluating). It was written by reading the code, not by running
it. Numbers come from `evals/results/cost-workflow-v2-vs-agent-v1.md` and CLAUDE.md.*

How to read it: Part 1 gives the ideas in plain words with no code. Part 2 walks through one run
start to finish. Part 3 goes tool by tool. Part 4 covers the rules, the engine loop and
pause/resume. Part 5 is the optimisation map. Part 6 is a file map and glossary. If you only have
five minutes, read Parts 1 and 5.

> **Status caveat (updated 2026-10-04, Phase 8).** The app now runs this pipeline. The idle loop
> starts a run for each match at or above the pipeline bar (`letter_loop_min_score`, default 85;
> matches between the auto-letter score and that bar still get the one-shot `cover_letter.py`), and
> resumes runs once you have answered their questions. The sidebar shows the question card, the
> letter with its open issues and what it leaves out, and the "To work on" list. Parts of this
> document written before Phase 8 (e.g. "Not built yet" in 3.3) describe the earlier state. Phase 7c
> (screening answers, learning suggestions, résumé notes) is not built. The evals still run through
> `scripts/letter_lab.py`.

---

# Part 1. The ideas, in plain words

## 1.1 The one-minute version

Imagine a small **ghostwriting studio** that writes one cover letter for one job ad.

- There is a **project manager** who decides what happens next. The manager never sees the letter. They
  only see a **status board**: "requirements analysed, 2 of 5 checks passed, draft 2 of 3, $0.12 spent."
- There are **specialists**, each good at one narrow task: one reads the ad, one compares it with your
  profile, one writes, one fact-checks, one checks style, one makes small fixes.
- There is a **rulebook enforced by a door guard**. The manager may *ask* for anything, but the guard
  checks every request first. "You can't write the letter until the missing-experience questions are
  answered." "You can't finish until all three checks passed on the latest draft." The rules are
  ordinary Python code, not instructions the AI is asked to remember.
- There is one **shared notebook** (the "state") that every specialist reads from and writes to.

In code terms:

| Studio word | Code word | Where |
|---|---|---|
| Project manager | orchestrator (the "agent") | `app/llm/letter/agent.py` |
| Specialists | tools | `app/llm/letter/tools/*.py`, `gap_policy.py` |
| Door guard | guardrails and gates | `guardrails.py`, `registry.py` |
| Shared notebook | `LetterState` | `state.py` |
| Filing clerk who logs and costs every step | runner | `runner.py` |

The most important sentence in this whole document:

> **Code enforces the rules. The model only chooses the order of steps (and, inside tools, does the
> reading and writing).**

Everything else follows from that. It's why the agent can be trusted not to skip a fact-check, and
why, as we'll see in Part 5, the agent's "decisions" currently almost never differ from a fixed
script.

## 1.2 The seven ideas, one at a time

### Idea 1: Turn the ad into a checklist

A job ad is a wall of text. Before anyone writes, `analyze_job` turns it into a numbered checklist of
**requirements** (R1, R2, R3 ...), in the employer's own words. "Experience building Power BI
dashboards." "Ability to explain findings to non-technical staff." "Australian work rights."

Everything downstream refers to these ids. Nobody has to re-read the ad to say "did we cover R3?"

### Idea 2: Two different ratings per requirement

This is the subtle one. Each requirement gets **two separate labels**, because they answer different
questions.

1. **Importance: how much does the *employer* care?**
   `essential` / `important` / `nice_to_have`.
2. **Letter role: what should the *letter* do with it?**
   - `headline`: a lead point. Make it with concrete evidence.
   - `mention`: relevant, worth a brief mention if the candidate has it.
   - `implied`: anyone good at the headline item has this (git, CI/CD). Don't name it, but it may be
     used if it fits a sentence naturally.
   - `not_for_letter`: eligibility or admin (work rights, licence, clearance, start date). Essential
     to the employer, but you never write it in a letter. It becomes a note on the job card.

Why two? Because "Australian work rights" is *essential* yet should never appear in a letter, and
"idempotent retry handling" can matter to the employer yet is something nobody writes about in a
one-page letter. One rating would force a bad trade-off.

### Idea 3: Evidence pointers (every claim must point at something real)

Your profile is cut into addressable pieces, each with a stable id:

```
experience:12            the whole role
experience:12#s3         the 3rd sentence of that role's description
qualification:4          a degree or certificate
skill:7                  one entry from your skills list
profile:summary          your summary paragraph
```

When the system says "R2 is supported by `experience:12#s3`", that is a **pointer**: a precise
reference to a line of your profile. Code can look the pointer up and prove it exists. A pointer to
something that does not exist (a hallucination) simply fails to resolve. This one trick makes
fact-checking mostly a *lookup* rather than an opinion.

### Idea 4: Supported, partial, or gap: and "no silent gaps"

`match_profile` compares each requirement to your profile and labels it:

- **supported**: the profile shows you did this same kind of activity at the asked level.
- **partial**: related but not the same (Tableau when Power BI is asked), or only lightly shown, or only
  a bare entry in your skills list.
- **gap**: nothing in the profile backs it.

The honesty rule: a **gap on an essential, letter-relevant requirement** is called a *must-have gap*,
and **drafting is blocked** until the user has decided about it. The system will not write around a
gap or quietly invent experience. It asks you.

### Idea 5: The writer never marks its own homework

The writer (`generate_letter`) produces the letter and also lists its own claims. But that list is
self-reported, so it isn't trusted. Three separate checkers look at the finished draft:

- **check_claims**: every factual statement is backed by your profile (or, for statements about the
  employer, by the ad).
- **check_requirements**: the must-cover items actually appear in the letter.
- **style_lint**: a free, code-only check (banned phrases, length, em dashes, sign-off ...).

Different jobs, run by different calls, on different model sizes. The writer (expensive, creative) and
the checkers (cheaper, strict) are deliberately separate.

### Idea 6: Fix, don't rewrite

If a check fails, `revise_letter` makes a **narrow edit** that fixes only the listed problems. A
rewrite would risk breaking the parts that passed. After any new draft, **all three checks run again**,
because a pass on draft 2 says nothing about draft 3.

### Idea 7: Budgets and a graceful stop

A run has hard limits: **3 drafts, 15 tool calls, $0.50**. If a limit hits, the run does not crash and does
not pretend. It hands back the **best draft so far**, flagged with what's still wrong ("open issues").
A draft that failed the fact-check is never returned as if it were clean.

---

# Part 2. One run, start to finish

Take a made-up Data Analyst ad that asks for Power BI (essential), SQL, stakeholder communication, and a
driver's licence. Here's the happy path, then the interesting branches.

```
START  open_run: create a letter_runs row + an empty notebook (LetterState)
  │
  ▼
1. analyze_job        (mid model, or cache hit)
     → R1 Power BI [essential/headline]   R2 SQL [important/headline]
       R3 stakeholders [important/mention]  R4 driver's licence [essential/not_for_letter]
     → also: tone, 8 keywords, facts about the employer, application instructions
  │
  ▼
2. match_profile      (mid model + code corrections)
     → R1 gap (profile has Tableau only)    R2 supported (experience:12#s3)
       R3 partial   R4 supported by a profile fact (location)
     → remembered "no"s from earlier ads are applied here automatically
  │
  ▼
   R1 is a must-have gap (essential + headline + gap + no decision) ───────────┐
  │                                                                              │
  ▼                                                                              ▼
3. ask_user  ──► run PAUSES (status: waiting_user)         generate_letter is REFUSED
     user answers later via the API:                          until R1 has a decision
       "No"  → R1 left out of the letter, remembered forever,
               counts towards your to-work-on list
       "Yes, I used Tableau for a year at uni" → small model proposes profile rows →
               user confirms with one click → rows saved (origin='ask_user') →
               R1 reset to "unknown" → run status becomes "answered"
     the run RESUMES: match_profile re-judges only R1 (now "partial")
  │
  ▼
4. generate_letter    (strong model, "Pro") → draft 1 + its self-declared claims
  │
  ▼
5. check_claims  (mid)      6. check_requirements (small)      7. style_lint (code, free)
     "R3 claim 'presented to executives' overstated"   pass            "2 em dashes"  → FAIL
  │
  ▼   at least one failed, all three have run
8. revise_letter      (strong) → draft 2: narrow edit fixing only the failures
  │
  ▼
9. all three checks again on draft 2 → all pass
  │
  ▼
10. finish   → accepted (all three passed on the LATEST draft)
     result: draft 2, clean = True
```

**Why are all three checks rerun after every revision, even if only one failed?**

There are two separate rules here, and they're easy to blur together:

1. *Before* revising, all three checks must have run on the current draft (`can_revise`). The reviser then
   fixes every failure in one pass. Otherwise a draft with a bad claim and an extra em dash could cost two
   Pro revisions: one for whichever check failed first, and a second for the one found afterwards.
2. *After* revising, all three run again on the new draft. A revision returns the **whole letter** with a
   fresh claims list, not a patch, so any sentence can have changed. Fixing an em dash can reword a
   sentence and quietly make a claim untrue, or push a must-cover item out. A pass on draft 1 proves
   nothing about draft 2, so checks are stored *on the draft* and a new draft starts with none.

Rerunning is cheap for two of the three: `style_lint` is free and `check_requirements` is about $0.001.
The real cost is `check_claims` (about $0.017 and 24 s per call). That's where a "style-only fix doesn't
need a claims recheck" shortcut would save money, and also where it would be unsafe unless the edit is
known not to touch any factual sentence. See lever 5 in Part 5.

What the numbers look like in practice (agent-v1, 15 ads): a letter averages **0.4 revisions**, so most
letters are just steps 1, 2, 4, 5-7 and finish. (0.4 is an average, so the report doesn't say how many
letters needed one; a single letter can have up to two.)

**Branches worth knowing:**

- **Cache hit on step 1.** The checklist depends only on the ad, never on the profile, so it's stored on
  the job row and reused until the ad text changes (or the analysis version is bumped).
- **Out of drafts.** If draft 3 still fails a check, the run ends as `budget_stopped` with the *best* draft
  and its open issues listed, not an error.
- **Account-level stop.** If the daily or total USD guard (or Google's daily quota) trips, the result
  carries `account_limit`, a signal to stop the *whole batch*, not just this letter.
- **A refused call.** If the agent asks for something not allowed ("revise" before the checks ran), nothing
  runs, no tool call is charged, and the reason goes back to it as the next turn's input.

---

# Part 3. The tools, one by one

There are **nine** things the agent can call. Eight do work; `finish` is handled in code.

Cost and timing figures below are per-letter averages from `agent-v1` (15 ads) unless stated.
"Tier" is the model size: **small** = gemini-3.1-flash-lite, **mid** = gemini-3.8-flash,
**strong** = gemini-3.1-pro-preview.

Notice that **no tool takes arguments**. Everything a tool needs lives in the notebook. That keeps the
manager from passing wrong data around, and it's a key reason the system is easy to guard.

## 3.1 `analyze_job`: read the ad, build the checklist

| | |
|---|---|
| **One line** | Turns one ad into requirements (with the two ratings), tone, keywords, employer facts, screening questions and application instructions. |
| **Reads** | The job row's `raw_description`. |
| **Writes** | `state.requirements`, `state.job.tone/keywords/company_facts/...`; caches the result on `job_listings.requirements_checklist`. |
| **Tier** | mid, as a structured-output call (the model must return a fixed JSON shape). |
| **Gate** | Runs once per run; refused if requirements already exist. |

**Code corrects the model afterwards** (`_postprocess`). This is a recurring theme: *the model proposes,
code disposes.* The model's labels are overridden when they break known rules:

- A regex catches eligibility wording (work rights, visa, clearance, police check, licence, own transport)
  and **forces `not_for_letter`**, so the letter can never say "I hold Australian work rights" by accident.
- Attitude wording ("passion for", "eager to learn", "team player") is **capped**: never essential, never a
  headline. (Otherwise the system would pause a letter to ask "do you have a genuine interest in learning?")
- A `nice_to_have` can't be a headline. At most **5 headlines**; extras demote to `mention`.
- Max **20 requirements**; over the cap, least-important items are dropped first.
- An `implied` item must point at a headline/mention item it follows from; if not, it becomes a `mention`.

It also extracts a short **`skill` name** per requirement ("Power BI", "SQL"). That's the key the
remembered-"no" list and the to-work-on counter use, so "Power BI dashboards" and "PowerBI proficiency"
count as the same thing.

**Cost.** It doesn't appear in the cost table at all, most likely because the 15 eval ads had their
checklists cached already. On a real first-time job there is one extra mid call. (Inference from the
table, not measured.)

## 3.2 `match_profile`: map each requirement to your evidence

| | |
|---|---|
| **One line** | Labels every requirement supported / partial / gap, with up to 3 evidence pointers each. |
| **Reads** | The checklist and your profile (as a catalogue of pointers) plus a few "facts" (visa, location) for eligibility items. |
| **Writes** | Each requirement's `status`, `evidence`, `note`; then applies remembered "no"s. |
| **Tier** | mid. About $0.010 and 20 s. |
| **Gate** | Refused if there are no requirements, or if none are `unknown`. |

**Code corrections (`_validate`):**

1. A pointer that doesn't resolve to real profile text is **dropped**.
2. "Supported/partial" with no valid evidence left becomes **gap**.
3. "Supported" backed *only* by a bare skills-list entry is downgraded to **partial**. A skill listing
   shows you *know of* something, not that you have *done* it.
4. Eligibility items cite no evidence; they're judged from profile facts only.
5. A requirement the model skipped entirely becomes a gap ("not assessed").

On a **resume** after an ask_user "Yes", only the requirements reset to `unknown` are re-judged. This is
deliberate: re-judging everything would let a noisy verdict flip and raise a brand-new question
mid-run.

## 3.3 `ask_user`: the honest answer to a gap

| | |
|---|---|
| **One line** | For every must-have gap, creates a question, all in one batch, then pauses the run. |
| **Model call** | None to *ask*. A small-model call happens later, when the user answers "Yes". |
| **Gate** | Needs analysed + matched requirements, at least one pending must-have gap, and no questions already out. |

A requirement "needs a user decision" when **all** are true: essential, letter role headline or mention,
status gap, no decision yet. Eligibility items are never asked about.

Two **gap policies** exist behind the same hook:

- `ask_user_gaps` (production): create questions; the run pauses as `waiting_user`.
- `leave_out_gaps` (evals): treat every pending gap as "leave out". This keeps eval runs comparable and
  non-interactive. It's why `ask_user` has never fired on the 15-ad eval set (none has a pending gap).

**Remembered "no"s.** If you said "No, I don't have Power BI" on an earlier ad, `match_profile` applies
that automatically, so you're **asked once per skill**, and each later ad that wants it counts as a
"sighting" for the **to-work-on list** (skills most ads want that you lack: a ranked self-improvement
list, `GET /gaps/to-work-on`, `scripts/gap_report.py`).

**The answer flow (`answers.py`, called from API requests while no worker holds the run):**

```
No   → requirement left out · "no" saved to gap_decisions · sighting counted → run may become 'answered'
Yes  → you type what you did → small model PROPOSES profile rows (nothing saved yet)
       → you see them, optionally edit, and click confirm (Q11: confirm before saving)
       → rows saved with origin='ask_user' (Q12) · profile_revised_at bumped
       · requirement reset to 'unknown' · any remembered "no" the new skills cover is cleared
```

Why confirm before saving? A misread row becomes evidence in *every later letter*. The parse prompt also
refuses to invent: "I used Tableau at uni" for a Power BI requirement records Tableau, **not** Power BI.

**Not built yet:** the sidebar question card, and the idle loop resuming `answered` runs. Both are
Phase 8.

## 3.4 `generate_letter`: the writer

| | |
|---|---|
| **One line** | Writes draft 1 and lists every claim with its evidence pointer. |
| **Tier** | **strong (Pro)**: the single biggest cost. About $0.10 and 54 s per letter. |
| **Gate** | Only when there are no drafts yet, all requirements matched, and no pending must-have gap. |

**What the writer is given** (one big prompt, built by `writer_context`):

1. **Rules** (`_WRITER_RULES`): truth first, no invention, partial evidence framed honestly, a listed skill
   backs "skills in X" never "experience with X", never lead with what the candidate hasn't done, never
   invent links or emails. Then craft rules: apply evidence to *this employer's work* rather than
   reciting the profile; no stock closes or bridges; open with who the candidate is.
2. **Style guide** (`SKILL.md` plus the banned-phrases list: the same file `style_lint` checks).
3. **Voice reference**: your own `writing_sample` (up to 1,500 words), else your CV text. For rhythm and
   vocabulary only, never facts.
4. **Candidate profile** as a pointer catalogue, the **full ad**, the **analysis**, and the **LETTER PLAN**.

**The LETTER PLAN is the heart of it.** It's built in `guardrails.py`, the *one* definition of what the
letter does with each requirement, shared by writer, reviser and checker so "must cover" can't mean three
different things:

| Bucket | Contains | Effect |
|---|---|---|
| **MUST ADDRESS** | headline items, and essential mentions, that the profile supports (fully or partly) with more than a skills listing | `check_requirements` **blocks** if one is missing |
| **MAY USE** | other supported/partial mentions and implied items, and anything backed only by a skills listing | use if it fits; never required |
| **DO NOT CLAIM** | gaps and anything the user chose to leave out | not mentioned, not written around |
| **NEVER IN THE LETTER** | eligibility items | shown to you on the job card instead |

A subtle rule worth remembering: a must-have backed **only by a skills listing** is *demoted* from must-cover
to may-use (`listing_only`). Requiring it produced "I have not ..." sentences and overclaims in 7 of 15
eval letters, so the rule moved into code.

The output schema forces `{letter, claims:[{quote, source}]}`. Each claim quotes the letter's exact words
and names one pointer.

## 3.5 `check_claims`: the fact-checker (two stages)

| | |
|---|---|
| **One line** | Verifies every factual statement in the latest draft. |
| **Tier** | **mid.** Stage 2 only; stage 1 is code. About $0.017 per call, 1.4 calls per letter, ~24 s each. |
| **Gate** | Needs a draft; runs once per draft. |

**Stage 1: code, free.**
- Every declared claim that cites a pointer must cite one that resolves. A made-up pointer is a **block** (a
  fabrication signal).
- Any link or email address in the letter that isn't in your profile is a **block**. (A past letter
  invented a `youtu.be` link when the ad asked for a video.)

**Stage 2: an independent model read.** It reads the letter sentence by sentence, lists *every* factual
claim it finds (not just the declared ones), says which declared claim each matches, names the profile
pointers that back it, writes a reason, and *then* gives a verdict. (Field order matters: reasoning before
verdict, because with the verdict first, a small model committed to "unsupported" and then argued the
opposite.)

Verdicts: **supported**, **overstated** (related source, bigger claim: a bigger team, "led" vs
"contributed", a tool used daily when it was only studied, "experience with" for a mere skill listing),
**unsupported**, or **not_a_claim** (interest, intent, the application itself, an honest admission like "I
have not used Power Apps").

**Code then disposes:** a "supported" verdict with no pointer that resolves counts as unsupported.
Statements about the employer must be backed by the *ad*.

**Blocking:** unresolved pointers, invented links, overstated/unsupported claims, employer claims the ad
doesn't make. **Warnings only:** true claims the writer forgot to declare. (Burning a Pro revision on
bookkeeping isn't worth it.)

*Why mid, not small?* On real letters the small model passed overclaims mid caught ("practical experience in
C#" from a bare skill listing, 3 of 3 runs). In the planted-claim test: small 35/36, mid 36/36.

## 3.6 `check_requirements`: did the letter cover what it had to?

| | |
|---|---|
| **Tier** | small. About $0.001 per call, a few seconds. |
| **Gate** | Needs a draft; once per draft. |

It checks the MUST ADDRESS list (and reports on MAY USE) by id. For each item the small model must **quote
the words in the letter** that address it, and **code verifies the quote is really in the letter**
(80% word overlap), so "addressed" can't rest on a sentence the checker imagined. PARTIAL items count as
addressed when the letter frames them as honest related experience. A missing must-cover item **blocks**.

## 3.7 `style_lint`: the free check

| | |
|---|---|
| **Tier** | None, pure code. Runs in about a millisecond and costs $0. |

**Blocking:** more than 1 em dash; more than 1 banned phrase; over 340 words or more than 5 body
paragraphs; leftover placeholders like `[Company]`; no sign-off.

**Warnings (never block, but are passed to a revision):** under 230 words; flat sentence rhythm; 3+
paragraphs starting with "I"; American spellings; sentences that lead with a gap ("Although I have not
..."); stock closes ("Thank you for considering ..."); stock bridges ("translates well", "carries over
directly").

The word counter and banned list live here and the eval rubric imports them, so the writer's check and the
eval yardstick can't disagree.

## 3.8 `revise_letter`: the narrow fix

| | |
|---|---|
| **Tier** | strong. About $0.03 per letter (0.4 calls × ~$0.08), ~36 s per call. |
| **Gate (`can_revise`)** | No pending gaps; a draft exists; fewer than 3 drafts; **all three checks have run** on the latest draft; **at least one failed**. |

It's given the *same system prompt and context block* as `generate_letter` (so it could reuse a cached
prefix), then the current draft, then exactly the issues listed by the failed checks. Blocking issues must
be fixed. Warnings are "fix only if easy". It's told to keep length and keep each paragraph's tie to the
employer. The result is a brand-new draft whose checks all start empty.

It measures `changed_pct` (how much of the text changed) but **does not enforce a limit** yet.

## 3.9 `finish`

Not a function: the agent loop decides it. It's accepted when `can_finish` passes (all three checks ran
and passed on the **latest** draft), **or** when the draft limit is reached with every check run (then the
best draft goes back flagged). Otherwise it's refused with the reason.

---

# Part 4. How the engine works

## 4.1 The shared notebook: `LetterState`

One JSON-serialisable object, saved to `letter_runs.state` after **every** step:

```
LetterState
├─ profile_id
├─ job            title, company, tone, keywords, screening_questions,
│                 application_instructions, company_facts
├─ requirements[] id, text, importance, letter_role, theme, implied_by, skill,
│                 evidence[], status, note, user_decision
├─ drafts[]       version, text, claims[], checks{ claims | requirements | style }
├─ user_questions[]
├─ side_outputs   (reserved: screening answers, learning suggestions, résumé notes)
└─ budget         drafts 0/3 · tool calls 0/15 · $0.00/$0.50
```

One design decision explains a lot: **checks belong to a draft**. A pass on draft 1 is stored *on draft 1*;
draft 2 starts with no checks. That makes "never finish on a draft that wasn't fully checked" a simple
lookup rather than a vague promise.

Because the full state is saved after each step, a run can be **debugged** (every step is in
`letter_run_steps`: tool, args, summary, error, duration) and **resumed** after a pause or crash.

## 4.2 The agent loop (`agent.py`)

Each pass of the loop does this:

```
1. persist state, refresh cost from llm_usage (the orchestrator's own calls count)
2. over a limit?  → finish if can_finish passes, else stop (budget_stopped)
3. ask the orchestrator (mid model): "call the next tool"
4. no tool / unknown tool?             → refuse, count it, loop
5. it called finish?                    → guardrails.can_finish → accept, or refuse with the reason
6. gate says no?                        → refuse, count it, loop  (nothing ran, no tool call charged)
7. run the tool through the runner      → log, time, cost, persist
8. tool failed?                         → stop ("failed")
9. questions now waiting on the user?   → stop ("waiting_user")
10. otherwise loop
```

**What the orchestrator sees each turn: the whole prompt:**

```
STATE
JOB: Data Analyst at Acme
REQUIREMENTS:
  R1 [essential/headline] supported (2 evidence)
  R2 [important/mention] partial, user: have_it (1 evidence)
  R3 [essential/not_for_letter] supported (0 evidence)
LATEST DRAFT: v1 of 3 allowed
  claims: not run on this draft
  requirements: pass
  style: FAIL — em_dash: 2 found, at most 1 allowed ...
BUDGET: tool calls 5/15, drafts 1/3, $0.121/$0.50

STEPS SO FAR: analyze_job, match_profile, generate_letter, check_requirements, style_lint
LAST RESULT: style_lint -> {"draft": 1, "passed": false, ...}

Call the next tool.
```

It's **stateless**: it never sees earlier conversation, never sees the letter text, only this compact
snapshot. That's why each orchestrator turn costs about 1,400 input tokens and about 5 s. And it's why a
run can resume from the saved state alone.

**Refusals are cheap and capped.** A refused call is logged as a step prefixed `refused:` but charges no
tool call. After **4** refusals/text-only replies the run stops, so a confused model can't loop for free.

## 4.3 The guardrails, as a table

All in `guardrails.py` / `registry.py`. Every refusal is a sentence written *for the orchestrator*, naming
what's missing, so it can recover.

| Rule | Enforced by | Plain meaning |
|---|---|---|
| No silent gaps | `drafting_blocked` | Every requirement matched and every must-have gap decided before any drafting. |
| First draft only via generate | `can_generate` | After draft 1, only `revise_letter` makes drafts. |
| One check per draft | `can_check` | Re-running a check on the same text only wastes money (and a model may flip its verdict). |
| Revise only when justified | `can_revise` | All three checks ran, ≥1 failed, and drafts < 3. |
| Finish gate | `can_finish` | All three ran and passed on the **latest** draft. |
| Draft / tool / USD limits | `Budget.exceeded` | 3 drafts, 15 tool calls, $0.50 per run. |
| Account guard | `client.py` | Daily $5 and total $200 caps. Mid/strong calls are blocked at the cap; small is never blocked. |

**How a run ends (`outcome.conclude`).** Both engines end the same way: pick the *best draft*, rank by
(1) passed `check_claims`, (2) fewest missing must-covers, (3) passed `style_lint`, (4) later draft. Then
`clean` is true only if all three checks passed on it. Anything else comes back with `open_issues`. A run
that claims "done" without a clean draft is converted to `failed`, a safety net for the code
disagreeing with itself.

## 4.4 Workflow vs agent (same tools, different manager)

- **Workflow** (`workflow.py`): a fixed script in code. analyze → match → [ask_user if a gap] → generate →
  3 checks → while a check fails (≤2 times): revise → 3 checks → finish.
- **Agent** (`agent.py`): the same tools, same gates, same ending, but a mid-tier model picks the next
  step each turn.

`letter_engine` defaults to **agent** (your decision, 2026-10-03: you wanted the agent experience for your
next job). The workflow remains a working fallback. The shared entry point is `engines.run_letter` /
`resume_letter`.

**What the head-to-head found.** On 15 ads the agent took the **identical tool path as the workflow on 15
of 15 jobs**, with **0 refusals**, at $0.178 vs $0.184 per letter but ~182 s vs ~145 s (the orchestrator
adds ~47 s and $0.013). Quality was a wash on the blind panel (musts 15 vs 15, claims 12 vs 13, detail 12
vs 12, would-send 0 vs 0).

## 4.5 Pause and resume

- `ask_user` makes the run end as `waiting_user`. No worker holds it. The user answers over HTTP.
- When every question is resolved, status becomes `answered`.
- `resume_letter` reopens the run **with the engine that started it**: loads the state, loads the profile
  *fresh* (so newly confirmed rows are citable), and adds **2 extra tool calls** to the budget (the pause
  spent one, the re-match spends one, so a question-asking run can still afford the same two revisions as one
  that didn't).
- The agent resumes with a note like "the run resumed after the user answered ask_user (R1 have_it); R1 has
  new profile rows: call match_profile".

---

# Part 5. Where optimisation can happen

First, the single most useful fact for thinking about this:

> **71% of a letter's cost is "thinking" tokens, and the Pro writer call alone is 61%.**
> The expensive part is *writing*, not *orchestrating*.

### 5.1 Money and time: where it actually goes (agent-v1, per letter)

| Step | Model | Calls | $ | Share | Seconds |
|---|---|---|---|---|---|
| generate_letter | strong | 1.0 | 0.1015 | 57% | 54 |
| revise_letter | strong | 0.4 | 0.0304 | 17% | 15 |
| check_claims | mid | 1.4 | 0.0219 | 12% | 35 |
| orchestrate | mid | 8.6 | 0.0130 | 7% | 47 |
| match_profile | mid | 1.0 | 0.0099 | 6% | 21 |
| check_requirements | small | 1.4 | 0.0016 | 1% | 12 |
| **Total** | | | **$0.178** | | **182 s** |

At 20 letters a month that's a few dollars at most, so these optimisations matter for **speed and quality**
far more than for the bill. Cost only becomes a real constraint after the trial ends (2026-12-30) when
Flash intro prices double.

My arithmetic (not from the report): the **revision loop as a whole** (the revise call plus the extra
check calls it triggers) is roughly $0.04 of $0.178, about 22%. Anything that makes draft 1 right first
time pays off more than it looks.

### 5.2 Levers, grouped by what they buy

Each lever below is a **hypothesis to test**, not a measured result, unless a number is cited. All can be
tested with `letter_lab.py run` + `cost-report` + `loop-report` + the grading panel.

#### A. Make the writer cheaper or faster

1. **Writer thinking level.** `GEMINI_THINKING_STRONG` sets Pro's thinking budget. Pro averages ~6,500
   thinking tokens per draft call vs ~1,050 output tokens. Lowering the level is a one-line env change.
   *Risk:* quality drops. *Test:* same 15 ads, compare panel grades and `cost-report`.
2. **Caching never hits.** CLAUDE.md notes Gemini implicit caching reports 0 cached tokens on every call.
   The revision is deliberately built to reuse the writer's prefix, and it can't. Input is only ~5.6k tokens,
   so the saving is small. Explicit caching is the thing to try, and only worth it if revisions get common.
3. **Revise on a cheaper tier?** Revisions are ~17% of cost. A mid-tier editor could do a narrow fix.
   *Risk:* narrow edits are where overclaims sneak back in. Weigh against `check_claims` failure rate.

#### B. Fewer wasted loops

4. **Better first drafts** (workflow-v3, in progress elsewhere). Each avoided revision saves the revise
   call, a repeated check_claims, and ~70 s. The panel's top complaint was that body paragraphs recite
   the profile instead of applying it to the employer's work, plus generic closes. That's what v3 targets.
5. **Cheap deterministic fixes for style failures.** Many style failures (an extra em dash) are fixable
   with no model call. Today any failed check, including a one-dash overage, triggers a full Pro revision.
   *Idea:* patch trivial style issues in code (swap an extra em dash for a comma), or run `style_lint` first
   and only call the model for what code can't fix. A code-only patch changes no facts, so it could skip the
   `check_claims` rerun (about $0.017 and 24 s), which a model revision can never safely do.
   *Caveats:* the "all three checks run before revising" rule exists to avoid spending two drafts on one fix,
   so a change here must keep that property. And the patch must really be mechanical: an em dash swap is
   safe, rewording a sentence is not, because that is exactly how a true claim becomes an overstated one.
6. **Enforce `changed_pct`.** It's measured but never limited. A cap would catch "revision" calls that are
   really rewrites.

#### C. Faster wall-clock

7. **Run the three checks concurrently.** They're independent reads of one draft. But `check_claims` is
   ~24 s and the other two are a few seconds, so the saving is only a few seconds per round, and the single
   worker / single DB session design means it needs care. Low value for the complexity.
8. **Drop the orchestrator.** It adds ~47 s and $0.013 and, on 15 of 15 ads, chose the same path as the
   script. You decided to keep the agent as the default, and that's a fair product call; the number is
   here so you can see the price of agency.
9. **`check_claims` thinking.** It's 12% of cost and 35 s, with ~1,700 thinking tokens per call. It runs on
   mid because small missed real overclaims. A middle path is lowering mid's thinking for this task only,
   tested with `letter_lab.py plant` (the planted-claim test) before trusting it.

#### D. Quality gaps to close (from CLAUDE.md, known today)

10. The claims judge passes **frequency claims** ("daily") and "apply my skills in X" on a listing.
11. The writer still copies **one stock line** from the writing sample.
12. The blind panel is **stricter than you** (CLAUDE.md records 8/15 on held-out letters). Decide whose
    standard governs before tuning against it.
13. **Voice on/off comparison** is parked in `future_work/voice-toggle-and-comparison.md`.

#### E. Make the agent's agency worth something

This is the honest big-picture point. The agent chose the **same path as the script on 15/15 jobs** because
the gates leave it almost no valid choice: after `analyze_job` only `match_profile` is allowed, after a
draft only the three checks, and so on. Agency only pays off when there's a **real decision with more than
one good answer**. Candidates:

- **Tools that give it real choices**: Phase 7c's side outputs (`suggest_learning`, `answer_screening`,
  `suggest_resume_tweaks`): *which* to call, and when, depends on the situation.
- **Which failure to fix first** or whether to ask the user about a *partial* match, not just a gap.
- **Recovering from unusual states**: a tool failing, a thin ad, a profile with little evidence.
- The ask_user path has not yet fired in any eval, so the one scenario where agency matters is the least
  tested. A designed eval ad with a genuine must-have gap would show whether the agent handles pause and
  resume as well as the script does.

#### F. Product plumbing (not model tuning, but it gates everything)

- Sidebar question card, idle-loop trigger and resume of `answered` runs (Phase 8).
- Surfacing `eligibility_notes` and `open_issues` in the sidebar.
- Side outputs (Phase 7c): only if you ask.

### 5.3 A suggested order, if it were me

1. Let the v3 writer experiment finish. It targets the biggest quality complaint *and* cuts the biggest cost
   (revisions).
2. Then test a **lower writer thinking level** (cheap to try, big lever on both time and money).
3. Build an eval ad that **forces a must-have gap**, to test pause and resume on the agent.
4. Only then consider deterministic style patches and enforcing `changed_pct`.

---

# Part 6. Reference

## 6.1 File map

| File | What it is |
|---|---|
| `app/llm/letter/agent.py` | The orchestrator loop and its system prompt. |
| `app/llm/letter/workflow.py` | The fixed-order engine (same tools and guardrails). |
| `app/llm/letter/engines.py` | `run_letter` / `resume_letter` / `answered_runs`; `letter_engine` default. |
| `app/llm/letter/outcome.py` | `open_run`, `reopen_run`, `conclude`, `LetterResult`, `GapPolicy`. |
| `app/llm/letter/registry.py` | Tool name → function + model-facing description + gate. |
| `app/llm/letter/guardrails.py` | The rules, and the one definition of must-cover / may-use / do-not-claim. |
| `app/llm/letter/state.py` | `LetterState`, `Requirement`, `Draft`, `Check`, evidence-pointer resolver. |
| `app/llm/letter/runner.py` | Times, logs, costs and persists every tool call. |
| `app/llm/letter/tools/` | `analyze_job`, `match_profile`, `generate` (writer), `revise`, `check_claims`, `check_requirements`, `style_lint`. |
| `app/llm/letter/gap_policy.py` | `ask_user` tool, remembered "no"s. |
| `app/llm/letter/answers.py` | Handling the user's No / Yes / confirm. |
| `app/llm/letter/style.py` | Style guide and voice as prompt text. |
| `app/llm/skills/cover_letter_style/` | `SKILL.md`, `banned_phrases.txt`, `us_to_au.txt`. |
| `app/gaps.py`, `app/api/letters.py` | Remembered "no"s, the to-work-on list, API endpoints. |
| `app/llm/client.py` | Provider abstraction, tiers, `complete_tools`, usage log, budget guard. |
| `scripts/letter_lab.py` | The eval harness (`run --engine`, `cost-report`, `loop-report`, `plant`). |
| `scripts/grading_panel.py` | The blind Opus grading panel. |
| `evals/results/` | Measured results (cost report, planted-claim test, panel grades). |

## 6.2 Where each tool's data lives in the database

| Table | Holds |
|---|---|
| `letter_runs` | One row per run: engine, status, final draft number, cost, tool calls, saved state. |
| `letter_run_steps` | One row per step: tool, args, result summary, error (`refused: ...`), duration. |
| `llm_usage` | One row per model call: tier, tokens, thinking tokens, estimated USD, `run_id`. |
| `gap_decisions` / `gap_sightings` | Remembered "no"s and the counter behind the to-work-on list. |
| `job_listings.requirements_checklist` | The cached `analyze_job` result. |

Run statuses: `running`, `waiting_user`, `answered`, `done`, `budget_stopped`, `failed`.

## 6.3 Glossary

- **Agent / orchestrator**: the model that picks the next tool each turn.
- **Tool**: one narrow capability with a fixed name and no arguments.
- **State / notebook**: `LetterState`, the shared record of the run.
- **Gate**: a code check run *before* a tool; refuses with a reason.
- **Guardrail**: a rule in `guardrails.py` that tools, gates and engines all share.
- **Requirement**: one item from the ad's checklist (R1, R2 ...).
- **Importance**: how much the employer cares. **Letter role**: what the letter does with it.
- **Evidence pointer**: an id like `experience:12#s3` naming one piece of profile text.
- **Supported / partial / gap**: how well the profile backs a requirement.
- **Must-have gap**: an essential, letter-relevant requirement with no evidence and no decision yet.
- **Listing-only**: backed only by a bare skills-list entry; counts as "skills in", not "experience with".
- **Draft**: one version of the letter; checks belong to a draft.
- **Open issues**: what's still wrong with the draft handed back when a run stops short.
- **Tier**: small / mid / strong model size, chosen per call; callers never name a model.
- **Thinking tokens**: hidden reasoning tokens, billed as output; the dominant cost.

## 6.4 Places where the code and the older docs disagree

Small things I noticed while reading. None breaks anything, but they will trip up a reader.

- `check_claims.py`'s header says stage 2 runs on a "small model"; the code uses **mid** (`CLAIMS_TIER`).
- Plan §5.8's example says the checks "run in parallel"; both engines run them **one after another**.
- `registry._gate_finish` always returns "ok" and is never the real gate; the loop uses `can_finish`.
- `revise.py` documents a "reject if it changes more than X%" rule as a TODO; only `changed_pct` is measured.
- The cost table has no `analyze_job` row (cached on those ads), so real first-run cost is slightly higher.
