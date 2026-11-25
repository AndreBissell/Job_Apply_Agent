# Experiment checkpoints and comparison: plan (branch `cost_perform_analysis`)

**Status:** v1.0, APPROVED by the owner 2026-11-25. Not built yet; step 0 is next.

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
    "prices_seen": {"gemini-3.1-pro-preview": [2.0, 12.0, 0.2], "…": []}
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

`compare` uses the most recent writer + judge A/A pair whose `comparable` matches, else the
judge-only one. The report says which pair it used. For averaged checkpoints, see §3.5.

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
| 0 | Record the baseline test count (`python -m pytest -q`) here before any code | none |
| 1 | `scripts/eval_checkpoints.py`: build a checkpoint from a run + judging (allowlist, checks of §3.1), append / load the JSONL, parent rule | none |
| 2 | Pairing and stats: paired differences, ads up / down, A/A `s`, T and the labels, cost at today's prices | none |
| 3 | `compare` report and its terminal headline | none |
| 4 | `history` report with the Mermaid charts; `checkpoints` list | none |
| 5 | `letter_lab.py` subcommands wired to 1–4 | none |
| 6 | Real data: write **c0** (`dev-agent-v1`, `v1-dev`) and **c1** (A/A of c0: the same letters re-judged, `pts-cal-2` + `pts-check-1`); `compare c0 c1` must give paired SD ≈ 0.65 and mean move ≈ 0.19; c0's cost must match the cost table ($0.258 a letter); check the JSONL by eye for any text | none |
| 7 | Docs: T1 row in the map set to done with a link here; one line in CLAUDE.md's current task; this plan's Decision log | none |

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
  only judge-only exists; "no noise reference yet" when none.
- **Comparability.** `compare` refuses on a `comparable` mismatch and names the field; differing
  `settings` are listed, not refused.
- **Prices.** Recomputed cost at a changed `PRICES` fixture; the report notes the change.
- **Measure checkpoints.** Stored and compared on shared measures only.
- **Backend label.** `judge_backends` is recorded per judging; a `vertex` judging shows the
  2026-12-30 trial-credits line in both reports, a `claude-cli` one does not.
- **Reports.** `compare` and `history` render on fixtures (key headings and numbers present,
  Mermaid block well formed); `--dry-run` writes nothing.

## Done when

- [ ] The baseline test count is recorded in this plan before any code (step 0).
- [ ] The **entire** test suite runs and passes after the code is added.
- [ ] `tests/test_eval_checkpoints.py` covers every bullet in §7.
- [ ] No existing test is weakened: none deleted, skipped, xfailed or loosened; any changed test is
      listed here with its reason.
- [ ] c0 and its judge-only A/A checkpoint are in `evals/results/v2/checkpoints.jsonl`, and the
      comparison reproduces plan §9.0's noise numbers and the cost table's $0.258.
- [ ] `compare-c0-c1.md` and `history.md` are written and read well (manual check 1–2).
- [ ] `git diff --cached` checked for personal data before the commit: numbers, ad keys, row ids
      and settings only.
- [ ] The map's T1 row, CLAUDE.md and this plan's Decision log are updated.

## Manual checks (owner, at the end)

1. Open `evals/results/v2/compare-c0-c1.md` on GitHub: is the headline clear, and does the
   per-letter table answer "which letters moved and why"?
2. Open `evals/results/v2/history.md` on GitHub: do the Mermaid charts render?
3. Run `letter_lab.py checkpoints --set v2` and `compare --set v2 c0` yourself.

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
