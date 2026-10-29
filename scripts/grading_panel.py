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

v2 (evals/grading-standard-v2.md, eval set v2): one packet per letter, each graded by a
fresh judge, twice (graders A and B), with one fresh adjudicator per letter they split on.

    python scripts/grading_panel.py v2-pack <tag> <run> [<run> ...] [--only KEY ...]
    python scripts/grading_panel.py v2-run <tag>                   # one fresh judge per packet -> answers/<A|B>/
    python scripts/grading_panel.py v2-check <tag>                 # missing / malformed answers
    python scripts/grading_panel.py v2-disagreements <tag>         # -> disagreements/<lid>.json
    python scripts/grading_panel.py v2-run <tag> --stage adjudicate  # one fresh adjudicator per file
    python scripts/grading_panel.py v2-merge <tag>                 # -> work/v2/runs/<run>/judge-<tag>.json
    python scripts/grading_panel.py v2-agreement <tag> <run> [--only KEY ...] [--accept KEY ...]
    python scripts/grading_panel.py v2-pairs <tag> <baseline> <new>  # A/B packets, both orders
    python scripts/grading_panel.py v2-picks <tag>                 # -> picks.json

Everything under evals/panels/ and evals/work/ is gitignored: packets hold ad text, the
profile and letters. The graders' notes quote letters, so they stay in gitignored folders.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
PANELS = EVALS / "panels"
RUNS = EVALS / "runs"
ITEMS = ("supported_musts_covered", "no_unsupported_claims", "specific_detail", "would_send")
PROFILE_ID = 1
PROFILE_HEADING = "# The candidate's profile"  # a v2 packet's judge-facing part starts here (v2-run)

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles are cp1252; the reports print ≤ and ±
    sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(EVALS / 'eval.db').as_posix()}")
os.environ.setdefault("APP_ENV", "test")
sys.path.insert(0, str(ROOT))


def _profile_text(profile) -> str:
    from app.llm.letter.state import ProfileIndex

    index = ProfileIndex(profile)
    facts = "\n".join(f"- {k}: {v}" for k, v in index.facts.items())
    return (
        f"{PROFILE_HEADING} (the ONLY source of truth about the candidate)\n\n"
        f"Name: {profile.name}\n\nEligibility facts (not evidence, just facts):\n{facts}\n\n"
        f"Profile entries (pointer in brackets, then the text):\n\n{index.prompt_catalog()}\n"
    )


def _profile_block() -> str:
    from app.db import SessionLocal
    from app.llm.letter.runner import load_profile

    with SessionLocal() as db:
        return _profile_text(load_profile(db, PROFILE_ID))


def _ad(key: str, jobs: Path = EVALS / "jobs") -> str:
    d = json.loads((jobs / f"{key}.json").read_text(encoding="utf-8"))
    head = (f"Title: {d['title']}\nCompany: {d.get('company') or '(not stated / recruiter)'}\n"
            f"Location: {d.get('location') or '-'}")
    return head + "\n\n" + d["raw_description"].strip()


def _letter(run: str, key: str, runs: Path = RUNS) -> str:
    return (runs / run / "letters" / f"{key}.txt").read_text(encoding="utf-8").strip()


def _keys(run: str, runs: Path = RUNS) -> list[str]:
    meta = json.loads((runs / run / "run.json").read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# v2: one fresh judge per letter (evals/grading-standard-v2.md; docs/letter-metrics-plan.md §6)
# ---------------------------------------------------------------------------
V2_WORK = EVALS / "work" / "v2"
V2_RUNS = V2_WORK / "runs"
V2_DB = V2_WORK / "eval.db"
V2_REF = V2_WORK / "reference" / "ads"
V2_ADS = EVALS / "sets" / "v2" / "ads"
V2_SET_SPLIT = EVALS / "sets" / "v2" / "split.json"
CANDIDATES = EVALS / "candidates" / "candidates.csv"
CALIBRATION_DIR = V2_WORK / "calibration"  # gitignored: the halves and the worked examples quote real letters
CALIBRATION_SPLIT = CALIBRATION_DIR / "split.json"
CALIBRATION_EXAMPLES = CALIBRATION_DIR / "examples.md"
ACCEPTED_UNTRUE = CALIBRATION_DIR / "accepted-untrue.json"  # {key: the claim}: judge flags the owner accepted
GRADERS = ("A", "B")
CHECKLIST = ("D2a", "D2b", "D2c", "D2d", "D2e", "D3a", "D3b", "D4a", "D4b", "D5a", "D5b", "D5c",
             "D6a", "D6b", "D6c", "D6d", "D6e", "D6f", "D6g", "D6h", "D7a")
TICKS = ("generic", "missed_important", "weak_example", "sounds_ai", "no_personal_reason", "too_long")
LABELS = ("supported", "stretch", "fabricated", "employer_fact_not_in_ad")
UNTRUE = LABELS[1:]
PRESENTED = ("experience", "skill", "related", "claimed", "none")
# The owner's grades for a v2 run (evals/work/v2/runs/<run>/grades-owner.csv; letter_lab.py
# grade-sheet writes the blank form). untrue = Y/N, a tick = any of Y / x / 1, blank = not ticked.
OWNER_FIELDS = ("key", "send_readiness", "untrue", "untrue_text", *TICKS, "note")
KAPPA_WARNING = "on fewer than ~30 letters one disagreement moves kappa a lot: read the counts first"


def _profile_block_v2() -> str:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.llm.letter.runner import load_profile

    engine = create_engine(f"sqlite:///{V2_DB.as_posix()}")
    try:
        with Session(engine) as db:
            return _profile_text(load_profile(db, PROFILE_ID))
    finally:
        engine.dispose()


def _verify_reference() -> list[str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_reference  # points DATABASE_URL at the v2 eval.db (read-only); v2 never uses v1's

    return eval_reference.verify()


def _reference(key: str) -> dict:
    return json.loads((V2_REF / f"{key}.json").read_text(encoding="utf-8"))


def _flags(ref: dict) -> dict[str, tuple[bool, bool]]:
    """Row id -> (must_cover, listing_only), from the guardrails' own definitions."""
    from types import SimpleNamespace

    from app.llm.letter.guardrails import listing_only, must_cover
    from app.llm.letter.state import Requirement

    reqs = [Requirement.model_validate(r) for r in ref["requirements"]]
    musts = {r.id for r in must_cover(SimpleNamespace(requirements=reqs))}
    return {r.id: (r.id in musts, listing_only(r)) for r in reqs}


def graded_rows(ref: dict) -> list[str]:
    """The rows a judge answers for: every row except not_for_letter (standard §4)."""
    return [r["id"] for r in ref["requirements"] if r["letter_role"] != "not_for_letter"]


def _checklist_block(ref: dict) -> str:
    flags, ev = _flags(ref), ref.get("evidence", {})

    def pointers(ps: list[str]) -> str:
        return "".join(f"\n      - [{p}] ({ev[p]['kind']}) {ev[p]['text']}" if p in ev else f"\n      - [{p}]"
                       for p in ps) or " none"

    out = ["## Reference checklist (the answer key: grade by it, standard §1)\n"]
    for r in ref["requirements"]:
        must, listing = flags[r["id"]]
        if r["letter_role"] == "not_for_letter":
            out.append(f"{r['id']} (not_for_letter: eligibility, not graded) {r['text']}\n")
            continue
        out.append(f"{r['id']} [{r['importance']} · {r['letter_role']} · {r['status']}]"
                   f" must_cover={'yes' if must else 'no'} listing_only={'yes' if listing else 'no'}\n"
                   f"    text: {r['text']}\n    evidence:{pointers(r['evidence'])}")
        if r.get("related_evidence"):
            out.append(f"    related_evidence (does NOT back it):{pointers(r['related_evidence'])}")
        if r.get("note"):
            out.append(f"    note: {r['note']}")
        out.append("")
    return "\n".join(out)


def _letter_block(lid: str, text: str, label: str | None = None) -> str:
    from app.llm.letter.rubric import word_count

    return f"## Letter {label or lid} ({word_count(text)} words)\n\n```\n{text}\n```\n"


def _job_block(job: str, key: str) -> str:
    return f"# JOB {job}\n\n## Job ad\n\n```\n{_ad(key, V2_ADS)}\n```\n\n{_checklist_block(_reference(key))}"


def _keys_for(runs: list[str], only: list[str] | None, half: str | None = None) -> list[str]:
    keys = [k for k in _keys(runs[0], V2_RUNS) if all(k in set(_keys(r, V2_RUNS)) for r in runs[1:])]
    if half:
        keys = [k for k in keys if k in set(calibration_half(half))]
    if only:
        missing = sorted(set(only) - set(keys))
        if missing:
            raise SystemExit(f"Not graded in every run given: {', '.join(missing)}")
        keys = [k for k in keys if k in set(only)]
    return keys


def _check_reference(keys: list[str]) -> None:
    problems = _verify_reference()
    missing = [k for k in keys if not (V2_REF / f"{k}.json").exists()]
    if problems or missing:
        raise SystemExit("The reference checklist doesn't verify (scripts/eval_reference.py verify):\n  "
                         + "\n  ".join(problems + [f"no reference for {k}" for k in missing]))


def cmd_v2_pack(args) -> int:
    """One packet per letter, for one fresh judge each (standard §2)."""
    keys = _keys_for(args.runs, args.only, args.half)
    _check_reference(keys)
    out = PANELS / args.tag
    if (out / "key" / "letters.json").exists():
        raise SystemExit(f"{_rel(out)} already has letter packets: use a new tag")
    (out / "packets").mkdir(parents=True, exist_ok=True)
    (out / "key").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    rng.shuffle(keys)
    jobs = {k: f"J{j:02d}" for j, k in enumerate(keys, 1)}
    letters = [(run, k) for k in keys for run in args.runs]
    rng.shuffle(letters)
    ids = rng.sample(range(1000, 9999), len(letters))
    profile = _profile_block_v2()
    mapping = {}
    for (run, key), n in zip(letters, ids):
        lid = f"L{n}"
        mapping[lid] = {"run": run, "key": key, "job": jobs[key]}
        packet = [
            f"# Letter packet {lid} (grading standard v2)\n",
            "You are one fresh judge for ONE letter. Read `evals/grading-standard-v2.md` in full and "
            "apply it exactly. Read nothing else.\n",
            f"Write your answers to `evals/panels/{args.tag}/answers/<your grader letter>/{lid}.json`.\n",
            profile, "---\n", _job_block(jobs[key], key), _letter_block(lid, _letter(run, key, V2_RUNS)),
        ]
        (out / "packets" / f"{lid}.md").write_text("\n".join(packet), encoding="utf-8")
    (out / "key" / "letters.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    (out / "packets" / "index.txt").write_text("\n".join(sorted(mapping)) + "\n", encoding="utf-8")
    print(f"{len(mapping)} letter packets, {len(keys)} jobs -> {_rel(out / 'packets')}/")
    print("One fresh judge per packet per grader (A and B). Judges must not be pointed at key/.")
    return 0


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _yn(v) -> str:
    return str(v).strip().upper()[:1]


def validate(ans: dict, ref: dict) -> list[str]:
    """Shape errors in one judge's answer for one letter (standard §9). A judge with any is re-run."""
    errs = []
    reqs = {r.get("id"): r for r in ans.get("requirements", [])}
    want = graded_rows(ref)
    if sorted(reqs) != sorted(want):
        errs.append(f"requirements: want {want}, got {sorted(reqs)}")
    for rid, r in reqs.items():
        if _yn(r.get("addressed")) not in ("Y", "N"):
            errs.append(f"{rid}: addressed must be Y or N")
        if r.get("presented_as") not in PRESENTED:
            errs.append(f"{rid}: presented_as must be one of {', '.join(PRESENTED)}")
    for i, c in enumerate(ans.get("claims", [])):
        if c.get("label") not in LABELS:
            errs.append(f"claim {i + 1}: label must be one of {', '.join(LABELS)}")
        if c.get("about") not in ("candidate", "employer"):
            errs.append(f"claim {i + 1}: about must be candidate or employer")
    checklist = ans.get("checklist", {})
    if sorted(checklist) != sorted(CHECKLIST):
        errs.append(f"checklist: want all of {CHECKLIST[0]}..{CHECKLIST[-1]}, missing "
                    f"{sorted(set(CHECKLIST) - set(checklist))}, extra {sorted(set(checklist) - set(CHECKLIST))}")
    errs += [f"{d}: answer must be Y or N" for d, v in checklist.items() if _yn(v.get("answer")) not in ("Y", "N")]
    grade = ans.get("send_readiness", {}).get("grade")
    if grade not in (1, 2, 3, 4):
        errs.append("send_readiness.grade must be 1, 2, 3 or 4")
    ticks = ans.get("ticks", {})
    if sorted(ticks) != sorted(TICKS) or not all(isinstance(v, bool) for v in ticks.values()):
        errs.append(f"ticks: want all six of {', '.join(TICKS)} as true/false")
    elif grade == 4 and any(ticks.values()):
        errs.append("ticks: a 4 has no reasons ticked")
    return errs


def check_quotes(ans: dict, letter: str) -> tuple[dict, list[str]]:
    """The answer with its quotes checked against the letter (check_requirements.quote_in_letter),
    so a judge can't credit words it imagined: an addressed row whose quote isn't there counts as
    not addressed, and a claim whose quote isn't there is dropped. Returns (answer, problems)."""
    import copy

    from app.llm.letter.tools.check_requirements import quote_in_letter

    ans, problems = copy.deepcopy(ans), []
    for r in ans["requirements"]:
        if _yn(r["addressed"]) == "Y" and not quote_in_letter(r.get("quote", ""), letter):
            r["addressed"] = "N"
            problems.append(f"{r['id']}: quote not in the letter, counted as not addressed")
    kept = []
    for c in ans["claims"]:
        if quote_in_letter(c.get("quote", ""), letter):
            kept.append(c)
        else:
            problems.append(f"claim dropped, quote not in the letter: {c.get('quote', '')[:80]!r}")
    ans["claims"] = kept
    return ans, problems


def claim_counts(claims: list[dict]) -> str:
    return ", ".join(f"{label} {sum(c['label'] == label for c in claims)}" for label in UNTRUE)


def cells(ans: dict) -> dict[str, str]:
    """Every answer two graders can agree or disagree on, as a comparable string."""
    out = {"send_readiness": str(ans["send_readiness"]["grade"]), "claims": claim_counts(ans["claims"])}
    out |= {d: _yn(ans["checklist"][d]["answer"]) for d in CHECKLIST}
    out |= {f"req:{r['id']}": _yn(r["addressed"]) for r in ans["requirements"]}
    out |= {f"tick:{t}": "Y" if ans["ticks"][t] else "N" for t in TICKS if t not in DERIVED_TICKS}
    return out


def _detail(ans: dict, item: str):
    if item == "send_readiness":
        return ans["send_readiness"]
    if item == "claims":
        return ans["claims"]
    if item.startswith("req:"):
        return next(r for r in ans["requirements"] if r["id"] == item[4:])
    if item.startswith("tick:"):
        return ans["ticks"][item[5:]]
    return ans["checklist"][item]


def _mapping(tag: str, name: str = "letters") -> dict:
    path = PANELS / tag / "key" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"No {_rel(path)}: run `v2-pack` (or `v2-pairs`) first")
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_answer(tag: str, grader: str, lid: str) -> dict | None:
    path = PANELS / tag / "answers" / grader / f"{lid}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["letters"][lid]


def _answers(tag: str, graders: tuple[str, ...] = GRADERS) -> tuple[dict, dict[str, dict[str, dict]], dict[str, list[str]]]:
    """(mapping, {lid: {grader: checked answer}}, {lid: quote problems}); refuses on a missing or
    malformed answer, naming every one, so those judges can be re-run."""
    mapping = _mapping(tag)
    out, problems, bad = {}, {}, []
    for lid, m in sorted(mapping.items()):
        ref, letter = _reference(m["key"]), _letter(m["run"], m["key"], V2_RUNS)
        out[lid] = {}
        for g in graders:
            try:
                raw = _raw_answer(tag, g, lid)
            except (json.JSONDecodeError, KeyError) as e:
                bad.append(f"{g}/{lid}: unreadable ({e})")
                continue
            if raw is None:
                bad.append(f"{g}/{lid}: missing")
                continue
            errs = validate(raw, ref)
            if errs:
                bad.append(f"{g}/{lid}: " + "; ".join(errs))
                continue
            out[lid][g], found = check_quotes(raw, letter)
            problems.setdefault(lid, []).extend(f"grader {g}: {p}" for p in found)
    if bad:
        raise SystemExit(f"{len(bad)} answer file(s) to redo:\n  " + "\n  ".join(bad))
    return mapping, out, problems


def cmd_v2_check(args) -> int:
    _, answers, problems = _answers(args.tag, tuple(args.graders))
    print(f"All {len(answers)} letters answered by graders {' and '.join(args.graders)}; shapes valid.")
    for lid, ps in sorted(problems.items()):
        for p in ps:
            print(f"  {lid} {p}")
    return 0


def cmd_v2_disagreements(args) -> int:
    mapping, answers, _ = _answers(args.tag)
    out = PANELS / args.tag / "disagreements"
    out.mkdir(parents=True, exist_ok=True)
    agree: dict[str, list[int]] = {}
    split_letters = 0
    for lid, by in answers.items():
        a, b = cells(by["A"]), cells(by["B"])
        split = []
        for item in a:
            group = item.split(":")[0] if ":" in item else ("checklist" if item.startswith("D") else item)
            tally = agree.setdefault(group, [0, 0])
            tally[1] += 1
            if a[item] == b.get(item):
                tally[0] += 1
            else:
                split.append({"item": item, "grader_A": _detail(by["A"], item), "grader_B": _detail(by["B"], item)})
        path = out / f"{lid}.json"
        if split:
            split_letters += 1
            path.write_text(json.dumps({"letter": lid, "job": mapping[lid]["job"], "cells": split},
                                       indent=2, ensure_ascii=False), encoding="utf-8")
        elif path.exists():
            path.unlink()
    print("A/B agreement: " + ", ".join(f"{g} {n}/{t}" for g, (n, t) in agree.items()))
    print(f"{split_letters} of {len(answers)} letters need the adjudicator -> {_rel(out)}/ "
          "(one fresh adjudicator per file)")
    return 0


def _apply(final: dict, ruling: dict, by: dict[str, dict]) -> None:
    item, value = ruling["item"], str(ruling.get("value", "")).strip()
    sided = by.get(ruling.get("sided_with"), by["A"])
    if item == "send_readiness":
        final["send_readiness"] = {"grade": int(value), "reason": ruling.get("reason", ""),
                                   "edits": sided["send_readiness"].get("edits", [])}
    elif item == "claims":
        final["claims"] = ruling["claims"]
    elif item.startswith("req:"):
        row = next(r for r in final["requirements"] if r["id"] == item[4:])
        theirs = next(r for r in sided["requirements"] if r["id"] == item[4:])
        row.update(addressed=_yn(value), quote=ruling.get("quote", theirs.get("quote", "")),
                   evidence_used=ruling.get("evidence_used", theirs.get("evidence_used", [])),
                   presented_as=ruling.get("presented_as", theirs.get("presented_as", "none")))
    elif item.startswith("tick:"):
        final["ticks"][item[5:]] = _yn(value) == "Y"
    else:
        final["checklist"][item] = {"answer": _yn(value), "quote": ruling.get("quote", ""),
                                    "reason": ruling.get("reason", "")}


# Ticks code works out from the judge's own answers (eval-set plan Decision log 2026-10-29), so a
# tick can't contradict them; the judge still answers all six (`judge_ticks`), and two judges
# splitting on a derived tick isn't a cell to adjudicate. The owner's sounds_ai is a count rule
# (letter_metrics C5b): the judge's own sounds_ai is kept as an alarm and never calibrated on.
DERIVED_TICKS = ("missed_important", "no_personal_reason")
ALARM_TICKS = ("sounds_ai",)


def missable_rows(ref: dict) -> list[str]:
    """Rows a letter can miss: backed (supported or partial), essential or important, and meant to
    be said (headline or mention). A gap is never a miss, and an implied row needn't be said."""
    return [r["id"] for r in ref["requirements"] if r["status"] in ("supported", "partial")
            and r["importance"] in ("essential", "important") and r["letter_role"] in ("headline", "mention")]


def missed_rows(ref: dict, requirements: list[dict]) -> list[str]:
    said = {r["id"] for r in requirements if _yn(r["addressed"]) == "Y"}
    return [rid for rid in missable_rows(ref) if rid not in said]


def derived_ticks(ref: dict, answer: dict) -> dict[str, bool]:
    return {"missed_important": bool(missed_rows(ref, answer["requirements"])),
            "no_personal_reason": _yn(answer["checklist"]["D4b"]["answer"]) == "N"}


def _versions(tag: str, lid: str) -> list[dict]:
    """The judge version behind each of a letter's answer files ("not recorded": a subagent wrote it)."""
    paths = [PANELS / tag / "answers" / g / f"{lid}.json" for g in GRADERS] + [PANELS / tag / "adjudication" / f"{lid}.json"]
    found = []
    for p in paths:
        if p.exists():
            j = json.loads(p.read_text(encoding="utf-8")).get("judge")
            v = {k: j.get(k) for k in JUDGE_VERSION} if j else {"backend": "not recorded"}
            if v not in found:
                found.append(v)
    return found


def final_v2(tag: str, graders: tuple[str, ...] = GRADERS) -> dict[str, dict]:
    """lid -> the final answer: agreed cells from grader A, split cells from the adjudicator."""
    import copy

    mapping, answers, problems = _answers(tag, graders)
    finals = {}
    for lid, by in answers.items():
        first = by[graders[0]]  # one grader (a calibration run): its answers are final, nothing splits
        a, b = cells(first), cells(by[graders[-1]])
        split = [item for item in a if a[item] != b.get(item)]
        rulings = {}
        path = PANELS / tag / "adjudication" / f"{lid}.json"
        if path.exists():
            rulings = {r["item"]: r for r in json.loads(path.read_text(encoding="utf-8"))["rulings"]}
        missing = [item for item in split if item not in rulings]
        if missing:
            raise SystemExit(f"{lid} has split cells with no ruling ({', '.join(missing)}): "
                             "run its adjudicator first")
        final = copy.deepcopy(first)
        for item in split:
            _apply(final, rulings[item], by)
        m = mapping[lid]
        final, found = check_quotes(final, _letter(m["run"], m["key"], V2_RUNS))
        claims, ref = final["claims"], _reference(m["key"])
        gap_rows = {r["id"] for r in ref["requirements"] if r["status"] == "gap"}
        finals[lid] = {
            **m, "letter": lid,
            "send_readiness": final["send_readiness"]["grade"],
            "untrue": "Y" if any(c["label"] in UNTRUE for c in claims) else "N",
            "counts": {label: sum(c["label"] == label for c in claims) for label in LABELS}
            | {"gap_fabrications": sum(c["label"] == "fabricated" and c.get("requirement") in gap_rows
                                       for c in claims)},
            "ticks": {**final["ticks"], **derived_ticks(ref, final)},
            "judge_ticks": final["ticks"],
            "missed_rows": missed_rows(ref, final["requirements"]),
            "checklist": {d: _yn(final["checklist"][d]["answer"]) for d in CHECKLIST},
            "requirements": final["requirements"],
            "claims": claims,
            "checklist_doubts": [{**d, "grader": g} for g in graders for d in by[g].get("checklist_doubts", [])],
            "quote_problems": problems.get(lid, []) + [f"final: {p}" for p in found],
            "adjudicated": split,
            "judge": _versions(tag, lid),
        }
    return finals


def _doubts_md(tag: str, finals: dict[str, dict]) -> str:
    lines = [f"# Checklist doubts, panel `{tag}`\n",
             "Rows of the reference checklist a judge thought were wrong. The checklist stays the "
             "answer key: these never changed a score. Recheck each one against the profile and the ad.\n"]
    by_key: dict[str, list] = {}
    for f in finals.values():
        by_key.setdefault(f["key"], []).extend(f["checklist_doubts"])
    if not any(by_key.values()):
        lines.append("None.")
    for key, doubts in sorted(by_key.items()):
        if not doubts:
            continue
        rows = {r["id"]: r for r in _reference(key)["requirements"]}
        lines.append(f"## {key}\n")
        for d in doubts:
            r = rows.get(d.get("id"), {})
            lines.append(f"- **{d.get('id')}** ({r.get('importance', '?')}, {r.get('status', '?')}) "
                         f"{r.get('text', '(no such row)')}\n  - grader {d['grader']}: {d.get('why', '')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_v2_merge(args) -> int:
    finals = final_v2(args.tag, tuple(args.graders))
    versions = []
    for f in finals.values():
        versions += [v for v in f["judge"] if v not in versions]
    if len(versions) > 1:  # one panel, one judge: else its numbers mix two judges (metrics plan §6.5)
        raise SystemExit("This panel's answers come from more than one judge version:\n  "
                         + "\n  ".join(judge_label(v) for v in versions)
                         + "\nRe-run the odd ones with `v2-run --redo`")
    print(f"Judge: {judge_label(versions[0]) if versions else 'none'}")
    for run in sorted({f["run"] for f in finals.values()}):
        rows = {f["key"]: f for f in finals.values() if f["run"] == run}
        path = V2_RUNS / run / f"judge-{args.tag}.json"
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        grades = [f["send_readiness"] for f in rows.values()]
        print(f"{run}: {len(rows)} letters, send-readiness mean {sum(grades) / len(grades):.2f}, "
              f"send-ready (3+) {sum(g >= 3 for g in grades)}/{len(rows)}, "
              f"untrue {sum(f['untrue'] == 'Y' for f in rows.values())}/{len(rows)} -> {_rel(path)}")
    doubts = PANELS / args.tag / "checklist-doubts.md"
    doubts.write_text(_doubts_md(args.tag, finals), encoding="utf-8")
    n = sum(len(f["checklist_doubts"]) for f in finals.values())
    print(f"{n} checklist doubt(s) for the owner to recheck -> {_rel(doubts)}")
    return 0


# Pairwise: one fresh judge per pair per order (standard §8)

def cmd_v2_pairs(args) -> int:
    keys = _keys_for([args.run_a, args.run_b], args.only)
    _check_reference(keys)
    out = PANELS / args.tag
    if (out / "key" / "pairs.json").exists():
        raise SystemExit(f"{_rel(out)} already has pair packets: use a new tag")
    (out / "pairs").mkdir(parents=True, exist_ok=True)
    (out / "key").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    rng.shuffle(keys)
    ids = rng.sample(range(100, 1000), 2 * len(keys))
    profile = _profile_block_v2()
    pairs = {}
    for j, key in enumerate(keys, 1):
        p1, p2 = f"P{ids.pop()}", f"P{ids.pop()}"
        for pid, twin, first, second in ((p1, p2, args.run_a, args.run_b), (p2, p1, args.run_b, args.run_a)):
            pairs[pid] = {"key": key, "first": first, "second": second, "twin": twin}
            packet = [
                f"# Pair packet {pid} (grading standard v2, §8)\n",
                "You are one fresh judge for ONE pair of letters. Read `evals/grading-standard-v2.md` in "
                "full and apply §8 exactly. Read nothing else.\n",
                f"Write your answer to `evals/panels/{args.tag}/answers/pairs/{pid}.json`.\n",
                profile, "---\n", _job_block(f"J{j:02d}", key),
                _letter_block("", _letter(first, key, V2_RUNS), "first"),
                _letter_block("", _letter(second, key, V2_RUNS), "second"),
            ]
            (out / "pairs" / f"{pid}.md").write_text("\n".join(packet), encoding="utf-8")
    meta = {"baseline": args.run_a, "new": args.run_b, "pairs": pairs}
    (out / "key" / "pairs.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out / "pairs" / "index.txt").write_text("\n".join(sorted(pairs)) + "\n", encoding="utf-8")
    print(f"{len(pairs)} pair packets ({len(keys)} ads, both orders) -> {_rel(out / 'pairs')}/")
    return 0


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test on the decided ads (ties left out)."""
    from math import comb

    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def pick_result(pick_1: str, pick_2: str, first_1: str, second_1: str) -> str | None:
    """The run that won an ad over its two orders, or None for a tie. Order 2 shows the letters
    swapped, so a pick that follows the position rather than the letter is a tie."""
    def winner(pick, first, second):
        return {"first": first, "second": second}.get(pick.strip().lower())

    w1 = winner(pick_1, first_1, second_1)
    w2 = winner(pick_2, second_1, first_1)
    return w1 if w1 is not None and w1 == w2 else None


def cmd_v2_picks(args) -> int:
    meta = _mapping(args.tag, "pairs")
    base, new, pairs = meta["baseline"], meta["new"], meta["pairs"]
    results, missing, done = {}, [], set()
    for pid, p in sorted(pairs.items()):
        if pid in done:
            continue
        done |= {pid, p["twin"]}
        picks = []
        for x in (pid, p["twin"]):
            path = PANELS / args.tag / "answers" / "pairs" / f"{x}.json"
            if not path.exists():
                missing.append(x)
                continue
            pick = json.loads(path.read_text(encoding="utf-8"))["pairs"][x]["pick"]
            if pick.strip().lower() not in ("first", "second", "same"):
                missing.append(f"{x} (pick {pick!r})")
            picks.append(pick)
        if len(picks) == 2:
            results[p["key"]] = pick_result(picks[0], picks[1], p["first"], p["second"])
    if missing:
        raise SystemExit("Pair answers to redo: " + ", ".join(missing))
    wins = sum(r == new for r in results.values())
    losses = sum(r == base for r in results.values())
    ties = len(results) - wins - losses
    rate = (wins + ties / 2) / len(results) if results else 0.0
    summary = {"baseline": base, "new": new, "ads": len(results), "new_wins": wins, "baseline_wins": losses,
               "ties": ties, "win_rate": round(rate, 3), "sign_test_p": round(sign_test(wins, losses), 4),
               "per_ad": {k: (v or "tie") for k, v in sorted(results.items())}}
    (PANELS / args.tag / "picks.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"{new} vs {base} on {len(results)} ads: {wins} wins, {losses} losses, {ties} ties "
          f"(order-dependent picks count as ties); win rate {rate:.2f}, sign test p = {summary['sign_test_p']}")
    return 0


# Running the judges (stage 8; eval-set plan Decision log 2026-10-28 and 2026-10-29): one headless
# call per answer file, so a judge gets the standard and its packet only, never the repo (the
# owner's sounds_ai list lives in letter_metrics.py and must not reach it). Answers land where the
# subagent judges wrote them, so v2-check / v2-disagreements / v2-merge read them unchanged.

JUDGE_MODEL = "claude-opus-5-5"  # a full id: an alias such as "opus" moves when a new model ships
JUDGE_EFFORT = "high"
JUDGE_TIMEOUT = 1200  # seconds for one judge
JUDGE_ATTEMPTS = 2  # a judge that fails or answers in the wrong shape is replaced by a fresh one
JUDGE_DIR = Path(tempfile.gettempdir()) / "letter-judge"  # claude-cli's working folder: empty, neutral name
STANDARD_V2 = EVALS / "grading-standard-v2.md"
JUDGE_VERSION = ("backend", "model", "effort", "cli_version", "standard_sha256", "instructions_sha256",
                 "examples_sha256")
JUDGE_SYSTEM = ("You are a judge in a blind cover-letter evaluation. You have no tools and no files: everything "
                "you need is in the user's message. Apply the grading standard it contains exactly, and answer "
                "only in the JSON shape it asks for.")
JUDGE_TASKS = {
    "grade": (
        "You are one fresh judge for ONE cover letter, letter {lid} of job {job}. Accuracy and consistency "
        "matter more than speed.\n\n"
        "Work in the standard's order (§3): the profile, then the ad, then the reference checklist, and only "
        "then the letter. Answer §4, §5, §6, §7 in turn. Quote the letter's exact words for every addressed "
        "row and every claim. Code checks each quote against the letter, and an addressed row whose quote "
        "isn't there counts as not addressed. Grade by the reference checklist even where you doubt it, and "
        "put the doubt in `checklist_doubts`. Length is not quality.\n\n"
        "Answer with the §9 JSON for letter {lid}."),
    "adjudicate": (
        "You are the adjudicator for ONE cover letter, letter {lid} of job {job}. Two judges (A and B) applied "
        "the standard to it and split on the cells in the disagreements above, which show both answers for "
        "each cell. For each cell, re-read the letter, the ad, the checklist and the profile yourself, and "
        "rule by the standard's text, not by which judge argued better. Where the standard is silent, say so "
        "and follow its stated tests.\n\n"
        "Answer with one ruling per cell, its `item` copied from the disagreements. What goes in `value`:\n"
        "- `send_readiness`: 1-4. `sided_with` picks whose edit list is kept.\n"
        "- a `D..` question: Y or N, with the `quote` it rests on.\n"
        "- `req:<id>`: Y or N. Add `quote`, `presented_as` and `evidence_used` when your answer differs from "
        "the judge you side with; otherwise theirs are kept.\n"
        "- `tick:<name>`: Y or N.\n"
        "- `claims`: the word `claims`, plus a `claims` field holding the full corrected list in the §9 shape."),
}


class JudgeError(Exception):
    """One judge call that gave no usable answer; a fresh judge is tried instead."""


class UsageLimit(JudgeError):
    """The subscription's usage limit: no judge will answer until it resets, so the run stops."""


USAGE_LIMIT = re.compile(r"hit your .*limit|usage limit", re.IGNORECASE)


def _obj(props: dict, optional: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": props, "required": [k for k in props if k not in optional],
            "additionalProperties": False}


def _list(item: dict) -> dict:
    return {"type": "array", "items": item}


_STR = {"type": "string"}
_YN = {"type": "string", "enum": ["Y", "N"]}
_PRESENTED = {"type": "string", "enum": list(PRESENTED)}
_CLAIM = _obj({"quote": _STR, "about": {"type": "string", "enum": ["candidate", "employer"]},
               "label": {"type": "string", "enum": list(LABELS)}, "source": _list(_STR),
               "requirement": {"type": ["string", "null"]}, "why": _STR})


def grade_schema(lid: str) -> dict:
    """The standard's §9 shape for one letter, so the CLI turns a malformed answer back itself."""
    letter = _obj({
        "job": _STR,
        "requirements": _list(_obj({"id": _STR, "addressed": _YN, "quote": _STR, "presented_as": _PRESENTED,
                                    "evidence_used": _list(_STR), "reason": _STR})),
        "claims": _list(_CLAIM),
        "checklist": _obj({d: _obj({"answer": _YN, "quote": _STR, "reason": _STR}) for d in CHECKLIST}),
        "send_readiness": _obj({"grade": {"type": "integer", "enum": [1, 2, 3, 4]},
                                "edits": _list(_obj({"what": _STR, "sentences": {"type": "integer"}})),
                                "reason": _STR}),
        "ticks": _obj({t: {"type": "boolean"} for t in TICKS}),
        "note": _STR,
        "checklist_doubts": _list(_obj({"id": _STR, "why": _STR})),
    })
    return _obj({"standard": {"type": "string", "enum": ["v2"]}, "letters": _obj({lid: letter})})


ADJUDICATION_SCHEMA = _obj({"rulings": _list(_obj(
    {"item": _STR, "value": _STR, "reason": _STR, "sided_with": {"type": "string", "enum": ["A", "B", "neither"]},
     "quote": _STR, "presented_as": _PRESENTED, "evidence_used": _list(_STR), "claims": _list(_CLAIM)},
    optional=("quote", "presented_as", "evidence_used", "claims")))})


def _packet_body(packet: str) -> str:
    """The packet from its profile block on. Its opening lines tell a subagent which files to read
    and write; here the standard is pasted in and code saves the answer, so no path is needed."""
    at = packet.find(PROFILE_HEADING)
    if at < 0:
        raise SystemExit("A packet without its profile block: re-pack it with v2-pack")
    return packet[at:]


EXAMPLE_HEAD = "## Example: "
READING_TODO = "(to write: the worked reading, then the owner confirms it)"


def load_examples(path: Path) -> dict[str, str]:
    """key -> its worked example (standard §10). Refuses an unfinished reading, and any letter that
    isn't in the calibration half: the check half is never shown to a judge."""
    parts = re.split(rf"^{re.escape(EXAMPLE_HEAD)}(\S+)\s*$", path.read_text(encoding="utf-8"), flags=re.M)
    examples = {key: body.strip() for key, body in zip(parts[1::2], parts[2::2])}
    calibration = set(calibration_half("calibration"))
    bad = [f"{k}: not in the calibration half" for k in examples if k not in calibration]
    bad += [f"{k}: its worked reading isn't written" for k, body in examples.items() if READING_TODO in body]
    if bad or not examples:
        raise SystemExit(f"{_rel(path)} can't be used:\n  " + ("\n  ".join(bad) or "no examples in it"))
    return examples


def examples_block(examples: dict[str, str], skip: str | None) -> str:
    """The worked examples for one judge, numbered, without the letter it grades (leave-one-out),
    so a calibration-half letter is never graded with its own answer in view."""
    shown = [body for key, body in examples.items() if key != skip]
    return ("# Worked examples: the candidate's own grades on other letters (standard §10)\n\n"
            + "\n\n".join(f"## Example {i}\n\n{body}" for i, body in enumerate(shown, 1)) + "\n")


def judge_prompt(stage: str, packet: str, lid: str, job: str, disagreements: dict | None = None,
                 examples: str | None = None) -> str:
    """A judge's whole message: the standard, (the worked examples,) its packet, (the
    disagreements,) then the task last. The one builder for every backend, so each backend sends
    the same text."""
    parts = ["# The grading standard\n", STANDARD_V2.read_text(encoding="utf-8"), "---\n"]
    if examples:
        parts += [examples, "---\n"]
    parts += ["# Your packet\n", _packet_body(packet)]
    if disagreements is not None:
        parts += ["---\n", "# The disagreements\n",
                  "```json\n" + json.dumps(disagreements["cells"], indent=2, ensure_ascii=False) + "\n```\n"]
    parts += ["---\n", "# Your task\n", JUDGE_TASKS[stage].format(lid=lid, job=job)]
    return "\n".join(parts)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def judge_version(backend: str, cli: str, examples: str | None = None) -> dict:
    """What makes two judges the same judge (metrics plan §6.5). Grading and adjudicating share one
    version, so a panel's answer files can be checked against each other. `examples` is the worked
    examples file's text (None: no examples)."""
    return {"backend": backend, "model": JUDGE_MODEL, "effort": JUDGE_EFFORT, "cli_version": cli,
            "standard_sha256": _sha(STANDARD_V2.read_text(encoding="utf-8")),
            "instructions_sha256": _sha(JUDGE_SYSTEM + "".join(JUDGE_TASKS[s] for s in sorted(JUDGE_TASKS))),
            "examples_sha256": _sha(examples) if examples else None}


def judge_label(version: dict) -> str:
    if version.get("backend") in (None, "not recorded"):
        return "backend not recorded (Claude Code subagents)"
    ex = version.get("examples_sha256")
    return (f"{version['backend']} `{version['model']}`, effort {version['effort']}, CLI {version['cli_version']}, "
            f"standard {version['standard_sha256'][:8]}, instructions {version['instructions_sha256'][:8]}, "
            f"examples {ex[:8] if ex else 'none'}")


def panel_judge(records: dict[str, dict]) -> str:
    """The judge behind a judge-<tag>.json, for report headers."""
    found = []
    for r in records.values():
        found += [v for v in r.get("judge", [{"backend": "not recorded"}]) if v not in found]
    return "; ".join(judge_label(v) for v in found) or "none"


def _judge_bin() -> str:
    """The Claude Code binary: JUDGE_CLAUDE_BIN, else `claude` on PATH. It must be the executable
    itself: a .cmd shim runs through cmd.exe, which mangles the JSON schema's quotes."""
    name = os.environ.get("JUDGE_CLAUDE_BIN", "claude")
    path = shutil.which(name) or name
    if Path(path).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise SystemExit(f"{path} is a script shim: set JUDGE_CLAUDE_BIN to claude.exe")
    return path


def cli_version() -> str:
    try:
        out = subprocess.run([_judge_bin(), "--version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"Can't run Claude Code ({e}): set JUDGE_CLAUDE_BIN to claude.exe") from None
    return out.stdout.split()[0] if out.stdout.strip() else "unknown"


def _cli_command(schema: dict) -> list[str]:
    return [_judge_bin(), "-p", "--model", JUDGE_MODEL, "--effort", JUDGE_EFFORT,
            "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence", "--system-prompt", JUDGE_SYSTEM,
            "--output-format", "json", "--json-schema", json.dumps(schema)]


def run_claude_cli(prompt: str, schema: dict) -> tuple[dict, dict]:
    """One fresh judge on the owner's Claude subscription: headless `claude -p` with no tools, no
    setting sources (so no CLAUDE.md, hooks or memory), no MCP servers, no skills and no saved
    session, run from an empty folder, with the prompt on stdin. Probe 2026-10-29: only the
    account's email and the folder's path still reach it. Returns (answer, call facts)."""
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # the subscription, never API billing
    start = time.monotonic()
    try:
        proc = subprocess.run(_cli_command(schema), input=prompt, capture_output=True, text=True,
                              encoding="utf-8", cwd=JUDGE_DIR, env=env, timeout=JUDGE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise JudgeError(f"no answer in {JUDGE_TIMEOUT} s") from None
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise JudgeError(f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}") from None
    if out.get("is_error") or out.get("subtype") != "success":
        why = str(out.get("result") or out.get("subtype"))[:300]
        raise (UsageLimit if USAGE_LIMIT.search(why) else JudgeError)(why)
    models = sorted(out.get("modelUsage") or {})
    if JUDGE_MODEL not in models:  # Claude Code also makes a small side call on another model
        raise JudgeError(f"answered by {', '.join(models) or 'no model'}, not {JUDGE_MODEL}")
    answer = out.get("structured_output")
    if not isinstance(answer, dict):
        raise JudgeError("no structured answer")
    return answer, {"seconds": round(time.monotonic() - start), "models_used": models,
                    "notional_usd": out.get("total_cost_usd")}


BACKENDS = {"claude-cli": run_claude_cli}  # vertex: stage 8 step 9


def _check_judge_dir() -> None:
    JUDGE_DIR.mkdir(parents=True, exist_ok=True)
    if any(JUDGE_DIR.iterdir()):
        raise SystemExit(f"{JUDGE_DIR} must be empty: it is the judge's working folder")


def _answer_errors(stage: str, answer: dict, lid: str, key: str, disagreements: dict | None) -> list[str]:
    if stage == "grade":
        letters = answer.get("letters", {})
        if list(letters) != [lid]:
            return [f"answer is for {list(letters)}, not {lid}"]
        return validate(letters[lid], _reference(key))
    rulings = {r.get("item"): r for r in answer.get("rulings", [])}
    errs = [f"no ruling for {c['item']}" for c in disagreements["cells"] if c["item"] not in rulings]
    if "send_readiness" in rulings and str(rulings["send_readiness"].get("value", "")).strip() not in ("1", "2", "3", "4"):
        errs.append("send_readiness ruling must be 1-4")
    if "claims" in rulings and not isinstance(rulings["claims"].get("claims"), list):
        errs.append("the claims ruling has no claims list")
    return errs


def _judge_one(backend, version: dict, stage: str, panel: Path, m: dict, lid: str, path: Path,
               disagreements: dict | None, examples: dict[str, str] | None,
               stop: threading.Event) -> tuple[bool, str]:
    """Fresh judges for one answer file until one answers in the right shape (at most JUDGE_ATTEMPTS).
    A wrongly shaped answer is kept beside it as <lid>.rejected.json, to read. Once any judge hits
    the usage limit (`stop`), no other starts."""
    if stop.is_set():
        return False, "not started: usage limit"
    packet = (panel / "packets" / f"{lid}.md").read_text(encoding="utf-8")
    prompt = judge_prompt(stage, packet, lid, m["job"], disagreements,
                          examples_block(examples, skip=m["key"]) if examples else None)
    schema = grade_schema(lid) if stage == "grade" else ADJUDICATION_SCHEMA
    rejected, errs = path.with_suffix(".rejected.json"), []
    for _ in range(JUDGE_ATTEMPTS):
        try:
            answer, facts = backend(prompt, schema)
        except UsageLimit:
            stop.set()
            raise  # no retry: the run stops (cmd_v2_run)
        except JudgeError as e:
            errs = [str(e)]
            continue
        errs = _answer_errors(stage, answer, lid, m["key"], disagreements)
        path.parent.mkdir(parents=True, exist_ok=True)
        if errs:
            rejected.write_text(json.dumps({"errors": errs, "answer": answer}, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            continue
        path.write_text(json.dumps({**answer, "judge": {**version, **facts}}, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        rejected.unlink(missing_ok=True)
        if stage == "grade":
            ans = answer["letters"][lid]
            return True, f"grade {ans['send_readiness']['grade']}, {claim_counts(ans['claims'])} ({facts['seconds']} s)"
        return True, f"{len(answer['rulings'])} ruling(s) ({facts['seconds']} s)"
    return False, "; ".join(errs)


def cmd_v2_run(args) -> int:
    """Fill answers/<grader>/ (--stage grade) or adjudication/ (--stage adjudicate) with one fresh
    judge per file. A file already there is kept unless --redo, so a re-run only redoes failures."""
    panel, mapping = PANELS / args.tag, _mapping(args.tag)
    if args.stage == "grade":
        todo = [(lid, panel / "answers" / g / f"{lid}.json", None) for lid in sorted(mapping) for g in args.graders]
    else:
        todo = [(f.stem, panel / "adjudication" / f.name, json.loads(f.read_text(encoding="utf-8")))
                for f in sorted((panel / "disagreements").glob("L*.json"))]
    only, half = set(args.only or []), set(calibration_half(args.half)) if args.half else None
    todo = [t for t in todo if (not only or mapping[t[0]]["key"] in only) and (half is None or mapping[t[0]]["key"] in half)
            and (args.redo or not t[1].exists())]
    if not todo:
        print("Nothing to do: every answer is there (--redo replaces them)")
        return 0
    if args.backend == "claude-cli":
        _check_judge_dir()
    use = not args.no_examples and CALIBRATION_EXAMPLES.exists()
    examples = load_examples(CALIBRATION_EXAMPLES) if use else None
    version = judge_version(args.backend, cli_version() if args.backend == "claude-cli" else "-",
                            CALIBRATION_EXAMPLES.read_text(encoding="utf-8") if use else None)
    print(f"{len(todo)} fresh judge(s), {args.jobs} at a time. Judge: {judge_label(version)}")
    failed, limit, stop = [], None, threading.Event()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(_judge_one, BACKENDS[args.backend], version, args.stage, panel, mapping[lid],
                               lid, path, dis, examples, stop): path for lid, path, dis in todo}
        for fut in as_completed(futures):
            try:
                ok, line = fut.result()
            except UsageLimit as e:  # every judge still queued would fail the same way
                limit, ok, line = limit or str(e), False, "usage limit"
            print(f"  {'' if ok else 'FAILED '}{_rel(futures[fut])}: {line}")
            if not ok:
                failed.append(futures[fut])
    if limit:
        print(f"Stopped: {limit}. Run the same command again after the reset; answers already saved are kept.")
        return 1
    if failed:
        print(f"{len(failed)} of {len(todo)} failed: run the same command again for fresh judges")
        return 1
    print("Next: " + ("v2-check, then v2-disagreements" if args.stage == "grade" else "v2-merge"))
    return 0


# The calibration / check halves (stage 8 step 2; metrics plan §6.3 as changed 2026-10-29)

SPLIT_SEED = 20261029
CHECK_SIZE = 9
HALVES = ("calibration", "check")


def _relevance() -> dict[str, str]:
    """key -> Y / N / ? : the owner's Would apply label (candidates.csv), as letter_lab._v2_ads reads it."""
    split = json.loads(V2_SET_SPLIT.read_text(encoding="utf-8"))
    would = {}
    with CANDIDATES.open(encoding="utf-8-sig", newline="") as fh:  # saved by Excel: a BOM
        for r in csv.DictReader(fh):
            col = next((c for c in r if c.startswith("would_apply")), None)
            if col:
                would[str(r["job_id"]).strip()] = (r[col] or "").strip().upper()[:1]
    return {a["key"]: would.get(str(a["job_id"])) if would.get(str(a["job_id"])) in ("Y", "N") else "?"
            for side in ("dev", "test") for a in split.get(side, [])}


def calibration_split(owner: dict[str, dict], relevant: dict[str, str], check_size: int, seed: int) -> dict:
    """The owner's graded letters in two halves. A label only one letter carries (a grade given
    once, or the only untrue Y) can't be tested on a half, so that letter goes to calibration,
    where it is useful as an example. The rest are stratified by grade, then relevance: each
    stratum gives the check half its share of `check_size` (largest remainders, ties seeded),
    seeded random within the stratum."""
    grades = {g: sum(r["send_readiness"] == g for r in owner.values()) for g in "1234"}
    untrue = [k for k, r in owner.items() if _yn(r["untrue"]) == "Y"]
    forced: dict[str, list[str]] = {}
    for k, r in sorted(owner.items()):
        if grades[r["send_readiness"]] == 1:
            forced.setdefault(k, []).append(f"the only letter graded {r['send_readiness']}")
    if len(untrue) == 1:
        forced.setdefault(untrue[0], []).append("the only untrue letter")
    rest = sorted(set(owner) - set(forced))
    if not 0 < check_size <= len(rest):
        raise SystemExit(f"A check half of {check_size} needs at least that many letters; {len(rest)} left")
    rng = random.Random(seed)
    strata: dict[tuple[str, str], list[str]] = {}
    for k in rest:
        strata.setdefault((owner[k]["send_readiness"], relevant.get(k, "?")), []).append(k)
    order = sorted(strata)
    share = {s: len(strata[s]) * check_size / len(rest) for s in order}
    seats = {s: int(share[s]) for s in order}
    tie = {s: rng.random() for s in order}
    for s in sorted(order, key=lambda s: (seats[s] - share[s], tie[s]))[:check_size - sum(seats.values())]:
        seats[s] += 1
    check = []
    for s in order:
        ks = list(strata[s])
        rng.shuffle(ks)
        check += ks[:seats[s]]
    return {"forced": forced, "calibration": sorted(set(owner) - set(check)), "check": sorted(check),
            "strata": [{"grade": g, "relevant": rel, "letters": len(strata[(g, rel)]), "check": seats[(g, rel)]}
                       for g, rel in order]}


def calibration_half(name: str) -> list[str]:
    if not CALIBRATION_SPLIT.exists():
        raise SystemExit(f"No {_rel(CALIBRATION_SPLIT)}: run `v2-split <run>` first")
    return json.loads(CALIBRATION_SPLIT.read_text(encoding="utf-8"))[name]


def cmd_v2_split(args) -> int:
    """Write the halves once. They are fixed before any judge sees a letter, so they are never
    rewritten: a change of mind means a new decision in the plan, by hand."""
    if CALIBRATION_SPLIT.exists():
        raise SystemExit(f"{_rel(CALIBRATION_SPLIT)} exists and is fixed: the halves are never redrawn")
    path = V2_RUNS / args.run / "grades-owner.csv"
    owner, relevant = read_owner(path), _relevance()
    split = calibration_split(owner, relevant, args.check, args.seed)
    out = {"run": args.run, "owner_grades_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "seed": args.seed, "check_size": args.check, **split}
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_SPLIT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    for half in ("calibration", "check"):
        print(f"{half} ({len(split[half])}):")
        for k in split[half]:
            note = f"  <- {'; '.join(split['forced'][k])}" if k in split["forced"] else ""
            print(f"  {k}  grade {owner[k]['send_readiness']}, relevant {relevant.get(k, '?')}{note}")
    print(f"-> {_rel(CALIBRATION_SPLIT)}")
    return 0


def example_draft(key: str, run: str, o: dict) -> str:
    """One worked example (standard §10), drafted from the owner's grade with its reading left to
    write. sounds_ai is left out: the owner's tick is the phrase-count rule, which a judge must not
    learn (it would learn the count instead of hearing the letter)."""
    from app.llm.letter.rubric import word_count

    ad = json.loads((V2_ADS / f"{key}.json").read_text(encoding="utf-8"))
    rows = "\n".join(f"- {r['id']} [{r['importance']} · {r['letter_role']} · {r['status']}] {r['text']}"
                     for r in _reference(key)["requirements"] if r["letter_role"] != "not_for_letter")
    letter = _letter(run, key, V2_RUNS)
    ticks = ", ".join(t for t in TICKS if t not in ALARM_TICKS and _ticked(o.get(t))) or "none"
    untrue = "Y: " + (o.get("untrue_text") or "") if _yn(o["untrue"]) == "Y" else "N"
    return (f"{EXAMPLE_HEAD}{key}\n\n"
            f"Job: {ad['title']}, {ad.get('company') or '(recruiter)'}\n\nChecklist rows:\n{rows}\n\n"
            f"The letter ({word_count(letter)} words):\n\n```\n{letter}\n```\n\n"
            f"The candidate's grade: send-readiness **{o['send_readiness']}**. Untrue: {untrue}. "
            f"Reasons ticked: {ticks}.\n\nThe candidate's note: {o.get('note') or '(none)'}\n\n"
            f"Worked reading: {READING_TODO}\n")


def cmd_v2_examples(args) -> int:
    """Draft evals/work/v2/calibration/examples.md from the calibration half, once. The worked
    readings are then written by hand and confirmed by the owner; v2-run refuses the file until
    every one is written."""
    if CALIBRATION_EXAMPLES.exists():
        raise SystemExit(f"{_rel(CALIBRATION_EXAMPLES)} exists: edit it by hand")
    split = json.loads(CALIBRATION_SPLIT.read_text(encoding="utf-8")) if CALIBRATION_SPLIT.exists() else None
    if split is None:
        raise SystemExit(f"No {_rel(CALIBRATION_SPLIT)}: run `v2-split <run>` first")
    owner = read_owner(V2_RUNS / split["run"] / "grades-owner.csv")
    head = (f"# Worked examples (standard §10): the calibration half of `{split['run']}`\n\n"
            "Gitignored: real letters. Only calibration-half letters; v2-run never shows a letter its own.\n")
    blocks = [example_draft(k, split["run"], owner[k]) for k in split["calibration"]]
    CALIBRATION_EXAMPLES.write_text(head + "\n" + "\n".join(blocks), encoding="utf-8")
    print(f"{len(blocks)} examples drafted -> {_rel(CALIBRATION_EXAMPLES)} (write each worked reading)")
    return 0


# Probes (stage 8 step 6; metrics plan §6.6 as changed 2026-10-29): copies of letters with filler
# added (`pad`: no padded copy may grade higher than its original) or an untrue claim planted
# (`plant`: the judge must flag it). A probe run is a run folder like any other, so v2-pack packs
# it blind; its run.json says which run it copies and holds the spec.

PROBE_KINDS = ("pad", "plant")


def probe_letter(letter: str, after: str, text: str) -> str:
    """The letter with `text` inserted right after the one place `after` appears."""
    if letter.count(after) != 1:
        raise SystemExit(f"{after[:60]!r} must appear exactly once in the letter ({letter.count(after)} found)")
    at = letter.index(after) + len(after)
    return letter[:at] + text + letter[at:]


def cmd_v2_probe(args) -> int:
    """Write probe run <name> from a spec: [{"key", "kind": "pad"|"plant", "after", "text", "why"}]."""
    from app.llm.letter.rubric import MAX_WORDS, word_count

    out = V2_RUNS / args.name
    if out.exists():
        raise SystemExit(f"{_rel(out)} exists: probes are written once")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    letters = {}
    for p in spec:
        if p.get("kind") not in PROBE_KINDS or p["key"] in letters:
            raise SystemExit(f"{p.get('key')}: kind must be one of {PROBE_KINDS}, one probe per letter")
        letters[p["key"]] = probe_letter(_letter(args.run, p["key"], V2_RUNS), p["after"], p["text"])
        if p["kind"] == "pad" and word_count(letters[p["key"]]) > MAX_WORDS:
            raise SystemExit(f"{p['key']}: the padded copy is over {MAX_WORDS} words, so it would test the cap, "
                             "not length bias")
    (out / "letters").mkdir(parents=True)
    for key, text in letters.items():
        (out / "letters" / f"{key}.txt").write_text(text, encoding="utf-8")
    meta = {"run_id": args.name, "engine": "probe", "probe_of": args.run, "results": [{"key": k} for k in letters],
            "probes": spec}
    (out / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(letters)} probe letter(s) -> {_rel(out)}; pack them with `v2-pack <tag> {args.name}`")
    return 0


def probe_results(meta: dict, probe_judge: dict, original_judge: dict) -> list[dict]:
    """One result per probe: pad passes when the copy grades no higher than its original; plant
    passes when the judge labels a claim in the planted text untrue."""
    from app.llm.letter.tools.check_requirements import quote_in_letter

    out = []
    for p in meta["probes"]:
        rec = probe_judge.get(p["key"])
        if rec is None:
            out.append({**p, "passed": None, "result": "not judged"})
        elif p["kind"] == "pad":
            orig = original_judge.get(p["key"], {}).get("send_readiness")
            passed = None if orig is None else rec["send_readiness"] <= orig
            out.append({**p, "passed": passed, "result": f"copy {rec['send_readiness']}, original {orig or 'not judged'}"})
        else:
            hits = [c for c in rec["claims"] if c["label"] in UNTRUE and quote_in_letter(c["quote"], p["text"])]
            out.append({**p, "passed": bool(hits),
                        "result": "; ".join(f"{c['label']}: {c['quote']!r}" for c in hits) or "not flagged"})
    return out


def cmd_v2_probe_report(args) -> int:
    meta = json.loads((V2_RUNS / args.probe_run / "run.json").read_text(encoding="utf-8"))
    paths = [V2_RUNS / args.probe_run / f"judge-{args.tag}.json", V2_RUNS / meta["probe_of"] / f"judge-{args.original}.json"]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit("No " + ", ".join(_rel(p) for p in missing) + ": run v2-merge first")
    probe_judge, original_judge = (json.loads(p.read_text(encoding="utf-8")) for p in paths)
    judges = {panel_judge(probe_judge), panel_judge(original_judge)}
    if len(judges) > 1:  # a probe only means something against the same judge
        raise SystemExit("The probes and the originals were graded by different judges:\n  " + "\n  ".join(judges))
    results = probe_results(meta, probe_judge, original_judge)
    lines = [f"# Probes `{args.probe_run}` (judge `{args.tag}`, originals `{args.original}`)\n",
             f"Judge: {judges.pop()}.\n", "| Probe | Kind | Result | |", "|---|---|---|---|"]
    lines += [f"| {r['key']} | {r['kind']} | {r['result']} | "
              f"{'-' if r['passed'] is None else ('pass' if r['passed'] else '**fail**')} |" for r in results]
    out = PANELS / args.tag / f"probes-{args.probe_run}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = [r["key"] for r in results if r["passed"] is False]
    print(f"{len(results)} probes: " + ("all pass" if not failed else f"FAIL on {', '.join(failed)}") + f" -> {_rel(out)}")
    return 1 if failed else 0


# Agreement with the owner (metrics plan §6.4): counts, because the check half is 8-9 letters

def weighted_kappa(a: list[int], b: list[int], levels: int = 4) -> float | None:
    """Cohen's kappa with quadratic weights on a 1..levels scale: 1 = perfect, 0 = chance, and
    a near miss costs less than a far one. None when it is undefined (every grade the same)."""
    n = len(a)
    if n == 0:
        return None

    def w(i: int, j: int) -> float:
        return (i - j) ** 2 / (levels - 1) ** 2

    observed = sum(w(x, y) for x, y in zip(a, b)) / n
    pa = [a.count(i) / n for i in range(1, levels + 1)]
    pb = [b.count(i) / n for i in range(1, levels + 1)]
    expected = sum(w(i, j) * pa[i - 1] * pb[j - 1] for i in range(1, levels + 1) for j in range(1, levels + 1))
    return None if expected == 0 else 1 - observed / expected


def _ticked(v: str | None) -> bool:
    return (v or "").strip().lower() in ("y", "yes", "x", "1", "true")


def read_owner(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig") as fh:  # -sig: Excel saves CSVs with a BOM
        rows = {r["key"]: r for r in csv.DictReader(fh) if (r.get("send_readiness") or "").strip()}
    for key, r in rows.items():
        if r["send_readiness"].strip() not in ("1", "2", "3", "4"):
            raise SystemExit(f"{_rel(path)} {key}: send_readiness must be 1-4")
        if _yn(r.get("untrue")) not in ("Y", "N"):
            raise SystemExit(f"{_rel(path)} {key}: untrue must be Y or N")
    return rows


# The pass bar (eval-set plan Decision log 2026-10-29), fixed before any check-half run. Written for
# 9 letters and scaled: ready / not ready and D4b at least n - 2, within ±1 at least n - 1.
BASELINE_GRADE = 2  # the owner's most common grade at stage 7: a judge must beat answering it every time
MISSED_RECALL_BAR = 0.8
UNGATED_TICKS = ("generic", "weak_example", "too_long")  # base rates ~60%: 9 letters can't tell a judge from chance


def owner_missed_rows(note: str | None) -> list[str]:
    """The row ids in the owner's "Missed R5 (...) and R8 (...)" note (stage 7's convention)."""
    m = re.match(r"\s*Missed\s+([^;]*)", re.sub(r"\([^()]*\)", "", note or ""))
    return re.findall(r"\bR\d+[a-z]?\b", m.group(1)) if m else []


def _missed_agreement(keys: list[str], owner: dict, judge: dict, refs: dict[str, dict]) -> dict:
    """Row by row: the owner's missed rows (from the notes) against the rows the judge didn't see
    addressed. An owner row outside the missable set is listed as `stray` (a note to recheck)."""
    out = {"owner": 0, "caught": 0, "owner_only": [], "judge_only": [], "stray": [], "rows": 0}
    for k in keys:
        if k not in refs:
            continue
        can = set(missable_rows(refs[k]))
        own = set(owner_missed_rows(owner[k].get("note")))
        theirs = set(judge[k]["missed_rows"] if "missed_rows" in judge[k]
                     else missed_rows(refs[k], judge[k]["requirements"]))
        out["rows"] += len(can)
        out["owner"] += len(own & can)
        out["caught"] += len(own & can & theirs)
        out["owner_only"] += [(k, r) for r in sorted(own & can - theirs)]
        out["judge_only"] += [(k, r) for r in sorted(theirs - own)]
        out["stray"] += [(k, r) for r in sorted(own - can)]
    return out


def agreement(owner: dict[str, dict], judge: dict[str, dict], accepted: set[str] = frozenset(),
              refs: dict[str, dict] | None = None) -> dict:
    """The judge against the owner on the letters both graded. A judge "untrue" flag the owner
    accepted (`accepted`) was a real miss by the owner, so it counts as the owner's Y. With the
    reference checklists (`refs`), missed rows are compared row by row too."""
    keys = sorted(set(owner) & set(judge))
    o = [int(owner[k]["send_readiness"]) for k in keys]
    j = [judge[k]["send_readiness"] for k in keys]
    own_untrue = {k for k in keys if _yn(owner[k]["untrue"]) == "Y" or k in accepted}
    judge_untrue = {k for k in keys if judge[k]["untrue"] == "Y"}
    ticks = {}
    for t in TICKS:
        ov = {k for k in keys if _ticked(owner[k].get(t))}
        jv = {k for k in keys if judge[k]["ticks"][t]}
        ticks[t] = {"agree": len(keys) - len(ov ^ jv), "owner_only": sorted(ov - jv), "judge_only": sorted(jv - ov)}
    return {
        "n": len(keys),
        "exact": sum(x == y for x, y in zip(o, j)),
        "baseline_exact": sum(x == BASELINE_GRADE for x in o),
        "ready": sum((x >= 3) == (y >= 3) for x, y in zip(o, j)),
        "within_1": sum(abs(x - y) <= 1 for x, y in zip(o, j)),
        "kappa": weighted_kappa(o, j),
        "missed": None if refs is None else _missed_agreement(keys, owner, judge, refs),
        "pairs": {k: (x, y) for k, x, y in zip(keys, o, j)},
        "untrue": {"tp": len(own_untrue & judge_untrue), "fn": sorted(own_untrue - judge_untrue),
                   "tn": len(set(keys) - own_untrue - judge_untrue), "fp": sorted(judge_untrue - own_untrue)},
        "ticks": ticks,
    }


def pass_bar(ag: dict) -> list[tuple[str, str, str, bool | None]]:
    """(measure, result, bar, passed) per gated measure; passed is None when nothing tests it."""
    n, u, mi, d4b = ag["n"], ag["untrue"], ag["missed"], ag["ticks"]["no_personal_reason"]
    pos = u["tp"] + len(u["fn"])
    rows = [
        ("Send-readiness, exact", f"{ag['exact']}/{n}", f"more than always-{BASELINE_GRADE} ({ag['baseline_exact']}/{n})",
         ag["exact"] > ag["baseline_exact"]),
        ("Ready / not ready (3+ vs ≤ 2)", f"{ag['ready']}/{n}", f"at least {n - 2}", ag["ready"] >= n - 2),
        ("Send-readiness, within ±1", f"{ag['within_1']}/{n}", f"at least {n - 1}", ag["within_1"] >= n - 1),
        ("Untrue: caught the owner's flags (TPR)", f"{u['tp']}/{pos}", "all (planted claims test it too)",
         u["tp"] == pos if pos else None),
        ("No personal reason (D4b)", f"{d4b['agree']}/{n}", f"at least {n - 2}", d4b["agree"] >= n - 2),
    ]
    if mi is not None:
        rows.append(("Missed rows: the owner's, also missed by the judge", f"{mi['caught']}/{mi['owner']}",
                     f"at least {MISSED_RECALL_BAR:.0%}", mi["caught"] >= MISSED_RECALL_BAR * mi["owner"] if mi["owner"] else None))
    return rows


def _tick_use(t: str) -> str:
    if t in DERIVED_TICKS:
        return "derived (rows)" if t == "missed_important" else "derived (D4b)"
    return "alarm: the judge's ear vs the owner's count rule" if t in ALARM_TICKS else "reported, not gated"


def _agreement_md(run: str, tag: str, ag: dict, judge: dict[str, dict], owner: dict[str, dict]) -> str:
    n, u = ag["n"], ag["untrue"]
    neg = u["tn"] + len(u["fp"])
    kappa = "undefined" if ag["kappa"] is None else f"{ag['kappa']:.2f}"
    bar = pass_bar(ag)
    verdict = "PASS" if all(p is not False for *_, p in bar) else "FAIL"
    lines = [
        f"# Judge `{tag}` vs the owner, run `{run}` ({n} letters)\n",
        f"Judge: {panel_judge(judge)}.\n",
        "Counts, not percentages: the check half is only 8-9 letters (metrics plan §6.4). The bar is the "
        "eval-set plan's (Decision log 2026-10-29), written for 9 letters and scaled to these.\n",
        f"**Pass bar: {verdict}**\n",
        "| Measure | Result | Bar | |", "|---|---|---|---|",
        *[f"| {m} | {r} | {b} | {'-' if p is None else ('pass' if p else '**fail**')} |" for m, r, b, p in bar],
        f"| Untrue: agreed it was clean (TNR) | {u['tn']}/{neg} | each judge-only flag reviewed below | |",
        f"| Weighted kappa (quadratic) | {kappa} | {KAPPA_WARNING} | |",
        "", "## Send-readiness per letter (owner, judge)\n",
        *[f"- {k}: {o} vs {j}" + ("" if o == j else "  ←") for k, (o, j) in ag["pairs"].items()],
        "", "## Untrue\n",
        "Missed by the judge (must be fixed: it has to catch every flag the owner made):",
        *([f"- {k}: owner said {owner[k].get('untrue_text') or '(no text)'!r}" for k in u["fn"]] or ["- none"]),
        "", "Flagged by the judge only. Accept (it was a real miss) or reject each, then re-run with "
            "`--accept KEY ...` for the accepted ones:",
    ]
    for k in u["fp"]:
        lines.append(f"- {k}:")
        lines += [f"  - {c['label']}: {c['quote']!r} ({c.get('why', '')})"
                  for c in judge[k]["claims"] if c["label"] in UNTRUE]
    if not u["fp"]:
        lines.append("- none")
    mi = ag["missed"]
    if mi is not None:
        lines += ["", "## Missed rows\n",
                  f"Of {mi['rows']} missable rows (backed, essential or important, headline or mention), the owner's "
                  f"notes name {mi['owner']} as missed; the judge also missed {mi['caught']}.",
                  "", "The owner's, which the judge counted as addressed:",
                  *([f"- {k} {r}" for k, r in mi["owner_only"]] or ["- none"]),
                  "", "The judge's only (it found no quote addressing them):",
                  *([f"- {k} {r}" for k, r in mi["judge_only"]] or ["- none"])]
        if mi["stray"]:
            lines += ["", "In the owner's notes but not missable (recheck the note):",
                      *[f"- {k} {r}" for k, r in mi["stray"]]]
    lines += ["", "## Reason ticks\n", "| Tick | Agree | Owner only | Judge only | Use |", "|---|---|---|---|---|"]
    lines += [f"| {t} | {v['agree']}/{n} | {', '.join(v['owner_only']) or '-'} | {', '.join(v['judge_only']) or '-'} "
              f"| {_tick_use(t)} |" for t, v in ag["ticks"].items()]
    return "\n".join(lines) + "\n"


def cmd_v2_agreement(args) -> int:
    judge_path = V2_RUNS / args.run / f"judge-{args.tag}.json"
    if not judge_path.exists():
        raise SystemExit(f"No {_rel(judge_path)}: run `v2-merge {args.tag}` first")
    judge = json.loads(judge_path.read_text(encoding="utf-8"))
    owner = read_owner(V2_RUNS / args.run / args.owner)
    if args.half:
        owner = {k: v for k, v in owner.items() if k in set(calibration_half(args.half))}
    if args.only:
        owner = {k: v for k, v in owner.items() if k in set(args.only)}
    refs = {k: _reference(k) for k in judge if (V2_REF / f"{k}.json").exists()}
    accepted = set(args.accept or [])  # plus every flag the owner accepted earlier, so none is forgotten
    if ACCEPTED_UNTRUE.exists():
        accepted |= set(json.loads(ACCEPTED_UNTRUE.read_text(encoding="utf-8")))
    ag = agreement(owner, judge, accepted, refs)
    if not ag["n"]:
        raise SystemExit("No letter graded by both the owner and the judge")
    out = PANELS / args.tag / f"agreement-{args.run}{'-' + args.half if args.half else ''}.md"
    out.write_text(_agreement_md(args.run, args.tag, ag, judge, owner), encoding="utf-8")
    u = ag["untrue"]
    failed = [m for m, _, _, p in pass_bar(ag) if p is False]
    print(f"{ag['n']} letters: send-readiness exact {ag['exact']}/{ag['n']} (always-{BASELINE_GRADE} "
          f"{ag['baseline_exact']}), ready {ag['ready']}/{ag['n']}, within ±1 {ag['within_1']}/{ag['n']}; "
          f"untrue TPR {u['tp']}/{u['tp'] + len(u['fn'])}, TNR {u['tn']}/{u['tn'] + len(u['fp'])} -> {_rel(out)}")
    print("Pass bar: " + ("PASS" if not failed else "FAIL on " + "; ".join(failed)))
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

    p = sub.add_parser("v2-pack", help="v2: one blind packet per letter, for one fresh judge each")
    p.add_argument("tag")
    p.add_argument("runs", nargs="+", help="v2 run(s) in evals/work/v2/runs/")
    p.add_argument("--only", nargs="+", metavar="KEY")
    p.add_argument("--half", choices=HALVES, help="just one half of the calibration split")
    p.add_argument("--seed", type=int, default=20261026)
    p.set_defaults(fn=cmd_v2_pack)
    p = sub.add_parser("v2-split", help="v2: the calibration / check halves of a run's owner grades (once)")
    p.add_argument("run")
    p.add_argument("--check", type=int, default=CHECK_SIZE, help="letters in the check half")
    p.add_argument("--seed", type=int, default=SPLIT_SEED)
    p.set_defaults(fn=cmd_v2_split)
    p = sub.add_parser("v2-run", help="v2: one fresh judge per answer file (headless, never the repo)")
    p.add_argument("tag")
    p.add_argument("--backend", choices=sorted(BACKENDS), default="claude-cli")
    p.add_argument("--stage", choices=("grade", "adjudicate"), default="grade")
    p.add_argument("--graders", nargs="+", choices=GRADERS, default=list(GRADERS))
    p.add_argument("--only", nargs="+", metavar="KEY", help="just these ads")
    p.add_argument("--half", choices=HALVES, help="just one half of the calibration split")
    p.add_argument("--jobs", type=int, default=3, help="judges at a time")
    p.add_argument("--redo", action="store_true", help="replace answers already there")
    p.add_argument("--no-examples", action="store_true", help="the standard alone, without the worked examples")
    p.set_defaults(fn=cmd_v2_run)
    p = sub.add_parser("v2-examples", help="v2: draft the worked examples from the calibration half (once)")
    p.set_defaults(fn=cmd_v2_examples)
    p = sub.add_parser("v2-probe", help="v2: a probe run of padded or planted copies of a run's letters (once)")
    p.add_argument("name", help="the probe run's folder in evals/work/v2/runs/")
    p.add_argument("run", help="the run whose letters it copies")
    p.add_argument("--spec", required=True, help='JSON list of {"key", "kind": "pad"|"plant", "after", "text", "why"}')
    p.set_defaults(fn=cmd_v2_probe)
    p = sub.add_parser("v2-probe-report", help="v2: did the padded copies grade no higher, and were the plants caught")
    p.add_argument("tag", help="the panel that graded the probe run")
    p.add_argument("probe_run")
    p.add_argument("--original", required=True, help="the panel that graded the originals")
    p.set_defaults(fn=cmd_v2_probe_report)
    one_grader = "the graders that answered (A alone: a one-grader calibration run, nothing to adjudicate)"
    p = sub.add_parser("v2-check", help="v2: every answer present and well formed; quote problems listed")
    p.add_argument("tag")
    p.add_argument("--graders", nargs="+", choices=GRADERS, default=list(GRADERS), help=one_grader)
    p.set_defaults(fn=cmd_v2_check)
    p = sub.add_parser("v2-disagreements", help="v2: one file per letter whose graders split")
    p.add_argument("tag")
    p.set_defaults(fn=cmd_v2_disagreements)
    p = sub.add_parser("v2-merge", help="v2: final answers into each run's judge-<tag>.json + checklist doubts")
    p.add_argument("tag")
    p.add_argument("--graders", nargs="+", choices=GRADERS, default=list(GRADERS), help=one_grader)
    p.set_defaults(fn=cmd_v2_merge)
    p = sub.add_parser("v2-pairs", help="v2: A/B pair packets, both orders, one fresh judge each")
    p.add_argument("tag")
    p.add_argument("run_a", help="the baseline")
    p.add_argument("run_b", help="the new version")
    p.add_argument("--only", nargs="+", metavar="KEY")
    p.add_argument("--seed", type=int, default=20261026)
    p.set_defaults(fn=cmd_v2_pairs)
    p = sub.add_parser("v2-picks", help="v2: win rate of the new run (order-dependent picks = ties)")
    p.add_argument("tag")
    p.set_defaults(fn=cmd_v2_picks)
    p = sub.add_parser("v2-agreement", help="v2: the merged judge vs the owner's grades (counts)")
    p.add_argument("tag")
    p.add_argument("run")
    p.add_argument("--owner", default="grades-owner.csv", help="owner grades file in the run folder")
    p.add_argument("--only", nargs="+", metavar="KEY", help="just these letters")
    p.add_argument("--half", choices=HALVES, help="just one half of the calibration split")
    p.add_argument("--accept", nargs="+", metavar="KEY",
                   help="letters whose judge-only untrue flag the owner accepted")
    p.set_defaults(fn=cmd_v2_agreement)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
