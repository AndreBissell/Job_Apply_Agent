"""Blind grading panel for cover-letter evals (procedure: evals/grading-panel.md).

Two runs' letters for the same jobs are put in one blind packet (random letter ids,
shuffled jobs, shuffled letter order, writers hidden). Two model graders grade every
letter against evals/grading-standard.md, an adjudicator rules on the cells they split,
and `merge` un-blinds the result into each run's folder.

    python scripts/grading_panel.py pack <tag> <run_a> <run_b>     # -> evals/panels/<tag>/packet.md
    #   ... graders write evals/panels/<tag>/grader-A.json and grader-B.json ...
    python scripts/grading_panel.py disagreements <tag>            # -> evals/panels/<tag>/disagreements.json
    #   ... adjudicator writes evals/panels/<tag>/adjudication.json ...
    python scripts/grading_panel.py merge <tag> [--fill] [--compare RUN=FILE ...]

`merge` writes evals/runs/<run>/grades-<tag>.csv for both runs. --fill also copies the
grades into BLANK rows of grades.csv (never over a row the user graded). --compare
reports cell agreement between this panel's grades for RUN and another grades file in
that run's folder (another panel's grades, or the user's grades.csv).

Everything under evals/panels/ is gitignored: packets hold ad text, the profile and
letters. The graders' notes quote letters, so they stay in the gitignored run folders.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
PANELS = EVALS / "panels"
RUNS = EVALS / "runs"
ITEMS = ("supported_musts_covered", "no_unsupported_claims", "specific_detail", "would_send")
PROFILE_ID = 1

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(EVALS / 'eval.db').as_posix()}")
os.environ.setdefault("APP_ENV", "test")
sys.path.insert(0, str(ROOT))


def _profile_block() -> str:
    from app.db import SessionLocal
    from app.llm.letter.runner import load_profile
    from app.llm.letter.state import ProfileIndex

    with SessionLocal() as db:
        profile = load_profile(db, PROFILE_ID)
        index = ProfileIndex(profile)
        facts = "\n".join(f"- {k}: {v}" for k, v in index.facts.items())
        return (
            "# The candidate's profile (the ONLY source of truth about the candidate)\n\n"
            f"Name: {profile.name}\n\nEligibility facts (not evidence, just facts):\n{facts}\n\n"
            f"Profile entries (pointer in brackets, then the text):\n\n{index.prompt_catalog()}\n"
        )


def _ad(key: str) -> str:
    d = json.loads((EVALS / "jobs" / f"{key}.json").read_text(encoding="utf-8"))
    head = (f"Title: {d['title']}\nCompany: {d.get('company') or '(not stated / recruiter)'}\n"
            f"Location: {d.get('location') or '-'}")
    return head + "\n\n" + d["raw_description"].strip()


def _letter(run: str, key: str) -> str:
    return (RUNS / run / "letters" / f"{key}.txt").read_text(encoding="utf-8").strip()


def _keys(run: str) -> list[str]:
    meta = json.loads((RUNS / run / "run.json").read_text(encoding="utf-8"))
    return [r["key"] for r in meta["results"] if "error" not in r]


def cmd_pack(args) -> int:
    keys = [k for k in _keys(args.run_a) if k in set(_keys(args.run_b))]
    out = PANELS / args.tag
    (out / "key").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    ids = rng.sample(range(1000, 9999), 2 * len(keys))
    rng.shuffle(keys)
    mapping: dict[str, dict] = {}
    pkt = [_profile_block(), "\n---\n"]
    for j, key in enumerate(keys, 1):
        letters = [(args.run_a, key), (args.run_b, key)]
        rng.shuffle(letters)
        pkt += [f"\n# JOB J{j:02d}\n", "## Job ad\n", "```", _ad(key), "```\n"]
        for run, k in letters:
            lid = f"L{ids.pop()}"
            mapping[lid] = {"run": run, "key": k, "job": f"J{j:02d}"}
            pkt += [f"## Letter {lid}\n", "```", _letter(run, k), "```\n"]
    (out / "packet.md").write_text("\n".join(pkt), encoding="utf-8")
    (out / "key" / "mapping.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"{len(mapping)} letters, {len(keys)} jobs -> {(out / 'packet.md').relative_to(ROOT)}")
    print("Graders must not be pointed at the key/ folder.")
    return 0


def _graded(tag: str, who: str) -> dict:
    return json.loads((PANELS / tag / f"grader-{who}.json").read_text(encoding="utf-8"))["letters"]


def _grade(cell: dict) -> str:
    return cell["grade"].strip().upper()


def cmd_disagreements(args) -> int:
    a, b = _graded(args.tag, "A"), _graded(args.tag, "B")
    if set(a) != set(b):
        print(f"Graders graded different letters: {len(a)} vs {len(b)}")
        return 1
    out, agree = [], {i: 0 for i in ITEMS}
    for lid in sorted(a):
        for item in ITEMS:
            if _grade(a[lid][item]) == _grade(b[lid][item]):
                agree[item] += 1
            else:
                out.append({"letter": lid, "job": a[lid]["job"], "item": item,
                            "grader_A": a[lid][item], "grader_B": b[lid][item]})
    (PANELS / args.tag / "disagreements.json").write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                                          encoding="utf-8")
    print(f"A/B agreement per item (of {len(a)}): {agree}")
    print(f"{len(out)} cells to adjudicate -> evals/panels/{args.tag}/disagreements.json")
    return 0


def _final(tag: str) -> dict[tuple[str, str], dict[str, tuple[str, str]]]:
    a, b = _graded(tag, "A"), _graded(tag, "B")
    adj = {}
    path = PANELS / tag / "adjudication.json"
    if path.exists():
        adj = {(r["letter"], r["item"]): r for r in json.loads(path.read_text(encoding="utf-8"))["rulings"]}
    mapping = json.loads((PANELS / tag / "key" / "mapping.json").read_text(encoding="utf-8"))
    final = {}
    for lid, m in mapping.items():
        row = {}
        for item in ITEMS:
            ga, gb = _grade(a[lid][item]), _grade(b[lid][item])
            if ga == gb:
                row[item] = (ga, a[lid][item]["reason"] if ga == "N" else "")
            else:
                r = adj.get((lid, item))
                if r is None:
                    raise SystemExit(f"{lid} {item} is split and has no adjudication: run the adjudicator first")
                row[item] = (_grade(r), r["reason"] if _grade(r) == "N" else "")
        final[(m["run"], m["key"])] = row
    return final


def _notes(g: dict[str, tuple[str, str]]) -> str:
    reasons = " | ".join(f"{i}: {g[i][1]}" for i in ITEMS if g[i][0] == "N")
    return "opus-blind" + (f": {reasons}" if reasons else "")


def cmd_merge(args) -> int:
    final = _final(args.tag)
    for run in sorted({r for r, _ in final}):
        meta = json.loads((RUNS / run / "run.json").read_text(encoding="utf-8"))
        rows = {k: v for (r, k), v in final.items() if r == run}
        with (RUNS / run / f"grades-{args.tag}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["key", "title", *ITEMS, "notes"])
            for res in meta["results"]:
                if res["key"] in rows:
                    g = rows[res["key"]]
                    w.writerow([res["key"], res["title"], *[g[i][0] for i in ITEMS], _notes(g)])
        y = {i: sum(v[i][0] == "Y" for v in rows.values()) for i in ITEMS}
        all4 = sum(all(v[i][0] == "Y" for i in ITEMS) for v in rows.values())
        print(f"{run}: " + ", ".join(f"{i} {n}/{len(rows)}" for i, n in y.items()) + f", all four {all4}/{len(rows)}")
        if args.fill:
            path = RUNS / run / "grades.csv"
            with path.open(encoding="utf-8") as fh:
                existing = list(csv.DictReader(fh))
            filled = 0
            for row in existing:
                g = rows.get(row["key"])
                if g is None or any((row.get(i) or "").strip() for i in ITEMS):
                    continue  # a row the user graded is never overwritten
                row.update({i: g[i][0] for i in ITEMS}, notes=_notes(g))
                filled += 1
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["key", "title", *ITEMS, "notes"])
                w.writeheader()
                w.writerows(existing)
            print(f"  filled {filled} blank row(s) of {path.relative_to(ROOT)}")
    for spec in args.compare or []:
        run, fname = spec.split("=", 1)
        with (RUNS / run / fname).open(encoding="utf-8") as fh:
            other = {r["key"]: r for r in csv.DictReader(fh)}
        per_item, cells = {}, 0
        for i in ITEMS:
            pairs = [(final[(run, k)][i][0], (other[k].get(i) or "").strip().upper())
                     for k in other if (run, k) in final and (other[k].get(i) or "").strip()]
            per_item[i] = f"{sum(a == b for a, b in pairs)}/{len(pairs)}"
            cells += len(pairs)
        print(f"agreement with {run}/{fname}: {per_item}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="blind packet of two runs' letters for the same jobs")
    p.add_argument("tag")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--seed", type=int, default=20261003)
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("disagreements", help="compare grader-A.json and grader-B.json")
    p.add_argument("tag")
    p.set_defaults(fn=cmd_disagreements)
    p = sub.add_parser("merge", help="final grades (agreed + adjudicated), un-blinded into each run folder")
    p.add_argument("tag")
    p.add_argument("--fill", action="store_true", help="also fill BLANK rows of each run's grades.csv")
    p.add_argument("--compare", nargs="+", metavar="RUN=FILE",
                   help="agreement with another grades file in that run's folder")
    p.set_defaults(fn=cmd_merge)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
