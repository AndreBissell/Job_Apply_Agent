"""Batch cover-letter generation — dev and testing only.

Generates (or regenerates) cover letters for all above-threshold matches.
Stops cleanly on DailyQuotaError. The primary path is on-demand via
POST /jobs/{id}/regenerate; use this script only for bulk dev runs.

    python scripts/run_cover_letters.py
    python scripts/run_cover_letters.py --profile-id 2
    python scripts/run_cover_letters.py --force   # regenerate existing letters
"""

from __future__ import annotations

import argparse
import logging
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.llm.client import DailyQuotaError  # noqa: E402
from app.llm.cover_letter import THRESHOLD, generate_cover_letter  # noqa: E402
from app.models import Match  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch cover-letter generation.")
    parser.add_argument("--profile-id", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="regenerate existing letters")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db = SessionLocal()
    try:
        matches = db.scalars(
            select(Match)
            .where(Match.user_id == args.profile_id)
            .where(Match.score >= THRESHOLD)
            .options(selectinload(Match.cover_letter), selectinload(Match.job))
            .order_by(Match.score.desc())
        ).all()
    finally:
        db.close()

    if not matches:
        print(f"No matches at or above threshold {THRESHOLD} for profile {args.profile_id}.")
        return 0

    print(f"Found {len(matches)} match(es) at or above threshold {THRESHOLD}.")
    generated = 0
    skipped = 0
    failed = 0

    for m in matches:
        job_title = m.job.title if m.job else f"job {m.job_id}"
        score = float(m.score) if m.score else 0
        has_letter = m.cover_letter is not None

        if has_letter and not args.force:
            print(f"  SKIP  [{score:.0f}] {job_title} — letter already exists (--force to regen)")
            skipped += 1
            continue

        action = "REGEN" if has_letter else "GEN  "
        try:
            cl = generate_cover_letter(m.job_id, args.profile_id, force=args.force)
            if cl is not None:
                chars = len(cl.generated_content or "")
                print(f"  {action} [{score:.0f}] {job_title} — {chars} chars")
                generated += 1
            else:
                print(f"  SKIP  [{score:.0f}] {job_title} — below threshold (check score)")
                skipped += 1
        except DailyQuotaError as exc:
            print(f"\nQUOTA REACHED — stopping. ({exc})")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  [{score:.0f}] {job_title} — {exc}")
            failed += 1

    print(f"\nDone. Generated: {generated}  Skipped: {skipped}  Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
