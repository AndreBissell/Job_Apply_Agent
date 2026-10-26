"""The reference checklist for the frozen eval set (docs/letter-metrics-plan.md §7, build step 1).
No LLM calls.

One fixed, owner-corrected checklist per split ad: each requirement's importance, letter role,
status (supported / partial / gap) and evidence pointers. Coverage (M1, M2) and best evidence
(M3) are scored against it, so it is built once and never re-analysed per run.

Source: the stage-3 analysis run (letter_runs.state in evals/work/v2/eval.db, read-only), which
evals/work/v2/runs/stage3/checks.csv flattens; the two are checked to agree row for row. The
run keeps fields the CSV drops (skill, note, implied_by), so each reference row loads straight
back into app.llm.letter.state.Requirement and guardrails.must_cover / listing_only work on it.

Corrections come from two places, and every one is confirmed by the owner before it is used:
  inputs.json   the owner's corrections: labelling comments drafted into field changes, and
                calls made in chat (hand-written, gitignored: they describe the real profile),
                plus `non_technical_evidence`. One may also split a row that bundles two asks
                (`add`: the new rows, inserted after it). An owner correction replaces any
                pattern change proposed for the same row.
  patterns      three rules in this file, applied to every split ad (owner, 2026-10-23/26):
    (a) years and seniority asks, judged against relevant paid work only (jobs, minus the
        non-technical ones; projects, thesis and coursework are not years):
          years held / years asked (a range's lower number): under 1/2 gap, 1/2 partial, 1+ supported
          "senior" / "lead" / "principal": supported if a relevant job title says so, else gap
        A gap keeps what the analysis cited under `related_evidence`, so the judge can tell
        honest "related experience" framing from a claim of the years.
    (b) a technical-support ask backed by non-technical work           -> drop that evidence
                                                                         (gap if none is left)
    (c) a duty of the job ("Interrogate the pricing systems") tagged as an
        essential gap                                                   -> important

Everything lives in evals/work/v2/reference/ (gitignored: real ads and profile):
    inputs.json      hand-written: comment corrections, no-change notes
    proposals.json   written by `propose`; `decide` fills in each yes/no
    ads/<key>.json   written by `build`: the frozen checklist, one per ad (read-only)
    MANIFEST.json    written by `build`: sources, counts, decisions, a SHA-256 of every file

    python scripts/eval_reference.py propose              # (re)write proposals.json and print them
    python scripts/eval_reference.py show 193             # print one ad's proposals
    python scripts/eval_reference.py decide 193:R3=yes 90:R4=no [--note "the owner's reason"]
    python scripts/eval_reference.py build                # refuses while any decision is open
    python scripts/eval_reference.py verify               # check nothing has changed
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "evals" / "work" / "v2"
WORK_DB = WORK / "eval.db"
os.environ["DATABASE_URL"] = f"sqlite:///{WORK_DB.as_posix()}"  # only read
os.environ["APP_ENV"] = "test"
sys.path.insert(0, str(ROOT))

from app.llm.letter.guardrails import listing_only, must_cover  # noqa: E402
from app.llm.letter.state import Requirement  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles are cp1252; Seek titles aren't
    sys.stdout.reconfigure(encoding="utf-8")

SET_DIR = ROOT / "evals" / "sets" / "v2"
SPLIT = SET_DIR / "split.json"
CHECKS = WORK / "runs" / "stage3" / "checks.csv"
REF_DIR = WORK / "reference"
INPUTS = REF_DIR / "inputs.json"
PROPOSALS = REF_DIR / "proposals.json"
ADS_DIR = REF_DIR / "ads"
MANIFEST = REF_DIR / "MANIFEST.json"
ANALYZE_ENGINE = "eval-analyze"  # letter_lab.py analyze (scripts/eval_tag.py reads the same run)
# What a correction may change. related_evidence: what the analysis cited for an item that is
# now a gap; not backing (guardrails read `evidence` as backing), only context for the judge.
FIELDS = ("importance", "letter_role", "status", "evidence", "related_evidence")
SPLIT_FIELDS = FIELDS + ("text",)  # a split rewrites the parent's text to the part it keeps
ROW_FIELDS = ("id", "text", "importance", "letter_role", "status", "evidence")  # an added row needs these
M2_WEIGHTS = {"essential": 4, "important": 2, "nice_to_have": 1}  # metrics plan, M2

# (a) an explicit minimum of years, or a seniority level, in the requirement's own words.
# Group 1 is the lower number of a range ("2-3 years" -> 2).
_YEARS = re.compile(r"\b(\d+)\s*(?:\+|[-–]\s*\d+\s*\+?)?\s*(?:years?|yrs)\b", re.I)
_SENIOR = re.compile(r"\b(?:senior|lead|principal)\b(?=\s+(?:and\s+\w+\s+)?(?:positions?|roles?|level|"
                     r"engineers?|developers?|commercial))", re.I)
_SENIOR_TITLE = re.compile(r"\b(?:senior|lead|principal)\b", re.I)
# "work with the Lead Developer" names a colleague, not a level the candidate needs
_SENIOR_COLLEAGUE = re.compile(r"\b(?:with|alongside|under|from|by|to)\s+(?:the\s+|our\s+|a\s+|your\s+)?"
                               r"(?:senior|lead|principal)\b", re.I)
_ALTERNATIVE = re.compile(r"\bor\b[^.;]*$", re.I)  # "a degree ... or 1-2 years": the years are one branch
# (b) the ask is technical / IT support
_TECH_SUPPORT = re.compile(
    r"help ?desk|service desk|technical support|\bIT support|desktop support|customer support issues"
    r"|support (?:tickets|requests|incidents)|production support|application support", re.I)
# (c) the requirement is worded as a duty: it starts with what the job holder will do
_DUTY_VERBS = frozenset("""
    analyse analyze assist build collaborate conduct coordinate create deliver design develop
    ensure help implement interrogate join lead log maintain manage monitor own participate perform
    plan prepare produce provide review run support test work write
""".split())


# ---------------------------------------------------------------------------
# The three pattern rules: each takes one stage-3 requirement (a dict) and returns the field
# changes it proposes, or None. Pure functions, so tests can feed them fake rows.
# ---------------------------------------------------------------------------
def years_status(held_months: int, asked_years: int) -> str:
    """The owner's ratio rule (2026-10-26): under half the years asked is a gap."""
    ratio = held_months / (12 * asked_years) if asked_years else 1.0
    return "supported" if ratio >= 1 else "partial" if ratio >= 0.5 else "gap"


def rule_years(r: dict, held_months: int, titles: list[str]) -> dict | None:
    """(a) A years ask by the ratio rule; a seniority ask by the titles of relevant jobs held.
    Skipped where the years are only one branch of an "or" ("a degree or 1-2 years")."""
    if r["letter_role"] == "not_for_letter":
        return None
    m = _YEARS.search(r["text"])
    if m and not _ALTERNATIVE.search(r["text"][:m.start()]):
        status = years_status(held_months, int(m.group(1)))
    elif _SENIOR.search(r["text"]) and not _SENIOR_COLLEAGUE.search(r["text"]):
        status = "supported" if any(_SENIOR_TITLE.search(t) for t in titles) else "gap"
    else:
        return None
    if status == r["status"] or (status != "gap" and not r["evidence"]):
        return None  # unchanged, or nothing cited that could back it
    if status == "gap":
        return {"status": "gap", "evidence": [], "related_evidence": list(r["evidence"])}
    return {"status": status}


def _is_under(pointer: str, prefixes: list[str]) -> bool:
    return any(pointer == p or pointer.startswith(p + "#") for p in prefixes)


def rule_support(r: dict, non_technical: list[str]) -> dict | None:
    """(b) customer service is not evidence for technical / IT support: drop it, and the item
    becomes a gap if nothing else backs it."""
    if r["letter_role"] == "not_for_letter" or not _TECH_SUPPORT.search(r["text"]):
        return None
    kept = [p for p in r["evidence"] if not _is_under(p, non_technical)]
    if len(kept) == len(r["evidence"]):
        return None
    return {"evidence": kept} if kept else {"status": "gap", "evidence": []}


def rule_duty(r: dict) -> dict | None:
    """(c) a duty of the job is the employer's work, not a must-have the candidate lacks."""
    if not (r["importance"] == "essential" and r["letter_role"] in ("headline", "mention")
            and r["status"] == "gap"):
        return None
    first = re.match(r"[A-Za-z]+", r["text"].strip())
    if first and first.group(0).lower() in _DUTY_VERBS:
        return {"importance": "important"}
    return None


PATTERNS = {"a": "years or seniority ask, judged against relevant paid work",
            "b": "non-technical work as evidence for technical support",
            "c": "a duty of the job tagged as an essential gap"}


# ---------------------------------------------------------------------------
# Reading the sources
# ---------------------------------------------------------------------------
def read_split(path: Path = SPLIT) -> dict[str, dict]:
    """key -> {job_id, side, scenario, title, company} for every ad in the dev/test split."""
    s = json.loads(path.read_text(encoding="utf-8"))
    return {a["key"]: {"job_id": a["job_id"], "side": side, "scenario": a["scenario"],
                       "title": a["title"], "company": a["company"]}
            for side in ("dev", "test") for a in s[side]}


def read_stage3(db_path: Path, keys: set[str]) -> dict[str, dict]:
    """key -> {run_id, requirements} from each ad's latest finished analysis run."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT j.source || '-' || j.source_job_id, lr.id, lr.state
               FROM job_listings j JOIN matches m ON m.job_id = j.id AND m.user_id = 1
               JOIN letter_runs lr ON lr.id = (
                   SELECT id FROM letter_runs WHERE match_id = m.id AND engine = ? AND status = 'done'
                   ORDER BY id DESC LIMIT 1)""", (ANALYZE_ENGINE,)).fetchall()
    finally:
        con.close()
    out = {k: {"run_id": rid, "requirements": json.loads(st)["requirements"]}
           for k, rid, st in rows if k in keys}
    missing = sorted(keys - set(out))
    if missing:
        raise SystemExit(f"no finished analysis run for: {missing}")
    return out


def _csv_pointers(cell: str) -> list[str]:
    return re.findall(r"\[([a-z]+:[^\]]+)\]", cell or "")


def check_against_csv(stage3: dict[str, dict], path: Path = CHECKS) -> list[str]:
    """Every split ad's rows in checks.csv match the run: same ids, verdicts and pointers."""
    with path.open(encoding="utf-8", newline="") as f:
        csv_rows = {(r["key"], r["id"]): r for r in csv.DictReader(f) if r["key"] in stage3}
    problems = []
    run_ids = {(k, r["id"]) for k, s in stage3.items() for r in s["requirements"]}
    if set(csv_rows) != run_ids:
        problems.append(f"row sets differ: only in CSV {sorted(set(csv_rows) - run_ids)[:5]}, "
                        f"only in run {sorted(run_ids - set(csv_rows))[:5]}")
    for k, s in stage3.items():
        for r in s["requirements"]:
            c = csv_rows.get((k, r["id"]))
            if not c:
                continue
            for f in ("text", "importance", "letter_role", "status"):
                if c[f] != r[f]:
                    problems.append(f"{k} {r['id']} {f}: CSV {c[f]!r} vs run {r[f]!r}")
            if _csv_pointers(c["evidence"]) != r["evidence"]:
                problems.append(f"{k} {r['id']} evidence differs")
    return problems


def relevant_work(db_path: Path, non_technical: list[str]) -> tuple[int, list[str]]:
    """(months, titles) of the frozen profile's paid jobs, minus the non-technical ones.
    Projects, thesis and coursework are not years of experience (owner, 2026-10-26)."""
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, title, start_date, end_date FROM experiences "
                           "WHERE user_id = 1 AND experience_type = 'job'").fetchall()
    finally:
        con.close()
    months, titles = 0, []
    for eid, title, start, end in rows:
        if _is_under(f"experience:{eid}", non_technical) or not start:
            continue
        s = datetime.date.fromisoformat(start)
        e = datetime.date.fromisoformat(end) if end else datetime.date.today()
        months += (e.year - s.year) * 12 + (e.month - s.month)
        titles.append(title)
    return months, titles


def read_inputs(path: Path = INPUTS) -> dict:
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} is missing (the hand-written comment corrections)")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("non_technical_evidence", [])
    data.setdefault("corrections", [])
    data.setdefault("no_change", [])
    return data


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------
def _merge(into: dict, change: dict, pid: str) -> None:
    for f, v in change.items():
        if f in into and into[f] != v:
            raise SystemExit(f"{pid}: two sources propose different {f}: {into[f]!r} vs {v!r}")
        into[f] = v


def propose(split: dict[str, dict], stage3: dict[str, dict], inputs: dict,
            work: tuple[int, list[str]]) -> list[dict]:
    """One proposal per requirement that any source would change, sources merged.
    work = relevant_work(): the months and job titles rule (a) judges against."""
    by_job = {a["job_id"]: k for k, a in split.items()}
    found: dict[str, dict] = {}

    def add(key: str, r: dict, change: dict, source: dict) -> dict:
        pid = f"{split[key]['job_id']}:{r['id']}"
        p = found.setdefault(pid, {"id": pid, "key": key, "req_id": r["id"], "set": {}, "sources": []})
        _merge(p["set"], change, pid)
        p["sources"].append(source)
        return p

    for key, s in stage3.items():
        for r in s["requirements"]:
            for name, change in (("a", rule_years(r, *work)),
                                 ("b", rule_support(r, inputs["non_technical_evidence"])),
                                 ("c", rule_duty(r))):
                if change:
                    add(key, r, change, {"pattern": name, "why": PATTERNS[name]})
    owned: set[str] = set()
    for c in inputs["corrections"]:
        key = by_job.get(c["job_id"])
        if key is None:
            raise SystemExit(f"inputs.json: job {c['job_id']} is not in the split")
        reqs = stage3[key]["requirements"]
        r = next((x for x in reqs if x["id"] == c["id"]), None)
        if r is None:
            raise SystemExit(f"inputs.json: job {c['job_id']} has no {c['id']}")
        pid = f"{c['job_id']}:{c['id']}"
        bad = set(c.get("set", {})) - set(SPLIT_FIELDS if c.get("add") else FIELDS)
        if bad:
            raise SystemExit(f"inputs.json: {pid} may not change {sorted(bad)}")
        if pid in found and pid not in owned:  # the owner's call replaces the pattern's
            found[pid]["set"] = {}
            for s in found[pid]["sources"]:
                s["replaced_by_owner"] = True
        owned.add(pid)
        source = {k: c[k] for k in ("comment", "owner") if k in c} | {"why": c["why"]}
        p = add(key, r, c.get("set", {}), source)
        for new in c.get("add", []):
            missing = [f for f in ROW_FIELDS if f not in new]
            if missing or new["id"] in {x["id"] for x in reqs} | {x["id"] for x in p.get("add", [])}:
                raise SystemExit(f"inputs.json: {pid} added row needs {ROW_FIELDS} and a new id")
            p.setdefault("add", []).append({"theme": r.get("theme") or "", "skill": r.get("skill") or "",
                                            "implied_by": list(r.get("implied_by") or []),
                                            "note": None, "user_decision": None, **new,
                                            "split_from": r["id"]})

    out = []
    for p in found.values():
        r = next(x for x in stage3[p["key"]]["requirements"] if x["id"] == p["req_id"])
        p["set"] = {f: v for f, v in p["set"].items() if r.get(f) != v}  # drop no-op fields
        if p["set"] or p.get("add"):
            p["was"] = {f: r.get(f) for f in p["set"]}
            p["decision"] = None
            out.append(p)
    return sorted(out, key=lambda p: (split[p["key"]]["side"], int(p["id"].split(":")[0]),
                                      int(p["req_id"][1:])))


def keep_decisions(new: list[dict], old: list[dict]) -> list[dict]:
    """A re-run keeps a decision only where the proposed change is exactly the same."""
    prev = {p["id"]: p for p in old}
    for p in new:
        o = prev.get(p["id"])
        if o and o["set"] == p["set"] and o.get("add") == p.get("add"):
            p["decision"] = o.get("decision")
            if o.get("note"):
                p["note"] = o["note"]
    return new


# ---------------------------------------------------------------------------
# Applying and building
# ---------------------------------------------------------------------------
def apply(requirements: list[dict], proposals: list[dict]) -> list[dict]:
    """The stage-3 rows with every accepted change applied. A changed row keeps the original
    values under `stage3` and the reasons under `corrections`, so the key is auditable. Rows a
    split adds come straight after their parent, marked `split_from`."""
    accepted = {p["req_id"]: p for p in proposals if p["decision"] == "yes"}
    out = []
    for r in requirements:
        row = dict(r)
        p = accepted.get(r["id"])
        if p:
            row.update(p["set"])
            row["stage3"] = dict(p["was"])
            row["corrections"] = p["sources"]
        out.append(row)
        for new in (p or {}).get("add", []):
            out.append({**new, "corrections": p["sources"]})
    return out


def counts(rows: list[dict]) -> dict:
    reqs = [Requirement.model_validate(r) for r in rows]
    scored = [r for r in reqs if r.status in ("supported", "partial")
              and r.letter_role in ("headline", "mention")]
    return {
        "requirements": len(reqs),
        **{s: sum(1 for r in reqs if r.status == s) for s in ("supported", "partial", "gap")},
        "must_cover": len(must_cover(SimpleNamespace(requirements=reqs))),
        "must_have_gaps": sum(1 for r in reqs if r.needs_user),
        "listing_only": sum(1 for r in reqs if listing_only(r)),
        "m2_weight_total": sum(M2_WEIGHTS[r.importance] for r in scored),
    }


def evidence_catalog(rows: list[dict]) -> dict[str, dict]:
    """Every cited pointer -> its profile text and kind (work / project / qualification /
    skill / summary): M3 ranks evidence by kind."""
    from sqlalchemy.orm import selectinload

    from app.db import SessionLocal
    from app.llm.letter.state import ProfileIndex
    from app.models import Profile

    with SessionLocal() as db:
        prof = db.get(Profile, 1, options=[selectinload(Profile.qualifications),
                                           selectinload(Profile.experiences),
                                           selectinload(Profile.skills)])
        index = ProfileIndex(prof)
        exp_kind = {str(e.id): ("work" if e.experience_type == "job" else "project")
                    for e in prof.experiences}
    out = {}
    for p in sorted({p for r in rows for p in r["evidence"] + r.get("related_evidence", [])}):
        text = index.resolve(p)
        if text is None:
            raise SystemExit(f"pointer {p} does not resolve in the frozen profile")
        kind, _, rest = p.partition(":")
        if kind == "experience":
            kind = exp_kind[rest.split("#")[0]]
        elif kind == "profile":
            kind = "summary"
        out[p] = {"kind": kind, "text": text}
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() or None


def build(split: dict[str, dict], stage3: dict[str, dict], proposals: list[dict]) -> dict:
    open_ = [p["id"] for p in proposals if p["decision"] not in ("yes", "no")]
    if open_:
        raise SystemExit(f"{len(open_)} proposals still need a yes/no: {', '.join(open_)}")
    if MANIFEST.exists():
        raise SystemExit(f"{MANIFEST.relative_to(ROOT)} exists: the reference is frozen and never rewritten")
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    entries, totals = [], {}
    for key in sorted(split, key=lambda k: split[k]["job_id"]):
        a = split[key]
        mine = [p for p in proposals if p["key"] == key]
        rows = apply(stage3[key]["requirements"], mine)
        c = counts(rows)
        data = {"key": key, "job_id": a["job_id"], "title": a["title"], "company": a["company"],
                "split": a["side"], "scenario": a["scenario"],
                "source": {"run": "stage3", "letter_run_id": stage3[key]["run_id"]},
                "counts": c, "requirements": rows, "evidence": evidence_catalog(rows)}
        path = ADS_DIR / f"{key}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(path, stat.S_IREAD)
        entries.append({"key": key, "job_id": a["job_id"], "split": a["side"], "file": f"ads/{path.name}",
                        "sha256": sha256(path), "changed": sum(1 for p in mine if p["decision"] == "yes")})
        for f, v in c.items():
            totals[f] = totals.get(f, 0) + v
    manifest = {
        "made_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "sources": {"eval_db": WORK_DB.name, "checks_csv_sha256": sha256(CHECKS),
                    "split_sha256": sha256(SPLIT), "inputs_sha256": sha256(INPUTS),
                    "proposals_sha256": sha256(PROPOSALS)},
        "proposals": {"total": len(proposals),
                      "yes": sum(1 for p in proposals if p["decision"] == "yes"),
                      "no": sum(1 for p in proposals if p["decision"] == "no")},
        "totals": totals, "ads": entries,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(MANIFEST, stat.S_IREAD)
    return manifest


def verify() -> list[str]:
    if not MANIFEST.exists():
        return ["no MANIFEST.json: not built yet"]
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    problems = [f"{a['file']}: hash differs" for a in m["ads"]
                if not (REF_DIR / a["file"]).exists() or sha256(REF_DIR / a["file"]) != a["sha256"]]
    extra = {p.name for p in ADS_DIR.glob("*.json")} - {Path(a["file"]).name for a in m["ads"]}
    problems += [f"ads/{n}: not in the manifest" for n in sorted(extra)]
    return problems


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------
def _fmt(field: str, v) -> str:
    if field in ("evidence", "related_evidence"):
        return ", ".join(v) if v else "none"
    return str(v)


def print_ad(job_id: int, split: dict, stage3: dict, proposals: list[dict], inputs: dict) -> None:
    key = next(k for k, a in split.items() if a["job_id"] == job_id)
    a = split[key]
    print(f"\n#{job_id} {a['title']} ({a['company']}) - {a['side']}, scenario {a['scenario']}")
    for p in (p for p in proposals if p["key"] == key):
        r = next(x for x in stage3[key]["requirements"] if x["id"] == p["req_id"])
        print(f"  {p['id']}  {r['text']}")
        print(f"     now: {r['importance']} / {r['letter_role']} / {r['status']}; evidence: {_fmt('evidence', r['evidence'])}")
        if p["set"]:
            print("     proposed: " + "; ".join(f"{f} {_fmt(f, p['was'][f])} -> {_fmt(f, v)}"
                                             for f, v in p["set"].items()))
        for new in p.get("add", []):
            print(f"     + new row {new['id']}: {new['text']}")
            print(f"         {new['importance']} / {new['letter_role']} / {new['status']}; "
                  f"evidence: {_fmt('evidence', new['evidence'])}")
        for s in p["sources"]:
            if "pattern" in s:
                line = f"pattern ({s['pattern']}) {s['why']}"
            else:
                line = (f"comment \"{s['comment']}\"" if "comment" in s else f"owner: {s['owner']}") + f" ({s['why']})"
            print(f"     from: {line}" + (" [replaced by the owner's call]" if s.get("replaced_by_owner") else ""))
        print(f"     decision: {p['decision'] or '-'}" + (f" ({p['note']})" if p.get("note") else ""))
    for n in (n for n in inputs["no_change"] if n["job_id"] == job_id):
        print(f"  no change: comment \"{n['comment']}\": {n['why']}")


def _load_all():
    split = read_split()
    stage3 = read_stage3(WORK_DB, set(split))
    problems = check_against_csv(stage3)
    if problems:
        raise SystemExit("stage-3 run and checks.csv disagree:\n  " + "\n  ".join(problems[:20]))
    return split, stage3, read_inputs()


def _read_proposals() -> list[dict]:
    if not PROPOSALS.exists():
        raise SystemExit("no proposals.json: run `propose` first")
    return json.loads(PROPOSALS.read_text(encoding="utf-8"))["proposals"]


def _write_proposals(proposals: list[dict]) -> None:
    PROPOSALS.write_text(json.dumps({"proposals": proposals}, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("propose")
    sh = sub.add_parser("show")
    sh.add_argument("job_ids", nargs="*", type=int)
    de = sub.add_parser("decide")
    de.add_argument("answers", nargs="+", help="<job_id>:<R#>=yes|no")
    de.add_argument("--note", help="the owner's reason, kept with each answer given")
    sub.add_parser("build")
    sub.add_parser("verify")
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        problems = verify()
        print("OK: every reference file matches its manifest hash" if not problems
              else "FAILED:\n  " + "\n  ".join(problems))
        return 1 if problems else 0
    if args.cmd == "decide":
        proposals = _read_proposals()
        by_id = {p["id"]: p for p in proposals}
        for ans in args.answers:
            pid, _, val = ans.partition("=")
            if pid not in by_id or val not in ("yes", "no"):
                raise SystemExit(f"bad answer {ans!r}: expected <job_id>:<R#>=yes|no for a listed proposal")
            by_id[pid]["decision"] = val
            if args.note:
                by_id[pid]["note"] = args.note
        _write_proposals(proposals)
        left = sum(1 for p in proposals if p["decision"] is None)
        print(f"Recorded {len(args.answers)}; {left} still open")
        return 0

    split, stage3, inputs = _load_all()
    if args.cmd == "propose":
        REF_DIR.mkdir(parents=True, exist_ok=True)
        work = relevant_work(WORK_DB, inputs["non_technical_evidence"])
        print(f"Rule (a) judges against {work[0]} months of relevant paid work ({', '.join(work[1])})")
        proposals = propose(split, stage3, inputs, work)
        if PROPOSALS.exists():
            proposals = keep_decisions(proposals, _read_proposals())
        _write_proposals(proposals)
        jobs = sorted({split[p["key"]]["job_id"] for p in proposals} | {n["job_id"] for n in inputs["no_change"]})
        for j in jobs:
            print_ad(j, split, stage3, proposals, inputs)
        n_rows = sum(len(s["requirements"]) for s in stage3.values())
        print(f"\n{len(proposals)} proposals on {len({p['key'] for p in proposals})} of {len(split)} ads "
              f"({n_rows} rows; checks.csv agrees with the run)")
        return 0
    if args.cmd == "show":
        proposals = _read_proposals()
        for j in args.job_ids or sorted({split[p["key"]]["job_id"] for p in proposals}):
            print_ad(j, split, stage3, proposals, inputs)
        return 0
    if args.cmd == "build":
        m = build(split, stage3, _read_proposals())
        print(f"Built {len(m['ads'])} reference checklists in {ADS_DIR.relative_to(ROOT)} "
              f"({m['proposals']['yes']} corrections accepted, {m['proposals']['no']} rejected)")
        print("Totals: " + ", ".join(f"{k} {v}" for k, v in m["totals"].items()))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
