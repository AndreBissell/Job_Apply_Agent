# Grading standard v2 (the letter judge)

**Status:** approved by the owner (2026-10-26). Not yet used on any letter. Calibration examples
(§10) are added in stage 8, then it is frozen as judge v1.
**Applies to:** letters on the frozen v2 set (`letter_lab.py --set v2`). Runs graded before v2
keep `grading-standard.md` (v1).
**Metrics it serves:** docs/letter-metrics-plan.md §3, Layers 1 and 2, and the predicted
send-readiness. Code computes every score from your answers; you never add anything up.

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
4. Answer in this order: requirements (§4), claims (§5), the checklist (§6), then send-readiness
   and the reasons (§7). The later answers depend on the earlier ones.
5. **Length is not quality.** A longer letter earns nothing for being longer. Extra words that
   add no fact about the candidate or the job count against it (D6, too_long).

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
- **Partial rows** count as addressed when the related evidence is stated honestly against the
  requirement ("I built the reporting in Tableau, and the same modelling carries over to Power
  BI"). A partial row claimed as fully had is still addressed, but it is also a **stretch**
  claim (§5).
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
| D2c | Are the sentences that connect evidence to the job specific to it, rather than a formula ("prepares me to", "exactly the kind of", "positions me well to")? |
| **D3 Employer-specific** | |
| D3a | Is at least one fact from the ad about this employer, team, product, project, program or platform used meaningfully, tied to why the candidate wants the role or how their experience applies? The role title, the company name, the contract length, generic industry phrases ("innovative solutions") and a restated requirement don't count. |
| D3b | **Swap test:** pick the most employer-specific sentence. If you swap in another employer advertising the same title and change only the name, does the sentence stop fitting? (Y = it stops fitting.) |
| **D4 Motivation** | |
| D4a | Is there a specific reason for wanting this role or this employer, grounded in the ad, beyond generic praise ("your reputation for excellence")? |
| **D5 Concrete evidence** | |
| D5a | Are there at least 2 pieces of evidence with specifics: the tool, the task, and the scale or the outcome? |
| D5b | Does every number in the letter come from the profile (or the ad, for employer facts)? Y if the letter has no numbers. |
| D5c | Is at least one specific **checkable**: a named project, tool or organisation, or a number from the profile? |
| **D6 Voice and polish** | |
| D6a | Does it avoid a stock opener ("I am writing to express my interest", "I am excited to apply for")? |
| D6b | Does it have fewer than 3 machine-written tells ("demonstrating my ability to" tails, "seamlessly", triple adjectives, stacked intensifiers, "I am confident that my")? List the ones you find. |
| D6c | Is no phrase, sentence structure or achievement repeated within the letter? |
| D6d | Is the spelling Australian throughout? List any US spellings. |
| D6e | Does the tone match the ad's own tone (formal, casual, technical, warm)? |
| D6f | Is it 250–340 words? Use the word count the packet gives. |
| D6g | Is the close specific to this role, rather than a stock close ("I look forward to hearing from you" alone, "thank you for your consideration")? |
| D6h | Is self-promotion moderate: confident but with no stacked superlatives ("exceptional, outstanding results")? |
| **D7 Understands the job** | |
| D7a | Does the letter show it grasped the goal of the role and what is hard about it, beyond restating the duties, and connect the candidate to that? |

## 7. Predicted send-readiness (1–4) and the reasons

### 7.1 The scale (the owner's, defined by how much editing the letter needs)

| Grade | Meaning |
|---|---|
| **4** | Send as is: word-level fixes only (a spelling, the salutation, a swapped word). |
| **3** | Light edits: change, add or delete **at most 2 sentences in total**. |
| **2** | Heavy edits: rework a paragraph, reorder paragraphs, or swap the main example. |
| **1** | Start over: the letter is built on the wrong evidence or misreads the job, so fixing it means rewriting most of it. |

"Send" means the candidate would be **happy** to send it, not just that they could. A 4 is rare:
it needs every must-cover row addressed with its best evidence, the strongest work experience in
concrete detail, an employer-specific reason for this role, every paragraph applying its evidence
to the ad's work, no untrue claims, and a voice that reads like a person, in 250–340 words.

### 7.2 How to grade it

List the edits you'd make to turn the letter into one the candidate would be happy to send
(`edits`), each with the number of sentences it touches. Grade from that list, using the scale.
These problems always need an edit (v1's "not sendable as is" triggers, kept):

- a `must_cover` row not addressed, or addressed with weaker evidence when the row holds stronger;
- any `stretch`, `fabricated` or `employer_fact_not_in_ad` claim;
- D3a or D3b is N (nothing about this employer);
- over 340 words;
- a sentence that leads with a gap ("While I am new to...", "Although my background is not
  in..."). One short clause of honest related-experience framing is fine;
- no clear reason for wanting this role, or experience listed without saying how it applies;
- stock phrasing or generic filler in more than one place, or the same phrase or achievement
  repeated;
- three or more machine-written tells;
- more than three US spellings.

Count each one at the edit it really takes: one stretched sentence fixed in place is 1 sentence;
a missing employer reason usually needs a new sentence or two; a paragraph that recites the
profile without applying it needs that paragraph reworked (a 2).

### 7.3 The reasons ("why not a 4?")

When the grade is below 4, tick every reason that applies (they are the owner's six ticks). Leave
all of them unticked for a 4.

| Tick | When to tick it | Usually shows up as |
|---|---|---|
| `generic` | it would fit any company advertising this title | D3a/D3b N, D2b N, D7a N |
| `missed_important` | something important from the ad is missing | a `must_cover` row, or an essential or important supported row, not addressed |
| `weak_example` | a weak or wrong example is used where a better one exists | D2a N, D5a N, weaker evidence used than the row holds |
| `sounds_ai` | it sounds machine-written, or uses stock phrases | D6a, D6b, D6c or D2c N |
| `no_real_reason` | no real reason for wanting this job | D4a N |
| `too_long` | too long or waffly | D6f N, or filler that adds no fact |

You may add a one-line `note`, as the owner may.

## 8. Picking between two letters (pairwise)

A pairwise packet holds one ad, its reference checklist, the profile, and two letters, `first`
and `second`, by different hidden writers. As with grading, each packet gets a fresh judge. The
same pair is also given to another fresh judge with the order swapped, so a pick that depends on
which letter came first counts as a tie. You only ever see one order.

Pick the letter the candidate would rather send: `first`, `second` or `same`. Decide in this
order, and stop at the first step that separates them:
1. untrue claims: the letter with fewer `fabricated`, then fewer `stretch` claims;
2. predicted send-readiness (§7);
3. the `must_cover` rows addressed, then the other supported and partial rows by importance;
4. the recruiter checklist (§6).

Choose `same` only if none of these separates them. Length is not a reason. Give a one-line
reason for each pick.

## 9. The answer file (JSON)

Write valid JSON with a tool to the answer path the packet names, and check it parses. The file
holds the one letter you graded (code later collects every letter's file into one):

```json
{
  "standard": "v2",
  "letters": {
    "L1234": {
      "job": "J01",
      "requirements": [
        {"id": "R1", "addressed": "Y", "quote": "At Acme I built the stock dashboards in Power BI",
         "presented_as": "experience", "evidence_used": ["experience:12#s2"], "reason": ""},
        {"id": "R4", "addressed": "N", "quote": "", "presented_as": "none",
         "evidence_used": [], "reason": "not mentioned"},
        {"id": "R6", "addressed": "N", "quote": "my thesis used similar models",
         "presented_as": "related", "evidence_used": ["qualification:3"],
         "reason": "gap row, framed honestly as related"}
      ],
      "claims": [
        {"quote": "led a team of five analysts", "about": "candidate", "label": "stretch",
         "source": ["experience:12#s4"], "requirement": null,
         "why": "the entry says contributed to a team of five, not led"},
        {"quote": "Acme's new Sydney office", "about": "employer",
         "label": "employer_fact_not_in_ad", "source": [], "requirement": null,
         "why": "the ad doesn't mention an office opening"}
      ],
      "checklist": {
        "D2a": {"answer": "Y", "quote": "...", "reason": ""},
        "D2b": {"answer": "N", "quote": "", "reason": "the second paragraph lists tools without tying them to the ad"}
      },
      "send_readiness": {
        "grade": 3,
        "edits": [{"what": "fix the 'led' stretch", "sentences": 1},
                  {"what": "remove the office fact", "sentences": 1}],
        "reason": "two one-sentence fixes; otherwise ready"
      },
      "ticks": {"generic": false, "missed_important": false, "weak_example": false,
                "sounds_ai": false, "no_real_reason": false, "too_long": false},
      "note": "",
      "checklist_doubts": []
    }
  }
}
```

Field rules:
- `requirements`: one entry per checklist row except `not_for_letter`, in checklist order.
  `addressed` is Y or N. `presented_as` is one of `experience`, `skill`, `related`, `claimed`
  (a gap row claimed as had) or `none`. Leave `quote` empty when nothing addresses it, except for
  `related` and `claimed`, which quote the words.
- `claims`: every claim, in letter order. `about` is `candidate` or `employer`. `source` holds
  profile pointers, or for employer facts the ad's words. `requirement` is a row id or null.
- `checklist`: all 18 ids, D2a to D7a.
- `send_readiness.grade` is 1, 2, 3 or 4.
- `ticks`: all six keys, true or false; all false when the grade is 4.
- `checklist_doubts`: rows you think the answer key got wrong, as `{"id", "why"}`, where `why`
  names the profile pointers or ad words behind the doubt. The answer key stays the truth: you
  still grade by it, and a doubt never changes a score. Code collects every doubt into one list
  for the owner to recheck later.

The owner's "anything untrue or stretched?" answer is worked out from `claims` by code: Y when
any claim is `stretch`, `fabricated` or `employer_fact_not_in_ad`.

A pairwise answer file holds the one pair you judged: `{"standard": "v2", "pairs": {"P01":
{"pick": "first|second|same", "reason": "..."}}}`.

## 10. Calibration examples

Added in stage 8 from the calibration half of the owner's grades (their grade, ticks and notes
on real letters, as worked examples). Until then this standard has none, and it is not frozen.
