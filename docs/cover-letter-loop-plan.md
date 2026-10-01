# Cover-letter agent + Gemini migration — PLAN (living document)

> **Status: DRAFT v0.3 — 2026-10-01. Rebased onto an agent design. Open to change.**
> **Budget window:** new Google Cloud $300 trial, ~2026-10-01 → ~2026-12-30.
> That ends before Gemini Flash prices double on 2027-01-01, so the intro
> prices in §3 hold for the whole trial.
>
> v0.2 planned two hard-coded review loops (employer critic, then style critic,
> then one writer). v0.3 replaces that with **one agent that calls tools** until
> a letter passes the requirements and claim checks, or a budget runs out. The
> design basis is "Job Application Agent: Design Plan" (2026-10-01). This file
> fits that design onto this codebase.
>
> This is a starting point, not a spec. Sections marked 🧪 are experiments
> whose results may rewrite them. Numbers marked *(est.)* are guesses until
> Phase 1's usage logging replaces them. When a decision is made or a test
> changes the plan, edit this file and add a line to the **Decision log**.

---

## 1. Goals

1. **Better, employer-specific cover letters that never overclaim.** The
   letter covers every must-have the profile can *honestly* support, and makes
   no claim the profile does not support. It reads like the user wrote it.
2. **The model decides the path; code enforces the rules.** An orchestrating
   model picks which tool to call next. Anything that must always hold (no
   unsupported claims, a draft limit, both checks passing before finish) is
   checked in code and never left to the model.
3. **Build the workflow first, then the agent, and compare them.** The same
   tool functions run in both a fixed workflow and the agent loop. An eval set
   and a pass/fail rubric decide which one ships (§9). The workflow may win for
   the letter itself, and that would be a useful finding.
4. **Move all LLM calls to Google Gemini**, using the $300 trial credit.
5. **Stay cost-aware.** Use the strong model only where it changes the result,
   and log every call.
6. **Side outputs**, each behind its own toggle: answers to screening
   questions, learning suggestions for real gaps, and resume tailoring notes.

### Non-goals (for now)
- Auto-submitting anything. The user reviews and submits every application.
- Generating a per-job CV. `user_cvs` is user-level by design. Resume help is
  advice only (§5.6).
- Optimising against an "AI-detector %". Detectors are unreliable, and chasing
  them makes writing worse. The targets are specific, true, and sounds like the
  user. The user's own final read is the last check.
- Adopting an agent framework (LangGraph etc.). Write the loop by hand first,
  so we understand what a framework would do for us.

---

## 2. What exists today (the baseline)

| Piece | Today |
|---|---|
| Provider | OpenAI: `gpt-5-nano` (JSON tasks), `gpt-5-mini` (letters) |
| Letter generation | `app/llm/cover_letter.py`: one `complete_text` call, no checks |
| Letter input | name, quals, experiences + `experience_skills` evidence map, extracted job **summary / responsibilities / skills**, match reasoning + gaps |
| Letter input, missing | **the raw job ad** (`raw_description`), company context, samples of the user's own writing |
| Job analysis | `extract.py` (small model, every scanned job): `job_skills`, requirements JSON, seniority, summary |
| Matching | `match.py`: one score + reasoning + a `gaps` list. No per-requirement evidence |
| When | Idle loop: letters for matches ≥ `auto_cover_letter_min_score` (75), or `/jobs/{id}/regenerate` |
| Storage | `cover_letters` (one per match): `generated_content`, `edited_content`, `status` |
| Cost tracking | None. `client.py` records no token usage |
| Tool calling | None. `client.py` only has `complete_json` / `complete_text` |

The current letter prompt never sees the original job ad, only our summary of
it, so the employer's wording and must-haves are lost before writing.
`analyze_job` (§5.2) fixes this.

---

## 3. Model selection (Gemini)

Prices from https://ai.google.dev/gemini-api/docs/pricing, checked 2026-10-01,
paid tier, prompts ≤200k tokens, per 1M tokens. **Re-check before locking in.**
Intro prices on 3.6–3.8 Flash **double on 2027-01-01**. Thinking tokens are
billed as output.

| Model ID | Status | In / Out per 1M | Notes |
|---|---|---|---|
| `gemini-3.1-flash-lite` | Stable | $0.25 / $1.50 | Shutdown 2027-05-07 → `gemini-3.5-flash-lite` ($0.30 / $2.50) |
| `gemini-3.8-flash` | Stable | $0.75 / $3.75 (intro) | Newest Flash; $1.50 / $7.50 from 2027-01-01 |
| `gemini-3.1-pro-preview` | **Preview** | $2.00 / $12.00 | Strongest; preview, so it may change or be rate-limited |
| `gemini-2.5-*` | Restricted | — | "Limited to existing users"; don't build on it |

Also available: Batch API (50% off, latency up to hours), context caching,
Google Search grounding (5,000 free requests/month, then $14 per 1,000).

### Tiers (🧪 validate in Phases 2–6)

The v0.2 tiers were `small / critic / writer`. The agent design moves the
checks to the cheap model, so a "critic" tier no longer fits. New names:

| Tier (env var) | Default model | Used by |
|---|---|---|
| `small` (`GEMINI_MODEL_SMALL`) | `gemini-3.1-flash-lite` | quick-screen, extract, match score, search re-rank, `check_claims`, `check_requirements`, `suggest_learning` |
| `mid` (`GEMINI_MODEL_MID`) | `gemini-3.8-flash` | orchestrator, `analyze_job`, `match_profile`, `answer_screening`, resume notes |
| `strong` (`GEMINI_MODEL_STRONG`) | `gemini-3.1-pro-preview` | `generate_letter`, `revise_letter` |
| (code) | — | `style_lint`, `finish`, `ask_user`, all guardrails |

The design basis puts `analyze_job`, `match_profile` and the orchestrator on
the strong model. Here they start on `mid` because they produce structured
output and run more often. Things to test:
- **`analyze_job` / `match_profile`: mid vs strong.** Compare requirement
  lists and evidence mapping on the eval set. If mid misses must-haves or maps
  weak evidence, move them up.
- **Orchestrator: mid vs strong.** It makes a call at every step, so it is the
  biggest overhead cost. Its context is a state *summary* (§5.4), so mid
  should be enough. Check that it picks sensible next steps.
- **Checks on `small`.** Claim checking takes judgement. Flash-Lite may be
  too lenient. Measure how many planted false claims it catches (§9); if it
  misses them, move `check_claims` to `mid`.
- **Writer: strong vs mid.** If a checked and revised Flash letter is as good
  as a Pro letter in a blind read, use Flash and save about 3×.
- **The checker is not the writer.** A different model checks than writes, to
  reduce self-grading bias.

---

## 4. Phase 1: Switch the provider to Gemini (tiers, tool calling, cost logging)

All of this lives in `app/llm/client.py`, which stays the **one** place that
knows about providers and models.

1. **Tiers.** `complete_json(..., tier="small")`,
   `complete_text(..., tier="strong")`. Valid tiers: `small` / `mid` /
   `strong`. Each provider maps tiers to models from env. OpenAI stays a
   dormant fallback (`small` → `OPENAI_MODEL_SMALL`, `mid` and `strong` →
   `OPENAI_MODEL_LETTER`). Callers name a tier, never a model.
2. **Tool calling (new).** Add `complete_tools(system, messages, tools,
   tier)`. It returns either a tool call (name + JSON args) or text. Tool
   definitions are provider-neutral (name, description, JSON schema) and
   `client.py` translates them into Gemini function declarations. Nothing
   outside `client.py` imports a provider SDK. This could wait until Phase 7,
   but putting it here means one provider rewrite instead of two.
3. **Default provider = `gemini`.** Update `.env.example`, CLAUDE.md's LLM
   Layer section, and `scripts/check_llm.py` (check all three tiers and one
   tool call).
4. **Structured output.** Keep the Gemini `response_schema` path (Pydantic).
5. **Temperature and thinking.** ⚠️ *Verify against current docs:* Gemini 3
   guidance has been to leave temperature at the default 1.0, because lowering
   it can cause looping. If that still holds, drop `temperature` for 3.x as
   `_generate_openai` already does for GPT-5. Set thinking per tier: low for
   `small`, medium for `mid`, high for `strong`. Thinking is the biggest cost
   lever.
6. **Usage and cost logging.** Every call records task, tier, model, input /
   output / thinking / cached tokens, estimated USD, duration and timestamp in
   a new `llm_usage` table, with an optional `run_id` so per-run cost can be
   summed (§7). Prices sit in one dict in `client.py`.
7. **Budget guard.** `llm_daily_budget_usd` / `llm_total_budget_usd` in
   `profiles.preferences`. When a cap is hit, `mid` and `strong` calls raise
   `BudgetExceededError`. The idle loop backs off as it does for
   `DailyQuotaError`, and the sidebar shows a banner. `small` keeps running so
   scanning still works. This protects against a runaway loop, which is a
   bigger risk than normal spend.
8. **Rate limit.** Raise `LLM_RPM` (e.g. 30) after checking the real quota in
   the Cloud console. Keep the single-worker executor.
9. **Prompt order for caching.** Put the identical blocks first (system
   prompt, tool definitions, profile, job ad) and the per-call content last,
   so repeated calls in one run hit Gemini's implicit prefix cache. Check this
   with the `cached` token counts.
10. **Score drift on switchover.** Gemini will score on a different scale from
    gpt-5-nano, and the suggestion miner and the 75 threshold depend on that
    scale. Leaning towards bumping `profiles.profile_revised_at` at switchover,
    which reuses the existing 0.35 down-weighting of older scores. Decide in
    Phase 1.

**Done when:** pytest passes; `check_llm.py` passes on all tiers and one tool
call; `check_matching.py` bands hold on Gemini; one real scan → extract →
match → letter run writes `llm_usage` rows with plausible costs.

---

## 5. The agent design

### 5.1 Core concepts

| Term | Meaning | Here |
|---|---|---|
| **Workflow** | Code decides the order of steps. Predictable, cheap, easy to debug | v0.2's two loops; the Phase 6 baseline |
| **Agent** | A model decides the order, calling tools until a goal is met. Flexible, but costs more and varies between runs | Phase 7 |
| **Tool** | A function the agent can call: name, description, typed inputs, structured output. It may call an LLM or just run code | `generate_letter`, `check_claims`, `style_lint`, … |
| **Skill** | Instructions and reference material loaded when relevant. Not a function; it shapes *how* something is done | The cover-letter style guide + voice samples |
| **State** | One object every tool reads and writes. The run's memory | `LetterState` (§5.3), persisted per run |

**The split that matters most:** the model decides the path; code enforces the
rules (§5.7).

### 5.2 Tools

Each step that v0.2 handled as a loop stage becomes one tool with one job, on
the cheapest implementation that works.

| Tool | Job | Reads | Writes | Runs on | Maps to existing code |
|---|---|---|---|---|---|
| `analyze_job` | Turn the ad into a requirements checklist (id, text in the employer's words, `must`/`should`), plus tone, keywords to mirror, and any screening questions in the ad | `raw_description` + extracted fields | `requirements`, `job.*` | mid, structured | Builds on `extract.py`. **Cached on the job** (it depends only on the job), so it runs once per job, not once per run |
| `match_profile` | For each requirement, find evidence pointers into the profile and mark it `supported` / `partial` / `gap` | requirements, profile | `requirements[].evidence`, `.status` | mid, structured | New. `match.py` stays the cheap whole-job score that decides *whether* to write a letter |
| `ask_user` | Ask the user about must-have gaps or unclear facts | gap requirements | `user_questions`, `user_decision` | code + sidebar UI | New. Pauses the run (§5.5) |
| `generate_letter` | Write the first draft from supported evidence only, with the style skill loaded. Returns the text **and** a list of claims, each with its source pointer | evidence, decisions, style skill | new `drafts[]` entry | strong | Replaces the one-shot prompt in `cover_letter.py` |
| `revise_letter` | Make targeted fixes to the latest draft for the failed checks only. An edit, not a rewrite | latest draft, failed checks | new `drafts[]` entry | strong | New |
| `check_claims` | Verify every factual claim against the profile (two-stage, below) | latest draft, profile | `checks.claims` | code + small | New |
| `check_requirements` | Confirm every supported must-have is still addressed | latest draft, requirements | `checks.requirements` | small | New |
| `style_lint` | Em dashes, banned phrases, word count, paragraph count, sentence-length variance, every paragraph starting with "I", placeholders like `[Company]`, sign-off present | latest draft | `checks.style` | **code only** | New: `style_lint.py` |
| `answer_screening` | Draft answers to screening questions from the profile | questions, profile | `side_outputs.screening_answers` | mid | New. **Usually blocked:** Seek shows most screening questions in the apply flow, not the ad (§10 Q10) |
| `suggest_learning` | Suggest ways to close real gaps (e.g. a Power BI course) | confirmed gaps | `side_outputs.learning_suggestions` | small | New |
| `suggest_resume_tweaks` | Resume tailoring notes (§5.6) | requirements, evidence, final draft | `side_outputs.resume_notes` | mid | Was v0.2 §5.4 |
| `finish` | End the run. Accepted only if the guardrails pass | whole state | final output | **code only** | — |

**`check_claims` in two stages.** The writer's own claims list is
self-reported, so the checker must not rely on it alone:
1. *Code:* every declared claim must have a source pointer that resolves to a
   real profile field. A missing or unresolved source fails immediately. This
   part is a lookup and makes no LLM call.
2. *Small model:* extract the factual claims from the letter text
   independently, match each one to a declared claim, and judge whether the
   pointed-to profile text supports it. An undeclared or unsupported claim
   fails. For example, "presented to executives" when the profile says
   "presented weekly reports to team leads".

**What a good tool definition needs** (this is the core skill of agent design):
- A verb name that says what the tool does.
- A description written for the model that says when to use the tool *and when
  not to*. For example, `revise_letter`: "Use after a check fails. Fix only the
  listed issues. Do not use for the first draft."
- Typed inputs and structured JSON outputs.
- Errors that explain themselves. A refused `finish` says why
  ("check_claims has not run on draft 3") so the agent can recover.

`answer_screening`, `suggest_learning` and `suggest_resume_tweaks` do not
depend on the letter. The agent can call them at any point, or code can run
them in parallel after `match_profile`.

### 5.3 The state object (`LetterState`)

A Pydantic model, serialised to JSON and persisted per run (§7). It replaces
passing prose critiques between loops, which is how v0.2's loops could drop
each other's content.

```json
{
  "job": {
    "job_id": 412, "title": "Data Analyst", "company": "Example Pty Ltd",
    "tone": "corporate", "keywords": ["stakeholder reporting", "SQL"],
    "screening_questions": []
  },
  "requirements": [
    {"id": "R1", "text": "Experience with Power BI", "priority": "must",
     "evidence": [], "status": "gap",
     "user_decision": {"choice": "adjacent", "note": "1 yr Tableau, no Power BI", "source": "user"}},
    {"id": "R2", "text": "Strong SQL", "priority": "must",
     "evidence": ["experience:12#s3", "skill:7"], "status": "supported", "user_decision": null}
  ],
  "drafts": [
    {"version": 1, "text": "...",
     "claims": [{"text": "Built SQL reports for 3 teams", "source": "experience:12#s3"}],
     "checks": {
       "claims": {"passed": true, "issues": []},
       "requirements": {"passed": false, "uncovered": ["R4"]},
       "style": {"passed": false, "blocking": ["em_dash"], "warnings": ["low_sentence_variance"]}
     }}
  ],
  "user_questions": [],
  "side_outputs": {"screening_answers": [], "learning_suggestions": [], "resume_notes": null},
  "budget": {"drafts_used": 1, "max_drafts": 3, "tool_calls": 6, "max_tool_calls": 15, "cost_usd": 0.07}
}
```

Design choices:
- **Requirements are a checklist with ids, not prose.** After a revision,
  coverage is rechecked by id instead of by rereading everything.
- **Evidence points into the profile by stable database id, not list
  position.** Positions shift when the profile is edited; ids don't. Pointer
  grammar *(draft)*: `experience:<id>`, `experience:<id>#s<n>` (the n-th
  sentence of `Experience.description`; experiences have free-text
  descriptions, not bullets, so code splits them into sentences),
  `qualification:<id>`, `skill:<id>`, `profile:summary`, `user_decision:<req
  id>` (a fact the user gave via `ask_user`). A resolver in `state.py` turns a
  pointer into text. That makes claim checking a lookup.
- **Checks belong to a draft version.** A pass on draft 2 says nothing about
  draft 3. The finish gate reads only the latest draft.
- **`user_decision` records how a gap was resolved** (`have_it` /
  `adjacent` / `leave_out`, plus a note), so a gap is never resolved by
  invention. If the answer is `have_it`, the right fix is to add it to the
  profile, and the `ask_user` UI should offer that.
- **The orchestrator never sees the full state.**
  `summary_for_orchestrator()` gives it requirement statuses, check results
  and budget, not draft text. Tools read the full text from state themselves.

### 5.4 Skill: the cover-letter style guide

Style rules go into the prompt before writing (prevention) and into
`style_lint` after writing (detection). The skill is the prevention half.

`app/llm/skills/cover_letter_style/`:
- **`SKILL.md`**: the rules. Plain, specific sentences. Real project names and
  numbers over adjectives. No em dashes. One clear reason for wanting this
  specific job. Around 250–350 words 🧪 (Q7). Australian spelling 🧪 (Q7).
  `Sincerely, {name}` sign-off. Name the degree and university for recent
  graduates (carried over from today's prompt).
- **`banned_phrases.txt`**: "I am thrilled to apply", "proven track record",
  "fast-paced environment", "leverage my skills", "delve", "tapestry", …
  **The same file feeds `style_lint`**, so the rule and the check can't drift
  apart. The list grows as we spot new ones.
- **`voice_samples/`**: 2–3 short pieces of the user's own writing (Q5). These
  do more against "sounds machine-written" than any check. **Gitignored**,
  because they are personal. Move them to the DB when the app goes
  multi-user.

Later 🧪: learn from the user's edits. `generated_content` vs `edited_content`
shows what the user changes. Summarise recurring edits into style notes (e.g.
"cuts adjectives, prefers 'I built' over 'I was responsible for'") and append
them to the skill.

No grammar tool. Current models rarely make grammar errors. If needed, add
LanguageTool as code inside `style_lint`.

### 5.5 The orchestrator

The orchestrator gets three things instead of a hard-coded sequence:
1. **A goal:** a cover letter that addresses every must-have the profile
   supports, with no unsupported claim.
2. **A definition of done:** on the latest draft, `check_claims` and
   `check_requirements` pass and `style_lint` reports no blocking issues.
3. **A budget:** at most 3 drafts and 15 tool calls (🧪 tunables), plus the
   USD guard. Enforced in code.

System prompt sketch:

```
You are preparing a job application. Your goal is a cover letter that
addresses every must-have requirement supported by the user's profile,
with no unsupported claims.

Work from the state summary. Start with analyze_job and match_profile.
If a must-have is a gap with no user decision, call ask_user before
drafting. Never resolve a gap by writing around it.
After any draft, run check_claims, check_requirements and style_lint.
If a check fails, use revise_letter with only the listed issues.
Call each enabled side-output tool once.
Call finish when all checks pass on the latest draft.
```

The loop is plain code (`app/llm/letter/agent.py`):

```python
while True:
    step = client.complete_tools(SYSTEM, [state.summary_for_orchestrator()], TOOLS, tier="mid")
    if step.tool == "finish":
        ok, reason = guardrails.can_finish(state)
        if ok:
            break
        state.add_tool_result("finish", error=reason)
        continue
    result = run_tool(step.tool, step.args, state)   # guardrails checked inside run_tool
    state.apply(result)
    persist(state)                                   # resumable after a crash or ask_user pause
    if state.waiting_on_user():
        return "waiting_user"                        # frees the worker (see below)
    if state.budget_exceeded():
        state.flag_for_human("Budget reached")
        break
```

**Fitting the agent into this app:**
- **Background, single worker.** Runs execute in the idle loop's single-worker
  executor, like today's cover-letter phase. A run can take minutes *(est.)*,
  so SSE progress events (`letter_run_step`) let the sidebar show what it is
  doing.
- **`ask_user` can't block.** Letters are generated in the background, often
  when the user isn't looking. `ask_user` persists the questions, marks the run
  `waiting_user`, frees the worker, and the sidebar card shows "1 question
  before your letter". Answering resumes the run from its saved state.
  Unanswered questions would mean top matches never get a letter, so see Q9
  for a default.
- **Gap decisions should be remembered across jobs** 🧪. The same gaps ("Power
  BI") will come up across many ads. Storing decisions at profile level, keyed
  by normalised skill name (reusing `prefilter.normalise_skill()`), means the
  user answers once. A per-job override is still possible. See Q9.

### 5.6 Side outputs

- **Screening answers** (`answer_screening`): grounded in the profile, with the
  same evidence pointers. Depends on capturing the questions (Q10).
- **Learning suggestions** (`suggest_learning`): only for gaps the user
  confirmed as real (`leave_out`). Short, concrete (course, cert, small
  project).
- **Resume tailoring notes** (`suggest_resume_tweaks`, decided 2026-10-01):
  advice only, behind `resume_advice_enabled`. When off, no call is made.
  Output: `lead_with[]`, `keywords_to_mirror[]`, `consider_cutting[]`,
  `gaps_to_address[]`. It may only reference experience that exists in the
  profile. Uses `user_cvs` if a CV is stored, otherwise profile experiences.
- Shown as collapsible sections on the job card and the Quick-Apply overlay.

### 5.7 Guardrails (enforced in code, `app/llm/letter/guardrails.py`)

Models sometimes declare victory early, skip a check, or loop. These rules sit
in code so the agent can't get around them. **The fixed workflow uses the same
guardrails**, which keeps the comparison fair.

- **Finish gate.** `finish` is refused unless `check_claims` and
  `check_requirements` have both run and passed on the latest draft, and
  `style_lint` has no blocking issues. The refusal names what is missing.
- **Budget.** Stop at `max_drafts` or `max_tool_calls`, or when the USD guard
  trips. On stop, return the **best draft so far** (claims passing first, then
  most must-haves covered), with its open issues flagged for the user. Never
  return a draft that failed `check_claims` as if it were clean.
- **No silent gaps.** `generate_letter` and `revise_letter` refuse to run while
  a must-have has status `gap` and no `user_decision`.
- **Grounded claims only.** A claim without a resolvable source fails
  `check_claims` automatically (stage 1, code).
- **Revise, don't regenerate.** After draft 1, only `revise_letter` may create
  drafts. 🧪 Optionally reject a revision that changes more than X% of the
  text.
- **Never submits.** The user reads, edits and submits. This hasn't changed.

### 5.8 Example run (Data Analyst ad)

One possible path. On another job the agent might pass on the first draft and
stop after six calls; that flexibility is the point.

| # | Tool | Result | Why |
|---|---|---|---|
| 1 | `analyze_job` | 6 requirements (4 must, 2 should); cache hit if the job was analysed before | Always first |
| 2 | `match_profile` | 5 supported, 1 gap: Power BI (must) | Needs evidence before writing |
| 3 | `ask_user` | Remembered decision: "Tableau 1 yr, no Power BI" → `adjacent` (no pause) | Must-have gap blocks drafting |
| 4 | `suggest_learning` | Power BI fundamentals course | Real gap confirmed |
| 5 | `generate_letter` | Draft 1, frames Tableau honestly | Gaps resolved |
| 6–8 | `check_claims` / `check_requirements` / `style_lint` | Pass / **fail R4** (stakeholder reporting) / 1 em dash + "fast-paced environment" | Required after each draft |
| 9 | `revise_letter` | Draft 2: adds R4, fixes style | Targeted fixes |
| 10 | `check_claims` | **Fail:** "presented to executives" not in profile | Rechecks every draft |
| 11 | `revise_letter` | Draft 3: "presented weekly reports to team leads" (`experience:12#s5`) | Fix only the bad claim |
| 12 | all three checks | Pass | Run in parallel |
| 13 | `finish` | Accepted | Done |

Step 10 is the failure v0.2 was exposed to: adding content to satisfy the
requirements check pushed the writer to overstate. Because `check_claims` runs
on every draft, it is caught and fixed with a narrow edit.

---

## 6. When it runs (decided 2026-10-01, carried over from v0.2)

Preferences in `profiles.preferences`, editable in the Personalise panel:
- **`letter_loop_enabled`** (bool, default `True`): master switch. Off means
  every letter is a one-shot `cover_letter.py` draft and no tools or agent run.
- **`letter_loop_min_score`** (int 0–100, default `85`): matches at or above
  this get the full pipeline automatically. 75–84 get a one-shot draft. If set
  below `auto_cover_letter_min_score`, the effective bar is the higher of the
  two, and the sidebar explains this next to the control.
- **`letter_engine`** (`"workflow"` | `"agent"`, new): which pipeline runs.
  Both share the same tools, so this also makes the §9 comparison and a quick
  fallback possible. The default is set by the Phase 7 comparison.
- **Side-output toggles:** `resume_advice_enabled` (True),
  `learning_suggestions_enabled` (True), `screening_answers_enabled` (True,
  but a no-op until questions are captured).
- *Nice-to-have:* a per-letter **"Polish"** button to run the pipeline on
  demand below the bar.

---

## 7. Data model changes (draft; update docs/database-schema.md FIRST)

The schema doc is the source of truth. Add these there first, then write the
Alembic migrations. Use the portable styles (BigInteger variant,
`text("false")` defaults, CASCADE, JSON stored as `Text`).

- **`llm_usage`** (Phase 1): `id, created_at, task, tier, model,
  input_tokens, output_tokens, thinking_tokens, cached_tokens, cost_usd,
  duration_ms, job_id NULL, match_id NULL, run_id NULL`. Feeds the budget
  guard and the per-run cost numbers. Retention: purge rows older than ~180
  days (open).
- **`letter_runs`** (Phase 3): `id, match_id FK CASCADE, engine
  ('workflow'|'agent'), status ('running'|'waiting_user'|'done'|
  'budget_stopped'|'failed'), state JSON, final_draft_version, tool_calls,
  cost_usd, started_at, finished_at`. The persisted `LetterState`. Several
  runs per match are allowed (regenerate, A/B), so `match_id` is not unique.
  `cover_letters.generated_content` remains the chosen final text, so nothing
  downstream changes. This replaces v0.2's `cover_letter_revisions`, because
  drafts and checks live in the state JSON.
- **`letter_run_steps`** (Phase 3): `id, run_id FK CASCADE, seq, tool, args
  JSON, result_summary JSON, error, duration_ms, created_at`. One row per tool
  call, for debugging ("which decision caused this?") and for eval reports.
- **Requirements cache** (Phase 3): `job_listings.requirements_checklist`
  (JSON text) + `requirements_checklist_at`. It is `analyze_job`'s output and
  depends only on the job, so it belongs on the global job row. (This was
  v0.2's `employer_rubric`.)
- **Gap decisions** (Phase 7, if Q9 says yes): `gap_decisions(id, user_id FK
  CASCADE, skill_key, choice, note, created_at, updated_at)`, UNIQUE
  `(user_id, skill_key)`. `skill_key` is the normalised name and is not
  FK'd to `skills`, consistent with `job_skills`.
- **New preference keys** (no migration; add to `DEFAULTS` in
  `app/preferences.py`): `letter_loop_enabled`, `letter_loop_min_score`,
  `letter_engine`, `resume_advice_enabled`, `learning_suggestions_enabled`,
  `screening_answers_enabled`, `llm_daily_budget_usd`,
  `llm_total_budget_usd` (Q8), `letter_max_drafts` (3),
  `letter_max_tool_calls` (15).

---

## 8. Code layout (proposed)

```
app/llm/
  client.py              # + tiers, complete_tools, usage logging, budget guard
  cover_letter.py        # stays: the one-shot engine (75–84, and loop off)
  letter/
    state.py             # LetterState, pointer resolver, summary_for_orchestrator
    tools/               # one module per tool, each a plain function on state
      analyze_job.py  match_profile.py  ask_user.py  generate.py  revise.py
      check_claims.py  check_requirements.py  style_lint.py
      screening.py  learning.py  resume_notes.py
    registry.py          # tool name -> function + model-facing description/schema
    guardrails.py        # can_finish, gap gate, budget, best-draft selection
    workflow.py          # fixed sequence over the same tools (the baseline)
    agent.py             # orchestrator loop
  skills/cover_letter_style/
    SKILL.md  banned_phrases.txt  voice_samples/ (gitignored)
scripts/
  letter_lab.py          # run eval set × engine, write rubric results + cost to markdown
evals/
  jobs/                  # 10–15 saved ads (fixtures; no profile data committed)
  rubric.md
```

Tools are plain functions that take and return state. `workflow.py` and
`agent.py` are two drivers over the same `registry.py`, so neither has its own
copy of the logic.

---

## 9. Evaluation

Without evals we can't tell whether a change helped or only felt like it did.

- **Eval set:** 10–15 real saved jobs covering strong matches, real gaps,
  screening questions (if any exist), and short and long ads. Snapshot their
  `raw_description` into `evals/jobs/` so the set stays fixed even after
  retention purges the rows.
- **Rubric: pass/fail, not 1–10.** Models grading their own output are lenient
  on vague scales.
  - Every must-have the profile supports is addressed
  - No claim lacks a profile source
  - No banned phrases or em dashes
  - At least one specific detail about the company or role
  - Within the word limit
  - Would the user send it with light edits? (the user's judgement)
- **Planted-claim test** for `check_claims`: take passing letters, insert
  1–2 false claims, and measure the catch rate. This decides whether the check
  stays on `small`.
- **Logging:** every call goes to `llm_usage` + `letter_run_steps`.
- **Comparison table** (filled in by `letter_lab.py`):

| Measure | One-shot (today) | Workflow | Agent |
|---|---|---|---|
| Rubric pass rate | | | |
| Avg tokens / run | | | |
| Avg cost / run | | | |
| Avg time / run | | | |
| Runs that hit the budget cap | | | |
| Runs where the user had to fix a factual error | | | |

The agent pays an orchestrator call at every step, so measure that overhead.
A likely result is that the workflow wins for the letter itself, and agency
pays off on open-ended tasks such as company research or choosing which
listings to apply for. Either answer is worth having.

### Cost per letter *(est.)*. Replace with real numbers after Phase 1
Assumes mid = 3.8 Flash, strong = 3.1 Pro, small = Flash-Lite, moderate
thinking, implicit caching on the shared prefix.

| Step | ≈ Cost |
|---|---|
| `analyze_job` (once per job, cached) | $0.01 |
| `match_profile` | $0.01 |
| Draft or revision (strong) | $0.04 each |
| Three checks (code + small ×2) | $0.003 |
| Orchestrator call (mid, summary context) | ~$0.003 each × 8–15 |
| **Workflow, 3 drafts** | **≈ $0.15** |
| **Agent, 3 drafts** | **≈ $0.18–0.20** |
| Pipeline per scanned job (screen + extract + match, small) | ≈ $0.004 |

Rough 90-day envelope: 3,600 scanned jobs ≈ $15, 300 full-pipeline letters
≈ $60, eval runs (≈15 jobs × 3 engines × several iterations) ≈ $50–100. Well
under $300. A looping bug is a bigger risk than normal spend, which is why the
budget guard comes in Phase 1.

---

## 10. Phases (order matters: each step testable before the next)

| # | Phase | Output | Depends on |
|---|---|---|---|
| 0 | **Decisions + checks** | Answers to Q5–Q10; confirm trial credit + expiry in Cloud Billing; confirm model IDs via `client.models.list()` | — |
| 1 | **Gemini migration** (§4) | Tiers, `complete_tools`, `llm_usage`, budget guard, all callers on Gemini | 0 |
| 2 | **State, eval set, baseline** | `LetterState` + pointer resolver; `evals/` set + rubric; `letter_lab.py`; score today's one-shot letters as the first baseline | 1 |
| 3 | **`analyze_job` + `match_profile`** | Structured outputs, requirements cache, `letter_runs` / `letter_run_steps` tables; checked by hand on the eval set | 2 |
| 4 | **Style skill + `style_lint`** | Skill folder, banned list shared by prompt and lint, voice samples wired in | 2 |
| 5 | **Draft + check tools** | `generate_letter`, `check_claims` (both stages), `check_requirements`, `revise_letter`; planted-claim test | 3, 4 |
| 6 | **Fixed workflow = baseline** | `workflow.py`: analyze → match → (gap default) → draft → checks → revise ≤2 → finish, with the guardrails. Run evals. **Usable on its own; could ship here** | 5 |
| 7 | **Agent** | `ask_user` (+ sidebar question UI, resume), gap memory (Q9), side-output tools, tool descriptions, orchestrator prompt, `agent.py`. Run evals and fill in the comparison table. Set the `letter_engine` default from the result | 6 |
| 8 | **Integration** | Idle-loop trigger (§6), SSE progress, sidebar: final letter + open issues, "not claimed" list, side-output sections, Personalise controls (wired like `auto_cover_letter_min_score`: `DEFAULTS` → `PreferencesUpdate` with bounds → `sidebar.js`) | 7 (or 6 if the workflow ships first) |
| 9 | **Later / optional** | `research_company` tool with search grounding (where agency clearly helps; recruiter-posted ads are a risk); learn-from-edits style notes; "Polish" button; Batch API for the extract/match backlog | 8 |

The baseline and harness come before any agent work on purpose. Without them we
can't tell whether checks, revisions or an orchestrator beat one
well-prompted Pro call.

---

## 11. Risks / things to watch

- **Fabrication creep.** "Cover every requirement" pushes the writer to
  stretch. The no-silent-gaps rule and code-level claim grounding are
  non-negotiable.
- **Lenient checker.** A small-model claim checker that waves through
  overstatements makes the whole design look safe when it isn't. The
  planted-claim test measures this.
- **Agent overhead and variance.** Orchestrator calls add cost and time, and
  two runs on one job can take different paths. The step log and the
  comparison table make this visible.
- **Over-polish / sameness.** Past 2–3 drafts, letters tend to get more
  generic. Keep the draft cap low and watch for this in evals.
- **Paused runs pile up.** If questions go unanswered, high-scoring matches
  wait for letters. Q9's default and gap memory address this.
- **Preview model churn.** `gemini-3.1-pro-preview` may change. `strong` is one
  env var; falling back to 3.8 Flash is a config change.
- **Price change 2027-01-01** (Flash doubles) and **trial expiry
  ~2026-12-30**. Keep the OpenAI path working as a fallback, and re-check
  prices before continuing past the trial.
- **Privacy.** Profile data and voice samples go to Google. Verify the
  paid-tier data-use terms (not used for training at time of writing).
  Voice samples are gitignored.

---

## 12. Open questions

**Resolved 2026-10-01 (from v0.2, still valid):**
- ~~Q1 Trial timing~~: new trial, ~2026-10-01 → ~2026-12-30. Confirm the
  expiry in Phase 0.
- ~~Q2 Trigger~~: automatic at ≥85, tunable, with a master toggle (§6).
- ~~Q3 Company research~~: later, as an experiment (Phase 9).
- ~~Q4 Resume scope~~: tailoring notes only, behind a toggle (§5.6).

**Still open:**
- **Q5 Voice samples.** Can you provide 2–3 pieces of your own writing (an
  old cover letter, a uni essay intro, a long email)?
- **Q6 AI detector.** Drop it entirely (leaning this way, per the design
  basis), or keep a report-only score?
- **Q7 Letter shape.** 250–350 words? Australian spelling? Address a named
  contact when the ad has one?
- **Q8 Budget caps.** Suggested: $5/day, $200 total. Per-run cap? Suggested:
  $0.50.
- **Q9 Unanswered gaps.** When a must-have gap has no decision, should the
  run (a) pause until you answer, or (b) assume `leave_out`, which is always
  honest, write the letter, and show the question so answering triggers a
  re-run? Leaning towards (b) plus profile-level gap memory, so automatic
  letters don't stall.
- **Q10 Screening questions.** Seek shows most of them in the Quick Apply
  flow, not the ad. Capturing them depends on the parked §5.2 apply-flow
  detection (CLAUDE.md), which needs a live session. Until then,
  `answer_screening` only covers questions written into the ad itself.

---

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-10-01 | v0.2: critics critique, one writer revises | Avoid agents undoing each other's edits; fewer expensive calls. *Superseded by v0.3* |
| 2026-10-01 | Measure (harness + baseline) before building | We don't yet know the loop beats one good Pro call. *Still holds* |
| 2026-10-01 | Budget window = new $300 trial, ~2026-10-01 → ~2026-12-30 | Fresh trial; ends before the 2027-01-01 Flash price rise |
| 2026-10-01 | Runs automatically at ≥85; threshold is a preference; master toggle | Polished top matches, tunable bar, easy off switch |
| 2026-10-01 | Resume = tailoring notes only, own toggle | Cheap, no CV storage change; cover-letter-only is possible |
| 2026-10-01 | Company research deferred, experiment only | Recruiter-posted ads make company facts risky |
| 2026-10-01 | **v0.3: rebased onto an agent design** (tools + shared state + orchestrator + code guardrails) | In v0.2 the fixed loops fought, and style edits dropped content the employer loop had added. One state object and per-draft checks prevent that |
| 2026-10-01 | Build the workflow first over the same tools, then the agent; ship whichever wins the comparison | Learn when agency pays for itself; the workflow is a usable fallback |
| 2026-10-01 | Tiers renamed `small / mid / strong`; checks on `small`, orchestrator + analysis on `mid` | The "critic" tier no longer exists; analysis and orchestration start on Flash until evals say otherwise |
| 2026-10-01 | Evidence pointers use stable DB ids (`experience:12#s3`), not list indices | Indices shift when the profile is edited |
| 2026-10-01 | `check_claims` = code source check + independent small-model extraction | The writer's own claims list is self-reported and can't be the only check |
| 2026-10-01 | No agent framework yet; hand-written loop | Understand the plumbing before adopting LangGraph or similar |
