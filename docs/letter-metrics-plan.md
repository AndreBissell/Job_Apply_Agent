# Cover-letter metrics, v2 (eval-set stage 5)

**Status:** APPROVED by the owner, 2026-10-23. No code yet; build steps in §7, each with a
go-ahead. Thresholds that are still guesses (C7's 10%, G1's 5-term run, the +60-word length
probe) are tuned once on the first real letters, before any grading, then frozen.
**Applies to:** runs on the frozen v2 set (`letter_lab.py --set v2`). v1 runs keep
`evals/rubric.md`'s table, so the old results stay readable.
**Parent plan:** docs/eval-set-plan.md (this is its stage 5; stages 7 and 8 use the human form
and the calibration below).

## 1. Why new metrics

The v1 rubric is 5 code gates and 4 human Y/N items. It no longer measures progress:

- `would_send` was **0/15 on every panel** (one-shot, workflow v1/v2/v3, agent v1). Writer
  changes that clearly helped (stock closes 13/15 -> 0/15, profile copying 36% -> 18%,
  `evals/results/grading-opus-v4.md`) never moved the headline number. A Y/N item that sits at 0
  can't tell two versions apart.
- `supported_musts_covered` sits at 15/15: it confirms the floor but can't tell a letter that
  covers the 3 must-haves and nothing else from one that also covers the strong supporting items.
- Nothing measures how strong the evidence used is, how the letter reads to a recruiter skimming
  it, or whether the same formula is repeated across every letter.

What the owner needs (2026-10-23): a grading task that stays easy across the set's 31 letters (dev 17, test 14), an AI
judge that grades in more detail but is calibrated to the owner's grade, and metrics for
(a) covering as many of the ad's requirements as possible **without lying**, (b) what recruiters
look for, and (c) lightly, how an AI screener reads it.

## 2. What the research says, and what it changes

The deeper review of AI screening (how screeners read, their biases, where human and AI readers
disagree, each finding rated by strength) is in **docs/ai-recruitment-research.md**; its §5 lists
the metric changes it led to, which are folded in below. Summary:

| Finding | Source | What it changes here |
|---|---|---|
| Tailoring is what works: 10.7% callback with no letter, 12.5% with a generic one, 16.4% with a tailored one (7,287 real applications). | ResumeGo field experiment | Requirement coverage and employer-specific detail stay at the centre. |
| 47% of employers say a tailored letter influences the hire, and 53% rank examples of skills as the most important content. | SEEK employer research (au.seek.com career advice) | Concrete evidence is its own dimension (D5), separate from coverage. |
| Once an AI writing tool appeared on Freelancer.com, the link between a letter's word overlap with the job post (TF-IDF) and callbacks fell 51%, and the premium for tailored letters disappeared. Employers moved to verifiable signals such as work history. | Cui, Dias & Ye 2025 (arXiv 2509.25054); Galdin & Silbert 2025 (arXiv 2511.08785) | **Keyword overlap is a diagnostic only, never a target.** Reward specific, checkable profile evidence applied to the ad; penalise echoing the ad's own wording (C3). |
| Recruiters penalise generic or obviously AI text rather than AI use as such: about 1 in 5 would reject an obviously AI-written letter. | TopResume hiring-manager survey 2025 | AI-tell checks (D6) and cross-letter repetition (C4). |
| The first screen of a résumé takes seconds (7.4 s on average). | Ladders eye-tracking study 2018 | First-paragraph test (in D2). |
| AI screeners parse the application, match it against the requirements, then score and rank it (Workday HiredScore letter grades, Oracle 0–5; seekAi shortlists in some markets). | Jobscan; SEEK product pages | The AI-screener view (Layer 4), noted for later. |
| LLM screeners prefer text written by their own model (67–82% self-preference in a résumé experiment). | Xu, Li & Jiang 2025 (arXiv 2509.00462) | Grade with a different model family from the Gemini writer (Opus), and keep an AI-screener score secondary. |
| Splitting each criterion into yes/no checklist questions makes LLM judges much more consistent than Likert scales. | CheckEval, EMNLP 2025 (arXiv 2403.18771) | Layer 2 is a yes/no checklist, not 1–5 scores. |
| An LLM judge should be fitted to ONE expert's pass/fail grades and written reasons: a small calibration split as few-shot examples, a held-out check split, TPR/TNR rather than raw agreement, then freeze it and test it once. | Hamel Husain, "Creating a LLM-as-a-Judge" (hamel.dev) | §6 calibration. |
| Pairwise comparison is more sensitive than absolute grades but has position bias: run both orders and count an order-dependent result as a tie. Comparative judgements give reliable rankings from fewer labels. | Zheng et al. 2023 (MT-Bench); Kiritchenko & Mohammad 2017 (arXiv 1712.01765) | A/B picks (§5) and pairwise judging. |

Caveat: apart from ResumeGo and the two Freelancer.com papers, most hiring statistics online come
from vendors that sell résumé or cover-letter services. They are used here only for direction,
never as thresholds.

## 3. The metrics

Four layers. Every metric is computed per letter and reported on **relevant ads (Would apply =
Y) first**, then on all ads, as the eval-set plan says.

### Layer 0: gates (code, free; unchanged)

The 5 `rubric.auto_checks` items (`app/llm/letter/rubric.py`): em dashes ≤ 1, banned phrases ≤ 1,
≤ 340 words, no placeholders, a sign-off. They stay exactly as they are so v1 and v2 runs agree on
them. One new hard gate comes from Layer 1: **fabrications = 0**. One new guard (research doc
B3): **G1 no stuffing**: no run of 5 or more comma-separated skill terms, and no skill named more
than twice. Keywords come only with evidence.

### Layer 1: requirement coverage, honestly (the core metric)

**The reference checklist.** Each ad gets one fixed checklist to measure against: the stage-3
analysis (`evals/work/v2/runs/stage3/checks.csv`: per requirement `importance`, `letter_role`,
`status` supported/partial/gap and `evidence` pointers), with the owner's corrections from
labelling applied (the three `scenario_ok` = N ads and the verdict notes in the `comment`
column). Claude drafts the corrections from the comments, and the owner confirms them in chat
before the file is used. It is stored gitignored in `evals/work/v2/reference/`. It is the
yardstick, so it is built once and not re-analysed per run. Later engines may run their own
analysis, but they are measured against this one.

| Id | Metric | Definition |
|---|---|---|
| M1 | **Must-cover recall** | addressed `must_cover` items / `must_cover` items, with `guardrails.must_cover` applied to the reference. Target 100%. |
| M2 | **Weighted backed coverage** | Σ weight of addressed supported/partial items / Σ weight of all supported/partial items. Weights: essential 4, important 2, nice-to-have 1 (owner, 2026-10-23: one essential is worth at least two important items). `not_for_letter` and `implied` items are left out. The "hit as many as possible" number; on one page it will never be 100%. |
| M3 | **Best-evidence rate** | of the addressed headline items, the share that use the strongest evidence the reference holds for them. Strength order: work experience > project or thesis > qualification > listed skill. |
| M4 | **Claim honesty** | the judge lists every factual claim and labels it `supported`, `stretch`, `fabricated` or `employer_fact_not_in_ad`, using the v1 standard's rulings (scope, audience, seniority, rounded numbers, bare-listing experience). Gaps follow the same labels: a not-backed requirement claimed as had is `fabricated`; a partial one claimed as full is a `stretch`; honest "related experience" framing is fine. Reported: fabrications per letter (gate: 0), of which gap claims; stretches per letter; and claim precision = supported / all claims. |
| — | **Honest coverage** (headline) | M2 for a letter with 0 fabrications, else 0. One number for "as many requirements as possible without lying". |

**"Addressed"** works the way `check_requirements` does: the judge quotes the letter's words that
address the item, and code checks the quote is really there (`check_requirements.quote_in_letter`),
so a judge can't credit a sentence it imagined. It has to be **explicit**: a reader who doesn't
know the profile must be able to see the requirement answered. Coverage by implication ("five
years as a manager" for "MS Office") doesn't count, because literal screeners miss it and LLM
screeners' inferences are unreliable (research doc B2). A listing-only item (`guardrails.listing_only`)
counts as addressed only when it is presented as a skill, never as experience; presented as
experience, it is a stretch.

### Layer 2: recruiter-quality checklist (judge, yes/no)

Each sub-question is answered Y/N with a short quote or reason. A dimension's score is its share
of Y. The dimensions split into two groups, following the one validated rubric found for this
(Galdin & Silbert 2025: before AI writing tools, employers paid for the **custom** criteria and
not for the **generic** ones; research doc A1):
- **custom** = D2, D3, D4, D7 (the letter shows it was written for this job);
- **generic** = D5, D6 (quality any good letter has).

Both group means are reported. The **checklist score** = (2 × custom mean + generic mean) / 3,
the same 2:1 weighting the paper used.

| Dim | Sub-questions (each Y/N) | Owner tick it maps to (§4) |
|---|---|---|
| D2 Applied, not recited | Does the first paragraph state the strongest matching evidence (the 6-second test)? Does every body paragraph tie its evidence to a duty or requirement named in the ad? Is the connecting sentence specific rather than a formula ("prepares me to")? | weak example, generic |
| D3 Employer-specific | Is at least one ad fact about this employer, team, product or program used meaningfully? Does it pass the swap test (the sentence stops fitting if another employer's name is swapped in)? (v1's specific_detail) | generic |
| D4 Motivation | Is there a specific reason for wanting this role or employer, grounded in the ad, beyond generic praise? | no real reason |
| D5 Concrete evidence | Are there at least 2 pieces of evidence with specifics (tool, task, and scale or outcome)? Does every number come from the profile? Is at least one specific **checkable** (a named project, tool or organisation, or a number from the profile)? (research doc A3) | weak example |
| D6 Voice and polish | No stock opener ("I am writing to express")? Fewer than 3 machine-written tells? No phrase or structure repeated? Australian spelling? Tone matching the ad (`analysis.tone`)? 250–340 words? A close specific to the role? Self-promotion kept moderate, with no stacked superlatives? (A4) | sounds AI, waffly |
| D7 Understands the job | Does the letter show it grasped the goal of the role and what is hard about it (beyond restating the duties), and connect the candidate to that? (Galdin & Silbert's "understands the goal / complexity" criteria) | generic, no real reason |

The judge also gives a **predicted send-readiness (1–4)** on the owner's scale (§4), reasoned from
Layers 1 and 2. That prediction is the number calibrated to the owner's grade (§6).

### Layer 3: code signals (free, deterministic; in `letter_lab.py report`)

| Id | Signal | Definition |
|---|---|---|
| C3 | Ad echo / profile copy | share of letter words in runs of 6+ words copied verbatim from the ad (parroting); share in runs of 8+ words copied from the profile (the measure panel 4 used: 36% -> 18%). |
| C4 | Cross-letter repetition | 4–6-word phrases that appear in 3 or more of a run's letters, with names and skill terms excluded. Per run: the count and the list. Catches formulas such as "prepares me to" or "exactly the kind of", which a grader reading one letter at a time can't see. |
| C5 | Style warnings | `style_lint.lint` warnings: gap-led sentences, stock close or bridge, US spellings, low sentence-length variance, too many paragraphs starting with "I". |
| C6 | Cost and time | per letter, from the existing `cost-report`. |
| C7 | Nearest-letter similarity | normalised edit distance from each letter's body (greeting and sign-off removed) to the closest other letter in the run; flagged under 10% as a near-template. Galdin & Silbert zeroed the "custom" score of any proposal under 4% from another by the same writer (research doc A1). |
### Layer 4: AI-screener view (noted, not built)

A Claude-family screener would read only the ad and the letter, then give a 0–100 fit score and
list the requirements it saw evidenced. The metric would be **communicated fit** = screener score
minus the match score from the full profile: it shows when real fit never gets said. It stays
secondary because of the self-preference finding (§2). Use screeners from **2 or more model
families** and report each score plus their agreement: a majority vote across models cut
self-preference by 60–71% in Xu et al. Also report **screener-visible coverage**: the
requirements the screener saw evidenced, against the reference checklist. See
`future_work/ai-screener-metric.md` and research doc B1–B2.

### Headline scorecard (top of every report; relevant ads first)

1. **Send-readiness**: the mean (1–4) and the **send-ready rate** (share at 3 or 4, i.e. light
   edits at most). The owner's grade where there is one, else the calibrated judge, and the table
   says which.
2. **Fabrication-free rate**: letters with 0 fabrications.
3. **Must-cover recall** (M1).
4. **Honest coverage** (mean).
5. **Recruiter checklist** (custom weighted 2:1 over generic, §3 Layer 2; both group means shown).
6. **Cost per letter.**

When two versions are compared: the **A/B win rate** of the new version over the baseline
(wins + ½ ties, divided by ads compared), with a sign test, from the owner's picks and from the
judge's.

Everything else (M2–M4 alone, D2–D7 alone, C3–C7) is diagnostic: it explains a headline move,
and is never optimised on its own.

## 4. The owner's grading form (about 2 minutes per letter)

A generated sheet, blind (the run isn't named) and shuffled, with the ad shown beside each letter.

1. **Send-readiness**, defined by how much editing it needs:
   - **4** send as is (word-level fixes only)
   - **3** light edits (change, add or delete at most 2 sentences)
   - **2** heavy edits (rework a paragraph, or swap the main example)
   - **1** start over
2. **Anything untrue or stretched?** N, or Y plus the bad bit pasted in.
3. **Why not a 4?** Tick any that apply: generic / fits any company · missed something important ·
   weak or wrong example used · sounds AI / stock phrases · no real reason for wanting this job ·
   too long / waffly. An optional one-line note.

The ticks are the reasons the judge learns from (§6); the table in §3 shows which judge
dimension each one maps to. The 4-level scale replaces v1's Y/N `would_send`: 3+ is roughly the
old Y, and the extra levels separate "needs a paragraph reworked" from "start over", which a
binary item can't.

## 5. A/B picks (when two versions are compared)

On the 10 relevant dev ads (Would apply = Y): two letters for the same ad side by side, in random order, with the
writers hidden. Pick **A**, **B** or **same** (about 1 minute each). The judge does the same on
every ad, in both orders; if its pick changes with the order, it counts as a tie.

## 6. How the judge is built and calibrated

1. **Standard v2** (`evals/grading-standard-v2.md`, build step 2) writes Layers 1–2 as questions,
   carrying over v1's binding rulings (what a must-have is, what a stretch is, the swap test, the
   light-edit budget). `evals/grading-standard.md` stays as v1 for old runs.
2. **The owner grades the baseline's dev letters** (eval-set stage 7) before seeing any judge
   output, so nothing anchors the grades.
3. **Split** the graded dev letters into seeded halves, stratified by scenario. From the
   **calibration half**, the owner's grades, ticks and notes become the judge's few-shot examples
   and written reasons. The **check half** is never shown to the judge and only measures it.
4. **Agreement on the check half.** 8 or 9 of the 17 dev letters, so it's reported as counts, not percentages:
   - send-readiness: exact matches and within ±1 (aim: within ±1 on all but one);
   - "untrue": TPR, which must catch every claim the owner flagged. The judge's extra flags are
     each shown to the owner, who accepts or rejects them. Accepted ones were real misses, not
     judge errors (the v1 panel was stricter than the owner on claims and often right);
   - per-tick agreement for each of the 6 reasons.
   If agreement falls short, revise the prompt using **only** the calibration half and measure
   again.
5. **Freeze** it as judge v1 (prompt, model, standard file hash) and print the version on every
   report. Any change means re-grading every run being compared (the existing panel rule in
   `evals/grading-panel.md`).
6. **Length probe** (research doc B4: LLM judges favour longer text): during calibration the judge
   also grades padded copies of 3 letters (+60 words of filler that adds no facts). It passes
   only if no padded copy scores higher than its original.
7. **Bias controls, kept from v1:** blind and shuffled; an Opus judge (a different model family
   from the Gemini writer; run as Claude Code subagents, so no Vertex spend); "length is not
   quality" in the prompt; 2 graders + an adjudicator; pairwise in both orders. **New in v2: one
   fresh judge per letter** (and per pair-order for the A/B picks): each subagent sees only the
   profile, one ad, its reference checklist and one letter, so no letter can anchor another and
   the judge can't drift over a long packet. This replaces v1's reverse-order second grader.
8. **Afterwards:** on later runs the owner grades about 5 letters plus the A/B picks, to catch
   drift. If agreement drops, recalibrate. The **test set** is graded once, at the end, by both
   the owner and the frozen judge.

## 7. Build steps (each with the owner's go-ahead)

1. **Reference checklist**: a builder reading `checks.csv` and the candidates.csv comments; the
   corrections drafted, then confirmed by the owner; written to `evals/work/v2/reference/`.
   **Done 2026-10-26** (`scripts/eval_reference.py`; Decision log).
2. **Standard v2 + judge schema**: `evals/grading-standard-v2.md`, the judge's JSON shape
   (claims with labels, per-requirement quotes, D2–D7 answers, predicted send-readiness); extend
   `scripts/grading_panel.py` with a v2 pack, a pairwise pack in both orders, and an agreement
   report (exact / ±1, weighted kappa, TPR/TNR, per-tick).
   **Built 2026-10-26** (`v2-*` commands; procedure and judge prompts in `evals/grading-panel.md`;
   `tests/test_grading_panel_v2.py`, fake data). Not yet run on real letters: none exist until
   stage 6, so the first real check is a free `v2-pack` on the baseline letters.
3. **Grading sheet + code metrics**: `letter_lab.py grade-sheet` (the §4 form, blind), G1 and C3–C7
   plus the scorecard in `report`, reusing `rubric.auto_checks`, `style_lint.lint`,
   `check_requirements.quote_in_letter`, `guardrails.must_cover` / `listing_only` and
   `prefilter.normalise_skill`. Tests use fake letters and fake employers.
4. Then eval-set stages 6–8 as planned: baseline letters, owner grades, judge calibration.

## Decision log

- 2026-10-23: **metrics v2 drafted** from research (§2) and the owner's choices in chat:
  - Owner grade = **4-level send-readiness + untrue Y/N + reason ticks** (about 2 min per
    letter), not Y/N would_send (saturated at 0/15) or a 1–10 score (drifts; hard for a judge to
    match).
  - **A/B picks on the 10 relevant dev ads** when comparing two versions.
  - **AI-screener simulation: a to-do, not a core part of this task** (owner). Written up in
    `future_work/ai-screener-metric.md`.
  - Keyword overlap is diagnostic only, because the evidence says it stops working as a signal
    once everyone writes with AI. Specific, checkable evidence is what's rewarded.
  - Coverage is measured against ONE fixed, owner-corrected reference checklist per ad (from
    stage 3), so engines are measured against the same yardstick rather than their own analysis.
- 2026-10-23: **folded in the AI-recruitment research** (docs/ai-recruitment-research.md, owner
  asked for a deeper, corroborated review). Changes: D7 "understands the job"; a custom vs generic
  split weighted 2:1 (Galdin & Silbert's validated rubric); "addressed" must be explicit (LLM
  screeners infer unreliably); D5 asks for a checkable specific; D6 asks for moderate
  self-promotion; G1 no-stuffing guard; C7 nearest-letter similarity; C8 intensity; a judge length
  probe; Layer 4 to use 2+ model families. Rejected as a reason: "concrete wording seems truer"
  (failed replication).
- 2026-10-23: **M2 weights 4/2/1** (owner), replacing 3/2/1, under which two important items
  outweighed one essential. No study gives these weights; it is a judgement call. Once real
  letters exist, compute M2 both ways to see whether the ranking of letters changes. Watch for
  M2 rising while send-readiness falls: that would mean letters are cramming more requirements
  in with less evidence each.
- 2026-10-23: **5 overlapping metrics cut** (owner). The rest keep their ids, so references
  elsewhere stay valid.
  - C1 (gates): a second listing of Layer 0.
  - M5 (gap handling): its rules were M4's labels; now part of M4's definition, with gap claims
    as a sub-count of fabrications.
  - C8 (intensity): a word list can't read context; D6's self-promotion question covers it.
  - C2 (keyword coverage): only an exact-term check, and keyword overlap is a weak signal; M2
    with the explicit rule covers coverage.
  - D1 (first impression): "names the role" was almost always yes; "strongest evidence first"
    moved into D2 and "no stock opener" into D6.
  - C3 was considered and kept: it compares a letter with its sources (the ad, the profile),
    while C7 compares letters with each other, and profile copy is a number that has moved
    (36% -> 18%).
  - Now: Layer 0 = 5 gates + G1; Layer 1 = M1–M4 + honest coverage; Layer 2 = D2–D7; Layer 3 =
    C3–C7.
- 2026-10-23: **plan approved** (owner). The definitions are fixed before any letter is graded.
  The guessed thresholds (C7, G1, the length probe) may be tuned once on the first real letters,
  before grading, and are then frozen. Judge calibration (§6) may still reword a yes/no question
  if the judge can't match the owner's grades; any such change is logged here.
- 2026-10-26: **reference checklist built and frozen** (§7 step 1; no LLM calls).
  `scripts/eval_reference.py` reads the stage-3 run (eval.db, checked row for row against
  checks.csv), proposes corrections, records the owner's yes/no per row, then writes one read-only
  JSON per split ad to `evals/work/v2/reference/ads/` with MANIFEST.json (hashes, sources, every
  decision with its reason). Each row still loads as a `Requirement`, so `guardrails.must_cover`
  / `listing_only` apply to it unchanged; each file also maps every cited pointer to its profile
  text and kind (work / project / qualification / skill), which M3 ranks by.
  - **Scope (owner):** the labelling error patterns are corrected on every split ad, not only the
    commented ones: a consistent answer key matters more than matching the comments.
  - **Rules (owner, set while answering):**
    - *Years asks*: relevant paid work only (projects, thesis and coursework are not years;
      non-technical jobs don't count towards technical asks), against a range's lower number:
      under half the years asked = gap, half or more = partial, all = supported. An ask with no
      number of years ("demonstrated experience as a developer") is judged on the evidence, not
      on duration. "Building and shipping working solutions" is not limited to paid work, so a
      degree's projects count there (kept partial).
    - *Seniority asks* ("senior", "lead"): supported only if a relevant job title held says so.
    - A row that becomes a gap keeps what the analysis cited as `related_evidence`, never as
      `evidence` (guardrails read `evidence` as backing): the judge can then tell honest
      related-experience framing from a claim of the years.
    - *A row bundling two asks is split*: "2-3 years' experience including C#, JS/TS, HTML/CSS"
      became a years row (gap) and a stack row (supported), so a letter is credited for the
      backed half and flagged only for claiming the other.
    - *Non-technical customer service* is dropped as evidence for technical product support, but
      kept for "talk to clients on the phone" (that is what customer service shows).
    - *A duty of the job* drops from essential to important only when no candidate could bring it
      (it needs the employer's own systems) or it is the job's domain overview rather than a
      listed requirement; duties that are core skills (manual testing, test cases, lakehouse
      design) stay essential.
    - Evidence follows the frozen profile, not the owner's real skills; where the owner has more
      (comments on #12, #168), add it to the profile after the baseline.
  - **Counts:** 16 proposals on 13 of 31 ads, 10 accepted / 6 rejected. 376 -> 377 rows; partial
    130 -> 123, gap 92 -> 98, supported 154 -> 156, must-cover 110 -> 106, must-have gaps 20 -> 23,
    M2 weight base 653 -> 635.
  - Scenario tags are unchanged (the agent's path comes from its own analysis); only #149 would
    move on the reference (3 -> 1).
- 2026-10-26: **standard v2 drafted** (§7 step 2; `evals/grading-standard-v2.md`, DRAFT, no code
  yet). Owner decisions:
  - **The packet carries the full profile** as well as the reference checklist: M4 checks every
    claim against the profile, and the checklist's evidence map holds only the entries cited for
    a requirement, so an honest claim from another entry would look fabricated.
  - **v2 gets its own `disagreements` and `merge`** in `scripts/grading_panel.py`, split per
    question, because §6.7 keeps 2 graders + an adjudicator.
  - **The judge doesn't pick the must-haves:** `guardrails.must_cover` on the reference decides
    them. v1's "what is a must-have" ruling is carried over as an explanation only.
  - **Weighted kappa is reported with a warning:** on 8–9 letters one disagreement swings it, so
    the exact and ±1 counts are the main agreement figures.
  - **One fresh judge per letter** (owner's suggestion), and per pair-order for the A/B picks
    (§6.7).
  - **v1's "not sendable" triggers are counted by the edit each one takes**, not an automatic cap
    at 2: a one-sentence fix is still a light edit. A cap would bunch nearly every letter at 2,
    which is how `would_send` sat at 0/15. Untrue claims are already scored on their own (the
    untrue figure, the fabrication gate).
  - **Stretch vs fabricated:** a stretch inflates something an entry backs; fabricated has no
    entry behind its core fact. So an invented result, number or tool is **fabricated** (v1 had
    listed them as stretches, when only Y/N mattered); a bigger audience or a rounded-up number
    stays a stretch; a gap claimed as had is fabricated; unsure = stretch.
  - **Smaller mechanics:** code counts the words for D6f; the judge reads tone from the ad; D6d is
    strict (any US spelling = N; v1's "more than three" stays in send-readiness); ticks only
    below a 4; A/B picks go fewer untrue claims > predicted grade > coverage > checklist, and
    never length.
  - **The owner's answer key is the truth.** The judge grades by it; where it thinks a row is
    wrong it writes a `checklist_doubts` entry, which never changes a score. Code collects them
    into one list for the owner to recheck later.
  - Standard v2 **approved** (owner). It is frozen after stage 8 adds the calibration examples.
