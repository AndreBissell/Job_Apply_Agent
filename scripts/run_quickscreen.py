"""Batch runner for the pre-extraction quick screen (app/llm/quickscreen.py).

By default, screens every job_listings row that has a ``raw_description`` but no
``quick_screen_at`` yet. One bad job never kills the batch; a daily-quota 429 stops
it cleanly (no point retrying a per-day exhaustion). Reports how many jobs were
skipped vs. passed so you can sanity-check the skip rate against the expected
breakeven before trusting it on a large backlog.

    python scripts/run_quickscreen.py                # all pending jobs
    python scripts/run_quickscreen.py --limit 5      # first 5 (saves quota in dev)
    python scripts/run_quickscreen.py --job-id 12    # one specific job
    python scripts/run_quickscreen.py --force        # re-screen even if done
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.llm.client import DailyQuotaError  # noqa: E402
from app.llm.quickscreen import quick_screen  # noqa: E402
from app.models import JobListing  # noqa: E402


def _select_job_ids(db, args) -> list[int]:
    if args.job_id is not None:
        return [args.job_id]
    stmt = select(JobListing.id).where(JobListing.raw_description.is_not(None))
    if not args.force:
        stmt = stmt.where(JobListing.quick_screen_at.is_(None))
    stmt = stmt.order_by(JobListing.id)
    if args.limit is not None:
        stmt = stmt.limit(args.limit)
    return list(db.scalars(stmt).all())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pre-extraction quick screen over job listings.")
    parser.add_argument("--limit", type=int, default=None, help="max jobs to process")
    parser.add_argument("--job-id", type=int, default=None, help="screen one job by id")
    parser.add_argument("--force", action="store_true", help="re-screen already-done jobs")
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
        print("No jobs to screen (need raw_description present and quick_screen_at null).")
        return 0

    print(f"Quick-screening {len(job_ids)} job(s)…")
    processed = skipped = passed = errored = 0
    quota_stopped = False

    for job_id in job_ids:
        processed += 1
        try:
            outcome = quick_screen(job_id, 1, force=args.force)
            if outcome == "skipped":
                skipped += 1
            elif outcome == "passed":
                passed += 1
            elif outcome == "error-passed":
                errored += 1
            print(f"  job {job_id}: {outcome}")
        except DailyQuotaError as exc:
            print(f"  job {job_id}: DAILY QUOTA REACHED — stopping. ({exc})")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 — one bad job mustn't kill the batch
            errored += 1
            print(f"  job {job_id}: FAILED — {exc}")

    rate = f"{100 * skipped / processed:.0f}%" if processed else "n/a"
    print(
        f"\nDone. processed={processed} skipped={skipped} passed={passed} "
        f"errored={errored} skip_rate={rate}"
        f"{' (stopped on daily quota)' if quota_stopped else ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
