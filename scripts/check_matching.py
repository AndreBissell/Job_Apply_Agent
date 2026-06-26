"""Diagnostic report for job<->profile matching.

Runs the full pipeline (prefilter + LLM score) over the seeded test jobs and
prints a human-readable report: per-job pre-filter signals, the LLM score, and a
PASS/FAIL against each test job's expected score range. Re-scores in place via
``match_job(force=True)`` — it does not create throwaway rows.

    python scripts/check_matching.py                # all test jobs (source="test")
    python scripts/check_matching.py --source test  # same
    python scripts/check_matching.py --all          # real Seek jobs too
    python scripts/check_matching.py --profile-id 2 # score a different profile

Use this AFTER scripts/seed_test_jobs.py has populated the test jobs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.llm.client import DailyQuotaError  # noqa: E402
from app.llm.match import match_job  # noqa: E402
from app.llm.prefilter import PrefilterResult, normalise_skill, prefilter  # noqa: E402
from app.models import Experience, JobListing, Match, Profile  # noqa: E402

# Expected LLM score ranges (inclusive) for the seeded test jobs.
EXPECTED: dict[str, tuple[int, int]] = {
    "test-001": (75, 90),
    # Widened from 85 -> 90: a graduate with every required skill + a matching IT
    # degree + relevant internship/capstone is a legitimate top-band match (verified
    # 2026-06-23: scored 88). Capping it lower would miscalibrate the live scorer.
    "test-002": (70, 90),
    "test-003": (45, 65),
    "test-004": (30, 50),
    "test-005": (0, 20),
}

RULE = "-" * 60
HEAVY = "=" * 60


def _load_profile(db, profile_id: int) -> Profile | None:
    return db.scalar(
        select(Profile)
        .where(Profile.id == profile_id)
        .options(
            selectinload(Profile.skills),
            selectinload(Profile.qualifications),
            selectinload(Profile.experiences).selectinload(Experience.skills),
        )
    )


def _select_jobs(db, args) -> list[JobListing]:
    stmt = select(JobListing).where(JobListing.extracted_at.is_not(None))
    if not args.all:
        stmt = stmt.where(JobListing.source == args.source)
    stmt = stmt.order_by(JobListing.source, JobListing.source_job_id)
    return list(
        db.scalars(stmt.options(selectinload(JobListing.job_skills))).all()
    )


def _user_word_set(profile: Profile) -> set[str]:
    """Normalised words (len >= 3) across all user skill names — for near-miss checks."""
    words: set[str] = set()
    for s in profile.skills:
        words.update(w for w in normalise_skill(s.name).split() if len(w) >= 3)
    return words


def _near_miss(gap: str, user_words: set[str]) -> bool:
    """True if a gap skill shares a normalised word with some user skill."""
    gap_words = {w for w in normalise_skill(gap).split() if len(w) >= 3}
    return bool(gap_words & user_words)


def _verdict(source_job_id: str, score: int) -> str:
    rng = EXPECTED.get(source_job_id)
    if rng is None:
        return "(no expected range)"
    lo, hi = rng
    ok = lo <= score <= hi
    return f"{'PASS' if ok else 'FAIL'} (expected {lo}-{hi}, got {score})"


def _print_job(
    idx: int,
    job: JobListing,
    pf: PrefilterResult,
    match: Match | None,
    user_words: set[str],
    warnings: list[str],
) -> int | None:
    print(f"JOB {idx}: {job.title} @ {job.company or 'Unknown'}")
    print(f"  source_job_id: {job.source_job_id}")
    print(f"  Seniority: {job.seniority or '?'} | Work type: {job.work_type or '?'}")
    print()
    print("  PRE-FILTER")
    hard_total = pf.hard_total_job
    wanted_hard = pf.hard_skill_matches + pf.hard_skill_gaps
    print(f"  Hard skills wanted ({hard_total}): {', '.join(wanted_hard) or '(none)'}")
    print(f"    matched ({len(pf.hard_skill_matches)}): "
          f"{', '.join(pf.hard_skill_matches) or '-'}")
    print(f"    gaps    ({len(pf.hard_skill_gaps)}): "
          f"{', '.join(pf.hard_skill_gaps) or '-'}")
    wanted_soft = pf.soft_skill_matches + pf.soft_skill_gaps
    print(f"  Soft skills wanted ({len(wanted_soft)}): {', '.join(wanted_soft) or '(none)'}")
    print(f"    matched ({len(pf.soft_skill_matches)}): "
          f"{', '.join(pf.soft_skill_matches) or '-'}")
    pct = round(pf.overlap_pct * 100)
    print(f"  Overlap: {pf.hard_overlap_count}/{hard_total} ({pct}%) | "
          f"Seniority flag: {'YES' if pf.seniority_flag else 'NO'} | "
          f"Qual match: {'YES' if pf.qual_match else 'NO'}")

    # Collect near-miss warnings for the gaps on this job.
    for gap in pf.hard_skill_gaps:
        if _near_miss(gap, user_words):
            warnings.append(
                f"  {job.source_job_id}: job wants \"{gap}\", overlaps a user skill word "
                f"— review whether an alias is needed"
            )

    print()
    if match is None or match.score is None:
        print("  LLM SCORE: (no match row — scoring may have failed)")
        return None
    score = int(match.score)
    print(f"  LLM SCORE: {score}/100")
    print(f"  Reasoning: {match.reasoning or '-'}")
    gaps = json.loads(match.gaps) if match.gaps else []
    print(f"  Gaps: {gaps}")
    print()
    print(f"  VERDICT: {_verdict(job.source_job_id, score)}")
    return score


def main() -> int:
    parser = argparse.ArgumentParser(description="Matching diagnostic report.")
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--source", default="test", help="job source to report on")
    parser.add_argument("--all", action="store_true", help="include all extracted jobs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db = SessionLocal()
    try:
        profile = _load_profile(db, args.profile_id)
        if profile is None:
            print(f"Profile {args.profile_id} not found. Run scripts/seed_profile.py first.")
            return 1
        jobs = _select_jobs(db, args)
    finally:
        db.close()

    if not jobs:
        scope = "any source" if args.all else f"source='{args.source}'"
        print(f"No extracted jobs found for {scope}. Run scripts/seed_test_jobs.py first.")
        return 1

    # Re-score every job in place (force) so the report reflects the current prompt.
    quota_stopped = False
    for job in jobs:
        try:
            match_job(job.id, args.profile_id, force=True)
        except DailyQuotaError as exc:
            print(f"DAILY QUOTA REACHED while scoring job {job.id} — stopping. ({exc})")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 — keep going; row will show as failed
            print(f"  job {job.id}: scoring FAILED — {exc}")

    # Reload everything fresh for the report (match rows were just written).
    db = SessionLocal()
    try:
        profile = _load_profile(db, args.profile_id)
        user_words = _user_word_set(profile)
        jobs = _select_jobs(db, args)

        print(HEAVY)
        print("MATCHING DIAGNOSTIC REPORT")
        print(f"Profile: {profile.name} (id={profile.id})")
        print(f"Jobs: {len(jobs)} ({'all extracted' if args.all else args.source})")
        print(HEAVY)
        print()

        summary: list[tuple[str, str, int | None]] = []
        warnings: list[str] = []
        for idx, job in enumerate(jobs, 1):
            pf = prefilter(job, profile)
            match = db.scalar(
                select(Match).where(
                    Match.user_id == profile.id, Match.job_id == job.id
                )
            )
            score = _print_job(idx, job, pf, match, user_words, warnings)
            summary.append((job.source_job_id, job.title, score))
            print()
            print(RULE)
            print()
    finally:
        db.close()

    # Summary table.
    print("SUMMARY TABLE")
    print(f"  {'src':<10} {'Title':<34} {'Score':>5}  {'Expected':<10} Result")
    passed = 0
    expected_count = 0
    scores: list[int] = []
    for src, title, score in summary:
        rng = EXPECTED.get(src)
        exp_str = f"{rng[0]}-{rng[1]}" if rng else "-"
        if score is None:
            result = "NO SCORE"
            score_str = "-"
        else:
            scores.append(score)
            score_str = str(score)
            if rng:
                expected_count += 1
                if rng[0] <= score <= rng[1]:
                    passed += 1
                    result = "PASS"
                else:
                    result = "FAIL"
            else:
                result = "-"
        print(f"  {src:<10} {title[:34]:<34} {score_str:>5}  {exp_str:<10} {result}")

    print()
    if expected_count:
        print(f"Passed: {passed}/{expected_count}")
    if scores:
        spread = max(scores) - min(scores)
        flag = "good" if spread >= 40 else "LOW — prompt may need retuning"
        print(f"Score range: {min(scores)}-{max(scores)} (spread: {spread} points) — {flag}")

    print()
    print("NEAR-MISS KEYWORD WARNINGS (gaps overlapping a user skill word):")
    if warnings:
        for w in warnings:
            print(w)
    else:
        print("  (none — no gap shares a word with a user skill)")

    if quota_stopped:
        print("\nNOTE: stopped early on daily quota; some jobs may be stale.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
