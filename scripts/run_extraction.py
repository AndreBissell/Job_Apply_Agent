"""Batch runner for LLM job extraction (app/llm/extract.py).

By default, extracts every job_listings row that has a ``raw_description`` but no
``extracted_at`` yet. One bad job never kills the batch; a daily-quota 429 stops
it cleanly (no point retrying a per-day exhaustion).

    python scripts/run_extraction.py                # all pending jobs
    python scripts/run_extraction.py --limit 5      # first 5 (saves quota in dev)
    python scripts/run_extraction.py --job-id 12    # one specific job
    python scripts/run_extraction.py --force        # re-extract even if done
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.llm.client import DailyQuotaError  # noqa: E402
from app.llm.extract import extract_job  # noqa: E402
from app.models import JobListing  # noqa: E402


def _select_job_ids(db, args) -> list[int]:
    if args.job_id is not None:
        return [args.job_id]
    stmt = select(JobListing.id).where(JobListing.raw_description.is_not(None))
    if not args.force:
        stmt = stmt.where(JobListing.extracted_at.is_(None))
    stmt = stmt.order_by(JobListing.id)
    if args.limit is not None:
        stmt = stmt.limit(args.limit)
    return list(db.scalars(stmt).all())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM extraction over job listings.")
    parser.add_argument("--limit", type=int, default=None, help="max jobs to process")
    parser.add_argument("--job-id", type=int, default=None, help="extract one job by id")
    parser.add_argument("--force", action="store_true", help="re-extract already-done jobs")
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
        print("No jobs to extract (need raw_description present and extracted_at null).")
        return 0

    print(f"Extracting {len(job_ids)} job(s)…")
    processed = succeeded = failed = 0
    quota_stopped = False

    for job_id in job_ids:
        processed += 1
        try:
            extract_job(job_id, force=args.force)
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
