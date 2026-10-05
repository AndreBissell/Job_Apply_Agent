"""Read-only check: do matches.status and matches.applied_at agree on "applied"?

The dashboard, retention and the evidence export count an application by
``applied_at IS NOT NULL``. Two kinds of row disagree with ``status``:

  * applied_at set, status != 'applied': a forced re-score reset the status
    (the Regenerate bug fixed on branch fix/applied-evidence-safety). These were
    missing from the evidence export before the fix; they count everywhere now.
  * status = 'applied', applied_at NULL: applied before the column existed
    (migration 60d65ffb16df did not backfill). Not on the dashboard, and since the
    fix not in the evidence export either.

Opens the SQLite file in read-only mode (``mode=ro``), so it cannot write.

    python scripts/check_applied_consistency.py              # app.db (test)
    python scripts/check_applied_consistency.py real.db      # the real profile
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

QUERIES = {
    "applied_at set, status != 'applied'":
        "SELECT m.id, m.job_id, m.status, m.applied_at, j.title, j.company "
        "FROM matches m JOIN job_listings j ON j.id = m.job_id "
        "WHERE m.applied_at IS NOT NULL AND m.status != 'applied' ORDER BY m.applied_at",
    "status = 'applied', applied_at NULL":
        "SELECT m.id, m.job_id, m.status, m.applied_at, j.title, j.company "
        "FROM matches m JOIN job_listings j ON j.id = m.job_id "
        "WHERE m.status = 'applied' AND m.applied_at IS NULL ORDER BY m.id",
}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "app.db").resolve()
    if not path.is_file():
        print(f"No such database: {path}")
        return 2
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        total = conn.execute("SELECT COUNT(*) FROM matches WHERE applied_at IS NOT NULL").fetchone()[0]
        print(f"{path.name}: {total} match(es) with applied_at set")
        mismatched = 0
        for label, sql in QUERIES.items():
            rows = conn.execute(sql).fetchall()
            mismatched += len(rows)
            print(f"\n{label}: {len(rows)}")
            for mid, job_id, status, applied_at, title, company in rows:
                print(f"  match {mid} / job {job_id}  status={status!r}  applied_at={applied_at}  "
                      f"{title} @ {company or '?'}")
    finally:
        conn.close()
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
