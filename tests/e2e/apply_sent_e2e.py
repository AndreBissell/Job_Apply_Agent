"""End-to-end check of Quick Apply evidence: Apply-button detection, auto-register on the
"application sent" page, and the in-app screenshot (docs/centrelink-ease-apply-plan.md).

    python tests/e2e/apply_sent_e2e.py            # headless Chromium
    python tests/e2e/apply_sent_e2e.py --headed
    python tests/e2e/apply_sent_e2e.py --keep     # keep the temp folder (DB, screenshots)

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB in a temp folder (alembic head, profile 1, two known jobs
   with a cover letter) and starts ``scripts/run_api.py test --db <scratch>`` on port 8001
   with LLM_PROVIDER=stub (a stray model call fails instead of costing money) and
   APP_SCREENSHOTS_DIR in the temp folder (no file lands in app/screenshots).
2. Launches Chromium with the unpacked extension, switched to the TEST backend.
3. Serves Seek pages built from tests/fixtures/apply_flow_samples.json (the scrubbed
   Apply buttons and "application sent" page the user copied on 2026-10-14) through
   request interception. Any other Seek request gets a 404 from the harness and anything
   else off-machine is aborted: nothing reaches Seek or the internet. No tab is opened by
   the extension here (the side panel isn't used).
4. Checks: the detail page stores apply_type quick / external from the button text; the
   cover-letter overlay has no Screenshot button and its Mark Applied records 'manual'
   with no screenshot and no download; the sent page records 'auto' with a screenshot file
   in the app's folder, shows the confirmation panel, and a reload changes nothing; Retake
   replaces the file; a job never captured gets a stub (placeholder title, employer from the
   page) and an unscored applied match; a review -> sent change without a page load is
   caught; the review page and the bare /apply URL (where external ads' buttons go) record
   nothing; the greeting with the user's name is stored nowhere; the extension makes no
   click/input/submit on the page and no download.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"
FIXTURE = ROOT / "tests" / "fixtures" / "apply_flow_samples.json"
PORT = 8001
BACKEND = f"http://127.0.0.1:{PORT}"
SEEK = "https://www.seek.com.au"
SEEK_HOSTS = {"www.seek.com.au", "au.seek.com", "seek.com.au"}
SENT_HOST = "seek-assistant-apply-sent-host"
QA_HOST = "seek-assistant-quick-apply-host"
NAME = "NAME"  # the scrubbed first name in the sent page's greeting: must be stored nowhere

# Jobs: 1 (quick button, letter, gets the sent page), 2 (external button), 6 (quick button,
# letter, Mark Applied by hand), 5 (never captured: sent page only), 7 (review -> sent in
# place), 3 (the bare /apply URL), 8 (review page only).
J_QUICK, J_EXTERNAL, J_NEW, J_MANUAL, J_SPA, J_BARE, J_REVIEW = (
    "90000001", "90000002", "90000005", "90000006", "90000007", "90000003", "90000008")

# The side panel's tab opening is recorded, never done: a tab it opens can start loading
# before Playwright's routing attaches and reach real Seek (CLAUDE.md e2e rule).
TABS_STUB = """
window.__opened = [];
if (globalThis.chrome && chrome.tabs) {
  chrome.tabs.create = (o) => { window.__opened.push((o && o.url) || ''); return Promise.resolve({}); };
}
"""

RECORDER = """
<script>
  window.__events = [];
  for (const t of ['click', 'input', 'change', 'submit', 'keydown']) {
    document.addEventListener(t, (e) => {
      // Clicks on our own overlays (the harness presses their buttons) are not page events.
      if (e.composedPath().some((n) => n && n.id && n.id.startsWith('seek-assistant-'))) return;
      window.__events.push(t + ':' + (e.target.id || e.target.tagName));
    }, true);
  }
</script>
"""


def page(title: str, body: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>{RECORDER}'
            f'</head><body>{body}</body></html>')


def detail_page(job_id: str, title: str, button_html: str) -> str:
    return page(f"{title} Job in Brisbane - SEEK", (
        f'<h1 data-automation="job-detail-title">{title}</h1>'
        f'{button_html}'
        f'<div data-automation="jobAdDetails"><p>About the role: {title} for job {job_id}. '
        'Build and support systems.</p></div>'))


def review_page() -> str:
    steps = ["Choose documents", "Answer employer questions", "Update SEEK Profile", "Review and submit"]
    current = ' aria-current="step"'
    nav = "".join(f'<button type="button"{current if s == steps[-1] else ""}>{s}</button>' for s in steps)
    return page("Apply | SEEK", (
        f'<div data-automation="job-header"><p>Applying for</p><h1>Some Role</h1>'
        f'<nav aria-label="Progress bar">{nav}</nav></div>'
        '<main><p>Check your application.</p><button data-testid="review-submit-application">Submit</button></main>'))


def sent_page(sample: dict) -> str:
    return page(sample["title"], f'<main>{sample["html"]}<h2>You might also like...</h2>'
                                 '<a href="/job/99999999">Junior Example Role</a></main>')


# ---------------------------------------------------------------------------
# Scratch backend
# ---------------------------------------------------------------------------
def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_e2e.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + db.as_posix(), "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    con = sqlite3.connect(db)
    # Availability checks off: the side panel (opened at the end) must not visit job pages.
    con.execute("INSERT INTO profiles (id, name, email, password_hash, preferences) VALUES (1, 'E2E Test', "
                "'e2e@example.invalid', 'x', ?)", (json.dumps({"availability_checks_enabled": False}),))
    for sid, title in ((J_QUICK, "Graduate Engineer"), (J_MANUAL, "Support Analyst")):
        cur = con.execute("INSERT INTO job_listings (source, source_job_id, url, title, company) "
                          "VALUES ('seek', ?, ?, ?, 'Example Co')", (sid, f"{SEEK}/job/{sid}", title))
        match = con.execute("INSERT INTO matches (user_id, job_id, score, status) VALUES (1, ?, 90, 'new')",
                            (cur.lastrowid,)).lastrowid
        con.execute("INSERT INTO cover_letters (match_id, generated_content, status) VALUES (?, ?, 'draft')",
                    (match, f"Dear Hiring Manager, about {title}. Sincerely, E2E Test"))
    con.commit()
    con.close()
    return db


def port_free() -> bool:
    try:
        urllib.request.urlopen(f"{BACKEND}/health", timeout=1)
        return False
    except Exception:
        return True


def start_server(db: Path, work: Path) -> subprocess.Popen:
    stub_responses = work / "llm_stub_responses.json"
    stub_responses.write_text("{}", encoding="utf-8")
    env = {**os.environ, "LLM_PROVIDER": "stub", "PYTHONUNBUFFERED": "1",
           "LLM_STUB_RESPONSES": str(stub_responses), "LLM_STUB_LOG": str(work / "llm_stub.jsonl"),
           "APP_SCREENSHOTS_DIR": str(work / "shots")}
    log = open(work / "server.log", "w", encoding="utf-8")
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
    raise SystemExit(f"backend did not start; see {work / 'server.log'}")


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
        print(("PASS " if ok else "FAIL ") + what)

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.results if not ok)


# The page whose timer the waits use. Never time.sleep while the browser runs: the
# request router is a Python callback, so a blocked thread stalls every request.
PAGE = None


def wait_until(fn, timeout_s: float = 20, step_s: float = 0.25):
    end = time.time() + timeout_s
    value = fn()
    while not value and time.time() < end:
        PAGE.wait_for_timeout(int(step_s * 1000))
        value = fn()
    return value


def shadow_text(pg, host_id: str) -> str:
    return pg.evaluate("""(id) => {
      const host = document.getElementById(id);
      return host && host.shadowRoot ? host.shadowRoot.textContent : '';
    }""", host_id)


def shadow_buttons(pg, host_id: str) -> list[str]:
    return pg.evaluate("""(id) => {
      const host = document.getElementById(id);
      return host && host.shadowRoot ? [...host.shadowRoot.querySelectorAll('button')].map((b) => b.textContent.trim()) : [];
    }""", host_id)


def run(headed: bool, keep: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    samples = json.loads(FIXTURE.read_text(encoding="utf-8"))
    buttons = {s["id"]: s for s in samples["apply_buttons"]}
    sent = samples["sent_page"]
    if not port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")

    work = Path(tempfile.mkdtemp(prefix="sent_e2e_"))
    shots_dir = work / "shots"
    shots_dir.mkdir()
    repo_shots = ROOT / "app" / "screenshots"
    repo_shots_before = sorted(p.name for p in repo_shots.glob("*")) if repo_shots.exists() else []
    db = make_scratch_db(work)
    server = start_server(db, work)
    c = Checks()
    blocked: list[str] = []
    unknown_seek: list[str] = []
    downloads: list[str] = []

    def quick_button(job_id: str) -> str:
        return buttons["quick"]["html"].replace(buttons["quick"]["job_id"], job_id)

    pages = {
        f"/job/{J_QUICK}": detail_page(J_QUICK, "Graduate Engineer", quick_button(J_QUICK)),
        f"/job/{J_EXTERNAL}": detail_page(J_EXTERNAL, "Software Engineer", buttons["external"]["html"]),
        f"/job/{J_MANUAL}": detail_page(J_MANUAL, "Support Analyst", quick_button(J_MANUAL)),
        f"/job/{J_QUICK}/apply/success": sent_page(sent),
        f"/job/{J_NEW}/apply/success": sent_page(sent),
        f"/job/{J_SPA}/apply/review": review_page(),
        f"/job/{J_REVIEW}/apply/review": review_page(),
        f"/job/{J_BARE}/apply": page("SEEK", "<main><p>Taking you to the employer's site…</p></main>"),
    }

    def route(r):
        url = urlparse(r.request.url)
        if url.hostname in SEEK_HOSTS:
            body = pages.get(url.path.rstrip("/") or "/")
            if body is not None and r.request.resource_type == "document":
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
            unknown_seek.append(r.request.url)
            return r.fulfill(status=404, body="not a fixture")
        blocked.append(r.request.url)
        return r.abort()

    con = sqlite3.connect(db)

    def q(sql: str, *args):
        return con.execute(sql, args).fetchall()

    def job_row(sid: str):
        rows = q("SELECT id, title, company, apply_type, raw_description FROM job_listings WHERE source_job_id = ?", sid)
        return rows[0] if rows else None

    def match_row(sid: str):
        rows = q("SELECT m.score, m.status, m.applied_at, m.applied_via, m.screenshot_path, m.screenshot_taken_at "
                 "FROM matches m JOIN job_listings j ON j.id = m.job_id WHERE j.source_job_id = ?", sid)
        return rows[0] if rows else None

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(work / "profile"), channel="chromium", headless=not headed, accept_downloads=True,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )

            def intercepted(u: str) -> bool:
                url = urlparse(u)
                if url.scheme == "chrome-extension":
                    return False
                return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

            ctx.route(intercepted, route)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
            for extra in ctx.pages:  # the initial blank tab: one tab only, so ours is the active one
                extra.close()
            pg = ctx.new_page()
            global PAGE
            PAGE = pg
            pg.on("console", lambda m: m.text.startswith("[SeekAssistant]") and print("   console:", m.text))
            pg.on("download", lambda d: downloads.append(d.suggested_filename))

            # --- Apply-button detection on detail pages -----------------------------------
            pg.goto(f"{SEEK}/job/{J_QUICK}")
            c.check(wait_until(lambda: (job_row(J_QUICK) or [None] * 5)[4]) is not None,
                    "quick ad: the detail page was captured")
            c.check(job_row(J_QUICK)[3] == "quick", f"quick ad: apply_type 'quick' ({job_row(J_QUICK)[3]})")
            c.check(wait_until(lambda: shadow_buttons(pg, QA_HOST)) != [],
                    "quick ad: the cover-letter overlay is shown")
            c.check(shadow_buttons(pg, QA_HOST) == ["Copy", "Save edits", "⟳ Rewrite", "Mark Applied"],
                    f"overlay buttons are Copy / Save edits / Rewrite / Mark Applied, no Screenshot ({shadow_buttons(pg, QA_HOST)})")

            pg.goto(f"{SEEK}/job/{J_EXTERNAL}")
            c.check(wait_until(lambda: job_row(J_EXTERNAL)) is not None, "external ad: the detail page was captured")
            c.check(job_row(J_EXTERNAL)[3] == "external",
                    f"external ad: apply_type 'external' ({job_row(J_EXTERNAL) and job_row(J_EXTERNAL)[3]})")

            # --- Mark Applied by hand: 'manual', no screenshot, no download ---------------
            pg.goto(f"{SEEK}/job/{J_MANUAL}")
            wait_until(lambda: "Mark Applied" in shadow_buttons(pg, QA_HOST))
            pg.locator(f"#{QA_HOST}").locator("button", has_text="Mark Applied").click()
            m = wait_until(lambda: (match_row(J_MANUAL) or [None] * 6)[2] and match_row(J_MANUAL))
            c.check(m and m[1] == "applied" and m[3] == "manual", f"Mark Applied records 'manual' ({m and m[1:4]})")
            pg.wait_for_timeout(2000)
            c.check(match_row(J_MANUAL)[4] is None, "Mark Applied takes no screenshot any more")

            # --- The sent page: recorded 'auto' with a screenshot ---------------------------
            pg.goto(f"{SEEK}/job/{J_QUICK}/apply/success?sol=fixture")
            wait_until(lambda: (match_row(J_QUICK) or [None] * 6)[3] == "auto", timeout_s=25)
            wait_until(lambda: (match_row(J_QUICK) or [None] * 6)[4], timeout_s=15)
            m = match_row(J_QUICK)
            c.check(m and m[1] == "applied" and m[2] and m[3] == "auto",
                    f"sent page: recorded applied, via 'auto' ({m and m[1:4]})")
            c.check(m and float(m[0]) == 90, "sent page: the score is left alone")
            files = sorted(shots_dir.rglob("*.png"))
            c.check(m and m[4] and len(files) == 1 and files[0].name == Path(m[4]).name,
                    f"sent page: one screenshot file in the app's folder ({[f.name for f in files]})")
            rel = files[0].relative_to(shots_dir).as_posix() if files else ""
            c.check(re.fullmatch(r"evidence/\d{4}-\d{2}/\d{4}_\d{2}-\d{2}-\d{4}_Example-Co\.png", rel),
                    f"sent page: saved as evidence/<month>/<time>_<date>_<company>.png ({rel})")
            c.check(files and files[0].stat().st_size > 1000, "sent page: the file is a real image")
            c.check(wait_until(lambda: "Application recorded" in shadow_text(pg, SENT_HOST)),
                    "sent page: the confirmation panel is shown")
            c.check("Screenshot saved in the app." in shadow_text(pg, SENT_HOST),
                    f"sent page: the panel says the screenshot was saved ({shadow_text(pg, SENT_HOST)!r})")
            c.check(job_row(J_QUICK)[1] == "Graduate Engineer", "sent page: a known job keeps its title")
            first = match_row(J_QUICK)

            # Retake: the file is replaced, not added to.
            pg.wait_for_timeout(1200)
            pg.locator(f"#{SENT_HOST}").locator("button", has_text="Retake screenshot").click()
            c.check(wait_until(lambda: match_row(J_QUICK)[5] != first[5]), "Retake: a new screenshot was taken")
            c.check(len(list(shots_dir.rglob("*.png"))) == 1, "Retake: still one file (the old one is replaced)")

            # Reload: idempotent.
            before = match_row(J_QUICK)
            pg.reload()
            wait_until(lambda: "Application recorded" in shadow_text(pg, SENT_HOST))
            pg.wait_for_timeout(1500)
            after = match_row(J_QUICK)
            c.check(after[2] == before[2] and after[3] == "auto", "reload: the applied date and 'auto' are unchanged")
            c.check(q("SELECT COUNT(*) FROM matches m JOIN job_listings j ON j.id = m.job_id "
                      "WHERE j.source_job_id = ?", J_QUICK)[0][0] == 1, "reload: still one match")
            c.check(len(list(shots_dir.rglob("*.png"))) == 1, "reload: still one file")

            # --- A job never captured: stub + unscored applied match ----------------------
            pg.goto(f"{SEEK}/job/{J_NEW}/apply/success?sol=fixture")
            m = wait_until(lambda: (match_row(J_NEW) or [None] * 6)[4] and match_row(J_NEW), timeout_s=25)
            j = job_row(J_NEW)
            c.check(j is not None and j[1] == f"Seek job {J_NEW}" and j[2] == sent["employer"] and j[3] == "quick",
                    f"never-captured job: stub with a placeholder title, the employer from the page, 'quick' ({j and j[1:4]})")
            c.check(m and m[0] is None and m[1] == "applied" and m[3] == "auto",
                    f"never-captured job: an unscored applied match, via 'auto' ({m and m[:4]})")

            # --- Review -> sent without a page load (single-page app) ----------------------
            pg.goto(f"{SEEK}/job/{J_SPA}/apply/review?sol=fixture")
            pg.wait_for_timeout(2000)
            c.check(match_row(J_SPA) is None, "review page: nothing recorded")
            pg.evaluate("""([html]) => {
              history.pushState({}, '', location.pathname.replace('/review', '/success') + location.search);
              document.querySelector('main').innerHTML = html;
              document.title = 'Application sent | SEEK';
            }""", [sent["html"]])
            m = wait_until(lambda: (match_row(J_SPA) or [None] * 6)[4] and match_row(J_SPA), timeout_s=25)
            c.check(m and m[1] == "applied" and m[3] == "auto", f"review -> sent in place: recorded ({m and m[1:4]})")

            # --- Pages that must record nothing --------------------------------------------
            pg.goto(f"{SEEK}/job/{J_REVIEW}/apply/review?sol=fixture")
            pg.goto(f"{SEEK}/job/{J_BARE}/apply?sol=fixture")
            pg.wait_for_timeout(3000)
            c.check(match_row(J_REVIEW) is None and job_row(J_REVIEW) is None,
                    "the review page alone records nothing")
            c.check(match_row(J_BARE) is None and job_row(J_BARE) is None,
                    "the bare /apply URL (external ads go there too) records nothing")

            # --- Privacy and side effects ----------------------------------------------------
            dump = "\n".join(str(r) for t in ("job_listings", "matches", "screening_questions")
                             for r in q(f"SELECT * FROM {t}"))
            c.check(f"Keep it up, {NAME}" not in dump and f", {NAME}" not in dump,
                    "the greeting with the user's name is stored nowhere")
            c.check(q("SELECT COUNT(*) FROM job_listings WHERE source_job_id = '99999999'")[0][0] == 0,
                    "the 'You might also like' job is not captured")
            events = pg.evaluate("window.__events")
            c.check(events == [], f"no click/input/change/submit/keydown on the page ({events})")
            c.check(downloads == [], f"nothing saved to Downloads ({downloads})")
            c.check(unknown_seek == [], f"no Seek request beyond the fixture pages ({unknown_seek[:5]})")

            # --- Overview: collate the unreported screenshots into a Centrelink PDF --------
            # Quick (J_QUICK, J_NEW, J_SPA) have screenshots; Mark Applied (J_MANUAL) has none.
            # (The idle loop may quick-screen the captured ads on the stub model meanwhile;
            # what must not call a model is the evidence path itself, so wait for the loop
            # to go quiet, then count stub calls across collate and undo.)
            stub_log = work / "llm_stub.jsonl"

            def stub_calls() -> int:
                """Model calls other than the idle loop's own scoring of the captured ads."""
                if not stub_log.exists():
                    return 0
                tasks = [json.loads(line).get("task") for line in stub_log.read_text(encoding="utf-8").splitlines() if line.strip()]
                return sum(1 for t in tasks if t not in ("quickscreen", "extract", "match"))

            settled = stub_calls()
            for _ in range(10):
                pg.wait_for_timeout(1500)
                if stub_calls() == settled:
                    break
                settled = stub_calls()
            ext_id = sw.url.split("/")[2]
            side = ctx.new_page()
            side_downloads = []
            side.on("download", lambda d: side_downloads.append(d))
            side.add_init_script(TABS_STUB)
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")
            card = side.locator("#ov-evidence")
            c.check(wait_until(lambda: "not yet reported" in card.inner_text()),
                    "Overview: the evidence card shows the unreported screenshots")
            c.check(card.locator(".ev-count .big").inner_text() == "3",
                    f"Overview: 3 Quick Apply applications unreported, the hand-marked one left out ({card.inner_text()!r})")
            collate = card.locator("button.ev-collate")
            c.check("Collate 3 screenshots" in collate.inner_text(), f"the button names the count ({collate.inner_text()!r})")
            collate.click()
            wait_until(lambda: side_downloads, timeout_s=20)
            name = side_downloads[0].suggested_filename if side_downloads else ""
            c.check(re.fullmatch(r"\d{4}_\d{2}-\d{2}-\d{4}_3_jobs\.pdf", name),
                    f"Collate: one PDF downloaded, named (time)_(date)_3_jobs ({name})")
            pdf = Path(side_downloads[0].path()).read_bytes() if side_downloads else b""
            c.check(pdf.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf)) == 3,
                    "Collate: the PDF has 3 pages")
            c.check(0 < len(pdf) < 3_000_000, f"Collate: under Centrelink's 3 MB ({len(pdf)} bytes)")
            saved = shots_dir / "centrelink_ready" / name
            c.check(name and saved.is_file() and saved.read_bytes() == pdf,
                    "Collate: the same PDF is saved in the app's centrelink_ready folder")
            c.check(q("SELECT COUNT(*) FROM evidence_bundle_items")[0][0] == 3, "Collate: 3 applications marked reported")
            c.check(wait_until(lambda: "All Quick Apply applications reported" in card.inner_text()),
                    "Overview: the card now says everything is reported")
            c.check("enter 3 as the number of applications" in card.inner_text(),
                    "Overview: it says what to enter on Centrelink's form")
            c.check(len(list(shots_dir.rglob("evidence/*/*.png"))) == 3,
                    "the screenshots stay in their month folder")

            undo = card.locator(".ev-row .ev-undo").first  # its text changes to "Sure?"
            undo.click()
            c.check(wait_until(lambda: undo.inner_text() == "Sure?", timeout_s=3),
                    f"Undo asks for a second click ({undo.inner_text()!r})")
            undo.click()
            c.check(wait_until(lambda: "not yet reported" in card.inner_text() and
                               card.locator(".ev-count .big").inner_text() == "3"),
                    "Undo: the 3 applications are unreported again")
            c.check(not saved.exists(), "Undo: the PDF is deleted from the app folder")
            c.check(q("SELECT COUNT(*) FROM evidence_bundles")[0][0] == 0, "Undo: the bundle record is gone")
            c.check(side.evaluate("window.__opened") == [], "the side panel opened no tab")
            c.check(stub_calls() == settled, f"no LLM call while collating or undoing ({stub_calls() - settled})")
            ctx.close()
    finally:
        con.close()
        stop_server(server)

    c.check(blocked == [], f"nothing else left the machine ({blocked[:5]})")
    now_repo_shots = sorted(p.name for p in repo_shots.glob("*")) if repo_shots.exists() else []
    c.check(now_repo_shots == repo_shots_before, "app/screenshots was not touched")
    print(f"\n{len(c.results) - c.failed}/{len(c.results)} checks passed")
    if not c.failed and not keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"scratch folder kept for a look: {work}")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # panel text has ✓ / …
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    sys.exit(run(args.headed, args.keep))
