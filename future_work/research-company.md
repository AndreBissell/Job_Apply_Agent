# research_company: look the employer up with Google Search grounding

**Put off:** 2026-10-01 (plan Q3: "later, as an experiment"); moved out of Phase 9 of
`docs/cover-letter-loop-plan.md` into this file on 2026-10-04.
**Waiting on:** nothing technical. An experiment, not a fix. Worth doing only if graders
keep marking letters down on `specific_detail` for ads that say little about the employer.

## What

A new agent tool, `research_company`, that asks Gemini with **Google Search grounding**
(a Vertex tool on the request, `google_search`) for a few concrete, current facts about the
employer: what it does, a recent project or product, its stated values. The writer uses them
the way it uses `job.company_facts` today, as raw material for the one specific sentence about
this employer.

Today the only company facts are what `analyze_job` lifts from the ad (`company_facts`, up to
5). Short ads give the writer nothing, and the panel's `specific_detail` fails come from that.

## Why it was put off

- **Recruiter-posted ads point it at the wrong company.** Many Seek ads are posted by an
  agency ("our client, a leading ..."). `job_listings.company` is then the recruiter, or empty.
  Researching the recruiter and praising it in the letter is worse than saying nothing. The
  company-name fix (plan Decision log 2026-10-02) only stores a name that appears verbatim in
  the ad, which helps but doesn't say whether that name is the employer or the agency.
- **Facts from the web are a new fabrication path.** Everything the letter says is checked
  against the profile or the ad today. A researched fact needs its own grounding (a source URL)
  and the claims checker has to know about it.
- The letter pipeline came first.

## Cost (Vertex pricing page, checked 2026-10-04)

- **Grounding:** 5,000 grounding *queries* a month free, shared across all Gemini 3 models,
  then **$14 per 1,000 queries**. Billed per query, and one prompt can make several. Input
  tokens supplied by grounding aren't charged. Source:
  https://docs.cloud.google.com/vertex-ai/generative-ai/pricing
- **Tokens:** a mid-tier call about the size of `analyze_job`, ≈ $0.01 (an estimate from
  `analyze_job`'s $0.0096 average in `evals/eval.db`, not measured for this tool).
- At ~20 letters a month we'd stay far inside the free queries, so ≈ $0.01 a letter.
- **Unverified:** that the $300 trial credit pays for grounding (inferred: the trial page
  excludes only AI Studio and partner models); that grounding works together with structured
  output (`responseSchema`) on our models; and that it works on the `global` endpoint. Check
  all three with one call before building.

## How (rough)

- **Where it sits.** A tool in `app/llm/letter/registry.py` that the agent *may* call after
  `analyze_job` and before `generate_letter`, when `state.job.company_facts` is thin (say fewer
  than 2) and the employer is named. Behind a toggle, e.g. `company_research_enabled`, default
  **off**, wired like the side-output toggles (`DEFAULTS` → `PreferencesUpdate` → Personalise
  checkbox). Once per run, a failure is shown and never fatal (the side-output rules in
  `app/llm/letter/side_outputs.py` are the model). Unlike the side outputs it feeds the letter,
  so it should count towards `max_tool_calls`. The workflow engine runs it as a fixed step when
  the toggle is on and the facts are thin.
- **Guarding against the wrong company.** Only run when the employer name is in the ad
  verbatim; ask the model first whether the ad is from the employer or an agency, and skip on
  "agency" or "unsure"; reject results whose location or industry contradict the ad.
- **Grounding the facts.** Each fact is stored with its source URL (from the response's
  grounding metadata, not from the model's text), e.g. `state.job.researched_facts`. The writer
  marks which it used; `check_claims` stage 1 accepts a company claim only if it matches the ad
  or a researched fact with a URL. The sidebar shows the sources so the user can check them.
- **Seek policy.** Grounding searches Google's index from Google's servers, so it's not our
  request to Seek. Still, drop any `seek.com.au` source from the facts, so the tool never ends
  up relaying Seek content.
- **Model:** `mid`. Call it through `app/llm/client.py` with `task="research_company"` and the
  `run_id`, like every other tool, so the cost lands in `llm_usage`.

## How to know it worked

- An eval run with the toggle on vs off on the eval set (`letter_lab.py run`), graded by the
  blind Opus panel (`evals/grading-panel.md`): `specific_detail` should go up on the thin ads,
  and `no_unsupported_claims` must not go down.
- A hand check of every researched fact used in a letter: is it true (open the URL) and about
  the right company?
- A recruiter test: take a few eval ads, make them read as agency-posted, and confirm the tool
  skips them.
- Cost per letter and grounding queries per run from `letter_lab.py cost-report`.
- Record the result and the keep/drop call in the plan's Decision log.
