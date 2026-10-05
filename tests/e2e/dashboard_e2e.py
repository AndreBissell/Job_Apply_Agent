"""End-to-end check of the Centrelink dashboard: the Overview and Applied tabs of the side
panel against a scratch backend (docs/centrelink-dashboard-plan.md).

    python tests/e2e/dashboard_e2e.py            # headless Chromium
    python tests/e2e/dashboard_e2e.py --headed
    python tests/e2e/dashboard_e2e.py --keep     # keep the screenshots

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB in a temp folder (alembic head) holding profile 1, applied
   jobs across three monthly periods (one hidden, one with no logged AI spend), seven jobs
   with a letter ready (one with your edits), one job whose letter run waits on a question,
   one high scorer with no letter, and some ``llm_usage`` rows. Dates are relative to
   today, so the run works on any day. The letter bars are set to 100 and no job has a
   description, so the idle loop has no LLM work; the server runs with LLM_PROVIDER=stub
   anyway, so a stray call would fail instead of costing money.
2. Starts ``scripts/run_api.py test --db <scratch>`` on port 8001 and opens the unpacked
   extension's side panel (TEST backend). Every request off this machine is aborted, and
   ``chrome.tabs.create`` is stubbed to record the URL: the panel never opens a tab, so
   nothing reaches Seek (a real tab can start loading before routing attaches to it).
3. Checks the Overview (opens first, start-date prompt, progress, waiting letters, the
   Keep Applying row and its arrows, Copy / Open / Mark applied, the suggested searches,
   the ✎ editor) and the Applied tab (periods newest first with the current one open,
   counts against the target, cost and "not costed", + Interview set and undo, the
   per-period and full CSV exports), at a narrow and a wide panel width. Then deleting:
   no Delete on an applied job's Jobs card, the backend's 409, and the Applied tab's Delete
   (cancel, confirm, the row, DB rows and period count gone).

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
EXTENSION = ROOT / "extension"
PORT = 8001
BACKEND = f"http://127.0.0.1:{PORT}"
EDITED_LETTER = "Dear Hiring Manager, this is MY EDITED letter. Sincerely, E2E Test"

TODAY = dt.date.today()
ANCHOR = TODAY - dt.timedelta(days=3)  # the period start the test types in


def add_months(d: dt.date, k: int) -> dt.date:
    from app.obligation import add_months as _add
    return _add(d, k)


def local_noon_utc(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, 12).astimezone().astimezone(dt.timezone.utc)


def fmt(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# Scratch DB
# ---------------------------------------------------------------------------
def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_dashboard.db"
    url = "sqlite:///" + db.as_posix()
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    seed(url)
    return db


def seed(url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.llm.letter.state import JobInfo, LetterState
    from app.models import CoverLetter, JobListing, LetterRun, LlmUsage, Match, Profile, Skill

    engine = create_engine(url)
    with Session(engine) as s:
        s.add(Profile(id=1, name="E2E Test", email="e2e@example.invalid", password_hash="x",
                      target_role="Data Analyst", target_location="Brisbane",
                      preferences=json.dumps({"auto_cover_letter_min_score": 100, "letter_loop_min_score": 100})))
        s.add_all([Skill(user_id=1, name="SQL"), Skill(user_id=1, name="Power BI")])
        s.flush()
        next_id = iter(range(1, 1000))

        def job(title, score, *, applied=None, hidden=False, letter=None, edited=None, company=None):
            jid = next(next_id)
            s.add(JobListing(id=jid, source="seek", source_job_id=f"9{jid:04d}",
                             url=f"https://au.seek.com/job/9{jid:04d}", title=title,
                             company=company or f"Company {jid}", location="Brisbane QLD",
                             extracted_at=dt.datetime.now(dt.timezone.utc)))
            s.flush()
            s.add(Match(id=jid, user_id=1, job_id=jid, score=score, status="applied" if applied else "new",
                        applied_at=local_noon_utc(applied) if applied else None,
                        hidden_at=dt.datetime.now(dt.timezone.utc) if hidden else None))
            s.flush()
            if letter:
                s.add(CoverLetter(match_id=jid, generated_content=letter, edited_content=edited))
            return jid

        def cost(jid, usd):
            s.add(LlmUsage(task="match", tier="small", model="stub", cost_usd=Decimal(str(usd)), job_id=jid))

        # Current period (starts ANCHOR): 3 applications, one hidden, one never costed.
        cur = [job("Current Analyst A", 91, applied=ANCHOR),
               job("Current Analyst B", 84, applied=TODAY - dt.timedelta(days=1), hidden=True),
               job("Current Analyst C", 77, applied=TODAY)]
        cost(cur[0], 0.12)
        cost(cur[0], 0.08)
        cost(cur[1], 0.05)
        # Previous period: 2 applications.
        prev_start = add_months(ANCHOR, -1)
        for i in range(2):
            cost(job(f"Previous Role {i}", 80, applied=prev_start + dt.timedelta(days=i + 1)), 0.10)
        # Two back: 21 applications (target met).
        two_back = add_months(ANCHOR, -2)
        for i in range(21):
            cost(job(f"Older Role {i:02d}", 70 + i % 20, applied=two_back + dt.timedelta(days=i % 25)), 0.01)

        # Keep Applying: 7 ready letters (scores out of order on purpose), one with edits.
        for title, score in [("Ready 88", 88), ("Ready 96", 96), ("Ready 76", 76), ("Ready 92", 92),
                             ("Ready 86", 86), ("Ready 80", 80), ("Ready 78", 78)]:
            job(title, score, letter=f"Letter for {title}",
                edited=EDITED_LETTER if score == 96 else None)
        # Not ready: a letter paused on a question, and a high scorer with no letter.
        waiting = job("Waiting On You", 97, letter="older letter")
        s.flush()
        state = LetterState(profile_id=1, job=JobInfo(job_id=waiting, title="Waiting On You"))
        s.add(LetterRun(match_id=waiting, engine="agent", status="waiting_user", state=state.model_dump_json()))
        job("No Letter Yet", 95)
        s.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def port_free() -> bool:
    try:
        urllib.request.urlopen(f"{BACKEND}/health", timeout=1)
        return False
    except Exception:
        return True


def start_server(db: Path, log_path: Path, stub_log: Path) -> subprocess.Popen:
    env = {**os.environ, "LLM_PROVIDER": "stub", "PYTHONUNBUFFERED": "1", "LLM_STUB_LOG": str(stub_log)}
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "scripts/run_api.py", "test", "--db", str(db)], cwd=ROOT,
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            health = json.load(urllib.request.urlopen(f"{BACKEND}/health", timeout=1))
            if health.get("env") != "test":
                raise SystemExit(f"backend on {PORT} is not the test env: {health}")
            return proc
        except SystemExit:
            raise
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit(f"backend did not start; see {log_path}")


def stop_server(proc: subprocess.Popen) -> None:
    if os.name == "nt":  # uvicorn's reloader has a child process
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
    proc.wait(timeout=15)


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, what: str) -> None:
        self.results.append((bool(ok), what))
        print(("  ok   " if ok else "  FAIL ") + what)

    def failed(self) -> int:
        return sum(1 for ok, _ in self.results if not ok)


def wait_for(fn, timeout_s: float = 10, every: float = 0.2):
    end = time.time() + timeout_s
    value = fn()
    while not value and time.time() < end:
        time.sleep(every)
        value = fn()
    return value


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def run(headed: bool, keep: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    if not port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")
    work = Path(tempfile.mkdtemp(prefix="dash_e2e_"))
    db = make_scratch_db(work)
    stub_log = work / "llm_stub.jsonl"
    server = start_server(db, work / "server.log", stub_log)
    con = sqlite3.connect(db)
    usage_before = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    c = Checks()
    blocked: list[str] = []
    errors: list[str] = []

    def route(r):
        url = urlparse(r.request.url)
        blocked.append(r.request.url)
        return r.abort()

    def intercepted(u: str) -> bool:
        url = urlparse(u)
        if url.scheme in ("chrome-extension", "blob", "data"):
            return False
        return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(work / "profile"), channel="chromium", headless=not headed, accept_downloads=True,
                viewport={"width": 380, "height": 900},
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )
            ctx.route(intercepted, route)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
            ext_id = sw.url.split("/")[2]

            side = ctx.new_page()
            side.on("pageerror", lambda e: errors.append(str(e)))
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")
            # The clipboard needs a focused, permitted page; record what would be copied. Tabs
            # the panel opens are recorded, NEVER opened: a tab made by chrome.tabs.create can
            # start loading before Playwright's routing attaches to it, so it would reach Seek.
            side.evaluate("""() => { window.__copied = null; window.__opened = [];
                navigator.clipboard.writeText = t => { window.__copied = t; return Promise.resolve(); };
                chrome.tabs.create = o => { window.__opened.push(o.url); return Promise.resolve({}); }; }""")
            c.check(side.evaluate("chrome.tabs.create.toString().includes('__opened')"), "tab opening is stubbed")

            def opened_url(click):
                before = side.evaluate("window.__opened.length")
                click()
                wait_for(lambda: side.evaluate("window.__opened.length") > before)
                return side.evaluate("window.__opened.at(-1) || ''")

            # --- Overview opens first, with the start-date prompt -------------------
            print("Overview")
            side.locator("#ov-progress .ov-setup").wait_for(timeout=15000)
            c.check(side.locator("nav#tabs .tab.active").inner_text() == "Overview", "opens on the Overview tab")
            c.check(side.locator("nav#tabs .tab").first.inner_text() == "Overview", "Overview is the first tab")
            c.check(side.is_hidden("#jobs-section") and side.is_visible("#overview-section"), "only the Overview shows")
            c.check(side.is_visible("#hdr-jobs-btns"), "Scan Page / Refresh show on the Overview")
            setup = side.inner_text("#ov-progress")
            c.check("When does your Centrelink month start?" in setup, "no start date: the prompt shows")
            c.check("by calendar month" in setup, "the prompt says it counts by calendar month until set")

            # --- set the start date --------------------------------------------------
            side.fill("#ov-progress input[type=date]", ANCHOR.isoformat())
            side.fill("#ov-progress input[type=number]", "20")
            side.click("#ov-progress button[type=submit]")
            side.locator("#ov-progress .ov-count").wait_for(timeout=10000)
            end = add_months(ANCHOR, 1) - dt.timedelta(days=1)
            days_left = (end - TODAY).days + 1
            prog = side.inner_text("#ov-progress")
            c.check(side.inner_text("#ov-progress .big") == "3", "counter: 3 this period (hidden one included)")
            c.check("/ 20" in prog, "counter: out of 20")
            c.check(f"{fmt(ANCHOR)} – {fmt(end)}" in prog, f"period shown as {fmt(ANCHOR)} – {fmt(end)}")
            c.check(f"{days_left} days left" in prog and "17 to go" in prog, "days left and to go")
            width = side.evaluate("document.querySelector('#ov-progress .ov-fill').style.width")
            c.check(width == "15%", f"progress bar at 15% (got {width})")
            prefs = json.load(urllib.request.urlopen(f"{BACKEND}/profile/1/preferences"))
            c.check(prefs["obligation_cycle_start"] == ANCHOR.isoformat(), "start date saved as YYYY-MM-DD")

            # --- waiting letters -----------------------------------------------------
            waiting = side.locator("#ov-progress .ov-waiting")
            c.check(waiting.count() == 1 and "1 letter needs your answer" in waiting.inner_text(),
                    "waiting letters row: 1 letter needs your answer")
            waiting.click()
            c.check(side.locator("nav#tabs .tab.active").inner_text() == "Jobs", "the waiting row opens the Jobs tab")
            side.click("nav#tabs .tab[data-tab=overview]")
            side.locator("#ov-progress .ov-count").wait_for(timeout=10000)

            # --- Keep Applying -------------------------------------------------------
            side.locator("#ov-scroller .mini-card").first.wait_for(timeout=10000)
            cards = side.locator("#ov-scroller .mini-card")
            titles = [cards.nth(i).locator(".mc-title").inner_text() for i in range(cards.count())]
            c.check(titles == ["Ready 96", "Ready 92", "Ready 88", "Ready 86", "Ready 80", "Ready 78", "Ready 76"],
                    f"7 ready letters, best first, none waiting/letter-less/applied (got {titles})")
            c.check(side.inner_text("#ov-ready-count") == "7 letters ready to send", "ready count under the heading")
            c.check(cards.first.get_attribute("data-tier") == "gold" and cards.nth(1).get_attribute("data-tier") == "blue",
                    "score tiers colour the cards")
            card_w = side.evaluate("document.querySelector('.mini-card').getBoundingClientRect().width")
            c.check(195 <= card_w <= 205, f"mini-cards ~200px wide (got {card_w:.0f})")

            # arrows
            c.check(side.is_visible("#ov-nav"), "arrows show when the row overflows")
            c.check(side.is_disabled("#ov-prev") and side.is_enabled("#ov-next"), "at the start: ‹ off, › on")
            side.click("#ov-next")
            moved = wait_for(lambda: side.evaluate("document.getElementById('ov-scroller').scrollLeft") > 0)
            c.check(moved, "› scrolls the row")
            c.check(wait_for(lambda: side.is_enabled("#ov-prev")), "after scrolling: ‹ on")
            side.click("#ov-prev")
            c.check(wait_for(lambda: side.evaluate("document.getElementById('ov-scroller').scrollLeft") <= 2),
                    "‹ scrolls back")
            c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"),
                    "narrow: no sideways page scroll")

            # copy letter: your edits win over the generated text
            first = cards.first
            first.locator("button", has_text="Letter").click()
            c.check(wait_for(lambda: side.evaluate("window.__copied")) == EDITED_LETTER, "⧉ copies your edited letter")
            cards.nth(1).locator("button", has_text="Letter").click()
            c.check(wait_for(lambda: side.evaluate("window.__copied") == "Letter for Ready 92"),
                    "⧉ copies the generated letter when there are no edits")

            # open ad
            url = opened_url(lambda: first.locator("button", has_text="Open ad").click())
            c.check(url.startswith("https://au.seek.com/job/9"), f"Open ad opens the Seek ad ({url})")

            # mark applied: two clicks
            applied_btn = first.locator(".mc-applied")
            applied_btn.click()
            c.check(applied_btn.inner_text() == "Click again to confirm", "first click asks to confirm")
            c.check(side.inner_text("#ov-progress .big") == "3", "one click does not count an application")
            applied_btn.click()
            c.check(wait_for(lambda: side.locator("#ov-scroller .mini-card").count() == 6), "the card leaves the row")
            c.check(side.inner_text("#ov-progress .big") == "4", "the counter bumps to 4")
            c.check(side.inner_text("#ov-ready-count") == "6 letters ready to send", "the ready count drops to 6")
            row = con.execute("SELECT m.status, m.applied_at FROM matches m JOIN job_listings j ON j.id = m.job_id "
                              "WHERE j.title = 'Ready 96'").fetchone()
            c.check(row[0] == "applied" and row[1], "the job is applied in the DB")

            # suggested searches
            searches = side.locator("#ov-searches .ov-search")
            n = searches.count()
            c.check(1 <= n <= 3, f"1-3 suggested searches ({n})")
            first_q = searches.first.locator(".q").inner_text() if n else ""
            c.check(n and "Brisbane" in searches.first.inner_text(), "suggestions carry the search location")
            c.check(side.is_visible("#ov-search-hint"), "hint: open one, then press Scan Page")
            if n:
                url = opened_url(lambda: searches.first.click())
                slug = first_q.lower().replace(" ", "-") + "-jobs"
                c.check(url == f"https://au.seek.com/{slug}?where=Brisbane", f"a suggestion opens its Seek search ({url})")

            # the ✎ editor
            side.click("#ov-progress .ov-edit-btn")
            c.check(side.input_value("#ov-progress input[type=date]") == ANCHOR.isoformat(), "✎ shows the saved date")
            side.fill("#ov-progress input[type=number]", "0")
            side.click("#ov-progress button[type=submit]")
            time.sleep(0.3)
            c.check(not side.evaluate("document.querySelector('#ov-progress input[type=number]').validity.valid")
                    and json.load(urllib.request.urlopen(f"{BACKEND}/profile/1/preferences"))["obligation_target"] == 20,
                    "a target of 0 is refused (nothing saved)")
            side.fill("#ov-progress input[type=number]", "4")
            side.click("#ov-progress button[type=submit]")
            side.locator("#ov-progress .ov-count").wait_for(timeout=10000)
            c.check(wait_for(lambda: "/ 4" in side.inner_text("#ov-progress")), "target changed to 4")
            c.check(wait_for(lambda: "met" in (side.get_attribute("#ov-progress", "class") or ""))
                    and "target met" in side.inner_text("#ov-progress"), "4/4: the card turns green, target met")
            side.click("#ov-progress .ov-edit-btn")
            side.fill("#ov-progress input[type=number]", "20")
            side.click("#ov-progress button[type=submit]")
            c.check(wait_for(lambda: "/ 20" in side.inner_text("#ov-progress")), "target back to 20")

            # wide
            side.set_viewport_size({"width": 760, "height": 900})
            time.sleep(0.4)
            c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"),
                    "wide: no sideways page scroll")
            side.screenshot(path=str(work / "overview-wide.png"))
            side.set_viewport_size({"width": 380, "height": 900})
            time.sleep(0.4)
            side.screenshot(path=str(work / "overview-narrow.png"), full_page=True)

            run_applied_checks(c, side, con, work)
            run_delete_checks(c, side, con)

            c.check(not errors, f"no page errors {errors[:3]}")
            usage_after = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            c.check(usage_after == usage_before, "no llm_usage row written")
            c.check(not stub_log.exists() or not stub_log.read_text(encoding="utf-8").strip(), "no model call")
            c.check(not blocked, f"nothing left the machine (no Seek request either) {blocked[:3]}")
            ctx.close()
    finally:
        con.close()
        stop_server(server)

    failed = c.failed()
    print(f"\n{len(c.results) - failed}/{len(c.results)} checks passed. Screenshots and logs: {work}")
    if not failed and not headed and not keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


def run_applied_checks(c: Checks, side, con, work: Path) -> None:
    """The Applied tab, after the Overview checks: target 20, start ANCHOR, and Ready 96
    marked applied today (so the current period holds 4)."""
    print("Applied")
    prev_start, two_back = add_months(ANCHOR, -1), add_months(ANCHOR, -2)
    side.click("nav#tabs .tab[data-tab=applied]")
    side.locator("details.period").first.wait_for(timeout=10000)
    c.check(side.is_hidden("#hdr-jobs-btns"), "Scan Page / Refresh hide on Applied")
    periods = side.locator("details.period")
    starts = [periods.nth(i).get_attribute("data-start") for i in range(periods.count())]
    c.check(starts == [ANCHOR.isoformat(), prev_start.isoformat(), two_back.isoformat()],
            f"three periods, newest first ({starts})")
    opened = [side.evaluate(f"document.querySelectorAll('details.period')[{i}].open") for i in range(3)]
    c.check(opened == [True, False, False], f"the current period is open, the rest closed ({opened})")
    c.check("27 applications in 3 periods" in side.inner_text("#applied-status"), "status: 27 applications in 3 periods")

    cur_sum = periods.nth(0).locator("summary").inner_text()
    end = add_months(ANCHOR, 1) - dt.timedelta(days=1)
    c.check(f"{fmt(ANCHOR)} – {fmt(end)}" in cur_sum and "current" in cur_sum.lower(), "current period range and tag")
    c.check("4/20" in cur_sum and "≈ US$0.25" in cur_sum, f"current: 4/20, ≈ US$0.25 ({cur_sum!r})")
    c.check("2/20" in periods.nth(1).locator("summary").inner_text()
            and "≈ US$0.20" in periods.nth(1).locator("summary").inner_text(), "previous: 2/20, ≈ US$0.20")
    old = periods.nth(2).locator(".p-count")
    c.check(old.inner_text() == "21/20 ✓" and "met" in old.get_attribute("class"), "two back: 21/20 ✓ in green")

    rows = periods.nth(0).locator(".p-jobs li")
    titles = {rows.nth(i).locator(".pj-title").inner_text() for i in range(rows.count())}
    c.check(titles == {"Current Analyst A", "Current Analyst B", "Current Analyst C", "Ready 96"},
            f"current period lists its 4 jobs, the hidden one included ({sorted(titles)})")
    c.check("2 not costed" in periods.nth(0).locator(".p-foot").inner_text(), "2 not costed in the current period")
    row_a = periods.nth(0).locator(".p-jobs li", has_text="Current Analyst A")
    meta = row_a.locator(".pj-meta").inner_text()
    c.check(meta == f"Company 1 · Applied {fmt(ANCHOR)} · ≈ US$0.20", f"row: company, date applied, cost ({meta!r})")
    c.check(row_a.locator(".score").inner_text() == "91" and "t-blue" in row_a.locator(".score").get_attribute("class"),
            "row: match score in its tier colour")

    # + Interview: set, then undo (with a confirm)
    iv = row_a.locator(".iv-btn")
    c.check(iv.inner_text() == "+ Interview", "+ Interview button")
    iv.click()
    c.check(wait_for(lambda: iv.inner_text() == "✓ Interview"), "click: ✓ Interview")
    row = con.execute("SELECT status, interview_at FROM matches WHERE id = 1").fetchone()
    c.check(row[0] == "applied" and row[1], "interview stored, status still applied")
    c.check(not side.evaluate("window.__opened.some(u => u.endsWith('/job/90001'))"), "the button doesn't open the ad")
    side.once("dialog", lambda d: d.accept())
    iv.click()
    c.check(wait_for(lambda: iv.inner_text() == "+ Interview"), "click again (confirmed): undone")
    c.check(con.execute("SELECT interview_at FROM matches WHERE id = 1").fetchone()[0] is None, "interview cleared in the DB")

    # a row opens its ad
    before = side.evaluate("window.__opened.length")
    row_a.locator(".pj-title").click()
    wait_for(lambda: side.evaluate("window.__opened.length") > before)
    c.check(side.evaluate("window.__opened.at(-1)") == "https://au.seek.com/job/90001", "a row opens its ad")

    # open sections survive a reload
    periods.nth(1).locator("summary").click()
    side.click("nav#tabs .tab[data-tab=overview]")
    side.click("nav#tabs .tab[data-tab=applied]")
    time.sleep(0.8)
    opened = [side.evaluate(f"document.querySelectorAll('details.period')[{i}].open") for i in range(3)]
    c.check(opened == [True, True, False], f"open sections are kept across a reload ({opened})")

    # exports
    with side.expect_download() as dl:
        periods.nth(0).locator("button", has_text="Export this period").click()
    d = dl.value
    c.check(d.suggested_filename == f"applied-jobs-{ANCHOR.isoformat()}-to-{end.isoformat()}.csv",
            f"per-period CSV name ({d.suggested_filename})")
    rows_csv = list(csv.reader(io.StringIO(Path(d.path()).read_text(encoding="utf-8"))))
    c.check(len(rows_csv) == 5 and rows_csv[0][0] == "Date Applied", f"per-period CSV: header + 4 rows ({len(rows_csv)})")
    with side.expect_download() as dl:
        side.click("#export-applied-btn")
    all_rows = list(csv.reader(io.StringIO(Path(dl.value.path()).read_text(encoding="utf-8"))))
    dates = [r[0] for r in all_rows[1:]]
    c.check(len(all_rows) == 28, f"Export CSV: header + all 27 applications ({len(all_rows)})")
    c.check(dates == sorted(dates, reverse=True), "Export CSV: newest first")

    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "narrow: no sideways page scroll")
    side.screenshot(path=str(work / "applied-narrow.png"), full_page=True)
    side.set_viewport_size({"width": 760, "height": 900})
    time.sleep(0.4)
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "wide: no sideways page scroll")
    side.screenshot(path=str(work / "applied-wide.png"))
    side.set_viewport_size({"width": 380, "height": 900})

    # no start date: calendar months, with a pointer to the Overview
    prefs = json.loads(con.execute("SELECT preferences FROM profiles WHERE id = 1").fetchone()[0])
    con.execute("UPDATE profiles SET preferences = ? WHERE id = 1",
                (json.dumps({**prefs, "obligation_cycle_start": None}),))
    con.commit()
    side.click("nav#tabs .tab[data-tab=overview]")
    side.click("nav#tabs .tab[data-tab=applied]")
    note = side.locator(".ap-note")
    note.wait_for(timeout=10000)
    first = side.locator("details.period").first
    c.check("calendar month" in note.inner_text() and first.get_attribute("data-start") == TODAY.replace(day=1).isoformat(),
            "no start date: grouped by calendar month, with a note")
    c.check(side.evaluate("document.querySelector('details.period').open"), "calendar view: the current month is open")
    note.locator("button").click()
    c.check(side.locator("nav#tabs .tab.active").inner_text() == "Overview"
            and wait_for(lambda: side.locator("#ov-progress .ov-setup").count() == 1),
            "the note's link opens the Overview's start-date prompt")


def run_delete_checks(c: Checks, side, con) -> None:
    """An applied job is evidence: no Delete on its Jobs card, the backend refuses it
    without allow_applied, and the Applied tab's own Delete removes it after a confirm.
    Runs after run_applied_checks (no start date: calendar-month periods)."""
    print("Delete")
    side.click("nav#tabs .tab[data-tab=jobs]")
    card_a = side.locator("#job-list li", has_text="Current Analyst A")
    card_a.wait_for(timeout=10000)
    c.check(card_a.locator(".del-btn").count() == 0, "Jobs tab: an applied job's card has no Delete")
    card_new = side.locator("#job-list li", has_text="No Letter Yet")
    c.check(card_new.locator(".del-btn").count() == 1, "Jobs tab: an unapplied job's card keeps Delete")
    card_new.locator(".apply-btn").click()
    c.check(wait_for(lambda: card_new.locator(".del-btn").count() == 0), "Mark Applied on a card removes its Delete")

    req = urllib.request.Request(f"{BACKEND}/jobs/1?profile_id=1", method="DELETE")
    try:
        urllib.request.urlopen(req)
        refused = False
    except urllib.error.HTTPError as e:
        refused = e.code == 409
    c.check(refused, "backend: DELETE of an applied job without allow_applied is 409")
    c.check(con.execute("SELECT COUNT(*) FROM matches WHERE id = 1").fetchone()[0] == 1, "...and the match is still there")

    side.click("nav#tabs .tab[data-tab=applied]")
    period = side.locator("details.period").first
    row = period.locator(".p-jobs li", has_text="Current Analyst C")
    # Switching tabs re-renders the list; wait for the job just marked applied so the
    # count below is read from the fresh render, not the stale one.
    period.locator(".p-jobs li", has_text="No Letter Yet").wait_for(timeout=10000)
    count_before = period.locator(".p-count").inner_text()
    jid = int(row.get_attribute("data-job-id"))
    del_btn = row.locator(".del-btn")
    c.check(del_btn.count() == 1, "Applied tab: each row has a Delete")

    opened_before = side.evaluate("window.__opened.length")
    messages: list[str] = []
    side.once("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    del_btn.click()
    time.sleep(0.5)
    c.check(messages and "Permanently delete this application" in messages[0]
            and "evidence export" in messages[0] and "can't be undone" in messages[0],
            f"Delete asks to confirm, naming the period and the export ({messages[:1]})")
    c.check(con.execute("SELECT COUNT(*) FROM matches WHERE job_id = ?", (jid,)).fetchone()[0] == 1,
            "cancelled: nothing deleted")
    c.check(side.evaluate("window.__opened.length") == opened_before, "the Delete button doesn't open the ad")

    side.once("dialog", lambda d: d.accept())
    del_btn.click()
    c.check(wait_for(lambda: period.locator(".p-jobs li", has_text="Current Analyst C").count() == 0),
            "confirmed: the row leaves the Applied tab")
    gone = con.execute("SELECT (SELECT COUNT(*) FROM job_listings WHERE id = ?) + "
                       "(SELECT COUNT(*) FROM matches WHERE job_id = ?)", (jid, jid)).fetchone()[0]
    c.check(gone == 0, "confirmed: job and match deleted in the DB")
    n_before = int(count_before.split("/")[0])
    c.check(wait_for(lambda: period.locator(".p-count").inner_text().startswith(f"{n_before - 1}/")),
            f"the period count drops by one (was {count_before})")
    side.click("nav#tabs .tab[data-tab=jobs]")
    c.check(wait_for(lambda: side.locator("#job-list li", has_text="Current Analyst A").count() == 1)
            and side.locator("#job-list li", has_text="Current Analyst C").count() == 0,
            "the Jobs tab no longer lists it")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # check names hold ⧉ ‹ › –
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the screenshots and logs")
    args = parser.parse_args()
    sys.exit(run(args.headed, args.keep))
