"""End-to-end check of the Pipeline tab (docs/pipeline-tab-plan.md) against a scratch backend.

    python tests/e2e/pipeline_e2e.py            # headless Chromium
    python tests/e2e/pipeline_e2e.py --headed
    python tests/e2e/pipeline_e2e.py --keep     # keep the screenshots

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB (alembic head): profile 1 (letter bars at 100, so no job is the
   idle loop's work), 25 unapplied jobs (5 with a letter, 1 whose letter run waits on a
   question, 1 not seen live for 3 days, 18 with no letter), plus an applied, a hidden and a
   closed job that must NOT be listed, and three `generate_letter` costs (12¢ each) for the
   letter estimate. No job has ad text, so the availability check never picks one, and the
   server runs with LLM_PROVIDER=stub, so a stray model call fails instead of costing money.
2. Starts ``scripts/run_api.py test --db <scratch>`` on port 8001 and opens the extension's
   side panel (TEST backend). Every request off this machine is aborted and
   ``chrome.tabs.create`` is stubbed to record the URL (a real tab can load before routing
   attaches, and reach Seek). POST /jobs/{id}/write-letter is intercepted, so nothing is queued.
3. Checks the list (only unapplied jobs; the box shows about 7.5 rows and scrolls; the edge
   shadows; chip counts, filtering, the empty state; search; sort; the filter remembered across
   a panel reload), live reloads (scroll kept, an open detail kept), and the detail view (open,
   "← All jobs", Esc, scroll and focus restored, Open ad recorded, the letter editor, "Write a
   letter" two-click with the estimate and one POST).

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
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dashboard_e2e as dash  # noqa: E402  (server, Checks and wait_for are shared)

ROOT = dash.ROOT
EXTENSION = dash.EXTENSION
PORT = dash.PORT
BACKEND = dash.BACKEND
Checks = dash.Checks
wait_for = dash.wait_for

NOW = dt.datetime.now(dt.timezone.utc)
LETTER_JOBS = [("Letter Job 1", 96), ("Letter Job 2", 91), ("Letter Job 3", 88), ("Letter Job 4", 84),
               ("Letter Job 5", 79)]
PLAIN_SCORES = [92, 41, 87, 63, 75, 55, 81, 68, 47, 72, 59, 66, 83, 52, 77, 61, 44, 69]
VISIBLE = len(LETTER_JOBS) + 1 + 1 + len(PLAIN_SCORES)  # + waiting + stale = 25
NEWEST = "Plain Job 02"  # captured most recently (score 41)


# ---------------------------------------------------------------------------
# Scratch DB
# ---------------------------------------------------------------------------
def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_pipeline.db"
    url = "sqlite:///" + db.as_posix()
    env = {**dash.os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    seed(url)
    return db


def seed(url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.llm.letter.state import JobInfo, LetterState, UserQuestion
    from app.models import CoverLetter, JobListing, JobSkill, LetterRun, LlmUsage, Match, Profile, Skill

    engine = create_engine(url)
    with Session(engine) as s:
        s.add(Profile(id=1, name="E2E Test", email="e2e@example.invalid", password_hash="x",
                      target_role="Software Developer", target_location="Brisbane",
                      preferences=json.dumps({"auto_cover_letter_min_score": 100, "letter_loop_min_score": 100})))
        s.add_all([Skill(user_id=1, name="Typescript Programming"), Skill(user_id=1, name="SQL")])
        s.flush()
        ids = iter(range(1, 1000))
        order = iter(range(1000))  # capture order: a later job is older

        def job(title, score, *, applied=False, hidden=False, closed=False, seen=NOW, letter=None,
                skills=(), captured=None):
            jid = next(ids)
            s.add(JobListing(id=jid, source="seek", source_job_id=f"8{jid:04d}",
                             url=f"https://au.seek.com/job/8{jid:04d}", title=title,
                             company=f"Company {jid}", location="Brisbane QLD",
                             extracted_at=NOW, last_seen_live_at=seen,
                             date_scraped=captured or NOW - dt.timedelta(hours=2 + next(order)),
                             expired_detected_at=NOW if closed else None))
            s.flush()
            for name in skills:
                s.add(JobSkill(job_id=jid, name=name, skill_type="hard"))
            s.add(Match(id=jid, user_id=1, job_id=jid, score=score, status="applied" if applied else "new",
                        reasoning=f"Reasoning for {title}.", gaps=json.dumps([f"Gap for {title}"]),
                        applied_at=NOW if applied else None, hidden_at=NOW if hidden else None, scored_at=NOW))
            s.flush()
            if letter:
                s.add(CoverLetter(match_id=jid, generated_content=letter))
            return jid

        for title, score in LETTER_JOBS:
            job(title, score, letter=f"Letter text for {title}.", skills=("TypeScript", "Kubernetes"))
        waiting = job("Waiting Job", 93, letter="An older letter.", skills=("Kubernetes",))
        state = LetterState(profile_id=1, job=JobInfo(job_id=waiting, title="Waiting Job"), user_questions=[
            UserQuestion(id="Q1", requirement_id="R1", requirement_text="Experience with Kubernetes",
                         skill="Kubernetes", skill_key="kubernetes",
                         prompt="Have you used Kubernetes? The ad asks for it.")])
        s.add(LetterRun(match_id=waiting, engine="agent", status="waiting_user", state=state.model_dump_json()))
        job("Stale Job", 70, seen=NOW - dt.timedelta(days=3), skills=("Angular",))
        for i, score in enumerate(PLAIN_SCORES, start=1):
            captured = NOW - dt.timedelta(minutes=5) if f"Plain Job {i:02d}" == NEWEST else None
            job(f"Plain Job {i:02d}", score, captured=captured, skills=("Angular",) if i % 3 == 0 else ())
        job("Applied Job", 99, applied=True)
        job("Hidden Job", 98, hidden=True)
        job("Closed Job", 97, closed=True)
        for _ in range(3):
            s.add(LlmUsage(task="generate_letter", tier="strong", model="stub", cost_usd=Decimal("0.12")))
        s.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
STUB_JS = """() => { window.__opened = []; window.__writes = [];
    navigator.clipboard.writeText = t => Promise.resolve();
    chrome.tabs.create = o => { window.__opened.push(o.url); return Promise.resolve({}); }; }"""


def run(headed: bool, keep: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    if not dash.port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")
    work = Path(tempfile.mkdtemp(prefix="pipeline_e2e_"))
    db = make_scratch_db(work)
    stub_log = work / "llm_stub.jsonl"
    server = dash.start_server(db, work / "server.log", stub_log)
    con = sqlite3.connect(db)
    usage_before = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    c = Checks()
    blocked: list[str] = []
    errors: list[str] = []
    writes: list[str] = []

    def intercepted(u: str) -> bool:
        url = urlparse(u)
        if url.scheme in ("chrome-extension", "blob", "data"):
            return False
        return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

    def route(r):
        blocked.append(r.request.url)
        return r.abort()

    def fake_write(r):
        writes.append(r.request.url)
        return r.fulfill(status=200, content_type="application/json", body=json.dumps({"queued": 1, "reason": "queued"}))

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
            side.route("**/write-letter*", fake_write)
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")
            side.evaluate(STUB_JS)

            run_list_checks(c, side, work)
            run_detail_checks(c, side, writes)

            c.check(not errors, f"no page errors {errors[:3]}")
            usage_after = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            c.check(usage_after == usage_before, "no llm_usage row written")
            c.check(not stub_log.exists() or not stub_log.read_text(encoding="utf-8").strip(), "no model call")
            c.check(not blocked, f"nothing left the machine (no Seek request either) {blocked[:3]}")
            ctx.close()
    finally:
        con.close()
        dash.stop_server(server)

    failed = c.failed()
    print(f"\n{len(c.results) - failed}/{len(c.results)} checks passed. Screenshots and logs: {work}")
    if not failed and not headed and not keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


def rows(side) -> list[str]:
    return side.evaluate("[...document.querySelectorAll('#job-list .pl-title')].map(e => e.textContent)")


def box_scroll(side) -> float:
    return side.evaluate("document.getElementById('pl-box').scrollTop")


def chip(side, key):
    return side.locator(f"#pl-chips .pl-chip-btn[data-filter={key}]")


def run_list_checks(c: Checks, side, work: Path) -> None:
    print("List")
    side.click("nav#tabs .tab[data-tab=jobs]")
    c.check(wait_for(lambda: len(rows(side)) == VISIBLE), f"all {VISIBLE} unapplied jobs are listed ({len(rows(side))})")
    titles = rows(side)
    c.check(not {"Applied Job", "Hidden Job", "Closed Job"} & set(titles), "applied, hidden and closed jobs are not")
    c.check(titles[0] == "Letter Job 1" and titles[1] == "Waiting Job", f"sorted by score, best first ({titles[:3]})")
    c.check(side.inner_text("#pl-count") == f"{VISIBLE} jobs", f"the count says {VISIBLE} jobs")

    box = side.evaluate("""(() => { const b = document.getElementById('pl-box');
        const row = document.querySelector('#job-list li').getBoundingClientRect().height;
        return { client: b.clientHeight, scroll: b.scrollHeight, row }; })()""")
    c.check(box["scroll"] > box["client"], f"the box scrolls ({box})")
    rows_visible = box["client"] / box["row"]
    c.check(7.0 <= rows_visible <= 8.0, f"about 7.5 rows show ({rows_visible:.2f})")
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "no sideways page scroll")
    wrap_cls = lambda: side.get_attribute("#pl-box-wrap", "class") or ""  # noqa: E731
    c.check("can-down" in wrap_cls() and "can-up" not in wrap_cls(), "at the top: only the bottom shadow shows")
    side.evaluate("document.getElementById('pl-box').scrollTop = 1e6")
    c.check(wait_for(lambda: "can-up" in wrap_cls() and "can-down" not in wrap_cls()),
            "at the bottom: only the top shadow shows")
    side.evaluate("document.getElementById('pl-box').scrollTop = 0")
    side.screenshot(path=str(work / "pipeline.png"), full_page=True)

    # status chips on the rows
    def status_of(title):
        return side.locator("#job-list li", has_text=title).locator(".pl-status").inner_text()
    c.check(status_of("Waiting Job") == "❓ Answer", "a waiting run's row says ❓ Answer")
    c.check(status_of("Letter Job 3") == "✅ Letter", "a job with a letter says ✅ Letter")
    c.check(status_of("Stale Job") == "🕓 Check due", "a job not seen live in a day says 🕓 Check due")
    c.check(status_of("Plain Job 01") == "No letter", "a job below the bar says No letter")

    # chips and counts
    counts = side.evaluate("""Object.fromEntries([...document.querySelectorAll('#pl-chips .pl-chip-btn')]
        .map(b => [b.dataset.filter, Number(b.querySelector('.pl-chip-n').textContent)]))""")
    c.check(counts == {"all": VISIBLE, "question": 1, "letter": 5, "writing": 0, "none": 18, "check": 1},
            f"chip counts ({counts})")
    c.check(chip(side, "all").get_attribute("aria-pressed") == "true", "All is selected at first")
    chip(side, "letter").click()
    c.check(wait_for(lambda: len(rows(side)) == 5) and all(t.startswith("Letter Job") for t in rows(side)),
            f"✅ Letter shows the 5 jobs with a letter ({rows(side)})")
    c.check(side.inner_text("#pl-count") == f"Showing 5 of {VISIBLE}", "the count says Showing 5 of 25")
    c.check(chip(side, "letter").get_attribute("aria-pressed") == "true"
            and chip(side, "all").get_attribute("aria-pressed") == "false", "the pressed chip is marked")
    box_h = side.evaluate("document.getElementById('pl-box').clientHeight")
    c.check(box_h < 5.5 * 46, f"with 5 rows the box shrinks to fit ({box_h})")
    chip(side, "writing").click()
    c.check(wait_for(lambda: side.locator("#job-list li.pl-empty").count() == 1)
            and "No letters being written" in side.inner_text("#job-list"), "an empty filter says so")
    chip(side, "check").click()
    c.check(wait_for(lambda: rows(side) == ["Stale Job"]), "🕓 Check due shows the stale job")

    # the filter is remembered across a panel reload
    chip(side, "letter").click()
    side.wait_for_timeout(300)
    side.reload()
    side.evaluate(STUB_JS)
    side.click("nav#tabs .tab[data-tab=jobs]")
    c.check(wait_for(lambda: len(rows(side)) == 5) and chip(side, "letter").get_attribute("aria-pressed") == "true",
            "after a panel reload the ✅ Letter filter is still on")
    chip(side, "all").click()
    c.check(wait_for(lambda: len(rows(side)) == VISIBLE), "All shows every job again")

    # search
    c.check(side.is_visible("#pl-search"), "the search box shows (more than 10 jobs)")
    side.fill("#pl-search", "plain job 1")
    c.check(wait_for(lambda: rows(side) and all(t.startswith("Plain Job 1") for t in rows(side))),
            f"search filters by title ({rows(side)})")
    side.fill("#pl-search", "Company 3")
    c.check(wait_for(lambda: len(rows(side)) >= 1), "search matches the company too")
    side.fill("#pl-search", "zzz")
    c.check(wait_for(lambda: 'No job matches "zzz"' in side.inner_text("#job-list")), "no match says so")
    side.fill("#pl-search", "")
    c.check(wait_for(lambda: len(rows(side)) == VISIBLE), "clearing the search shows every job")

    # sort
    side.select_option("#pl-sort", "newest")
    c.check(wait_for(lambda: rows(side)[0] == NEWEST), f"Newest puts the latest capture first ({rows(side)[:2]})")
    side.select_option("#pl-sort", "score")
    c.check(wait_for(lambda: rows(side)[0] == "Letter Job 1"), "Score puts the best first again")

    # a live reload keeps the scroll position
    side.evaluate("document.getElementById('pl-box').scrollTop = 150")
    side.evaluate("loadJobs()")
    side.wait_for_timeout(800)
    c.check(abs(box_scroll(side) - 150) < 2, f"a reload keeps the scroll position ({box_scroll(side)})")


def run_detail_checks(c: Checks, side, writes: list[str]) -> None:
    print("Detail")
    side.evaluate("document.getElementById('pl-box').scrollTop = 200")
    side.wait_for_timeout(200)
    target = side.locator("#job-list li", has_text="Plain Job 05").locator(".pl-row")  # score 75, below the bar
    target.scroll_into_view_if_needed()
    scroll_before = box_scroll(side)
    target.click()
    view = side.locator("#job-detail-view")
    c.check(wait_for(lambda: view.is_visible()) and side.is_hidden("#pl-page"), "a row opens the detail view")
    c.check(side.inner_text("#pl-detail .pl-d-title") == "Plain Job 05", "the detail shows the job")
    c.check("Reasoning for Plain Job 05." in side.inner_text("#pl-detail")
            and "Gap for Plain Job 05" in side.inner_text("#pl-detail"), "...with why it scored and the gaps")
    c.check(side.evaluate("document.activeElement.id") == "pl-back", "focus moves to ← All jobs")
    c.check(wait_for(lambda: "below your letter bar" in side.inner_text("#pl-detail .job-detail")),
            "the body says there is no letter and why")

    # Open ad: recorded, never opened
    view.locator("button", has_text="Open ad on Seek").click()
    c.check(wait_for(lambda: side.evaluate("window.__opened.at(-1) || ''").startswith("https://au.seek.com/job/8")),
            "Open ad on Seek goes through chrome.tabs.create (stubbed)")

    # Write a letter: two clicks, the estimate, one POST
    write = view.locator(".pl-write")
    c.check(write.count() == 1 and write.inner_text() == "Write a letter", "a job below the bar offers Write a letter")
    write.click()
    c.check(wait_for(lambda: write.inner_text() == "Write letter? ≈ 12¢"), f"the first click shows the estimate ({write.inner_text()})")
    c.check(not writes, "one click sends nothing")
    write.click()
    c.check(wait_for(lambda: write.inner_text() == "Queued ✍") and len(writes) == 1
            and "/write-letter" in writes[0], f"the second click POSTs once ({writes})")

    # an open detail survives a live reload
    side.evaluate("loadJobs()")
    side.wait_for_timeout(800)
    c.check(view.is_visible() and side.inner_text("#pl-detail .pl-d-title") == "Plain Job 05",
            "a live reload leaves the open detail alone")

    # back: Esc, scroll and focus restored
    side.keyboard.press("Escape")
    c.check(wait_for(lambda: view.is_hidden() and side.is_visible("#pl-page")), "Esc goes back to the list")
    c.check(abs(box_scroll(side) - scroll_before) < 2, f"the list is where it was ({box_scroll(side)} vs {scroll_before})")
    c.check(side.evaluate("document.activeElement.closest('li')?.textContent.includes('Plain Job 05')"),
            "focus is back on the row just viewed")

    # a job with a letter: the editor; no Write a letter
    side.locator("#job-list li", has_text="Letter Job 2").locator(".pl-row").click()
    c.check(wait_for(lambda: side.locator("#pl-detail textarea").count() == 1)
            and side.input_value("#pl-detail textarea") == "Letter text for Letter Job 2.", "a letter job shows its letter")
    c.check(side.locator("#pl-detail .pl-write").count() == 0, "...and no Write a letter")
    side.click("#pl-back")
    c.check(wait_for(lambda: side.is_visible("#pl-page")), "← All jobs goes back")

    # keyboard: Enter on a focused row opens it
    side.locator("#job-list li", has_text="Stale Job").locator(".pl-row").focus()
    side.keyboard.press("Enter")
    c.check(wait_for(lambda: view.is_visible()) and side.inner_text("#pl-detail .pl-d-title") == "Stale Job",
            "Enter on a focused row opens it")
    c.check(side.inner_text("#pl-detail .pl-status") == "🕓 Check due", "the stale job's detail says 🕓 Check due")
    side.click("#pl-back")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the screenshots and logs")
    args = parser.parse_args()
    sys.exit(run(args.headed, args.keep))
