"""End-to-end check of Rewrite letter + letter date (docs/letter-rewrite-plan.md).

    python tests/e2e/letter_rewrite_e2e.py            # headless Chromium
    python tests/e2e/letter_rewrite_e2e.py --headed
    python tests/e2e/letter_rewrite_e2e.py --keep     # keep the temp folder (DB, logs)

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB (alembic head): profile 1 with a skill changed on 10 Oct, the
   letter loop OFF (so a rewrite is the one-shot) and availability checks off; three jobs with
   a letter written on 1 Oct (so "before profile update"): A (your edits, in Keep Applying),
   C (no edits, its ad page is opened) and D (applied); and E, written on 15 Oct (up to date).
2. Starts ``scripts/run_api.py test --db <scratch>`` on port 8001 with LLM_PROVIDER=stub and a
   canned ``cover_letter`` text: a rewrite really runs on the single worker, writes nothing
   to any provider and costs nothing (extract / match have no canned answer and just fail).
3. Serves the ads as fixture pages through request interception; any other Seek request is
   a 404 from the harness and anything else off-machine is aborted. The side panel's
   ``chrome.tabs.create`` is recorded, never done.
4. Checks: only an outdated letter offers Rewrite (E's card shows its date and no button, and
   the button goes once a rewrite lands, on the card and on the panel); the card's "Written 1
   Oct, before profile update" and two-click Rewrite (the first
   click names the cost and that it replaces your edits, sends nothing, then disarms; the
   second queues one POST with discard_edits); the new letter lands, your edits are replaced
   (D1), ``generated_at`` moves and the card comes back "Written <today>" with no "Writing…";
   the backend refuses an applied job (409); on the ad page the panel shows the date, a Rewrite
   button, and after two clicks puts the new letter in its box; an applied job's panel has no
   Rewrite; the extension makes no click/input/submit on Seek's page.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
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
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"
PORT = 8001
BACKEND = f"http://127.0.0.1:{PORT}"
SEEK = "https://www.seek.com.au"
SEEK_HOSTS = {"www.seek.com.au", "au.seek.com", "seek.com.au"}
QA_HOST = "seek-assistant-quick-apply-host"

J_A, J_C, J_D, J_E = "90000011", "90000013", "90000014", "90000015"
OLD = "2026-10-01 00:00:00"          # when the seeded letters were written
PROFILE_CHANGED = "2026-10-10 00:00:00"
FRESH = "2026-10-15 00:00:00"        # job E's letter: after the profile change, so no Rewrite
EDITED = "Dear Hiring Manager, MY EDITS to letter A. Sincerely, E2E Test"
REWRITTEN = "Dear Hiring Manager, this is the REWRITTEN letter. Sincerely, E2E Test"

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
      if (e.composedPath().some((n) => n && n.id && n.id.startsWith('seek-assistant-'))) return;
      window.__events.push(t + ':' + (e.target.id || e.target.tagName));
    }, true);
  }
</script>
"""


def detail_page(job_id: str, title: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{title} Job in Brisbane - SEEK</title>'
            f'{RECORDER}</head><body><h1 data-automation="job-detail-title">{title}</h1>'
            f'<div data-automation="jobAdDetails"><p>About the role: {title} for job {job_id}. '
            'Build and support systems.</p></div></body></html>')


def make_scratch_db(folder: Path) -> Path:
    db = folder / "scratch_e2e.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + db.as_posix(), "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    con = sqlite3.connect(db)
    prefs = {"availability_checks_enabled": False, "letter_loop_enabled": False,
             "auto_cover_letter_min_score": 100}
    con.execute("INSERT INTO profiles (id, name, email, password_hash, preferences) VALUES (1, 'E2E Test', "
                "'e2e@example.invalid', 'x', ?)", (json.dumps(prefs),))
    con.execute("INSERT INTO skills (user_id, name, created_at, updated_at) VALUES (1, 'SQL', ?, ?)",
                (PROFILE_CHANGED, PROFILE_CHANGED))
    for sid, title, edited, applied, written in ((J_A, "Graduate Engineer", EDITED, None, OLD),
                                                 (J_C, "Support Analyst", None, None, OLD),
                                                 (J_D, "Data Analyst", None, "2026-10-12 00:00:00", OLD),
                                                 (J_E, "Platform Engineer", None, None, FRESH)):
        cur = con.execute("INSERT INTO job_listings (source, source_job_id, url, title, company, raw_description) "
                          "VALUES ('seek', ?, ?, ?, 'Example Co', ?)",
                          (sid, f"{SEEK}/job/{sid}", title, f"About the role: {title}."))
        match = con.execute("INSERT INTO matches (user_id, job_id, score, status, applied_at) VALUES (1, ?, 90, ?, ?)",
                            (cur.lastrowid, "applied" if applied else "new", applied)).lastrowid
        con.execute("INSERT INTO cover_letters (match_id, generated_content, edited_content, status, created_at, "
                    "updated_at, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (match, f"Dear Hiring Manager, the OLD letter for {title}. Sincerely, E2E Test", edited,
                     "edited" if edited else "draft", written, written, written))
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
    stub_responses.write_text(json.dumps({"cover_letter": REWRITTEN}), encoding="utf-8")
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


# Never time.sleep while the browser runs: the request router is a Python callback, so a
# blocked thread stalls every request. Waits use this page's timer.
PAGE = None


def wait_until(fn, timeout_s: float = 20, step_s: float = 0.25):
    end = time.time() + timeout_s
    value = fn()
    while not value and time.time() < end:
        PAGE.wait_for_timeout(int(step_s * 1000))
        value = fn()
    return value


def shadow_eval(pg, js: str):
    return pg.evaluate(f"""(id) => {{
      const host = document.getElementById(id);
      const root = host && host.shadowRoot;
      if (!root) return null;
      return ({js})(root);
    }}""", QA_HOST)


def run(headed: bool, keep: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    if not port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")
    work = Path(tempfile.mkdtemp(prefix="rewrite_e2e_"))
    db = make_scratch_db(work)
    server = start_server(db, work)
    c = Checks()
    blocked: list[str] = []
    unknown_seek: list[str] = []
    pages = {f"/job/{J_C}": detail_page(J_C, "Support Analyst"), f"/job/{J_D}": detail_page(J_D, "Data Analyst")}

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

    def letter(sid: str):
        con.commit()  # end any read snapshot so the server's writes show
        return con.execute(
            "SELECT cl.generated_content, cl.edited_content, cl.generated_at FROM cover_letters cl "
            "JOIN matches m ON m.id = cl.match_id JOIN job_listings j ON j.id = m.job_id "
            "WHERE j.source_job_id = ?", (sid,)).fetchone()

    def job_id(sid: str) -> int:
        return con.execute("SELECT id FROM job_listings WHERE source_job_id = ?", (sid,)).fetchone()[0]

    today = date.today()
    today_label = f"Written {today.day} {today.strftime('%b')}"

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(work / "profile"), channel="chromium", headless=not headed,
                viewport={"width": 380, "height": 900},
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )

            def intercepted(u: str) -> bool:
                url = urlparse(u)
                if url.scheme in ("chrome-extension", "blob", "data"):
                    return False
                return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

            ctx.route(intercepted, route)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
            ext_id = sw.url.split("/")[2]
            for extra in ctx.pages:
                extra.close()
            side = ctx.new_page()
            global PAGE
            PAGE = side
            errors: list[str] = []
            side.on("pageerror", lambda e: errors.append(str(e)))
            side.add_init_script(TABS_STUB)
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")

            # --- Keep Applying card -------------------------------------------------------
            print("Keep Applying card")
            card = side.locator("#ov-scroller .mini-card", has_text="Graduate Engineer")
            card.wait_for(timeout=15000)
            c.check(side.locator("#ov-scroller .mini-card", has_text="Data Analyst").count() == 0,
                    "the applied job is not in Keep Applying")
            written = card.locator(".mc-written")
            c.check(written.locator(".mc-written-when").inner_text() == "Written 1 Oct, before profile update",
                    f"card: 'Written 1 Oct, before profile update' ({written.inner_text()!r})")
            c.check("outdated" in (written.get_attribute("class") or "").split(), "card: marked outdated (amber)")
            btn = card.locator(".mc-rewrite")
            c.check(btn.inner_text() == "⟳ Rewrite", f"card: a Rewrite button ({btn.inner_text()!r})")
            fresh = side.locator("#ov-scroller .mini-card", has_text="Platform Engineer")
            c.check(fresh.locator(".mc-written").inner_text() == "Written 15 Oct",
                    f"fresh card: 'Written 15 Oct', not outdated ({fresh.locator('.mc-written').inner_text()!r})")
            c.check(fresh.locator(".mc-rewrite").count() == 0, "fresh card: no Rewrite (written after the profile change)")

            posts: list[str] = []
            side.on("request", lambda r: "/regenerate" in r.url and r.method == "POST" and posts.append(r.url))
            btn.click()
            label = btn.inner_text()
            c.check("Replaces your edits" in label and "10¢" in label,
                    f"card: the first click names the edits and the cost ({label!r})")
            c.check(not posts, "card: one click sends nothing")
            c.check(wait_until(lambda: btn.inner_text() == "⟳ Rewrite", timeout_s=8), "card: left alone, it disarms")
            c.check(letter(J_A)[0].startswith("Dear Hiring Manager, the OLD"), "card: ...and the letter is untouched")

            with side.expect_response(lambda r: "/regenerate" in r.url) as resp:
                btn.click()
                btn.click()
            c.check(resp.value.ok and resp.value.json() == {"status": "queued"}, "card: the second click queues it")
            c.check(len(posts) == 1 and f"/jobs/{job_id(J_A)}/regenerate?profile_id=1&discard_edits=true" in posts[0],
                    f"card: one POST, with discard_edits ({posts})")
            landed = wait_until(lambda: letter(J_A)[0] == REWRITTEN, timeout_s=40)
            c.check(landed, f"card: the new letter landed ({letter(J_A)[0][:40]!r})")
            row = letter(J_A)
            c.check(row[1] is None, "card: your edits were replaced (D1)")
            c.check(row[2] and row[2] > OLD, f"card: generated_at moved ({row[2]})")
            new_card = side.locator("#ov-scroller .mini-card", has_text="Graduate Engineer")
            c.check(wait_until(lambda: new_card.locator(".mc-written-when").count() == 1 and
                               new_card.locator(".mc-written-when").inner_text() == today_label, timeout_s=25),
                    f"card: back as '{today_label}', not outdated")
            c.check(new_card.locator(".mc-writing").count() == 0, "card: no 'Writing…' left behind")
            c.check(new_card.locator(".mc-rewrite").count() == 0, "card: no Rewrite once the letter is up to date")

            req = urllib.request.Request(f"{BACKEND}/jobs/{job_id(J_D)}/regenerate?profile_id=1", method="POST")
            try:
                urllib.request.urlopen(req, timeout=5)
                status = 200
            except urllib.error.HTTPError as e:
                status = e.code
            c.check(status == 409, f"backend: an applied job is refused ({status})")

            # --- The Quick Apply panel on the ad -----------------------------------------
            print("Quick Apply panel")
            pg = ctx.new_page()
            PAGE = pg
            pg.goto(f"{SEEK}/job/{J_C}")
            buttons = wait_until(lambda: shadow_eval(pg, "r => [...r.querySelectorAll('button')].map(b => b.textContent.trim())"))
            c.check(buttons == ["Copy", "Save edits", "⟳ Rewrite", "Mark Applied"],
                    f"panel: Copy / Save edits / Rewrite / Mark Applied ({buttons})")
            c.check(shadow_eval(pg, "r => r.querySelector('.written').textContent") ==
                    "Written 1 Oct, before your profile update", "panel: the written date, before your profile update")
            rewrite = pg.locator(f"#{QA_HOST}").locator("button", has_text="Rewrite")
            rewrite.click()
            c.check(rewrite.inner_text().startswith("Rewrite?") and "10¢" in rewrite.inner_text(),
                    f"panel: the first click names the cost ({rewrite.inner_text()!r})")
            rewrite.click()
            c.check(wait_until(lambda: "Writing a new letter" in (shadow_eval(pg, "r => r.querySelector('.rewrite-note').textContent") or "")),
                    "panel: says it is writing")
            c.check(wait_until(lambda: shadow_eval(pg, "r => r.querySelector('textarea').value") == REWRITTEN, timeout_s=50),
                    "panel: the new letter is put in the box")
            c.check(shadow_eval(pg, "r => r.querySelector('.rewrite-note').textContent") == "New letter ✓",
                    "panel: 'New letter ✓'")
            c.check(shadow_eval(pg, "r => r.querySelector('.written').textContent") == today_label,
                    f"panel: the date updates to '{today_label}'")
            c.check(letter(J_C)[0] == REWRITTEN, "panel: the DB has the new letter")
            visible = shadow_eval(pg, "r => [...r.querySelectorAll('button')].filter(b => !b.hidden).map(b => b.textContent.trim())")
            c.check(visible == ["Copy", "Save edits", "Mark Applied"], f"panel: Rewrite goes once it is up to date ({visible})")
            c.check(pg.evaluate("window.__events") == [], "panel: no click/input/submit on Seek's page")

            pg.goto(f"{SEEK}/job/{J_D}")
            buttons = wait_until(lambda: shadow_eval(pg, "r => [...r.querySelectorAll('button')].map(b => b.textContent.trim())"))
            c.check(buttons == ["Copy", "Save edits", "✓ Applied"], f"panel: an applied job has no Rewrite ({buttons})")

            c.check(not errors, f"no side-panel page errors {errors[:3]}")
            c.check(side.evaluate("window.__opened") == [], "the side panel opened no tab")
            ctx.close()
    finally:
        con.close()
        stop_server(server)

    stub_log = work / "llm_stub.jsonl"
    tasks = [json.loads(line)["task"] for line in stub_log.read_text(encoding="utf-8").splitlines()
             if line.strip()] if stub_log.exists() else []
    c.check(set(tasks) <= {"quickscreen", "extract", "match", "cover_letter"} and tasks.count("cover_letter") == 2,
            f"model calls: two one-shot letters, nothing else but scoring ({sorted(set(tasks))}, "
            f"{tasks.count('cover_letter')} letters)")
    c.check(unknown_seek == [], f"no Seek request beyond the fixture pages ({unknown_seek[:5]})")
    c.check(blocked == [], f"nothing else left the machine ({blocked[:5]})")
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
