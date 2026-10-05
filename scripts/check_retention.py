"""Check the retention sweep against a time-shifted COPY of a real database.

    python scripts/check_retention.py              # copies real.db
    python scripts/check_retention.py --source app.db
    python scripts/check_retention.py --keep       # leave the scratch copy for inspection

tests/test_retention.py covers the rules on synthetic rows. This covers what it
can't: rows written by the real app and migrations (SQLite's naive datetime
strings, real FK cascades), and the idle loop's entry point `_retention_tick`.
Nobody has to wait four months for data to age: the sweep takes `now`, and
scenario 3 backdates the copy instead.

SAFETY — the source DB is never written:
  * it is opened read-only (sqlite URI mode=ro) and copied with the backup API
    into a fresh temp directory;
  * DATABASE_URL points at that copy BEFORE anything in app/ is imported, and
    the script aborts unless the engine resolves to it;
  * screenshot files are fake ones in the temp directory, never app/screenshots*;
  * no LLM calls (retention is pure Python; the idle loop is not started);
  * the source file's SHA-256 is compared before and after.

Scenarios, each on a fresh copy:
  1. Default preferences: the 150-match floor keeps everything, at any age.
  2. Floor off, `now` shifted so the window falls between the older and newer
     matches: only old `status='new'` rows with no `applied_at` are purged;
     cover letters / letter runs cascade; a job still matched by another
     profile survives; screenshot files expire but `screenshot_taken_at` stays;
     the 24h gate holds.
  3. Every match backdated 200 days, then the idle loop's `_retention_tick()`
     runs at the real current time.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", default="real.db", help="DB to copy (default real.db)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = parser.parse_args()

    source = (ROOT / args.source).resolve()
    if not source.is_file():
        print(f"Source DB not found: {source}")
        return 2
    source_hash = sha256(source)

    scratch = Path(tempfile.mkdtemp(prefix="check_retention_"))
    copy_path = scratch / "copy.db"
    shots = scratch / "screenshots"
    shots.mkdir()

    # Point the app at the copy BEFORE importing it: db.py builds its engine at
    # import, and load_dotenv() never overrides a variable that is already set.
    os.environ["DATABASE_URL"] = f"sqlite:///{copy_path.as_posix()}"
    os.environ["APP_ENV"] = "test"
    sys.path.insert(0, str(ROOT))

    def fresh_copy(engine=None) -> None:
        if engine is not None:
            engine.dispose()  # release pooled handles before overwriting the file
        src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(copy_path)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        for f in shots.iterdir():
            f.unlink()

    fresh_copy()

    from sqlalchemy import func, select, text

    from app import db as app_db
    from app import retention
    from app.models import CoverLetter, JobListing, JobSkill, LetterRun, LetterRunStep, Match, Profile
    from app.preferences import get_preferences, set_preferences

    engine_db = Path(app_db.engine.url.database).resolve()
    if engine_db != copy_path.resolve() or engine_db == source:
        print(f"ABORT: engine points at {engine_db}, expected the scratch copy {copy_path}")
        return 2
    SessionLocal = app_db.SessionLocal
    print(f"Source : {source} (read-only)\nCopy   : {copy_path}\n")

    def utc(dt):
        return retention.utc(dt)

    def screenshot(match: Match, name: str, taken_at: datetime) -> Path:
        path = shots / name
        path.write_bytes(b"fake png")
        match.screenshot_path = name
        match.screenshot_taken_at = taken_at
        return path

    try:
        # ── Scenario 1: the floor protects a small history ─────────────────────
        print("Scenario 1: default preferences (150-match floor), now = +1 year")
        with SessionLocal() as db:
            n = db.scalar(select(func.count()).select_from(Match))
            res = retention.run_sweep(db, 1, shots, now=retention.now_utc() + timedelta(days=365))
            after = db.scalar(select(func.count()).select_from(Match))
            floor = retention.floor_matches(get_preferences(db, 1))
        if n < floor:
            check(res.matches_purged == 0 and after == n,
                  f"{n} matches < floor {floor}: nothing purged ({res.matches_purged} purged, {after} left)")
        else:
            print(f"  (skipped: source has {n} matches >= floor {floor})")

        # ── Scenario 2: window boundary, whitelist, cascades, screenshots ──────
        print("\nScenario 2: floor off, window split between older and newer matches")
        fresh_copy(app_db.engine)
        with SessionLocal() as db:
            matches = db.scalars(
                select(Match).where(Match.user_id == 1).order_by(Match.created_at, Match.id)
            ).all()
            k = len(matches) * 2 // 3
            while 0 < k < len(matches) and matches[k - 1].created_at == matches[k].created_at:
                k += 1
            old, new = matches[:k], matches[k:]
            if len(old) < 6 or len(new) < 2:
                print(f"  ABORT: need >=6 older and >=2 newer matches with distinct times "
                      f"(got {len(old)} / {len(new)})")
                return 2
            boundary = utc(old[-1].created_at) + (utc(new[0].created_at) - utc(old[-1].created_at)) / 2
            now = boundary + timedelta(days=retention.DEFAULT_WINDOW_DAYS)

            set_preferences(db, 1, {"retention_floor_matches": 0, "retention_last_run": None,
                                    "retention_window_days": retention.DEFAULT_WINDOW_DAYS})

            applied, shortlisted, stamped, lettered, shot_purged, shared = old[:6]
            applied.status = "applied"
            applied.applied_at = utc(applied.created_at) + timedelta(hours=1)
            applied_at_before = applied.applied_at
            applied_shot = screenshot(applied, "applied.png", utc(applied.created_at))
            applied_taken_before = applied.screenshot_taken_at
            shortlisted.status = "shortlisted"
            stamped.applied_at = utc(stamped.created_at)  # 'new' but applied_at set: not purgeable
            purged_shot = screenshot(shot_purged, "purged.png", utc(shot_purged.created_at))
            kept_new = new[0]
            kept_shot = screenshot(kept_new, "recent.png", now - timedelta(days=5))

            for m in (lettered, kept_new):  # cover_letters.match_id is unique
                if db.scalar(select(CoverLetter.id).where(CoverLetter.match_id == m.id)) is None:
                    db.add(CoverLetter(match_id=m.id, generated_content="check letter"))
            run = LetterRun(match_id=lettered.id, engine="workflow")
            db.add(run)
            db.flush()
            db.add(LetterRunStep(run_id=run.id, seq=1, tool="analyze_job"))

            # A second profile still matching one old job: the job must survive.
            other = Profile(name="retention check", email="retention-check@example.invalid",
                            password_hash="x")
            db.add(other)
            db.flush()
            db.add(Match(user_id=other.id, job_id=shared.job_id, score=50))
            db.commit()

            protected_ids = {applied.id, shortlisted.id, stamped.id}
            expected_purged = {m.id for m in old} - protected_ids
            purged_job_ids = {m.job_id for m in old if m.id in expected_purged}
            expected_jobs_deleted = purged_job_ids - {shared.job_id}
            jobs_before = db.scalar(select(func.count()).select_from(JobListing))
            run_id, applied_id, kept_new_id = run.id, applied.id, kept_new.id
            lettered_id, shared_job = lettered.id, shared.job_id

        print(f"  {len(old)} older / {len(new)} newer matches; boundary {boundary:%Y-%m-%d %H:%M:%S}, "
              f"sweep now = {now:%Y-%m-%d}")
        with SessionLocal() as db:
            res = retention.run_sweep_if_due(db, 1, shots, now=now)
        with SessionLocal() as db:
            remaining = set(db.scalars(select(Match.id).where(Match.user_id == 1)).all())
            check(res is not None and res.matches_purged == len(expected_purged),
                  f"purged {res.matches_purged if res else None} matches, expected {len(expected_purged)}")
            check(not (remaining & expected_purged), "every old purgeable match is gone")
            check(remaining == protected_ids | {m.id for m in new},
                  "applied, shortlisted, applied_at-stamped and all newer matches survive")

            a = db.get(Match, applied_id)
            check(a.status == "applied" and utc(a.applied_at) == utc(applied_at_before),
                  "applied match keeps status and applied_at")
            check(a.screenshot_path is None and utc(a.screenshot_taken_at) == utc(applied_taken_before)
                  and not applied_shot.exists(),
                  "applied match's screenshot file expired; screenshot_taken_at kept")
            check(not purged_shot.exists(), "purged match's screenshot file deleted")
            k_new = db.get(Match, kept_new_id)
            check(kept_shot.exists() and k_new.screenshot_path == "recent.png",
                  "5-day-old screenshot on a kept match untouched")
            check(res.screenshots_expired == 2, f"screenshots_expired = {res.screenshots_expired}, expected 2")

            letters = set(db.scalars(select(CoverLetter.match_id)).all())
            check(lettered_id not in letters and kept_new_id in letters,
                  "cover letter cascaded with its purged match; kept match's letter remains")
            check(db.get(LetterRun, run_id) is None
                  and db.scalar(select(func.count()).select_from(LetterRunStep)
                                .where(LetterRunStep.run_id == run_id)) == 0,
                  "letter run and its steps cascaded")

            jobs_after = set(db.scalars(select(JobListing.id)).all())
            check(res.jobs_purged == len(expected_jobs_deleted)
                  and not (jobs_after & expected_jobs_deleted)
                  and jobs_before - len(jobs_after) == len(expected_jobs_deleted),
                  f"{res.jobs_purged} orphaned job listings deleted, expected {len(expected_jobs_deleted)}")
            check(shared_job in jobs_after, "job still matched by another profile survives")
            orphan_skills = db.scalar(select(func.count()).select_from(JobSkill)
                                      .where(JobSkill.job_id.in_(expected_jobs_deleted)))
            check(orphan_skills == 0, "job_skills of deleted jobs cascaded")

            last = get_preferences(db, 1)["retention_last_run"]
            check(res.more_pending is False and last == now.isoformat(),
                  "backlog drained; retention_last_run set to the sweep time")
        with SessionLocal() as db:
            check(retention.run_sweep_if_due(db, 1, shots, now=now + timedelta(hours=23)) is None,
                  "second sweep within 24h is skipped")
            later = now + timedelta(hours=25)
            # The cutoff moves 25h too, so newer matches within 25h of the boundary age out now.
            due = db.scalar(select(func.count()).select_from(Match).where(
                Match.user_id == 1, Match.status == "new", Match.applied_at.is_(None),
                Match.created_at < later - timedelta(days=retention.DEFAULT_WINDOW_DAYS)))
            again = retention.run_sweep_if_due(db, 1, shots, now=later)
            check(again is not None and again.matches_purged == due,
                  f"sweep after 24h runs again and purges exactly what aged out ({due})")

        # ── Scenario 3: the idle loop's entry point on a backdated copy ────────
        print("\nScenario 3: all matches backdated 200 days; idle loop's _retention_tick() at real time")
        fresh_copy(app_db.engine)
        with SessionLocal() as db:
            db.execute(text(
                "UPDATE matches SET created_at = datetime(created_at, '-200 days'), "
                "scored_at = datetime(scored_at, '-200 days')"
            ))
            db.commit()
            set_preferences(db, 1, {"retention_floor_matches": 0, "retention_last_run": None})
            first = db.scalars(select(Match).where(Match.user_id == 1).order_by(Match.created_at)).first()
            first.status = "applied"
            first.applied_at = utc(first.created_at)
            db.commit()
            keep_id = first.id
            total = db.scalar(select(func.count()).select_from(Match).where(Match.user_id == 1))

        import app.api.main as api_main

        api_main._screenshots_dir = shots  # never the real screenshots folder
        records: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture(level=logging.INFO)
        for name in ("app.retention", api_main.logger.name):
            logging.getLogger(name).addHandler(handler)
            logging.getLogger(name).setLevel(logging.INFO)

        api_main._retention_tick()
        with SessionLocal() as db:
            left = set(db.scalars(select(Match.id).where(Match.user_id == 1)).all())
            last = get_preferences(db, 1)["retention_last_run"]
        errors = [r for r in records if r.levelno >= logging.ERROR]
        check(not errors, "tick raised no errors" + (f": {errors[0].getMessage()}" if errors else ""))
        check(left == {keep_id}, f"{total - len(left)} of {total} purged; only the applied match left")
        check(any("Retention sweep (profile 1)" in r.getMessage() for r in records),
              "sweep logged its summary line")
        check(last is not None and abs(datetime.fromisoformat(last) - datetime.now(timezone.utc))
              < timedelta(minutes=5), "retention_last_run stamped with the real time")
        records.clear()
        api_main._retention_tick()
        with SessionLocal() as db:
            check(set(db.scalars(select(Match.id).where(Match.user_id == 1)).all()) == left
                  and not records, "second tick is a no-op (not due)")
    finally:
        app_db.engine.dispose()
        print()
        check(sha256(source) == source_hash, f"source {source.name} unchanged (SHA-256 match)")
        if args.keep:
            print(f"Scratch kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed"
          + ("" if not failed else " — FAILED: " + "; ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
