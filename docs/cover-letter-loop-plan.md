# Cover-letter agent + Gemini migration — PLAN (living document)

> **Status: DRAFT v0.4 — 2026-10-01. Rebased onto an agent design; Q5–Q10
> answered. Phases 0-5 built 2026-10-01/03; Phase 3 hand-check adjudicated by the user and fixed 2026-10-03. Open to change.**
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
  questions live from Seek's Quick Apply page is a Phase 9 item, tied to the
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
  ad text only for now).
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
  `skills` / `qualifications` / `experience_skills` tables, with no new
  columns. 🧪 Optionally tag their origin (e.g. a `source` value like
  `ask_user`) so the profile editor can show "added while applying to X".
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
| Rubric pass rate | user: 0/8 graded; panel opus-v1: 0/15 | v1: opus-v1 2/15, opus-v2 1/15; **v2: opus-v2 0/15** | |
| Avg tokens / run | 867 in, 1,958 out+thinking | v2: 17,149 in, 19,855 out+thinking | |
| Avg cost / run | $0.025 | v1 $0.194; **v2 $0.184** | |
| Avg time / run | 19s | v1 138s; **v2 145s** | |
| Runs that hit the budget cap | n/a | v1 1/15; **v2 0/15** | |
| Runs where the user had to fix a factual error | user: 0/9; opus-v1: 13/15 | v1: 9/15; **v2: 2/15** (opus-v2) | |

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
| 7 | **Agent + ask_user** | `ask_user` (sidebar question card with Yes + text box / No, answer → profile rows, run resume), `gap_decisions` for remembered "no"s, **the to-work-on list (§5.9): `skill` field on `analyze_job`, `gap_sightings` counter hooked into extraction and letter runs, `GET /gaps/to-work-on`, `gap_report.py`**, side-output tools, tool descriptions, orchestrator prompt, `agent.py`. Run evals and fill in the comparison table. Set the `letter_engine` default from the result | 6 |
| 8 | **Integration** | Idle-loop trigger (§6), SSE progress, sidebar: final letter + open issues, "not claimed" list, side-output sections, a "To work on" section in the profile editor (ranked list with counts + clear button, §5.9), Personalise controls (wired like `auto_cover_letter_min_score`: `DEFAULTS` → `PreferencesUpdate` with bounds → `sidebar.js`) | 7 (or 6 if the workflow ships first) |
| 9 | **Later / optional** | Read the live screening questions from Seek's Quick Apply page (needs the parked apply-flow detection); `research_company` tool with search grounding (recruiter-posted ads are a risk); tune the agent's "hiring manager" framing and addressing; learn-from-edits style notes; "Polish" button; Batch API for the extract/match backlog | 8 |

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
- ~~Q3 Company research~~: later, as an experiment (Phase 9).
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
  (Phase 9).

**Still open (small, decide while building):**
- **Q11 Confirm step.** Should `ask_user` show the parsed profile rows for a
  one-click confirm before saving, or save straight away? Leaning towards
  confirm, because a wrong row becomes evidence everywhere.
- **Q12 Origin tag.** Mark profile rows created by `ask_user` so the profile
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
