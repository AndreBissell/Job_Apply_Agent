"""End-to-end check of the simplified Settings view (docs/settings-simplify-plan.md).

    python tests/e2e/settings_e2e.py            # headless Chromium
    python tests/e2e/settings_e2e.py --headed
    python tests/e2e/settings_e2e.py --keep     # keep the screenshots

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB (alembic head): profile 1 with REAL's letter settings (auto 80,
   pipeline 75, so the pipeline score does nothing today), three one-pass letter costs (2¢ each)
   for the hint's estimate, and six jobs found by the search "data analyst" for the Overview's
   search-performance table. The jobs score below every bar, have no ad text and are never
   extracted, so neither the idle loop nor the availability check picks one; the server runs
   with LLM_PROVIDER=stub, so a stray model call fails instead of costing money.
2. Starts ``scripts/run_api.py test --db <scratch>`` on port 8001 and opens the side panel (TEST
   backend). Every request off this machine is aborted and ``chrome.tabs.create`` is stubbed.
3. Checks: the "Fact-check & revise" dropdown (REAL's 75/80 loads as Every letter; each option
   writes the right keys; options at or below the auto score are never offered; a stored
   non-preset score is kept as its own option; the hint and its live estimate), auto-save of the
   numbers (out of range snaps back and saves nothing), each Extras box writes only its key, the
   (i) notes, the removed controls are gone, values survive a panel reload, and the Overview's
   "How past searches did" table. Never presses ↻ (that calls the model).

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
Checks = dash.Checks
wait_for = dash.wait_for

NOW = dt.datetime.now(dt.timezone.utc)
REMOVED_IDS = ["letter-loop-enabled", "letter-loop-min", "letter-loop-save", "letter-engine", "limit-drafts",
               "limit-tools", "limit-usd", "limits-note", "auto-letter-save", "scan-pages-save", "search-perf-btn"]
EXTRAS = {"side-resume": "resume_advice_enabled", "side-screening": "screening_answers_enabled",
          "side-learning": "learning_suggestions_enabled"}


def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_settings.db"
    url = "sqlite:///" + db.as_posix()
    env = {**dash.os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    seed(url)
    return db


def seed(url: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import JobListing, LlmUsage, Match, Profile

    engine = create_engine(url)
    with Session(engine) as s:
        s.add(Profile(id=1, name="E2E Test", email="e2e@example.invalid", password_hash="x",
                      target_role="Data Analyst", target_location="Brisbane",
                      preferences=json.dumps({"auto_cover_letter_min_score": 80, "letter_loop_min_score": 75,
                                              "obligation_cycle_start": dt.date.today().isoformat()})))
        s.flush()
        for i, score in enumerate([70, 40, 35, 30, 25, 20], start=1):
            s.add(JobListing(id=i, source="seek", source_job_id=f"7{i:04d}", url=f"https://au.seek.com/job/7{i:04d}",
                             title=f"Data Analyst {i}", company=f"Company {i}", discovered_query="data analyst",
                             date_scraped=NOW))
            s.flush()
            s.add(Match(id=i, user_id=1, job_id=i, score=score, status="new", scored_at=NOW))
        for _ in range(3):
            s.add(LlmUsage(task="cover_letter", tier="strong", model="stub", cost_usd=Decimal("0.02")))
        s.commit()
    engine.dispose()


STUB_JS = """() => { window.__opened = [];
    chrome.tabs.create = o => { window.__opened.push(o.url); return Promise.resolve({}); }; }"""


def run(headed: bool, keep: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    if not dash.port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")
    work = Path(tempfile.mkdtemp(prefix="settings_e2e_"))
    db = make_scratch_db(work)
    stub_log = work / "llm_stub.jsonl"
    server = dash.start_server(db, work / "server.log", stub_log)
    con = sqlite3.connect(db)
    (usage_before,), = con.execute("SELECT COUNT(*) FROM llm_usage").fetchall()
    c = Checks()
    blocked: list[str] = []
    errors: list[str] = []

    def prefs() -> dict:
        (raw,), = con.execute("SELECT preferences FROM profiles WHERE id = 1").fetchall()
        return json.loads(raw or "{}")

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
            panel = f"chrome-extension://{ext_id}/sidebar.html"
            side.goto(panel)
            side.evaluate(STUB_JS)

            run_layout_checks(c, side, work)
            run_quality_checks(c, side, prefs)
            run_number_checks(c, side, prefs)
            run_toggle_checks(c, side, prefs)
            run_reload_checks(c, side, prefs, panel)
            run_search_perf_checks(c, side)

            c.check(not errors, f"no page errors {errors[:3]}")
            (usage_after,), = con.execute("SELECT COUNT(*) FROM llm_usage").fetchall()
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


def options(side) -> list[str]:
    return side.evaluate("[...document.querySelectorAll('#letter-quality option')].map(o => o.value)")


def wait_db(side, fn, timeout_s: float = 10):
    """wait_for for a database read. It waits with Playwright, not time.sleep: the context routes
    requests through a Python predicate, and Playwright's sync API only answers it while Python is
    inside a Playwright call, so a plain sleep would hold the panel's save until the next one."""
    end = time.time() + timeout_s
    value = fn()
    while not value and time.time() < end:
        side.wait_for_timeout(200)
        value = fn()
    return value


def loop_keys(p: dict) -> dict:
    return {k: p.get(k) for k in ("letter_loop_enabled", "letter_loop_min_score")}


def hint(side) -> str:
    return side.inner_text("#letter-quality-hint")


def saved_text(side, control: str) -> str:
    return side.evaluate(f"document.querySelector('{control}').closest('.pref-row').querySelector('.pref-saved').textContent")


def open_settings(side) -> None:
    if side.is_hidden("#settings-section"):
        side.click("#settings-btn")
    wait_for(lambda: side.is_visible("#settings-section"))


def run_layout_checks(c: Checks, side, work: Path) -> None:
    print("Layout")
    wait_for(lambda: side.input_value("#auto-letter-min") == "80")  # preferences loaded
    open_settings(side)
    c.check(side.locator("#settings-section .set-group").all_text_contents() == ["Cover letters", "Finding jobs"],
            "two groups: Cover letters, Finding jobs")
    c.check(side.locator("#settings-section button.btn:not(#settings-back)").count() == 0, "no Save buttons")
    gone = [i for i in REMOVED_IDS if side.locator(f"#{i}").count()]
    c.check(not gone, f"engine, limits and Save controls are gone ({gone})")
    c.check(side.locator("#settings-section #search-perf").count() == 0, "search performance left Settings")
    c.check(side.locator("#personalise-panel .pref-more:visible").count() == 0, "every (i) note starts closed")
    c.check(side.evaluate("document.scrollingElement.scrollWidth <= window.innerWidth"), "no sideways page scroll")
    side.screenshot(path=str(work / "settings.png"), full_page=True)

    info = side.locator("button[aria-controls=info-availability]")
    info.click()
    c.check(side.is_visible("#info-availability") and info.get_attribute("aria-expanded") == "true",
            "(i) opens its note")
    info.click()
    c.check(side.is_hidden("#info-availability") and info.get_attribute("aria-expanded") == "false",
            "(i) again closes it")


def run_quality_checks(c: Checks, side, prefs) -> None:
    print("Fact-check & revise")
    c.check(side.input_value("#letter-quality") == "all", "REAL's auto 80 / pipeline 75 loads as Every letter")
    c.check(options(side) == ["all", "85", "90", "95", "never"], f"options above the auto score only ({options(side)})")
    c.check(hint(side).startswith("Every automatic letter"), f"the hint says every letter is checked ({hint(side)!r})")

    side.select_option("#letter-quality", "85")
    c.check(wait_db(side, lambda: prefs().get("letter_loop_min_score") == 85 and prefs().get("letter_loop_enabled") is True),
            f"Score 85+ writes enabled + 85 ({loop_keys(prefs())})")
    c.check(wait_for(lambda: saved_text(side, "#letter-quality") == "Saved ✓"), "Saved ✓ shows beside it")
    c.check(wait_for(lambda: hint(side) == "80–84: quick pass ≈ 2¢ · 85+: fact-checked ≈ 20¢"),
            f"the hint splits the bands with the live estimate ({hint(side)!r})")

    side.select_option("#letter-quality", "never")
    c.check(wait_db(side, lambda: prefs().get("letter_loop_enabled") is False), f"Never writes enabled = false ({loop_keys(prefs())})")
    c.check(prefs().get("letter_loop_min_score") == 85, "Never keeps the stored score")
    c.check(wait_for(lambda: hint(side) == "Every letter is a quick pass ≈ 2¢, with no checks."), f"Never's hint ({hint(side)!r})")

    side.select_option("#letter-quality", "all")
    c.check(wait_db(side, lambda: prefs().get("letter_loop_enabled") is True and prefs().get("letter_loop_min_score") == 0),
            f"Every letter writes enabled + 0, so it stays true if the auto score moves ({loop_keys(prefs())})")

    side.select_option("#letter-quality", "90")
    wait_db(side, lambda: prefs().get("letter_loop_min_score") == 90)
    side.fill("#auto-letter-min", "92")
    side.press("#auto-letter-min", "Tab")
    c.check(wait_db(side, lambda: prefs().get("auto_cover_letter_min_score") == 92), "the auto score saves without a button")
    c.check(wait_for(lambda: side.input_value("#letter-quality") == "all" and options(side) == ["all", "95", "never"]),
            f"raising it past the chosen bar redraws as Every letter ({options(side)})")


def run_number_checks(c: Checks, side, prefs) -> None:
    print("Numbers")
    side.fill("#auto-letter-min", "150")
    side.press("#auto-letter-min", "Tab")
    c.check(wait_for(lambda: side.input_value("#auto-letter-min") == "92"), "an out-of-range score snaps back")
    c.check(saved_text(side, "#auto-letter-min") == "0–100 only", "and says why")
    c.check(prefs().get("auto_cover_letter_min_score") == 92, "and saves nothing")

    side.fill("#scan-pages", "30")
    side.press("#scan-pages", "Tab")
    c.check(wait_for(lambda: side.input_value("#scan-pages") == "10") and "scan_max_pages" not in prefs(),
            "pages per scan above the 25 ceiling snaps back and saves nothing")
    side.fill("#scan-pages", "12")
    side.press("#scan-pages", "Tab")
    c.check(wait_db(side, lambda: prefs().get("scan_max_pages") == 12), "a valid pages-per-scan saves")
    c.check(side.evaluate("scanMaxPages") == 12, "and the scan uses it")


def run_toggle_checks(c: Checks, side, prefs) -> None:
    print("Toggles")
    for box_id, key in EXTRAS.items():
        before = prefs()
        side.uncheck(f"#{box_id}")
        c.check(wait_db(side, lambda: prefs().get(key) is False), f"{box_id} off writes {key} = false")
        others = {k: v for k, v in prefs().items() if k in EXTRAS.values() and k != key}
        c.check(all(others.get(k) == before.get(k) for k in others), f"{box_id} touches only its own key")
    side.check("#side-resume")
    c.check(wait_db(side, lambda: prefs().get("resume_advice_enabled") is True), "ticking it again writes true")
    side.check("#llm-suggest")
    c.check(wait_db(side, lambda: prefs().get("llm_search_suggestions") is True), "AI suggestions on saves (no model call yet)")
    side.uncheck("#availability-checks")
    c.check(wait_db(side, lambda: prefs().get("availability_checks_enabled") is False), "Hide closed ads off saves")
    side.check("#availability-checks")
    wait_db(side, lambda: prefs().get("availability_checks_enabled") is True)


def run_reload_checks(c: Checks, side, prefs, panel: str) -> None:
    print("Reload")
    # a stored pipeline score that isn't a preset must survive as its own option
    side.evaluate(f"""fetch('{dash.BACKEND}/profile/1/preferences', {{ method: 'PUT',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ auto_cover_letter_min_score: 75, letter_loop_min_score: 87, letter_loop_enabled: true }}) }})""")
    wait_db(side, lambda: prefs().get("letter_loop_min_score") == 87)
    side.goto(panel)
    side.evaluate(STUB_JS)
    wait_for(lambda: side.input_value("#auto-letter-min") == "75")
    open_settings(side)
    c.check(side.input_value("#letter-quality") == "87" and options(side) == ["all", "85", "87", "90", "95", "never"],
            f"a stored 87 shows as Score 87+ ({options(side)})")
    c.check(side.input_value("#scan-pages") == "12", "pages per scan survives a reload")
    c.check(side.is_checked("#side-resume") and not side.is_checked("#side-screening")
            and not side.is_checked("#side-learning") and side.is_checked("#llm-suggest")
            and side.is_checked("#availability-checks") and side.is_checked("#qa-question-help"),
            "the toggles survive a reload")


def run_search_perf_checks(c: Checks, side) -> None:
    print("Overview: How past searches did")
    side.click("#settings-back")
    side.click("nav#tabs .tab[data-tab=overview]")
    c.check(side.is_visible("#ov-search-perf") and not side.evaluate("document.getElementById('ov-search-perf').open"),
            "the Overview has a closed 'How past searches did'")
    side.click("#ov-search-perf summary")
    c.check(wait_for(lambda: side.locator("#search-perf table tr").count() == 2), "opening it loads the table")
    cells = side.locator("#search-perf table tr").nth(1).locator("td").all_inner_texts()
    c.check(cells == ["data analyst", "6", "0", "0%"], f"one row for 'data analyst': 6 jobs, 0 good ({cells})")
    c.check("weak" in (side.locator("#search-perf table tr").nth(1).get_attribute("class") or ""),
            "a big, low-yield search is marked weak")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the screenshots and logs")
    args = ap.parse_args()
    sys.exit(run(args.headed, args.keep))
