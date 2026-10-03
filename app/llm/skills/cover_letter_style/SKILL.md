# Cover-letter style guide

Loaded into the writer's prompt before drafting (docs/cover-letter-loop-plan.md
§5.4). This is the *prevention* half; `style_lint.py` is the *detection* half
that runs after. They read the same `banned_phrases.txt`, so the rule and the
check can't drift apart.

## Rules

- **Plain, specific sentences.** Real project names, numbers and outcomes beat
  adjectives. "Built an itinerary generator that beat a GPT-5 mini baseline on
  4 of 5 metrics" beats "I have a proven track record of success".
- **No em dashes.** Use a comma, a full stop, or rewrite the sentence.
- **One clear, specific reason for wanting *this* job.** Not "I am passionate
  about technology" — something that could only be said about this role or
  company, drawn from `job.company_facts` / `job.keywords`.
- **One page: aim for 250–300 words.** Over ~340 words doesn't fit a page.
  Under 230 reads as thin.
- **Australian spelling** (organise, analyse, colour, programme where it means
  a course of study, licence as a noun / license as a verb). See
  `us_to_au.txt` for the specific pairs checked.
- **Open "Dear Hiring Manager,"** (addressing a named contact can be tuned
  later). **Close "Sincerely, {name}"** using the candidate's name as given.
- **Name the degree and university by name** for a recent or current graduate.
- **Never invent or embellish.** Every claim must trace to an evidence
  pointer. If the evidence is partial (e.g. Tableau when the ad asks for Power
  BI), say so honestly rather than blurring the distinction.
- **Avoid the phrases in `banned_phrases.txt`.** They read as generic or
  machine-written regardless of how true they are.

## Voice

Tone and rhythm should match the candidate's own writing, supplied as a voice
reference (`app/llm/letter/style.py::voice_reference`) — **never as a source of
facts.** Candidates vary: some write short, direct sentences; some write
longer, reflective ones. Match the sample's rhythm and level of directness,
not its content or topic.

## Shape (for reference — a typical good letter)

1. **Opening** — who you are, the role, one honest reason you want it.
2. **Evidence paragraph(s)** — the strongest headline requirements, each with
   a concrete example.
3. **Closing** — a short line tying your interest back to the company/role,
   then the sign-off.

Three to four paragraphs. No subject line, no date header, no placeholders.
