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
    #   ... one fresh judge per packet writes answers/<A|B>/<lid>.json ...
    python scripts/grading_panel.py v2-check <tag>                 # missing / malformed answers
    python scripts/grading_panel.py v2-disagreements <tag>         # -> disagreements/<lid>.json
    #   ... one fresh adjudicator per file writes adjudication/<lid>.json ...
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


def _profile_text(profile) -> str:
    from app.llm.letter.state import ProfileIndex

    index = ProfileIndex(profile)
    facts = "\n".join(f"- {k}: {v}" for k, v in index.facts.items())
    return (
        "# The candidate's profile (the ONLY source of truth about the candidate)\n\n"
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
GRADERS = ("A", "B")
CHECKLIST = ("D2a", "D2b", "D2c", "D3a", "D3b", "D4a", "D4b", "D5a", "D5b", "D5c",
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


def _keys_for(runs: list[str], only: list[str] | None) -> list[str]:
    keys = [k for k in _keys(runs[0], V2_RUNS) if all(k in set(_keys(r, V2_RUNS)) for r in runs[1:])]
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
    keys = _keys_for(args.runs, args.only)
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
    out |= {f"tick:{t}": "Y" if ans["ticks"][t] else "N" for t in TICKS}
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


def _answers(tag: str) -> tuple[dict, dict[str, dict[str, dict]], dict[str, list[str]]]:
    """(mapping, {lid: {grader: checked answer}}, {lid: quote problems}); refuses on a missing or
    malformed answer, naming every one, so those judges can be re-run."""
    mapping = _mapping(tag)
    out, problems, bad = {}, {}, []
    for lid, m in sorted(mapping.items()):
        ref, letter = _reference(m["key"]), _letter(m["run"], m["key"], V2_RUNS)
        out[lid] = {}
        for g in GRADERS:
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
    _, answers, problems = _answers(args.tag)
    print(f"All {len(answers)} letters answered by graders {' and '.join(GRADERS)}; shapes valid.")
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


def final_v2(tag: str) -> dict[str, dict]:
    """lid -> the final answer: agreed cells from grader A, split cells from the adjudicator."""
    import copy

    mapping, answers, problems = _answers(tag)
    finals = {}
    for lid, by in answers.items():
        a, b = cells(by["A"]), cells(by["B"])
        split = [item for item in a if a[item] != b.get(item)]
        rulings = {}
        path = PANELS / tag / "adjudication" / f"{lid}.json"
        if path.exists():
            rulings = {r["item"]: r for r in json.loads(path.read_text(encoding="utf-8"))["rulings"]}
        missing = [item for item in split if item not in rulings]
        if missing:
            raise SystemExit(f"{lid} has split cells with no ruling ({', '.join(missing)}): "
                             "run its adjudicator first")
        final = copy.deepcopy(by["A"])
        for item in split:
            _apply(final, rulings[item], by)
        m = mapping[lid]
        final, found = check_quotes(final, _letter(m["run"], m["key"], V2_RUNS))
        claims = final["claims"]
        gap_rows = {r["id"] for r in _reference(m["key"])["requirements"] if r["status"] == "gap"}
        finals[lid] = {
            **m, "letter": lid,
            "send_readiness": final["send_readiness"]["grade"],
            "untrue": "Y" if any(c["label"] in UNTRUE for c in claims) else "N",
            "counts": {label: sum(c["label"] == label for c in claims) for label in LABELS}
            | {"gap_fabrications": sum(c["label"] == "fabricated" and c.get("requirement") in gap_rows
                                       for c in claims)},
            "ticks": final["ticks"],
            "checklist": {d: _yn(final["checklist"][d]["answer"]) for d in CHECKLIST},
            "requirements": final["requirements"],
            "claims": claims,
            "checklist_doubts": [{**d, "grader": g} for g in GRADERS for d in by[g].get("checklist_doubts", [])],
            "quote_problems": problems.get(lid, []) + [f"final: {p}" for p in found],
            "adjudicated": split,
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
    finals = final_v2(args.tag)
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


def agreement(owner: dict[str, dict], judge: dict[str, dict], accepted: set[str] = frozenset()) -> dict:
    """The judge against the owner on the letters both graded. A judge "untrue" flag the owner
    accepted (`accepted`) was a real miss by the owner, so it counts as the owner's Y."""
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
        "within_1": sum(abs(x - y) <= 1 for x, y in zip(o, j)),
        "kappa": weighted_kappa(o, j),
        "pairs": {k: (x, y) for k, x, y in zip(keys, o, j)},
        "untrue": {"tp": len(own_untrue & judge_untrue), "fn": sorted(own_untrue - judge_untrue),
                   "tn": len(set(keys) - own_untrue - judge_untrue), "fp": sorted(judge_untrue - own_untrue)},
        "ticks": ticks,
    }


def _agreement_md(run: str, tag: str, ag: dict, judge: dict[str, dict], owner: dict[str, dict]) -> str:
    n, u = ag["n"], ag["untrue"]
    pos, neg = u["tp"] + len(u["fn"]), u["tn"] + len(u["fp"])
    kappa = "undefined" if ag["kappa"] is None else f"{ag['kappa']:.2f}"
    lines = [
        f"# Judge `{tag}` vs the owner, run `{run}` ({n} letters)\n",
        "Counts, not percentages: the check half is only 8-9 letters (metrics plan §6.4).\n",
        "| Measure | Result | Aim |", "|---|---|---|",
        f"| Send-readiness, exact | {ag['exact']}/{n} | |",
        f"| Send-readiness, within ±1 | {ag['within_1']}/{n} | all but one |",
        f"| Weighted kappa (quadratic) | {kappa} | {KAPPA_WARNING} |",
        f"| Untrue: caught the owner's flags (TPR) | {u['tp']}/{pos} | all |",
        f"| Untrue: agreed it was clean (TNR) | {u['tn']}/{neg} | |",
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
    lines += ["", "## Reason ticks\n", "| Tick | Agree | Owner only | Judge only |", "|---|---|---|---|"]
    lines += [f"| {t} | {v['agree']}/{n} | {', '.join(v['owner_only']) or '-'} | {', '.join(v['judge_only']) or '-'} |"
              for t, v in ag["ticks"].items()]
    return "\n".join(lines) + "\n"


def cmd_v2_agreement(args) -> int:
    judge_path = V2_RUNS / args.run / f"judge-{args.tag}.json"
    if not judge_path.exists():
        raise SystemExit(f"No {_rel(judge_path)}: run `v2-merge {args.tag}` first")
    judge = json.loads(judge_path.read_text(encoding="utf-8"))
    owner = read_owner(V2_RUNS / args.run / args.owner)
    if args.only:
        owner = {k: v for k, v in owner.items() if k in set(args.only)}
    ag = agreement(owner, judge, set(args.accept or []))
    if not ag["n"]:
        raise SystemExit("No letter graded by both the owner and the judge")
    out = PANELS / args.tag / f"agreement-{args.run}.md"
    out.write_text(_agreement_md(args.run, args.tag, ag, judge, owner), encoding="utf-8")
    u = ag["untrue"]
    print(f"{ag['n']} letters: send-readiness exact {ag['exact']}/{ag['n']}, within ±1 {ag['within_1']}/{ag['n']}; "
          f"untrue TPR {u['tp']}/{u['tp'] + len(u['fn'])}, TNR {u['tn']}/{u['tn'] + len(u['fp'])} -> {_rel(out)}")
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
    p.add_argument("--seed", type=int, default=20261026)
    p.set_defaults(fn=cmd_v2_pack)
    p = sub.add_parser("v2-check", help="v2: every answer present and well formed; quote problems listed")
    p.add_argument("tag")
    p.set_defaults(fn=cmd_v2_check)
    p = sub.add_parser("v2-disagreements", help="v2: one file per letter whose graders split")
    p.add_argument("tag")
    p.set_defaults(fn=cmd_v2_disagreements)
    p = sub.add_parser("v2-merge", help="v2: final answers into each run's judge-<tag>.json + checklist doubts")
    p.add_argument("tag")
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
    p.add_argument("--only", nargs="+", metavar="KEY", help="just these letters (the check half)")
    p.add_argument("--accept", nargs="+", metavar="KEY",
                   help="letters whose judge-only untrue flag the owner accepted")
    p.set_defaults(fn=cmd_v2_agreement)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
