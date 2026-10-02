"""Fill job_listings.company from the ad text for rows that have none.

    python scripts/backfill_company.py test          # app.db (test environment)
    python scripts/backfill_company.py real          # real.db
    python scripts/backfill_company.py eval          # evals/eval.db
    python scripts/backfill_company.py real --dry-run

Before 2026-10-02 the extension sent no company from job detail pages, so most
captured rows have company = NULL and cover letters said "at Unknown". New captures
now carry it (JSON-LD on the detail page, and /ingest backfills from search cards),
and extraction fills it from the ad text. This repairs the rows captured before that.

Only ever fills NULL: a company already captured from Seek is never overwritten.
One small-tier LLM call per row (a fraction of a cent each). Makes no request to Seek.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASES = {"real": "real.db", "test": "app.db", "eval": "evals/eval.db"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("env", choices=sorted(DATABASES))
    parser.add_argument("--dry-run", action="store_true", help="list the rows without calling the LLM")
    args = parser.parse_args()

    # Before any app import: db.py reads DATABASE_URL at import time.
    os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / DATABASES[args.env]).as_posix()}"
    sys.path.insert(0, str(ROOT))

    # Bring the DB to the current schema first, as run_api.py does on startup;
    # the models expect every column, so an un-migrated DB fails on the first row.
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.llm.client import BudgetExceededError, DailyQuotaError
    from app.llm.extract import infer_employer_name
    from app.models import JobListing

    with SessionLocal() as db:
        rows = db.execute(
            select(JobListing.id, JobListing.title)
            .where((JobListing.company.is_(None)) | (JobListing.company == ""))
            .where(JobListing.raw_description.isnot(None))
            .order_by(JobListing.id)
        ).all()
    print(f"{args.env}: {len(rows)} job(s) with no company and a stored description")
    if args.dry_run:
        for job_id, title in rows:
            print(f"  {job_id:>5}  {title}")
        return 0

    filled = 0
    for job_id, title in rows:
        try:
            name = infer_employer_name(job_id)
        except (DailyQuotaError, BudgetExceededError) as exc:
            print(f"Stopped: {exc}")
            return 1
        filled += bool(name)
        print(f"  {job_id:>5}  {title[:55]:55}  -> {name or '(not named in the ad)'}")
    print(f"Filled {filled} of {len(rows)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
