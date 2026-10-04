# Cover-letter agent + Gemini migration — PLAN (living document)

> **Status: DRAFT v0.4 — 2026-10-01. Rebased onto an agent design; Q5–Q10
> answered. Phases 0-6, 7a, 7b and Phase 8 (sidebar + idle loop) built 2026-10-01/04; 7c (side outputs) next. Open to change.**
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

Also available: Vertex batch inference (50% off; all three of our models,
the Pro preview included, on the `global` endpoint; most jobs finish within 24 h
of starting, plus up to 72 h queueing; input is a JSONL file in Cloud Storage or
a BigQuery table). **Checked 2026-10-04 and not worth it at our volume** (about
$0.001 saved per job; §10.2). Also context caching, and Google Search grounding
(5,000 free grounding *queries* a month across Gemini 3 models, then $14 per
1,000; Vertex pricing page, checked 2026-10-04).

### Tiers (🧪 validate in Phases 2–6)

The v0.2 tiers were `small / critic / writer`. The agent design moves the
checks to the cheap model, so a "critic" tier no longer fits. New names:

| Tier (env var) | Default model | Used by |
|---|---|---|
| `small` (`GEMINI_MODEL_SMALL`) | `gemini-3.1-flash-lite` | quick-screen, extract, match score, search re-rank, `check_requirements`, `suggest_learning` |
| `mid` (`GEMINI_MODEL_MID`) | `gemini-3.8-flash` | orchestrator, `analyze_job`, `match_profile`, `check_claims` (moved up 2026-10-03, Decision log), `answer_screening`, resume notes |
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
| `analyze_job` | Turn the ad into a requirements checklist. Each item is rated twice, separately: `importance` (essential / important / nice_to_have: how much the employer cares) and `letter_role` (headline / mention / implied / not_for_letter: what the letter should do with it), plus a `theme`. Also tone, keywords to mirror, screening questions in the ad, and `company_facts` (raw material for a specific detail) | `raw_description` + extracted fields | `requirements`, `job.*` | mid, structured | Builds on `extract.py`. **Cached on the job** (it depends only on the job), so it runs once per job, not once per run |
| `match_profile` | For each requirement, find evidence pointers into the profile and mark it `supported` / `partial` / `gap` | requirements, profile | `requirements[].evidence`, `.status` | mid, structured | New. `match.py` stays the cheap whole-job score that decides *whether* to write a letter |
| `ask_user` | Ask the user whether they have experience that fills a must-have gap, with a text box to answer. "Yes" saves the answer to the profile as a new experience / skill / qualification; "no" means `leave_out` | gap requirements | `user_questions`, `user_decision`, **new profile rows**; a "no" also goes on the to-work-on list (§5.9) | code + sidebar UI (+ small model to structure the answer) | New. Pauses the run (§5.5) |
| `generate_letter` | Write the first draft from supported evidence only, with the style skill loaded. Returns the text **and** a list of claims, each with its source pointer | evidence, decisions, style skill | new `drafts[]` entry | strong | Replaces the one-shot prompt in `cover_letter.py` |
| `revise_letter` | Make targeted fixes to the latest draft for the failed checks only. An edit, not a rewrite | latest draft, failed checks | new `drafts[]` entry | strong | New |
| `check_claims` | Verify every factual claim against the profile (two-stage, below) | latest draft, profile | `checks.claims` | code + mid (was small; Decision log 2026-10-03) | New |
| `check_requirements` | Confirm every supported must-have is still addressed | latest draft, requirements | `checks.requirements` | small | New |
| `style_lint` | The first-version "AI detection" (Q6): em dashes, banned phrases, word count / one-page fit, paragraph count, sentence-length variance, every paragraph starting with "I", American spellings, placeholders like `[Company]`, sign-off present | latest draft | `checks.style` | **code only** | New: `style_lint.py` |
| `answer_screening` | Draft answers to screening questions from the profile | questions, profile | `side_outputs.screening_answers` | mid | New. **Ad text only to start** (Q10): covers questions written into the ad. Reading the live Quick Apply questions is Phase 9 |
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
    "screening_questions": [], "company_facts": ["They build logistics software"]
  },
  "requirements": [
    {"id": "R1", "text": "Experience with Power BI", "importance": "essential",
     "letter_role": "headline", "theme": "BI tooling", "implied_by": [],
     "evidence": ["experience:31"], "status": "partial", "note": "Tableau at uni, not Power BI",
     "user_decision": {"choice": "have_it", "answer": "Used Tableau for 1 year at uni, no Power BI",
                       "saved_as": ["experience:31", "skill:44"]}},
    {"id": "R5", "text": "Driver's licence", "importance": "essential",
     "letter_role": "not_for_letter", "theme": "Eligibility", "implied_by": [],
     "evidence": [], "status": "gap", "user_decision": null},
    {"id": "R2", "text": "Strong SQL", "importance": "essential",
     "letter_role": "headline", "theme": "Data", "implied_by": [],
     "evidence": ["experience:12#s3", "skill:7"], "status": "supported", "user_decision": null},
    {"id": "R6", "text": "Git and CI/CD", "importance": "important",
     "letter_role": "implied", "theme": "Engineering practice", "implied_by": ["R2"],
     "evidence": ["skill:9"], "status": "partial", "user_decision": null}
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
  `qualification:<id>`, `skill:<id>`, `profile:summary`. A resolver in
  `state.py` turns a pointer into text. That makes claim checking a lookup.
  Facts the user gives through `ask_user` are saved to the profile first, so
  they get ordinary pointers and need no special case.
- **Checks belong to a draft version.** A pass on draft 2 says nothing about
  draft 3. The finish gate reads only the latest draft.
- **`user_decision` records how a gap was resolved**: `have_it` (the
  answer was saved to the profile, and `saved_as` lists the new rows) or
  `leave_out`. A gap is never resolved by invention. After a `have_it`
  answer, `match_profile` reruns for that requirement against the new rows. It
  may come back `partial` (e.g. Tableau for a Power BI ask), and the writer
  then frames it honestly as related experience.
- **The orchestrator never sees the full state.**
  `summary_for_orchestrator()` gives it requirement statuses, check results
  and budget, not draft text. Tools read the full text from state themselves.

### 5.4 Skill: the cover-letter style guide

Style rules go into the prompt before writing (prevention) and into
`style_lint` after writing (detection). The skill is the prevention half.

`app/llm/skills/cover_letter_style/`:
- **`SKILL.md`**: the rules. Plain, specific sentences. Real project names and
  numbers over adjectives. No em dashes. One clear reason for wanting this
  specific job. **Must fit on one page; aim for about 250–300 words** (Q7).
  **Australian spelling** (organise, analyse, colour, programme where
  appropriate). Open with "Dear Hiring Manager" for now; addressing a named
  contact can be tuned later. `Sincerely, {name}` sign-off. Name the degree and
  university for recent graduates (carried over from today's prompt).
- **`banned_phrases.txt`**: "I am thrilled to apply", "proven track record",
  "fast-paced environment", "leverage my skills", "delve", "tapestry", …
  **The same file feeds `style_lint`**, so the rule and the check can't drift
  apart. The list grows as we spot new ones.
- **The user's voice (Q5, decided).** This does more against "sounds
  machine-written" than any check. It is user data, so it lives in the DB, not
  in the skill folder:
  1. **Writing sample (preferred).** A new "Your writing" section in the
     profile editor with one large free-text box. The user pastes whatever they
     have (an old cover letter, a uni essay, a long email, a few rambling
     paragraphs). It's a dump: no structure, no minimum, editable any time.
     Stored as `profiles.writing_sample` (§7).
  2. **Fallback: the profile's own text.** If there's no sample, the writer
     uses the user's own words in `Experience.description` and
     `profiles.summary` as the voice reference. This is weaker, because CV-style
     text is terser than how people write letters, so the sidebar nudges the
     user to paste a sample.
  The prompt uses the voice material for tone and rhythm only, never as a
  source of facts. Facts come from evidence pointers.

Later 🧪: learn from the user's edits. `generated_content` vs `edited_content`
shows what the user changes. Summarise recurring edits into style notes (e.g.
"cuts adjectives, prefers 'I built' over 'I was responsible for'") and append
them to the skill.

No grammar tool. Current models rarely make grammar errors. If needed, add
LanguageTool as code inside `style_lint`.

**"AI detection", v1 (Q6, decided):** no external detector. It's two parts:
the skill (prevention, in the writer's context) and `style_lint` (detection,
in code). Lint rules:
- **Blocking:** any em dash; any banned phrase; over the one-page limit
  (proxy: >340 words or >5 body paragraphs 🧪); placeholders; missing
  sign-off.
- **Warnings (passed to `revise_letter`, don't block finish):** under 230
  words; low sentence-length variance; 3+ paragraphs opening with "I";
  American spellings from a small `us_to_au.txt` list (e.g. -ize, -yze, color).

Tune the thresholds from the eval set.

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
- **`ask_user` flow (Q9, decided).** Letters are generated in the background,
  so `ask_user` can't block the worker:
  1. It saves the questions, marks the run `waiting_user`, and frees the
     worker. The sidebar card shows "1 question before your letter".
  2. Each question names the requirement in the employer's words: "This role
     asks for *experience with Power BI*. Do you have experience that covers
     this?" It offers **Yes** with a text box ("Tell us what you did, where and
     roughly how long"), and **No**.
  3. **Yes:** a small-model call turns the typed answer into profile rows. Each
     row is whichever fits: an `experiences` row, a `skills` row, a
     `qualifications` row, and `experience_skills` links between them. The
     sidebar shows what will be saved (🧪 a one-click confirm, so a
     misread answer doesn't land in the profile silently). The rows are written,
     `profiles.profile_revised_at` is bumped, and the run resumes. The new
     facts are then normal evidence for this letter and every future match.
  4. **No:** the requirement becomes `leave_out`. The letter doesn't mention
     it, and `suggest_learning` may suggest a way to close it. The skill is
     saved to the **to-work-on list** (§5.9), which counts how often it shows
     up in later ads.
- **Answers are remembered, so each gap is asked once.**
  - A "yes" is remembered automatically, because it is now in the profile and
    `match_profile` finds it next time.
  - A "no" is stored in `gap_decisions` (§7), keyed by normalised name (reusing
    `prefilter.normalise_skill()`), so the same gap on the next ad is left out
    without asking.
  - The user can clear a remembered "no" in the profile editor (e.g. after
    doing the course). This takes it off the to-work-on list (§5.9).
- **Questions are batched.** All must-have gaps for a run are asked together
  in one card, not one pause per gap.

### 5.6 Side outputs

- **Screening answers** (`answer_screening`): grounded in the profile, with the
  same evidence pointers. **v1 reads only the ad text** (Q10). Reading the real
  questions live from Seek's Quick Apply page is Phase 9 (§10.1), tied to the
  parked apply-flow detection in CLAUDE.md.
- **Learning suggestions** (`suggest_learning`): only for gaps the user
  confirmed as real (`leave_out`). Short, concrete (course, cert, small
  project). When a run has several such gaps, lead with the one highest on the
  to-work-on list (§5.9): it is the one most ads ask for.
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
| 1 | `analyze_job` | 6 requirements; the licence is `not_for_letter`, CI/CD is `implied`; cache hit if the job was analysed before | Always first |
| 2 | `match_profile` | 4 supported, 2 gaps: Power BI (essential headline), driver's licence (eligibility) | Needs evidence before writing |
| 3 | `ask_user` | The licence is never asked about (eligibility is a job-card note, not a letter claim). Power BI: run pauses; the user answers Yes, "Used Tableau for 1 year at uni"; saved as an experience + skill; run resumes; R1 → `partial` | A must-have gap blocks drafting |
| 4 | `suggest_learning` | Power BI fundamentals course | Power BI still not covered directly |
| 5 | `generate_letter` | Draft 1, frames Tableau honestly as related experience; no licence mention | Gaps resolved |
| 6–8 | `check_claims` / `check_requirements` / `style_lint` | Pass / **fail R4** (stakeholder reporting) / 1 em dash + "fast-paced environment" | Required after each draft |
| 9 | `revise_letter` | Draft 2: adds R4, fixes style | Targeted fixes |
| 10 | `check_claims` | **Fail:** "presented to executives" not in profile | Rechecks every draft |
| 11 | `revise_letter` | Draft 3: "presented weekly reports to team leads" (`experience:12#s5`) | Fix only the bad claim |
| 12 | all three checks | Pass | Run in parallel |
| 13 | `finish` | Accepted | Done |

Step 10 is the failure v0.2 was exposed to: adding content to satisfy the
requirements check pushed the writer to overstate. Because `check_claims` runs
on every draft, it is caught and fixed with a narrow edit.

### 5.9 The to-work-on list (saved "no"s + a counter) (added 2026-10-03)

**Goal:** every "No" to an `ask_user` question is a skill or tool the user
doesn't have yet. Save them all, count how often each one shows up in ads, and
rank them. The skills that keep appearing are the ones worth learning or
getting familiar with. A single "no" says little; the count is what makes the
list useful.

**Simple by design:** no LLM calls. One small hook when the user answers "No",
one when a job is scanned, and a count when the list is read.

1. **Saver (on "No").** Save one row per skill to `gap_decisions` (§7): the
   normalised `skill_key`, a readable `label` ("Power BI"), the requirement's
   wording, and when the user said no. If the skill is already on the list,
   don't add a second row. Just record this ad as another sighting. This is the
   same row that stops the gap being asked about again (§5.5).
2. **Counter (sightings).** A sighting is "this ad asked for a skill the user
   said no to". It is recorded in `gap_sightings` (§7), at most once per ad per
   skill, so re-running a job never double-counts. Sightings come from:
   - **Every scanned job.** After `extract.py` writes `job_skills`, match their
     names against the remembered `skill_key`s using `normalise_skill()`. This
     is code only, so it covers every scanned ad, not just the ≥85 matches that
     get a full letter run.
   - **Letter runs.** When `match_profile` leaves a requirement as a gap and
     its key matches a remembered "no", it becomes `leave_out` without asking
     (already planned) and counts as a sighting, with its `importance`.
   - **Seeding.** When a skill is first saved, count the jobs already in the
     DB that ask for it. This way the list is useful straight away ("Power BI:
     you just said no, and it's already in 14 of your scanned ads").
3. **Reading the list.** Rank by the number of distinct ads in the last 90
   days, so the list follows what the market is asking for now. Break ties by
   how many of those ads rated it essential. Each item shows the label, the
   total and 90-day counts, the essential count, the three most recent job
   titles that asked for it, and the date of the "no".
   - `GET /gaps/to-work-on` returns the ranked list.
   - `python scripts/gap_report.py` writes it to `reports/to-work-on.md`, a
     readable file to look over. It is gitignored because it's personal
     profile data.
   - The sidebar's profile editor gets a "To work on" section in Phase 8.
4. **Clearing.** The user clears an item after learning it, for example after
   finishing the course. This sets `cleared_at` rather than deleting, so it
   leaves the list and is no longer auto-left-out, but its sightings history is
   kept. An item clears automatically when the profile gains a skill with the
   same normalised name (e.g. a later "Yes" answer, or adding it in the
   editor).

**Prerequisite: a short skill name per requirement.** Requirements are
sentences in the employer's words ("Experience building dashboards in Power
BI"). Two ads won't word them the same, so counting needs a short name.
`analyze_job` gains a `skill` field per requirement ("Power BI"; empty for
attitudes and other non-skills), and `ANALYSIS_VERSION` is bumped so cached
checklists are rebuilt. A "no" to a requirement with no `skill` is still
remembered, keyed by its normalised wording, but it will rarely recur, which
is correct for one-off asks.

**Not counted (on purpose, for now):** gaps the user was never asked about
(nice-to-haves, eligibility), and `partial` matches. The list holds only skills
the user has said they don't have. Counting every unasked gap is a possible
extension once the basic list proves useful.

**Done when:** a "No" in the sidebar adds the skill once; scanning an ad that
lists it adds exactly one sighting, and re-scanning it adds none; the report
ranks a skill seen in 5 ads above one seen in 2; clearing a skill removes it
from the list without losing its count. Tests cover the dedupe, the 90-day
window and auto-clear.

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
  `learning_suggestions_enabled` (True), `screening_answers_enabled` (True;
  ad text only for now; live Quick Apply questions are Phase 9, §10.1).
- ~~*Nice-to-have:* a per-letter **"Polish"** button to run the pipeline on
  demand below the bar.~~ **Built in Phase 8:** Regenerate runs the full
  pipeline whatever the score when the master switch is on (Decision log
  2026-10-04, Phase 8 design choice 6). No separate button.

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
- **`profiles.writing_sample`** (Text, nullable; Phase 4): the pasted voice
  dump (§5.4). It is a column rather than a preference because it can be long
  and is profile content, not a setting.
- **`gap_decisions`** (Phase 7): `gap_decisions(id, user_id FK CASCADE,
  skill_key, label, requirement_text, created_at, cleared_at NULL)`, UNIQUE
  `(user_id, skill_key)`. It stores remembered **"no"** answers only; "yes"
  answers become real profile rows. `skill_key` is the normalised name and is
  not FK'd to `skills`, consistent with `job_skills`. It is also the
  to-work-on list (§5.9); `cleared_at` takes an item off it without losing
  its history.
- **`gap_sightings`** (Phase 7, §5.9): `gap_sightings(id, gap_id FK →
  gap_decisions CASCADE, job_id, job_title, importance NULL, source
  ('scan'|'letter_run'|'seed'), seen_at)`, UNIQUE `(gap_id, job_id)`. This is
  the counter: one row per ad that asked for a skill the user said no to.
  `job_id` has **no FK** and the title is copied in, so retention purges of
  job rows don't erase the counts (the same reasoning as `llm_usage`).
- **Rows created by `ask_user`** go into the existing `experiences` /
  `skills` / `qualifications` / `experience_skills` tables, tagged with a
  nullable `origin` column (`'ask_user'`, shown as "added while applying to a
  job"; Q12, built in 7b, migration `f6a9c3d8e217`).
- **New preference keys** (no migration; add to `DEFAULTS` in
  `app/preferences.py`): `letter_loop_enabled`, `letter_loop_min_score`,
  `letter_engine`, `resume_advice_enabled`, `learning_suggestions_enabled`,
  `screening_answers_enabled`, `llm_daily_budget_usd` ($5),
  `llm_total_budget_usd` ($200), `llm_run_budget_usd` ($0.50) (Q8: starting
  values, expected to change once real costs are in), `letter_max_drafts`
  (3), `letter_max_tool_calls` (15).

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
    registry.py          # tool name -> function + model-facing description/schema (Phase 7: only the agent needs it)
    guardrails.py        # can_finish, gap gate, budget, best-draft selection
    outcome.py           # open_run / conclude / LetterResult / gap policy: how BOTH engines start and end a run
    workflow.py          # fixed sequence over the same tools (the baseline)
    agent.py             # orchestrator loop
  skills/cover_letter_style/
    SKILL.md  banned_phrases.txt  us_to_au.txt   # voice comes from the DB, not here
app/
  gaps.py                # to-work-on list (§5.9): save a "no", record sightings, rank, clear. Code only
scripts/
  letter_lab.py          # run eval set × engine, write rubric results + cost to markdown
  gap_report.py          # writes the ranked to-work-on list to reports/to-work-on.md (gitignored)
evals/
  jobs/                  # 10–15 saved ads (fixtures; no profile data committed)
  rubric.md
```

Tools are plain functions that take and return state. `workflow.py` and
`agent.py` are two drivers over the same tools (the workflow imports them directly;
`registry.py` arrives with the agent in Phase 7), so neither has its own
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
| Rubric pass rate | user: 0/8 graded; panel opus-v1: 0/15 | v1: opus-v1 2/15, opus-v2 1/15; **v2: opus-v2 0/15, opus-v3 0/15**; v3: opus-v4 0/15 (musts 15, claims 12, detail 13, would_send 0; v2 on the same panel 15 / 11 / 13 / 0) | v1: opus-v3 0/15 (musts 15, claims 12, detail 12, would_send 0; workflow-v2 on the same panel: 15 / 13 / 12 / 0) |
| Avg tokens / run | 867 in, 1,958 out+thinking | v2: 17,149 in, 19,855 out+thinking | v1: 29,168 in, 18,448 out+thinking (orchestrator 12,204 in, 1,037 out+thinking) |
| Avg cost / run | $0.025 | v1 $0.194; **v2 $0.184**; v3 $0.213 (+$0.010 one-off re-analysis) | v1 $0.178, of which orchestrator $0.013 (8.6 calls, 7%); lower than v2 only because the shared writer thought less this run |
| Avg time / run | 19s | v1 138s; **v2 145s**; v3 171s | v1 182s, of which orchestrator 47s |
| Runs that hit the budget cap | n/a | v1 1/15; **v2 0/15** | v1 0/15; 0 guardrail refusals; same tool path as the workflow 15/15 |
| Runs where the user had to fix a factual error | user: 0/9; opus-v1: 13/15 | v1: 9/15; **v2: 2/15** (opus-v2 and opus-v3) | v1: 3/15 (opus-v3) |

Filled 2026-10-03 from `evals/results/baseline-oneshot.md`, `workflow-v1.md` and `workflow-v2.md` (15-ad set). **`workflow-v2` is the baseline the agent has to beat**: grade the agent in a new panel against workflow-v2 (`evals/grading-panel.md`). The Opus panel is a blind model grader, much stricter than the user on claims; compare the engines on its numbers, not its numbers with the user's (`evals/results/grading-opus-v1.md`).

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
| 0 | ✅ **Decisions + checks** | Answers to Q5–Q10; confirm trial credit + expiry in Cloud Billing; confirm model IDs via `client.models.list()` | — |
| 1 | ✅ **Gemini migration** (§4) | Tiers, `complete_tools`, `llm_usage`, budget guard, all callers on Gemini | 0 |
| 2 | ✅ **State, eval set, baseline** | `LetterState` + pointer resolver; `evals/` set + rubric; `letter_lab.py`; score today's one-shot letters as the first baseline | 1 |
| 3 | ✅ **`analyze_job` + `match_profile`** | Structured outputs, requirements cache, `letter_runs` / `letter_run_steps` tables; checked by hand on the eval set | 2 |
| 4 | ✅ **Style skill + `style_lint` + voice** | Skill folder, banned list shared by prompt and lint, AU spelling list; `profiles.writing_sample` + a "Your writing" paste box in the profile editor; experience-text fallback | 2 |
| 5 | ✅ **Draft + check tools** | `generate_letter`, `check_claims` (both stages), `check_requirements`, `revise_letter`; planted-claim test | 3, 4 |
| 6 | ✅ **Fixed workflow = baseline** | `workflow.py`: analyze → match → unanswered gaps treated as `leave_out` (evals only; there is no UI yet) → draft → checks → revise ≤2 → finish, with the guardrails. Run evals. **Usable on its own; could ship here** | 5 |
| 7 | ✅ **Agent + ask_user** (7a ✅ agent + comparison, 2026-10-03; 7b ✅ ask_user + to-work-on list, 2026-10-03 (API only; sidebar card in Phase 8); writer v3 ✅ 2026-10-04 (apply evidence, no stock close; panel unchanged, would_send 0/15); 7c ✅ side outputs, 2026-10-04) | `ask_user` (sidebar question card with Yes + text box / No, answer → profile rows, run resume), `gap_decisions` for remembered "no"s, **the to-work-on list (§5.9): `skill` field on `analyze_job`, `gap_sightings` counter hooked into extraction and letter runs, `GET /gaps/to-work-on`, `gap_report.py`**, side-output tools, tool descriptions, orchestrator prompt, `agent.py`. Run evals and fill in the comparison table. Set the `letter_engine` default from the result | 6 |
| 8 | ✅ **Integration** (built 2026-10-04; the side-output sections landed with 7c the same day; see the Decision log) | Idle-loop trigger (§6), SSE progress, sidebar: final letter + open issues, "not claimed" list, side-output sections, a "To work on" section in the profile editor (ranked list with counts + clear button, §5.9), Personalise controls (wired like `auto_cover_letter_min_score`: `DEFAULTS` → `PreferencesUpdate` with bounds → `sidebar.js`) | 7 (or 6 if the workflow ships first) |
| 9 | **Help with the live Quick Apply questions + a question bank** (9a DONE 2026-10-04: capture + bank, no LLM; 9b/9c next) (§10.1; rescoped 2026-10-04 from 5 sampled questionnaires, `docs/quick-apply-samples.md`) | Read the questions on the Quick Apply page the user opened; `user` questions are listed and never sent to the LLM; `assisted` ones get an answering strategy (years from dates, skill in a role, multi-select, free-text draft) with **what the employer wants** beside **what you have**, and wanted-but-missing skills go through the `ask_user` gap memory. Every question is stored in a **question bank**, so a repeat is recognised without a model call and only new ones are classified (once). 9a capture + bank (no LLM), 9b assist, 9c drafts + evals. The other old Phase 9 items moved out: company research, framing/addressing and learn-from-edits to `future_work/`; the "Polish" button is built (Regenerate, Phase 8); Batch API checked and not worth it (§10.2) | 8 |

The baseline and harness come before any agent work on purpose. Without them we
can't tell whether checks, revisions or an orchestrator beat one
well-prompted Pro call.

### 10.1 Phase 9: help with the live Quick Apply questions

*Rescoped 2026-10-04 (user), revised the same day from 5 real questionnaires (22 questions)
collected in `docs/quick-apply-samples.md`. That file is the evidence for everything below:
markup, id families, question kinds. Read it first.*

**Why.** `answer_screening` (7c) only sees questions written into the ad text (Q10). On Seek
the real questions sit in the Quick Apply flow (`/job/{id}/apply/role-requirements`, step
"Answer employer questions"). Most are not the app's to answer: 17 of the 22 sampled were
personal or logistics questions. The rest relate directly to the ad (languages, years in a
role, a skill "in a role"), and for those the app already has what it needs: the requirements
checklist cached on the job (`analyze_job`) and the per-requirement evidence (`match_profile`).

**Goal.**
1. Help with every **`assisted`** question; show **`user`** questions as "yours to answer"
   and never send them to the LLM.
2. Keep a **question bank**: every question the extension reads is stored, so a question seen
   before is recognised next time (by id or text) with its kind and answering strategy already
   known. Only genuinely new questions cost a model call, and once, not per job.

#### The two kinds

| Kind | Seen in the samples | What the app does |
|---|---|---|
| `user` | work rights (all 5 jobs, Seek's 11-option list or the employer's own wording), salary (4 jobs: bands, single figures or free text), notice period, work arrangement (in office, hours, WFH), how you heard, Indigenous identity, motivation, child-safety legal declarations | Listed as "yours to answer". No AI help; never sent to the LLM |
| `assisted` | years' experience as a role (dropdown, 2 jobs), "worked in a role which requires C#" (yes/no), "which programming languages are you experienced in" (checkboxes), years with a specific skill (free text, employer-written) | The two views below |

#### The two views of an assisted question

1. **What the employer wants** (the best-candidate answer), from the requirements checklist:
   which options the ad asks for, how much (`importance`), the ad's own words as a quote; for
   years, any figure the ad states.
2. **What you have**, from the profile evidence: which options your experience backs, and for
   years the figure your experience supports.

Per option: **Wanted · you have it** (tick it) / **Wanted · not in your profile** (a gap: ask,
below) / **You have it** (optional, honest either way) / no label.

**Wanted but missing → ask the user**, through Phase 7b's gap memory (`app/gaps.py`
`skill_key`, `find_decision`): a remembered "no" shows "you said you don't have this" and isn't
asked again; otherwise the Yes + text / No card; Yes → proposed rows → confirm (Q11),
`origin='ask_user'`, labels recompute; No → remembered. Record a gap sighting either way (§5.9).

**Honesty rule (code-enforced).** "Wanted" is information, never advice to claim something.
"You have it" needs an evidence pointer. Specific beats general: "worked in a **role** requiring
C#" needs work experience, not a course or a bare skill listing (the letter's `listing_only`
rule); "React Native with Expo" is not React.js. Years are never rounded up. The app never
ticks, types or submits anything on Seek's page.

#### Answering strategies (one per assisted question)

| Strategy | Example | Answer comes from |
|---|---|---|
| `years_role_bracket` | "How many years' experience do you have as a full stack developer?" (8 fixed brackets: No experience / Less than 1 year / 1, 2, 3, 4, 5 years / More than 5 years) | code: years from the dates of experiences matching the role, merged for overlaps, mapped to a bracket rounding **down** |
| `years_skill_text` | "How many years of experience … React Native with Expo …?" (free text) | code computes the years for that skill; a short honest sentence (template or mid-tier draft); none → gap |
| `skill_in_role_yes_no` | "Have you worked in a role which requires C# development experience?" | code: work-experience evidence for the skill → Yes; otherwise gap |
| `skill_multi_select` | "Which of the following programming languages are you experienced in?" | code: each option → checklist (wanted) and profile (have), via `normalise_skill` |
| `free_text_describe` | "Describe your experience with SQL" (not seen yet) | `answer_screening` (mid) draft from the same evidence, with Copy |
| `user` | everything in the `user` row above | nothing |

New strategies get added as the bank shows new question shapes.

#### Sorting a question, cheapest first

1. **Question bank hit.** Seen before (same Seek library id, or same normalised text, type
   and options) → reuse its kind, strategy and parameters. No model call.
2. **Seek library id table** in code: `AU_Q_6` work rights, `AU_Q_8` salary, `AU_Q_13`
   notice → `user`; `AU_Q_136` languages → `skill_multi_select`; `AU_Q_218` C# in a role →
   `skill_in_role_yes_no`. Confirmed stable across jobs for `AU_Q_6` and `AU_Q_13`.
3. **`user` keyword filter** on the text, for any wording (S5 asks work rights and salary in
   its own words): work rights / visa / citizen, salary / pay, notice / start date, office /
   hours / WFH, how did you hear, Aboriginal / Torres Strait / gender / disability, criminal /
   findings / compliance / declarations, motivated. Runs before any model call.
4. **Text templates** for Seek's generated per-role questions (their ids are a 32-hex hash per
   role, so ids don't repeat): "How many years' experience do you have as (a/an) <role>?",
   "Have you worked in a role which requires <skill> experience?".
5. **Small model, once per new question**, for what's left (employer-written technical
   questions). It returns kind, strategy and parameters (the skill or role asked about); code
   checks they are words from the question. The result goes into the bank.

#### The question bank (new)

Every captured question is upserted into the bank; questions are not personal data, so the
bank is shared across users when the app goes multi-user. Never stored: what was selected or
typed (Seek pre-fills answers), the user's name or anything under `[data-adora-mask]`.

- **Identity:** the Seek library id when there is one (`AU_Q_<n>`, version kept separately),
  otherwise a fingerprint of normalised text + type + option texts. Generated per-role ids
  (`AU_Q_<32 hex>`) and employer ids (`indirect_...`) are per job, so they're recorded on the
  job link, not used as identity.
- **Stored per question:** text, type, options, kind, strategy, parameters, how it was
  classified (`library_id` / `keyword` / `template` / `model` / `user`), `status`
  (`new` → `confirmed` when the user agrees or corrects it), times seen, first/last seen.
- **Per job:** which bank questions the job's form had, in order, with Seek's field name and
  option values (option values repeat across questions, so they're scoped by question).
- **Review:** the sidebar (or the profile editor) lists `new` questions so the user can confirm
  or correct kind and strategy; a correction is remembered for every later job.
- **Export:** a script writes the bank to Markdown in the shape of
  `docs/quick-apply-samples.md`, so the samples doc and the 9c eval set stay current without
  copy-pasting HTML.
- Schema: two new tables (e.g. `screening_questions`, `job_screening_questions`), portable as
  usual. Update `docs/database-schema.md` **first**.

#### Capture (content script; settled by the samples)

Group the `<form>`'s fields by `name="questionnaire.<qid>"`. Radio: `fieldset[role=radiogroup]`
+ `<legend>`; dropdown: `select` + `label[for]`; free text: `textarea` + `label[for]`;
checkboxes: the first `<strong>` in the container holding the inputs, option ids on the inputs.
Skip empty-value placeholder options; trim labels; never read `checked`, `selected` or typed
values. Detect the step with `nav[aria-label="Progress bar"]` + `aria-current="step"`; job id
from the URL. Class names are generated: never select on them.

**Policy fit.** A pure read of the DOM of a page the user opened and clicked through. No
navigation, no clicks, no second hop, no calls to Seek's APIs, nothing filled in or submitted
(CLAUDE.md, Seek access policy). Applications that leave Seek for an employer's site are out of
scope.

#### Flow

1. *Capture* on the questions step → 2. `POST /jobs/{id}/screening-questions` (validate, upsert
into the bank, link to the job; no LLM) → 3. *Sort* (bank → id table → keywords → templates →
model for new ones) → 4. *Assist* (strategies above; gaps → ask) → 5. *Show* in the
Quick-Apply overlay on the apply page and on the job's card in the sidebar: each question with
its kind, the two views per option, gap questions, Copy on drafts.

**Decided leanings (revisit if 9a/9b says otherwise).** Runs **outside the letter run** (the
user reaches Quick Apply after the letter exists). Questions live in the bank and are linked
**per job**; labels and drafts are **derived** and recomputed when the profile changes. The
ad-text `answer_screening` path in the letter run stays as is.

**Open questions (decide while building).**
- Gap questions outside a run: `app/llm/letter/answers.py` takes a `run_id`; a run-less path
  is needed. Does a "Yes" here also reopen the letter?
- No honest option on a multiple-choice question: "none fits, answer yourself", or point at
  the lowest option and flag it?
- Jobs with no `match_profile` result (below the letter bar): spend ≈ $0.02 to analyse on
  demand, or free name matching on `job_skills` only?
- Motivation questions: `user` by default; an opt-in "draft from my cover letter" later?
- Is `AU_Q_218` (C# in a role) one id per skill or one shared id? A second sample will tell.

#### Steps (each committed, with the user's go-ahead between, as in Phase 7)

- **9a: capture + bank (no LLM).** Selectors, capture, endpoint, the two tables, sorting
  layers 1–4 (no model), the overlay listing each question with its kind, the review list and
  the export script. **DONE 2026-10-04** (Decision log): migration
  `b4d8e2f6a913`; `app/screening/` (`identity.py`, `sort.py`, `bank.py`); `app/api/screening.py`
  (`POST`/`GET /jobs/{id}/screening-questions`, `GET /screening-questions?status=new`,
  `PATCH /screening-questions/{id}`); capture + overlay in `content_script.js`; review list in
  the profile editor; `scripts/export_question_bank.py`; `tests/e2e/quick_apply_e2e.py`.
- **9b: assist.** Layer 5 (small model, once per new question), the strategies, years from
  dates, wanted/have labels, the gap → ask path, display.
- **9c: drafts + evals.** `free_text_describe` / `years_skill_text` drafts through
  `answer_screening`; an eval set built from the bank (kind accuracy, where a `user` question
  reaching the model is a failure; strategy accuracy; no "you have it" without evidence).

#### Verification

1. *No cost first:* Playwright with the unpacked extension against a scratch test DB on port
   8001 (`run_api.py test`, never real.db). The apply page is served from scrubbed fixtures
   built from the five samples through request interception (no request reaches Seek); the LLM
   is stubbed. Check capture, bank upserts (a repeat question is recognised, not duplicated),
   sorting, that `user` questions never reach the stub, labels on a known profile/ad pair, the
   gap loop, and that the extension makes no navigation or click.
2. *Eval (9c, small spend with the user's OK).*
3. *One live check the user drives* against the **test** backend. Record it in the Decision log.

### 10.2 Batch API for the extract/match backlog: checked 2026-10-04, not worth it

Was an old Phase 9 item. Vertex batch inference **works for us**, but at our
volume it would save about **$4 over the whole trial**, it delays scores by up
to a day, and it needs new moving parts. Not built. Sources are Google's
official docs, read 2026-10-04.

| Question | Finding | Source |
|---|---|---|
| Our models supported? Preview excluded? | Yes, all three. The supported list includes Gemini 3.8 Flash, Gemini 3.1 Pro *preview* and Gemini 3.1 Flash-Lite. Preview models are not excluded; only *tuned* Gemini 3+ models are. The list gives display names, not ID strings (the IDs match ours, but that mapping is mine) | [Batch inference with Gemini](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/batch-prediction-gemini) (last updated 2026-10-02) |
| Discount | 50% off online prices. Flex/Batch table, global: 3.1 Flash-Lite $0.125 / $0.75 (online $0.25 / $1.50); 3.8 Flash $0.375 / $1.875 to 2026-12-31; 3.1 Pro preview $1.00 / $6.00. Doesn't stack with implicit caching (the 90% cache discount wins), which doesn't hit for us anyway (future_work/gemini-prompt-caching.md) | Same page; [Vertex pricing](https://docs.cloud.google.com/vertex-ai/generative-ai/pricing) ("Gemini models are available in batch mode at 50% discount") |
| `GOOGLE_CLOUD_LOCATION=global`? | Yes, the global endpoint works for base models (not tuned ones). A BigQuery output dataset must be in the same region as the job | Batch page; [Batch from Cloud Storage](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/batch-prediction-from-cloud-storage) |
| Formats | Input: one JSONL file in Cloud Storage (one `{"request": GenerateContentRequest}` per line) or a BigQuery table. Output: JSONL in Cloud Storage (each line has `status`, the request, and `response` with `usageMetadata`) or a BigQuery table. The GenAI SDK's `client.batches.create(model=..., src="gs://...", config=CreateBatchJobConfig(dest=...))` covers it. No inline-request input on Vertex (inline lists are a Developer API feature) | Batch from Cloud Storage |
| Latency | "Most jobs complete within 24 hours after it starts running (not counting the queue time)". A job can queue up to 72 h before it expires; work unfinished at 24 h is cancelled and only completed requests are billed | Batch page |
| Limits / quota | Up to 200,000 requests per job; 1 GB input file from Cloud Storage. No predefined quota: a shared pool, so jobs may queue. Not a covered SLA service. No explicit caching or Provisioned Throughput | Batch page |
| Trial credit covers it? | The $300 credit excludes only "Gemini API in AI Studio costs" and partner models sold as a managed API. Vertex AI and Cloud Storage aren't excluded, so batch and its bucket are covered. **Inferred**: no page names batch explicitly. Trial accounts can't request quota increases, but batch has no quota to raise | [Free Trial features](https://docs.cloud.google.com/free/docs/free-cloud-features) (last updated 2026-09-30) |
| Does the trial or the org block it? | Nothing on the trial page blocks it. **Unverified**: whether the project's org policies (the same org that blocks API keys) allow creating a bucket, or restrict resource locations; and whether the AI Platform service agent can be granted read on the bucket. Check with `gcloud resource-manager org-policies list --project=<project>` before building anything | — |
| Structured output and thinking levels in batch requests? | The request line is a full `GenerateContentRequest`, so `generationConfig.responseSchema` and `thinkingConfig` should carry over. **Unverified**: no page says so explicitly | Batch from Cloud Storage (request format) |
| Developer API "Gemini Batch API" relevant? | No. It needs an API key (the org blocks keys) and the trial credit can't pay for AI Studio / Gemini API costs. Its one extra, inline requests (no bucket), doesn't change that | [Gemini API batch mode](https://ai.google.dev/gemini-api/docs/batch-mode); Free Trial features |

**The saving at our volume.** Real per-job costs from `llm_usage` in
`evals/eval.db` (54 jobs, small tier): `extract` $0.00123, `match` $0.00096,
plus `employer_name` $0.00033 when the company is missing (app.db, 17 calls).
About **$0.0022 per scanned job**, so batch saves about **$0.0011 per job**.
The §9 envelope (3,600 scanned jobs in 90 days, an upper bound) gives **≈ $4
saved over the whole trial**. Flash-Lite's price doesn't double on 2027-01-01,
so the saving doesn't grow then. Letters (≈ $0.18–0.23 each, 70–80% of it the Pro
draft and revision) would be the only meaningful saving, but they are
multi-step tool runs where each call depends on the last, and a letter a day
late defeats the idle loop. The cost reports (`evals/results/cost-*.md`) don't
cover extract/match; their numbers are the letter's.

**Why not, beyond the money.** Scores would arrive up to 24 h (+ queue) after
a scan, so the sidebar's ranked list and the letter pipeline (which waits on a
match) would lag by a day. It would add a bucket, a JSONL writer, a job poller,
an output parser that maps results back to job ids, and partial-failure
handling, none of which exists today.

**When to revisit.** If the app becomes multi-user (hundreds of thousands of
jobs a month), or for a bulk *re-score* that nobody waits on: the unbuilt
"re-score against updated profile" button (CLAUDE.md, retention entry, item 10)
re-matches every live match after a profile change, which is exactly a
batch-shaped job. Sketch for then: `client.submit_batch(tier, task, requests)`
and `client.collect_batch(job)` in `app/llm/client.py`, so the provider, model
IDs and prices stay in the one place; callers still name a tier and a `task=`.
`PRICES` gains the batch rate. One `llm_usage` row per response is written at
collection time from its `usageMetadata`, with the `job_id` it belongs to and a
batch marker (a new column or a task suffix; schema doc first). The budget
guard checks an *estimate* (input tokens × batch price + a max-output
allowance) before submitting, since the real cost is only known a day later;
`small` stays never-blocked, as now. Bulk extract/match is all `small`, so in
practice the guard would only matter if mid-tier work were ever batched.

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
  wait for letters. Batching, remembered "no"s and "yes" answers landing in the
  profile keep the number of questions small. If it's still a problem, add a
  "skip, leave it out" option or an auto-`leave_out` after N days.
- **Bad data from `ask_user`.** A misparsed answer would put a wrong fact into
  the profile, which then counts as evidence everywhere. The confirm step and
  origin tagging (§7) are there for this.
- **Preview model churn.** `gemini-3.1-pro-preview` may change. `strong` is one
  env var; falling back to 3.8 Flash is a config change.
- **Price change 2027-01-01** (Flash doubles) and **trial expiry
  ~2026-12-30**. Keep the OpenAI path working as a fallback, and re-check
  prices before continuing past the trial.
- **Privacy.** Profile data and voice samples go to Google. Verify the
  paid-tier data-use terms (not used for training at time of writing).
  The writing sample lives in the DB, never in the repo.

---

## 12. Open questions

**Resolved 2026-10-01 (from v0.2, still valid):**
- ~~Q1 Trial timing~~: new trial, ~2026-10-01 → ~2026-12-30. Confirm the
  expiry in Phase 0.
- ~~Q2 Trigger~~: automatic at ≥85, tunable, with a master toggle (§6).
- ~~Q3 Company research~~: later, as an experiment. Moved out of Phase 9 to
  `future_work/research-company.md` (2026-10-04).
- ~~Q4 Resume scope~~: tailoring notes only, behind a toggle (§5.6).

- ~~Q5 Voice~~: a free-text "Your writing" paste box in the profile editor
  (`profiles.writing_sample`). If it's empty, fall back to the user's own text
  in experiences and the summary (§5.4).
- ~~Q6 AI detector~~: no external detector. v1 is the lint script plus the
  style skill in the writer's context (§5.4).
- ~~Q7 Letter shape~~: Australian spelling; must fit on one page, about
  250–300 words; "Dear Hiring Manager" for now, with addressing and framing
  tuned later.
- ~~Q8 Budget caps~~: start at $5/day, $200 total, $0.50/run. These are
  placeholders until Phase 1 shows real costs.
- ~~Q9 Gaps~~: `ask_user` asks with a Yes + text box / No. Yes saves the
  answer to the profile as experience / skill / qualification rows; No means
  `leave_out` and is remembered (§5.5).
- ~~Q10 Screening~~: ad text only at first; live Quick Apply questions later
  (Phase 9, now the only Phase 9 item; §10.1).

**Decided 2026-10-03 (user):** Q11 = confirm before saving; Q12 = a plain origin tag
("added while applying to a job"), no job title. See the Decision log.

**Was open:**
- ~~**Q11 Confirm step.**~~ Should `ask_user` show the parsed profile rows for a
  one-click confirm before saving, or save straight away? Leaning towards
  confirm, because a wrong row becomes evidence everywhere.
- ~~**Q12 Origin tag.**~~ Mark profile rows created by `ask_user` so the profile
  editor can show where they came from? This would need a small column on the
  three tables.

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
| 2026-10-01 | Voice = pasted free-text dump in `profiles.writing_sample`, falling back to experience/summary text | Easiest for the user to provide; the profile's own words are better than nothing |
| 2026-10-01 | "AI detection" v1 = `style_lint` + style skill, no external detector | Detectors are unreliable; lint rules are free and specific |
| 2026-10-01 | Australian spelling, one page, about 250–300 words, "Dear Hiring Manager" | User preference; addressing and framing to be tuned later |
| 2026-10-01 | Budget caps $5/day, $200 total, $0.50/run, as placeholders | Revisit once `llm_usage` shows real costs |
| 2026-10-01 | `ask_user`: Yes + text box → saved as profile rows; No → `leave_out`, remembered | Gaps get filled with real facts that help every future match; nothing is invented; each gap is asked once |
| 2026-10-01 | Screening answers from ad text only for now | Live Quick Apply questions need the parked apply-flow detection |
| 2026-10-01 | **Phase 0 done.** Trial expiry confirmed 2026-12-30 (user). `gemini-3.1-flash-lite`, `gemini-3.8-flash`, `gemini-3.1-pro-preview` all listed on the Vertex project and answered a live call. Auth is Vertex ADC, not an API key | Closes the Phase 0 checks |
| 2026-10-01 | **Phase 1 done**, all in `client.py` + `app/llm/usage.py`: tiers (`GEMINI_MODEL_SMALL/_MID/_STRONG`; old `GEMINI_MODEL` still read as small); `complete_tools` (stateless: tool results ride in the next state summary, so no thought-signature replay); `llm_usage` table (no FKs, so the spend log outlives deleted rows); budget guard (small never blocked, fails open if the table is unreadable); `GET /llm/usage` + sidebar banner; idle loop pauses letters 10 min on a cap hit | Plan §4 items 1–9 |
| 2026-10-01 | Gemini 3 called **without `temperature`** (re-verified in Google's Gemini 3 docs: keep 1.0); thinking level per tier low / medium / high via `GEMINI_THINKING_*` | §4 item 5 |
| 2026-10-01 | **Score drift: do NOT bump `profile_revised_at`** at switchover. `check_matching.py` on Gemini: 3/3 test jobs in band (95 / 95 / 55, spread 40). Revisit if real-job scores look shifted | §4 item 10. Reversible: bumping later just down-weights older scores in the miner |
| 2026-10-01 | 🧪 Observed: thinking tokens dominate small-call cost. A trivial `small` call used 110–262 thinking tokens against 1–5 output tokens at level `low` (flash-lite's own default is `minimal`). Re-test `small` at `minimal` on the eval set before Phase 2 numbers are taken as final | Thinking is the biggest cost lever (§4 item 5) |
| 2026-10-01 | **Phase 2 built.** `app/llm/letter/state.py` (`LetterState`, `ProfileIndex` pointer resolver, `summary_for_orchestrator`), `app/llm/letter/rubric.py` (code-checked rubric items), `scripts/letter_lab.py` (snapshot → prepare → run → report), `evals/rubric.md` | Plan §5.3, §9 |
| 2026-10-01 | Evals run against the **real profile**, in a scratch `evals/eval.db` built from a read-only copy of real.db with the job side wiped. Ads, eval.db, set.json and letters are **gitignored**; only `evals/rubric.md` and `evals/results/*.md` are committed | User choice: the "would I send it" item only means something for the user's own letters; ad text and letters written as the user stay off GitHub |
| 2026-10-01 | Pointer sentences are **1-based** (`experience:12#s1` is the first sentence); lines/bullets are sentence boundaries | The splitter must be deterministic or `#s<n>` pointers drift |
| 2026-10-01 | `banned_phrases.txt` created now (Phase 2) with a seed list, because the rubric needs it. Phase 4's `style_lint` and style guide read the same file | One list, no drift |
| 2026-10-01 | Grading split: 5 rubric items are checked by code; 4 (supported must-haves, no unsupported claims, specific detail, would send) are graded by the user in `grades.csv`. No LLM judge for the baseline | The plan warns that lenient model graders make the design look safer than it is; `check_claims` gets its own planted-claim test in Phase 5 |
| 2026-10-01 | ⚠️ **Eval set is only 9 ads** (17 snapshotted; 8 scored < 50). Bands: 3 at 88–90, **none at 75–84**, 6 at 55–60. Grow it to 12–15 with more relevant real scans **before Phase 6**, when engines are compared on a fixed set | The plan's 10–15 target, and the 75–84 band is where the one-shot vs pipeline split (§6) actually matters |
| 2026-10-02 | **Baseline graded** (9 letters, one-shot, Gemini 3.1 Pro): `would_send` 0/9, `supported_musts_covered` 4/9, `specific_detail` 4/8, `no_unsupported_claims` 9/9. Rubric limits tightened: at most 1 em dash, at most 1 generic phrase | The one-shot writer does not fabricate; it is shallow and misses requirements, which points at Phase 3 (the writer never sees the ad) and Phase 4 (style/voice), not at claim-checking |
| 2026-10-02 | **Requirements carry two ratings, not one must/should flag.** `importance` = how much the employer cares (essential / important / nice_to_have, inferred from wording because many ads use one flat "you likely have" list); `letter_role` = what the letter does with it: **headline** (lead point with evidence), **mention** (brief, if the candidate has it), **implied** (not named, but may be used if it fits a sentence), **not_for_letter** (eligibility/admin, shown to the user as a job-card note) | User feedback from grading: not every requirement belongs in a letter (work rights), and some real ones are implied by others (CI/CD under "builds production web apps"). A single must/should flag cannot express either |
| 2026-10-02 | Code constrains the model's judgement in `analyze_job`: nice-to-have can't be a headline; at most 5 headlines; obvious eligibility wording (work rights, citizenship, clearances, licence, police check) is forced to `not_for_letter`; `implied_by` may only point at headline/mention items | Same principle as the guardrails: the model decides, code enforces what must always hold |
| 2026-10-02 | `pending_gaps` (blocks drafting, asks the user) now means: essential + headline/mention + gap + undecided. Eligibility, implied and nice-to-have gaps never trigger a question | Keeps `ask_user` to the few questions that matter; an eligibility gap is a note, not a letter claim |
| 2026-10-02 | `match_profile` validates in code: pointers must resolve (brackets/quotes stripped first), a bare listed skill can't make a requirement `supported` (-> partial), supported/partial with no valid evidence -> gap, eligibility items are judged from profile facts and never cited. Live finding: Gemini copied pointers WITH brackets on one job, which first demoted 7 good matches to gaps; fixed by normalising pointers and telling the prompt to omit brackets | Hallucinated or malformed evidence must never reach the writer; the correction list is shown in the review pack |
| 2026-10-02 | `analyze_job` + `match_profile` run on the **mid** tier (gemini-3.8-flash); ~$0.016 per job for both, `analyze_job` once per job (cached on `job_listings.requirements_checklist`, keyed by a description fingerprint and `ANALYSIS_VERSION`) | Plan §3 test: mid vs strong on the eval set. Hand-check decides whether to move up |
| 2026-10-02 | Phase 3 surfaces `eligibility_notes` in `GET /jobs` and `GET /jobs/{id}` (empty until a job has been analysed). **Sidebar display deferred to Phase 8**, because analysis only runs for jobs headed for a letter until the idle-loop integration | Data is ready; UI waits for there to be data on most cards |
| 2026-10-02 | ⚠️ Found while reviewing: **`job_listings.company` is NULL for every Seek job** in both DBs (the extension reads it only from search-result cards, not the detail page), so letters currently say "at Unknown". Not fixed in Phase 3 (needs the extension and a live Seek page to verify; the JSON-LD `hiringOrganization` is the likely source) | Hurts "specific detail"; flagged for the user |
| 2026-10-02 | **Company-name bug fixed** in three layers: (1) the extension's detail-page capture now sends company/location/work type from the JSON-LD `hiringOrganization` / `jobLocation` / `employmentType`, with unverified DOM fallbacks; (2) `/ingest` backfills company/location/work_type/salary onto existing rows (it previously only backfilled description/taxonomy/query); (3) `extract.py` recovers `employer_name` from the ad text and fills a NULL `company`, stored only if the name appears verbatim in the ad. `scripts/backfill_company.py` repairs old rows. Eval + test DBs: 12/17 and 9/13 filled, all verbatim in the ad; the rest are recruiter or unnamed-employer ads, correctly left empty | Letters said "at Unknown", which hurt `specific_detail`. ⚠️ The graded one-shot baseline was written WITHOUT company names, so part of any later improvement in `specific_detail` is this fix, not the pipeline. Decided 2026-10-02: NOT re-graded; the baseline stands as a lower bound for `specific_detail`. real.db repaired the same day (6/8 filled, all verbatim; backup in backups/real.db.pre-company-backfill) |
| 2026-10-03 | **Phase 3 hand-check adjudicated.** The user reviewed Claude's blind grading of `analysis-2` (95/100 importance, 91/100 letter role, 91/100 evidence, 10 missed) and accepted every ruling, major and minor. That grading stands as the Phase 3 hand-check | The plan's "checked by hand" gate; the user's own judgement on each disputed row |
| 2026-10-03 | **Fixes from the hand-check, `ANALYSIS_VERSION` 2.** `analyze_job`: attitudes never essential and never headlines (prompt + `_ATTITUDE_RE` in code); soft skills, stack listings and beyond-seniority duties aren't headlines; grad "contribute to" duties at most important; "ideally / familiarity with" = nice_to_have; benefits aren't requirements; read the intro and cover every listed duty; cap 16 → 20, dropping nice-to-haves first instead of truncating by position; an `implied` item with no parent becomes `mention` (code); new `application_instructions` field (e.g. "show off projects in your cover letter"). `match_profile`: building X ≠ using X day-to-day (the one overclaim); check skills and every sentence before calling a gap; evidence applied consistently across requirements; bundled asks are partial when part-covered; location matching the profile is supported, and terms accepted by applying aren't gaps | 4 of the 5 MAJOR rows were needless `ask_user` triggers (essential + headline + gap); 3 of the 10 misses were must-haves cut at the 16 cap; the AI-in-delivery overclaim would have passed `check_claims` stage 1 |
| 2026-10-03 | **Verified on reruns.** `analysis-3` (~$0.20): all 10 misses captured, disputed rows match the rulings, but `ask_user` triggers went 7 → 0: "different activity = partial at most" let the thesis count as partial evidence for "uses AI coding tools daily", which silenced a question the user can answer yes to. Rule tightened: same activity with a different tool = partial; same subject, different activity = **gap**. `analysis-4`: exactly the 2 legitimate triggers (both AI-tool-use asks), all 5 AI-use rows consistently gap | Over-correcting towards partial hides gaps the user could fill with real facts, which is what `ask_user` exists for |
| 2026-10-03 | **`analyze_job` + `match_profile` stay on `mid`** (§3 test) | 91–95% agreement with a blind strong-model grader; the disagreements were rule problems the prompt/code fixes address, not model capability |
| 2026-10-03 | **Phase 4 built.** `profiles.writing_sample` (migration `e8b4f2a61c93`) + "Your writing" box in the standalone and sidebar profile editors; skill folder (`SKILL.md`, `banned_phrases.txt`, `us_to_au.txt`); `app/llm/letter/style.py` (`style_guide_prompt`, `voice_reference` / `voice_prompt`); `app/llm/letter/tools/style_lint.py` (code-only, writes `checks.style`) | Plan §5.4 |
| 2026-10-03 | **One em dash allowed in `style_lint`**, the same limit as the eval rubric (user decision), instead of the stricter block-any-dash first proposed. A spaced en dash (" – ") counts as an em dash in `style_lint` only; the rubric still counts real em dashes only, so old runs stay comparable | Keeps writer and eval consistent for now; tighten later if dashes keep creeping in |
| 2026-10-03 | `style_lint` limits: banned phrases > 1 and em dashes > 1 block; > 340 words or > 5 body paragraphs block; placeholders and missing sign-off block. Warnings: < 230 words, sentence-length stdev < 6.0 words (baseline letters ran 5.1-8.8, the user's own writing 8.0-9.9), 3+ paragraphs opening with "I", US spellings. Word counter, banned list and placeholder pattern live in `style_lint.py` and `rubric.py` imports them | One definition of each, so the writer's check and the eval can't drift |
| 2026-10-03 | `banned_phrases.txt` grew by 5 from the baseline letters ("strong foundation", "technical foundation", "i would welcome the opportunity", "eager to bring", "problem-solving skills"); `us_to_au.txt` kept short on purpose (program, licence/license, practice/practise are context-dependent) | Phrases the one-shot writer reused across unrelated jobs. Under the new list the baseline letters fail `style_lint` 8/9, which is the point |
| 2026-10-03 | Voice = the user's `writing_sample` (3 samples, 921 words, contact lines and dates removed; two are cover letters, one a uni reflection; the user says some is AI-assisted), trimmed to 1,500 words, tone and rhythm only. Fallback: summary + experience descriptions | Plan Q5 |
| 2026-10-03 | **Early test, `oneshot-styled`** (the one-shot writer + style guide + voice, otherwise identical inputs; eval-only, production unchanged): 9 letters, $0.039/letter (baseline $0.026), 9/9 pass the rubric's code checks (baseline: generic-phrase limit 6/9), `style_lint` 8/9 (one with 6 body paragraphs), 290-340 words (aim 250-300, so the writer runs long). Awaits the user's grades. 🧪 Watch: it copies stock lines from the sample verbatim across letters ("the most relevant ... I can point to", "What I took from that project") | Tests style + voice alone before Phase 5 builds on them |
| 2026-10-03 | **Grading `styled-oneshot` deferred.** Making the voice (`writing_sample`) switchable and comparing voice on vs off moves to `future_work/voice-toggle-and-comparison.md`, to run on the finished agent | Grading the one-shot stand-in says little about the final writer; the comparison is worth more once the agent exists |
| 2026-10-03 | **To-work-on list added to Phase 7** (§5.9, user request). Every `ask_user` "No" is saved, and each later ad asking for that skill is counted (`gap_sightings`, once per ad, from every scan via `job_skills` plus letter runs). The ranked list (90-day count, then essential count) is served at `GET /gaps/to-work-on` and written to `reports/to-work-on.md`. Builds on `gap_decisions` rather than adding a separate store; `analyze_job` gains a short `skill` name per requirement so different wordings count as one item | The user wants to see which missing skills and tools keep coming up, to decide what to learn. The data lives in the DB (real/test envs, Postgres later) and the readable file is generated from it. Counting from `job_skills` covers every scanned ad, not just the ≥85 ones that get letter runs |
| 2026-10-03 | **Phase 5 built.** `app/llm/letter/guardrails.py`; tools `generate.py` (`generate_letter`), `revise.py` (`revise_letter`), `check_claims.py`, `check_requirements.py`; `letter_lab.py run --engine tools` (analyze → match → unanswered must-have gaps = `leave_out` → draft → 3 checks → one revision → 3 checks; writes `drafts.md` + `states/`), `--only` / `--resume`, and `plant` (planted-claim test, `--resume` / `--report-only`). 405 tests (68 new in `tests/test_letter_draft_tools.py`, written by a Sonnet subagent to a spec). Workflow driver + best-draft selection are Phase 6 | Plan §5.2, §5.7 |
| 2026-10-03 | **The letter plan has ONE definition** (`guardrails.py`), read by the writer, the reviser and `check_requirements`: `must_cover` = headline items + essential mentions that are supported/partial and not left out; `may_use` = other supported/partial mentions + implied items; `do_not_claim` = gaps + `leave_out`; eligibility = `not_for_letter`. Gates: no drafting while a requirement is unmatched or a must-have gap is undecided; `generate_letter` writes draft 1 only; `revise_letter` needs all 3 checks run on the latest draft and one failed, within `max_drafts`; `can_finish` is in place for Phase 6 | User decision: `check_requirements` blocks on headlines + essential mentions only; a one-page letter can't name 15 things |
| 2026-10-03 | **The writer sees the full raw ad** (user decision) plus the analysis, the per-requirement evidence (resolved text), the style guide and voice, and a no-copy line. One strong `complete_json` call returns the letter + claims (exact quote + ONE pointer). `generate` and `revise` share the system prompt and context block, with the task last, for Gemini's implicit prefix cache. ⚠️ **`cached_tokens` is 0 on every one of ~200 logged calls** (eval/real/test DBs): implicit caching is not happening on this Vertex setup. Not chased: it would save under 1¢/run; thinking is the real cost (≈5.4k thinking tokens ≈ 75% of the $0.08 draft call) | Plan §2: the old writer never saw the employer's wording |
| 2026-10-03 | **`check_claims` rules** (deviates from §5.2 on undeclared claims): a declared pointer that doesn't resolve **blocks** (a fabrication signal); an undeclared or unsourced claim goes to stage 2 and only **warns** if the profile backs it (bookkeeping isn't worth a strong-model revision); overstated/unsupported block; claims about the employer are checked against the ad. Two fixes from the eval: a `not_a_claim` verdict (a forced choice put "I am applying for…" and "I have not used Power Apps" under unsupported), and **reason before verdict** in the schema (flash-lite wrote "unsupported", then a reason arguing the claim was backed) | Code disposes: a "supported" verdict with no pointer that resolves counts as unsupported |
| 2026-10-03 | **`check_requirements` marks PARTIAL items**, and honest related-experience framing counts as addressed; the model's quote must be in the letter (≥80% of its words), checked in code. Smoke run: unmarked, R9 (stakeholders; partial via Coles customer requests) failed on both drafts although both framed it honestly, which pushes the reviser towards overclaiming | Same principle as `match_profile`: the checker must know what the profile can honestly support |
| 2026-10-03 | **`tools-v1`** (9 ads, Gemini 3.1 Pro writer): $1.49 total, **$0.165/letter** (est. $0.15), ~2 min each. 6/9 pass all three checks within 2 drafts (4 on draft 1); rubric code checks 9/9. Revisions changed ~5% of the text (edits, not rewrites) but caused regressions: 2 dropped a must-cover item the draft before had, 1 went over length. The revise prompt now says to keep every MUST ADDRESS item and the length. Not re-run to test that: Phase 6's loop measures it. Copying from the writing sample fell from 62 to 19 shared 6-word runs (vs `styled-oneshot`), but "the most relevant … I can point to" is still in 4/9 (future_work/voice-toggle-and-comparison.md) | First full run of the Phase 5 tools |
| 2026-10-03 | **`check_claims` moves `small` → `mid`.** Planted test (`evals/results/plant-tools-v1.md`; 18 plants × undeclared/miscited, 5 types): small 35/36, mid 36/36, both 0 false alarms once mid's 2 "false alarms" on the clean letters were read: they were real overclaims small had passed ("I have practical experience in C# programming" from a bare skill listing; "apply my skills in automation"; "exposure to operational workflows" from a job title alone). Re-judging all 14 saved drafts: small flipped verdicts between identical runs (4/14 drafts, then 0/14 vs 2/14 fails after the schema fix), passed the C# overclaim in 3 of 3 runs, and filed real claims under `not_a_claim`. Cost ≈ $0.018/check vs $0.004 (≈ +3¢/letter) | Plan §3: "if it misses them, move check_claims to mid". Planted claims are blunt; the real risk is subtle stretches, which only mid caught |
| 2026-10-03 | Robustness: `client.py` now retries a dropped connection ("Server disconnected without sending a response", httpx `RemoteProtocolError` etc.) like a 5xx; it crashed an eval run. `letter_lab.py run` and `plant` save after every job/letter and resume, after a closed window and that disconnect each lost a run in progress | The idle loop hits the same network errors |
| 2026-10-03 | **Eval daily cap raised to $20, with a printed warning at $10** (user decision; first set to $10 the same day), for `evals/eval.db` only: `EVAL_DAILY_BUDGET_USD` / `EVAL_DAILY_WARN_USD` in `letter_lab.py` (the warning prints once per command, checked at start and after each job in `run` / `analyze` / `plant`); the cap is re-applied on every command because `prepare` rebuilds eval.db from real.db (which keeps the $5 app default). **Eval spend to date: $5.38** (2026-10-01 $0.27, 10-02 $0.22, 10-03 $4.89), recorded here because `prepare` empties eval.db's `llm_usage` | A full engine run over a 15-ad set (~$3-4) on top of a day's other eval work would hit $5 mid-run. Cloud Billing remains the authority on spend |
| 2026-10-03 | **Phase 6 built.** `app/llm/letter/workflow.py`: `run_workflow(db, job_id, profile_id, gap_policy=leave_out_gaps, max_revisions=2)` runs analyze → match → gap policy → generate → the 3 checks → `revise_letter` on the **latest** draft ≤2 times → finish, through `runner.execute_tool` and the shared guardrails. Callable from production as is (Phase 8). It returns a `WorkflowResult` (status, draft, `clean`, `open_issues`, `stop_reason`, `account_limit`) and only raises when there is no job/match. `letter_lab.py run --engine workflow`; `--engine tools` kept for tools-v1 and now logs `engine="tools"` (it logged "workflow"; `analyze` now logs "eval-analyze"). 455 tests (50 new in `tests/test_letter_workflow.py`, written by a Sonnet subagent to a spec) | Plan §5.1, §10 |
| 2026-10-03 | **Best-draft selection** (`guardrails.best_draft`): passed check_claims, then fewest missing must-cover items, then passed style_lint, then the later draft. A check that never ran counts as failed, so an unchecked draft never outranks a checked one. The returned draft is `letter_runs.final_draft_version` (`finish_run(final_version=)`); it is "clean" only if all 3 checks ran and passed on it, otherwise its issues come back in `open_issues` (§5.7: a draft that failed check_claims is never returned as clean) | User decision; §5.7 |
| 2026-10-03 | **Stopping never crashes.** The revision limit, the per-run budget (`max_tool_calls` 15, `max_cost_usd` $0.50), a failed tool, and the account's USD guard / daily quota all end as `budget_stopped` or `failed` with the best draft so far. An account-level stop also sets `account_limit`, because every later run will hit it too (letter_lab re-raises it to stop the batch; the idle loop should back off). A full run is 14 tool calls; `finish` is not logged as a step in the workflow (the gate is `can_finish`). **The gap policy is a parameter** (default: every pending must-have gap → `leave_out`); Phase 7 passes `ask_user`, and a policy that leaves questions open ends the run as `waiting_user` | User decisions |
| 2026-10-03 | **`registry.py` waits for Phase 7**: only the agent needs model-facing tool schemas; the workflow imports the tool functions directly | User decision; avoids building schemas nobody calls yet |
| 2026-10-03 | **Eval set grown to 15** (user's choice): the original 9 + `seek-94879548`, `-94907677`, `-94562501` (75s in real.db) + `-94908023`, `-94487596`, `-94796145` (85-90). `snapshot` took the ad pool from 17 to 54; `prepare --reselect` rebuilt eval.db ($0.12). **eval.db scores by band: 85+ = 8** (94419843 90, 94691623 90, 94605424 88, 94254837 88, 94424868 88, 94908023 88, 94796145 88, 94487596 85); **75-84 = 4** (94657953, 94570352, 94124650, 94879548, all 75); **under 75 = 3** (94241502 68, 94907677 60, 94562501 40). No swap was needed. ⚠️ **The re-score moved a lot**: three of the original 9 jumped (52→90, 72→88, 75→88 in real.db vs eval.db), and two of the "75s" fell (→60, →40). The same profile and model gave 50-point swings, so `match.py` scores are noisy at the 75 line. `prepare` now carries the `analyze_job` cache over from the old eval.db, so the original 9 kept the exact checklists tools-v1 used | Plan §9 target of 10-15; the 75-84 band now has 4 |
| 2026-10-03 | **`workflow-v1`** (15 ads): $2.91 total, **$0.194/letter**, 138 s/letter. **14/15 return a clean draft** (10 clean on draft 1, 4 after one revision); 1 hit the revision limit (Graduate LCNC, returned v3 with 2 must-cover items flagged). The returned draft was never an earlier one (0/15). Revisions: 6 in total, changing 8% of words on average; **0 went over length** and **0 broke check_claims or any other passing check**, but **2 dropped a must-cover item**, both in the LCNC run. On the original 9 against tools-v1: clean 8/9 vs 6/9, over-length 0/5 vs 1/5, broke a check 0/5 vs 2/5, dropped 2/5 vs 2/5. So the revise prompt's "keep coverage and length" line fixed length and claim regressions but not coverage drops. ⚠️ tools-v1 judged claims on `small`, workflow-v1 on `mid`. Code rubric checks 15/15, style_lint 15/15; letters average 306 words (aim 250-300). Results: `evals/results/workflow-v1.md`, `workflow-v1-loop.md` (`letter_lab.py loop-report <run> --against <run>`). Awaits the user's grades | Phase 6 eval |
| 2026-10-03 | ⚠️ **Finding: partial must-cover items backed only by a bare skill listing.** `match_profile`'s code rule makes a bare listed skill `partial`, and a partial headline is `must_cover`, so the writer must address something it has no concrete evidence for. It does so honestly ("I have not administered database technology… however my skills in SQL… provide a technical baseline"). 7/15 jobs have such items and 7/15 returned letters contain an "I have not…" sentence. Both coverage drops above come from this: small `check_requirements` flip-flops on whether such a sentence "addresses" the item, so each revision "fixes" one and "drops" another. **Not changed during Phase 6** (it would break the comparison). Proposed for Phase 7: a partial whose evidence is only `skill:` pointers goes to `may_use`, or becomes an `ask_user` question ("you list SQL; have you administered a database?") | Spelling out a gap in a letter is weak; the user's grades will show whether they mind |
| 2026-10-03 | **One-shot baseline extended to the 15-ad set**: the 6 new ads were run as `baseline-oneshot-new6` ($0.13) and merged into `evals/runs/baseline-oneshot/` (run.json rows tagged `added_from`, ungraded rows appended to grades.csv; pre-merge copy in `backups/baseline-oneshot.pre-merge`). Under the current rubric limits the baseline passes `generic_phrase_limit` on 1/15 (the banned list grew in Phase 4) and style_lint on 1/15. The 6 new one-shot letters were written with company names, unlike the original 9 | User request: the baseline covers the full set |
| 2026-10-03 | Eval spend on 2026-10-03: **$8.07** ($4.89 before the set rebuild, recorded above, + $3.18 in the rebuilt eval.db: prepare $0.12, smoke + workflow-v1 $2.91, one-shot $0.13); under the $10 warning line | `prepare` empties eval.db's `llm_usage`, so the running total lives here |
| 2026-10-03 | **Judgement items graded by a blind Opus panel** (user request) instead of by hand: `evals/grading-standard.md` (the "base levels") written first by one Opus agent from the rubric, the Decision-log rulings and the user's grades on 5 of the 9 graded one-shot letters, without seeing any workflow letter; then 2 independent blind Opus graders on all 30 letters (random ids, shuffled, engine hidden, per-job must-have list fixed before grading), and an Opus adjudicator for the 9 split cells. Graders agreed on 111/120 cells; all 9 adjudications went N. Against the user's own grades: **11/20 on the calibration letters, 8/15 on the 4 held-out letters**, and 15 of 16 disagreements are the panel being stricter (claims 8/9 letters: invented scope/process/outcome and a wrong tenure the user passed). Panel grades land in `evals/runs/<run>/grades-opus-v1.csv` and fill only BLANK cells of `grades.csv` (the user's 9 baseline grades are untouched); `letter_lab.py report <run> --grades grades-opus-v1.csv` writes `<run>-opus-v1.md`; model-grader notes stay out of committed results (they quote letters and name profile details). Summary: `evals/results/grading-opus-v1.md` | A model grader is not the user, so it is held to a written standard, double-graded blind and measured against held-out user grades; the absolute rates aren't comparable with the user's, the same-grader engine comparison is |
| 2026-10-03 | **Workflow beats one-shot on every judgement item** (panel, 15 vs 15): musts covered 15 vs 4, no unsupported claims 6 vs 2, specific detail 11 vs 4, would send 2 vs 0; full rubric pass 2/15 vs 0/15. Still far from sendable: 13/15 workflow letters are not `would_send`, mostly a sentence that leads with a gap (10/13) or experience listed without applying it to the ad's work | The Phase 6 baseline the agent has to beat |
| 2026-10-03 | ⚠️ **`check_claims` (mid) had passed all 9 workflow letters the panel failed on claims.** 7 of the 9 workflow claim fails are experience wording ("experience with / exposure to / practical skills in X") resting on a skill the profile only lists, the same overclaim the user ruled on in Phase 5 (C#). Not changed yet: proposed for Phase 7, together with the partial-must-cover finding (same root cause, bare skill listings): `check_claims` and the writer should treat a bare listing as backing only "skills in / knowledge of / familiar with X", then re-run `plant` and the 15-ad set | One root cause behind both the weakest claims and the gap-led sentences |
| 2026-10-03 | **Invented-link guard** (fixed the same day): the Maxum ad asked for "a link to a 2 minute video" and a workflow style revision wrote `youtu.be/<name>`; the judge passed it (not a claim about experience). `check_claims` stage 1 now blocks, in code, any URL or email address in the letter that isn't in the profile (`invented_links`), and the writer prompt forbids inventing links, contact details or attachments ("the user adds it"). On every saved eval letter it flags only that one. 3 new tests (458 total). Application instructions the profile can't satisfy should reach the user as a note (Phase 8, like `eligibility_notes`) | A fabricated link sent to an employer is a fabrication; deterministic code is the right check |
| 2026-10-03 | **Listed-skill fix** (commit 907fe81, the Phase 6 follow-up the user asked for before Phase 7): `guardrails.listing_only(r)` (every evidence pointer is `skill:`); such an item is never `must_cover` and goes to `may_use` (a listing-only headline too). The writer is told a listing backs "skills in / knowledge of", never "experience with / exposure to / a background in / hands-on / practical", and not to lead with what the candidate hasn't done; the plan marks such items `LISTED SKILL ONLY`. The claims judge counts experience wording on a listed-only skill as overstated. `style_lint` warns (doesn't block) on gap-led sentences (`gap_led_sentences`). 464 tests | One root cause behind the weakest claims (7/9 workflow-v1 claim fails) and the gap-led sentences (10/13 would_send fails) |
| 2026-10-03 | **`workflow-v2`** (same 15 ads, after the fix): $2.75, **$0.184/letter**, 145 s. 15/15 return a clean draft (9 on draft 1, 6 after one revision; v1 14/15), **0/6 revisions dropped a must-cover item** (v1 2/6), 0 over length, 0 hit the cap. Gap-led sentences: **0/15 letters** (v1 9/15). The new judge rule caught two "experience writing in C#" claims on a listed-only skill, both fixed by a revision. The writer still runs long: 3 first drafts were over 340 words (306 average) | Re-run after the fix; `evals/results/workflow-v2-loop.md` |
| 2026-10-03 | **Panel opus-v2** (fresh blind Opus panel, workflow-v1 vs workflow-v2, same standard; graders read one job at a time, so every must-have list came before its letters): A/B 111/120; **re-graded workflow-v1 at 57/60 against panel opus-v1**, so the panel is consistent across sessions. v2 vs v1: **claims 13 vs 6**, musts 14 vs 15, detail 12 vs 13, would_send 0 vs 1. Every v2 would_send fail is the same thing: paragraphs that recite the profile (internship, thesis) without saying how it applies to this employer's work, often with a generic close ("my background translates well…"). That is the user's "application" note and the next writer lever. `evals/results/grading-opus-v2.md` | The fix worked on claims and gap sentences; sendability now hinges on applying evidence to the ad |
| 2026-10-03 | **Panel tooling in the repo**: `scripts/grading_panel.py` (pack / disagreements / merge with `--fill` and `--compare`) and `evals/grading-panel.md` (procedure + grader and adjudicator prompts). Panels live in `evals/panels/<tag>/` (gitignored); grades land in `evals/runs/<run>/grades-<tag>.csv` (panel 1 = `opus-v1`, panel 2 = `opus-v2`); reports are `results/<run>-<tag>.md`. Re-merging both panels with the script reproduced every number | Phase 7 grades the agent the same way |
| 2026-10-03 | **Planted-claim test on workflow-v2** (`check_claims` on mid, with the new listed-skill rule): **59/60 caught** (undeclared 29/30, miscited 30/30; the one miss was an undeclared study-to-work plant), **0/15 false alarms on the clean letters**, $1.50. The stricter rule did not make the judge trigger-happy. `evals/results/plant-workflow-v2.md` | Guards the fix against over-blocking |
| 2026-10-03 | Eval spend on 2026-10-03, final: **$12.32** ($4.89 before the set rebuild + $7.43 in the rebuilt eval.db: prepare $0.12, workflow-v1 $2.91, one-shot $0.13, workflow-v2 $2.75, plant $1.50). Past the $10 warning line, under the $20 cap. The four Opus grading panels used no Gemini | Running total (prepare empties eval.db's `llm_usage`) |
| 2026-10-03 | **Phase 7a built: the agent.** `app/llm/letter/registry.py` (tool name -> function, model-facing `ToolSpec`, and a gate; every description says when to use the tool and when not to, and the claim/match/revise ones repeat the listed-skill rule); `app/llm/letter/agent.py` (`run_agent`, the §5.5 loop on `complete_tools`, tier mid, `task="orchestrate"`); `app/llm/letter/outcome.py` (`open_run`, `conclude`, `LetterResult`, `GapPolicy`, `leave_out_gaps`), which the workflow now uses too, so the two engines end a run in one place (`WorkflowResult` is kept as an alias). `letter_lab.py run --engine agent`; `loop-report` adds the agent's path vs the workflow's, refusals and orchestrator overhead | Plan §5.2, §5.5, §8 |
| 2026-10-03 | **How the agent is held to the rules.** Stateless turns: each orchestrator call gets the state summary, the steps so far and the last result (or refusal), never a history, so no thought signatures are replayed. Every call passes a gate first (the same `guardrails` functions the tools and workflow use, plus `can_check`: a check runs once per draft, and analyze/match run once); a refused call runs nothing, costs no tool call, is logged as a `refused:` step and its reason is the next turn's result. Refusals and text replies are capped at 4 per run (`MAX_REFUSALS`). `finish` is code: accepted when `can_finish` passes, or when the draft limit is reached with all three checks run (`out_of_drafts`: ends `budget_stopped` with the best draft, as the workflow's revision limit does); finish is not a tool call. The budget is checked before every turn, and orchestrator calls count towards the run's $0.50 (they log `llm_usage` with the run id; `runner.persist` re-reads it). The gap policy is the `ask_user` tool (leave_out for the comparison) | §5.7: the model chooses, code enforces |
| 2026-10-03 | Smoke: one orchestrator turn on gemini-3.8-flash = 1,151 in / 63 out+thinking tokens, **$0.0011**; later turns carry a longer summary, so ~$0.002 each *(est.)* | Orchestrator overhead is small next to the $0.08 draft call |
| 2026-10-03 | **`agent-v1`** (15 ads, gap policy leave_out like the workflow): $2.67, **$0.178/letter**, 182 s. 15/15 clean (10 on draft 1, 5 after revisions; workflow-v2 9 + 6), 0 hit a limit, **0 guardrail refusals, and the same tool path as the workflow on 15/15 jobs**. Orchestrator: 8.6 calls/letter, $0.013 (7% of the run), 47 s (5.5 s a call). One revision dropped a must-cover item while cutting a 349-word draft, and the next draft restored it (the shared `revise_letter`, not an agent choice). Cost per task, both engines: `evals/results/cost-workflow-v2-vs-agent-v1.md` (`letter_lab.py cost-report`, new): thinking tokens are 71% of workflow-v2's cost; the Pro draft call alone is 61% | Phase 7a eval; `evals/results/agent-v1-loop.md` |
| 2026-10-03 | **Panel opus-v3** (fresh blind Opus panel, workflow-v2 vs agent-v1, same standard): A/B 118/120 (2 adjudicated N, both "daily" AI-tool-use claims); re-graded workflow-v2 at **57/60** against opus-v2 (consistent again). agent-v1 vs workflow-v2: musts 15 vs 15, **claims 12 vs 13**, detail 12 vs 12 (the same 3 generic ads), would_send 0 vs 0. The claim fails are the shared writer's wording (a "daily" frequency the profile doesn't state, "apply my skills in PHP" on a listed-only skill, an invented purpose for the thesis evaluation); `check_claims` passed all of them. `evals/results/grading-opus-v3.md` | One-letter difference = writer noise, not the engine |
| 2026-10-03 | **Recommendation: `letter_engine` defaults to `workflow`** (preference wired in Phase 8). The agent reproduced the fixed sequence on every job, at +$0.013 and +37-47 s per letter, with the same quality. The guardrails leave no real choices in letter writing, so agency has nothing to add there. Keep `run_agent` as the alternative engine and revisit where there are real choices (side outputs, company research, choosing listings) | §9: "a likely result is that the workflow wins for the letter itself" |
| 2026-10-03 | Follow-ups found by panel 3 (not done in 7a): the claims judge passes frequency claims ("daily routine", "daily workflow") and "apply my skills in X" on a listed-only skill. And the writer prompt (workflow-v3, the user's decision to do it after 7a): apply the evidence to this employer's work instead of reciting it, and no stock close | Both engines share the writer, so these apply to whichever ships |
| 2026-10-03 | Eval spend on 2026-10-03, after 7a: **$14.99** ($12.32 before + agent-v1 $2.67 + a $0.001 schema smoke). eval.db shows $10.11 today (past the $10 warning, under the $20 cap). The three Opus panel agents used no Gemini | Running total |
| 2026-10-03 | **User decision: `letter_engine` defaults to `agent`**, overriding the recommendation above. The user wants hands-on agent experience (their job next year is in agents), and at ~20 letters a month the agent's overhead is ~$0.26/month and a few minutes of background time. The workflow stays as the fallback engine; both share every tool and guardrail, so the agent can do nothing the workflow couldn't. Phase 7b and 7c are built agent-first: `ask_user` and the resume after the user's answers, re-matching after new profile rows, and the side-output tools are where the orchestrator gets real choices to make | Cost and quality were equal in 7a; the deciding factor is learning value, which is the user's call |
| 2026-10-03 | **Q11 and Q12 decided (user), for Phase 7b.** Q11: a "Yes" answer is parsed into proposed profile rows that the user sees and confirms with one click before anything is saved; the run stays `waiting_user` until then. Q12: rows created this way get a plain origin tag shown as "added while applying to a job" (a nullable origin column on experiences / skills / qualifications, value e.g. `ask_user`); no job title or job id is stored with it | A wrong row would become evidence in every later letter; the tag lets the profile editor show where a row came from without tying it to a job row that retention may purge |
| 2026-10-03 | **Phase 7b built: ask_user, remembered "no"s, the to-work-on list.** Schema doc first, then migration `f6a9c3d8e217` (`gap_decisions`, `gap_sightings` with `job_id` as a label and `seen_at` = the job's `date_scraped`, an `origin` column on experiences/skills/qualifications, `letter_runs` status index). `app/gaps.py` (code only: keys via `normalise_skill`, whole-phrase matching for keys of 3+ chars, save/clear/auto-clear, scan/seed/letter-run sightings, the ranked list). `app/llm/letter/gap_policy.py` (`apply_remembered`, called at the end of `match_profile`; `ask_user_gaps`, the production policy; `ask_user_tool`). `answers.py` (No -> leave_out + remembered + sighting; Yes -> small-model parse into PROPOSED rows; confirm writes them with `origin='ask_user'`, `on_cv=False`, links existing skills, bumps `profile_revised_at`, auto-clears, resets the requirement to `unknown`). New run status `answered`; `outcome.reopen_run` + `resume_workflow` / `resume_agent` + `engines.py` (`run_letter`, `resume_letter`, `answered_runs`). `analyze_job` gains `skill` (ANALYSIS_VERSION 3). API router `app/api/letters.py`; `scripts/gap_report.py` -> `reports/to-work-on.md` (gitignored). Extraction counts scan sightings; `PUT /profile-ui/data` carries `origin` over by natural key (the editors don't send it) and auto-clears. 148 new tests (680 total) by a Sonnet subagent to a spec | Plan §5.5, §5.9, Q11, Q12 |
| 2026-10-03 | **7b design choices.** (1) A re-match judges only the requirements reset to `unknown`: re-judging all of them let a noisy verdict flip and raise a new question mid-run. (2) The workflow now runs the gap policy as a logged, counted `ask_user` step, only when a must-have gap is pending (as the agent does); a buggy policy is a failed step, not an exception. No change on the eval set (no pending gaps). (3) A resumed run gets `RESUME_EXTRA_CALLS` = 2 more tool calls (ask_user + the re-match), so it can afford the same two revisions. (4) Remembered "no"s are applied in `match_profile`, so the agent sees `user: leave_out (remembered)` and only asks about new gaps. (5) Sightings count any requirement naming a remembered skill (not eligibility), not only pending gaps | Keeps the two engines identical and each gap asked once |
| 2026-10-03 | **7b live smoke** (scratch copy of eval.db, $0.0135): `skill` names are short and reusable ("C#/.NET", "PostgreSQL", "Problem solving"; empty for attitudes/duties). The Yes parser saved Tableau, not Power BI, for a Tableau answer to a Power BI question, and refused "yeah kind of". It turned "about a year … 2025" into `end: 2025-01` with no duration; the confirm step is where the user fixes that. ANALYSIS_VERSION 3 invalidates every cached checklist, so the next eval run re-analyses each ad (~$0.01 each) | Prompts checked against real models; not an eval |
| 2026-10-03 | **Not verified in 7b:** the sidebar question card (Phase 8: nothing in the UI calls the new API yet), the idle loop resuming `answered` runs (Phase 8: `engines.answered_runs` / `resume_letter` exist but nothing calls them), and an `ask_user` firing on a real ad (none of the 15 eval ads has a pending must-have gap; covered by fixtures and scripted answers) | Stated plainly so Phase 8 picks them up |
| 2026-10-04 | **workflow-v3: the writer applies evidence instead of reciting it.** `_WRITER_RULES` (generate.py) gained an "Apply the evidence, don't recite it" block: each body paragraph is built around a piece of the employer's work from the ad, uses one or two profile details in fresh words, and adds a connecting sentence that adds no new facts (no frequency/scale/rigour/purpose/outcome the profile lacks; "apply my skills in X" is an experience claim); no stock bridges; when the ad has nothing distinctive, use its most specific described work, never invented detail; don't open with "I am applying for"; close on a specific thing in the role, with stock closes named and banned. `revise.py`: a fix must not swap in a generic sentence or stock close. `SKILL.md` shape updated. `style_lint` gained `stock_close` (last body paragraph) and `stock_bridge` warnings; warnings only, so they spend no revisions and the rubric's code items are unchanged. 699 tests (19 new, Sonnet-written to a spec) | Panels opus-v2/v3: every would_send fail recited the profile and ended on a stock close |
| 2026-10-04 | **`workflow-v3` run** (15 ads): $3.35, **$0.223/letter** ($0.213 without the one-off ANALYSIS_VERSION 3 re-analysis, +16% on v2's $0.184: the writer thinks more, 8,556 vs 7,367 tokens per first draft, and 7 revisions vs 6), 171 s. 15/15 clean (9 on draft 1, same as v2), 0/7 revisions dropped a must-cover item. Measured in code on the returned letters: stock close **0/15** (v2 13/15), stock bridges **0** (v2 11), body words in 8-word runs copied verbatim from the profile **18%** (v2 36%). First drafts blocked by `check_claims` 4/15 (v2 3/15), all fixed by a revision | `evals/results/workflow-v3-loop.md`, `cost-workflow-v2-vs-workflow-v3.md` |
| 2026-10-04 | **Panel opus-v4** (fresh blind Opus panel, workflow-v2 vs workflow-v3, same standard): A/B 118/120 (2 adjudicated N); re-graded workflow-v2 at **57/60** against opus-v3. v3 vs v2: musts 15 vs 15, claims 12 vs 11, detail 13 vs 13 (the same 2 generic ads), **would_send 0 vs 0; no letter flipped**. The targeted habits are gone, but the writer replaced them with new formulas the strict would_send triggers catch: "prepares me to / equips me to" tails (9 letters), "exactly the kind of" (8), restating openers "The role involves..." (5); gap-led "While my X rather than Y" on 2. Two v3 letters were near-sendable (one split the graders). Claims not worse, but new kinds passed `check_claims`: "I have included a link to a two-minute video" (an invented attachment), an invented testing activity/outcome, a degree discipline the profile lacks. `evals/results/grading-opus-v4.md` | v3 hit what it targeted, not the panel's bar |
| 2026-10-04 | **v3 kept as the writer** (no worse on any panel item, better on every measured habit; reverting is one commit). **User decision: don't iterate on letter wording now**; polish later. Follow-ups logged, not done: (1) `check_claims` stage 1 should block "I have included / attached ..." claims as it blocks invented links; (2) `style_lint`'s gap-led pattern misses "While my X rather than Y"; (3) a lint warning for repeated capability tails ("prepares me to" x2) and "exactly the kind of"; (4) the earlier ones: "daily" frequency claims, the copied stock line from the writing sample | Wording polish deferred; the pipeline (7c / Phase 8) comes first |
| 2026-10-04 | Fixed a flaky Phase 7b test (`test_gaps_api::test_days_param_and_range`): it seeded a sighting exactly on the 1-day window edge, and Windows' coarse clock sometimes read both `now`s in the same tick. Seeded a minute past the edge | Test bug, not app code |
| 2026-10-04 | **Phase 8 built: the pipeline runs in the app.** `app/llm/letter/production.py` (what the single worker does next, running it, landing the letter), `view.py` (what the sidebar shows), the idle loop's letters phase (`_letters_phase` in main.py), `/regenerate`, API (`/jobs` gained `letter_run`; `GET /jobs/{id}/letter-info`), three preferences (`letter_loop_enabled` True, `letter_loop_min_score` 85, `letter_engine` agent; bounded in `PreferencesUpdate`, validated on read by `preferences.letter_settings`), and the sidebar + both profile editors. **Follows §6, which the task prompt did not restate:** the master switch off means every letter is the one-shot; matches from `auto_cover_letter_min_score` up to the loop bar get the one-shot (~3¢), at or above it the pipeline (~20¢); the effective bar is the HIGHER of the two settings, and the sidebar says so. Schema doc first: no DDL, one new `letter_runs.status` value `cancelled`. 846 tests (147 new, Sonnet-written to a spec in 3 files) | Plan §6, §10 row 8 |
| 2026-10-04 | **Phase 8 design choices.** (1) **Order:** answered runs resume first, then the best-scored match without a letter. A `waiting_user` run is not work: its match is skipped, so it never blocks the loop and is never retried. (2) **Retries:** a match is skipped while it has a `running` / `waiting_user` / `answered` run, after 2 failed attempts (`failed`, or `budget_stopped` with no draft), and for 30 minutes after the latest one, so a deterministic failure can't spend money every 20 s. An explicit regenerate ignores these. (3) **Account limit:** the USD guard / daily quota with no draft marks the run `cancelled` (not a failed attempt), pauses letters 10 minutes and shows the banner, as the one-shot did. (4) **Orphans:** a run still `running` at start-up (a crash) is marked `failed`, or it would block its match forever. (5) **Landing:** the best draft goes to `cover_letters.generated_content` even when the run stopped short of a clean one; `edited_content` is never written (a regenerate over edits keeps them and the status `edited`; the sidebar offers "Load generated draft", unsaved until Save). The one-shot got the same status rule. (6) **Regenerate** (the "Polish" button of §6) runs the full pipeline whatever the score when the master switch is on, supersedes a run waiting on a question (`cancelled`), and now runs on the single worker (`_bg_executor.submit`, not BackgroundTasks) so a minutes-long run can't overlap the idle loop's LLM job. (7) **SSE:** `letter_run_started`, `letter_run_waiting`, `letter_run_done`, `letter_run_failed` (+ the existing `cover_letter_ready`); the plan's per-step `letter_run_step` is NOT built. The sidebar debounces reloads and never reloads while the user is typing in the list. (8) **Open issues, "not claimed" list, eligibility notes and application instructions are derived from the run's persisted state**, shown only while the run's final draft is still the letter's text. (9) `llm_run_budget_usd`, `letter_max_drafts` and `letter_max_tool_calls` (§7) are still not read by anything (wired later the same day, see below) | Plan §5.5, §5.7, §6 |
| 2026-10-04 | **Found by driving the real sidebar page** (Playwright + stubbed `chrome.*`, a scratch copy of the test DB, idle loop off, every LLM function raising so the run made no model call, 35 checks): the profile editors had no `university_project` / `assignment` option (the standalone: `assignment`), which `ask_user` produces, so saving the profile retyped such rows to `job` and dropped their origin tag. Both editors fixed; the smoke now saves the profile and checks type and origin survive | A latent 7b bug that only the UI exposes |
| 2026-10-04 | **Live verification ($0.1375, user-approved)**: the unpacked extension loaded in Playwright's Chromium (real `chrome.*`; port 8000 and every external host blocked at the network layer) against a scratch copy of the test DB running the REAL idle loop, with one crafted ad (two essential skills the test profile lacks; every other no-letter match hidden). Live SSE moved the card pending -> Writing -> Needs your answer with no clicks; **`ask_user` fired on a real model analysis for the first time** (Kubernetes and Salesforce, both essential headlines; work rights stayed a card note). A Yes went through the real small-model parse (capstone, Feb-Oct 2024, the user's own words, `origin='ask_user'`), confirm saved it and reset that requirement; a No left Salesforce out and remembered it. The loop resumed the `answered` run by itself: re-matched only Kubernetes, draft 1 passed all three checks, finish (12 steps, 8 tool calls, agent engine); the letter landed and the card went Ready over SSE; the letter frames the capstone honestly ("in a university setting rather than production") and the notes list Salesforce as left out. The REAL/TEST pill flips storage and reloads; `chrome.tabs.create` opened a tab. Still not verified: Chrome's actual side panel (the page was opened as an extension tab) and a capture from a live Seek page (not part of Phase 8) | Closes the Phase 8 "not verified" list |
| 2026-10-04 | **Phase 7c built: the side outputs.** Tools `answer_screening` (mid), `suggest_learning` (small), `suggest_resume_tweaks` (mid) in `app/llm/letter/tools/`, their rules in `app/llm/letter/side_outputs.py`, toggles `screening_answers_enabled` / `learning_suggestions_enabled` / `resume_advice_enabled` (all True; `DEFAULTS` -> `PreferencesUpdate` -> `letter_settings()["side_outputs"]` -> `production` -> `engines`), results in `letter_runs.state.side_outputs` (no DDL), shown by `view.letter_info` as collapsed sidebar sections (Copy on screening answers) plus three Personalise checkboxes. The Quick-Apply overlay does NOT show them (optional, skipped). 1008 tests (162 new, Sonnet-written to a spec in 2 files, reviewed) | Plan §5.2, §5.6, §6 |
| 2026-10-04 | **Phase 7c design choices.** (1) **Budget:** side calls count in `budget.side_calls`, not against `max_tool_calls` (a full workflow run is already 14/15; charging them would let an extra crowd out a revision). Each runs at most once, so three is their bound; the run's USD cap still covers them. (2) **Off = absent:** a disabled tool is not offered and the side-output prompt section is not added, so all three off is exactly the Phase 8 run (tested). The enabled set is fixed in the state when the run opens, so a resumed run keeps it. (3) **Finish requires every DUE side output** (enabled, not run, has something to work on), enforced in code (§5.5 "call each enabled side-output tool once"); if the letter's tool cap ends the run with a clean letter, code runs the due ones itself (not past the USD cap). (4) **Readiness:** screening and learning wait until the gaps are settled (so an ask_user Yes can feed an answer); résumé notes wait for the final letter (`guardrails.letter_final`: clean, or best draft when out of drafts). (5) **A failed side output is shown, never fatal** to the letter, never retried. (6) **Workflow:** the same tools in a fixed order after the letter, so the engines still differ only in step order. (7) **Screening grounding:** `check_claims` stage 1 in code (pointers resolve, quotes in the answer, no invented links), no stage-2 judge (drafts the user reads one by one; a second mid call wasn't worth it for v1); profile facts citable as `fact:work_rights` etc. (8) **Learning:** only gaps the USER confirmed (`UserDecision.assumed` marks the evals' unasked leave-outs), ordered in code by the to-work-on rank. (9) **Résumé notes:** code drops unresolved pointers, keywords not in the ad, cuts not in the CV/profile, gap advice for supported items; reads the default `user_cvs` row, else the profile | Plan §5.5, §5.6 |
| 2026-10-04 | **Phase 8 follow-up: `view.not_claimed`** no longer lists gaps with no `skill` name the user wasn't asked about (attitudes, "interest in <company>", general duties): "Nothing in your profile backs this" was misleading. A skill-less must-have the user said no to keeps its line; an evals-only assumed leave-out reads "Left out without asking you". **Checked and NOT a problem (corrected 2026-10-04):** an attitude/interest item rated a headline gap ("Share interest in Fujitsu", real.db run 2) goes into the writer's DO NOT CLAIM list, which I first suspected suppresses interest in the letter. The real Fujitsu letter does say "I am drawn to the 2027 Fujitsu Graduate Program" and "I want to join this program", so the writer expresses interest anyway; no change needed (watch for it on other ads) | Misleading reason in the sidebar |
| 2026-10-04 | **No-cost UI check (7c):** the sidebar page in Chromium (stubbed `chrome.*`, page served from an intercepted localhost origin) against a scratch copy of the test DB on port 8001, idle loop replaced by a sleeper, every `complete_*` raising, port 8000 and all other hosts blocked; three seeded runs (all outputs; a failed one; none enabled): 27/27 checks (sections, collapse, Copy to clipboard, partly-covered/issue/answer-yourself notes, failure message, no sections for a letter-only run, the attitude item gone from "left out", the three toggles saved/reloaded) | Not verified: Chrome's real side panel |
| 2026-10-04 | **`agent-v2-side` run** (15 ads, agent, writer v3, all three side outputs on; user-approved up to $4.50): **$3.41**, $0.227/letter, 229 s. 15/15 clean (10 on draft 1), 5 revisions, 0 dropped a must-cover item, 0 hit a cap. **Path: the same as the workflow's on 15/15 (side tools left out of the comparison); 0 guardrail refusals; finish was never refused for a side output.** `suggest_resume_tweaks` ran 15/15, always as the last tool before finish, on the final draft (5 of them a draft 2); 0/15 before the letter was final; 2 items dropped by code in one run. It costs **$0.0116/letter** (mid, 3.9k in, 0.9k out + 1.5k thinking) and 19 s, plus about one orchestrator turn. `answer_screening` and `suggest_learning` ran 0/15 and were correctly marked "not needed": the set has no screening questions and, with the leave-out policy, no user-confirmed gap. So **the eval cannot show whether the agent uses its new choices well**: with one applicable side tool and a clear gate it had no real choice. Testing the other two needs a crafted ad (questions + a remembered "no"), as Phase 8's live check did. Against agent-v1 ($0.178, 182 s, writer v2): the +$0.049 is mostly writer v3 (generate 8.5k thinking tokens, 74 s/call) plus résumé notes; against workflow-v3 ($0.213 like for like, 171 s) it is +$0.014: résumé notes $0.012 + the orchestrator $0.018, less one fewer revision. The notes read as grounded (every item cites profile sentences; gap advice says to present honestly), but use US spelling ("emphasizing"): the AU spelling rule is the writer's, not theirs (follow-up). `evals/results/agent-v2-side-loop.md`, `cost-agent-v1-vs-agent-v2-side.md` | Plan §9; the side outputs' cost and placement |
| 2026-10-04 | **The three per-run limits are wired** (they were in `DEFAULTS` but read by nothing; the caps were literals in `Budget`). `letter_max_drafts` (1-5, default 3), `letter_max_tool_calls` (6-40, 15) and `llm_run_budget_usd` (0.05-5.0, $0.50): `preferences.LIMIT_BOUNDS` -> `PreferencesUpdate` (same bounds) -> `letter_settings()["limits"]` (a bad stored value reads as the default) -> `production` -> `engines.run_letter(limits=)` -> `open_run`, which copies them into `state.budget`, so a paused or resumed run keeps the limits it opened with. `Budget`'s defaults now come from the same constants. The workflow's revision count follows `max_drafts - 1` unless a caller passes `max_revisions`. Sidebar: three number inputs in Personalise, out-of-range values snap back unsaved, and a note warns when the tool-call cap can't fit the drafts (4 per draft + 2). The evals still run on the defaults. Verified: 1126 tests (118 new in tests/test_letter_limits.py, Sonnet-written to a spec, reviewed), and the controls driven in Chromium against a scratch DB, 14/14 | Plan §7; Phase 8 design choice (9) |
| 2026-10-04 | **Live gap + screening run ($0.2006, user-approved; scratch copy of real.db, real profile)** on job 18 (Tabcorp Software Engineer, first must-have "Experience in mobile development using React Native or Flutter"). No ad in real.db or the eval set has screening questions, so three were appended to the copy's ad text (work rights, years of React Native/Flutter, salary). `analyze_job` found all three. **`ask_user` did not fire**: `match_profile` rated the must-have *partial* ("Experience with React.js, but no React Native or Flutter"), which its rules allow (same kind of activity, related tool), so no gap was confirmed and `suggest_learning` was correctly "not needed". Path: analyze -> match -> generate -> 3 checks (all passed on draft 1) -> answer_screening -> suggest_resume_tweaks -> finish; 9 orchestrator turns, 0 refusals. **Screening answers: all three came back `covered: no` with a note telling the user to answer; nothing was invented.** Q1 because the real profile's visa/work status is empty, Q2 honestly (no React Native/Flutter), Q3 (salary is never in a profile). So the verified-answer path is still untested on a model. Résumé notes were grounded and flagged a real profile problem: the real profile's summary is the single letter "s". The letter frames the partial honestly ("While my experience is in web development rather than mobile development...") but that is the gap-led "While my X rather than Y" shape `style_lint` misses (known follow-up). Cost: generate $0.109, orchestrator $0.021 (9 turns), check_claims $0.021, match $0.016, answer_screening $0.012, résumé notes $0.011, analyze $0.010 | Side outputs on a real model; ask_user and suggest_learning still unproven on the agent |
| 2026-10-04 | Eval spend on 2026-10-04: **$6.76** (workflow-v3 $3.35 incl. $0.14 re-analysis; agent-v2-side $3.41; the live gap run $0.20, on a scratch copy of real.db). The three Opus panel agents used no Gemini | Running total |
| 2026-10-04 | **Phase 9 rescoped to one item: read the live screening questions from Seek's Quick Apply page** (§10.1). Blocked on the parked apply-flow detection, a live session the user drives; selectors are not guessed. Open questions named, not decided: questions arriving after the run has finished, multiple-choice answers, per-job vs per-run storage. The other old Phase 9 items moved out so nothing is lost: `research_company` → `future_work/research-company.md`; "hiring manager" framing/addressing → `future_work/letter-framing-and-addressing.md`; learn-from-edits style notes → `future_work/learn-from-edits.md` (own files, not folded into the voice toggle: each has its own trigger and data source); the "Polish" button is already built (Regenerate, Phase 8 design choice 6) | Wording work is put off (2026-10-04 decision); the screening item is the only one with a concrete user-facing gap |
| 2026-10-04 | **Vertex batch inference checked: possible, not worth it; not built** (§10.2, sources there). All three of our models are supported, the Pro *preview* included; 50% off; works on the `global` endpoint; JSONL in Cloud Storage or BigQuery; most jobs finish within 24 h of starting, plus up to 72 h queueing; 200k requests / 1 GB per job; no predefined quota. The trial credit covers Vertex and Cloud Storage (inferred: only AI Studio and partner MaaS are excluded). The Developer API Batch API is irrelevant (needs an API key; the trial credit can't pay for it). Saving at our volume: extract + match ≈ $0.0022/job (eval.db `llm_usage`), so ≈ $0.0011/job, ≈ $4 over the trial at the §9 envelope. Letters can't be batched (multi-step, latency). **Unverified:** whether the org's policies allow the bucket; whether `responseSchema` / `thinkingConfig` carry over in batch requests. Revisit for multi-user volume or a bulk re-score after a profile change | The saving is trivial next to the 24 h lag on scores and the new moving parts (bucket, poller, result mapping) |
| 2026-10-04 | **Phase 9 scope readjusted (user): help with the Quick Apply questions, not just answer them.** Each question is `user` (personal, legal, demographic or logistics: work rights, identity, salary, how you heard; motivation by default; no AI help, never sent to the LLM) or `assisted` (relates to the ad: languages, frameworks, years of experience). Assisted questions show what the employer wants (requirements checklist) beside what the user has (profile evidence); wanted-but-missing options go through the `ask_user` gap memory (ask once, remember the no). Leaning: runs outside the letter run, questions stored per job, labels derived. Split into 9a capture / 9b assist / 9c drafts + evals. Waits on the live session for the DOM | Most Seek questions aren't the app's to answer; the useful help is showing which options the ad wants and which the profile honestly backs |
| 2026-10-04 | **Phase 9 revised from 5 real Quick Apply questionnaires (22 questions; `docs/quick-apply-samples.md`).** 17 of 22 were `user` (work rights on all 5 jobs). Sorting runs cheapest first: question bank → Seek library id table (`AU_Q_<n>`, stable across jobs) → `user` keyword filter (employers also ask work rights and salary in their own words) → templates for Seek's generated per-role questions (32-hex ids, new per role) → small model once per genuinely new question. Assisted questions get a strategy (`years_role_bracket`, `years_skill_text`, `skill_in_role_yes_no`, `skill_multi_select`, `free_text_describe`). **User decision: absorb every new question into a question bank** (two new tables; schema doc first) so the app learns how to answer it next time; never store selected or typed answers. The live-session prerequisite is met for the questions step (markup settled from the samples) | The bank turns a per-job model call into a one-off per question, and builds the 9c eval set as a side effect |
| 2026-10-04 | **Phase 9a built: capture + question bank, no LLM.** Two tables (`screening_questions`: global bank, identity `lib:AU_Q_<n>` or `fp:<32 hex>` over normalised text + type + sorted option labels, kind/strategy/parameters/classified_by, status new→confirmed, `times_seen` = distinct jobs; `job_screening_questions`: per job, form order, Seek's per-form id, field name, option values; only added/updated, never removed by a capture), migration `b4d8e2f6a913`, schema doc first. Sorting layers 2–4 in `app/screening/sort.py` (library table `AU_Q_6/8/13` user, `AU_Q_136` multi-select, `AU_Q_218` skill-in-role; `user` keyword topics work_rights / identity / legal / salary / notice / work_arrangement / source / motivation, deliberately specific so "office manager" or "Microsoft Office" don't trip them; templates "years' experience as <role>" with options and "worked in a role which requires <skill> experience"). A bank row is only re-sorted while `unknown`; a user correction (`classified_by='user'`) is never overwritten. **All 22 sampled questions sort as the samples doc records**: 21 sorted (17 user, 4 assisted), S5's React Native years question left `unknown` for 9b's model. Content script: an apply page (`/job/{id}/apply/...`) no longer runs the detail-page branch (before, a Quick Apply visit could have marked the job **expired**, since it has no description); a debounced MutationObserver re-reads the step on single-page changes and only posts when what it read changed; a job never captured gets a stub row from the header `<h1>`. Overlay bottom-left lists each question as "Yours to answer (topic)" / "We'll help — coming in 9b" / "New — not sorted yet". Review list in the profile editor (Confirm, or correct kind/strategy). `scripts/export_question_bank.py` writes the bank in the samples doc's shape. `run_api.py test --db <file>` for scratch DBs. **Verified:** 1261 tests (135 new); Playwright with the unpacked extension against a scratch DB on `run_api.py test` (port 8001, `LLM_PROVIDER=stub`), pages built from the scrubbed samples via request interception: 56/56 checks (kinds in the overlay, 18 bank rows for 22 questions, repeat visit adds nothing, SPA step changes, no click/input/change/submit event, no navigation, no pre-filled answer or masked text stored, no LLM row, no request left the machine). **Finding:** Chromium 148 blocked the content script's fetch to localhost (Local Network Access) until the harness disabled that check; the user's own Chrome may show a one-time "access devices on your local network" prompt on Seek. **Not verified (needs the live check the user drives):** the real Seek markup through the extension, the step-change behaviour on Seek's real SPA, the header `<h1>`, and the review list's use in practice | The bank is the 9b/9c groundwork; sorting needs no model for 21/22 samples |
