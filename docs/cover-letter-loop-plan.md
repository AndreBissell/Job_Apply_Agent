# Cover-letter refinement loops + Gemini migration — PLAN (living document)

> **Status: DRAFT v0.2 — 2026-10-01. Open to change.**
> **Budget window:** new Google Cloud $300 trial, ~2026-10-01 → ~2026-12-30.
> That ends before Gemini Flash prices double on 2027-01-01, so the intro
> prices in §3 hold for the whole trial.
> This is a starting base, not a spec. Nothing here is locked in until it has
> been tested on real jobs. Every section marked 🧪 is an experiment whose
> outcome may rewrite the section. Every number marked *(est.)* is a guess to
> be replaced by real measurements from Phase 1's usage logging. When a
> decision is made or a test changes the plan, edit this file and add a line
> to the **Decision log** at the bottom. Don't let it go stale.

---

## 1. Goals

1. **Better cover letters, tailored to the specific employer.** Today one
   `complete_text` call writes each letter and nothing checks it. The letter
   should answer as many of the job's stated requirements as the profile can
   *honestly* support, and should read as well-written and like the user, not
   like an AI.
2. **Two review agents running in a loop** after the first draft:
   - **Employer agent**: reads the letter as the hiring manager would.
     "Which of my requirements did this letter answer? Which did it miss?"
   - **Cover-letter perfectionist** (writing agent): grammar, structure,
     formatting, how the candidate comes across, AI tells, the user's voice.
3. **Move all LLM calls to Google Gemini.** This uses the $300 Google Cloud
   trial credit. See open question Q1 about when the trial expires.
4. **Stay cost-aware.** The budget is generous, but we will do a lot of
   testing. Use the strongest model only where it changes the result, and
   measure every call.
5. **Resume tailoring notes** (advice, not a rewritten CV), behind their
   own on/off toggle. See §5.4.

### Non-goals (for now)
- Auto-submitting anything. The user still reviews and submits every
  application.
- Generating a new CV document per job. `user_cvs` is user-level by design
  (see docs/database-schema.md). Decided 2026-10-01: notes only (§5.4).
- Optimising against an "AI-detector %" as the main target (see §5.3).

---

## 2. What exists today (baseline we're improving)

| Piece | Today |
|---|---|
| Provider | OpenAI: `gpt-5-nano` (JSON tasks), `gpt-5-mini` (letters) |
| Letter generation | `app/llm/cover_letter.py`: one `complete_text` call, no review |
| Letter input | name, quals, experiences + evidence map, job **summary / responsibilities / skills** (extracted), match reasoning + gaps |
| Letter input — missing | **the raw job ad text** (`raw_description`), company context, any sample of the user's own writing |
| When | Idle loop Phase 2, automatically for matches ≥ `auto_cover_letter_min_score` (default 75); or `/jobs/{id}/regenerate` |
| Storage | `cover_letters` (one per match): `generated_content`, `edited_content`, `status` |
| Cost tracking | None. `client.py` does not record token usage |

The current letter prompt never sees the original job ad, only our own
extracted summary of it. Employer wording and stated "must-haves" are lost
before the letter is written. Fixing this is part of Phase 2 and costs almost
nothing.

---

## 3. Model selection (Gemini)

Prices are from https://ai.google.dev/gemini-api/docs/pricing, checked
2026-10-01, paid tier, prompts ≤200k tokens, per 1M tokens. **Re-check before
locking in.** Introductory prices on the 3.6–3.8 Flash models **double on
2027-01-01**. Thinking tokens are billed as output.

| Model ID | Status | In / Out per 1M | Notes |
|---|---|---|---|
| `gemini-3.1-flash-lite` | Stable | $0.25 / $1.50 | Shutdown 2027-05-07 → `gemini-3.5-flash-lite` ($0.30 / $2.50) |
| `gemini-3.8-flash` | Stable | $0.75 / $3.75 (intro) | Newest Flash; $1.50 / $7.50 from 2027-01-01 |
| `gemini-3.1-pro-preview` | **Preview** | $2.00 / $12.00 | Strongest model; preview = may change or rate-limit |
| `gemini-2.5-*` | Stable but restricted | — | "Limited to existing users"; don't build on it |

Also available: Batch API at 50% off (latency up to hours); context caching;
Google Search grounding (5,000 free requests/month across 3.x, then $14 per
1,000).

### Proposed tier mapping (🧪 to be validated in Phase 2)

| Tier (env var) | Default model | Used by | Why |
|---|---|---|---|
| `GEMINI_MODEL_SMALL` | `gemini-3.1-flash-lite` | quick-screen, extraction, matching, search re-rank | High-volume structured JSON. Replaces `gpt-5-nano` |
| `GEMINI_MODEL_CRITIC` | `gemini-3.8-flash` | employer agent, writing agent, employer rubric | Needs real reading judgement, runs several times per letter, structured output |
| `GEMINI_MODEL_WRITER` | `gemini-3.1-pro-preview` | first draft + each revision | The only text an employer reads. Fallback: `gemini-3.8-flash` |

Things to test before trusting this mapping:
- **Matching on Flash-Lite vs 3.8 Flash.** The match score decides which jobs
  get letters. Run `scripts/check_matching.py` on both and compare against the
  expected bands. If Flash-Lite is noticeably worse, give matching the critic
  tier (≈$0.004/job *(est.)*, still small).
- **Writer: Pro vs 3.8 Flash.** If a looped Flash letter is as good as a
  looped Pro letter in a blind comparison, use Flash and save ~3×.
- **Critic ≠ writer.** Using a different model to judge than to write reduces
  "grading your own homework" bias. Keep them different unless tests say
  otherwise.

---

## 4. Phase 1: Switch the provider to Gemini (with tiers + cost logging)

All of this lives in `app/llm/client.py`, still the **one** place that knows
about providers and models.

1. **Tiers instead of one model per provider.** Add a `tier` argument:
   `complete_json(..., tier="small")`, `complete_text(..., tier="writer")`.
   Valid tiers are `small` / `critic` / `writer`. Each provider maps tiers to
   models from env (`GEMINI_MODEL_SMALL/CRITIC/WRITER`). OpenAI keeps its two
   vars as a dormant fallback (`critic` maps to `OPENAI_MODEL_SMALL`).
   Callers name a tier and never name a model.
2. **Default provider = `gemini`.** Update `.env.example`, `CLAUDE.md` (LLM
   Layer section), and `scripts/check_llm.py` so it checks all three tiers.
3. **Structured output.** Keep the existing Gemini `response_schema` path
   (Pydantic). Callers keep calling `schema.model_validate()`.
4. **Temperature and thinking.** ⚠️ *Verify against current docs:* Gemini 3
   guidance has been to leave temperature at the default 1.0, because lowering
   it can cause looping or degraded output. If that still holds, drop
   `temperature` for 3.x the same way `_generate_openai` already does for
   GPT-5. Set a **thinking level/budget per tier**: low for `small`, medium for
   `critic`, high for `writer`. Thinking tokens are billed as output, so this
   is the biggest cost lever.
5. **Usage + cost logging (new, essential).** Every call records task, tier,
   model, input / output / thinking / cached tokens, estimated USD and
   timestamp into a new `llm_usage` table. Prices sit in one dict in
   `client.py`. This replaces every *(est.)* in this file with real numbers.
6. **Budget guard.** `profiles.preferences` gets `llm_daily_budget_usd` and
   `llm_total_budget_usd`. When a cap is hit, `writer` and `critic` calls raise
   a `BudgetExceededError`. The idle loop treats it like `DailyQuotaError`
   (back off) and the sidebar shows a banner. The `small` tier keeps running
   so scanning still works. This protects against a runaway loop bug, which
   is a bigger risk than normal spend.
7. **Rate limit.** `LLM_RPM=8` was sized for the old free tier. Paid-tier
   limits are much higher. Raise it (e.g. 30) after checking the project's
   real quota in the Cloud console. Keep the single-worker executor.
8. **Prompt order for implicit caching.** Put the identical blocks (profile,
   then job ad) first and the per-call instructions last, so repeated calls in
   one loop hit Gemini's implicit prefix cache. Free win; verify with the
   `cached` token counts from step 5.
9. **Score drift on switchover.** Gemini scores will be calibrated
   differently from gpt-5-nano scores. The suggestion miner and the 75
   threshold both depend on that scale. Options:
   (a) bump `profiles.profile_revised_at` at switchover, which reuses the
   existing 0.35 down-weighting of older scores for free;
   (b) re-score recent matches;
   (c) accept the drift.
   Leaning towards (a). Decide in Phase 1.

**Done when:** pytest passes; `check_llm.py` passes on all 3 tiers;
`check_matching.py` bands hold on Gemini; one real scan → extract → match →
letter run shows rows in `llm_usage` with plausible costs.

---

## 5. The two agents

### Design principle: critics critique, one writer writes

Neither agent edits the letter directly. Both return **structured critiques**
(JSON). A single **writer** call per round applies both critiques together.

Why: if each agent rewrote the letter itself, they would fight. The employer
agent adds a requirement-heavy sentence, the writing agent trims it for flow,
and the next round puts it back. One writer that sees both critiques can make
trade-offs. It is also cheaper: one expensive writer call per round, not two.

### 5.1 Employer agent ("hiring manager")

**Step A: Employer rubric (once per job, cached, critic tier).**
Built from the **raw job ad** plus extracted fields. Output (JSON):
- `must_haves[]`, `nice_to_haves[]`: each requirement stated in the
  employer's own wording.
- `what_they_really_want`: 2–3 sentences reading between the lines.
- `tone`: e.g. corporate, startup, public sector.
- `keywords_to_mirror[]`: terms that recruiter/ATS screening looks for.

The rubric is cached per job (new column or table, see §7) and reused every
round and every regeneration.

**Step B: Critique (each round, critic tier).**
Input: rubric + profile evidence + current letter. Output (JSON):
- For each requirement, a `status`:
  - `addressed_strongly`: specific evidence given.
  - `addressed_weakly`: mentioned, but no concrete evidence.
  - `missed_but_supported`: the profile has evidence and the letter doesn't
    use it. **This is the main fix target.**
  - `not_supported`: the profile has no evidence. Leave it out, or frame it
    honestly as eagerness to learn. **Never invent.**
- `unsupported_claims[]`: anything in the letter not backed by the profile.
  **Hard gate:** any entry here must be fixed before the letter can pass.
- `coverage_score` 0–100: share of requirements the profile can support that
  the letter actually covers.
- `would_interview` 0–100 plus one sentence of reasoning, from the hiring
  manager's perspective.
- `top_fixes[]`: at most 3, ranked.

⚠️ **Fabrication risk.** "Answer as many requirements as possible" pushes the
writer to stretch the truth. The `not_supported` status and the
`unsupported_claims` gate exist to counter that. A letter that honestly skips
a requirement beats one that fakes it, because the claim may come up in the
interview.

### 5.2 Cover-letter perfectionist (writing agent)

**Step A: Free deterministic lint (pure Python, no LLM call).**
`app/llm/style_lint.py`:
- A banned/overused phrase list: "I am excited to apply", "delve",
  "tapestry", "I am confident that", "proven track record", "fast-paced
  environment", "leverage", etc. The list grows as we spot new ones.
- Em-dash count, word count (target ~250–400 🧪), paragraph count, sentence
  length variance (low variance is a strong AI tell).
- Every paragraph opening with "I".
- Sign-off and name present, no placeholders like `[Company]`.

Output is a list of flags passed into the critique and the writer prompt.

**Step B: Critique (each round, critic tier).** Output (JSON):
- `issues[]`: `{quote, problem, suggestion}` for grammar, clarity, awkward
  phrasing and formatting.
- `ai_tells[]`: generic or template-feeling passages, quoted.
- `voice_match` 0–100: how close it sounds to the user's writing samples (§5.3).
- `impression`: one or two sentences on how the candidate comes across
  (confident vs arrogant, specific vs vague).
- `writing_score` 0–100.
- `top_fixes[]`: at most 3.

### 5.3 "AI %" and "sounds like me": honest constraints

- **AI-detector scores are unreliable.** They produce false positives on human
  writing, and an LLM cannot measure "AI %" of text. Optimising a letter
  against a detector makes it worse, not more human. Plan: use the lint (§5.2A)
  plus the critic's `ai_tells` as the main signal. 🧪 Optionally call one
  external detector (e.g. GPTZero/Sapling, paid API, cost TBD) to **report**
  a score, never as a loop target. Q6.
- **Voice needs examples of the user's writing.** The profile currently holds
  none. Two sources:
  1. **Writing samples** (Q5): 2–3 things the user wrote themselves (an old
     cover letter, a uni essay intro, a long email). Stored in
     `profiles.preferences` or a small new table. Fed to the writer and critic.
  2. **The user's own edits (free, grows over time).** Every time the user
     edits a letter, `generated_content` vs `edited_content` shows what they
     change. 🧪 Later: summarise recurring edits into a "style notes" block,
     e.g. "cuts adjectives, prefers 'I built' over 'I was responsible for'".

### 5.4 Resume tailoring notes (decided 2026-10-01)

Advice only. The tool never rewrites or stores a per-job CV, so `user_cvs`
is unchanged.

- **Toggle:** `resume_advice_enabled` (bool, default `True`) in the
  Personalise panel. When it is off, no notes are generated and **no LLM call
  is made**, so you can choose cover-letter-only.
- **Source:** the employer agent already knows the requirements and what the
  letter did or didn't cover. Its final pass outputs a `resume_notes`
  object 🧪. If folding it into the critique hurts critique quality, use one
  separate critic-tier call on the final letter instead:
  - `lead_with[]`: CV experiences/bullets to move to the top for this job.
  - `keywords_to_mirror[]`: employer wording to reuse (ATS screening).
  - `consider_cutting[]`: content that is irrelevant for this role.
  - `gaps_to_address[]`: requirements a CV line could cover better than the
    letter can.
- **Grounding:** notes can only reference experience that exists in the
  profile, the same "never invent" rule as the letter. If a CV is stored in
  `user_cvs` it is used as input; without one, the notes work from profile
  experiences.
- **Display:** a collapsible "Resume tips" section on the job card and the
  Quick-Apply overlay (Phase 6).

---

## 6. The loop

```
                ┌──────────── employer rubric (once per job, cached) ────────────┐
                ▼                                                                │
  DRAFT (writer) ──► round r = 1..MAX_ROUNDS                                     │
                       ├─ style_lint(letter)            (free)                   │
                       ├─ employer_critique(letter)     (critic) ◄───────────────┘
                       ├─ writing_critique(letter)      (critic)
                       ├─ STOP if passes (see below)
                       ├─ STOP if no improvement vs best so far (plateau)
                       └─ REVISE (writer) using both critiques' top_fixes + lint flags
  FINAL = best-scoring version seen (not necessarily the last)
```

Starting stop rules 🧪 (all are tunables in `profiles.preferences`):
- **Pass:** `coverage_score ≥ 80` AND `writing_score ≥ 85` AND
  `unsupported_claims` empty AND no lint errors.
- **Max rounds:** 3. The hypothesis is that round 1 captures most of the gain.
  Phase 2 testing should confirm or kill this.
- **Plateau:** combined score improves by less than 3 points over the best so
  far, so stop.
- **Keep best, not last.** Revisions can make a letter worse, so return the
  highest-scoring version.

Revision prompt rules: apply the fixes; keep anything the critiques praised;
never add claims missing from the profile; keep within the word-count range.

### When the loop runs (decided 2026-10-01)
Both settings live in `profiles.preferences` and are editable in the
sidebar's Personalise panel:
- **`letter_loop_enabled`** (bool, default `True`): master on/off switch,
  shown as a clear toggle. When it is off, every letter stays a single-shot
  draft and the agents never run.
- **`letter_loop_min_score`** (int 0–100, default `85`): matches at or above
  this score get the full loop automatically, in the idle loop's
  cover-letter phase.
- **Interaction with the existing threshold.** A letter only exists at or
  above `auto_cover_letter_min_score` (default 75). Matches between 75 and 84
  get a one-shot draft; 85+ get the loop. If the loop threshold is set *below*
  the letter threshold, the effective bar is the higher of the two, because
  there is no letter to polish below it. The sidebar should explain this next
  to the control.
- *Nice-to-have, not committed:* a per-letter **"Polish"** button to run the
  loop on demand for a job below the bar.

### Cost per letter *(est.)*. Replace with real numbers after Phase 1
Assumes the critic is 3.8 Flash, the writer is 3.1 Pro, and moderate thinking.

| Step | Calls | ≈ Cost |
|---|---|---|
| Employer rubric | 1 (cached per job) | $0.01 |
| Draft | 1 Pro | $0.04 |
| Each round: 2 critiques + 1 revise | 2 Flash + 1 Pro | $0.065 |
| **Full loop, 3 rounds** | ~11 calls | **≈ $0.25** |
| Typical (stops after 1–2 rounds) | 6–8 calls | ≈ $0.12–0.18 |
| For comparison: everything on 3.8 Flash, 3 rounds | | ≈ $0.07 |
| Pipeline per scanned job (screen + extract + match, Flash-Lite) | 3 | ≈ $0.004 |

Rough 90-day envelope: 3,600 scanned jobs ≈ $15, 300 looped letters ≈ $75,
testing ≈ $50–100. That is well under $300. Money is less of a risk than a
looping bug, which is why the budget guard comes first.

Run times *(est.)*: one looped letter is 1–5 minutes on the single worker. That
is fine for background work, but the sidebar should show progress (§8).

---

## 7. Data model changes (draft; docs/database-schema.md must be updated FIRST)

Per CLAUDE.md, the schema doc is the source of truth. Add these there
first, then write the Alembic migrations. Use the portable column styles
(BigInteger variant, `text("false")` defaults, CASCADE).

- **`llm_usage`** (Phase 1): `id, created_at, task, tier, model,
  input_tokens, output_tokens, thinking_tokens, cached_tokens, cost_usd,
  job_id NULL, match_id NULL`. This feeds the budget guard and cost reports.
  Retention: keep summaries, purge rows older than ~180 days (open).
- **`cover_letter_revisions`** (Phase 5): `id, cover_letter_id FK CASCADE,
  round, content, employer_critique JSON, writing_critique JSON, lint JSON,
  coverage_score, writing_score, created_at`. Lets us see why the loop chose
  what it chose, and is how we tune it. `cover_letters.generated_content`
  stays the chosen final version, so nothing downstream changes.
- **Employer rubric cache**: `job_listings.employer_rubric` (JSON text) +
  `employer_rubric_at`. The rubric depends only on the job, not the user, so
  it belongs on the global job row.
- **Writing samples**: start in `profiles.preferences`. Move to a table only
  if it grows.
- **`resume_notes`** (JSON text, §5.4): stored with the loop output, either on
  `cover_letters` or on the final `cover_letter_revisions` row. Decide in
  Phase 5. NULL when resume advice is disabled.
- **New preference keys** (no migration; `profiles.preferences` is a JSON
  blob, so add each key to `DEFAULTS` in `app/preferences.py`):
  `letter_loop_enabled` (True), `letter_loop_min_score` (85),
  `resume_advice_enabled` (True), `llm_daily_budget_usd`,
  `llm_total_budget_usd` (Q8), plus the §6 stop-rule tunables
  (`loop_max_rounds` 3, `loop_target_coverage` 80, `loop_target_writing` 85,
  `loop_plateau_points` 3).

---

## 8. Phases (order matters: measure before building)

| # | Phase | Output | Depends on |
|---|---|---|---|
| 0 | **Decisions + checks** | Answers to the remaining §10 questions (Q5–Q8); confirm the new trial's credit + exact expiry in Cloud Billing; confirm model IDs via `client.models.list()` on the new key | — |
| 1 | **Gemini migration** (§4) | Tiers, `llm_usage`, budget guard, all callers on Gemini | 0 |
| 2 | **Test harness + baseline** 🧪 | `scripts/letter_lab.py`: fixed eval set of 5–8 real saved jobs. Run one-shot letters (Flash vs Pro, with/without raw job ad). Write every output + cost to a markdown file for a blind side-by-side read | 1 |
| 3 | **Employer agent** | Rubric + critique modules + Pydantic schemas; tested on the eval set | 2 |
| 4 | **Writing agent** | `style_lint.py` + critique; writing samples wired in | 2 |
| 5 | **Loop orchestrator** | `app/llm/letter_loop.py`, `cover_letter_revisions`, stop rules; harness compares one-shot vs 1/2/3 rounds | 3, 4 |
| 6 | **Integration** | Idle-loop trigger gated on `letter_loop_enabled` + `letter_loop_min_score` (§6); SSE progress events; sidebar shows coverage + writing score, the "not supported" list (what the letter deliberately didn't claim), and Resume tips (§5.4). Personalise-panel controls: loop toggle, loop threshold, resume-advice toggle, budget caps. Wired like the existing settings: `DEFAULTS` in `app/preferences.py` → typed field on `PreferencesUpdate` (`app/api/main.py`, `ge=0, le=100` on the threshold) → load/save handlers like `auto_cover_letter_min_score` / `llm_search_suggestions` in `extension/sidebar.js` | 5 |
| 7 | **Later / optional** | Company research experiment (Q3); learn-from-edits style notes; "Polish" button; Batch API (50% off) for the extract/match backlog | 6 |

Phase 2 comes before the agents on purpose. Without a baseline and a
side-by-side harness, we can't tell whether the loop beats a single
well-prompted Pro call. It might not, and that would be worth knowing before
spending money on it.

---

## 9. Risks / things to watch

- **Fabrication creep**: see §5.1. The `unsupported_claims` gate is
  non-negotiable.
- **Over-polish / sameness**: loops tend to converge on smooth, generic
  prose, which is itself an AI tell. Watch for this in Phase 5 and stop early
  if it shows up.
- **Preview model churn**: `gemini-3.1-pro-preview` may change behaviour or be
  replaced. The writer tier is one env var, so falling back to 3.8 Flash is a
  config change.
- **Price changes on 2027-01-01**: Flash prices double. Re-check the
  envelope if the project outlives the trial.
- **Trial expiry (~2026-12-30)**: when credit runs out or the trial ends,
  calls fail unless billing is upgraded. Keep the OpenAI path working as a
  fallback, and re-check model prices before continuing past the trial.
- **Privacy**: profile data goes to Google. Check the paid-tier Gemini API
  data-use terms (paid tier is not used for training, per Google's terms at
  time of writing — verify).

---

## 10. Open questions (answers will reshape this plan)

**Resolved 2026-10-01:**
- ~~Q1 Trial timing~~: a **new** trial with fresh credit, running ~2026-10-01
  → ~2026-12-30. Confirm the exact expiry in Cloud Billing during Phase 0.
- ~~Q2 Loop trigger~~: automatic at **≥85**, with the threshold
  personalisable (`letter_loop_min_score`) and a master on/off toggle
  (`letter_loop_enabled`). See §6.
- ~~Q3 Company research~~: **later, as an experiment** (Phase 7). Risk to
  test then: Seek ads are often posted by recruiters, and a wrong company
  fact is worse than none.
- ~~Q4 Resume scope~~: **tailoring notes only**, behind a
  `resume_advice_enabled` toggle so the user can choose cover-letter-only. See
  §5.4. Still to check: whether `user_cvs` has a CV stored today; the notes
  work either way.

**Still open:**
- **Q5 Writing samples.** Can you provide 2–3 pieces of your own writing for
  voice matching?
- **Q6 AI detector.** Report-only external detector score (paid API), or rely
  on the lint + critic only?
- **Q7 Letter shape.** Target length, and Australian spelling (likely yes,
  given Seek AU)? Address to a named contact when the ad has one?
- **Q8 Budget caps.** Daily / total USD caps for the guard. Suggested starting
  values: $5/day, $200 total.

---

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-10-01 | Plan drafted. Critics critique, one writer revises | Avoid agents undoing each other's edits; fewer expensive calls |
| 2026-10-01 | Measure (Phase 2 harness) before building the loop | We don't yet know the loop beats one good Pro call |
| 2026-10-01 | Budget window = new $300 trial, ~2026-10-01 → ~2026-12-30 | User started a fresh trial; it ends before the 2027-01-01 Flash price rise |
| 2026-10-01 | Loop runs automatically at ≥85; threshold is a preference; master on/off toggle | User wants polished top matches, a tunable bar, and an easy way to turn the loops off |
| 2026-10-01 | Resume = tailoring notes only, with their own on/off toggle | Cheap, no schema change; user may want cover-letter-only |
| 2026-10-01 | Company research deferred to Phase 7 as an experiment | Recruiter-posted ads make company facts risky; prove the core loop first |
