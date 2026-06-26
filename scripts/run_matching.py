"""Batch runner for LLM job<->profile matching (app/llm/match.py).

By default, scores every extracted job_listings row (``extracted_at`` set) that
has no ``matches`` row yet for the given profile. One bad job never kills the
batch; a daily-quota 429 stops it cleanly.

    python scripts/run_matching.py                   # all unmatched jobs, profile 1
    python scripts/run_matching.py --job-id 42       # one specific job
    python scripts/run_matching.py --limit 5 --force # re-score 5 jobs
    python scripts/run_matching.py --profile-id 2    # different profile
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.llm.client import DailyQuotaError  # noqa: E402
from app.llm.match import match_job  # noqa: E402
from app.models import JobListing, Match  # noqa: E402


def _select_job_ids(db, args) -> list[int]:
    if args.job_id is not None:
        return [args.job_id]
    stmt = select(JobListing.id).where(JobListing.extracted_at.is_not(None))
    if not args.force:
        # Exclude jobs already scored for this profile.
        scored = select(Match.job_id).where(Match.user_id == args.profile_id)
        stmt = stmt.where(JobListing.id.not_in(scored))
    stmt = stmt.order_by(JobListing.date_scraped.desc())
    if args.limit is not None:
        stmt = stmt.limit(args.limit)
    return list(db.scalars(stmt).all())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM matching over extracted jobs.")
    parser.add_argument("--profile-id", type=int, default=1, help="profile to score against")
    parser.add_argument("--job-id", type=int, default=None, help="match one job by id")
    parser.add_argument("--limit", type=int, default=None, help="max jobs to process")
    parser.add_argument("--force", action="store_true", help="re-score already-matched jobs")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db = SessionLocal()
    try:
        job_ids = _select_job_ids(db, args)
    finally:
        db.close()

    if not job_ids:
        print("No jobs to match (need extracted_at set and no existing match row).")
        return 0

    print(f"Matching {len(job_ids)} job(s) against profile {args.profile_id}…")
    processed = succeeded = failed = 0
    quota_stopped = False

    for job_id in job_ids:
        processed += 1
        try:
            match_job(job_id, args.profile_id, force=args.force)
            succeeded += 1
        except DailyQuotaError as exc:
            print(f"  job {job_id}: DAILY QUOTA REACHED — stopping. ({exc})")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 — one bad job mustn't kill the batch
            failed += 1
            print(f"  job {job_id}: FAILED — {exc}")

    print(
        f"\nDone. processed={processed} succeeded={succeeded} failed={failed}"
        f"{' (stopped on daily quota)' if quota_stopped else ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
