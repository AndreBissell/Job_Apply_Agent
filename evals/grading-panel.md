# Blind grading panel

How the four judgement items in `rubric.md` (supported_musts_covered,
no_unsupported_claims, specific_detail, would_send) are graded by model agents when
the user isn't grading by hand. First used 2026-10-03 (`results/grading-opus-v1.md`,
`results/grading-opus-v2.md`). The user's own grades always win: a panel only fills
blank cells.

**Why this shape:** a model grader is lenient or drifts unless it is held to a written
standard, graded blind, double-checked and measured. Panel 1 matched the user on
8/15 held-out cells (stricter on claims); panel 2 re-graded panel 1's letters at
57/60. So compare engines on one panel's numbers, never a panel's numbers with the
user's.

## Procedure

1. **Standard.** `grading-standard.md` is fixed. Change it only on purpose, and
   re-grade every run you compare afterwards, because a changed bar moves every number.
2. **Pack.** Two runs on the same jobs:
   `python scripts/grading_panel.py pack <tag> <run_a> <run_b>` writes
   `evals/panels/<tag>/packet.md`: the profile, then per job the ad and the two letters
   under random ids. Job order, letter order and ids are shuffled. The key is in
   `evals/panels/<tag>/key/`, and graders are never pointed at it.
3. **Grade.** Two Opus subagents, run in parallel, use the grader prompt below; B
   works in reverse job order.
4. **Disagreements.** `python scripts/grading_panel.py disagreements <tag>`.
5. **Adjudicate.** One Opus subagent rules on the disagreements, using the adjudicator
   prompt below.
6. **Merge.** `python scripts/grading_panel.py merge <tag> [--fill] [--compare RUN=FILE]`
   writes `evals/runs/<run>/grades-<tag>.csv` for both runs. `--fill` copies the grades
   into blank rows of `grades.csv`; `--compare` measures agreement with another grades
   file (re-grade a run an earlier panel graded, for a consistency check).
7. **Report.** `python scripts/letter_lab.py report <run> --grades grades-<tag>.csv`
   writes `results/<run>-<tag>.md`. The graders' reasons quote letters, so they stay in
   the gitignored run folder; write the summary in `results/grading-<tag>.md` without
   profile details.

## Grader prompt (replace `<X>`, `<tag>`, and the order line for grader B)

> You are grader `<X>` in a blind cover-letter evaluation. Accuracy and consistency
> matter more than speed. Read in full, and read nothing else (no file under
> `evals/runs/`, `evals/results/`, or `evals/panels/<tag>/key/`):
> `evals/grading-standard.md` (the standard; apply it exactly); `evals/rubric.md`
> (background); `evals/panels/<tag>/packet.md` (the profile, then jobs J01-Jnn, each
> with its ad and two letters by different, hidden writers; don't guess them or let
> it matter). [Grader B: work through the jobs in REVERSE order.] Read the packet ONE
> JOB AT A TIME: the profile first, then for each job read the ad and write down its
> must-haves (backed / partly / not, with profile pointers) BEFORE reading its
> letters. Grade each letter on its own, never relative to the other. For
> no_unsupported_claims, check every claim about the candidate against the profile and
> every claim about the employer against the ad, and quote each failing claim. For
> would_send, apply the listed N triggers one by one; count words near 340. Write
> `evals/panels/<tag>/grader-<X>.json`:
> `{"jobs": {"J01": {"must_haves": [{"text", "backing": "backed|partly|not", "evidence": [...]}]}},
> "letters": {"L1234": {"job": "J01", "supported_musts_covered": {"grade": "Y|N", "reason", "missing": []},
> "no_unsupported_claims": {"grade", "reason", "bad_claims": [{"quote", "why"}]},
> "specific_detail": {"grade", "reason", "detail"}, "would_send": {"grade", "reason", "triggers": []}}}}`.
> Grade every letter on all 4 items, write the file with a tool so it is valid JSON,
> and check it parses. Reply with per-item Y counts and any ambiguity rulings.

## Adjudicator prompt

> You are the adjudicator in a blind cover-letter evaluation. Two graders applied the
> same standard and disagreed on the cells in `evals/panels/<tag>/disagreements.json`.
> Read `evals/grading-standard.md`, that file, and `evals/panels/<tag>/packet.md` (the
> profile plus the jobs involved), and nothing under `evals/runs/`, `evals/results/` or
> the panel's `key/`. For each cell, re-read the letter, the ad and the profile
> yourself, and rule by the standard's text, not by which grader argued better.
> specific_detail needs both an ad fact about this employer, used meaningfully, and a
> pass on the swap test. would_send applies the triggers and the light-edit budget as
> written. Where the standard is silent, say so and follow its stated tests. Write
> `evals/panels/<tag>/adjudication.json`:
> `{"rulings": [{"letter", "item", "grade": "Y|N", "reason", "sided_with": "A|B"}]}`,
> one ruling per cell, valid JSON. Reply with a one-line ruling per cell.

---

# v2: one fresh judge per letter

Standard: `grading-standard-v2.md`. Metrics: `docs/letter-metrics-plan.md` §6. Built
2026-10-26 (build step 2). The v1 procedure above stays as it was, for v1 runs.

**What changes from v1.** v1 had two graders reading every letter in one packet. In v2 each
letter has its own packet, and every packet goes to a **new** Opus subagent that has seen
nothing else. So no judge carries what it learned on one letter, or one employer, into the
next one. That gives each letter the same judge, from the same starting point.
- There are two graders, A and B, so a letter gets 2 fresh judges.
- A letter where they split gets 1 fresh adjudicator.
- Pairwise A/B picks get 1 fresh judge per pair, in each order.
- The judges run as Claude Code subagents, so there is no Vertex spend.

Everything under `evals/panels/` and `evals/work/` is gitignored. It holds the real profile,
the real ads and the letters.

## Procedure

1. **Answer key.** The reference checklist (`evals/work/v2/reference/ads/`) must verify. Both
   `v2-pack` and `v2-pairs` run `scripts/eval_reference.py`'s `verify()` themselves, and they
   refuse to run if it fails.
2. **Pack.**
   `python scripts/grading_panel.py v2-pack <tag> <run> [<run> ...] [--only KEY ...]`
   writes:
   - `evals/panels/<tag>/packets/<lid>.md`: one per letter, holding the profile, the job
     (ad + checklist) and the letter with its word count;
   - `packets/index.txt`: the list of packets;
   - the key in `key/letters.json`. Judges are never pointed at the key.

   Letter ids and job numbers are shuffled, and no packet names its run. A used tag is refused.
3. **Grade.** For every id in `index.txt`, start two fresh subagents, grader A and grader B,
   each with the grader prompt below and that one packet. Never give one subagent a second
   packet. A dev run of 17 letters takes 34 judges. Running them in parallel batches is fine.
4. **Check.** `python scripts/grading_panel.py v2-check <tag>` names every answer that is
   missing or has the wrong shape. Re-run each one it names with a new fresh judge. It also
   lists quotes that aren't in the letter. Code doesn't credit those (step 7).
5. **Disagreements.** `python scripts/grading_panel.py v2-disagreements <tag>` writes
   `disagreements/<lid>.json` for each letter where A and B split, and prints A/B agreement
   per group. A cell is one answer that can split:
   - send-readiness;
   - the claim counts;
   - each D question;
   - each requirement row;
   - each tick.
6. **Adjudicate.** Give each disagreements file to a fresh subagent with the adjudicator
   prompt. It writes `adjudication/<lid>.json`.
7. **Merge.** `python scripts/grading_panel.py v2-merge <tag>` refuses while any split cell
   has no ruling. When every cell has one, it:
   - takes agreed cells from A and split cells from the rulings;
   - checks every quote again: an addressed row whose quote isn't in the letter becomes N, and
     a claim whose quote isn't there is dropped;
   - writes `evals/work/v2/runs/<run>/judge-<tag>.json`, one record per ad: send-readiness,
     untrue Y/N, claim counts, gap fabrications, ticks, checklist and requirement rows;
   - writes `evals/panels/<tag>/checklist-doubts.md`: the answer-key rows a judge doubted,
     for the owner to recheck. A doubt never changes a score.
8. **Against the owner (stage 8).**
   `python scripts/grading_panel.py v2-agreement <tag> <run> [--only KEY ...] [--accept KEY ...]`
   reads the owner's `grades-owner.csv` in the run folder and writes
   `evals/panels/<tag>/agreement-<run>.md`. The results are counts, not percentages:
   - send-readiness, exact and within ±1;
   - weighted kappa, with its small-sample warning;
   - untrue TPR/TNR;
   - each reason tick.

   `--only` limits it to the check half. Read every flag the judge raised and the owner
   didn't. If it was a real miss by the owner, re-run with `--accept KEY`.
9. **Pairwise (comparing two versions).**
   `python scripts/grading_panel.py v2-pairs <tag> <baseline_run> <new_run> [--only KEY ...]`
   writes two packets per ad, `pairs/P###.md`, one in each order. Each pair packet gets a
   fresh judge with the pair-judge prompt. Then
   `python scripts/grading_panel.py v2-picks <tag>` writes `picks.json`:
   - wins, losses and ties;
   - the win rate, with a tie counted as half;
   - an exact sign test.

   An ad is won only when both orders pick the same letter. A pick that follows the position
   is a tie.

## v2 grader prompt (one packet; replace `<X>`, `<tag>`, `<lid>`)

> You are grader `<X>`, one fresh judge for ONE cover letter. Accuracy and consistency matter
> more than speed. Read `evals/grading-standard-v2.md` in full and apply it exactly. Then read
> your packet `evals/panels/<tag>/packets/<lid>.md`. Read nothing else: no other packet, and
> nothing under `evals/panels/<tag>/key/`, `evals/panels/<tag>/answers/`, `evals/work/`,
> `evals/runs/` or `evals/results/`.
>
> Work in the standard's order (§3): the profile, then the ad, then the reference checklist,
> and only then the letter. Answer §4, §5, §6, §7 in turn. Quote the letter's exact words for
> every addressed row and every claim. Code checks each quote against the letter, and an
> addressed row whose quote isn't there counts as not addressed. Grade by the reference
> checklist even where you doubt it, and put the doubt in `checklist_doubts`. Length is not
> quality.
>
> Write `evals/panels/<tag>/answers/<X>/<lid>.json` in the §9 shape with a tool, and check
> that it parses. Reply with one line: the grade, the number of untrue claims and any doubt.

## v2 adjudicator prompt (one letter; replace `<tag>`, `<lid>`)

> You are the adjudicator for ONE cover letter. Two judges (A and B) applied
> `evals/grading-standard-v2.md` to it and split on the cells in
> `evals/panels/<tag>/disagreements/<lid>.json`, which shows both answers for each cell. Read
> the standard in full, that file, and the letter's packet
> `evals/panels/<tag>/packets/<lid>.md`. Read nothing else: not `key/`, other packets or
> other answers.
>
> For each cell, re-read the letter, the ad, the checklist and the profile yourself, and rule
> by the standard's text, not by which judge argued better. Where the standard is silent, say
> so and follow its stated tests.
>
> Write `evals/panels/<tag>/adjudication/<lid>.json` as
> `{"rulings": [{"item", "value", "reason", "sided_with": "A|B|neither"}]}`, with one ruling
> per cell. The `item` is copied from the file. What goes in `value`:
> - `send_readiness`: 1–4. `sided_with` picks whose edit list is kept.
> - a `D..` question: Y or N, with the `quote` it rests on.
> - `req:<id>`: Y or N. Add `quote`, `presented_as` and `evidence_used` when your answer
>   differs from the judge you side with; otherwise theirs are kept.
> - `tick:<name>`: Y or N.
> - `claims`: the word `claims`, plus a `claims` field holding the full corrected list in the
>   §9 shape.
>
> Write valid JSON with a tool and check it parses. Reply with one line per cell.

## v2 pair-judge prompt (one pair packet; replace `<tag>`, `<pid>`)

> You are one fresh judge for ONE pair of cover letters. Read `evals/grading-standard-v2.md`
> in full: §8 is the rule for picking, and §4–§7 define what you weigh. Then read
> `evals/panels/<tag>/pairs/<pid>.md`, and read nothing else.
>
> Pick `first`, `second` or `same` by §8's order, stopping at the first step that separates
> the letters. Length is never a reason. Write
> `evals/panels/<tag>/answers/pairs/<pid>.json` as
> `{"standard": "v2", "pairs": {"<pid>": {"pick": "...", "reason": "..."}}}` with a tool,
> check it parses, and reply with the pick and the reason.
