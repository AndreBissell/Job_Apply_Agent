# Experiment history (eval set v2)

Written by `letter_lab.py history --set v2` from `evals/results/v2/checkpoints.jsonl`. Don't edit it by hand: run the command again. $ are at today's prices (`client.PRICES`); differences are over the ads both checkpoints share, with their noise label (a `compare` report gives the detail).

## 1. Kept chain

Every kept checkpoint, oldest first: what is in use now is the last row.

| Checkpoint | Date | Experiment | Note | Judge | Letters | Points | $ / letter | Seconds / letter | Missed rows / letter | Untrue / letter |
|---|---|---|---|---|---|---|---|---|---|---|
| c0 | 2026-11-25 | baseline | dev baseline, agent engine, judge v1 | `v1` (claude-cli) | 17 | 7.13 | $0.2581 | 487 | 0.88 | 0.47 |

Change from the parent:

| Checkpoint | Parent | Points | $ / letter | Seconds / letter | Missed rows / letter | Untrue / letter |
|---|---|---|---|---|---|---|
| c0 | — | — | — | — | — | — |

Change from c0, the first kept checkpoint:

| Checkpoint | From | Points | $ / letter | Seconds / letter | Missed rows / letter | Untrue / letter |
|---|---|---|---|---|---|---|
| c0 | — | — | — | — | — | — |

## 2. Trends

The kept chain as three line charts (GitHub draws them; VS Code's preview shows the code unless a Mermaid extension is installed). The table above has the same numbers.

```mermaid
xychart-beta
    title "Points per letter"
    x-axis ["c0"]
    y-axis "Points (0-10)" 0 --> 10
    line [7.1265]
```

```mermaid
xychart-beta
    title "Cost per letter at today's prices"
    x-axis ["c0"]
    y-axis "USD" 0 --> 0.33
    line [0.2581]
```

```mermaid
xychart-beta
    title "Seconds per letter"
    x-axis ["c0"]
    y-axis "Seconds" 0 --> 610
    line [487.253]
```

## 3. Not kept

Experiments that were tried and not adopted, against their parent (failures stay on record, D4).

None yet.

## 4. Measure checkpoints

Experiments measured without writing letters (E1-style), one table per experiment.

None yet.

## 5. Noise references

Each repeat check (A/A): how much a letter's number moved between two checkpoints that should match (s, the standard deviation of the per-letter differences). `compare` uses the most recent one that fits (plan §3.3). *Never moved* = s is 0, which gives no reference.

| Measure | c1 vs c0 (judge-only, 17 letters, 1 judging) |
|---|---|
| Points (0-10) | 0.65 |
| $ / letter, today's prices | not a reference (same letters) |
| $ / letter, as recorded | not a reference (same letters) |
| Seconds / letter | not a reference (same letters) |
| Drafts / letter | not a reference (same letters) |
| untrue: instances / letter | 0.33 |
| untrue: points lost / letter | 0.33 |
| missed_row: instances / letter | 0.43 |
| missed_row: points lost / letter | 0.56 |
| mix_up: instances / letter | 0.24 |
| mix_up: points lost / letter | 0.12 |
| weak_example: instances / letter | 0 (never moved) |
| weak_example: points lost / letter | 0 (never moved) |
| no_personal_reason: instances / letter | 0 (never moved) |
| no_personal_reason: points lost / letter | 0 (never moved) |
| too_long: instances / letter | 0 (never moved) |
| too_long: points lost / letter | 0 (never moved) |
| generic: instances / letter | 0 (never moved) |
| generic: points lost / letter | 0 (never moved) |
| stock_phrasing: instances / letter | 0 (never moved) |
| stock_phrasing: points lost / letter | 0 (never moved) |
