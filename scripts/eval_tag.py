"""Final scenario tags for the frozen eval set (docs/eval-set-plan.md, stage 3). No LLM calls.

Reads the frozen set's eval copy (evals/work/v2/eval.db, read-only): each ad's score and its
latest finished `letter_lab.py analyze --set v2` run (letter_runs.state, a LetterState). The
rule is scripts/eval_tags.py's, shared with eval_pool.py.

A must-have gap with a user decision on it (a remembered "no" in the frozen profile, applied
by apply_remembered) is not Requirement.needs_user. --decided-gaps says whether it still counts
as a missing must-have (`count`) or not (`ignore`); the dry run shows both.

    python scripts/eval_tag.py                               # print the tags and every change (writes nothing)
    python scripts/eval_tag.py --write --decided-gaps count  # back up candidates.csv, then add
                                                             # final_scenario, eval_score, why
Only those three columns are written; every other column, including the owner's labels, is
kept exactly as it is (checked after writing).
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK_DB = ROOT / "evals" / "work" / "v2" / "eval.db"
os.environ["DATABASE_URL"] = f"sqlite:///{WORK_DB.as_posix()}"  # nothing here writes to it
os.environ["APP_ENV"] = "test"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_freeze  # noqa: E402  (no side effects on import)
from eval_tags import STRONG_FROM, facts, label, scenario  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles are cp1252; Seek titles aren't
    sys.stdout.reconfigure(encoding="utf-8")

NEW_COLUMNS = ("final_scenario", "eval_score", "why")
NO_SCENARIO = "none"  # fits no scenario: eval_split.py leaves it out
ANALYZE_ENGINE = "eval-analyze"


def read_eval_copy(db_path: Path) -> dict[str, tuple[int | None, list | None]]:
    """key -> (score, requirements of the latest finished analysis run, or None)."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT j.source || '-' || j.source_job_id, m.score,
                      (SELECT lr.state FROM letter_runs lr WHERE lr.match_id = m.id AND lr.engine = ?
                         AND lr.status = 'done' ORDER BY lr.id DESC LIMIT 1)
               FROM job_listings j LEFT JOIN matches m ON m.job_id = j.id AND m.user_id = 1""",
            (ANALYZE_ENGINE,)).fetchall()
    finally:
        con.close()
    return {key: (score, json.loads(state)["requirements"] if state else None) for key, score, state in rows}


def _names(reqs, with_status: bool = False) -> str:
    return ", ".join(label(r) + (f" ({r.status})" if with_status else "") for r in reqs)


def tag(score: int, requirements: list, count_decided: bool,
        listing_floor: int | None = None) -> tuple[int | None, str]:
    """(scenario, why): the one or two claims that decide the scenario, in plain words."""
    f = facts(requirements)
    must = f.must_gaps + (f.decided_gaps if count_decided else [])
    sc = scenario(score, must_gap=bool(must), nice_unsupported=bool(f.nice_unsupported),
                  listing_only=bool(f.listing_only), listing_min_score=listing_floor)
    decided = [r for r in must if r.user_decision is not None]
    must_why = "must-have missing: " + _names(must) + (
        f" (remembered \"no\": {_names(decided)})" if decided else "")
    why = {
        7: f"score {score}, under 40",
        6: f"score {score}, in 70-84",
        4: "listing-only: " + _names(f.listing_only),
        3: must_why,
        2: "no must-have missing; nice-to-have missing: " + _names(f.nice_unsupported, True),
        1: f"no must-have missing; every nice-to-have backed ({f.essential_partials} essential partial"
           f"{'' if f.essential_partials == 1 else 's'})",
        None: f"score {score}: no must-have missing, nothing listing-only, so no scenario",
    }[sc]
    return sc, why


def build(db_path: Path, set_dir: Path, count_decided: bool,
          listing_floor: int | None = None) -> dict[int, dict]:
    """real.db job id -> {key, final_scenario, eval_score, why} for every frozen ad."""
    manifest = json.loads((set_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    copy = read_eval_copy(db_path)
    out = {}
    for a in manifest["ads"]:
        score, reqs = copy.get(a["key"], (None, None))
        if score is None or reqs is None:
            sc, why = None, "not tagged: " + ("no score" if score is None else "no finished analysis run")
        else:
            sc, why = tag(score, reqs, count_decided, listing_floor)
        out[a["real_db_job_id"]] = {"key": a["key"], "title": a["title"], "final_scenario": sc,
                                    "eval_score": score, "why": why}
    return out


def write_sheet(path: Path, tags: dict[int, dict], backup_dir: Path) -> Path:
    """Back up the sheet, then rewrite it with only NEW_COLUMNS filled in. Refuses if the file
    is locked (open in Excel) and checks every other cell is unchanged afterwards."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields, rows = list(reader.fieldnames), list(reader)
    before = [dict(r) for r in rows]
    missing = sorted(set(tags) - {int(r["job_id"]) for r in rows})
    if missing:
        raise SystemExit(f"frozen ads with no row in {path.name}: {missing}")
    for col in NEW_COLUMNS:
        if col not in fields:
            fields.append(col)
    for r in rows:
        t = tags.get(int(r["job_id"]))
        if t:
            # "none", not blank: eval_split.py reads a blank final_scenario as "use the draw's tag"
            r["final_scenario"] = NO_SCENARIO if t["final_scenario"] is None else str(t["final_scenario"])
            r["eval_score"] = "" if t["eval_score"] is None else str(t["eval_score"])
            r["why"] = t["why"]
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.stem}.{datetime.datetime.now():%Y%m%d-%H%M%S}{path.suffix}"
    shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    try:
        os.replace(tmp, path)
    except PermissionError:
        tmp.unlink()
        raise SystemExit(f"{path} is locked (open in Excel?): close it and run again. Nothing was changed.")
    with path.open(encoding="utf-8-sig", newline="") as f:
        after = list(csv.DictReader(f))
    for old, new in zip(before, after, strict=True):
        changed = [c for c in old if c not in NEW_COLUMNS and old[c] != new[c]]
        if changed:
            raise SystemExit(f"row {old['job_id']}: {changed} changed; restore {backup}")
    return backup


def report(tags_count: dict[int, dict], tags_ignore: dict[int, dict], sheet: Path) -> str:
    with sheet.open(encoding="utf-8-sig", newline="") as f:
        drawn = {int(r["job_id"]): r for r in csv.DictReader(f)}
    lines = [f"{'job':>5} {'drawn':>5} {'final':>5} {'score':>9}  title / why"]
    changed, crossed, untagged = [], [], []
    for jid, t in sorted(tags_count.items(), key=lambda kv: (kv[1]["final_scenario"] or 99, kv[0])):
        d = drawn[jid]
        old_sc, old_score = int(d["scenario"]), int(d["score"])
        mark = "" if t["final_scenario"] == old_sc else " *"
        lines.append(f"#{jid:<4} {old_sc:>5} {str(t['final_scenario'] or '-'):>5} {old_score:>3} -> {str(t['eval_score']):>3}"
                     f"  {t['title'][:50]}{mark}\n{'':34}{t['why']}")
        if t["final_scenario"] is None:
            untagged.append(jid)
        elif t["final_scenario"] != old_sc:
            changed.append(f"#{jid} {old_sc} -> {t['final_scenario']} ({t['title'][:45]})")
        if t["eval_score"] is not None and (old_score >= STRONG_FROM) != (t["eval_score"] >= STRONG_FROM):
            crossed.append(f"#{jid} {old_score} -> {t['eval_score']} ({t['title'][:45]})")
    decided = [f"#{jid} {tags_count[jid]['final_scenario']} if counted, {tags_ignore[jid]['final_scenario']} "
               f"if not ({tags_count[jid]['why']})"
               for jid in sorted(tags_count) if tags_count[jid]["final_scenario"] != tags_ignore[jid]["final_scenario"]]
    lines += ["", f"Scenario changed from the draw ({len(changed)}):", *[f"  {c}" for c in changed],
              "", f"Crossed the 85 line ({len(crossed)}):", *[f"  {c}" for c in crossed],
              "", f"Tag depends on counting a remembered \"no\" as missing ({len(decided)}):",
              *[f"  {c}" for c in decided],
              "", f"Not tagged ({len(untagged)}): {untagged}"]
    counts = {}
    for t in tags_count.values():
        counts[t["final_scenario"]] = counts.get(t["final_scenario"], 0) + 1
    lines += ["", "Counts (remembered \"no\" counted): "
              + ", ".join(f"{k or '-'}: {v}" for k, v in sorted(counts.items(), key=lambda kv: kv[0] or 99))]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--write", action="store_true", help="write the tags into candidates.csv")
    p.add_argument("--decided-gaps", choices=["count", "ignore"],
                   help="does a must-have gap with a remembered \"no\" count as missing? (needed with --write)")
    p.add_argument("--listing-floor", type=int, metavar="SCORE",
                   help="count listing-only only from this score up (eval_pool.py uses 60; the plan's table has none)")
    args = p.parse_args(argv)
    problems = eval_freeze.verify(eval_freeze.SET_DIR)
    if problems:
        print("the frozen set does not verify:\n  " + "\n  ".join(problems))
        return 1
    if not WORK_DB.exists():
        print(f"No {WORK_DB.relative_to(ROOT)}: run `letter_lab.py prepare --set v2` and `analyze --set v2` first.")
        return 1
    tags = {mode: build(WORK_DB, eval_freeze.SET_DIR, mode == "count", args.listing_floor)
            for mode in ("count", "ignore")}
    print(f"Listing-only floor: {args.listing_floor or 'none (the plan table)'}")
    print(report(tags["count"], tags["ignore"], eval_freeze.CANDIDATES))
    if not args.write:
        print("\n(dry run: nothing written; --write --decided-gaps count|ignore saves the tags)")
        return 0
    if not args.decided_gaps:
        print("--write needs --decided-gaps count|ignore (the owner's call)")
        return 1
    backup = write_sheet(eval_freeze.CANDIDATES, tags[args.decided_gaps], eval_freeze.CANDIDATES.parent / "backups")
    print(f"\nWrote {', '.join(NEW_COLUMNS)} into {eval_freeze.CANDIDATES.relative_to(ROOT)} "
          f"(decided gaps: {args.decided_gaps}, listing floor: {args.listing_floor or 'none'}); backup {backup.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
