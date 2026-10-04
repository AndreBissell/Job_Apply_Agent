"""End-to-end check of Phase 9a capture and 9b help: unpacked extension -> backend -> bank -> overlay.

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

Phase 9b (question help) adds, on the same scratch DB: a known profile (a dated Full Stack
Developer job with JavaScript/React/C#, a university project with Python, Java listed
only) and a full-pipeline letter run for S2 and S5, a one-shot letter for S4, no letter for
S1/S3. LLM_PROVIDER=stub answers the one model call the flow can make (sorting S5's
unknown question, task "screening_classify") from a canned file and logs every call, so
the checks can say exactly what reached "the model". It checks what the overlay shows per
gating state (never a recommended or ticked answer), the gap No and Yes flows through our
own shadow-DOM UI (the Yes flow must leave the letter tables untouched), and that the side
panel's job card shows the same help.

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

HOST_ID = "seek-assistant-questions-host"
STUB_CLASSIFY = {"kind": "assisted", "strategy": "years_skill_text", "role": "", "skill": "React Native with Expo"}
LETTER_S2 = "Dear Hiring Manager, I build full stack web applications with React and C#. Sincerely, E2E Test"
LETTER_S4 = "Dear Hiring Manager, I enjoy building React front ends. Sincerely, E2E Test"
LETTER_S5 = "Dear Hiring Manager, I build mobile and web applications. Sincerely, E2E Test"

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
    document.addEventListener(t, (e) => {
      // Our own overlay lives in a shadow root on this page, and the harness clicks its
      // buttons: those events bubble (composed) up to document, and they are ours, not
      // events on Seek's elements. Skip any whose composed path runs through the overlay
      // host. Everything else, an event on a Seek element, is still recorded.
      if (e.composedPath().some((n) => n && n.id === 'seek-assistant-questions-host')) return;
      window.__events.push(t + ':' + (e.target.id || e.target.tagName));
    }, true);
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
def make_scratch_db(folder: Path, samples: dict) -> Path:
    db = folder / "scratch_e2e.db"
    url = "sqlite:///" + db.as_posix()
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name, email, password_hash) VALUES (1, 'E2E Test', "
                "'e2e@example.invalid', 'x')")
    con.commit()
    con.close()
    seed_profile_and_jobs(url, samples)
    return db


def seed_profile_and_jobs(url: str, samples: dict) -> None:
    """The known profile/ad pairs. Uses the app's models against the SCRATCH url only
    (DATABASE_URL is pointed at it before app.db is imported)."""
    import datetime as dt

    os.environ["DATABASE_URL"], os.environ["APP_ENV"] = url, "test"
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.llm.letter.state import JobInfo, LetterState, Requirement
    from app.models import CoverLetter, Experience, JobListing, LetterRun, Match, Skill

    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _fk(c, _):
        c.execute("PRAGMA foreign_keys=ON")

    db = sessionmaker(bind=engine, expire_on_commit=False)()
    js, react, cs, py, java = (Skill(user_id=1, name=n) for n in ("JavaScript", "React", "C#", "Python", "Java"))
    db.add_all([js, react, cs, py, java])
    db.flush()
    # One dated role only, so the years bracket never depends on today's date:
    # Jan 2023 - Jun 2024 is 17 months -> "1 year" (rounded down) in the years question.
    e1 = Experience(user_id=1, experience_type="job", title="Full Stack Developer", organization="Acme",
                    start_date=dt.date(2023, 1, 1), end_date=dt.date(2024, 6, 1),
                    description="Built React front ends. Wrote APIs in C# and .NET.")
    e3 = Experience(user_id=1, experience_type="university_project", title="Capstone",
                    description="Python data pipeline.")
    db.add_all([e1, e3])
    db.flush()
    e1.skills += [js, react, cs]
    e3.skills += [py]  # Java stays a listed skill only

    def job_and_match(sid: str):
        s = samples[sid]
        j = JobListing(source="seek", source_job_id=s["job_id"], url=f'https://www.seek.com.au/job/{s["job_id"]}',
                       title=s["title"], company=s["company"])
        db.add(j)
        db.flush()
        m = Match(user_id=1, job_id=j.id, score=90)
        db.add(m)
        db.flush()
        return j, m

    # S2: full pipeline (workflow run, done) whose final draft is the stored letter.
    j2, m2 = job_and_match("S2")
    state = LetterState(profile_id=1, job=JobInfo(job_id=j2.id, title=samples["S2"]["title"]), requirements=[
        Requirement(id="R1", text="3+ years' experience as a full stack developer", importance="essential",
                    letter_role="headline", status="supported", evidence=[f"experience:{e1.id}"]),
        Requirement(id="R2", text="Strong C# and .NET", importance="essential", letter_role="headline",
                    skill="C#", status="supported", evidence=[f"experience:{e1.id}"]),
        Requirement(id="R3", text="JavaScript and TypeScript", importance="important", letter_role="mention",
                    skill="JavaScript", status="supported", evidence=[f"skill:{js.id}"]),
        Requirement(id="R4", text="Experience with Objective-C", importance="nice_to_have",
                    letter_role="mention", skill="Objective-C", status="gap"),
        Requirement(id="R6", text="Australian work rights", importance="essential",
                    letter_role="not_for_letter", status="unknown"),
    ])
    state.add_draft(LETTER_S2)
    db.add(LetterRun(match_id=m2.id, engine="workflow", status="done", state=state.model_dump_json(),
                     final_draft_version=1))
    db.add(CoverLetter(match_id=m2.id, generated_content=LETTER_S2, status="draft"))

    # S4: a one-shot letter (a cover letter, no run).
    _, m4 = job_and_match("S4")
    db.add(CoverLetter(match_id=m4.id, generated_content=LETTER_S4, status="draft"))

    # S5: full pipeline too, so its unknown question goes to the (stub) model.
    j5, m5 = job_and_match("S5")
    state5 = LetterState(profile_id=1, job=JobInfo(job_id=j5.id, title=samples["S5"]["title"]), requirements=[
        Requirement(id="R1", text="React Native with Expo", importance="essential", letter_role="headline",
                    skill="React Native", status="gap"),
        Requirement(id="R2", text="Australian work rights", importance="essential",
                    letter_role="not_for_letter", status="unknown"),
    ])
    state5.add_draft(LETTER_S5)
    db.add(LetterRun(match_id=m5.id, engine="workflow", status="done", state=state5.model_dump_json(),
                     final_draft_version=1))
    db.add(CoverLetter(match_id=m5.id, generated_content=LETTER_S5, status="draft"))
    # S1 and S3 are left out on purpose: the extension creates them as stubs (no letter).
    db.commit()
    db.close()
    engine.dispose()


def port_free() -> bool:
    try:
        urllib.request.urlopen(f"{BACKEND}/health", timeout=1)
        return False
    except Exception:
        return True


def start_server(db: Path, log_path: Path, stub_responses: Path, stub_log: Path) -> subprocess.Popen:
    # run_api.py hands its environment to uvicorn's reload subprocess, so these reach the app.
    env = {**os.environ, "LLM_PROVIDER": "stub", "PYTHONUNBUFFERED": "1",
           "LLM_STUB_RESPONSES": str(stub_responses), "LLM_STUB_LOG": str(stub_log)}
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
    """The overlay's questions: the 9b view (.sa-q) once it has rendered, else the 9a list."""
    return page.evaluate("""() => {
      const host = document.getElementById('seek-assistant-questions-host');
      if (!host || !host.shadowRoot) return null;
      const root = host.shadowRoot;
      const assist = [...root.querySelectorAll('.sa-q')];
      if (assist.length) return assist.map((q) => ({
        kind: q.dataset.kind, text: q.querySelector('.sa-qtext').textContent,
        badge: q.querySelector('.sa-badge').textContent, assist: true }));
      return [...root.querySelectorAll('li')].map((li) => ({
        kind: li.dataset.kind, text: li.querySelector('.q').textContent,
        badge: li.querySelector('.badge').textContent, assist: false }));
    }""")


def panel_ready(page) -> bool:
    return bool(page.evaluate("""() => {
      const host = document.getElementById('seek-assistant-questions-host');
      return !!(host && host.shadowRoot && host.shadowRoot.querySelector('.sa-root'));
    }"""))


def wait_overlay(page, count: int, timeout_s: float = 20) -> list[dict] | None:
    """Wait for the help view (the panel first paints the plain list, then upgrades)."""
    end = time.time() + timeout_s
    while time.time() < end:
        items = overlay_items(page)
        if items is not None and len(items) == count and panel_ready(page):
            return items
        page.wait_for_timeout(250)
    return overlay_items(page)


def panel_text(page) -> str:
    return page.evaluate("""() => {
      const host = document.getElementById('seek-assistant-questions-host');
      return host && host.shadowRoot ? host.shadowRoot.textContent : '';
    }""")


def wait_panel_text(page, text: str, timeout_s: float = 15) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if text in panel_text(page):
            return True
        page.wait_for_timeout(250)
    return False


def panel_dump(page) -> dict:
    """Everything the help view shows, as data (read from our own shadow root)."""
    return page.evaluate("""() => {
      const root = document.getElementById('seek-assistant-questions-host').shadowRoot;
      const t = (e) => e.textContent.replace(/\\s+/g, ' ').trim();
      const msg = root.querySelector('.sa-msg');
      return {
        message: msg ? [...msg.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim() : null,
        buttons: [...root.querySelectorAll('.sa-root button')].map(t),
        inputs: root.querySelectorAll('input, select, textarea').length,
        ticked: root.querySelectorAll('input:checked, option:checked, [aria-checked="true"], [aria-selected="true"]').length,
        recommended: [...root.querySelectorAll('*')].filter((e) =>
          (typeof e.className === 'string' && /recommend|selected|checked|best|suggested/i.test(e.className))
          || e.hasAttribute('data-recommended')).length,
        text: t(root.querySelector('.sa-root') || root),
        questions: [...root.querySelectorAll('.sa-q')].map((q) => ({
          kind: q.dataset.kind, text: t(q.querySelector('.sa-qtext')), badge: t(q.querySelector('.sa-badge')),
          wants: [...q.querySelectorAll('.sa-block.wants')].map((b) => ({
            label: t(b.querySelector('.sa-label')), text: t(b),
            items: [...b.querySelectorAll('.sa-row')].map(t) })),
          has: [...q.querySelectorAll('.sa-block.has')].map((b) => ({
            label: t(b.querySelector('.sa-label')), text: t(b),
            chips: [...b.querySelectorAll('.sa-chip')].map(t) })),
          opts: [...q.querySelectorAll('.sa-opt')].map((o) => ({
            label: t(o.querySelector('.sa-optlabel')), tag: o.querySelector('.sa-tag') ? t(o.querySelector('.sa-tag')) : '' })),
          gaps: [...q.querySelectorAll('.sa-gap .sa-ask')].map(t),
          remembered: [...q.querySelectorAll('.sa-remembered')].map(t),
          blocks: q.querySelectorAll('.sa-block, .sa-opts, .sa-gaps').length,
          text_all: t(q),
        })),
      };
    }""")


def page_side_effects(page) -> dict:
    return page.evaluate("""() => ({
      events: window.__events, sameHref: location.href === window.__startHref,
      sameHistory: history.length === window.__startHistory,
      textareas: [...document.querySelectorAll('textarea')].map((t) => t.value),
      checked: [...document.querySelectorAll('input:checked')].map((i) => i.id),
    })""")


def shot(page, name: str) -> None:
    """Optional screenshots for a look at the UI: E2E_SHOTS=<folder> python tests/e2e/..."""
    folder = os.environ.get("E2E_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"))


def panel_locator(page):
    return page.locator(f"#{HOST_ID}")


def letter_state(con) -> list:
    """The letter tables as they stand (a Yes must not change any of it)."""
    return [con.execute("SELECT * FROM letter_runs ORDER BY id").fetchall(),
            con.execute("SELECT * FROM cover_letters ORDER BY id").fetchall(),
            con.execute("SELECT * FROM letter_run_steps ORDER BY id").fetchall()]


def run(headed: bool) -> int:
    from playwright.sync_api import sync_playwright

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    samples = {s["id"]: s for s in data["samples"]}
    if not port_free():
        raise SystemExit(f"port {PORT} is busy: stop the test server first (this run needs a scratch DB)")

    work = Path(tempfile.mkdtemp(prefix="qa_e2e_"))
    app_db = ROOT / "app.db"
    app_db_before = app_db.stat().st_mtime if app_db.exists() else None
    db = make_scratch_db(work, samples)
    stub_log = work / "llm_stub.jsonl"
    stub_responses = work / "llm_stub_responses.json"
    stub_responses.write_text(json.dumps({"screening_classify": STUB_CLASSIFY}), encoding="utf-8")
    server = start_server(db, work / "server.log", stub_responses, stub_log)
    c = Checks()
    blocked: list[str] = []
    seek_served: list[str] = []
    pages: dict[str, str] = {}  # path -> html served for it
    dumps: dict[str, dict] = {}  # sample id -> what the overlay showed on first load

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
            # Everything except the scratch backend and the extension's own pages goes through
            # `route` (Seek pages are served from fixtures, anything else is aborted). The
            # backend is left alone on purpose: routing it would also strip the extension
            # pages' CORS exemption, and the side panel could not reach it.
            def intercepted(u: str) -> bool:
                url = urlparse(u)
                if url.scheme == "chrome-extension":
                    return False
                return not (url.hostname in ("127.0.0.1", "localhost") and url.port == PORT)

            ctx.route(intercepted, route)
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
                c.check(panel_ready(page), f"{sid}: the help view rendered (not the plain 9a fallback)")
                if items:
                    for q, item in zip(s["questions"], items):
                        want = q["expected"]["kind"]
                        # S5 is a full-pipeline job, so its one unsorted question is sorted
                        # by the (stub) model when the help is fetched: assisted, not unknown.
                        if sid == "S5" and want == "unknown":
                            want = "assisted"
                        c.check(item["kind"] == want and item["text"] == " ".join(q["text"].split()),
                                f"{sid}: '{q['text'][:48]}' shown as {want} ({item['badge']})")
                if panel_ready(page):
                    dumps[sid] = panel_dump(page)
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
            c.check(jobs == 5, f"5 job rows: S1 and S3 created as stubs, S2/S4/S5 reused ({jobs})")
            dump = "\n".join(str(r) for t in ("screening_questions", "job_screening_questions", "job_listings")
                             for r in con.execute(f"SELECT * FROM {t}").fetchall())
            c.check(PREFILL not in dump and MASKED not in dump,
                    "no pre-filled answer and nothing under [data-adora-mask] stored")
            unknown = con.execute("SELECT text FROM screening_questions WHERE kind='unknown'").fetchall()
            c.check(len(unknown) == 0, f"no question left unsorted ({len(unknown)})")
            sorted_row = con.execute("SELECT kind, strategy, classified_by, parameters FROM screening_questions "
                                     "WHERE text LIKE '%React Native%'").fetchall()
            c.check(len(sorted_row) == 1 and sorted_row[0][:3] == ("assisted", "years_skill_text", "model")
                    and "React Native with Expo" in (sorted_row[0][3] or ""),
                    f"S5's React Native question was sorted by the stub model ({sorted_row})")

            # --- 9b: what the overlay shows, per gating state ----------------------------
            d2 = dumps.get("S2", {"questions": [], "text": "", "buttons": []})
            assisted2 = [q for q in d2["questions"] if q["kind"] == "assisted"]
            c.check(len(assisted2) == 3 and all(q["wants"] and q["has"] for q in assisted2)
                    and all(q["wants"][0]["label"] == "What the job wants"
                            and q["has"][0]["label"] == "What your profile has" for q in assisted2),
                    "S2: every assisted question has a 'What the job wants' and a 'What your profile has' block")
            years = next((q for q in assisted2 if "years" in q["text"].lower()), None)
            c.check(years is not None and "Your dates fall in: 1 year" in years["has"][0]["text"]
                    and "3+ years" in years["wants"][0]["text"],
                    "S2: years question shows 'Your dates fall in: 1 year' (17 months, rounded down) and the ad's 3+ years")
            csharp = next((q for q in assisted2 if "C#" in q["text"]), None)
            c.check(csharp is not None and "work" in csharp["has"][0]["chips"],
                    "S2: C# question shows a work evidence item")
            langs = next((q for q in assisted2 if q["opts"]), None)
            tags = {o["label"]: o["tag"] for o in (langs["opts"] if langs else [])}
            c.check(tags.get("C#", "").startswith("Wanted · you have it")
                    and tags.get("JavaScript", "").startswith("Wanted · you have it")
                    and tags.get("Objective-C") == "Wanted · not in your profile",
                    f"S2: languages list tags C#/JavaScript 'Wanted · you have it', Objective-C 'Wanted · not in your profile' ({tags})")
            c.check(tags.get("Java", "").startswith("You have it") and tags.get("HTML") == "",
                    f"S2: a listed-only skill reads 'You have it', an unrelated option has no label ({tags.get('Java')!r}, {tags.get('HTML')!r})")
            c.check(d2["ticked"] == 0 and d2["inputs"] == 0 and d2["recommended"] == 0,
                    f"S2: nothing ticked, selected or marked recommended; no answer inputs "
                    f"(inputs={d2['inputs']}, ticked={d2['ticked']}, recommended={d2['recommended']})")
            c.check("recommend" not in d2["text"].lower(), "S2: the panel never says 'recommend'")
            c.check(all(q["blocks"] == 0 for q in d2["questions"] if q["kind"] == "user")
                    and sum(1 for q in d2["questions"] if q["kind"] == "user") == 3,
                    "S2: the 3 user questions show only 'Yours to answer' (no help blocks)")
            gaps2 = [g for q in d2["questions"] for g in q["gaps"]]
            c.check(gaps2 == ["Objective-C — wanted (nice to have). Do you have it?"],
                    f"S2: one gap card, Objective-C ({gaps2})")

            d4 = dumps.get("S4", {"questions": [], "message": None, "buttons": []})
            c.check(bool(d4["message"]) and d4["message"].startswith("Question help needs a full-pipeline cover letter")
                    and not any(q["wants"] or q["has"] for q in d4["questions"]),
                    f"S4 (one-shot letter): one-shot message, no 'What the job wants' block ({d4['message']!r})")
            c.check(not any("Create a cover letter" in b for b in d4["buttons"]),
                    "S4: no 'Create a cover letter' button (that is for no letter at all)")
            for sid in ("S1", "S3"):
                d = dumps.get(sid, {"questions": [], "message": None, "buttons": []})
                c.check(d["message"] == "Create a cover letter to get help with these questions."
                        and "Create a cover letter" in d["buttons"]
                        and not any(q["wants"] or q["has"] for q in d["questions"]),
                        f"{sid} (no letter): 'Create a cover letter to get help...' message and the button, no help blocks")
            c.check(all(q["badge"] for d in dumps.values() for q in d["questions"]),
                    "every question carries a kind badge in every gating state")

            d5 = dumps.get("S5", {"questions": []})
            q4 = next((q for q in d5["questions"] if "React Native" in q["text"]), None)
            c.check(q4 is not None and bool(q4["wants"]) and bool(q4["has"])
                    and "Do you have any experience with these?" in q4["text_all"] and len(q4["gaps"]) == 2,
                    "S5: the sorted question shows wants/has, the open prompt and two gap cards (React Native, Expo)")
            c.check(q4 is not None and not q4["opts"], "S5: an open-ended question lists no options")

            # --- repeat visit: recognised, nothing new -----------------------------
            before = con.execute("SELECT SUM(times_seen), COUNT(*) FROM screening_questions").fetchone()
            page.goto(apply_url(samples["S2"]))
            wait_overlay(page, len(samples["S2"]["questions"]))
            page.wait_for_timeout(1500)
            after = con.execute("SELECT SUM(times_seen), COUNT(*) FROM screening_questions").fetchone()
            c.check(before == after, f"repeat S2 visit adds no rows and no sightings ({before} -> {after})")

            shot(page, "s2-overlay")
            # --- gap No, through our own overlay -----------------------------------
            c.check(con.execute("SELECT COUNT(*) FROM gap_decisions").fetchone()[0] == 0, "no remembered gaps before")
            gap = panel_locator(page).locator(".sa-gap", has_text="Objective-C")
            gap.get_by_role("button", name="No", exact=True).click()
            c.check(wait_panel_text(page, "You said you don't have Objective-C"),
                    "S2: No on Objective-C re-renders 'You said you don't have Objective-C'")
            dn = panel_dump(page)
            c.check(not [g for q in dn["questions"] for g in q["gaps"]],
                    "S2: the Objective-C card is gone after No (no buttons on the remembered line)")
            dec = con.execute("SELECT id, label, cleared_at FROM gap_decisions").fetchall()
            c.check(len(dec) == 1 and dec[0][1] == "Objective-C" and dec[0][2] is None,
                    f"gap_decisions has the Objective-C 'no' ({dec})")
            sight = con.execute("SELECT source, importance FROM gap_sightings").fetchall()
            c.check(len(sight) >= 1 and all(r[0] == "quick_apply" for r in sight),
                    f"gap_sightings rows have source 'quick_apply' ({sight})")
            # --- undo the No: "Change my answer" brings the Yes/No card back ----------
            panel_locator(page).get_by_role("button", name="Change my answer").click()
            c.check(wait_panel_text(page, "Objective-C — wanted (nice to have). Do you have it?"),
                    "S2: 'Change my answer' brings the Objective-C Yes/No card back")
            cleared = con.execute("SELECT cleared_at FROM gap_decisions WHERE label = 'Objective-C'").fetchone()
            c.check(cleared is not None and cleared[0] is not None,
                    f"the undone 'no' is cleared, not deleted ({cleared})")
            # Say No again: the sidebar check further down expects the remembered No.
            gap = panel_locator(page).locator(".sa-gap", has_text="Objective-C")
            gap.get_by_role("button", name="No", exact=True).click()
            reopened = (wait_panel_text(page, "You said you don't have Objective-C")
                        and con.execute("SELECT COUNT(*) FROM gap_decisions WHERE cleared_at IS NULL").fetchone()[0] == 1)
            c.check(reopened, "a second No reopens the same remembered row")
            fx = page_side_effects(page)
            c.check(fx["events"] == [] and fx["sameHref"] and fx["sameHistory"],
                    f"clicking in our overlay caused no event or navigation on Seek's elements ({fx['events']})")

            # --- gap Yes (no text), on S5's Expo -----------------------------------
            page.goto(apply_url(samples["S5"]))
            wait_overlay(page, len(samples["S5"]["questions"]))
            skills_before = {r[0] for r in con.execute("SELECT name FROM skills")}
            letters_before = letter_state(con)
            expo = panel_locator(page).locator(".sa-gap", has_text="Expo — wanted")
            expo.get_by_role("button", name="Yes", exact=True).click()
            c.check(expo.locator("textarea").count() == 1
                    and expo.locator("textarea").get_attribute("placeholder") == "Where did you use it? (optional)"
                    and "does not change your cover letter" in expo.inner_text(),
                    "S5: Yes reveals the optional 'Where did you use it?' box and says the letter is unchanged")
            expo.get_by_role("button", name="Continue", exact=True).click()
            expo.get_by_role("button", name="Add to my profile").wait_for(timeout=15000)
            shot(page, "s5-proposal")
            c.check(expo.locator("input[type=text]").first.input_value() == "Expo",
                    "S5: the proposal shows the skill 'Expo' in an editable field")
            expo.get_by_role("button", name="Add to my profile").click()
            c.check(wait_panel_text(page, "Added Expo to your profile. Your cover letter is unchanged."),
                    "S5: confirm shows 'Added Expo to your profile. Your cover letter is unchanged.'")
            row = con.execute("SELECT origin FROM skills WHERE name='Expo'").fetchall()
            c.check(row == [("ask_user",)]
                    and {r[0] for r in con.execute("SELECT name FROM skills")} - skills_before == {"Expo"},
                    f"skills table has Expo with origin 'ask_user' and nothing else new ({row})")
            c.check(letter_state(con) == letters_before,
                    "letter_runs, letter_run_steps and cover_letters are unchanged by a Yes")
            d5b = panel_dump(page)
            c.check(len([g for q in d5b["questions"] for g in q["gaps"]]) == 1,
                    "S5: after the Yes only the React Native card remains")
            fx = page_side_effects(page)
            c.check(fx["events"] == [] and fx["sameHref"] and fx["sameHistory"],
                    f"the Yes flow caused no event or navigation on Seek's elements ({fx['events']})")

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

            # --- the side panel's job card shows the same help ---------------------
            ext_id = sw.url.split("/")[2]
            side = ctx.new_page()
            side.on("pageerror", lambda e: print("   sidebar pageerror:", e))
            side.on("console", lambda m: m.type in ("error", "warning") and print("   sidebar console:", m.text))
            side.goto(f"chrome-extension://{ext_id}/sidebar.html")
            card = side.locator("ul#job-list li", has_text="Full Stack Developer").first
            try:
                card.wait_for(timeout=15000)
            except Exception:
                print("SIDEBAR DEBUG:", side.inner_text("body")[:1500])
                raise
            card.click()
            summary = side.locator("details.letter-notes summary", has_text="Quick Apply questions (6)")
            summary.wait_for(timeout=15000)
            c.check(True, "sidebar: S2's expanded card has 'Quick Apply questions (6)'")
            summary.click()
            sa = side.locator(".sa-root").first
            sa.wait_for(timeout=5000)
            shot(side, "sidebar")
            c.check(sa.locator(".sa-block.wants").count() == 3 and sa.locator(".sa-block.has").count() == 3
                    and "You said you don't have Objective-C" in sa.inner_text(),
                    "sidebar: the section shows the same wants/has blocks and the remembered 'no'")
            c.check(side.locator(".job-detail").first.is_visible(),
                    "sidebar: interacting with the section does not collapse the card")
            side.close()

            # --- the model calls, and nothing else -----------------------------------
            log = ([json.loads(line) for line in stub_log.read_text(encoding="utf-8").splitlines() if line.strip()]
                   if stub_log.exists() else [])
            c.check([e["task"] for e in log] == ["screening_classify"],
                    f"the stub log holds exactly one call, task screening_classify ({[e['task'] for e in log]})")
            norm = lambda t: " ".join(t.split())  # noqa: E731
            sent = norm(log[0]["user_content"]) if log else ""
            s5q4 = next(q for q in samples["S5"]["questions"] if "React Native" in q["text"])
            user_texts = [norm(q["text"]) for smp in samples.values() for q in smp["questions"]
                          if q["expected"]["kind"] == "user"]
            c.check(bool(log) and norm(s5q4["text"]) in sent and not [t for t in user_texts if t in sent],
                    f"that call carried S5 Q4 and none of the {len(user_texts)} user-kind question texts")

            usage = con.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
            c.check(usage == 0, f"no LLM call recorded ({usage} llm_usage rows)")
            con.close()
            ctx.close()

        c.check(not blocked, f"nothing off-machine requested ({blocked[:5]})")
        c.check(all(urlparse(u).hostname in SEEK_HOSTS for u in seek_served),
                f"{len(seek_served)} Seek requests, all answered by the harness, none reached Seek")
        c.check((app_db.stat().st_mtime if app_db.exists() else None) == app_db_before,
                "the repo's app.db was never touched")
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
