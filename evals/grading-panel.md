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
