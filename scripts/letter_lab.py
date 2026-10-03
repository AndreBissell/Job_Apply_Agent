"""Cover-letter eval harness (docs/cover-letter-loop-plan.md §9, Phase 2).

Runs a fixed set of real job ads through a letter engine, against YOUR real
profile, in a separate scratch database so real.db is never written to. Then it
scores each letter on the pass/fail rubric (evals/rubric.md): code checks the
mechanical items, you grade the judgement items in a CSV.

    python scripts/letter_lab.py snapshot          # 1. copy ads from real.db + app.db into evals/jobs/
    python scripts/letter_lab.py prepare           # 2. build evals/eval.db, extract + score every ad, pick the set
    python scripts/letter_lab.py run               # 3. write one letter per set job (engine: oneshot)
    python scripts/letter_lab.py read <run> [--against <run>]   # all letters in one readable letters.md
    #    ... fill in evals/runs/<run>/grades.csv (Y/N per column) ...
    python scripts/letter_lab.py report <run>      # 4. merge code checks + your grades -> evals/results/<run>.md

    python scripts/letter_lab.py analyze           # Phase 3: analyze_job + match_profile on the set -> review.md + checks.csv
    #    ... fill in evals/runs/<analysis-run>/checks.csv (Y/N per column) ...
    python scripts/letter_lab.py analysis-report <run>   # agreement rates -> evals/results/<run>.md

    python scripts/letter_lab.py run --engine tools      # Phase 5: draft + check tools, one revision -> also drafts.md + states/
    python scripts/letter_lab.py run --engine tools --only seek-94419843   # one job, as a smoke test
    python scripts/letter_lab.py plant <tools-run>        # planted-claim test for check_claims -> evals/results/plant-<run>.md

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
import textwrap
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
# Daily spend cap for eval.db only (user decision 2026-10-03): a full engine run
# over the set costs ~$3-4, which the app's $5 default would cut off mid-run.
# Re-applied on every command because `prepare` rebuilds eval.db from real.db,
# whose profile carries the app default. real.db's own cap is unchanged.
EVAL_DAILY_BUDGET_USD = 10.0

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
    _set_eval_budget()


def _set_eval_budget() -> None:
    from app.db import SessionLocal
    from app.preferences import set_preferences

    with SessionLocal() as db:
        set_preferences(db, PROFILE_ID, {"llm_daily_budget_usd": EVAL_DAILY_BUDGET_USD})


def _migrate_eval_db() -> None:
    """Bring an existing eval.db up to the current schema (a no-op when already there),
    so a migration added after `prepare` doesn't break `run` / `analyze`."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    _set_eval_budget()


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


def _engine_oneshot_styled(job_id: int):
    """The same one call with Phase 4's style guide + the user's voice in the system
    prompt. Same inputs otherwise, so the difference is style + voice alone."""
    from app.llm.cover_letter import generate_cover_letter

    cl = generate_cover_letter(job_id, PROFILE_ID, force=True, bypass_threshold=True, styled=True)
    return cl.generated_content if cl else None


TOOLS_MAX_REVISIONS = 1  # Phase 5 exercises revise_letter once; Phase 6's workflow sets the real loop


def _engine_tools(job_id: int):
    """Phase 5's draft + check tools in a fixed order (not yet Phase 6's workflow):
    analyze_job (cached) -> match_profile -> unanswered must-have gaps treated as
    leave_out (there is no ask_user UI yet) -> generate_letter -> the three checks
    -> one revise_letter if any failed -> the checks again. Returns the latest
    draft plus the full state, so `plant` can reuse letters that passed check_claims.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm.client import BudgetExceededError, DailyQuotaError
    from app.llm.letter import guardrails
    from app.llm.letter.runner import execute_tool, finish_run, start_run
    from app.llm.letter.state import JobInfo, LetterState, UserDecision
    from app.llm.letter.tools.analyze_job import analyze_job
    from app.llm.letter.tools.check_claims import check_claims
    from app.llm.letter.tools.check_requirements import check_requirements
    from app.llm.letter.tools.generate import generate_letter
    from app.llm.letter.tools.match_profile import match_profile
    from app.llm.letter.tools.revise import revise_letter
    from app.llm.letter.tools.style_lint import style_lint
    from app.models import JobListing, Match

    with SessionLocal() as db:
        job = db.get(JobListing, job_id)
        match = db.scalar(select(Match).where(Match.job_id == job_id, Match.user_id == PROFILE_ID))
        state = LetterState(profile_id=PROFILE_ID, job=JobInfo(job_id=job.id, title=job.title, company=job.company))
        ctx = start_run(db, match.id, "workflow", state)

        def step(name, fn):
            stop = state.budget_exceeded()
            if stop:
                raise RuntimeError(stop)
            r = execute_tool(ctx, state, name, fn)
            if not r.ok:
                raise RuntimeError(f"{name}: {r.error}")

        def checks():
            for name, fn in (("check_claims", check_claims), ("check_requirements", check_requirements),
                             ("style_lint", style_lint)):
                step(name, fn)

        try:
            step("analyze_job", analyze_job)
            step("match_profile", match_profile)
            for r in state.pending_gaps():
                r.user_decision = UserDecision(choice="leave_out", answer="eval: unanswered gap left out")
            step("generate_letter", generate_letter)
            checks()
            for _ in range(TOOLS_MAX_REVISIONS):
                if guardrails.can_revise(state) is not None:
                    break  # everything passed, or the draft cap
                step("revise_letter", revise_letter)
                checks()
        except (DailyQuotaError, BudgetExceededError):
            finish_run(ctx, state, "budget_stopped")
            raise
        except Exception:
            finish_run(ctx, state, "failed")
            raise
        finish_run(ctx, state, "done" if guardrails.can_finish(state) is None else "budget_stopped")
        return {"text": state.latest_draft.text, "state": state.model_dump(mode="json"), "letter_run_id": ctx.run.id}


ENGINES = {"oneshot": _engine_oneshot, "oneshot-styled": _engine_oneshot_styled, "tools": _engine_tools}


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
    if args.only:
        keys = [k for k in keys if k in args.only]
    _migrate_eval_db()

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

    meta = {
        "run_id": run_id, "engine": args.engine, "strong_model": client.model_for("strong"),
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"), "results": [],
    }
    meta_path = run_dir / "run.json"
    if args.resume and meta_path.exists():  # keep finished jobs, redo failed/missing ones
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["created_at"] = old.get("created_at", meta["created_at"])
        meta["results"] = [r for r in old["results"] if "error" not in r]
    results = meta["results"]
    done = {r["key"] for r in results}
    if done:
        print(f"  resuming: {len(done)} job(s) already done, skipped")

    def save() -> None:  # after every job, so a killed run loses at most the job in progress
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    for key in keys:
        if key in done:
            continue
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
            save()
            continue
        seconds = round(time.monotonic() - start, 1)
        extra: dict = {}
        if isinstance(text, dict):  # the tools engine: letter + its full LetterState
            out = text
            text = out["text"]
            (run_dir / "states").mkdir(exist_ok=True)
            (run_dir / "states" / f"{key}.json").write_text(
                json.dumps(out["state"], indent=2, ensure_ascii=False), encoding="utf-8")
            final = out["state"]["drafts"][-1]
            extra = {
                "letter_run_id": out["letter_run_id"],
                "drafts": len(out["state"]["drafts"]),
                "tool_checks": {n: c["passed"] for n, c in final["checks"].items()},
            }
        text = text or ""
        (run_dir / "letters" / f"{key}.txt").write_text(text, encoding="utf-8")
        checks = auto_checks(text)
        results.append({
            "key": key, "title": title, "company": company, "score": score,
            "seconds": seconds, **_run_cost(job_id, since), "auto": checks, **extra,
        })
        save()
        fails = [k for k in AUTO_ITEMS if not checks[k]]
        tool_note = ""
        if extra:
            failed = [n for n, ok in extra["tool_checks"].items() if not ok]
            tool_note = f"  {extra['drafts']} draft(s), tools {'ok' if not failed else 'FAIL: ' + ', '.join(failed)}"
        print(f"  {score!s:>4}  {title[:48]:48}  {checks['words']:>3}w  {seconds:>5}s  "
              f"{'auto ok' if not fails else 'auto FAIL: ' + ', '.join(fails)}{tool_note}")

    save()

    with (run_dir / "grades.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "title", *HUMAN_ITEMS, "notes"])
        for r in results:
            if "error" not in r:
                w.writerow([r["key"], r["title"], *[""] * len(HUMAN_ITEMS), ""])
    _write_letters_md(run_dir, meta)
    if (run_dir / "states").exists():
        _write_drafts_md(run_dir, meta)
        print(f"Draft history + check results: {(run_dir / 'drafts.md').relative_to(ROOT)}")
    print(f"\nRead them: {(run_dir / 'letters.md').relative_to(ROOT)}")
    print(f"Grade them: fill Y/N in {(run_dir / 'grades.csv').relative_to(ROOT)} (see evals/rubric.md),")
    print(f"then: python scripts/letter_lab.py report {run_id}")
    return 0


# ---------------------------------------------------------------------------
# 4. report
# ---------------------------------------------------------------------------
def _quote(text: str, width: int = 88) -> list[str]:
    """A letter as a wrapped markdown blockquote: readable raw and in preview."""
    out: list[str] = []
    for para in [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]:
        if out:
            out.append(">")
        for line in para.split("\n"):
            out += ["> " + w for w in textwrap.wrap(line, width - 2, break_on_hyphens=False)] or [">"]
    return out


def _write_letters_md(run_dir: Path, meta: dict, against: str | None = None) -> Path:
    """Write <run>/letters.md: every letter in one readable file, with its stats.

    The .txt files stay byte-for-byte what the engine wrote (the report re-checks
    them, and they are what you'd paste into an application). With `against`, each
    letter is followed by the other run's letter for the same job, folded.
    """
    from app.llm.letter.tools.style_lint import lint

    other_dir = RUNS_DIR / against if against else None
    ok = [r for r in meta["results"] if "error" not in r]
    lines = [
        f"# Letters: {meta['run_id']}", "",
        f"Engine `{meta['engine']}`, model `{meta['strong_model']}`, run {meta['created_at']}. "
        f"Grade in `grades.csv` (see evals/rubric.md)."
        + (f" Each letter is followed by the `{against}` letter for the same job (click to open)."
           if other_dir else ""),
        "",
    ]
    lines += [f"{i}. {r['title']}" + (f" ({r['company']})" if r.get("company") else "")
              for i, r in enumerate(ok, 1)]
    for i, r in enumerate(ok, 1):
        letter = run_dir / "letters" / f"{r['key']}.txt"
        if not letter.exists():
            continue
        text = letter.read_text(encoding="utf-8")
        res = lint(text)
        tags = [i_.split(":")[0] for i_ in res["issues"]] + [f"({w.split(':')[0]})" for w in res["warnings"]]
        lines += [
            "", "---", "",
            f"## {i}/{len(ok)}. {r['title']}" + (f" ({r['company']})" if r.get("company") else ""), "",
            f"Match score {r.get('score', '?')} · {res['words']} words · "
            f"style_lint {'pass' if res['passed'] else 'FAIL'}"
            + (f": {', '.join(tags)}" if tags else "") + f" · key `{r['key']}`", "",
            *_quote(text),
        ]
        other = other_dir / "letters" / f"{r['key']}.txt" if other_dir else None
        if other and other.exists():
            other_text = other.read_text(encoding="utf-8")
            lines += [
                "", f"<details><summary>{against}: {lint(other_text)['words']} words</summary>", "",
                *_quote(other_text), "", "</details>",
            ]
    out = run_dir / "letters.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _write_drafts_md(run_dir: Path, meta: dict) -> Path:
    """Write <run>/drafts.md for a tools-engine run: per job, every draft with its
    check results and claims (each next to the profile text it cites), so the
    checkers' calls can be judged by eye."""
    from app.db import SessionLocal
    from app.llm.letter.runner import load_profile
    from app.llm.letter.state import ProfileIndex

    with SessionLocal() as db:
        index = ProfileIndex(load_profile(db, PROFILE_ID))
    lines = [f"# Drafts and checks: {meta['run_id']}", "",
             "Each job: the requirements the letter had to cover, then every draft with its check results "
             "and declared claims. Unanswered must-have gaps were treated as leave_out.", ""]
    for r in meta["results"]:
        path = run_dir / "states" / f"{r['key']}.json"
        if "error" in r or not path.exists():
            continue
        st = json.loads(path.read_text(encoding="utf-8"))
        lines += ["---", "", f"## {r['title']}" + (f" ({r['company']})" if r.get("company") else ""), "",
                  f"Match score {r.get('score', '?')} · {len(st['drafts'])} draft(s) · ${r['cost_usd']:.4f} · "
                  f"{r['seconds']}s · key `{r['key']}`", ""]
        left_out = [q for q in st["requirements"] if q.get("user_decision")]
        for q in st["requirements"]:
            if q["letter_role"] == "headline" or q["importance"] == "essential":
                flag = " (left out)" if q in left_out else ""
                lines.append(f"- {q['id']} [{q['importance']}/{q['letter_role']}] **{q['status']}**{flag}: {q['text']}")
        for d in st["drafts"]:
            lines += ["", f"### Draft {d['version']}", ""]
            for name in ("claims", "requirements", "style"):
                c = d["checks"].get(name)
                if c is None:
                    lines.append(f"- {name}: not run")
                    continue
                lines.append(f"- {name}: {'pass' if c['passed'] else '**FAIL**'}")
                lines += [f"  - {i}" for i in c["issues"]]
                lines += [f"  - _(warning)_ {w}" for w in c["warnings"]]
            lines += ["", "<details><summary>Letter and declared claims</summary>", "", *_quote(d["text"]), ""]
            for c in d["claims"]:
                cited = index.resolve(c["source"]) if c.get("source") else None
                lines.append(f"- {c['text']!r} ← `{c.get('source')}`: {cited or '**does not resolve**'}")
            lines += ["", "</details>"]
        lines.append("")
    out = run_dir / "drafts.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def cmd_read(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    if not (run_dir / "run.json").exists():
        print(f"No run.json in {run_dir.relative_to(ROOT)}")
        return 1
    if args.against and not (RUNS_DIR / args.against / "letters").exists():
        print(f"No letters in evals/runs/{args.against}/")
        return 1
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    out = _write_letters_md(run_dir, meta, args.against)
    print(f"Wrote {out.relative_to(ROOT)} (open the preview: Ctrl+Shift+V)")
    return 0


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
    # The writer's own style check (Phase 4). Not part of the rubric pass rate: it is
    # stricter (spaced en dashes, warnings) and is reported so engines can be compared on it.
    from app.llm.letter.tools.style_lint import lint

    lint_rows, lint_pass = [], 0
    for r in ok:
        letter = run_dir / "letters" / f"{r['key']}.txt"
        if not letter.exists():
            continue
        res = lint(letter.read_text(encoding="utf-8"))
        lint_pass += res["passed"]
        tags = [i.split(":")[0] for i in res["issues"]] + [f"({w.split(':')[0]})" for w in res["warnings"]]
        lint_rows.append(f"| {r['title'][:50].replace('|', '/')} | {'✓' if res['passed'] else '✗'} | "
                         f"{res['sentence_stdev']} | {', '.join(tags) or '-'} |")
    if lint_rows:
        lines += [
            "", f"## style_lint (writer's check, not in the pass rate): {lint_pass}/{len(lint_rows)} pass", "",
            "Blocking issues plain, warnings in brackets. Sentence stdev: words; under 6 warns.", "",
            "| Job | Pass | Sentence stdev | Issues (warnings) |", "|---|---|---|---|", *lint_rows,
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


# ---------------------------------------------------------------------------
# 5. analyze (Phase 3): requirements checklist + evidence map, for hand-checking
# ---------------------------------------------------------------------------
_ROLE_TITLES = {
    "headline": "HEADLINE: lead points the letter should make with evidence",
    "mention": "MENTION: brief mention if the candidate has it",
    "implied": "IMPLIED: not named, but may be used if it fits a sentence",
    "not_for_letter": "NOT FOR THE LETTER: shown to you as a note",
}
_CHECK_COLUMNS = ("importance_ok", "letter_role_ok", "evidence_ok", "notes")


def _evidence_text(ctx, pointers: list[str]) -> str:
    return " || ".join(f"[{p}] {ctx.index.resolve(p)}" for p in pointers)


def cmd_analyze(args) -> int:
    if not EVAL_DB.exists() or not SET_FILE.exists():
        print("Run `letter_lab.py prepare` first.")
        return 1
    keys = json.loads(SET_FILE.read_text(encoding="utf-8"))["jobs"]
    _migrate_eval_db()

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm import client
    from app.llm.client import BudgetExceededError, DailyQuotaError
    from app.llm.letter.runner import execute_tool, finish_run, start_run
    from app.llm.letter.state import JobInfo, LetterState
    from app.llm.letter.tools.analyze_job import analyze_job
    from app.llm.letter.tools.match_profile import match_profile
    from app.models import JobListing, Match

    run_id = args.label or f"analysis-{datetime.datetime.now():%Y%m%d-%H%M}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Analysis {run_id}: mid model={client.model_for('mid')}, {len(keys)} jobs"
          f"{' (forcing re-analysis)' if args.force else ''}")

    review: list[str] = [
        f"# Requirements analysis review: {run_id}\n",
        "For each job: the checklist `analyze_job` built from the ad, then what `match_profile` found in your profile.",
        "Check against the ad (the full ad is at the end of each section). Then fill in `checks.csv` (Y/N); see evals/rubric.md.",
        "If the analysis MISSED a requirement the ad clearly states, add a row to checks.csv with id `MISSED` and the text.\n",
    ]
    rows: list[list[str]] = []
    totals = {"cost": 0.0, "runs": 0}
    role_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for key in keys:
        source, source_job_id = key.split("-", 1)
        with SessionLocal() as db:
            job = db.scalar(select(JobListing).where(
                JobListing.source == source, JobListing.source_job_id == source_job_id))
            if job is None:
                print(f"  {key}: not in eval.db — skipped")
                continue
            match = db.scalar(select(Match).where(Match.job_id == job.id, Match.user_id == PROFILE_ID))
            state = LetterState(profile_id=PROFILE_ID, job=JobInfo(job_id=job.id, title=job.title, company=job.company))
            ctx = start_run(db, match.id, "workflow", state)
            try:
                r1 = execute_tool(ctx, state, "analyze_job", analyze_job, force=args.force)
                r2 = execute_tool(ctx, state, "match_profile", match_profile) if r1.ok else None
            except (DailyQuotaError, BudgetExceededError) as exc:
                finish_run(ctx, state, "budget_stopped")
                print(f"Stopped: {exc}")
                break
            ok = r1.ok and r2 is not None and r2.ok
            finish_run(ctx, state, "done" if ok else "failed")
            totals["cost"] += state.budget.cost_usd
            totals["runs"] += 1
            title, company, score, ad = job.title, job.company, int(match.score), job.raw_description

            if not ok:
                err = (r1.error if not r1.ok else r2.error)
                print(f"  {key}: FAILED — {err}")
                review.append(f"\n---\n\n# {title} ({company or '?'}): FAILED\n\n{err}\n")
                continue

            by_role = {}
            for req in state.requirements:
                by_role.setdefault(req.letter_role, []).append(req)
                role_counts[req.letter_role] = role_counts.get(req.letter_role, 0) + 1
                status_counts[req.status] = status_counts.get(req.status, 0) + 1
                rows.append([key, req.id, req.text, req.importance, req.letter_role, req.theme, req.status,
                             _evidence_text(ctx, req.evidence), "", "", "", ""])

            lines = [f"\n---\n\n# {title} ({company or '?'})  [match score {score}, csv key {key}]\n",
                     f"Source: {r1.summary['source']}. Tone: {state.job.tone}. Run cost ${state.budget.cost_usd:.4f}.\n"]
            if state.job.company_facts:
                lines.append("**Company facts (raw material for a specific detail):**")
                lines += [f"- {f}" for f in state.job.company_facts]
            if state.job.keywords:
                lines.append(f"\n**Keywords to echo:** {', '.join(state.job.keywords)}")
            if state.job.screening_questions:
                lines.append("\n**Screening questions in the ad:**")
                lines += [f"- {q}" for q in state.job.screening_questions]
            if state.job.application_instructions:
                lines.append("\n**Application instructions in the ad:**")
                lines += [f"- {q}" for q in state.job.application_instructions]
            if state.eligibility_notes():
                lines.append("\n**Eligibility notes (heads-up on the job card, never in the letter):**")
                lines += [f"- {t}" for t in state.eligibility_notes()]
            if state.pending_gaps():
                lines.append("\n**Would ask you before drafting** (essential, not covered): "
                             + "; ".join(f"{g.id} {g.text}" for g in state.pending_gaps()))
            if r2.summary["corrections"]:
                lines.append("\n**Code corrected the model on:** " + "; ".join(r2.summary["corrections"]))
            for role, title_text in _ROLE_TITLES.items():
                items = by_role.get(role, [])
                lines.append(f"\n## {title_text} ({len(items)})\n")
                for req in items:
                    follows = f" (follows from {', '.join(req.implied_by)})" if req.implied_by else ""
                    lines.append(f"- **{req.id}** [{req.importance}] _{req.theme}_: {req.text}{follows}")
                    lines.append(f"  - **{req.status}**" + (f": {req.note}" if req.note else ""))
                    for p in req.evidence:
                        lines.append(f"  - evidence `{p}`: {ctx.index.resolve(p)}")
            lines.append(f"\n## THE AD\n\n{ad}\n")
            review.append("\n".join(lines))
            print(f"  {score:>3}  {title[:46]:46}  {len(state.requirements):>2} reqs  "
                  f"roles {r1.summary['by_letter_role']}  status {r2.summary['by_status']}")

    (run_dir / "review.md").write_text("\n".join(review), encoding="utf-8")
    with (run_dir / "checks.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "id", "text", "importance", "letter_role", "theme", "status", "evidence", *_CHECK_COLUMNS])
        w.writerows(rows)
    print(f"\nRoles: {role_counts}   Status: {status_counts}")
    print(f"{totals['runs']} jobs, ${totals['cost']:.4f} total (${totals['cost'] / max(totals['runs'], 1):.4f}/job)")
    print(f"Read: {(run_dir / 'review.md').relative_to(ROOT)}")
    print(f"Fill in Y/N: {(run_dir / 'checks.csv').relative_to(ROOT)}  (importance_ok, letter_role_ok, evidence_ok)")
    print(f"Then: python scripts/letter_lab.py analysis-report {run_id}")
    return 0


def cmd_analysis_report(args) -> int:
    run_dir = RUNS_DIR / args.run_id
    path = run_dir / "checks.csv"
    if not path.exists():
        print(f"No checks.csv for {args.run_id}")
        return 1
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    missed = [r for r in rows if r["id"].strip().upper() == "MISSED"]
    graded = [r for r in rows if r["id"].strip().upper() != "MISSED"]

    def rate(col: str, subset=None) -> str:
        subset = graded if subset is None else subset
        votes = [_yn(r[col]) for r in subset if _yn(r[col]) is not None]
        return f"{sum(votes)}/{len(votes)}" if votes else "not graded"

    lines = [
        f"# Requirements analysis: {args.run_id}", "",
        f"{len(graded)} requirements across {len({r['key'] for r in graded})} jobs; "
        f"{len(missed)} requirement(s) the analysis MISSED (rows marked MISSED).", "",
        "| Check | Agreed |", "|---|---|",
        f"| importance_ok (essential / important / nice_to_have) | {rate('importance_ok')} |",
        f"| letter_role_ok (headline / mention / implied / not_for_letter) | {rate('letter_role_ok')} |",
        f"| evidence_ok (the cited profile text really backs it) | {rate('evidence_ok')} |",
        "", "## By letter role", "", "| Role | Count | letter_role_ok | evidence_ok |", "|---|---|---|---|",
    ]
    for role in _ROLE_TITLES:
        sub_rows = [r for r in graded if r["letter_role"] == role]
        lines.append(f"| {role} | {len(sub_rows)} | {rate('letter_role_ok', sub_rows)} | {rate('evidence_ok', sub_rows)} |")
    disagreements = [r for r in graded if any(_yn(r[c]) is False for c in _CHECK_COLUMNS[:3])]
    if disagreements or missed:
        lines += ["", "## Where you disagreed", ""]
        for r in disagreements:
            bad = [c for c in _CHECK_COLUMNS[:3] if _yn(r[c]) is False]
            lines.append(f"- `{r['key']}` {r['id']} ({r['importance']}/{r['letter_role']}) {r['text'][:90]}: "
                         f"{', '.join(bad)}" + (f". {r['notes']}" if r["notes"].strip() else ""))
        for r in missed:
            lines.append(f"- `{r['key']}` MISSED: {r['text']}" + (f". {r['notes']}" if r["notes"].strip() else ""))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.run_id}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(ROOT)}")
    return 0


# ---------------------------------------------------------------------------
# 7. plant (Phase 5): how many planted false claims does check_claims catch?
# ---------------------------------------------------------------------------
# Overclaim types a careless writer produces. The planting model rewrites ONE sentence
# of a letter that already passed check_claims to carry one of these.
PLANT_TYPES = {
    "invented_tool": "says the candidate used a tool, language or technology the profile never mentions",
    "inflated_scope": "makes the audience, team, scale or seniority bigger than the profile says "
                      "(team leads -> executives, contributed -> led, a class project -> a client project)",
    "invented_metric": "adds a number or measurable outcome the profile does not give",
    "study_to_work": "turns something the candidate studied, or built a project about, into professional "
                     "or day-to-day use of it",
    "wrong_context": "attaches a real piece of experience to the wrong employer, role or setting",
}
PLANT_MODES = ("undeclared", "miscited")


def _plant_schema():
    from pydantic import BaseModel

    class Plant(BaseModel):
        original: str
        rewritten: str
        false_words: str
        cite: str

    return Plant


_PLANT_PROMPT = """\
You are building a test for an automated claim checker. Rewrite exactly ONE sentence of \
the cover letter so that it contains ONE false claim about the candidate, of the type \
given. The claim must be plausible (something a careless writer might write), stated as \
confidently as the rest of the letter, and clearly false given the profile: not a matter \
of opinion, and not something any line of the profile supports. Keep the sentence \
natural and change as little as possible.

Return:
- original: the sentence exactly as it appears in the letter, copied character for character.
- rewritten: the new sentence.
- false_words: the exact words in the rewritten sentence that make the false claim.
- cite: the profile pointer (from the square brackets, without them) that a careless \
writer would cite for it: related to the false claim, but not actually supporting it."""


def _mentions(issue: str, false_words: str) -> bool:
    """Does a check issue point at the planted words (not just fail for another reason)?"""
    import re

    # Numbers count whatever their length ("by 25%" is just "25"); short words don't.
    words = [w for w in re.findall(r"[a-z0-9]+", false_words.lower()) if len(w) > 2 or any(ch.isdigit() for ch in w)]
    if not words:
        return False
    present = set(re.findall(r"[a-z0-9]+", issue.lower()))
    return sum(w in present for w in words) / len(words) >= 0.5


def cmd_plant(args) -> int:
    src_dir = RUNS_DIR / args.run_id
    if not (src_dir / "states").exists():
        print(f"{args.run_id} has no states/: plant needs a `run --engine tools` run")
        return 1
    meta = json.loads((src_dir / "run.json").read_text(encoding="utf-8"))
    _migrate_eval_db()

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm.client import BudgetExceededError, DailyQuotaError, complete_json
    from app.llm.letter.runner import execute_tool, finish_run, start_run
    from app.llm.letter.state import Claim, Draft, LetterState
    from app.llm.letter.tools.check_claims import check_claims
    from app.models import LlmUsage, Match

    Plant = _plant_schema()
    label = args.label or f"plant-{args.run_id}"
    out_dir = RUNS_DIR / label
    out_dir.mkdir(parents=True, exist_ok=True)
    type_names = list(PLANT_TYPES)
    plants_path = out_dir / "plants.json"
    saved = {"clean": [], "plants": [], "run_ids": []}
    if (args.resume or args.report_only) and plants_path.exists():  # letters already finished are kept, not re-bought
        saved = {**saved, **json.loads(plants_path.read_text(encoding="utf-8"))}
    rows: list[dict] = saved["plants"]
    clean_rows: list[dict] = saved["clean"]
    run_ids: list[int] = saved["run_ids"]
    done = {r["key"] for r in clean_rows}
    print(f"Plant test {label}: from {args.run_id}, tiers {args.tiers}, {args.per_letter} plant(s) per letter"
          + (f"; resuming, {len(done)} letter(s) done" if done else ""))

    def save() -> None:  # after every letter, so a crash loses at most the letter in progress
        plants_path.write_text(json.dumps({"clean": clean_rows, "plants": rows, "run_ids": run_ids},
                                          indent=2, ensure_ascii=False), encoding="utf-8")

    def judge(ctx, base: LetterState, draft: Draft, tier: str):
        """check_claims on one draft; None if the call failed (logged in letter_run_steps)."""
        s = base.model_copy(deep=True)
        s.drafts = [draft]
        r = execute_tool(ctx, s, "check_claims", check_claims, tier=tier)
        if not r.ok:
            print(f"    check_claims ({tier}) failed: {r.error[:120]}")
            return None
        return r.summary

    letter_no = 0
    try:
        for res in ([] if args.report_only else meta["results"]):
            path = src_dir / "states" / f"{res['key']}.json"
            if "error" in res or not path.exists():
                continue
            if res["key"] in done:
                letter_no += 1  # keep the type rotation the same as an uninterrupted run
                continue
            state = LetterState.model_validate_json(path.read_text(encoding="utf-8"))
            clean = next((d for d in reversed(state.drafts)
                          if d.checks.get("claims") and d.checks["claims"].passed), None)
            if clean is None:
                print(f"  {res['key']}: no draft passed check_claims, skipped")
                continue
            with SessionLocal() as db:
                match = db.scalar(select(Match).where(Match.job_id == state.job.job_id, Match.user_id == PROFILE_ID))
                ctx = start_run(db, match.id, "eval-plant", state)
                run_ids.append(ctx.run.id)
                clean_draft = Draft(version=clean.version, text=clean.text, claims=clean.claims)
                for tier in args.tiers:
                    s = judge(ctx, state, clean_draft, tier)
                    clean_rows.append({"key": res["key"], "tier": tier, "passed": s and s["passed"],
                                       "error": s is None, "issues": s["issues"] if s else []})

                for j in range(args.per_letter):
                    ptype = type_names[(letter_no * args.per_letter + j) % len(type_names)]
                    data = complete_json(
                        _PLANT_PROMPT,
                        f"TYPE: {ptype}: {PLANT_TYPES[ptype]}\n\n=== PROFILE ===\n{ctx.index.prompt_catalog()}"
                        f"\n\n=== LETTER ===\n{clean.text}",
                        schema=Plant, tier="mid", task="plant_claim",
                        job_id=state.job.job_id, run_id=ctx.run.id,
                    )
                    p = Plant.model_validate(data)
                    if not p.original.strip() or p.original not in clean.text or p.rewritten == p.original:
                        print(f"  {res['key']} {ptype}: planting failed (sentence not found), skipped")
                        continue
                    text = clean.text.replace(p.original, p.rewritten, 1)
                    cite = p.cite.strip().strip("[]`'\" ").strip()
                    cite = cite if ctx.index.resolve(cite) else None
                    for mode in PLANT_MODES:
                        if mode == "miscited" and not cite:
                            continue
                        claims = list(clean.claims)
                        if mode == "miscited":
                            claims.append(Claim(text=p.false_words, source=cite))
                        planted = Draft(version=clean.version, text=text, claims=claims)
                        for tier in args.tiers:
                            s = judge(ctx, state, planted, tier)
                            if s is None:
                                outcome = "error"
                            else:
                                caught = (not s["passed"]) and any(_mentions(i, p.false_words) for i in s["issues"])
                                outcome = "caught" if caught else ("failed_other" if not s["passed"] else "missed")
                            rows.append({"key": res["key"], "type": ptype, "mode": mode, "tier": tier,
                                         "outcome": outcome, "original": p.original, "rewritten": p.rewritten,
                                         "false_words": p.false_words, "cite": cite,
                                         "issues": s["issues"] if s else []})
                            print(f"  {res['key']}  {ptype:16} {mode:10} {tier:6} {outcome}")
                finish_run(ctx, state, "done")
            save()
            letter_no += 1
    except (DailyQuotaError, BudgetExceededError) as exc:
        print(f"Stopped: {exc}")

    with SessionLocal() as db:
        costs = dict(db.execute(
            select(LlmUsage.tier, func_sum(LlmUsage.cost_usd))
            .where(LlmUsage.run_id.in_(run_ids or [-1]), LlmUsage.task == "check_claims")
            .group_by(LlmUsage.tier)
        ).all())
        plant_cost = db.scalar(select(func_sum(LlmUsage.cost_usd)).where(
            LlmUsage.run_id.in_(run_ids or [-1]), LlmUsage.task == "plant_claim")) or 0
    save()
    errors = sum(r["outcome"] == "error" for r in rows) + sum(bool(r.get("error")) for r in clean_rows)
    rows = [r for r in rows if r["outcome"] != "error"]  # a failed call is no evidence either way
    for r in rows:  # scored from the stored issues, so a scoring fix needs no new calls (--report-only)
        caught = bool(r["issues"]) and any(_mentions(i, r["false_words"]) for i in r["issues"])
        r["outcome"] = "caught" if caught else ("failed_other" if r["issues"] else "missed")
    clean_rows = [r for r in clean_rows if not r.get("error")]

    def rate(sub: list[dict]) -> str:
        return f"{sum(r['outcome'] == 'caught' for r in sub)}/{len(sub)}" if sub else "-"

    lines = [f"# Planted-claim test: {label}", "",
             f"Letters from `{args.run_id}` that passed `check_claims`, each with one sentence rewritten "
             f"(mid model) to carry one false claim. **undeclared**: the false claim is not on the writer's "
             f"claims list (stage 2 must find it). **miscited**: it is listed, citing a related profile "
             f"pointer that does not back it. *Caught* = the check failed with an issue naming the planted "
             f"words; *failed_other* = it failed, but not on the plant.", "",
             "| Tier | Caught (all) | undeclared | miscited | failed_other | False alarms on clean letters | check_claims cost |",
             "|---|---|---|---|---|---|---|"]
    for tier in args.tiers:
        sub = [r for r in rows if r["tier"] == tier]
        clean_sub = [r for r in clean_rows if r["tier"] == tier]
        lines.append(
            f"| {tier} | {rate(sub)} | {rate([r for r in sub if r['mode'] == 'undeclared'])} | "
            f"{rate([r for r in sub if r['mode'] == 'miscited'])} | "
            f"{sum(r['outcome'] == 'failed_other' for r in sub)} | "
            f"{sum(not r['passed'] for r in clean_sub)}/{len(clean_sub)} | ${float(costs.get(tier) or 0):.4f} |")
    lines += ["", "## By claim type", "", "| Type | " + " | ".join(args.tiers) + " |",
              "|---|" + "---|" * len(args.tiers)]
    for t in type_names:
        lines.append(f"| {t} | " + " | ".join(rate([r for r in rows if r["type"] == t and r["tier"] == tier])
                                             for tier in args.tiers) + " |")
    if errors:
        lines += ["", f"{errors} check call(s) failed and are left out of the counts above."]
    lines += ["", f"Planting cost ${float(plant_cost):.4f}. Details (letter text, gitignored): "
                  f"`evals/runs/{label}/plants.json`."]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote evals/results/{label}.md")
    return 0


def func_sum(col):
    from sqlalchemy import func

    return func.coalesce(func.sum(col), 0)


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
    p.add_argument("--only", nargs="+", metavar="KEY", help="just these set keys (e.g. seek-94419843), for a smoke test")
    p.add_argument("--resume", action="store_true",
                   help="continue an interrupted run (same --label): keep finished jobs, redo the rest")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("read", help="write evals/runs/<run>/letters.md, every letter in one readable file")
    p.add_argument("run_id")
    p.add_argument("--against", help="another run to show each job's letter from, folded underneath")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("report", help="merge code checks + grades into evals/results/<run>.md")
    p.add_argument("run_id")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("analyze", help="run analyze_job + match_profile on the set; write a hand-check pack")
    p.add_argument("--label", help="run id (default: analysis-<timestamp>)")
    p.add_argument("--force", action="store_true", help="ignore the cached analysis and redo it")
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("analysis-report", help="summarise your checks.csv grades")
    p.add_argument("run_id")
    p.set_defaults(fn=cmd_analysis_report)

    p = sub.add_parser("plant", help="planted-claim test: does check_claims catch false claims? (Phase 5)")
    p.add_argument("run_id", help="a `run --engine tools` run whose letters passed check_claims")
    p.add_argument("--tiers", nargs="+", default=["small", "mid"], choices=["small", "mid", "strong"])
    p.add_argument("--per-letter", type=int, default=2, help="plants per letter (types rotate)")
    p.add_argument("--label", help="output id (default: plant-<run>)")
    p.add_argument("--resume", action="store_true", help="keep letters already in plants.json, do the rest")
    p.add_argument("--report-only", action="store_true", help="re-score plants.json and rewrite the report; no LLM calls")
    p.set_defaults(fn=cmd_plant)

    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
