"""End-to-end check of the background availability checks: the side panel's checker against a
scratch backend (app/availability.py, extension/sidebar_availability.js).

    python tests/e2e/availability_e2e.py            # headless Chromium
    python tests/e2e/availability_e2e.py --headed
    python tests/e2e/availability_e2e.py --keep     # keep the work folder

Not collected by pytest (needs Chromium and a server). Nothing reaches Seek:
- Every request off this machine is aborted, and the panel's browser APIs for opening and reading
  a tab (``chrome.tabs.create`` / ``remove``, ``chrome.scripting.executeScript``) are STUBBED, so no
  tab is ever made (a real tab can start loading before routing attaches to it). What the "page"
  shows is canned per URL (a live ad, an empty page, a redirect, a challenge page).
- The server runs with LLM_PROVIDER=stub, every job is extracted and matched, and the letter bars
  are 100, so the idle loop has no model work; a stray call would fail instead of costing money.

What it checks, in order: the order jobs are visited in and the pacing; one miss is only a strike;
the canary (a session with no live page flags nothing); a later live page corroborates the held
misses and the second strike flags; the flagged job drops out of GET /jobs and a capture with a
description brings it back; a redirect proves nothing; an applied job is never visited; a scan
cancels the page being checked and blocks a session; a challenge page and five empty pages in a row
pause the checks; the Personalise toggle; the service worker's scan-tab set empties again.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dashboard_e2e as d  # noqa: E402  (its server/check helpers; importing it runs nothing)

ROOT, EXTENSION, BACKEND, PORT = d.ROOT, d.EXTENSION, d.BACKEND, d.PORT
Checks, wait_for = d.Checks, d.wait_for


def ago(days: float = 0, hours: float = 0) -> str:
    """A naive-UTC timestamp string as SQLite stores it."""
    t = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


def seek_url(n: int) -> str:
    return f"https://au.seek.com/job/{n}"


LIVE = {"host": "au.seek.com", "descLength": 900, "challenge": False}
EMPTY = {"host": "au.seek.com", "descLength": 0, "challenge": False}


def page(n: int, base: dict, title: str = "Job | SEEK") -> dict:
    return {**base, "path": f"/job/{n}", "title": title}


def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_availability.db"
    url = "sqlite:///" + db.as_posix()
    import os
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import CoverLetter, Profile

    engine = create_engine(url)
    with Session(engine) as s:
        s.add(Profile(id=1, name="E2E Test", email="e2e@example.invalid", password_hash="x",
                      preferences=json.dumps({"auto_cover_letter_min_score": 100, "letter_loop_min_score": 100})))
        s.commit()
        engine.dispose()
    return db


def add_job(db: Path, n: int, score: float, *, applied: bool = False, letter: bool = False,
            seen_days: float = 10, checked_days: float | None = None) -> None:
    """A stored listing, extracted and matched (so the idle loop never has AI work for it),
    last seen live `seen_days` ago."""
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO job_listings (id, source, source_job_id, url, title, raw_description, extracted_at,"
        " last_seen_live_at, availability_checked_at) VALUES (?, 'seek', ?, ?, ?, 'An ad', ?, ?, ?)",
        (n, str(n), f"{seek_url(n)}?type=standard&ref=search", f"Job {n}", ago(), ago(seen_days),
         ago(checked_days) if checked_days is not None else None),
    )
    con.execute(
        "INSERT INTO matches (id, user_id, job_id, score, status, applied_at) VALUES (?, 1, ?, ?, ?, ?)",
        (n, n, score, "applied" if applied else "new", ago() if applied else None),
    )
    if letter:
        con.execute("INSERT INTO cover_letters (match_id, generated_content, status) VALUES (?, 'Dear', 'draft')", (n,))
    con.commit()
    con.close()


def row(con: sqlite3.Connection, n: int) -> dict:
    cur = con.execute(
        "SELECT availability_misses, expired_detected_at, availability_checked_at, last_seen_live_at"
        " FROM job_listings WHERE id = ?", (n,))
    misses, expired, checked, seen = cur.fetchone()
    return {"misses": misses, "expired": expired, "checked": checked, "seen": seen}


def api(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(BACKEND + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=10))


INIT = """
(() => {
  // The one delayed start the panel arms (60 s) would fire mid-test: never let it.
  const realSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = (fn, ms, ...rest) => (ms === 60000 ? 0 : realSetTimeout(fn, ms, ...rest));
})();
"""

STUBS = """
() => {
  window.__opened = []; window.__removed = []; window.__paces = []; window.__injected = [];
  window.__pages = {}; window.__tabUrl = {}; let seq = 100;
  chrome.tabs.create = async (o) => { const id = ++seq; window.__opened.push(o); window.__tabUrl[id] = o.url; return { id }; };
  window.__hang = {};
  // Closing a tab makes a script still running in it fail, as it does in Chrome.
  chrome.tabs.remove = async (id) => { window.__removed.push(id); if (window.__hang[id]) window.__hang[id](new Error('tab closed')); };
  chrome.scripting.executeScript = async ({ target, func, args }) => {
    window.__injected.push({ name: func.name, args });
    const canned = window.__pages[window.__tabUrl[target.tabId]];
    if (canned === 'hang') return new Promise((_, reject) => { window.__hang[target.tabId] = reject; });
    if (canned === 'throw' || canned === undefined) throw new Error('tab is gone');
    return [{ result: canned }];
  };
  window.waitForTabComplete = async () => {};
  window.seekPace = async (gap) => { window.__paces.push(gap); };
}
"""


def run(headed: bool, keep: bool) -> int:
    from playwright.sync_api import sync_playwright

    if not d.port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")
    work = Path(tempfile.mkdtemp(prefix="avail_e2e_"))
    db = make_scratch_db(work)
    # 1 live (letter ready), 2 and 7 are closed ads, 3 redirects, 4 is applied (and would look closed).
    add_job(db, 1, 90, letter=True)
    add_job(db, 2, 80)
    add_job(db, 7, 75)
    add_job(db, 3, 70)
    add_job(db, 4, 95, applied=True)
    stub_log = work / "llm_stub.jsonl"
    server = d.start_server(db, work / "server.log", stub_log)
    con = sqlite3.connect(db)
    usage_before = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    c = Checks()
    blocked: list[str] = []
    errors: list[str] = []

    def intercepted(u: str) -> bool:
        url = urlparse(u)
        if url.scheme in ("chrome-extension", "blob", "data"):
            return False
        return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

    def route(r):
        blocked.append(r.request.url)
        return r.abort()

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(work / "profile"), channel="chromium", headless=not headed,
                viewport={"width": 380, "height": 900},
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )
            ctx.route(intercepted, route)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
            ext_id = sw.url.split("/")[2]

            side = ctx.new_page()
            side.on("pageerror", lambda e: errors.append(str(e)))
            side.add_init_script(INIT)
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")
            side.wait_for_function("typeof runAvailabilitySession === 'function' && typeof BACKEND === 'string'")
            side.evaluate(STUBS)
            c.check(side.evaluate("chrome.tabs.create.toString().includes('__opened')"), "tab opening is stubbed")

            def set_pages(mapping: dict) -> None:
                side.evaluate("m => { window.__pages = m; }", mapping)

            def reset_counters() -> None:
                side.evaluate("() => { window.__opened = []; window.__removed = []; window.__paces = []; window.__injected = []; }")

            def run_session() -> str:
                return side.evaluate("runAvailabilitySession()")

            def opened_ids() -> list[int]:
                return [int(urlparse(o["url"]).path.rsplit("/", 1)[1]) for o in side.evaluate("window.__opened")]

            # --- Session 1: order, pacing, one miss is only a strike ---------------------
            print("Session 1: order, pacing and strikes")
            set_pages({seek_url(1): page(1, LIVE), seek_url(2): page(2, EMPTY), seek_url(7): page(7, EMPTY),
                       seek_url(3): {**LIVE, "path": "/jobs", "title": "Jobs | SEEK"},
                       seek_url(4): page(4, EMPTY)})
            c.check(run_session() == "done", "the session runs")
            ids = opened_ids()
            c.check(ids == [1, 2, 7, 3], f"visited letter-ready first, then by score; applied job 4 never ({ids})")
            opened = side.evaluate("window.__opened")
            c.check(all(o["active"] is False for o in opened), "every check tab is a background tab (active: false)")
            c.check([urlparse(o["url"]).query for o in opened] == [""] * 4 and
                    all(urlparse(o["url"]).netloc == "au.seek.com" for o in opened),
                    "URLs are rebuilt as https://au.seek.com/job/{id} (the stored query string is dropped)")
            c.check(side.evaluate("window.__paces") == [15000] * 4, "each page waited the shared 15 s pace first")
            c.check(sorted(side.evaluate("window.__removed")) == [101, 102, 103, 104], "every check tab was closed")
            inj = side.evaluate("window.__injected")
            c.check({i["name"] for i in inj} == {"pageCheckListing"} and
                    all(i["args"][0]["description"] == side.evaluate("SELECTORS.DETAIL_DESCRIPTION") for i in inj),
                    "only the description/title reader is injected, with the description selector")
            r1, r2, r7, r3, r4 = (row(con, n) for n in (1, 2, 7, 3, 4))
            c.check(r1["misses"] == 0 and r1["checked"], "live job: checked, no strikes")
            c.check(r2["misses"] == 1 and r2["expired"] is None, "empty page after a live one: one strike, not flagged")
            c.check(r7["misses"] == 1 and r7["expired"] is None, "second empty page: one strike, not flagged")
            c.check(r3["misses"] == 0 and r3["expired"] is None and r3["checked"], "redirect away: checked, proves nothing")
            c.check(r4["checked"] is None and r4["misses"] == 0, "the applied job was never checked")
            jobs = {j["job_id"] for j in api("/jobs?profile_id=1")}
            c.check({1, 2, 3, 7} <= jobs, "nothing is hidden from the list after one strike")

            # --- Session 2: the canary ---------------------------------------------------
            print("Session 2: no live page, so nothing is flagged")
            con.execute("UPDATE job_listings SET availability_checked_at = ? WHERE id IN (2, 7)", (ago(2),))
            con.commit()
            reset_counters()
            c.check(run_session() == "done", "second session runs")
            c.check(sorted(opened_ids()) == [2, 7], f"only the two jobs with a strike were due ({opened_ids()})")
            r2, r7 = row(con, 2), row(con, 7)
            c.check(r2["misses"] == 1 and r2["expired"] is None and r7["misses"] == 1 and r7["expired"] is None,
                    "empty pages with no live page in the session: still one strike, nothing flagged")
            c.check(r2["checked"] > ago(0.1)[:10] and True, "...but they were stamped checked (they go to the back of the line)")

            # --- Session 3: a live page corroborates; the second strike flags -------------
            print("Session 3: live page corroborates, second strike flags")
            con.execute("UPDATE job_listings SET availability_checked_at = ? WHERE id IN (2, 7)", (ago(2),))
            con.execute("UPDATE job_listings SET availability_checked_at = ?, last_seen_live_at = ? WHERE id = 3",
                        (ago(5), ago(5)))
            con.commit()
            reset_counters()
            set_pages({seek_url(3): page(3, LIVE), seek_url(2): page(2, EMPTY), seek_url(7): page(7, EMPTY)})
            run_session()
            c.check(opened_ids() == [2, 7, 3], f"visited by score: the two strikes, then the live one ({opened_ids()})")
            r2, r7, r3 = row(con, 2), row(con, 7), row(con, 3)
            c.check(r2["expired"] and r2["misses"] == 2 and r7["expired"] and r7["misses"] == 2,
                    "held misses are sent once a live page is seen: both flagged on the second strike")
            c.check(r3["expired"] is None, "the live job stays unflagged")
            listed = {j["job_id"] for j in api("/jobs?profile_id=1")}
            c.check(2 not in listed and 7 not in listed and {1, 3} <= listed,
                    "flagged jobs drop out of GET /jobs; the rest stay")
            applied_view = {j["job_id"] for j in api("/jobs?profile_id=1&status=applied")}
            c.check(applied_view == {4}, "the Applied view is untouched")

            # --- A later capture with a description brings a job back ---------------------
            print("Un-expire")
            api("/ingest", "POST", {"listings": [{"source_job_id": "2", "url": seek_url(2), "title": "Job 2",
                                                  "raw_description": "Back again"}]})
            r2 = row(con, 2)
            c.check(r2["expired"] is None and r2["misses"] == 0, "a capture with a description un-expires the job")
            c.check(2 in {j["job_id"] for j in api("/jobs?profile_id=1")}, "...and it is back in the list")

            # --- A scan wins ------------------------------------------------------------------
            print("A scan wins")
            con.execute("UPDATE job_listings SET availability_checked_at = ?, last_seen_live_at = ? WHERE id = 1",
                        (ago(10), ago(10)))
            con.execute("UPDATE job_listings SET availability_checked_at = ?, last_seen_live_at = ? WHERE id != 1",
                        (ago(0.01), ago(0.01)))
            con.commit()
            before = row(con, 1)["checked"]
            reset_counters()
            set_pages({seek_url(1): "hang"})
            # A function body, so evaluate() does not wait for the session it starts.
            side.evaluate("() => { window.__session = runAvailabilitySession(); }")
            wait_for(lambda: side.evaluate("window.__opened.length") == 1)
            c.check(side.evaluate("window.__opened.length") == 1, "a check page is open")
            t0 = time.time()
            side.evaluate("cancelAvailabilityCheck()")
            c.check(time.time() - t0 < 5, "cancelling returns at once (it does not wait for the page)")
            c.check(side.evaluate("window.__removed.length") >= 1, "the check tab was closed")
            c.check(side.evaluate("window.__session") == "done", "the session ends")
            c.check(row(con, 1)["checked"] == before, "a cancelled check records no verdict at all")
            side.evaluate("scanning = true")
            reset_counters()
            c.check(run_session() == "busy" and side.evaluate("window.__opened.length") == 0,
                    "no session starts while a scan is running")
            side.evaluate("scanning = false")

            # --- A challenge page pauses everything --------------------------------------
            print("Challenge page and empty streak")
            reset_counters()
            set_pages({seek_url(1): {**EMPTY, "path": "/job/1", "title": "Just a moment...", "challenge": True}})
            run_session()
            c.check(opened_ids() == [1], "a challenge page ends the session after that page")
            c.check(row(con, 1)["misses"] == 0 and row(con, 1)["checked"] != before,
                    "...recorded as 'unknown', not as a miss")
            cooldown = sw.evaluate("chrome.storage.local.get('availabilityCooldownUntil')")
            c.check(cooldown.get("availabilityCooldownUntil", 0) > time.time() * 1000 + 5 * 3600 * 1000,
                    "a 6-hour pause is stored")
            reset_counters()
            con.execute("UPDATE job_listings SET availability_checked_at = ?, last_seen_live_at = ? WHERE id = 1",
                        (ago(10), ago(10)))
            con.commit()
            run_session()
            c.check(side.evaluate("window.__opened.length") == 0, "during the pause no page is opened")
            sw.evaluate("chrome.storage.local.remove('availabilityCooldownUntil')")

            # five empty pages in a row, nothing live: held, then sent as unknown, then pause
            con.execute("UPDATE job_listings SET availability_checked_at = ?, last_seen_live_at = ?",
                        (ago(0.01), ago(0.01)))
            con.commit()
            for n in range(20, 27):
                add_job(db, n, 85 - (n - 20), seen_days=10)
            reset_counters()
            set_pages({seek_url(n): page(n, EMPTY) for n in range(20, 27)})
            run_session()
            c.check(opened_ids() == [20, 21, 22, 23, 24], f"five empty pages in a row stop the session ({opened_ids()})")
            c.check(all(row(con, n)["misses"] == 0 and row(con, n)["expired"] is None for n in range(20, 25)),
                    "none of those empty pages became a strike (no live page corroborated them)")
            c.check(sw.evaluate("chrome.storage.local.get('availabilityCooldownUntil')").get("availabilityCooldownUntil"),
                    "...and the checks are paused again")
            sw.evaluate("chrome.storage.local.remove('availabilityCooldownUntil')")

            # --- The Personalise toggle ----------------------------------------------------
            print("Toggle")
            side.click("nav#tabs .tab[data-tab=jobs]")  # Personalise lives on the Jobs tab
            side.click("#personalise-toggle")
            box = side.locator("#availability-checks")
            c.check(box.is_checked(), "the Personalise checkbox is on by default")
            box.uncheck()
            c.check(wait_for(lambda: api("/profile/1/preferences")["availability_checks_enabled"] is False),
                    "unticking saves availability_checks_enabled = false")
            reset_counters()
            con.execute("UPDATE job_listings SET availability_checked_at = NULL, last_seen_live_at = ?", (ago(10),))
            con.commit()
            run_session()
            c.check(side.evaluate("window.__opened.length") == 0, "switched off: no page is opened")
            c.check(api("/availability/next")["job"] is None, "switched off: the backend offers nothing")
            box.check()
            c.check(wait_for(lambda: api("/profile/1/preferences")["availability_checks_enabled"] is True),
                    "ticking it again saves true")

            # --- The service worker's tab set ----------------------------------------------
            saved = sw.evaluate("chrome.storage.session.get('scanTabs')")
            c.check(saved.get("scanTabs") == [], f"the scan-tab set (kept in storage.session) is empty again ({saved})")

            c.check(not errors, f"no page errors {errors[:3]}")
            usage_after = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            c.check(usage_after == usage_before, "no llm_usage row written")
            c.check(not stub_log.exists() or not stub_log.read_text(encoding="utf-8").strip(), "no model call")
            c.check(not blocked, f"nothing left the machine (no Seek request either) {blocked[:3]}")
            ctx.close()
    finally:
        con.close()
        d.stop_server(server)

    failed = c.failed()
    print(f"\n{len(c.results) - failed}/{len(c.results)} checks passed. Logs: {work}")
    if not failed and not headed and not keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.headed, args.keep))
