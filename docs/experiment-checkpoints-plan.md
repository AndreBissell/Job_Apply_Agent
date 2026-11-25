# Experiment checkpoints and comparison: plan (branch `cost_perform_analysis`)

**Status:** v1.0, APPROVED by the owner 2026-11-25. **DONE 2026-11-25**: steps 0–7 built (c0 and c1 are in `evals/results/v2/checkpoints.jsonl`); the owner's manual checks below are still open.

**Where it fits:** row T1 in [optimisation-map.md](optimisation-map.md) ("Comparison tool… build
before E3"), plus a history log the map did not have. Every pass/fail rule in
[agent-optimisation-plan.md](agent-optimisation-plan.md) §7 and §9 is read from the reports this
tool writes. It makes no Gemini or judge call, so it costs nothing to run.

---

## Goal

When an optimisation experiment is finished, record its result once as a **checkpoint**. Then:

1. **Compare any two checkpoints** side by side, ad by ad: cost, time and judge points per letter
   and in total, how each problem type changed per letter (for example "missed rows −0.24 a
   letter"), how many ads went up and down, and whether each difference is bigger than the noise.
2. **Keep a history** of every checkpoint, so the 5th optimisation can be compared with the 1st,
   and the chain of kept changes shows how cost, time and score moved over the whole project.

Both are markdown reports, committed, readable on GitHub. The checkpoints are committed too, so the
history survives a dead laptop.

## Context: what exists today (checked against the code and files, 2026-11-25)

Everything the tool needs is already written by a run and a judging. Nothing new has to be
measured.

| Need | Where it is now | Notes |
|---|---|---|
| Letters in a run, time and drafts per letter | `evals/work/v2/runs/<run>/run.json`: `run_id`, `engine`, `strong_model`, `split`, `created_at`, and `results[]` with `key`, `seconds`, `drafts`, `status`, `clean`, `letter_run_id`, `cost_usd`, tokens | `results[]` also holds `title` and `company`: **these must never be copied** |
| Cost and tokens per letter, per step | `llm_usage` rows in `evals/work/v2/eval.db`, joined on `run_id` = `letter_run_id` | Exactly how `letter_lab.py cost-report` builds its table (`_cost_rows`, `scripts/letter_lab.py:1408`) |
| Judge points and problems per letter | `runs/<run>/judge-<tag>.json`, keyed by ad: `points`, `by_metric` (each problem type -> list of `low` / `high`), `missed_rows` (row ids like `R10`), `untrue`, `words`, `stock_hits`, `checklist` (Y/N per item), `judge` (version and hashes) | Also holds `issues`, `problems`, `note`, `claims`, `quote_problems`, `checklist_doubts`: **letter quotes, never copied** |
| The judge version | `evals/results/v2/judge-v1.json` (committed): backend, model, CLI version, and the hashes of the standard, instructions and examples | Every judge record carries the same block, so a checkpoint can check it |
| Points values | `scripts/letter_points.py` (the one scorer): `VALUES`, `CAP`, `score(issues)` | A change to it would change old scores, so its hash is recorded |
| Relevant ads | `letter_lab._v2_ads()`: the owner's Would-apply label per ad | 10 of the 17 dev ads |
| Gemini prices | `client.PRICES` in `app/llm/client.py` | Flash prices double 2027-01-01 |
| Judge-only noise | `runs/dev-agent-v1/judge-pts-cal-2.json` (8 letters) + `judge-pts-check-1.json` (9 letters): judge v1 on the same letters as `judge-v1-dev.json` | Plan §9.0: paired SD 0.65, mean moved 0.19 |

`scripts/letter_lab.py` is already 2,490 lines with 11 subcommands. Existing tests:
`tests/test_letter_lab_report_v2.py`, `test_letter_lab_sets.py`, `test_letter_points.py`,
`test_letter_metrics.py`, `test_grading_panel_v2.py`.

## Constraints: what not to touch

- **No app code, prompts, judge, standard, examples, scorer or eval-set files change.** This is
  read-only eval tooling plus one committed results file and its reports.
- **Public repo.** The checkpoints file and the reports hold numbers, ad keys, row ids (`R10`),
  checklist item ids and settings only. No letter text, quotes, judge notes, ad titles, company
  names or profile text. Built from an **allowlist** of fields, never by copying a record and
  deleting the risky fields (a field added later would leak). A test pins it.
- **Append-only.** A line in `checkpoints.jsonl` is never edited or deleted. A correction is a new
  checkpoint with `replaces: <id>`.
- **The test split stays closed.** `checkpoint` refuses a `test` run unless `--allow-test` is given
  (for the final graded run at the end of the project, plan order).
- No change to how `run`, `report`, `cost-report` or `grading_panel.py` behave.

---

## Decisions agreed with the owner (2026-11-25)

| # | Decision | Why |
|---|---|---|
| D1 | Checkpoints live in one committed, append-only file, `evals/results/v2/checkpoints.jsonl`, one line each | Backed up by git; one file is easy to load; a new checkpoint is one added line in a diff |
| D2 | Reports are markdown, generated from the JSONL, never edited by hand | Same as the other `evals/results/v2/*.md` files; readable on GitHub; the JSONL is the only source of truth |
| D3 | A checkpoint stores **per-letter rows**, not only averages | Per-letter rows allow ad-by-ad pairing, which is what measures noise; and a measure thought of later can be worked out for every old checkpoint at no cost |
| D4 | Checkpoints are made **by hand**, once per finished experiment, **failed ones included**, marked `kept: true/false` | Every run would bury the useful ones; failures stay on record so ideas aren't re-tried and the totals stay honest |
| D5 | **A new checkpoint's parent is the last kept one, never a failed one** | If E1 fails, E2 is measured against what is actually in use |
| D6 | Every difference carries a **noise label**; until E3's control arm exists the label says "provisional, judge-only" | Without it the tool reports chance as a trend |
| D7 | Cost is shown as recorded **and** recomputed at today's `PRICES` | A price change must never look like an optimisation |
| D8 | `compare` takes two checkpoint ids; with one id it compares with that checkpoint's parent | What the owner asked: pick the two versions to compare |
| D9 | The code lives in a new `scripts/eval_checkpoints.py` (pure functions: build, pair, stats, render), with thin `letter_lab.py` subcommands | `letter_lab.py` is already 2,490 lines, and pure functions are easy to test |
| D10 | Two separate fields, set by the owner at checkpoint time. **`verdict`** (pass / fail): did the experiment meet the rule written in the plan **before** it ran. **`kept`** (yes / no): is the change adopted, a free judgement that can differ from the verdict (pass but not kept, fail but kept). The report shows the rule's text and the numbers it needs. The tool never decides either one | Writing the rule first stops the goalposts moving after the results are seen; `kept` records the owner's real choice and the note says why |
| D11 | **Which judging.** Each `--judge` names one judging; a comma joins halves that cover different letters into one judging (`--judge pts-cal-2,pts-check-1`). **`--judge` given more than once = averaged**: each letter's points and problem counts are the mean over the judgings (§3.5) | Choosing the judging is needed every time; joining halves is needed for the day-one noise check; averaging is the fallback in plan §9.3 if noise is too big |
| D12 | **Points are re-added, not re-judged.** For each judging, the tool runs the saved problem list through `letter_points.score(issues)` and checks it equals the saved `points`. A mismatch stops the checkpoint, writes nothing, and names the letter. No letter is rewritten and no judge is run | If the scorer changed, the same problems add up differently, and comparing that letter with an older checkpoint would be unfair |

---

## 1. What a checkpoint holds

One JSON object per line. Names are fixed; a test checks the full set of allowed keys.

```jsonc
{
  "id": "c2",                       // c0, c1, c2 ... in order of creation
  "created": "2026-12-02T10:14:00",
  "kind": "letters",                // "letters" (a letter run) or "measure" (no letters, e.g. E1, E6)
  "experiment": "E3",               // id from the optimisation map, or "baseline"
  "note": "should-address tier, arm B",
  "parent": "c0",                   // last kept checkpoint unless --parent says otherwise
  "kept": true,
  "verdict": "pass",                // pass | fail | none (baseline, A/A)
  "rule": "Missed rows half of arm A's or fewer; ...",   // short text pasted from the plan
  "aa_of": null,                    // set on an A/A checkpoint: "this repeats <id> with no change"
  "replaces": null,                 // set on a correction

  // Must match for two checkpoints to be compared (else compare refuses)
  "comparable": {
    "set": "v2", "split": "dev",
    "judge": "v1",                  // name from judge-v1.json, checked against every letter's judge block
    "scorer_sha256": "…",           // of scripts/letter_points.py
    "judgings": 1                   // judgings averaged per letter (D11); 1 = a single judging
  },
  // Shown in the report as "what changed"; allowed to differ
  "settings": {
    "run": "dev-agent-v1", "judge_tags": [["v1-dev"]],   // one inner list per judging; halves joined inside it
    "judge_backends": ["claude-cli"],                     // one per judging; "vertex" spends Gemini trial credits (§3.6)
    "commit": "21c8405", "engine": "agent",
    "models": {"strong": "gemini-3.1-pro-preview", "mid": "…", "small": "…"},
    "thinking": {"strong": "high", "mid": "medium", "small": "low"},
    "prices_seen": {"gemini-3.1-pro-preview": [2.0, 12.0, 0.2], "…": []},
    "given_by_hand": ["commit", "thinking"]               // typed in with --commit / --thinking (D13); [] when run.json saved them
  },

  "totals": { "letters": 17, "cost_usd": 4.39, "seconds": 8279, "points_mean": 7.13 },

  "letters": [                      // one per ad, allowlisted fields only
    {
      "key": "seek-95191929", "relevant": true,
      "status": "done", "clean": true, "drafts": 1,
      "seconds": 176.2,
      "cost_usd": 0.1609,
      "by_task": {                  // from llm_usage, so cost can be recomputed at any price table
        "generate_letter": {"model": "gemini-3.1-pro-preview", "calls": 1,
                            "in": 6829, "cached": 0, "out": 999, "think": 7747, "cost_usd": 0.118, "ms": 93000}
      },
      "words": 326, "stock_hits": 11,
      "points": 8.65,               // the mean when judgings > 1
      "points_each": [8.65],        // one per judging, in --judge order
      "problems": {                 // every letter_points metric, always present, zeros included (means when judgings > 1)
        "missed_row":   {"n": 0, "low": 0, "high": 0, "lost": 0.0},
        "generic":      {"n": 1, "low": 0, "high": 1, "lost": 0.6}
      },
      "missed_row_ids": [],         // row ids only, e.g. ["R10", "R11"]; with judgings > 1, every id any judging charged
      "checklist": {"D2a": "N", "D4b": "N"}
    }
  ],

  "measures": {}                    // kind "measure": named numbers, e.g. {"G": 5, "U": 2, "S": 162, "cost_usd": 1.31}
}
```

A `measure` checkpoint (E1, E6 and similar) has no `letters`; `measures` holds the experiment's own
numbers, given on the command line. Its comparison only shows the measures both sides share.

## 2. Commands

All under `letter_lab.py`, all `--set v2`.

| Command | What it does |
|---|---|
| `checkpoint <run> --judge <tag>[,<tag>] [--judge <tag> …] --experiment E3 --note "…" [--parent cN] [--kept/--not-kept] [--verdict pass\|fail] [--rule "…"] [--aa-of cN] [--dry-run]` | Reads the run and its judging, checks them (§3.1), prints a summary and appends one line. `--dry-run` prints the line and writes nothing |
| `checkpoint --measure --experiment E1 --note "…" --value G=5 --value U=2 --value cost_usd=1.31 …` | A `measure` checkpoint, no run |
| `checkpoints` | Lists every checkpoint: id, experiment, kept, parent, points, $ / letter, s / letter, note |
| `compare <a> [<b>]` | Writes `evals/results/v2/compare-<a>-<b>.md` and prints its headline. With one id, compares with its parent |
| `history` | Writes `evals/results/v2/history.md` |

Example:

```
python scripts/letter_lab.py checkpoints --set v2
python scripts/letter_lab.py compare --set v2 c0 c2
python scripts/letter_lab.py compare --set v2 c2          # c2 against its parent

# One judging, two halves joined
python scripts/letter_lab.py checkpoint --set v2 dev-agent-v1 --judge pts-cal-2,pts-check-1 --aa-of c0 --experiment baseline
# Two judgings averaged
python scripts/letter_lab.py checkpoint --set v2 <run> --judge <tag-1> --judge <tag-2> --experiment E3 --note "…"
```

## 3. Rules the code enforces

### 3.1 When `checkpoint` refuses

- The run has no `run.json`, or the judging tag has no `judge-<tag>.json`.
- A letter's `judge` block differs from the named frozen judge (`judge-v1.json`).
- Two tags joined inside one judging (comma) both cover the same ad.
- With more than one `--judge`: a letter that is missing from any of the judgings (an average must
  be over the same judgings for every letter).
- Recomputed points differ from the stored `points` in any judging (D12).
- `--parent` names a checkpoint that is not kept (D5), unless it is an A/A checkpoint, whose parent
  is the checkpoint it repeats.
- The split is `test` and `--allow-test` is not given.
- A row would hold a key outside the allowlist (the privacy guard, also tested).

### 3.2 Pairing in `compare`

- `comparable` must match exactly, or `compare` refuses and says which field differs.
- Letters are paired by ad key. Ads present on one side only are listed and left out of every
  paired number. A letter that failed (no draft) is not paired.
- Every number is given for **all paired ads** and for the **relevant ads** beside it.

### 3.3 The noise label

The noise reference for a measure is an **A/A pair**: two checkpoints with the same settings, where
one has `aa_of` pointing at the other. From its paired per-ad differences, `s` = their standard
deviation, and

> **T = 2 × s ÷ √n**, where n is the number of paired ads in the comparison being labelled.

A mean difference larger than T is labelled **beyond noise**, otherwise **within noise**. This is
the rule in plan §9.0.

| Reference available | Label shown |
|---|---|
| Writer + judge A/A (E3 arm A against c0, once it exists) | `beyond noise` / `within noise` |
| Only the judge-only A/A (c0 against c1, the re-judging below) | the same, plus **"provisional: judge noise only, writer noise not included"** |
| None for this measure (cost and time until E3) | `no noise reference yet` |

**Which kind of A/A a pair is** (D14): the A/A checkpoint's `settings.run` is the same as the
checkpoint it repeats = **judge-only** (the same letters judged again); a different run =
**writer + judge** (new letters written with the same settings, then judged).

**What a judge-only A/A can be a reference for** (D14): only what the judge produces, i.e. points
and every problem measure. Its letters are the same, so their cost, time and drafts are the same
too: every difference is 0, `s` = 0 and T = 0, which would call any cost change "beyond noise".
Cost, time and drafts therefore show **no noise reference yet** until a writer + judge A/A exists.

`compare` uses the most recent writer + judge A/A pair whose `comparable` matches, else the
judge-only one (for judge measures only). The report says which pair it used. For averaged
checkpoints, see §3.5.

The judge-only reference is built on day one from files that already exist:
`checkpoint dev-agent-v1 --judge pts-cal-2,pts-check-1 --aa-of c0 --experiment baseline`. Its
comparison with c0 must reproduce plan §9.0's numbers (paired SD 0.65, mean moved 0.19), which is
the check that the tool is right.

### 3.4 Cost at one price table

Each checkpoint keeps the cost it recorded. `compare` and `history` also recompute every letter's
cost from `by_task` tokens at today's `client.PRICES` (thinking billed as output, cached input at
the cached price), and show both. The headline uses today's prices. If `PRICES` changed between
two checkpoints, the report says so.

### 3.5 Averaged judgings (D11)

- Each judging is checked on its own first (judge version, D12 re-add), then averaged per letter:
  `points`, and each problem's `n`, `low`, `high` and `lost`. `points_each` keeps every judging's
  score, so the spread between them is visible in the per-letter table.
- `judgings` is part of `comparable`, so a 2-judging checkpoint is only compared with another
  2-judging checkpoint. Comparing an average with a single judging would mix two noise levels.
  To compare with c0, judge c0's letters a second time and make an averaged c0.
- Averaging shrinks the judge's part of the noise. The noise reference is the A/A pair with the
  same `judgings`; if there is none yet, the single-judging reference is used and labelled
  **"conservative: reference from single judgings"** (its T is larger, so it can only under-claim).
- Cost: judging twice costs a second judge panel (about $18.58 notional on the owner's Claude
  subscription for 17 letters, plan §9.3). The tool makes no judge call itself.

### 3.6 Judge backend and the Gemini trial deadline

- **DEADLINE: any judging through Vertex AI (Claude on Vertex) must be finished before the Gemini
  trial ends on 2026-12-30.** It would spend the trial's credits, which stop then. Judging through
  the Claude CLI (the owner's subscription, judge v1's backend) has no deadline.
- Today only the `claude-cli` backend exists: `grading_panel.py` has `BACKENDS = {"claude-cli": …}`
  with Vertex marked "stage 8 step 9", not built. Judge v1 is frozen with `backend: claude-cli`, and
  `v2-run --judge v1` refuses any difference, so a Vertex judging would be a **new frozen judge**
  (its own name, its own calibration) whose scores are not comparable with v1's; `compare` refuses
  across judges (§3.2).
- Every checkpoint records each judging's backend in `settings.judge_backends`. The compare and
  history reports show it beside the judge name, and a `vertex` judging gets the line
  **"judged on Vertex: Gemini trial credits, ends 2026-12-30"**.

## 4. The compare report

`evals/results/v2/compare-c0-c2.md`, in this order:

1. **Headline.** One table: points, $ / letter (today's prices), s / letter, for A, B and the
   difference, with its noise label and ads up / same / down. All 17 and relevant 10.
2. **What changed.** Experiment, note, verdict, rule text; and every `settings` field that differs
   (engine, models, thinking, commit, prices).
3. **Problems per letter.** For each `letter_points` metric: instances per letter and points lost
   per letter, A, B, difference, noise label, ads better / worse. This is where "missed rows −0.24 a
   letter" appears.
4. **Cost and time by step.** Per task: $ / letter and s / letter, A, B, difference (the cost-report
   table, side by side).
5. **Per letter.** One row per ad: relevant, drafts A → B, points A → B (Δ), $ A → B, s A → B, the
   problems that changed (`missed_row +1, generic −1`). Sorted by points Δ, worst first, so the
   letter that fell most is at the top.
6. **Left out.** Ads present on one side only, failed letters.
7. **How to read this.** Three lines: what T is, which A/A pair gave it, and "no conclusion from
   one letter".

The terminal prints section 1 and the path.

## 5. The history report

`evals/results/v2/history.md`:

1. **Kept chain.** One row per kept checkpoint, oldest first: id, date, experiment, note, points,
   $ / letter, s / letter, missed rows / letter, untrue / letter; each with its change from the
   parent (with noise label) and from c0.
2. **Trends.** Three GitHub Mermaid `xychart-beta` line charts over the kept chain: points, $ /
   letter (today's prices), s / letter. In VS Code's preview they show as code unless a Mermaid
   extension is installed; the table in 1 has the same numbers.
3. **Not kept.** Failed experiments, each with its parent and its headline differences.
4. **Measure checkpoints.** E1-style results, one table per experiment.
5. **Noise references.** The A/A pairs and their `s` per measure.

---

## 6. Build steps

Each step ends with the full test suite passing.

| Step | What | Paid calls |
|---|---|---|
| 0 | Record the baseline test count (`python -m pytest -q`) here before any code. **DONE 2026-11-25: 2041 passed, 0 failed, 0 skipped** (commit `650876c`, `.venv` Python 3.11.9, 82 s; the system Python gives the same 2041) | none |
| 1 | `scripts/eval_checkpoints.py`: build a checkpoint from a run + judging (allowlist, checks of §3.1), append / load the JSONL, parent rule. **BUILT 2026-11-25**: 36 tests, suite 2077 passed | none |
| 2 | Pairing and stats: paired differences, ads up / down, A/A `s`, T and the labels, cost at today's prices. **BUILT 2026-11-25**: 22 tests (58 in the file), suite 2099 passed; 2 existing tests changed (Decision log) | none |
| 3 | `compare` report and its terminal headline. **BUILT 2026-11-25**: 12 tests (70 in the file), suite 2111 passed; no existing test changed | none |
| 4 | `history` report with the Mermaid charts; `checkpoints` list. **BUILT 2026-11-25**: 8 tests (78 in the file), suite 2119 passed; no existing test changed | none |
| 5 | `letter_lab.py` subcommands wired to 1–4; `run` saves `models` / `thinking` / `commit` in `run.json`, `checkpoint --commit / --thinking` for older runs (Decision log D13). **BUILT 2026-11-25**: 19 tests (18 in `test_eval_checkpoints.py`, 96 there; 1 in `test_letter_lab_sets.py`), suite 2138 passed; no existing test changed | none |
| 6 | Real data: write **c0** (`dev-agent-v1`, `v1-dev`) and **c1** (A/A of c0: the same letters re-judged, `pts-cal-2` + `pts-check-1`); `compare c0 c1` must give paired SD ≈ 0.65 and mean move ≈ 0.19; c0's cost must match the cost table ($0.258 a letter); check the JSONL by eye for any text. **BUILT 2026-11-25**: c0 and c1 written; `compare c0 c1`: 17 pairs, paired SD 0.6467, mean move +0.1912 (T 0.31; relevant 10: +0.23, T 0.41); c0 $0.2581 a letter, $4.387 total, as recorded and at today's prices alike; no text in the JSONL or the two reports; suite 2138 passed, no code or test changed (Decision log) | none |
| 7 | Docs: T1 row in the map set to done with a link here; one line in CLAUDE.md's current task; this plan's Decision log. **DONE 2026-11-25**: suite 2138 passed, no code or test changed | none |

## 7. Tests (new file `tests/test_eval_checkpoints.py`, fixtures only, no real data)

- **Privacy.** A fixture run and judging with a planted marker string in every text field
  (`title`, `company`, `issues`, `note`, `problems`, `claims`, `quote_problems`, `checklist_doubts`,
  the letter): the marker appears nowhere in the written line, and every key is in the allowlist.
- **Append-only.** After a second `checkpoint`, the first line is byte-for-byte unchanged; ids run
  c0, c1, c2; `replaces` is kept.
- **Parent rule.** Default parent is the last kept; an explicit non-kept parent is refused; an A/A
  checkpoint's parent is its `aa_of`.
- **Refusals.** Judge block differing from the frozen judge; overlapping joined halves; points
  mismatch in any judging; `test` split without `--allow-test`.
- **Averaging.** Two judgings give the per-letter mean of points and of every problem field, with
  `points_each` in `--judge` order and `judgings: 2`; a letter missing from one judging is refused;
  `compare` refuses a 1-judging against a 2-judging checkpoint; the "conservative" noise label
  appears when only a single-judging reference exists.
- **Pairing.** Ads on one side only are left out and listed; failed letters not paired; relevant
  subset correct.
- **Stats.** Mean difference, ads up / same / down, `s`, T = 2s/√n and each label, on hand-worked
  numbers.
- **Noise reference choice.** Writer + judge pair preferred over judge-only; "provisional" text when
  only judge-only exists; "no noise reference yet" when none. An A/A with the same run is
  judge-only and one with a different run is writer + judge; a judge-only A/A gives no reference
  for cost, time or drafts ("no noise reference yet" for those, never T = 0).
- **Run settings (D13).** `run` saves `models` / `thinking` / `commit` in `run.json` (dirty tree
  marked); a checkpoint copies them; `--commit` / `--thinking` fill them for an older run and
  `given_by_hand` names them; neither = unknown, never today's values.
- **Comparability.** `compare` refuses on a `comparable` mismatch and names the field; differing
  `settings` are listed, not refused.
- **Prices.** Recomputed cost at a changed `PRICES` fixture; the report notes the change.
- **Measure checkpoints.** Stored and compared on shared measures only.
- **Backend label.** `judge_backends` is recorded per judging; a `vertex` judging shows the
  2026-12-30 trial-credits line in both reports, a `claude-cli` one does not.
- **Reports.** `compare` and `history` render on fixtures (key headings and numbers present,
  Mermaid block well formed); `--dry-run` writes nothing.

## Done when

- [x] The baseline test count is recorded in this plan before any code (step 0): 2041 passed.
- [x] The **entire** test suite runs and passes after the code is added: 2138 passed.
- [x] `tests/test_eval_checkpoints.py` covers every bullet in §7 (the run-settings bullet also in
      `tests/test_letter_lab_sets.py`).
- [x] No existing test is weakened: none deleted, skipped, xfailed or loosened; any changed test is
      listed here with its reason (2, step 2's Decision log entry).
- [x] c0 and its judge-only A/A checkpoint are in `evals/results/v2/checkpoints.jsonl`, and the
      comparison reproduces plan §9.0's noise numbers and the cost table's $0.258.
- [ ] `compare-c0-c1.md` and `history.md` are written and read well (manual check 1–2).
- [x] `git diff --cached` checked for personal data before the commit: numbers, ad keys, row ids
      and settings only (step 6's and step 7's commits).
- [x] The map's T1 row, CLAUDE.md and this plan's Decision log are updated.

## Manual checks (owner, at the end)

1. Open `evals/results/v2/compare-c0-c1.md` on GitHub: is the headline clear, and does the
   per-letter table answer "which letters moved and why"?
2. Open `evals/results/v2/history.md` on GitHub: do the Mermaid charts render?
3. Run `letter_lab.py checkpoints --set v2`, `compare --set v2 c1` (one id: against its parent),
   `compare --set v2 c0 c1`, and the refusal `compare --set v2 c0` (c0 has no parent) yourself.
   Then `git status --short evals/results/v2` must print **nothing**: every file the tool writes
   is in that folder, so empty = `compare` rebuilt its report with identical content and no
   checkpoint was added. Any line = don't commit; a changed `checkpoints.jsonl` means a checkpoint
   was recorded by mistake (restore it with git). Never run `checkpoint` (singular) without
   `--dry-run` here: it appends a permanent line. (Changed 2026-11-25, owner: the first wording,
   `compare --set v2 c0`, is a refusal, and a whole-tree "clean" fails on any unrelated
   uncommitted work.)

---

## Decision log

- **2026-11-25. Plan drafted (v0.1)** from the owner's discussion: committed append-only JSONL
  (D1), generated markdown reports (D2), per-letter rows (D3), manual checkpoints including failed
  experiments (D4), parent = last kept (D5), noise labels (D6), cost at today's prices (D7),
  `compare` with one or two ids (D8). O1–O4 are this draft's defaults, waiting for the owner.
- **2026-11-25. Approved (v1.0).** The owner settled O1–O4 as D9–D12: code in
  `scripts/eval_checkpoints.py` (D9); `verdict` (against the rule written first) and `kept` (the
  owner's adoption choice) stay two separate fields (D10); judgings can be joined from halves and,
  new in v1.0, averaged when `--judge` is given more than once, with `judgings` part of
  `comparable` (D11, §3.5); points are re-added by the scorer and checked, never re-judged (D12).
  Judge v1 runs on the owner's Claude subscription, not on Gemini.
- **2026-11-25. Vertex deadline added (owner).** Claude can also be run through Vertex AI, which
  spends the Gemini trial's credits. Any Vertex judging must be done before the trial ends on
  2026-12-30; checkpoints record each judging's backend and the reports flag a Vertex one (§3.6).
  The Vertex backend is not built yet, and would be a new frozen judge, not v1. The same deadline
  is noted in CLAUDE.md's LLM layer.
- **2026-11-25. Step 1 built** (`scripts/eval_checkpoints.py`, `tests/test_eval_checkpoints.py`, 36
  tests). Checked against the real files first, read-only: `letter_points.score(issues)` re-adds
  to the stored `points` (and `by_metric`) for every letter of `v1-dev`, `pts-cal-2` and
  `pts-check-1`; every judge block equals `judge-v1.json`'s `version`; `llm_usage` summed per
  `letter_run_id` equals `run.json`'s `cost_usd` per letter ($4.387, $0.2581 a letter); the
  plan's §9.0 numbers come out of the two judgings (paired SD 0.647, mean move 0.191, 17 pairs).
  Choices the plan did not spell out:
  - **"Failed letter" means no draft** (`error` in the result, or 0 drafts), not `status`.
    `dev-agent-v1`'s one `failed` letter (a 429 in `check_claims`) has 2 drafts and was judged,
    so it is paired; that is how §9.0 got 17 pairs.
  - **More refusals than §3.1 lists**, all on the same "don't let a wrong judging in" line: a letter
    with a draft that no judging covers (also with one judging, so a half can't pass as a whole);
    a judge record whose `run` is another run, or an ad that is not in the run; the scorer's
    per-metric levels differing from the stored `by_metric` (D12's check, one level finer); one
    task using two models in one letter (a row keeps one model per task); and judgings that
    disagree on the letter's `words` / `stock_hits` (code-set, so they judged different letters).
  - **The test-split refusal also covers an `--only` run** whose ads are on the test side.
  - **Privacy guard checks values as well as keys**: every short value (ad key, status, model,
    tag, row id, checklist id) must be a plain name with no spaces, and checklist answers Y / N /
    mixed. Free text is allowed only in `note` and `rule`, which the owner types.
  - **Averaged checklists**: an item's answer when every judging agrees, else `mixed` (a Y/N is
    not averaged). `missed_row_ids` are sorted by row number (R3 before R10).
  - **Parent rule**: the default parent is the last kept checkpoint *of the same kind* (a kept
    measure checkpoint like E1 must not become a letter run's parent), and an A/A checkpoint is
    never a default parent, even if marked kept (it repeats, it doesn't change anything).
  - **A pass / fail verdict needs `--rule`** (D10's "rule written first"); `none` doesn't.
- **2026-11-25. Run settings are saved by the run (owner decision, D13).** A run records only its
  strong model; the thinking levels and the commit are recorded nowhere, and what ran = the code's
  defaults at that commit + any `.env` override (`GEMINI_MODEL_*`, `GEMINI_THINKING_*`, gitignored)
  at that moment. Reading them when the checkpoint is made would record the wrong day's values
  (c0 is made a month after `dev-agent-v1`). Decided:
  1. `settings.models` comes from `llm_usage` per tier (logged at call time: a fact for old runs too).
  2. **The one approved exception to "no change to how `run` behaves"**: `letter_lab.py run` adds
     `models`, `thinking` (per tier, read the way `client.py` reads them) and `commit` (git HEAD,
     marked when the tree had uncommitted changes) to `run.json`. Additive fields only; nothing
     else `run` does changes. Built in step 5, with a test.
  3. For a run without them (c0), `checkpoint --commit <sha> --thinking tier=level,...` types them
     in, and the checkpoint marks them as given by hand. For `dev-agent-v1`: commit `b423842` (the
     last commit before the run, 16 min earlier; `client.py` unchanged since, so its defaults were
     low / medium / high), and the owner confirmed 2026-11-25 that `.env` has no
     `GEMINI_THINKING_*` line, so c0's thinking is `small=low, mid=medium, strong=high`.
  4. Neither saved nor given: stored as unknown, and the reports say so. Today's values are never
     recorded as the run's.
  5. **Per tier, not per step (owner, 2026-11-25).** `client.py` sets one level per tier, so a
     per-tier record is exact. If an experiment later needs different levels on two steps of one
     tier (E4 lowers `generate_letter`, and `GEMINI_THINKING_STRONG` also moves `revise_letter`),
     that experiment adds the per-step override and records the level per call in `llm_usage`;
     the checkpoint allowlist then gains that field on purpose. Not built now.
  6. **Hand-given settings are marked** in a new allowlisted field, `settings.given_by_hand`
     (a list naming `commit` and / or `thinking`; empty when `run.json` saved them). The reports
     show the mark beside the value. Added to the allowlist on purpose, with its test (step 2).
- **2026-11-25. The two kinds of A/A (owner, D14), §3.3.** An A/A checkpoint with the same
  `settings.run` as the one it repeats is judge-only; a different run is writer + judge. A
  judge-only A/A is a noise reference for judge measures only (points and problems): its letters
  are identical, so cost, time and drafts would get `s` = 0 and T = 0, calling every change
  real. Those show "no noise reference yet" until a writer + judge A/A exists (E3's control arm).
- **2026-11-25. eval.db is read through a read-only connection** (`sqlite3`, `mode=ro`), with the
  same join as `letter_lab._cost_rows` (`llm_usage.run_id` = `results[].letter_run_id`, using the
  run's exact ids: `letter_run_id` 62 sits between the run's ids but belongs to another run). The
  tool cannot write to it.
- **2026-11-25. Step 2 built** (pairing and stats in `scripts/eval_checkpoints.py`; 22 tests, suite
  2099 passed). `compare()` returns the numbers the report needs (step 3 only renders them).
  **Changed tests** (none deleted, skipped or loosened):
  - `test_allowlist_is_pinned`: `SETTINGS_KEYS` gains `given_by_hand` (D13, added on purpose here).
  - `test_an_aa_checkpoints_parent_is_the_one_it_repeats`: its A/A reused c0's judge tag, which the
    new refusal below blocks; it now uses a fresh tag and checks the same parent rule.
  Choices the plan did not spell out:
  - **Two more A/A refusals at checkpoint time.** An A/A must have the same `comparable` as the
    checkpoint it repeats; and a judge-only A/A (same run) must use judge tags the original did not,
    else every judge difference is 0 and the noise looks nil.
  - **An A/A that never moved on a measure (s = 0) gives no reference** for it, shown as "no noise
    reference yet (c1 vs c0 never moved on this measure)", never T = 0. The plan says this for cost
    and time under a judge-only A/A (D14); the same trap exists for a judge measure that happened
    not to move in 17 letters (most problem types in c1 vs c0), and for a writer measure that moved
    by the same amount on every letter. Owner may overrule.
  - **The relevant-10 label** uses the A/A's `s` over all its pairs and the comparison's relevant n
    (T = 2s/√10): the A/A's own relevant subset is too small to give a steady `s`.
  - **Relevant** = relevant on both sides; ads whose label differs between the two are listed.
  - **Which reference.** Judge measures: writer + judge before judge-only, and within each the same
    `judgings` before a single-judging one ("conservative"). A reference with more judgings than
    the comparison is never used (it would under-state the noise and over-claim). Writer measures:
    a writer + judge A/A with any `judgings`, never marked conservative (cost does not depend on how
    often the letters were judged). The most recent matching pair wins. An A/A that a later
    checkpoint `replaces` is skipped.
  - **Cost at today's prices** for a model today's `PRICES` doesn't list is unknown (left out of
    that measure's pairs), never guessed. The per-task table (§4.4) is at today's prices.
  - **Measures compared:** points; cost at today's prices and as recorded; seconds; drafts; and for
    every `letter_points` metric, instances (`n`) and points lost (`lost`) per letter.
- **2026-11-25. "No noise reference yet" for a measure that never moved: confirmed (owner).** In
  the real repeat check (`v1-dev` against `pts-cal-2` + `pts-check-1`, 17 letters), points lost
  moved for missed rows (3 letters, wobble 0.561 a letter), untrue (2, 0.332) and mix-up (1, 0.121);
  weak example, no personal reason, generic, stock phrasing and too long never moved (wobble 0);
  total points 0.647; all problem types pooled 0.231. Parked until the whole plan is built: more
  repeat runs to learn which measures move (writer + judge repeats, i.e. E3's control arm, on
  Gemini credits, judged by v1 on the Claude CLI; a Vertex judging would be a new judge, §3.6),
  and pooling several repeat checks into one wobble figure instead of using only the latest.
- **2026-11-25. Step 3 built** (the compare report, `render_compare` / `render_headline` in
  `scripts/eval_checkpoints.py`; 12 tests, suite 2111 passed, no existing test changed). The
  terminal headline is the report's section 1, word for word. Choices the plan did not spell out:
  - **Provisional / conservative as marks.** A label cell reads `within noise (T 0.31)†`; the mark
    is explained once under the table (with the reference pair), not repeated in every row.
  - **Headline rows:** points, $ / letter at today's prices, $ / letter as recorded, seconds /
    letter, each for all ads and relevant ads. Drafts are in the per-letter table.
  - **Problems table:** the noise label is on points lost (what the score feels); instances are
    shown beside it. "Better" = fewer points lost on that letter.
  - **Per letter, "what changed":** problem instances B − A and the missed row ids gained / lost
    (`missed_row +1; missed rows +R3 -R12`); averaged checkpoints show each judging's points in
    brackets.
  - **Left out** also lists letters whose cost at today's prices is unknown (a model today's table
    doesn't list), so a missing price is never silent.
  - **Settings table** shows every setting on both sides with a "differs" mark (not only the
    differing ones), so *unknown* and *(typed in)* values are visible; the per-task table has an
    all-steps total row. Numbers: $ to 4 places, seconds whole, the rest to 2.
- **2026-11-25. Step 4 built** (`render_history`, `render_list`, `summary`, `mermaid_chart` in
  `scripts/eval_checkpoints.py`; 8 tests, suite 2119 passed, no existing test changed). Choices the
  plan did not spell out:
  - **The kept chain** = kept letters checkpoints, oldest first, without A/A checkpoints (they
    repeat, they change nothing) and without one a later checkpoint `replaces` (the correction
    stands in its place). Its changes are two tables, from the parent and from the first kept
    checkpoint, each cell "difference, noise label" over the ads both share; a pair `compare` would
    refuse says "not comparable (the field)".
  - **Per-checkpoint means:** cost and time over every letter (a failed letter cost money too),
    points and problems over the judged letters. This is how c0 comes to $0.258 a letter.
  - **Charts:** a point without a value (cost unknown at today's prices) is left out of the chart;
    the points chart runs 0 to 10, the others from 0 to a round ceiling a little above the top value.
  - **Noise references:** one column per repeat check, one row per measure; a judge-only check's
    cost / time / drafts read "not a reference (same letters)", an s of 0 "0 (never moved)".
  - **`checkpoints` list:** a measure checkpoint's numbers go in its note column; a replaced
    checkpoint is marked "(replaced by cN)".
- **2026-11-25. Step 5 built** (`letter_lab.py checkpoint / checkpoints / compare / history`, and
  `run` saving its settings; 19 tests, suite 2138 passed, no existing test changed). Choices the
  plan did not spell out:
  - **`run` (D13, the one approved change):** `run.json` gains `models` and `thinking` per tier
    (read with `client.model_for` / `client._thinking_level`, exactly as the calls read them) and
    `commit` (`git rev-parse --short HEAD`, `-dirty` when `git status --porcelain` lists anything,
    untracked files included: a new module the run imports is part of what ran; unknown if git
    can't say). Nothing else `run` does or prints changed.
  - **A resumed run** keeps the values it first ran with; a value that changed in between is
    recorded as both (`high+low`, `abc1234+def5678`). A run first written before these fields
    existed stays unsaved on resume (its earlier letters' settings are unknown, D13.4), so
    `--commit` / `--thinking` can still type them in.
  - **`checkpoint`:** `--kept` or `--not-kept` is required (D4: every checkpoint is marked);
    `--frozen-judge` defaults to `v1` (`evals/results/v2/judge-v1.json`); typing `--commit` /
    `--thinking` for a run whose `run.json` saved them is refused; every refusal prints "Refused,
    nothing written". The four commands refuse without `--set v2`.
  - **A measure checkpoint's commit** defaults to today's HEAD (a measure is recorded right after
    it is measured; no run file holds an older one); `--commit` overrides it. Owner may overrule.
  - **eval.db** is opened with `mode=ro` and queried for the run's exact `letter_run_id`s; a test
    checks the file is byte-for-byte unchanged after a checkpoint.
  - **The end-to-end `run` test** (stub model, no network) puts its working folder in the
    gitignored `evals/work/_pytest-<id>/` and deletes it afterwards, because `run` prints its paths
    relative to the repo; changing that print would have changed `run`.
- **2026-11-25. Step 6 built: c0 and c1 on the real files** (no code or test changed; no paid call;
  eval.db read-only). Commands, both with `--commit b423842 --thinking small=low,mid=medium,strong=high
  --set v2` (D13.3):
  - **c0**: `checkpoint dev-agent-v1 --judge v1-dev --experiment baseline --kept --note "dev
    baseline, agent engine, judge v1"`. 17 letters, all judged; points 7.13 (relevant 10: 7.15).
  - **c1**: `checkpoint dev-agent-v1 --judge pts-cal-2,pts-check-1 --experiment baseline --not-kept
    --aa-of c0 --note "repeat check of c0: same letters, judged again"`. Points 7.32. The two halves
    are joined with a **comma** (one judging, §3.3). The brief for this step wrote them as two
    `--judge` flags, which means *average two judgings* (D11); the tool refused it, correctly, because
    each half covers different letters (pts-cal-2 has 8, pts-check-1 the other 9), so no letter is in
    both. The comma form is the plan's own command.
  - **`compare c0 c1`** reproduces plan §9.0: 17 pairs, paired SD **0.6467**, mean move **+0.1912**
    (5 letters up, 11 same, 1 down); T = 2 × 0.6467 ÷ √17 = 0.31, so the move is within noise, as an
    A/A must be. Relevant 10: +0.23, T 0.41 (the all-17 SD with n = 10). Problem types: missed rows
    wobble 0.56 a letter, untrue 0.33, mix-up 0.12; the other five never moved (as the earlier entry).
  - **c0's cost = the cost table**: $0.2581 a letter, $4.387 total. `cost-dev-agent-v1.md` sums
    `llm_usage.cost_usd`, so its figure is the **as recorded** one; at today's prices it is the same,
    because every price in c0's `prices_seen` equals today's `client.PRICES`. They will part when the
    Flash prices double on 2027-01-01.
  - **Privacy check**: every string value in the JSONL is an id, ad key, row id, checklist Y/N, model,
    setting, tag, or one of the two typed notes; a search of the JSONL, `compare-c0-c1.md` and
    `history.md` for every run.json title and company and every judge quote (767 texts, also as
    40-character windows) found only three problem-type names (`no_personal_reason`,
    `stock_phrasing`, `weak_example`), which the judge's `issues` list carries as the issue type.
- **2026-11-25. Step 7 done: the plan is finished** (docs only; suite 2138 passed). The map's T1 row
  is `done` with c0's numbers and the judge noise T; CLAUDE.md's eval-set entry has one line on how
  to make a checkpoint and the reports (history stays here and in PROGRESS.md, CLAUDE.md stays one
  file). "Done when" ticked except the reports reading well, which is the owner's manual checks 1–2.
  **Next ideas, parked (not built):** revisit the "no noise reference yet" label for a measure the
  repeat check never moved; writer + judge repeat runs (E3's control arm), which give cost, time and
  drafts a noise reference; pooling several repeat checks into one noise figure instead of using
  only the latest.
- **2026-11-25. Manual checks, first findings (owner).** Check 2: the history charts draw, but with
  one kept checkpoint (c0) they show nothing, because a Mermaid line chart has no dot per point and
  one point makes no line. Left as it is (owner): the second kept checkpoint gives a line. If the
  next one is not kept and it still bothers, bars (alone or with the line) fix it. Check 3:
  `compare --set v2 c0` is refused ("c0 has no parent"), correctly; the one-id form to try is
  `compare --set v2 c1`.
- **2026-11-25. `checkpoints` prints aligned columns in the terminal (owner).** The raw markdown
  table was hard to read in a terminal. The owner chose aligned columns with every column kept
  (over fewer columns, or a card per checkpoint); "Seconds / letter" is headed "Seconds" there, and
  Note stays last so only a long note wraps. `--markdown` prints the old table, byte-for-byte the
  same (checked against the previous code on the real file and the fixtures). Both come from one
  `list_rows`, so they always show the same cells. 2 new tests; suite 2140 passed. **Changed test**
  (not loosened): `test_repeat_check_then_compare_history_and_list` checked the command's markdown
  (`| c0 |`); it now checks the aligned default and the same markdown check on `--markdown`.
- **2026-11-25. `compare`'s terminal headline in aligned columns too (owner: "fix all these
  terminal tables").** The checkpoint tool's only other table printed to a terminal (`checkpoint`
  prints plain lines, `history` only "Wrote"; no other `letter_lab` command prints a table, and
  `report` / `cost-report` are not touched). `terminal_text` turns markdown lines into terminal
  text: each table in aligned columns (its `|---|` line dropped, `\|` back to `|`), headings without
  `#`, no `**` or backticks; it changes the layout only, never a word or a number. `render_headline`
  and the report file are unchanged (`compare-c0-c1.md` regenerates byte-for-byte), so the step 3
  rule "the terminal headline is the report's section 1" now reads "the same lines, as terminal
  text"; the test pinning it is unchanged. `checkpoints` shares the column padding (`align`).
  2 new tests; suite 2142 passed. **Changed test** (not loosened):
  `test_repeat_check_then_compare_history_and_list` checked the printed `## 1. Headline`; it now
  checks `1. Headline` and that no `##`, `|` or `**` is printed (the report file's `## 1. Headline`
  is still checked).
