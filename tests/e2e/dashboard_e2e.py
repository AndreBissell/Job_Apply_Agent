"""End-to-end check of the Centrelink dashboard: the Overview and Applied tabs of the side
panel against a scratch backend (docs/centrelink-dashboard-plan.md).

    python tests/e2e/dashboard_e2e.py            # headless Chromium
    python tests/e2e/dashboard_e2e.py --headed
    python tests/e2e/dashboard_e2e.py --keep     # keep the screenshots

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB in a temp folder (alembic head) holding profile 1, applied
   jobs across three monthly periods (one hidden, one with no logged AI spend), seven jobs
   with a letter ready (one with your edits), one job whose letter run waits on a question,
   one high scorer with no letter, eight more letter-less jobs across seven score bands
   (one not seen live for 3 days), and some ``llm_usage`` rows (match costs on applied jobs,
   three job-less ``generate_letter`` costs for the letter estimate). Dates are relative to
   today, so the run works on any day. The letter bars are set to 100 and no job has a
   description, so the idle loop has no LLM work; the server runs with LLM_PROVIDER=stub
   anyway, so a stray call would fail instead of costing money.
2. Starts ``scripts/run_api.py test --db <scratch>`` on port 8001 and opens the unpacked
   extension's side panel (TEST backend). Every request off this machine is aborted, and
   ``chrome.tabs.create`` is stubbed to record the URL: the panel never opens a tab, so
   nothing reaches Seek (a real tab can start loading before routing attaches to it).
3. Checks the Overview (opens first, start-date prompt, progress, waiting letters, the
   Keep Applying row and its arrows, Copy / Open / Mark applied, the
   ✎ editor), the "No letter yet" bands (the 6 highest of 7 bands in ONE row that scrolls
   sideways inside its box while the page does not, the
   top card's "+ letters" two-click confirm ("Write 1? ≈ 12¢": the estimate comes from three
   seeded `generate_letter` costs, its basis named in the button's title) and its POST {lo, hi},
   then the queued band leaving the bands and its job showing in Keep Applying as a "✍ Writing…"
   card (Open ad and Mark applied, no Copy; "1 being written" in the ready count), the
   availability-check note, the section hidden when there are no bands; the write endpoint is
   intercepted and the band / ready lists are served from here, so nothing is queued), "Scan more" (up to 3 suggested-search
   rows, each with its own button; the one line "Each opens a Seek search in <where>...";
   a click opens THAT search in a foreground tab and scans that tab by id, even though the
   stubbed active tab is another page: one injection, "<phrase>: All 2 jobs ... already in
   your list", staying on the Overview; a closed tab and a tab that left the search each
   stop with their notice and inject nothing; a double click opens one tab and every button
   stays off, even across a live reload; every chrome.tabs / chrome.scripting call is
   stubbed before the click, so nothing is opened)
   and the Applied tab (periods newest first with the current one open,
   counts against the target, cost and "not costed", + Interview set and undo, the
   per-period and full CSV exports), at a narrow and a wide panel width. A row opens its
   read-only record in place (it never opens the ad): the ad's status and summary, both
   responsibilities, skills, the full text, the match, the letter as last saved (edited wins);
   "No cover letter was saved" and the dead ad ("Ad closed", "The ad text wasn't kept"); a
   click inside it doesn't collapse it, it survives a reload, and "Open ad on Seek" is the
   one way to the ad (recorded, never opened). Then deleting:
   no Delete on an applied job's Jobs card (a row with applied_at set but status 'new'
   shows ✓ Applied there too), the backend's 409, and the Applied tab's Delete
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
import re
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

# What the Applied tab's record shows (Current Analyst A has all of it, Previous Role 0 is a
# dead ad, Current Analyst C has nothing saved).
A_GENERATED = "GENERATED letter text for Analyst A. Sincerely, E2E Test"
A_EDITED = "EDITED letter text for Analyst A. Sincerely, E2E Test"
A_SUMMARY = "Build dashboards and reports for the finance team."
A_RESPONSIBILITIES = ["Maintain the weekly sales dashboard", "Write SQL for ad hoc requests"]
A_AD_TEXT = "About the role\nWe need a data analyst.\nYou will write SQL and build dashboards.\nApply now."
DEAD_SUMMARY = "An old role that Seek has taken down."
DEAD_LETTER = "Dear Hiring Manager, the letter for the closed ad. Sincerely, E2E Test"
DEAD_DAY = TODAY - dt.timedelta(days=5)  # the day the dead ad was noticed closed


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
    from app.models import CoverLetter, JobListing, JobSkill, LetterRun, LlmUsage, Match, Profile, Skill

    engine = create_engine(url)
    with Session(engine) as s:
        s.add(Profile(id=1, name="E2E Test", email="e2e@example.invalid", password_hash="x",
                      target_role="Data Analyst", target_location="Brisbane",
                      preferences=json.dumps({"auto_cover_letter_min_score": 100, "letter_loop_min_score": 100})))
        s.add_all([Skill(user_id=1, name="SQL"), Skill(user_id=1, name="Power BI")])
        s.flush()
        next_id = iter(range(1, 1000))

        def job(title, score, *, applied=None, hidden=False, letter=None, edited=None, company=None,
                record=None, skills=()):
            # record: extra JobListing columns (the Applied tab's record shows them);
            # skills: hard skill names for job_skills.
            jid = next(next_id)
            s.add(JobListing(id=jid, source="seek", source_job_id=f"9{jid:04d}",
                             url=f"https://au.seek.com/job/9{jid:04d}", title=title,
                             company=company or f"Company {jid}", location="Brisbane QLD",
                             extracted_at=dt.datetime.now(dt.timezone.utc), **(record or {})))
            s.flush()
            for name in skills:
                s.add(JobSkill(job_id=jid, name=name, skill_type="hard"))
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
        cur = [job("Current Analyst A", 91, applied=ANCHOR, letter=A_GENERATED, edited=A_EDITED,
                   skills=("SQL", "Tableau"), record={
                       "raw_description": A_AD_TEXT, "summary": A_SUMMARY,
                       "key_responsibilities": json.dumps(A_RESPONSIBILITIES),
                       "date_scraped": local_noon_utc(ANCHOR - dt.timedelta(days=1)),
                       "last_seen_live_at": local_noon_utc(ANCHOR)}),
               job("Current Analyst B", 84, applied=TODAY - dt.timedelta(days=1), hidden=True),
               job("Current Analyst C", 77, applied=TODAY)]
        cost(cur[0], 0.12)
        cost(cur[0], 0.08)
        cost(cur[1], 0.05)
        # Previous period: 2 applications.
        prev_start = add_months(ANCHOR, -1)
        for i in range(2):
            # Previous Role 0 is a dead ad: no text kept, a summary, a letter as generated.
            dead = ({"raw_description": None, "summary": DEAD_SUMMARY,
                     "expired_detected_at": local_noon_utc(DEAD_DAY)} if i == 0 else None)
            cost(job(f"Previous Role {i}", 80, applied=prev_start + dt.timedelta(days=i + 1),
                     letter=DEAD_LETTER if i == 0 else None, record=dead), 0.10)
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
        # More letter-less, unapplied, extracted jobs so the "no letter yet" bands are
        # interesting (bar 100): 99-95 (1), 94-90 (2), 74-70 (1), 59-55 (2), 44-40 (1), 34-30 (1),
        # 14-10 (1) = 7 bands, so only the 6 highest show; 9 jobs in all. "Band 56" was last seen
        # live 3 days ago, so the freshness gate holds it back (score < 60: never re-checked for
        # its own sake, so the panel's availability check leaves it alone).
        for title, score in [("Band 93", 93), ("Band 91", 91), ("Band 72", 72), ("Band 58", 58),
                             ("Band 41", 41), ("Band 33", 33), ("Band 12", 12)]:
            job(title, score)
        job("Band 56", 56, record={"last_seen_live_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)})
        # What letters cost: three pipeline draft calls (no job_id, so no applied job's cost
        # changes) and no one-pass `cover_letter` call, so the "Write N?" estimate is the mean of
        # these, 12 cents each, from the "draft" basis (letter_bands.letter_cost_estimate).
        for _ in range(3):
            s.add(LlmUsage(task="generate_letter", tier="strong", model="stub", cost_usd=Decimal("0.12")))
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
            c.check(side.locator("nav#tabs .tab.active").inner_text() == "Pipeline", "the waiting row opens the Pipeline tab")
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

            # the bin: two clicks. The DELETE is intercepted, so the scratch DB keeps the job and
            # the reload afterwards brings the card back (later checks count 6 ready letters).
            run_mini_delete_checks(c, side)

            # the "no letter yet" score bands
            run_band_checks(c, side)

            # Scan more: up to 3 search rows, the Seek calls stubbed
            run_scan_more_checks(c, side)

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


def run_mini_delete_checks(c: Checks, side) -> None:
    """The bin on a Keep Applying card: the first click arms it ("Delete?"), left alone it
    disarms, the second click sends DELETE /jobs/{id} and the card leaves the row."""
    print("Keep Applying bin")
    cards = side.locator("#ov-scroller .mini-card")
    c.check(cards.count() == 6 and cards.locator(".mc-del").count() == 6, "bin: one on every ready card")
    first = cards.first
    title = first.locator(".mc-title").inner_text()
    job_id = first.get_attribute("data-job-id")
    deletes: list[str] = []

    def on_delete(r):
        if r.request.method != "DELETE":
            return r.fallback()
        deletes.append(r.request.url)
        return r.fulfill(status=200, content_type="application/json", body=json.dumps({"deleted": True}))

    pattern = re.compile(rf"/jobs/{job_id}\?")
    side.route(pattern, on_delete)
    try:
        bin_btn = first.locator(".mc-del")
        bin_btn.click()
        c.check(bin_btn.inner_text() == "Delete?" and "confirm" in (bin_btn.get_attribute("class") or "").split(),
                f"bin: the first click asks 'Delete?' ({bin_btn.inner_text()!r})")
        c.check(not deletes, "bin: one click deletes nothing")
        c.check(wait_for(lambda: "confirm" not in (bin_btn.get_attribute("class") or "").split(), timeout_s=8),
                "bin: left alone, it disarms")
        c.check(not deletes, "bin: ...and still nothing is deleted")
        bin_btn.click()
        bin_btn.click()
        c.check(wait_for(lambda: len(deletes) == 1), f"bin: the second click sends one DELETE ({deletes})")
        c.check(bool(deletes) and f"/jobs/{job_id}?profile_id=1" in deletes[0], f"bin: ...for that job ({deletes[:1]})")
        c.check(wait_for(lambda: side.locator("#ov-scroller .mini-card", has_text=title).count() == 0),
                f"bin: the card '{title}' leaves the row")
        c.check(side.inner_text("#ov-ready-count") == "5 letters ready to send", "bin: the ready count drops to 5")
    finally:
        side.unroute(pattern, on_delete)
    # The job is still in the scratch DB (the DELETE never reached it): a reload brings it back.
    side.click("nav#tabs .tab[data-tab=jobs]")
    side.click("nav#tabs .tab[data-tab=overview]")
    c.check(wait_for(lambda: side.locator("#ov-scroller .mini-card").count() == 6),
            "bin: after a reload the (intercepted) job is back, 6 cards")


def run_band_checks(c: Checks, side) -> None:
    """The "No letter yet" section (bar 100): seven bands, the six highest shown, two columns,
    "+ letters" on the top card only. The write endpoint is intercepted (nothing is queued on
    the real server); the band and ready lists are served from here for the "queued" (band leaves,
    Keep Applying shows a "Writing…" card) and empty cases, and un-routed before later checks."""
    print("No letter yet")
    c.check(side.is_visible("#ov-bands"), "bands: the section shows")
    c.check(side.inner_text("#ov-bands h2").strip().lower() == "no letter yet", "bands: heading 'No letter yet'")
    c.check(wait_for(lambda: side.inner_text("#ov-bands-sub") == "9 scored below 100"),
            f"bands: sub '9 scored below 100' (got {side.inner_text('#ov-bands-sub')!r})")

    cards = side.locator("#ov-band-grid .band-card")
    ranges = [cards.nth(i).locator(".bc-range").inner_text() for i in range(cards.count())]
    counts = [cards.nth(i).locator(".bc-count").inner_text() for i in range(cards.count())]
    c.check(cards.count() == 6, f"bands: 6 cards (the 7th, 14–10, is dropped) ({cards.count()})")
    c.check(ranges == ["99–95", "94–90", "74–70", "59–55", "44–40", "34–30"],
            f"bands: highest first, hi–lo labels ({ranges})")
    c.check(counts == ["1", "2", "1", "2", "1", "1"], f"bands: counts per band ({counts})")
    # the tier of each band's lo score (sidebar_shared.js: gold 95, blue 90, green 75, amber 60)
    tiers = [cards.nth(i).get_attribute("data-tier") for i in range(cards.count())]
    c.check(tiers == ["gold", "blue", "amber", "hidden", "hidden", "hidden"],
            f"bands: tier follows tierOf(lo) ({tiers})")
    border = side.evaluate("(() => { const s = getComputedStyle(document.querySelector('.band-card')); "
                           "return [s.borderLeftWidth, s.borderTopWidth]; })()")
    c.check(border == ["3px", "1px"], f"bands: a 3px left border, 1px elsewhere ({border})")
    h = side.evaluate("document.querySelector('.band-card').getBoundingClientRect().height")
    c.check(30 <= h <= 40, f"bands: cards are about one line tall ({h:.0f}px)")

    # one row that scrolls sideways: every card shares a top, the grid is wider than the panel,
    # the page is not, and the grid is about one card tall
    tops = side.evaluate("[...document.querySelectorAll('#ov-band-grid .band-card')]"
                         ".map(el => Math.round(el.getBoundingClientRect().top * 10) / 10)")
    c.check(len(tops) == 6 and max(tops) - min(tops) < 1, f"bands: all 6 cards share one top, one row ({tops})")
    geo = side.evaluate("(() => { const g = document.getElementById('ov-band-grid'); "
                        "return { sw: g.scrollWidth, cw: g.clientWidth, h: g.getBoundingClientRect().height }; })()")
    c.check(geo["sw"] > geo["cw"], f"bands: the row scrolls sideways at the narrow width ({geo['sw']} > {geo['cw']})")
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"),
            "bands: ...but the page itself has no sideways scroll")
    c.check(geo["h"] < 50, f"bands: the grid is about one card tall ({geo['h']:.0f}px)")
    side.evaluate("document.getElementById('ov-band-grid').scrollLeft = 200")
    c.check(wait_for(lambda: side.evaluate("document.getElementById('ov-band-grid').scrollLeft") > 0),
            "bands: setting scrollLeft moves the row")
    side.evaluate("document.getElementById('ov-band-grid').scrollLeft = 0")

    # the button is on the top card only
    c.check(side.locator("#ov-band-grid .bc-btn").count() == 1 and cards.first.locator(".bc-btn").count() == 1,
            "bands: one button, on the top card")
    btn = cards.first.locator(".bc-btn")
    c.check(btn.inner_text() == "+ letters", "bands: the button reads '+ letters'")
    note = side.inner_text("#ov-band-note")
    c.check("1 waiting for an availability check" in note and side.is_visible("#ov-band-note"),
            f"bands: the stale ad is held back, with a note ({note!r})")

    # The write is intercepted: the real server never queues anything.
    posts: list[tuple[str, str | None]] = []
    state = {"queued": 0}
    real = json.load(urllib.request.urlopen(f"{BACKEND}/jobs/letter-bands?profile_id=1", timeout=10))
    c.check(real.get("est_usd_per_letter") == 0.12 and real.get("est_basis") == "draft" and real.get("writing") == 0
            and all("queued" not in b for b in real["bands"]),
            f"bands: the API estimates 12¢ from the draft calls, nothing writing, no 'queued' on a band "
            f"({real.get('est_usd_per_letter')}, {real.get('est_basis')}, {real.get('writing')})")

    def on_write(r):
        posts.append((r.request.method, r.request.post_data))
        return r.fulfill(status=200, content_type="application/json",
                         body=json.dumps({"queued": 1, "waiting_check": 0, "skipped": 0}))

    def on_get(r):
        # Once the write is "queued", the top band leaves the bands and counts as writing.
        if not state["queued"]:
            return r.fallback()
        data = json.load(urllib.request.urlopen(r.request.url, timeout=10))
        data["bands"] = data["bands"][1:]
        data["total"] -= 1
        data["writing"] = 1
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    def on_ready(r):
        # ...and its job joins the Keep Applying list with letter_queued, like the real /jobs does.
        if not state["queued"]:
            return r.fallback()
        data = json.load(urllib.request.urlopen(r.request.url, timeout=10))
        every = json.load(urllib.request.urlopen(f"{BACKEND}/jobs?profile_id=1&limit=500", timeout=10))
        job = next(j for j in every if j["title"] == "No Letter Yet")
        data.append({**job, "letter_queued": True})
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(data))

    ready_url = re.compile(r"/jobs\?[^#]*\bready=true\b")
    side.route("**/jobs/letter-bands?**", on_get)
    side.route("**/jobs/letter-bands/write**", on_write)
    side.route(ready_url, on_ready)
    try:
        btn.click()
        c.check(btn.inner_text() == "Write 1? ≈ 12¢" and "confirm" in (btn.get_attribute("class") or "").split(),
                f"bands: the first click asks 'Write 1? ≈ 12¢' ({btn.inner_text()!r})")
        c.check("draft" in (btn.get_attribute("title") or ""),
                f"bands: the button's title names the estimate's basis ({btn.get_attribute('title')!r})")
        c.check(not posts, "bands: nothing is posted by the first click")
        fits = side.evaluate("(() => { const c = document.querySelector('.band-card').getBoundingClientRect(); "
                             "const b = document.querySelector('.bc-btn').getBoundingClientRect(); "
                             "return b.right <= c.right + 0.5 && b.left >= c.left; })()")
        c.check(fits and side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"),
                "bands: the confirm label fits its card, no sideways scroll")
        c.check(wait_for(lambda: side.locator("#ov-band-grid .bc-btn").first.inner_text() == "+ letters", timeout_s=8),
                "bands: left alone, the button disarms back to '+ letters'")
        c.check(not posts, "bands: ...and still nothing is posted")

        side.locator("#ov-band-grid .bc-btn").first.click()
        state["queued"] = 1  # from here the reload sees the top band as queued
        side.locator("#ov-band-grid .bc-btn.confirm").click()
        c.check(wait_for(lambda: len(posts) == 1), f"bands: the second click posts once ({len(posts)})")
        c.check(bool(posts) and posts[0][0] == "POST" and json.loads(posts[0][1] or "{}") == {"lo": 95, "hi": 99},
                f"bands: ...as POST {{lo: 95, hi: 99}} ({posts[:1]})")

        # the queued band leaves the bands; the next one is on top and offers "+ letters"
        def first_range():
            return cards.first.locator(".bc-range").inner_text() if cards.count() else None
        c.check(wait_for(lambda: first_range() == "94–90"),
                f"bands: the 99–95 card is gone, 94–90 is first ({first_range()!r})")
        c.check(cards.first.locator(".bc-btn").count() == 1 and cards.first.locator(".bc-btn").inner_text() == "+ letters",
                "bands: ...and it has the '+ letters' button (no 'Writing…' state)")
        c.check(side.locator("#ov-band-grid .bc-btn:disabled").count() == 0, "bands: no disabled button")

        # Keep Applying shows the job being written
        writing = side.locator("#ov-scroller .mini-card.writing")
        c.check(wait_for(lambda: writing.count() == 1), f"writing: one 'writing' card in Keep Applying ({writing.count()})")
        w = side.locator("#ov-scroller .mini-card", has_text="No Letter Yet")
        c.check(w.count() == 1 and "writing" in (w.get_attribute("class") or "").split(),
                "writing: it is the 'No Letter Yet' job")
        c.check(w.locator(".mc-writing").count() == 1 and w.locator(".mc-writing").inner_text() == "✍ Writing…",
                f"writing: it says '✍ Writing…' ({w.locator('.mc-writing').all_inner_texts()})")
        c.check(w.locator("button", has_text="Letter").count() == 0, "writing: no '⧉ Letter' copy button")
        c.check(w.locator("button", has_text="Open ad").count() == 1, "writing: 'Open ad' is there")
        c.check(w.locator(".mc-applied").count() == 1, "writing: 'Mark applied' is there")
        count_text = side.inner_text("#ov-ready-count")
        c.check("6 letters ready to send" in count_text and "1 being written" in count_text,
                f"writing: the ready count is '6 letters ready to send · 1 being written' ({count_text!r})")
        time.sleep(0.5)
        c.check(len(posts) == 1, "bands: still exactly one POST")
    finally:
        state["queued"] = 0
        side.unroute(ready_url, on_ready)

    # The button sits on the highest band it can write: a band left with only jobs waiting for
    # an availability check keeps its card but passes the button down to the next band.
    held_back = {"bar": 100, "width": 5, "total": 4, "writing": 0, "est_usd_per_letter": 0.12, "est_basis": "draft",
                 "bands": [{"lo": 95, "hi": 99, "count": 1, "ready": 0, "waiting_check": 1},
                           {"lo": 90, "hi": 94, "count": 2, "ready": 2, "waiting_check": 0},
                           {"lo": 70, "hi": 74, "count": 1, "ready": 1, "waiting_check": 0}]}

    def on_held_back(r):
        return r.fulfill(status=200, content_type="application/json", body=json.dumps(held_back))

    side.route("**/jobs/letter-bands?**", on_held_back)  # the latest route is tried first
    try:
        side.click("nav#tabs .tab[data-tab=jobs]")
        side.click("nav#tabs .tab[data-tab=overview]")
        c.check(wait_for(lambda: cards.count() == 3 and cards.first.locator(".bc-range").inner_text() == "99–95"),
                "bands: a band with only held-back jobs keeps its card")
        c.check(cards.first.locator(".bc-btn").count() == 0
                and cards.nth(1).locator(".bc-btn").count() == 1
                and side.locator("#ov-band-grid .bc-btn").count() == 1,
                "bands: ...and the button moves to the next band that can be written (94–90)")
    finally:
        side.unroute("**/jobs/letter-bands?**", on_held_back)

    # nothing to show: the section is hidden
    def on_empty(r):
        return r.fulfill(status=200, content_type="application/json",
                         body=json.dumps({"bar": 100, "width": 5, "total": 0, "bands": []}))

    side.unroute("**/jobs/letter-bands?**", on_get)
    side.route("**/jobs/letter-bands?**", on_empty)
    try:
        side.click("nav#tabs .tab[data-tab=jobs]")
        side.click("nav#tabs .tab[data-tab=overview]")
        c.check(wait_for(lambda: side.is_hidden("#ov-bands")), "bands: with no bands the section is hidden")
    finally:
        side.unroute("**/jobs/letter-bands?**", on_empty)
        side.unroute("**/jobs/letter-bands/write**", on_write)
    side.click("nav#tabs .tab[data-tab=jobs]")
    side.click("nav#tabs .tab[data-tab=overview]")
    c.check(wait_for(lambda: side.is_visible("#ov-bands") and side.locator("#ov-band-grid .band-card").count() == 6),
            "bands: the real data is back after un-routing")
    c.check(side.locator("#ov-band-grid .band-card .bc-range").first.inner_text() == "99–95",
            "bands: ...with 99–95 first again")
    c.check(wait_for(lambda: side.locator("#ov-scroller .mini-card.writing").count() == 0
                     and side.inner_text("#ov-ready-count") == "6 letters ready to send"),
            f"writing: the real data has no writing card ({side.inner_text('#ov-ready-count')!r})")


SCAN_STUBS = """() => {
    window.__orig = {
        create: chrome.tabs.create, get: chrome.tabs.get, query: chrome.tabs.query,
        exec: chrome.scripting.executeScript, wait: window.waitForTabComplete,
    };
    window.__created = []; window.__injected = []; window.__createDelay = 0;
    // what chrome.tabs.get(4242) answers: a tab object, or null for "the tab was closed"
    window.__newTab = { id: 4242, url: 'https://au.seek.com/data-analyst-jobs/in-All-Brisbane-QLD' };
    chrome.tabs.create = o => {
        window.__created.push({ url: o.url, active: o.active });
        window.__opened.push(o.url);
        return new Promise(r => setTimeout(() => r({ id: 4242 }), window.__createDelay));
    };
    window.waitForTabComplete = async () => {};
    chrome.tabs.get = id => (id === 4242 && window.__newTab)
        ? Promise.resolve(window.__newTab) : Promise.reject(new Error('No tab'));
    // the tab IN FRONT is a different, non-Seek one: Scan more must not depend on it
    chrome.tabs.query = () => Promise.resolve([{ id: 999, url: 'https://example.com/' }]);
    chrome.scripting.executeScript = ({ target, func }) => {
        window.__injected.push({ tabId: target.tabId, func: func.name });
        return Promise.resolve([{ result: ['https://au.seek.com/job/90001', 'https://au.seek.com/job/90002'] }]);
    };
}"""

SCAN_RESTORE = """() => {
    chrome.tabs.create = window.__orig.create; chrome.tabs.get = window.__orig.get;
    chrome.tabs.query = window.__orig.query;
    chrome.scripting.executeScript = window.__orig.exec; window.waitForTabComplete = window.__orig.wait;
}"""

SCAN_RESET = """() => { window.__created.length = 0; window.__injected.length = 0;
    document.getElementById('ov-scan-status').textContent = ''; }"""


def run_scan_more_checks(c: Checks, side) -> None:
    """The Overview's suggested searches: up to 3 rows, each with its own Scan more button. Every
    chrome.tabs / chrome.scripting call the scan makes is stubbed BEFORE the click (a real tab can
    load before Playwright's routing attaches), so nothing is opened or injected; the two seeded
    links are jobs already in the list, so the scan stops at "All 2 jobs ... already in your list"
    and never opens a job tab. The tab in front is stubbed as a non-Seek page to show the scan
    reads the tab its click opened (by id), not the active one."""
    print("Scan more")
    suggestions = json.load(urllib.request.urlopen(f"{BACKEND}/jobs/suggested-searches?profile_id=1"))["suggestions"]
    phrases = suggestions[:3]
    c.check(len(phrases) >= 2, f"scan more: the seed gives at least 2 suggestions to click ({suggestions})")
    max_pages = side.evaluate("scanMaxPages")
    rows = side.locator("#ov-searches .ov-search")
    c.check(rows.count() == len(phrases) and side.locator("#ov-searches .ov-scan-more").count() == len(phrases)
            and side.locator("#ov-scan-more").count() == 0,
            f"scan more: {len(phrases)} rows, one Scan more button each, no old single button ({rows.count()})")
    c.check(all(rows.nth(i).locator(".ov-scan-more").count() == 1 for i in range(rows.count())),
            "scan more: exactly one button inside each row")
    shown = [rows.nth(i).locator(".q").inner_text() for i in range(rows.count())]
    c.check(shown == phrases, f"scan more: the rows are the API's first suggestions ({shown} vs {phrases})")
    wheres = [rows.nth(i).locator(".where").inner_text() for i in range(rows.count())]
    c.check(wheres == ["Brisbane"] * len(phrases), f"scan more: each row names Brisbane ({wheres})")
    c.check(all(rows.nth(i).locator(".ov-scan-more").inner_text() == "Scan more" for i in range(rows.count())),
            "scan more: every button reads 'Scan more'")
    line = side.inner_text("#ov-scan-line")
    expect_line = f"Each opens a Seek search in Brisbane and scans up to {max_pages} new jobs."
    c.check(line == expect_line, f"scan more: the line reads {expect_line!r} (got {line!r})")
    second = phrases[1]
    expect_url = side.evaluate("p => seekSearchUrl(p, 'Brisbane')", second)
    btns = side.locator("#ov-searches .ov-scan-more")

    def all_enabled():
        return (side.locator("#ov-searches .ov-scan-more:disabled").count() == 0 and side.is_enabled("#scan-btn"))

    c.check(wait_for(all_enabled), "scan more: the row buttons and Scan Page are enabled")

    side.evaluate(SCAN_STUBS)
    try:
        c.check(side.evaluate("chrome.tabs.create.toString().includes('__created') "
                              "&& chrome.tabs.get.toString().includes('__newTab') "
                              "&& chrome.tabs.query.toString().includes('example.com') "
                              "&& chrome.scripting.executeScript.toString().includes('__injected')"),
                "scan more: tabs.create / get / query and scripting.executeScript are stubbed before the click")
        c.check(side.evaluate("window.waitForTabComplete.toString().includes('{}') "
                              "&& waitForTabComplete === window.waitForTabComplete"),
                "scan more: waitForTabComplete override is what the panel's code sees")
        side.evaluate(SCAN_RESET)

        # --- the happy path: click the SECOND row ---------------------------------------
        btns.nth(1).click()
        status = f"{second}: All 2 jobs on this page are already in your list."
        c.check(wait_for(lambda: side.inner_text("#ov-scan-status") == status, timeout_s=15),
                f"scan more: the status reads {status!r} (got {side.inner_text('#ov-scan-status')!r})")
        created = side.evaluate("window.__created")
        c.check(len(created) == 1 and created[0]["url"] == expect_url and created[0]["active"] is True,
                f"scan more: exactly one tab, the second search, in the foreground ({created})")
        c.check(side.evaluate("window.__injected") == [{"tabId": 4242, "func": "pageCollectJobLinks"}],
                f"scan more: one injection into the new tab, not the active tab 999 ({side.evaluate('window.__injected')})")
        c.check(not any(t["active"] is False for t in created), "scan more: no job tab was opened")
        c.check(side.is_visible("#overview-section") and side.is_hidden("#jobs-section")
                and side.locator("nav#tabs .tab.active").inner_text() == "Overview",
                "scan more: stays on the Overview")
        c.check(wait_for(all_enabled), "scan more: every Scan more button and Scan Page are enabled again")
        c.check(not side.evaluate("scanning"), "scan more: the scan is not left running")

        # --- the search tab was closed before it could be scanned -----------------------
        side.evaluate("window.__newTab = null")
        side.evaluate(SCAN_RESET)
        btns.nth(0).click()
        status = f"{phrases[0]}: The search tab was closed before it could be scanned."
        c.check(wait_for(lambda: side.inner_text("#ov-scan-status") == status, timeout_s=15),
                f"scan more: a closed tab says {status!r} (got {side.inner_text('#ov-scan-status')!r})")
        c.check(wait_for(all_enabled), "scan more: buttons enabled again after the closed-tab stop")
        c.check(side.evaluate("window.__injected") == [] and len(side.evaluate("window.__created")) == 1,
                "scan more: a closed tab injects nothing (its own search tab was still opened once)")

        # --- the tab navigated away from the search --------------------------------------
        side.evaluate("window.__newTab = { id: 4242, url: 'https://au.seek.com/job/90001' }")
        side.evaluate(SCAN_RESET)
        btns.nth(0).click()
        status = f"{phrases[0]}: The search tab no longer shows a Seek search, so it was not scanned."
        c.check(wait_for(lambda: side.inner_text("#ov-scan-status") == status, timeout_s=15),
                f"scan more: a tab that left the search says {status!r} (got {side.inner_text('#ov-scan-status')!r})")
        c.check(wait_for(all_enabled), "scan more: buttons enabled again after the navigated-away stop")
        c.check(side.evaluate("window.__injected") == [], "scan more: a navigated-away tab injects nothing")

        # --- a double click opens one tab ----------------------------------------------
        side.evaluate("window.__newTab = { id: 4242, url: 'https://au.seek.com/data-analyst-jobs/in-All-Brisbane-QLD' }")
        side.evaluate("window.__createDelay = 300")
        side.evaluate(SCAN_RESET)
        side.evaluate("(() => { const b = document.querySelector('#ov-searches .ov-scan-more'); b.click(); b.click(); })()")
        c.check(side.locator("#ov-searches .ov-scan-more:not(:disabled)").count() == 0 and side.is_disabled("#scan-btn"),
                "scan more: every row button and Scan Page go off at once")
        # a live reload while the search tab is still opening must not re-enable them
        side.evaluate("ps => renderSuggestions({ suggestions: ps, location: 'Brisbane' })", phrases)
        c.check(side.locator("#ov-searches .ov-scan-more").count() == len(phrases)
                and side.locator("#ov-searches .ov-scan-more:not(:disabled)").count() == 0,
                "scan more: a reload mid-open keeps every button off")
        status = f"{phrases[0]}: All 2 jobs on this page are already in your list."
        c.check(wait_for(lambda: side.inner_text("#ov-scan-status") == status, timeout_s=15),
                "scan more: the double-clicked scan finishes")
        time.sleep(0.5)
        c.check(len(side.evaluate("window.__created")) == 1 and len(side.evaluate("window.__injected")) == 1,
                f"scan more: a double click opens one tab and scans once ({side.evaluate('window.__created')})")
        c.check(wait_for(all_enabled), "scan more: buttons enabled after the double click")
    finally:
        side.evaluate(SCAN_RESTORE)
    c.check(side.evaluate("chrome.tabs.create.toString().includes('__opened') "
                          "&& !chrome.tabs.create.toString().includes('__created')"),
            "scan more: the earlier recording tab stub is back")


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

    # a row opens its record in place (not the ad); the ad is the record's first button
    def record_text(row) -> str:
        return row.locator(".ap-record").inner_text()

    def opened_url(click):
        before = side.evaluate("window.__opened.length")
        click()
        wait_for(lambda: side.evaluate("window.__opened.length") > before)
        return side.evaluate("window.__opened.at(-1) || ''")

    opened_before = side.evaluate("window.__opened.length")
    row_a.locator(".pj-title").click()
    row_a.locator(".ap-record button", has_text="Open ad on Seek").wait_for(timeout=10000)
    rec = row_a.locator(".ap-record")
    c.check(rec.count() == 1 and side.evaluate("window.__opened.length") == opened_before,
            "a row opens its record inside the row, and does not open the ad")
    text = record_text(row_a)
    low = text.lower()
    c.check("the ad" in low and "the match" in low and "cover letter (your edited version)" in low,
            "record: The ad / The match / Cover letter (your edited version) sections")
    c.check(f"Last seen live {ANCHOR.strftime('%d/%m')}" in text and "Ad closed" not in text,
            f"record: ad status 'Last seen live {ANCHOR.strftime('%d/%m')}'")
    c.check(A_SUMMARY in text and all(r in text for r in A_RESPONSIBILITIES),
            "record: the summary and both responsibilities")
    c.check("Skills: SQL, Tableau" in text, "record: the ad's skills")
    c.check("Full ad text (as captured" in text, "record: the full ad text is offered, with its capture date")
    full = row_a.locator("details.ar-full")
    c.check(not full.evaluate("d => d.open") and "You will write SQL" not in text, "record: full ad text starts closed")
    full.locator("summary").click()
    c.check(wait_for(lambda: "You will write SQL" in row_a.locator(".ar-text").inner_text()),
            "record: the full ad text opens")
    full.locator("summary").click()
    c.check(row_a.locator(".ar-letter").inner_text() == A_EDITED and A_GENERATED not in text,
            "record: the letter is the edited version, not the generated one")
    c.check(row_a.locator(".ar-letter").get_attribute("contenteditable") is None
            and row_a.locator(".ap-record textarea").count() == 0, "record: the letter is read-only")
    c.check(row_a.locator(".ap-record h5 button").inner_text().strip().lower().endswith("copy"),
            "record: the letter has a Copy button")
    c.check(row_a.locator(".ar-reason .score").inner_text() == "91" and "never scored" not in text,
            "record: the match section shows the score")

    # the Open ad button is the way to Seek (recorded, never opened)
    url = opened_url(lambda: row_a.locator(".ap-record button", has_text="Open ad on Seek").click())
    c.check(url == "https://au.seek.com/job/90001", f"Open ad on Seek records the ad URL ({url})")
    c.check(row_a.locator(".ap-record").count() == 1, "...and the record stays open")

    # a click inside the record does not collapse it
    row_a.locator(".ar-letter").click()
    time.sleep(0.2)
    c.check(row_a.locator(".ap-record").count() == 1, "clicking inside the record doesn't collapse it")
    c.check("open" in (row_a.get_attribute("class") or "").split(), "the open row is marked")

    # an open record survives a reload (tab switch)
    side.click("nav#tabs .tab[data-tab=overview]")
    side.click("nav#tabs .tab[data-tab=applied]")
    c.check(wait_for(lambda: A_EDITED in record_text(row_a) if row_a.locator(".ap-record").count() else False),
            "an open record is kept across a reload, with its content")
    c.check(row_a.locator(".ap-record").count() == 1, "...exactly once")

    # no letter saved
    row_c = periods.nth(0).locator(".p-jobs li", has_text="Current Analyst C")
    row_c.locator(".pj-title").click()
    c.check(wait_for(lambda: "No cover letter was saved for this application." in (
        record_text(row_c) if row_c.locator(".ap-record").count() else "")),
        "a job with no letter says none was saved")
    row_c.locator(".pj-title").click()
    c.check(row_c.locator(".ap-record").count() == 0, "clicking the title again collapses the record")

    # open sections survive a reload
    periods.nth(1).locator("summary").click()
    side.click("nav#tabs .tab[data-tab=overview]")
    side.click("nav#tabs .tab[data-tab=applied]")
    time.sleep(0.8)
    opened = [side.evaluate(f"document.querySelectorAll('details.period')[{i}].open") for i in range(3)]
    c.check(opened == [True, True, False], f"open sections are kept across a reload ({opened})")

    # a dead ad: no text kept, but the summary and the letter still read
    row_dead = periods.nth(1).locator(".p-jobs li", has_text="Previous Role 0")
    row_dead.locator(".pj-title").click()
    row_dead.locator(".ap-record button", has_text="Open ad on Seek").wait_for(timeout=10000)
    dead = record_text(row_dead)
    c.check(f"Ad closed (noticed {DEAD_DAY.strftime('%d/%m')})" in dead and "Last seen live" not in dead,
            f"dead ad: 'Ad closed (noticed {DEAD_DAY.strftime('%d/%m')})'")
    c.check("The ad text wasn't kept." in dead and "Full ad text" not in dead, "dead ad: says the ad text wasn't kept")
    c.check(DEAD_SUMMARY in dead, "dead ad: its summary still shows")
    c.check("cover letter (as generated)" in dead.lower() and row_dead.locator(".ar-letter").inner_text() == DEAD_LETTER,
            "dead ad: its letter shows, 'as generated'")
    row_dead.locator(".pj-title").click()
    c.check(row_dead.locator(".ap-record").count() == 0 and "open" not in (row_dead.get_attribute("class") or "").split(),
            "the dead ad's record collapses")

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

    # with one record open, so the layout checks cover it (row A is still open from above)
    c.check(row_a.locator(".ap-record").count() == 1, "row A's record is open for the layout checks")
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "narrow: no sideways page scroll")
    c.check(side.evaluate("(() => { const r = document.querySelector('.ap-record').getBoundingClientRect(); "
                          "return r.right <= window.innerWidth && r.left >= 0; })()"), "narrow: the record fits the panel")
    side.screenshot(path=str(work / "applied-narrow.png"), full_page=True)
    side.set_viewport_size({"width": 760, "height": 900})
    time.sleep(0.4)
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "wide: no sideways page scroll")
    side.screenshot(path=str(work / "applied-wide.png"))
    side.set_viewport_size({"width": 380, "height": 900})
    row_a.locator(".pj-title").click()
    c.check(side.locator(".ap-record").count() == 0, "row A's record is collapsed again")

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
    # A drifted row: applied_at set but status still 'new'. Inserted here rather than in
    # seed() so the period counts the earlier checks rely on don't move. The card must
    # follow applied_at (the one definition of "applied"), not the status column.
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    con.execute("INSERT INTO job_listings (id, source, source_job_id, url, title, company, location, extracted_at) "
                "VALUES (900, 'seek', '99900', 'https://au.seek.com/job/99900', 'Drifted Status Row', "
                "'Company 900', 'Brisbane QLD', ?)", (now,))
    con.execute("INSERT INTO matches (id, user_id, job_id, score, status, applied_at) "
                "VALUES (900, 1, 900, 80, 'new', ?)", (now,))
    con.commit()
    side.click("nav#tabs .tab[data-tab=jobs]")
    side.click("#refresh-btn")
    card_drift = side.locator("#job-list li", has_text="Drifted Status Row")
    card_drift.wait_for(timeout=10000)
    c.check(card_drift.locator(".apply-btn").inner_text() == "✓ Applied",
            "Jobs tab: a row with applied_at set (status still 'new') shows ✓ Applied")
    c.check(card_drift.locator(".del-btn").count() == 0, "...and has no Delete")
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
