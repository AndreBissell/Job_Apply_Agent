"""Draw the eval-set candidates from the tracker's tags (docs/eval-set-plan.md, stage 1 -> 2).

Every ad in the scarce scenarios (1, 2, 6); a seeded random sample from the large ones
(3, 4, 7). Writes evals/candidates/candidates.csv (gitignored: real ads) for the owner's
labels. The same seed and the same real.db give the same draw.

    python scripts/eval_draw.py            # refuses if the sheet exists (it holds your labels)
    python scripts/eval_draw.py --force    # overwrite it
"""
import contextlib
import csv
import io
import random
import runpy
import sqlite3
import sys
from pathlib import Path

SEED = 20261020
SAMPLE = {3: 10, 4: 8, 7: 6}  # about double each scenario's dev+test target
TRACKER = Path(__file__).with_name("eval_pool.py")
OUT = Path("evals/candidates/candidates.csv")

if OUT.exists() and "--force" not in sys.argv[1:]:
    sys.exit(f"{OUT} already exists and may hold your labels: pass --force to overwrite it")

with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(str(TRACKER))
rows, TARGET = g["rows"], g["TARGET"]

rng = random.Random(SEED)
picked = []
for sc in sorted(TARGET):
    group = sorted((r for r in rows if r[0] == sc), key=lambda r: r[2])  # by job id: stable input
    take = group if sc not in SAMPLE else rng.sample(group, min(SAMPLE[sc], len(group)))
    picked += sorted(take, key=lambda r: -r[1])

c = sqlite3.connect("file:real.db?mode=ro", uri=True)
out = OUT
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads the dashes
    w = csv.writer(f)
    w.writerow(["job_id", "scenario", "scenario_name", "tag_from", "score", "title", "company", "url",
                "notes", "would_apply (Y/N)", "scenario_ok (Y/N)", "comment"])
    for sc, score, jid, title, company, _stale, missing, via, how in picked:
        url = c.execute("select url from job_listings where id=?", (jid,)).fetchone()[0]
        tag_from = "guess" if how and how.startswith("?") else "run" if how else "code"
        notes = "; ".join(x for x in [
            how if how and not how.startswith("?") else "",
            ("missing: " + ", ".join(missing[:4])) if missing else "",
            ("listing-only: " + ", ".join(via[:3])) if via else "",
        ] if x)
        w.writerow([jid, sc, TARGET[sc][0], tag_from, score, title, company, url, notes, "", "", ""])

print(f"seed {SEED}: {len(picked)} candidates -> {out}")
for sc in sorted(TARGET):
    n = sum(1 for r in picked if r[0] == sc)
    print(f"  {sc}. {TARGET[sc][0]:37} {n:>3}  (need {TARGET[sc][1] + TARGET[sc][2]})")
