# Seek Quick Apply: collected samples (Phase 9 groundwork)

Collected from outerHTML the user copied out of their own browser, starting 2026-10-04. This
is the reference for plan §10.1 (Phase 9: help with the live Quick Apply questions). It holds
the **questions, their types and options, and the markup patterns** only. Never record what
was selected or typed (Seek pre-fills answers), the user's name, email or CV file names.

Kinds follow plan §10.1: `user` = no AI help, never sent to the LLM; `assisted` = relates to
the ad.

## Markup patterns (confirmed so far)

| What | Selector / pattern | Seen in |
|---|---|---|
| Apply step URL | `/job/{jobId}/apply/role-requirements?sol=...` (the `sol` parameter looks like a tracking token; not needed) | S1 |
| Job id | from the URL path only; not present in the questions HTML | S1 |
| Job header | `[data-automation="job-header"]` wraps the progress bar; above it, "Applying for" + `<h1>` = job title, next `<span>` = employer; the logo `img[alt]` = employer name | S1, S2 |
| Progress bar | `nav[aria-label="Progress bar"]`; current step = `button[aria-current="step"]`. Steps seen: Choose documents → Answer employer questions → Update SEEK Profile → Review and submit | S1, S2 |
| One question | every answer field has `name="questionnaire.<questionId>"`; group the form's fields by that name | S1, S2 |
| Question ids: two families | **Seek's standard library:** `AU_Q_<n>_V_<version>`, options `AU_Q_<n>_V_<v>_A_<optionId>` (S2). The same `AU_Q_<n>` should mean the same question on every job, so its kind can be cached by id with no LLM call. **Confirmed for `AU_Q_6_V_10` and `AU_Q_13_V_2`**: identical id, text and options on S2 and S3 (different jobs). **Employer-written:** `indirect_<uuid>_<n>`, one uuid per job's questionnaire (S1) | S1, S2 |
| Single choice | `fieldset[role="radiogroup"][id="question-<qid>"]`; text = its `<legend>`; options = `input[type=radio]` (value = option id) + `label[for=<input id>]` | S1, S2 |
| Dropdown | `select#question-<qid>`; text = `label[for="question-<qid>"]`; options = `<option>` text (value = option id). No empty "choose" option seen | S1, S2 |
| Free text | `textarea#question-<qid>`; text = `label[for="question-<qid>"]` | S1 |
| Multi choice (checkboxes) | **No fieldset, no label for the question.** A plain `div` holds the question text in a `<strong>` (inside a `span`, not a `label`/`legend`), then the options: `input[type=checkbox][name="questionnaire.<qid>"]`, each with **id = option id** (no `value`), + `label[for=<option id>]`. So the text has to be taken from the first `<strong>` in the closest container that holds all the inputs with that name | S2 |
| Required marker | **not seen**: no `required` / `aria-required` in S1 | — |
| Buttons | `[data-testid="continue-button"]`, `[data-testid="back-button"]` (the extension never clicks them) | S1 |
| Personal data | the site header shows the user's first name, marked `data-adora-mask="true"` (Seek's own masking flag for session replay); radios carry `checked` and textareas their typed text (pre-filled answers) | S1, S2 |
| Unstable | class names (`_1ppah1f0`, `_1bnu76l...`) are generated; never select on them | S1 |

Capture rule that follows: read question text, type and options only. Never read `checked`,
`selected` or field values, and skip anything under `[data-adora-mask]`.

## Samples

### S1: IT Trainee, ATSICHS Brisbane (job 94904230)
Step: "Answer employer questions". 7 questions, all `user`, so no AI call.

| # | Question | Type | Options | Kind |
|---|---|---|---|---|
| 1 | Are you legally entitled to work in Australia? | single | Yes / No | user (work rights) |
| 2 | Do you identify as Aboriginal or Torres Strait Islander? | single | Yes, Aboriginal / Yes, Torres Strait Islander / Both Aboriginal and Torres Strait Islander / Neither Aboriginal and Torres Strait Islander / Prefer Not To Say | user (identity; sensitive) |
| 3 | What motivated you to apply for this position? | free text | — | user (motivation, by default) |
| 4 | Please provide your salary expectations: | dropdown | $40,000 - $60,000 … $180,000 - $200,000 (8 bands of $20k) | user (salary) |
| 5 | How did you hear about this job? | dropdown | Seek advertisement / Linkedin advertisement / LinkedIn post from a connection / ATSICHS Brisbane website / Facebook / Community event / Careers event / Job seeker agency / ATSICHS Brisbane Email Notification | user (source) |
| 6 | Have there been any findings against you in relation to allegations of inappropriate behaviour with respect to children/young people in your past or current employment? | single | Yes / No | user (legal declaration) |
| 7 | Are you aware of any compliance action under the National Law or any other law in relation to you that pertains to inappropriate behaviour with respect to children? | single | Yes / No | user (legal declaration) |

### S2: Full Stack Developer, Barrington Group Australia Pty Ltd
Step: "Answer employer questions". All Seek standard-library ids. The `<form>` paste was cut off
at the chat limit inside question 6 (after the 10th option), so the rest of the list and any
later questions are **missing**.

| # | Question (id) | Type | Options | Kind |
|---|---|---|---|---|
| 1 | Which of the following statements best describes your right to work in Australia? (`AU_Q_6_V_10`) | dropdown | I'm an Australian citizen / I'm a permanent resident and/or NZ citizen / I have a family/partner visa with no restrictions / I have a graduate temporary work visa / I have a holiday temporary work visa / I have a temporary visa with restrictions on work location (e.g. skilled regional visa 491) / I have a temporary protection or safe haven enterprise work visa / I have a temporary visa with no restrictions (e.g. doctoral student) / I have a temporary visa with restrictions on work hours (e.g. student visa, retirement visa) / I have a temporary visa with restrictions on industry (e.g. temporary activity visa 408) / I require sponsorship to work for a new employer (e.g. 482, 457) | user (work rights) |
| 2 | How many years' experience do you have as a full stack developer? (`AU_Q_6804_V_4`) | dropdown | No experience / Less than 1 year / 1 year / 2 years / 3 years / 4 years / 5 years / More than 5 years | **assisted (years)** |
| 3 | Have you worked in a role which requires C# development experience? (`AU_Q_218_V_2`) | single | Yes / No | **assisted (skill, in a role)** |
| 4 | What's your expected annual base salary? (`AU_Q_8_V_2`) | dropdown | $30k, $35k … $150k, $170k, $200k, $250k, $300k, $350k, $350k+ (23 steps) | user (salary) |
| 5 | How much notice are you required to give your current employer? (`AU_Q_13_V_2`) | dropdown | None, I'm ready to go now / 1 week / 2 weeks / 3 weeks / 4 weeks / 5 weeks / 6 or more weeks | user (notice) |
| 6 | Which of the following programming languages are you experienced in? (`AU_Q_136_V_3`) | **multi (checkboxes)** | JavaScript / HTML / CSS / Java / C / C# / Python / C++ / .NET / Objective-C / … (cut off; at least one more, option id `_A_27305`) | **assisted (skills list)** |

Notes for the design:
- Q3 asks about C# **in a role**, not just knowing it. "Have you" + "worked in a role" means
  work experience counts, a course or a listed skill doesn't. The "have" view must tell
  experience evidence apart from a bare skill listing (the letter's `listing_only` rule).
- Q2 is the years case: compute from experience dates, never round up. "As a full stack
  developer" means matching role titles/evidence, not all development work.
- Q6 mixes languages with a framework (.NET) and markup (HTML, CSS); option names need
  `normalise_skill` matching against the checklist and the profile.

### S3: job not named (pasted 2026-10-04)
Two questions, both Seek standard library, both identical to S2:

| # | Question (id) | Type | Kind |
|---|---|---|---|
| 1 | Which of the following statements best describes your right to work in Australia? (`AU_Q_6_V_10`) | dropdown, same 11 options as S2 | user |
| 2 | How much notice are you required to give your current employer? (`AU_Q_13_V_2`) | dropdown, same 7 options as S2 | user |

All `user`, so no AI call. A short questionnaire made only of library questions is probably
common.

### S4: job not named (pasted 2026-10-04; a front-end React role)

| # | Question (id) | Type | Options | Kind |
|---|---|---|---|---|
| 1 | Which of the following statements best describes your right to work in Australia? (`AU_Q_6_V_10`) | dropdown | same 11 as S2/S3 | user |
| 2 | What's your expected annual base salary? (`AU_Q_8_V_2`) | dropdown | same 23 as S2 | user |
| 3 | How many years' experience do you have as a Front End React Developer? (`AU_Q_D29EC90383D57C663E2D2A43F205280B_V_2`) | dropdown | **blank disabled placeholder**, then the same 8 brackets as S2's years question (No experience … More than 5 years) | **assisted (years)** |

New here:
- **A third id family:** `AU_Q_<32 hex>_V_<v>`, options `..._A_<same hex>_<1-8>`. Same template
  as S2's `AU_Q_6804_V_4` ("years' experience as a full stack developer"), but the role is
  different and so is the id. So "years as <role>" questions get a generated id per role: they
  can't be recognised by id, only by their text template.
- **Placeholder option:** `<option value="" disabled selected></option>` when nothing is
  pre-filled. Skip options with an empty value.

### S5: job not named (pasted 2026-10-04; a React Native / Expo role)
All employer-written. A different id shape from S1:
question `indirect_<questionnaire uuid>_<question uuid>`, option values
`generated_indirect_<questionnaire uuid>_<index>`. **Option values repeat across questions**
(both radio questions have `_0`, `_1`), so an option is only unique together with its question
id. Option labels carry trailing spaces ("Yes "); trim them.

| # | Question | Type | Options | Kind |
|---|---|---|---|---|
| 1 | Which of the following best describes your Australian working rights? | single | Australian Citizen/ Permanent Resident/ New Zealand Citizen / I am on a VISA that allows for full-time work (excluding WHV 417) / I do not have any current Australian Working rights | user (work rights, **employer wording**) |
| 2 | Are you comfortable with this being in an in-office role? (8:30am to 5pm, Monday to Friday) with WFH flexibility? | single | Yes / No | user (work arrangement) |
| 3 | What are your salary expectations for this role? | **free text** | — | user (salary, free text) |
| 4 | How many years of experience you have with building application on React Native with Expo, targeting web, iOS and Android ? | **free text** | — | **assisted (years + specific skill, free text)** |

What it changes:
- **Work rights and salary also come employer-written**, with no library id and in free
  text. Sorting by id alone misses them, so the `user` keyword filter on the question text
  (work rights / visa / salary / notice / office hours) is needed as well as the id table.
- **Work-arrangement questions** (in office, hours, WFH) are a new `user` group.
- **Q4 is the hard case.** It's a years question, but free text, worded differently from the
  template, and about one skill on several platforms. Only the model will recognise it. The
  answer has to combine years computed in code with an honest note: "React Native with Expo"
  is not React.js, so web-only React experience gives a gap (ask the user, as in §10.1), not a
  number of years.

## Seek standard-library ids seen

| Id | Question | Kind | Jobs |
|---|---|---|---|
| `AU_Q_6_V_10` | right to work (11-option visa list) | user | S2, S3, S4 |
| `AU_Q_8_V_2` | expected annual base salary | user | S2, S4 |
| `AU_Q_13_V_2` | notice period | user | S2, S3 |
| `AU_Q_136_V_3` | which programming languages are you experienced in | assisted | S2 |
| `AU_Q_218_V_2` | worked in a role requiring C# development experience | assisted | S2 |
| `AU_Q_6804_V_4` | years' experience as a full stack developer | assisted | S2 |
| `AU_Q_<32 hex>_V_2` | years' experience as a Front End React Developer (generated per role) | assisted | S4 |

Open: whether a skill-specific library question (e.g. `AU_Q_218` = C#) has a different id per
skill, or one id with the skill filled in. Only a second "worked in a role requiring X" on another
job will tell.

## Question kinds seen so far

- `user`: work rights (yes/no and Seek's 11-option visa list), Indigenous identity, motivation,
  salary (bands, single figures and free text), how you heard, notice period, work
  arrangement (in office / hours / WFH), child-safety legal declarations.
- `assisted`: years of experience in a role (dropdown), "worked in a role requiring X"
  (yes/no), "which languages are you experienced in" (checkboxes), years with a specific
  skill (free text, employer-written).

## Findings for the Phase 9 design (after 5 jobs, 22 questions)

1. **Every job had `user` questions; three of five had `assisted` ones.** 17 of 22 questions
   were `user` (work rights in all 5 jobs, salary in 4, notice in 2). So the `user` filter
   matters more than the model, and many pages need no AI call at all.
2. **Sort in layers, cheapest first:**
   - *by id*, for Seek's fixed library questions (`AU_Q_<n>`): a small table in code
     (`AU_Q_6` work rights, `AU_Q_8` salary, `AU_Q_13` notice = `user`; `AU_Q_136` languages,
     `AU_Q_218` C# in a role = `assisted`);
   - *by keywords on the text*, for `user` topics in any wording (S5 asks work rights and
     salary in its own words): work rights / visa / citizen, salary / pay, notice / start,
     office / hours / WFH, how did you hear, identity, legal declarations. Runs before any
     model call, so these never reach the LLM;
   - *by text template*, for Seek's generated per-role questions: "How many years' experience
     do you have as (a/an) <role>?" (seen twice, same 8 brackets) and probably "Have you
     worked in a role which requires <skill> experience?";
   - *by the small model*, for what's left: employer-written technical questions such as S5's
     React Native one.
3. **Years questions come in two forms:** Seek's fixed 8-bracket dropdown (map computed years
   to a bracket in code, rounding down) and employer free text (S5) where the answer is a
   short sentence. Both take the years from code, computed from the dates of experience that
   matches the *specific* skill or role asked about, never the model's arithmetic.
4. **Capture is settled for four control types:** radio (`fieldset` + `legend`), dropdown
   (`select` + `label[for]`), free text (`textarea` + `label[for]`), checkboxes (first `strong`
   in the container; option ids on the inputs). Group by `name="questionnaire.*"`; key options
   by question id + option value (values repeat across questions in S5); trim labels; skip
   empty-value placeholder options; never read `checked`, `selected` or typed values.

## Still unknown (none blocks 9a)

- The rest of S2's languages list and anything after it.
- A second "worked in a role requiring X" for another skill: is `AU_Q_218` one id per skill?
- An open "describe your experience with..." question (S5's free-text one asks for years, not
  a description).
- Other field types (number, date), a "required" marker, and whether moving between apply
  steps changes the URL without a page load. All can be checked during 9a's live check.
