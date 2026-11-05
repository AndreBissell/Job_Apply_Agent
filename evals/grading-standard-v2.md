# Grading standard v2 (the letter judge)

**Status:** approved by the owner (2026-10-26); rewritten for the points scheme (2026-11-04: §4.1,
§5, §7, §8, §9, §10). The points version has not been run on any letter yet. It is frozen as
judge v1 (points) once stage 8's calibration is done.
**Applies to:** letters on the frozen v2 set (`letter_lab.py --set v2`). Runs graded before v2
keep `grading-standard.md` (v1).
**Metrics it serves:** docs/letter-metrics-plan.md §3, Layers 1 and 2, and the letter's points
score. Code computes every score from your answers; you never add anything up.

You are grading cover letters the way the candidate would. You answer questions; you do not
give marks. Read this whole file before the packet.

## 1. What is true

- **The candidate's profile** in the packet is the ONLY truth about the candidate. Not the
  letter, not the checklist's wording, not what a typical candidate would have.
- **The ad** is the ONLY truth about the employer. A fact about the employer that isn't in the
  ad counts as not in the ad, even if it is true in the real world.
- **The reference checklist** for each job is the fixed answer key: which requirements the ad
  has, how much each matters, and what the profile backs. Use it as given. Do not make your own
  list of requirements, re-rate a row, or argue with its verdicts. If a row looks wrong to you,
  grade against it anyway and say so in `checklist_doubts` (§9): the owner rechecks those later,
  and they never change a score.

## 2. What the packet holds

Every letter is graded by a fresh judge that sees only that letter's packet: no other letter, no
other job, and nothing from an earlier grading. So every letter is judged from the same starting
point, and no letter can anchor or shift the grade of another.

The packet holds one letter and everything needed to grade it:

1. The candidate's profile: every entry with its pointer in brackets (`[experience:12#s3]`,
   `[qualification:4]`, `[skill:7]`), then the text.
2. The job (a job id such as J01, which only groups the answers later):
   - the ad (title, company, location, text);
   - the reference checklist: one row per requirement, with
     - `id` (R1, R2, ...; a split row has a letter suffix, R1b),
     - `text` (the employer's words),
     - `importance`: essential / important / nice_to_have,
     - `letter_role`: headline / mention / implied / not_for_letter,
     - `status`: supported / partial / gap,
     - `evidence`: the pointers that back it,
     - `related_evidence` (gap rows only): what is related but does NOT back it,
     - `note`: a one-line reason, and
     - two flags the code works out: `must_cover` (yes/no) and `listing_only` (yes/no);
   - the letter, under a random id (L1234), with its word count.

The letter's writer is hidden. Don't guess it, and don't let a guess matter. Other letters exist
for this job and others, but you never see them, and the standard doesn't ask you to imagine them.

## 3. How to work

1. Read the profile in full.
2. Read the ad, then the reference checklist. Only then read the letter.
3. **Grade the letter on its own** against this standard: not against an imagined better letter,
   and not against what a typical letter for this job might say.
4. Answer in this order: requirements (§4), claims (§5), the checklist (§6), then the problems
   (§7). The later answers depend on the earlier ones.
5. **Length is not quality.** A longer letter earns nothing for being longer. Extra words that
   add no fact about the candidate or the job count against it (filler, §7.8).

## 4. Requirements: is each one addressed? (M1–M3)

Answer for **every row except `not_for_letter`** (eligibility items such as work rights belong
to the application form, not the letter).

### 4.1 "Addressed" must be explicit

A row is **addressed** only if a reader who has never seen the profile can point to the words
in the letter that answer it. Quote those words exactly; code checks that the quote is in the
letter, and a quote it can't find counts as not addressed.

- **Explicit:** the letter names the requirement's skill, activity or subject, or a plain
  synonym of it, AND gives the candidate's evidence for it.
- **By implication doesn't count.** "Five years as a team manager" does not address "MS Office";
  "built a web app" does not address "Git". If the reader has to infer it, it is not addressed.
- **Every word that narrows the requirement must be answered** (owner, 2026-11-03). The quote
  has to answer the requirement's own subject, by the same word or a plain synonym: the named
  tool or product, the scope ("international", "senior stakeholders") and the quality asked for
  ("fault-tolerant", "independently"). A broader term, a neighbouring activity or a related trait
  does not answer it:
  - "container tools" does not address "experience with Kubernetes";
  - "a cross-team project" does not address "working with teams across time zones";
  - "I tested my code for edge cases" does not address "building fault-tolerant integrations";
  - "I managed my own workload" does not address "investigating problems independently".

  If you have to argue that the quote covers the requirement, it doesn't: answer N.
- **A row that asks for two things needs both** (owner, 2026-11-03). Answering one part is N:
  "I wrote unit tests in JUnit" does not address "experience developing user interfaces and
  writing unit tests". Say which part is missing in `reason`.
- **Hard skills are named; traits are named or shown** (owner, 2026-10-29). A hard skill (a
  language, tool or technique) must be named in the letter. A soft skill or trait (curiosity,
  reliability, passion, drive) must be named, or shown through an example that demonstrates it.
  Describing an activity alone doesn't show a trait: "I sat between the build and the end users"
  does not address "curiosity about both building technology and helping clients use it".
  A row that lists several traits or soft skills ("initiative, adaptability and a willingness to
  learn") is addressed when the letter names or shows at least one of them with evidence (owner,
  2026-11-04); an interest can be shown by the work and the reason the letter gives. The two-part
  rule below is for hard skills and activities.
- **A neighbouring activity is not the one asked for.** Delivering code through a team's
  deployment pipeline does not address "building automations"; using a reporting tool does not
  address "building the reports". The candidate must have done the thing the row names.
- **Partial rows** count as addressed only when the quote names what the ad asks for (the rule
  above) AND states the related evidence honestly against it ("I built the reporting in Tableau,
  and the same modelling applies to Power BI"). The related evidence told on its own, without
  naming what the ad asks for, is NOT addressed, however close it is: "I built reporting
  dashboards" does not address a Power BI row. A partial row claimed as fully had is still
  addressed, but it is also a **stretch** claim (§5).
  When the profile backs one part of a partial row and not the other (the row's note says which),
  naming the backed part in the ad's own words, with its evidence, addresses the row; the
  unbacked part needn't be named, and claiming it would be a stretch (owner, 2026-11-04). "I
  built the booking service in Java" addresses "Java and Spring Boot" when the profile has Java and no
  Spring Boot. The two-part rule above is for rows the profile backs in full.
- **Listing-only rows** (`listing_only` = yes) count as addressed only when the letter presents
  the item as a skill ("my skills include SQL"). Presented as experience ("I have hands-on
  experience with SQL"), it is NOT addressed, and it is a stretch claim (§5).
- **Gap rows** cannot be addressed: there is nothing to address them with. Record what the letter
  does with them: `claimed` (the letter says the candidate has it: a fabricated claim, §5),
  `related` (honest related-experience framing using `related_evidence`, which is fine), or
  `none` (not mentioned, which is fine).
- **Denying a backed row** ("I am new to React" when the profile backs React) means not
  addressed.

### 4.2 Which evidence was used (for M3)

For each addressed row, list the profile pointers the quoted words rest on (`evidence_used`).
Use the row's own `evidence` pointers where they fit, and any other profile pointer where the
letter used something else. Code compares what was used with the strongest evidence the row
holds, in this order: work experience > project or thesis > qualification > listed skill.

**Relevance comes before kind** (owner, 2026-10-29). When you judge whether a letter used a weak
example (§7.4), first ask whether
the example really shows what it is offered for. Among the examples that do, the candidate
ranks them like this (owner, same day):

1. **Professional work in the job's own field** where the skill was used: for a software job,
   a software internship, where teamwork means working in a team on code.
2. **University or personal work in the same field** (not professional) where the skill was
   used: for a software job, a student software project or the thesis.
3. **Work, professional or personal, outside the job's field** that still shows the skill: for
   a software job, teamwork or adapting to change in a retail job.

A higher rung beats a lower one whenever the profile has both for the same point, whatever
code's order above says. A job outside the field is still the right example for a point only
it really shows (talking to customers on the phone, when nothing in the field does). Code's
order goes by kind only and can't see this, so it is yours to judge.

### 4.3 What a must-have is (v1 ruling, kept for reference)

The checklist's `must_cover` flag already decides this, so you don't apply this rule. It explains
what the flag means. The must-haves are the ad's core, distinguishing requirements: its
essentials / requirements / "you'll bring" list, plus the role's defining activity. They never
include eligibility and admin, attitudes and traits, items marked ideally / nice to have /
highly regarded, or items implied by a stronger one. A row backed only by listed skills is never
`must_cover`: a listing backs "skills in X", never "experience with X".

## 5. Claims: is everything true? (M4)

List **every factual claim** the letter makes: anything about the candidate that an interviewer
could ask about ("tell me about the time you did that"), and every fact stated about the
employer. Not claims: wishes, intentions and enthusiasm ("I'd welcome the chance"), and
self-assessment adjectives on a skill the profile lists ("strong SQL skills").

For each claim give the exact quote, what backs it (profile pointers, or the ad's words), and
one label:

| Label | Meaning |
|---|---|
| `supported` | A profile entry (or, for the employer, the ad) says it. Paraphrase is fine when it keeps the same activity, scope and audience. Understating is fine. Naming a listed core language as used on a described project, where that is plausible, is fine. |
| `stretch` | A profile entry backs the core of it, but the letter enlarges it (the list below). |
| `fabricated` | Nothing in the profile backs the core fact: an activity, tool, job, qualification, result or number the profile doesn't hold, or a **gap row** claimed as had. |
| `employer_fact_not_in_ad` | A fact about the employer (a product, value, program, client, size, history) that the ad doesn't state. |

**Stretches (v1 rulings, binding):**
- a different audience, seniority or role ("presented to executives" for team leads; "led" for
  contributed; "managed" for helped);
- rounded-up numbers ("over 50" when the profile says 40);
- "practical / hands-on experience in X", "apply my skills in X" or "exposure to X" resting on a
  bare skill listing or a job title alone;
- building X presented as using X day to day; the same subject with a different activity;
- a process claim ("through rigorous testing", "ensuring data integrity") attached to a project
  whose entry doesn't say so;
- a partial row claimed as fully had ("my Power BI experience" when the profile shows Tableau).

**A summary sentence is fine** (owner, 2026-10-29). A sentence that sums up the examples told
right after it ("I have tested my applications as I built them", followed by the unit tests on
one project and the failure testing on another) is `supported` when it claims nothing beyond
those examples. Judge the summary by the examples it introduces, not as a separate claim.

**A skill put in the wrong experience** (owner, 2026-10-29). When a sentence places a skill, tool
or result inside one experience (by naming it, or because the sentence sits in a passage about
that experience), check **that experience's entry**. If only a different entry backs it, the
claim is a `stretch`, even though the profile has the skill. Read across sentences: a paragraph
that tells an internship and then says "I tested my cloud system for failures" puts that testing
in the internship, unless the letter says which project it was.

**A listed skill next to an experience: the wording decides** (owner, 2026-11-03). A skill from
the profile's skills list, named as a skill the candidate has beside an experience, is
`supported` ("I built the booking tool at Acme, supported by my skills in SQL"). Worded so the
skill sounds used in an experience whose entry doesn't have it, it is a `stretch` ("at Acme I
deployed the booking tool on AWS", when AWS appears only in a university project or the skills
list).

**Stretch or fabricated?** Ask whether some entry describes the claim's core fact. If one does
and the letter inflates it, it is a stretch. If none does, it is fabricated. If you can't decide,
say so in `why` and choose `stretch`.

**Gap claims:** if a claim concerns a gap row, put that row's id in `requirement`. A gap row
claimed as had is always `fabricated`. Honest framing ("my thesis used related methods, though
not in production") is `supported`, provided the related part is backed.

## 6. Recruiter checklist (D2–D7)

Answer each question **Y** or **N**. For a Y, quote the words that earn it where there are such
words; for an N, give the reason in one line (and quote the failing words if there are any).

| Id | Question |
|---|---|
| **D2 Applied, not recited** | |
| D2a | Does the first paragraph state the candidate's strongest evidence for the job (the best-backed headline items), so that a reader who stops after it knows why they fit? |
| D2b | Does every body paragraph tie its evidence to a duty or requirement named in the ad? (Evidence listed without saying how it applies to this job's work is a N.) |
| D2c | Are the sentences that connect evidence to the job specific to it, rather than a formula that could join any experience to any job? |
| D2d | Do the experiences in each body paragraph connect? Two or three experiences in one paragraph are fine when they are well aligned to the same point: they answer the same requirement, or one builds on the other. **Test:** state the paragraph's point in one sentence; every experience in the paragraph must support that sentence. Unrelated experiences put side by side, or experiences that don't serve one point, is a N. Name the paragraph and its experiences. |
| D2e | Is each part of an experience told in one place? A letter whose paragraphs each answer one requirement may use the same experience again, for a different requirement, telling a different part of it (owner, 2026-11-04): that is a Y. Telling the same part (the same work, skill or result) in two paragraphs, so the reader has to piece it together, is a N. Name the experience and the paragraphs it is split across. |
| **D3 Employer-specific** | |
| D3a | Is at least one fact from the ad about this employer, team, product, project, program or platform used meaningfully, tied to why the candidate wants the role or how their experience applies? The role title, the company name, the contract length, generic industry phrases ("a leader in the field") and a restated requirement don't count. |
| D3b | **Employer swap test:** pick the most employer-specific sentence. If you swap in another employer advertising the same title and change only the name, does the sentence stop fitting? (Y = it stops fitting.) |
| **D4 Motivation** | |
| D4a | Is there a specific reason for wanting this role or this employer, grounded in the ad, beyond generic praise ("your reputation for excellence")? |
| D4b | Is a reason for applying tied to the candidate: something in their own experience, study or goals that explains why they want this work (an interest they developed, something they want more of or want to go deeper in)? The ad's description of itself restated as the candidate's wish is a N, however specific ("Acme is building the future of logistics, which is where I want to start my career"); so is experience offered only as a skill to apply ("my background in X suits your team"), which shows fit, not motivation. Quote the sentence that ties the reason to the candidate. |
| **D5 Concrete evidence** | |
| D5a | Are there at least 2 pieces of evidence with specifics: the tool, the task, and the scale or the outcome? |
| D5b | Does every number in the letter come from the profile (or the ad, for employer facts)? Y if the letter has no numbers. |
| D5c | Is at least one specific **checkable**: a named project, tool or organisation, or a number from the profile? |
| **D6 Voice and polish** | |
| D6a | Does it avoid a stock opener ("I am writing to express my interest", "I am excited to apply for")? |
| D6b | Does it have fewer than 3 machine-written tells ("demonstrating my ability to" tails, triple adjectives, stacked intensifiers, "I am confident that my")? List the ones you find. |
| D6c | Is no phrase, sentence structure or achievement repeated within the letter? |
| D6d | Is the spelling Australian throughout? List any US spellings. |
| D6e | Does the tone match the ad's own tone (formal, casual, technical, warm)? |
| D6f | Is it 250–340 words? Use the word count the packet gives. |
| D6g | Is the close specific to this role, rather than a stock close ("I look forward to hearing from you" alone, "thank you for your consideration")? |
| D6h | Is self-promotion moderate: confident but with no stacked superlatives ("exceptional, outstanding results")? |
| **D7 Understands the job** | |
| D7a | Does the letter show it grasped the goal of the role and what is hard about it, beyond restating the duties, and connect the candidate to that? |

## 7. The problems

### 7.1 How it works

You don't give the letter a score. You find its problems and give each one a level, **low** or
**high**, using the anchors below. Code turns them into the letter's score; you never see or
weigh what a problem costs, and you never add anything up.

| Problem | Who finds it | Who sets the level |
|---|---|---|
| Untrue claim (§7.2) | you, in `claims` (§5) | you |
| Missed requirement (§7.3) | code, from your `requirements` (§4) | code |
| Weak example (§7.4) | you | you |
| Mixed-up experiences (§7.5) | you | you |
| Generic body (§7.6) | you | you |
| No personal reason (§7.7) | you | you |
| Too long (§7.8) | you list the filler; code counts the words | code |

Report a problem only when the letter has it; a clean letter has an empty list. Quote the words
each problem rests on. **Untrue claims, missed requirements, weak examples and mixed-up
experiences count one by one**, so report each instance separately. Generic body, no personal
reason and too long are one answer per letter. **One cause can be two problems**: a passage that merges two experiences
can put a skill in the wrong one (untrue, §5) and also leave the reader unsure which experience
shows what (mixed up, §7.5). Report both.

### 7.2 Untrue claims

Every `stretch`, `fabricated` or `employer_fact_not_in_ad` claim from §5 gets a level.

- **High:** any `fabricated` claim; or a `stretch` or `employer_fact_not_in_ad` that is the
  **only** support for one of these:
  - the letter's answer to a `must_cover` row (take the claim out and nothing true in the letter
    answers that row);
  - the opening paragraph's main claim of why the candidate fits the job;
  - for an employer fact, the reason for applying (take it out and the reason has nothing left).
- **Low:** every other `stretch` or `employer_fact_not_in_ad`: in a side sentence, beside true
  evidence for the same point, or on a row that isn't `must_cover`.
- **Real work in the wrong place is low** (owner, 2026-11-04): a stretch that only puts work the
  candidate really did into the wrong experience (a uni project's testing told as part of the
  internship) is low, even on a `must_cover` row, because the row still rests on work the
  candidate did. The "only support" test above is for stretches that inflate what was done (led
  for helped, used for listed, a bigger scale or result).

### 7.3 Missed requirements

You don't report these. Code works them out from your §4 answers: a row is missed when it is
backed (supported or partial), essential or important, meant to be said (headline or mention),
and you answered N. Code also sets the level from the answer key. So §4's rules are what count
here: answer each row strictly by §4.1.

**A wrong example is not a weak example** (owner, 2026-11-03). An example that doesn't show the
point it is offered for leaves that requirement unanswered: answer the row N (§4.1). If the
letter also presents the example as if it showed the point, that is a `stretch` claim (§5).
Don't report it under §7.4.

### 7.4 Weak example

Judge **every point the letter makes**, including points that answer no row or a gap row. For
each point, rank the examples that really show it by §4.2's rungs (relevance first), using the
whole profile, not only what the letter used.

- **Low (the delete test):** the point's best example is there, then a lower-rung example is
  added for the same point and adds nothing: deleting it loses nothing. A thin add-on line counts
  here only, never also as filler (§7.8).
- **High (the example swap test):** the point's **main** example (the one the point rests on:
  told alone, told first, or told with the detail) is from a lower rung when a higher rung in the
  profile shows the same thing. It is high even when the weaker example's wording matches the
  ad, and even when the stronger example appears nowhere in the letter.

List each weak example separately, with the point it is offered for, the example used, the
stronger one (its profile pointer), and the level. Two thin add-on lines after the same best
example are two weak examples.

### 7.5 Mixed-up experiences

Use your D2d and D2e answers (§6).

- **Low:** the same part of an experience split across two paragraphs (D2e N); one paragraph
  holding experiences that don't serve its point (D2d N); or an experience told, left for another
  and returned to within one paragraph. In each case the reader can still follow which experience
  shows what.
- **Not a mix-up:** using an experience again in a later paragraph for a different requirement,
  telling a different part of it (the internship's team in one paragraph, its stakeholder work in
  the next); or naming an experience again only to give the reason for applying.
- **High:** the reader can't follow which experience shows what: experiences merged or
  interleaved so that a skill, result or duty can't be placed.

Report each instance separately (each experience split, each paragraph whose experiences don't
serve its point), naming the experiences and paragraphs. A passage the reader can't follow is one
high, not one low per experience in it.

### 7.6 Generic body

Does the body link the candidate's experience directly to **this** job's work, as the ad
describes it? This is about the links, not the wording: stock or machine-sounding phrases are
checked by code, so don't count them here. The reason-for-applying sentence is judged only under
§7.7, never here.

- **None (not a problem):** direct, specific links throughout.
- **Low:** links in places, or thin ones.
- **High:** no real links, or links so vague they would fit any job ("support your team",
  "assist with the daily tasks of your department"): the body would fit any employer
  advertising this title.

Quote the sentence that best shows the level you chose.

### 7.7 No personal reason

Use your D4a and D4b answers (§6).

- **None (not a problem):** a reason for applying is tied to the candidate's own experience,
  study or goals (D4b Y), and it holds up when read.
- **Low:** the reason is specific to this ad, but not tied to the candidate (D4a Y, D4b N).
- **High:** the reason would fit any letter (D4a N); or it doesn't hold up when read: the ad fact
  it rests on isn't something a candidate could want, or the "because" doesn't follow.

When the letter gives more than one reason (an opener and a close, say), the best one sets the
level (owner, 2026-11-03): an opener that doesn't hold up is low, not high, when the close gives
a real reason specific to the ad. Experience offered as a skill to apply ("drawing on my capstone
project, I want to use data to ...") is fit, not a reason (D4b).

Quote the reason-for-applying sentence (or say there is none).

### 7.8 Too long

A **filler sentence** adds no evidence, no employer fact, no reason for applying and no link
between the evidence and the job. Not filler:
- the greeting, the sign-off, and ONE closing sentence asking for an interview or a conversation
  (a second one is filler);
- a thin add-on example (that is §7.4's low);
- a **link**: a sentence that ties the evidence to the ad's work ("This background prepares me to
  maintain your stock reporting tools"), or one that names the requirement a
  paragraph answers ("You need someone who can work with SQL databases"), however thin
  or formulaic (owner, 2026-11-04). A thin or vague link is §7.6's problem, and its wording is
  checked by code, so it is never also filler;
- a closing sentence that restates the reason for applying.

Filler is, for example, a self-description with nothing behind it ("I am a hard-working and
motivated person"), a sentence that only repeats a point already made, or a remark about the
application itself ("I am sending this application through your careers page"). Long sentences are not filler.
Quote every filler sentence. Code counts them, and also flags a letter over 340 words; you don't
judge the length.

### 7.9 What is not a problem

- **Structure on its own** (owner, 2026-11-03): the order of the points, repeated sentence
  openings, a missing greeting. Report these nowhere, unless they also cause a problem above
  (an experience split across paragraphs is §7.5).
- **Wording**: stock or machine-sounding phrases are checked by code.
- **How well the candidate fits the job**: gap rows left out of the letter are fine, and the
  fit is scored separately from the answer key. Judge the letter, not the candidate.
- **Length on its own**: a longer letter earns nothing and loses nothing for its length below
  340 words; only filler counts (§7.8).

You may add a one-line `note`.

## 8. Picking between two letters (pairwise)

A pairwise packet holds one ad, its reference checklist, the profile, and two letters, `first`
and `second`, by different hidden writers. As with grading, each packet gets a fresh judge. The
same pair is also given to another fresh judge with the order swapped, so a pick that depends on
which letter came first counts as a tie. You only ever see one order.

Pick the letter the candidate would rather send: `first`, `second` or `same`. Decide in this
order, and stop at the first step that separates them:
1. untrue claims (§7.2): the letter with fewer high ones, then fewer low ones;
2. missed requirements (§4, §7.3): the letter that addresses more `must_cover` rows, then more of
   the other essential and important backed rows;
3. the other problems (§7.4 to §7.8): the letter with fewer and less serious weak examples,
   mix-ups, generic body, missing personal reason and filler, weighed as the candidate would;
4. the recruiter checklist (§6).

Choose `same` only if none of these separates them. Length is not a reason. Give a one-line
reason for each pick.

## 9. The answer file (JSON)

Answer with valid JSON in this shape; code saves it. It holds the one letter you graded (code
later collects every letter's file into one):

```json
{
  "standard": "v2-points",
  "letters": {
    "L1234": {
      "job": "J01",
      "requirements": [
        {"id": "R1", "addressed": "Y", "quote": "At Acme I built the stock dashboards in Power BI",
         "presented_as": "experience", "evidence_used": ["experience:12#s2"], "reason": ""},
        {"id": "R4", "addressed": "N", "quote": "", "presented_as": "none",
         "evidence_used": [], "reason": "not mentioned"},
        {"id": "R5", "addressed": "N", "quote": "I wrote unit tests in JUnit",
         "presented_as": "experience", "evidence_used": ["experience:12#s3"],
         "reason": "two-part row: the user-interface part is not answered"},
        {"id": "R6", "addressed": "N", "quote": "my thesis used similar models",
         "presented_as": "related", "evidence_used": ["qualification:3"],
         "reason": "gap row, framed honestly as related"}
      ],
      "claims": [
        {"quote": "led a team of five analysts", "about": "candidate", "label": "stretch",
         "level": "low", "source": ["experience:12#s4"], "requirement": null,
         "why": "the entry says contributed to a team of five, not led; the paragraph's point also rests on the dashboards"},
        {"quote": "Acme's new Sydney office", "about": "employer",
         "label": "employer_fact_not_in_ad", "level": "high", "source": [], "requirement": null,
         "why": "the ad doesn't mention an office opening, and it is the only reason given for applying"},
        {"quote": "I built the stock dashboards in Power BI", "about": "candidate",
         "label": "supported", "level": null, "source": ["experience:12#s2"], "requirement": "R1",
         "why": ""}
      ],
      "checklist": {
        "D2a": {"answer": "Y", "quote": "...", "reason": ""},
        "D2b": {"answer": "N", "quote": "", "reason": "the second paragraph lists tools without tying them to the ad"}
      },
      "problems": {
        "weak_examples": [
          {"point": "adapting to changing priorities", "quote": "At the cafe, priorities changed hourly",
           "used": ["experience:7"], "stronger": ["experience:12#s5"], "level": "high",
           "why": "example swap test: the internship shows the same point at a higher rung"}
        ],
        "mix_ups": [
          {"quote": "At Acme I ... On my uni project I ... Back at Acme I ...",
           "experiences": ["experience:12", "qualification:3"], "paragraphs": [2],
           "level": "low", "why": "the internship is left for the project and returned to in one paragraph"}
        ],
        "generic": {"level": "low", "quote": "which will help me support your team",
                    "why": "the first two body paragraphs tie to the ad's reporting work; the third links only vaguely"},
        "no_personal_reason": {"level": "none", "quote": "my thesis on forecasting made me want to work on demand planning",
                               "why": "tied to the candidate's study"},
        "filler": [
          {"quote": "I am a hard-working and motivated person.", "why": "no evidence, employer fact or reason"}
        ]
      },
      "note": "",
      "checklist_doubts": []
    }
  }
}
```

Field rules:
- `standard` is `"v2-points"`.
- `requirements`: one entry per checklist row except `not_for_letter`, in checklist order.
  `addressed` is Y or N, by §4.1. `presented_as` is one of `experience`, `skill`, `related`,
  `claimed` (a gap row claimed as had) or `none`. Leave `quote` empty when nothing in the letter
  touches the row; otherwise quote the closest words, even when they don't address it, and say in
  `reason` what is missing.
- `claims`: every claim, in letter order. `about` is `candidate` or `employer`. `source` holds
  profile pointers, or for employer facts the ad's words. `requirement` is a row id or null.
  `level` is `low` or `high` for a `stretch`, `fabricated` or `employer_fact_not_in_ad` claim
  (§7.2; a `fabricated` claim is always `high`) and null for a `supported` one. When the level is
  high, `why` names what the claim is the only support for.
- `checklist`: all 21 ids, D2a to D7a.
- `problems` holds §7.4 to §7.8. Every key is always present: an empty list, or a level of
  `none`, when the letter doesn't have the problem.
  - `weak_examples` (§7.4): one entry per weak example. `used` and `stronger` are profile
    pointers; `level` is `low` or `high`; `why` names the test (delete or example swap).
  - `mix_ups` (§7.5): one entry per instance. `paragraphs` are numbered from 1, counting from the
    first paragraph after the greeting; `level` is `low` or `high`.
  - `generic` (§7.6) and `no_personal_reason` (§7.7): one answer each, `level` `none`, `low` or
    `high`. `quote` is the sentence that best shows the level (for `no_personal_reason`, the
    reason-for-applying sentence, or empty when there is none).
  - `filler` (§7.8): every filler sentence, quoted whole. No level: code counts them.
- Every `quote` must be the letter's exact words, and code checks each one. A row whose quote
  code can't find counts as not addressed; a claim, weak example, mix-up or filler sentence whose
  quote it can't find is dropped. In `mix_ups` only, `...` may join the pieces of a passage, and
  each piece is checked.
- `checklist_doubts`: rows you think the answer key got wrong, as `{"id", "why"}`, where `why`
  names the profile pointers or ad words behind the doubt. The answer key stays the truth: you
  still grade by it, and a doubt never changes a score. Code collects every doubt into one list
  for the owner to recheck later.

You never give a score. Code works out the letter's score from your answers: missed rows from
`requirements`, untrue claims from `claims`, the rest from `problems`, plus its own checks on
wording and length.

A pairwise answer file holds the one pair you judged: `{"standard": "v2-points", "pairs":
{"P01": {"pick": "first|second|same", "reason": "..."}}}`.

## 10. Calibration examples

The worked examples are the candidate's own answers on other letters for other jobs: the letter,
the job's checklist rows that matter, the rows they count as missed, and every problem they found
(§7) with its level and the words it rests on, then a worked reading of why, in this standard's
terms (which anchor, which test). They come after this standard in your message. They
quote real letters, so they live in a separate file that is never committed
(`evals/work/v2/calibration/examples.md`); only letters from the calibration half are ever used,
and a letter is never shown its own example.

Use them to learn how strictly the candidate reads the anchors: when a row counts as missed,
what a low looks like next to a high, which example choices they call weak, which paragraphs
they call mixed up, and which problems they don't count at all. Never compare your letter with
them, and never report a problem because your letter resembles an example: judge your letter by
this standard, as §3 says. An example teaches a rule; the rule decides.

This standard is frozen as judge v1 (points) once stage 8's calibration is done.
