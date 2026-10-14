"""Set up a hand-test of the background availability checks on the TEST database (app.db).

    python scripts/availability_demo.py setup     # add 2 fake "closed" jobs, make 1 real job due
    python scripts/availability_demo.py show      # print where the demo jobs stand
    python scripts/availability_demo.py strike2   # pretend a day passed, so the second strike can happen
    python scripts/availability_demo.py cleanup   # remove the fake jobs

It only ever opens ``app.db`` (the TEST environment's file), never ``real.db``. The fake jobs are
titled "FAKE closed ad N" and point at Seek ids that do not exist, so the panel's check should see a
page with no description. They score 65, below the default auto-letter bar, so no letter can be
written for them. The real job is one you captured from Seek that has a description; the panel must
see it live in the same session, or the empty pages are (correctly) not counted.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "app.db"
FAKE_IDS = ["99999901", "99999902"]


def ts(days_ago: float = 0) -> str:
    """A timestamp the way SQLite stores them here (naive UTC)."""
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


def connect() -> sqlite3.Connection:
    if not DB.exists():
        sys.exit(f"{DB} not found. Start the TEST backend once first: python scripts/run_api.py test")
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    columns = {row[1] for row in con.execute("PRAGMA table_info(job_listings)")}
    if "availability_misses" not in columns:
        con.close()
        sys.exit("app.db has not been upgraded yet. Start the TEST backend once "
                 "(python scripts/run_api.py test); it upgrades the database on startup. Then run this again.")
    return con


def pick_real_job(con: sqlite3.Connection):
    """A job captured from Seek, with a description and a match scoring 60+, not applied.
    One that already has a letter comes first: once the panel sees it live, the AI pipeline may
    act on it again, and a job with a letter is not given a new one."""
    return con.execute(
        "SELECT j.id, j.title, j.url, m.score FROM job_listings j JOIN matches m ON m.job_id = j.id"
        " WHERE j.source = 'seek' AND j.raw_description IS NOT NULL AND j.title NOT LIKE 'FAKE%'"
        " AND j.expired_detected_at IS NULL AND m.user_id = 1 AND m.applied_at IS NULL"
        " AND m.hidden_at IS NULL AND m.score >= 60"
        " ORDER BY EXISTS (SELECT 1 FROM cover_letters c WHERE c.match_id = m.id) DESC, m.score DESC LIMIT 1"
    ).fetchone()


def make_due(con: sqlite3.Connection, job_id: int) -> None:
    con.execute("UPDATE job_listings SET last_seen_live_at = ?, availability_checked_at = NULL,"
                " availability_misses = 0 WHERE id = ?", (ts(10), job_id))


def setup(con: sqlite3.Connection) -> None:
    for n, sid in enumerate(FAKE_IDS, start=1):
        if con.execute("SELECT 1 FROM job_listings WHERE source = 'seek' AND source_job_id = ?", (sid,)).fetchone():
            continue
        cur = con.execute(
            "INSERT INTO job_listings (source, source_job_id, url, title, raw_description, extracted_at,"
            " last_seen_live_at) VALUES ('seek', ?, ?, ?, 'old ad text', ?, ?)",
            (sid, f"https://au.seek.com/job/{sid}", f"FAKE closed ad {n}", ts(), ts(10)),
        )
        con.execute("INSERT INTO matches (user_id, job_id, score, status) VALUES (1, ?, 65, 'new')", (cur.lastrowid,))
    real = pick_real_job(con)
    if real is None:
        con.rollback()
        sys.exit("No real Seek job to use as the 'live' page. Capture a few jobs first (open a Seek job "
                 "page, or Scan Page on the TEST backend), wait for them to be scored, then run setup again.")
    make_due(con, real[0])
    con.commit()
    print("Added 2 fake closed jobs (FAKE closed ad 1 and 2).")
    print(f"Made this real job due for a check (it should come back 'live'):\n  [{real[0]}] {real[1]}  score {real[3]}\n  {real[2]}")
    print("\nNext: open the side panel on the TEST backend and wait about 60 seconds, then run 'show'.")


def show(con: sqlite3.Connection) -> None:
    rows = con.execute(
        "SELECT id, title, availability_misses, expired_detected_at IS NOT NULL, availability_checked_at"
        " FROM job_listings WHERE title LIKE 'FAKE%' OR availability_checked_at IS NOT NULL"
        " ORDER BY availability_checked_at DESC"
    ).fetchall()
    if not rows:
        print("Nothing checked yet.")
        return
    print(f"{'id':>5}  {'strikes':>7}  {'flagged':>7}  {'last checked (UTC)':<26}  title")
    for jid, title, misses, flagged, checked in rows:
        print(f"{jid:>5}  {misses:>7}  {'YES' if flagged else 'no':>7}  {(checked or 'never')[:26]:<26}  {title[:50]}")


def strike2(con: sqlite3.Connection) -> None:
    con.execute("UPDATE job_listings SET availability_checked_at = ? WHERE title LIKE 'FAKE%'", (ts(2),))
    real = pick_real_job(con)
    if real:
        make_due(con, real[0])
        print(f"Made the real job due again: [{real[0]}] {real[1]}")
    con.commit()
    print("Pretended a day has passed. Reload the side panel and wait about 60 seconds, then run 'show'.")


def cleanup(con: sqlite3.Connection) -> None:
    n = con.execute("DELETE FROM job_listings WHERE title LIKE 'FAKE closed ad%'").rowcount
    con.commit()
    print(f"Removed {n} fake job(s) (their matches go with them).")


COMMANDS = {"setup": setup, "show": show, "strike2": strike2, "cleanup": cleanup}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    connection = connect()
    try:
        COMMANDS[sys.argv[1]](connection)
    finally:
        connection.close()
