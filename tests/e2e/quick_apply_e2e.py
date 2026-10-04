"""End-to-end check of Phase 9a capture: unpacked extension -> backend -> bank -> overlay.

    python tests/e2e/quick_apply_e2e.py            # headless Chromium
    python tests/e2e/quick_apply_e2e.py --headed

Not collected by pytest (needs Chromium and a server). What it does:

1. Builds a scratch SQLite DB in a temp folder (alembic head, profile 1, and one job
   row for S2 so both the "job known" and the "job never captured" paths run) and
   starts ``scripts/run_api.py test --db <scratch>`` on port 8001 with
   LLM_PROVIDER=stub, so any LLM call would fail instead of costing money.
2. Launches Chromium with the unpacked extension, switched to the TEST backend.
3. Serves Seek apply pages built from tests/fixtures/quick_apply_samples.json (the
   5 sampled questionnaires, scrubbed) through request interception. Every other
   request to Seek is answered with a 404 from the harness and anything else
   off-machine is aborted: no request reaches Seek or the internet.
4. Checks, for S1..S5: the overlay lists every question with the kind recorded in
   docs/quick-apply-samples.md; the bank holds one row per distinct question (S3/S4
   repeat S2's library questions); a repeat visit adds nothing; a single-page step
   change (documents -> questions -> profile) captures on the questions step and
   drops the panel after it; nothing under [data-adora-mask] and no pre-filled
   answer reaches the backend; the extension causes no click/input/change/submit
   event and no navigation; no llm_usage row is written.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import html
import json
import os
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
FIXTURE = ROOT / "tests" / "fixtures" / "quick_apply_samples.json"
PORT = 8001
BACKEND = f"http://127.0.0.1:{PORT}"
SEEK_HOSTS = {"www.seek.com.au", "au.seek.com", "seek.com.au"}
PREFILL = "PREFILLED-ANSWER-MUST-NOT-LEAK"
MASKED = "MASKED-NAME-MUST-NOT-LEAK"
APPLY_STEPS = ["Choose documents", "Answer employer questions", "Update SEEK Profile", "Review and submit"]

KIND_OF_LABEL = {"Yours to answer": "user", "We’ll help": "assisted", "New": "unknown"}


# ---------------------------------------------------------------------------
# Fixture pages (markup per docs/quick-apply-samples.md "Markup patterns")
# ---------------------------------------------------------------------------
def _e(s: str) -> str:
    return html.escape(s, quote=True)


def question_html(q: dict, n: int) -> str:
    qid, name, text = q["seek_question_id"], q["field_name"], q["text"]
    t = q["input_type"]
    if t == "single":
        radios = "".join(
            f'<div class="_1ppah1f0"><input type="radio" id="{_e(qid)}-{i}" name="{_e(name)}" '
            f'value="{_e(o["value"])}"{" checked" if i == 0 else ""}>'
            f'<label for="{_e(qid)}-{i}"><span>{_e(o["label"])}</span></label></div>'
            for i, o in enumerate(q["options"])
        )
        return (f'<fieldset role="radiogroup" id="question-{_e(qid)}" class="_1bnu76l0">'
                f'<legend><span><strong>{_e(text)}</strong></span></legend>{radios}</fieldset>')
    if t == "dropdown":
        placeholder = '<option value="" disabled selected></option>' if q.get("placeholder") else ""
        opts = "".join(
            f'<option value="{_e(o["value"])}"{" selected" if i == 1 and not placeholder else ""}>'
            f'{_e(o["label"])}</option>' for i, o in enumerate(q["options"])
        )
        return (f'<div class="_x1"><label for="question-{_e(qid)}"><span>{_e(text)}</span></label>'
                f'<select id="question-{_e(qid)}" name="{_e(name)}">{placeholder}{opts}</select></div>')
    if t == "text":
        return (f'<div class="_x2"><label for="question-{_e(qid)}">{_e(text)}</label>'
                f'<textarea id="question-{_e(qid)}" name="{_e(name)}">{PREFILL} {n}</textarea></div>')
    if t == "multi":
        boxes = "".join(
            f'<div><input type="checkbox" name="{_e(name)}" id="{_e(o["value"])}"{" checked" if i < 2 else ""}>'
            f'<label for="{_e(o["value"])}">{_e(o["label"])}</label></div>'
            for i, o in enumerate(q["options"])
        )
        return f'<div class="_x3"><span><strong>{_e(text)}</strong></span><div>{boxes}</div></div>'
    raise ValueError(t)


# Records, in the PAGE's world, every user-ish event and navigation. The extension's
# content script runs in an isolated world, but events it caused would land here.
RECORDER = """
<script>
  window.__events = [];
  for (const t of ['click', 'input', 'change', 'submit', 'keydown']) {
    document.addEventListener(t, (e) => window.__events.push(t + ':' + (e.target.id || e.target.tagName)), true);
  }
  window.__startHref = location.href;
  window.__startHistory = history.length;
</script>
"""


def step_html(sample: dict, step: str) -> str:
    current = ' aria-current="step"'
    nav = "".join(
        f'<button type="button"{current if s == step else ""}>{_e(s)}</button>' for s in APPLY_STEPS
    )
    if step == "Answer employer questions":
        questions = "".join(question_html(q, i) for i, q in enumerate(sample["questions"]))
        # A field under Seek's personal-data mask: must never be read.
        questions += (f'<div data-adora-mask="true"><label for="question-MASKED">{MASKED}</label>'
                      f'<textarea id="question-MASKED" name="questionnaire.MASKED">{MASKED}</textarea></div>')
        main = (f'<form>{questions}<button type="button" data-testid="back-button">Back</button>'
                f'<button type="button" data-testid="continue-button">Continue</button></form>')
    else:
        main = f'<div><p>{_e(step)} (fixture step with no questions)</p></div>'
    return (
        f'<header><span data-adora-mask="true">{MASKED}</span></header>'
        f'<div data-automation="job-header"><span>Applying for</span><h1>{_e(sample["title"])}</h1>'
        f'<span>{_e(sample["company"])}</span><nav aria-label="Progress bar">{nav}</nav></div>'
        f'<main id="apply-main">{main}</main>'
    )


def page_html(sample: dict, step: str) -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>Apply | SEEK</title>{RECORDER}'
            f'</head><body>{step_html(sample, step)}</body></html>')


def apply_url(sample: dict) -> str:
    return f'https://www.seek.com.au/job/{sample["job_id"]}/apply/role-requirements?sol=fixture'


# ---------------------------------------------------------------------------
# Scratch backend
# ---------------------------------------------------------------------------
def make_scratch_db(folder: Path, s2: dict) -> Path:
    db = folder / "scratch_e2e.db"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + db.as_posix(), "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name, email, password_hash) VALUES (1, 'E2E Test', "
                "'e2e@example.invalid', 'x')")
    # S2's job is known (no description, so the idle loop has nothing to process);
    # S1, S3-S5 are not, so the stub-ingest path runs for them.
    con.execute("INSERT INTO job_listings (source, source_job_id, url, title, company) VALUES "
                "('seek', ?, ?, ?, ?)", (s2["job_id"], f'https://www.seek.com.au/job/{s2["job_id"]}',
                                         s2["title"], s2["company"]))
    con.commit()
    con.close()
    return db


def port_free() -> bool:
    try:
        urllib.request.urlopen(f"{BACKEND}/health", timeout=1)
        return False
    except Exception:
        return True


def start_server(db: Path, log_path: Path) -> subprocess.Popen:
    env = {**os.environ, "LLM_PROVIDER": "stub", "PYTHONUNBUFFERED": "1"}
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


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, what: str) -> None:
        self.results.append((bool(ok), what))
        print(("PASS " if ok else "FAIL ") + what)

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.results if not ok)


def overlay_items(page) -> list[dict] | None:
    return page.evaluate("""() => {
      const host = document.getElementById('seek-assistant-questions-host');
      if (!host || !host.shadowRoot) return null;
      return [...host.shadowRoot.querySelectorAll('li')].map((li) => ({
        kind: li.dataset.kind, text: li.querySelector('.q').textContent,
        badge: li.querySelector('.badge').textContent }));
    }""")


def wait_overlay(page, count: int, timeout_s: float = 15) -> list[dict] | None:
    end = time.time() + timeout_s
    while time.time() < end:
        items = overlay_items(page)
        if items is not None and len(items) == count:
            return items
        page.wait_for_timeout(250)
    return overlay_items(page)


def page_side_effects(page) -> dict:
    return page.evaluate("""() => ({
      events: window.__events, sameHref: location.href === window.__startHref,
      sameHistory: history.length === window.__startHistory,
      textareas: [...document.querySelectorAll('textarea')].map((t) => t.value),
      checked: [...document.querySelectorAll('input:checked')].map((i) => i.id),
    })""")


def run(headed: bool) -> int:
    from playwright.sync_api import sync_playwright

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    samples = {s["id"]: s for s in data["samples"]}
    if not port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")

    work = Path(tempfile.mkdtemp(prefix="qa_e2e_"))
    db = make_scratch_db(work, samples["S2"])
    server = start_server(db, work / "server.log")
    c = Checks()
    blocked: list[str] = []
    seek_served: list[str] = []
    pages: dict[str, str] = {}  # path -> html served for it

    def route(r):
        url = urlparse(r.request.url)
        if url.hostname in ("127.0.0.1", "localhost") and url.port == PORT:
            return r.continue_()
        if url.hostname in SEEK_HOSTS:
            seek_served.append(r.request.url)
            body = pages.get(url.path)
            if body is not None:
                return r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
            return r.fulfill(status=404, body="not a fixture")
        blocked.append(r.request.url)
        return r.abort()

    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(work / "profile"), channel="chromium", headless=not headed,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )
            ctx.route("**/*", route)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
            page = ctx.new_page()
            page.on("console", lambda m: m.text.startswith("[SeekAssistant]") and print("   console:", m.text))

            # --- S1..S5: load the questions step directly --------------------------
            for sid in ("S1", "S2", "S3", "S4", "S5"):
                s = samples[sid]
                path = urlparse(apply_url(s)).path
                pages[path] = page_html(s, "Answer employer questions")
                page.goto(apply_url(s))
                items = wait_overlay(page, len(s["questions"]))
                c.check(items is not None and len(items) == len(s["questions"]),
                        f"{sid}: overlay lists {len(s['questions'])} questions")
                if items:
                    for q, item in zip(s["questions"], items):
                        want = q["expected"]["kind"]
                        c.check(item["kind"] == want and item["text"] == " ".join(q["text"].split()),
                                f"{sid}: '{q['text'][:48]}' shown as {want} ({item['badge']})")
                fx = page_side_effects(page)
                c.check(fx["events"] == [], f"{sid}: no click/input/change/submit/keydown events ({fx['events']})")
                c.check(fx["sameHref"] and fx["sameHistory"], f"{sid}: no navigation")
                c.check(all(PREFILL in t or MASKED in t for t in fx["textareas"]),
                        f"{sid}: pre-filled answers untouched")

            con = sqlite3.connect(db)
            distinct = {q["seek_question_id"].split("_V_")[0] if q["seek_question_id"].startswith("AU_Q_")
                        and q["seek_question_id"].split("_")[2].isdigit() else q["text"]
                        for s in samples.values() for q in s["questions"]}
            bank = con.execute("SELECT COUNT(*) FROM screening_questions").fetchone()[0]
            c.check(bank == len(distinct), f"bank holds {bank} rows = {len(distinct)} distinct questions (22 captured)")
            seen = dict(con.execute("SELECT library_id, times_seen FROM screening_questions "
                                    "WHERE library_id IS NOT NULL").fetchall())
            c.check(seen.get("AU_Q_6") == 3 and seen.get("AU_Q_13") == 2 and seen.get("AU_Q_8") == 2,
                    f"library questions counted per job, not duplicated ({seen})")
            links = con.execute("SELECT COUNT(*) FROM job_screening_questions").fetchone()[0]
            c.check(links == 22, f"22 job links ({links})")
            jobs = con.execute("SELECT COUNT(*) FROM job_listings").fetchone()[0]
            c.check(jobs == 5, f"4 unknown jobs created as stubs, S2 reused ({jobs} job rows)")
            dump = "\n".join(str(r) for t in ("screening_questions", "job_screening_questions", "job_listings")
                             for r in con.execute(f"SELECT * FROM {t}").fetchall())
            c.check(PREFILL not in dump and MASKED not in dump,
                    "no pre-filled answer and nothing under [data-adora-mask] stored")
            unknown = con.execute("SELECT text FROM screening_questions WHERE kind='unknown'").fetchall()
            c.check(len(unknown) == 1 and "React Native" in unknown[0][0],
                    f"only S5's React Native question is unsorted ({len(unknown)})")

            # --- repeat visit: recognised, nothing new -----------------------------
            before = con.execute("SELECT SUM(times_seen), COUNT(*) FROM screening_questions").fetchone()
            page.goto(apply_url(samples["S2"]))
            wait_overlay(page, len(samples["S2"]["questions"]))
            page.wait_for_timeout(1500)
            after = con.execute("SELECT SUM(times_seen), COUNT(*) FROM screening_questions").fetchone()
            c.check(before == after, f"repeat S2 visit adds no rows and no sightings ({before} -> {after})")

            # --- single-page step changes (no page load) ---------------------------
            s = samples["S3"]
            pages[urlparse(apply_url(s)).path] = page_html(s, "Choose documents")
            page.goto(apply_url(s))
            page.wait_for_timeout(2000)
            c.check(overlay_items(page) is None, "documents step: no overlay")
            page.evaluate("(h) => { document.body.innerHTML = h; }", step_html(s, "Answer employer questions"))
            items = wait_overlay(page, len(s["questions"]))
            c.check(items is not None and len(items) == len(s["questions"]),
                    "questions step rendered without a page load: captured and listed")
            page.evaluate("(h) => { document.body.innerHTML = h; }", step_html(s, "Update SEEK Profile"))
            page.wait_for_timeout(2000)
            c.check(overlay_items(page) is None, "next step without a page load: overlay removed")
            fx = page_side_effects(page)
            c.check(fx["events"] == [] and fx["sameHref"] and fx["sameHistory"],
                    "step changes: no events or navigation caused by the extension")

            usage = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            c.check(usage == 0, f"no LLM call recorded ({usage} llm_usage rows)")
            con.close()
            ctx.close()

        c.check(not blocked, f"nothing off-machine requested ({blocked[:5]})")
        c.check(all(urlparse(u).hostname in SEEK_HOSTS for u in seek_served),
                f"{len(seek_served)} Seek requests, all answered by the harness, none reached Seek")
    finally:
        stop_server(server)
        print(f"server log: {work / 'server.log'}")

    print(f"\n{len(c.results) - c.failed}/{len(c.results)} checks passed")
    if not c.failed:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if c.failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    sys.exit(run(ap.parse_args().headed))
