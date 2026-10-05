# Blind Opus grading: workflow-v1 vs one-shot (15-ad set)

2026-10-03. The four judgement items in `evals/rubric.md` (supported_musts_covered,
no_unsupported_claims, specific_detail, would_send), graded by a panel of Opus
agents instead of the user, on all 30 letters: `workflow-v1` (15) and
`baseline-oneshot` (15: the user's original 9 plus the 6 new ads).

## Method

1. **Base levels first.** One Opus agent wrote `evals/grading-standard.md` from the
   rubric, the user's binding rulings from the plan's Decision log, and the user's
   own grades + notes on 5 of the 9 one-shot letters they graded. It never saw a
   workflow letter.
2. **Blind, double grading.** Two Opus graders independently graded all 30 letters
   against the standard. Letters had random ids and no engine labels; job order and
   the order of each job's two letters were shuffled; grader B worked in reverse job
   order. Per job, each grader listed the ad's must-haves and the profile's backing
   for each, then graded both letters against that one list.
3. **Adjudication.** A third Opus agent, also blind, ruled on every cell where the
   two disagreed.
4. **Check against the user.** The user's grades on the other 4 one-shot letters were
   held out of step 1, so agreement on them is an honest accuracy check.

Caveat: grader B read the letters of J01-J09 before writing those jobs' must-have
lists (the packet was too long for one read); its lists were still built from the
ad and profile alone.

## Panel reliability

| | |
|---|---|
| Grader A vs B, cells agreeing | **111/120** (musts 29/30, claims 28/30, detail 25/30, would_send 29/30) |
| Adjudicated | 9 cells, all ruled N (A 3, B 6). 5 were specific_detail borderlines |

## Agreement with the user (one-shot letters the user graded)

| Set | Cells agreeing |
|---|---|
| Calibration (5 letters, seen by the standard writer) | 11/20 |
| **Held out (4 letters)** | **8/15** |

15 of the 16 disagreements are the panel being **stricter**. By item:
- `no_unsupported_claims`: the user passed 9/9; the panel failed 8 of them, mostly
  for invented scope, process or outcomes added to a real project ("rigorous
  testing", "optimisation techniques", a widened outcome) and two for a wrong tenure.
  These are small embellishments that read naturally, which is how a reader misses
  them; the user's grades (2026-10-02) also predate the Phase 5 overclaim rulings.
- `supported_musts_covered`: 4 user Y → panel N (a backed must-have shown with weaker
  evidence than the profile holds, which the standard counts as N).
- `specific_detail`: 3 user Y → panel N, 1 user N → panel Y.
- `would_send`: full agreement (all N).

So the panel's **absolute** rates are not comparable with the user's grades; its
**relative** comparison of the two engines (same grader, same standard, blind) is.

## Engines compared (panel grades, 15 letters each)

| Item | Workflow (`workflow-v1`) | One-shot (`baseline-oneshot`) |
|---|---|---|
| supported_musts_covered | **15/15** | 4/15 |
| no_unsupported_claims | **6/15** | 2/15 |
| specific_detail | **11/15** | 4/15 |
| would_send | **2/15** | 0/15 |
| All four judgement items | 2/15 | 0/15 |
| Full rubric pass (with the code checks) | **2/15** | 0/15 (generic_phrase_limit fails 14/15) |

Per-letter tables: `workflow-v1-opus-v1.md`, `baseline-oneshot-opus-v1.md`. Reasons per N are
in `evals/runs/<run>/grades-opus-v1.csv` (gitignored: they quote the letters). The panel's
files are in `evals/panels/opus-v1/` (gitignored); procedure in `evals/grading-panel.md`.

## What the panel found in the workflow letters

- **Claims (9 N), despite `check_claims` passing 14/15.** 7 are experience wording on
  a bare skill listing (above): `check_claims` on `mid` accepts "experience with React"
  when the profile lists React as a skill. 1 pluralised a single placement. 1 was an
  **invented link**: the ad asked for "a link to a 2 minute video" and a style
  revision wrote `youtu.be/<name>`. The judge passed it because it isn't a claim about
  experience. Fixed in code the same day (`check_claims` stage 1 now blocks any link
  or email not in the profile; the writer prompt forbids inventing them).
- **would_send (13 N).** The top trigger is a sentence that leads with a gap
  ("Although I have not...", 10 of 13), then experience listed without applying it to
  the ad's work, then a failed detail/claims item. The gap sentences come from the
  partial must-covers backed only by a skill listing (plan Decision log, Phase 6
  finding). The 2 Y: the .NET graduate role and the AI Developer role.
- **Coverage: 15/15.** The guardrails' must-cover list and `check_requirements` work.
