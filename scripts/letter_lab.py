"""Cover-letter eval harness (docs/cover-letter-loop-plan.md §9, Phase 2).

Runs a fixed set of real job ads through a letter engine, against YOUR real
profile, in a separate scratch database so real.db is never written to. Then it
scores each letter on the pass/fail rubric (evals/rubric.md): code checks the
mechanical items, you grade the judgement items in a CSV.

    python scripts/letter_lab.py snapshot          # 1. copy ads from real.db + app.db into evals/jobs/
    python scripts/letter_lab.py prepare           # 2. build evals/eval.db, extract + score every ad, pick the set
    python scripts/letter_lab.py run               # 3. write one letter per set job (engine: oneshot)
    #    ... fill in evals/runs/<run>/grades.csv (Y/N per column) ...
    python scripts/letter_lab.py report <run>      # 4. merge code checks + your grades -> evals/results/<run>.md

Privacy: evals/jobs/, evals/eval.db, evals/set.json and evals/runs/ hold ad text,
your profile and letters written as you — all gitignored. Only the summary in
evals/results/ (titles, pass/fail, cost) is meant to be committed.

Cost: `prepare` is 2 small-tier calls per ad (~$0.002 each); `run` is one
strong-tier letter per set job (a few cents each). Every call lands in
eval.db's llm_usage table, which is where the per-run cost comes from.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
JOBS_DIR = EVALS / "jobs"
RUNS_DIR = EVALS / "runs"
RESULTS_DIR = EVALS / "results"
EVAL_DB = EVALS / "eval.db"
SET_FILE = EVALS / "set.json"

# Point the whole app layer at the scratch DB BEFORE anything from app/ is
# imported: db.py reads DATABASE_URL at import, and client.py's usage logging and
# budget guard use the same engine — so eval spend is logged to eval.db, not real.db.
os.environ["DATABASE_URL"] = f"sqlite:///{EVAL_DB.as_posix()}"
os.environ["APP_ENV"] = "test"
sys.path.insert(0, str(ROOT))

SET_MAX = 15
SET_MIN_SCORE = 50  # below this, nobody would write a letter — not a useful eval case
PROFILE_ID = 1  # one profile per DB (CLAUDE.md "TWO ENVIRONMENTS")

_SNAPSHOT_COLUMNS = (
    "source", "source_job_id", "url", "title", "company", "location", "work_type",
    "salary", "classification", "subclassification", "raw_description",
)


def _key(source: str, source_job_id: str) -> str:
    return f"{source}-{source_job_id}"


# ---------------------------------------------------------------------------
# 1. snapshot
# ---------------------------------------------------------------------------
def cmd_snapshot(args) -> int:
    """Copy every real Seek ad with a description out of the source DBs.

    Read-only on the sources (plain sqlite3, only the columns we need, so a
    source DB on an older migration still works). Synthetic `source='test'`
    jobs are skipped: the eval set should be real ads.
    """
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for src in args.sources:
        path = ROOT / src
        if not path.exists():
            print(f"  (skip {src}: not found)")
            continue
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT {', '.join(_SNAPSHOT_COLUMNS)} FROM job_listings "
            "WHERE source = 'seek' AND raw_description IS NOT NULL AND raw_description != ''"
        ).fetchall()
        con.close()
        for row in rows:
            data = dict(row)
            out = JOBS_DIR / f"{_key(data['source'], data['source_job_id'])}.json"
            if out.exists() and not args.force:
                skipped += 1
                continue
            data["snapshot_from"] = src
            data["snapshot_at"] = datetime.date.today().isoformat()
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            written += 1
    total = len(list(JOBS_DIR.glob("*.json")))
    print(f"Snapshot: {written} written, {skipped} already there. {total} ads in {JOBS_DIR.relative_to(ROOT)}/")
    return 0


# ---------------------------------------------------------------------------
# 2. prepare
# ---------------------------------------------------------------------------
def _build_eval_db(profile_source: Path) -> None:
    """Fresh eval.db = a copy of the profile DB with every job-side row removed.

    sqlite3's backup API copies consistently even if the API server has the
    source open. The copy is then migrated to head, and job_listings is emptied
    (cascading to job_skills, matches and cover_letters), so only the profile
    survives. The source file itself is never written to.
    """
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    src = sqlite3.connect(f"file:{profile_source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(EVAL_DB)
    src.backup(dst)
    src.close()
    dst.close()

    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import JobListing, LlmUsage, SavedSearch

    with SessionLocal() as db:
        db.execute(delete(JobListing))  # FK cascade: job_skills, matches, cover_letters
        db.execute(delete(SavedSearch))
        db.execute(delete(LlmUsage))
        db.commit()


def _choose_set(scored: list[tuple[str, int]]) -> list[str]:
    """Up to SET_MAX ads spread across score bands, so the set covers strong
    matches AND real gaps rather than just the top of the list."""
    eligible = sorted((s for s in scored if s[1] >= SET_MIN_SCORE), key=lambda s: -s[1])
    bands = [
        [k for k, s in eligible if s >= 85],
        [k for k, s in eligible if 75 <= s < 85],
        [k for k, s in eligible if SET_MIN_SCORE <= s < 75],
    ]
    chosen: list[str] = []
    # Round-robin across bands until full, so no band crowds out the others.
    while len(chosen) < SET_MAX and any(bands):
        for band in bands:
            if band and len(chosen) < SET_MAX:
                chosen.append(band.pop(0))
    return chosen


def cmd_prepare(args) -> int:
    ads = sorted(JOBS_DIR.glob("*.json"))
    if not ads:
        print("No ads in evals/jobs/ — run `letter_lab.py snapshot` first.")
        return 1
    profile_db = ROOT / args.profile_db
    if not profile_db.exists():
        print(f"Profile DB {args.profile_db} not found.")
        return 1

    print(f"Building {EVAL_DB.relative_to(ROOT)} from {args.profile_db} (profile only) ...")
    _build_eval_db(profile_db)

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm.client import DailyQuotaError
    from app.llm.extract import extract_job
    from app.llm.match import match_job
    from app.models import JobListing, Match, Profile

    with SessionLocal() as db:
        profile = db.get(Profile, PROFILE_ID)
        if profile is None:
            print(f"No profile {PROFILE_ID} in {args.profile_db}.")
            return 1
        print(f"Profile: {profile.name}")
        for path in ads:
            data = json.loads(path.read_text(encoding="utf-8"))
            db.add(JobListing(**{c: data.get(c) for c in _SNAPSHOT_COLUMNS}))
        db.commit()
        jobs = db.scalars(select(JobListing).order_by(JobListing.id)).all()
        job_ids = [(j.id, _key(j.source, j.source_job_id), j.title) for j in jobs]

    print(f"Extracting + scoring {len(job_ids)} ads (small tier, ~{len(job_ids) * 2} calls) ...")
    scored: list[tuple[str, int]] = []
    for job_id, key, title in job_ids:
        try:
            extract_job(job_id)
            match_job(job_id, PROFILE_ID)
        except DailyQuotaError as exc:
            print(f"Stopped: {exc}")
            return 1
        with SessionLocal() as db:
            m = db.scalar(select(Match).where(Match.job_id == job_id, Match.user_id == PROFILE_ID))
            score = int(m.score) if m and m.score is not None else -1
        scored.append((key, score))
        print(f"  {score:>4}  {title[:60]}")

    if SET_FILE.exists() and not args.reselect:
        print(f"\nKept the existing {SET_FILE.relative_to(ROOT)} (pass --reselect to rebuild it).")
    else:
        chosen = _choose_set(scored)
        SET_FILE.write_text(json.dumps({"jobs": chosen}, indent=2), encoding="utf-8")
        print(f"\nEval set: {len(chosen)} ads -> {SET_FILE.relative_to(ROOT)} (edit it to swap ads in/out).")
    return 0


# ---------------------------------------------------------------------------
# 3. run
# ---------------------------------------------------------------------------
def _engine_oneshot(job_id: int):
    """Today's production path: one complete_text call (cover_letter.py)."""
    from app.llm.cover_letter import generate_cover_letter

    cl = generate_cover_letter(job_id, PROFILE_ID, force=True, bypass_threshold=True)
    return cl.generated_content if cl else None


ENGINES = {"oneshot": _engine_oneshot}


def _run_cost(job_id: int, since: datetime.datetime) -> dict:
    """Tokens and cost of the letter-writing calls for one job in this run."""
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import LlmUsage

    with SessionLocal() as db:
        row = db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmUsage.cost_usd), 0),
                func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.output_tokens + LlmUsage.thinking_tokens), 0),
            ).where(LlmUsage.job_id == job_id, LlmUsage.created_at >= since, LlmUsage.task != "extract",
                    LlmUsage.task != "match")
        ).one()
    return {"calls": row[0], "cost_usd": float(row[1]), "input_tokens": int(row[2]), "output_tokens": int(row[3])}


def cmd_run(args) -> int:
    if not EVAL_DB.exists() or not SET_FILE.exists():
        print("Run `letter_lab.py prepare` first.")
        return 1
    keys = json.loads(SET_FILE.read_text(encoding="utf-8"))["jobs"]

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm import client
    from app.llm.client import BudgetExceededError, DailyQuotaError
    from app.llm.letter.rubric import AUTO_ITEMS, HUMAN_ITEMS, auto_checks
    from app.models import JobListing, Match

    run_id = args.label or f"{datetime.datetime.now():%Y%m%d-%H%M}-{args.engine}"
    run_dir = RUNS_DIR / run_id
    (run_dir / "letters").mkdir(parents=True, exist_ok=True)
    engine = ENGINES[args.engine]
    print(f"Run {run_id}: engine={args.engine}, model={client.model_for('strong')}, {len(keys)} jobs")

    results = []
    for key in keys:
        source, source_job_id = key.split("-", 1)
        with SessionLocal() as db:
            job = db.scalar(select(JobListing).where(
                JobListing.source == source, JobListing.source_job_id == source_job_id))
            if job is None:
                print(f"  {key}: not in eval.db (re-run prepare?) — skipped")
                continue
            m = db.scalar(select(Match).where(Match.job_id == job.id, Match.user_id == PROFILE_ID))
            job_id, title, company = job.id, job.title, job.company
            score = int(m.score) if m and m.score is not None else None

        since = datetime.datetime.now(datetime.timezone.utc)
        start = time.monotonic()
        try:
            text = engine(job_id)
        except (DailyQuotaError, BudgetExceededError) as exc:
            print(f"Stopped: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 — one bad job shouldn't sink the run
            print(f"  {key}: FAILED — {str(exc)[:160]}")
            results.append({"key": key, "title": title, "company": company, "score": score, "error": str(exc)})
            continue
        seconds = round(time.monotonic() - start, 1)
        text = text or ""
        (run_dir / "letters" / f"{key}.txt").write_text(text, encoding="utf-8")
        checks = auto_checks(text)
        results.append({
            "key": key, "title": title, "company": company, "score": score,
            "seconds": seconds, **_run_cost(job_id, since), "auto": checks,
        })
        fails = [k for k in AUTO_ITEMS if not checks[k]]
        print(f"  {score!s:>4}  {title[:48]:48}  {checks['words']:>3}w  {seconds:>5}s  "
              f"{'auto ok' if not fails else 'auto FAIL: ' + ', '.join(fails)}")

    meta = {
        "run_id": run_id, "engine": args.engine, "strong_model": client.model_for("strong"),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"), "results": results,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    with (run_dir / "grades.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "title", *HUMAN_ITEMS, "notes"])
        for r in results:
            if "error" not in r:
                w.writerow([r["key"], r["title"], *[""] * len(HUMAN_ITEMS), ""])
    print(f"\nLetters: {run_dir.relative_to(ROOT)}/letters/")
    print(f"Grade them: fill Y/N in {(run_dir / 'grades.csv').relative_to(ROOT)} (see evals/rubric.md),")
    print(f"then: python scripts/letter_lab.py report {run_id}")
    return 0


# ---------------------------------------------------------------------------
# 4. report
# ---------------------------------------------------------------------------
def _yn(value: str) -> bool | None:
    v = (value or "").strip().lower()
    if v in ("y", "yes", "1", "true", "pass"):
        return True
    if v in ("n", "no", "0", "false", "fail"):
        return False
    return None


def cmd_report(args) -> int:
    from app.llm.letter.rubric import AUTO_ITEMS, HUMAN_ITEMS

    run_dir = RUNS_DIR / args.run_id
    meta_path = run_dir / "run.json"
    if not meta_path.exists():
        print(f"No run {args.run_id} in {RUNS_DIR.relative_to(ROOT)}/")
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    grades: dict[str, dict] = {}
    grades_path = run_dir / "grades.csv"
    if grades_path.exists():
        with grades_path.open(encoding="utf-8") as fh:
            grades = {row["key"]: row for row in csv.DictReader(fh)}

    ok = [r for r in meta["results"] if "error" not in r]
    # Re-run the code checks on the saved letters rather than trusting the numbers
    # stored at run time, so changing a rubric limit applies to old runs too.
    from app.llm.letter.rubric import auto_checks

    for r in ok:
        letter = run_dir / "letters" / f"{r['key']}.txt"
        if letter.exists():
            r["auto"] = auto_checks(letter.read_text(encoding="utf-8"))
    lines = [
        f"# Letter eval: {meta['run_id']}",
        "",
        f"Engine `{meta['engine']}`, strong model `{meta['strong_model']}`, run {meta['created_at']}. "
        f"{len(ok)} letters ({len(meta['results']) - len(ok)} failed). Rubric: evals/rubric.md.",
        "",
        "| Score | Job | Words | " + " | ".join(AUTO_ITEMS + HUMAN_ITEMS) + " | Pass | Cost | Time |",
        "|---|---|---|" + "---|" * (len(AUTO_ITEMS) + len(HUMAN_ITEMS)) + "---|---|---|",
    ]
    item_pass = {i: [0, 0] for i in AUTO_ITEMS + HUMAN_ITEMS}  # [passed, graded]
    full_pass = graded_letters = 0
    for r in ok:
        cells, all_pass, ungraded = [], True, False
        for item in AUTO_ITEMS:
            v = bool(r["auto"][item])
            item_pass[item][0] += v
            item_pass[item][1] += 1
            all_pass &= v
            cells.append("✓" if v else "✗")
        g = grades.get(r["key"], {})
        for item in HUMAN_ITEMS:
            v = _yn(g.get(item, ""))
            if v is None:
                ungraded = True
                cells.append("?")
                continue
            item_pass[item][0] += v
            item_pass[item][1] += 1
            all_pass &= v
            cells.append("✓" if v else "✗")
        if not ungraded:
            graded_letters += 1
            full_pass += all_pass
        verdict = "?" if ungraded else ("✓" if all_pass else "✗")
        job = f"{r['title']} ({r['company'] or '?'})".replace("|", "/")
        lines.append(
            f"| {r['score']} | {job} | {r['auto']['words']} | " + " | ".join(cells)
            + f" | {verdict} | ${r['cost_usd']:.4f} | {r['seconds']}s |"
        )

    n = len(ok) or 1
    lines += [
        "",
        "## Per-item pass rate",
        "",
        "| Item | Passed |",
        "|---|---|",
        *[f"| {i} | {p}/{t} |" for i, (p, t) in item_pass.items()],
        "",
        "## Comparison-table row (plan §9)",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Rubric pass rate | {full_pass}/{graded_letters} fully graded letters"
        + (f" ({len(ok) - graded_letters} not yet graded)" if graded_letters < len(ok) else "") + " |",
        f"| Avg tokens / run | {sum(r['input_tokens'] for r in ok) // n} in, {sum(r['output_tokens'] for r in ok) // n} out+thinking |",
        f"| Avg cost / run | ${sum(r['cost_usd'] for r in ok) / n:.4f} |",
        f"| Avg time / run | {sum(r['seconds'] for r in ok) / n:.1f}s |",
        "| Runs that hit the budget cap | n/a (one-shot) |",
        f"| Runs where the user had to fix a factual error | "
        f"{item_pass['no_unsupported_claims'][1] - item_pass['no_unsupported_claims'][0]}"
        f" of {item_pass['no_unsupported_claims'][1]} graded |",
    ]
    notes = [(r["key"], grades.get(r["key"], {}).get("notes", "").strip()) for r in ok]
    notes = [(k, t) for k, t in notes if t]
    if notes:
        lines += ["", "## Grader notes", "", *[f"- `{k}`: {t}" for k, t in notes]]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{meta['run_id']}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshot", help="copy real Seek ads into evals/jobs/")
    p.add_argument("--sources", nargs="+", default=["real.db", "app.db"])
    p.add_argument("--force", action="store_true", help="overwrite existing snapshots")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("prepare", help="build evals/eval.db, extract + score, choose the set")
    p.add_argument("--profile-db", default="real.db", help="DB whose profile 1 the letters are written for")
    p.add_argument("--reselect", action="store_true", help="rebuild evals/set.json even if it exists")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("run", help="write one letter per set job")
    p.add_argument("--engine", choices=sorted(ENGINES), default="oneshot")
    p.add_argument("--label", help="run id (default: timestamp-engine)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="merge code checks + grades into evals/results/<run>.md")
    p.add_argument("run_id")
    p.set_defaults(fn=cmd_report)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
