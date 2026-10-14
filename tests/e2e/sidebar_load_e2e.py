"""Load check for the side panel's scripts: does every script load, in order, and does
the panel survive its tabs, Personalise, zoom and every live-update event?

    python tests/e2e/sidebar_load_e2e.py            # headless Chromium
    python tests/e2e/sidebar_load_e2e.py --headed

Not collected by pytest (needs Chromium). It guards the sidebar.js split (plan
docs/refactor-9-10-plan.md, #10): the panel is several classic scripts sharing one global
scope, and a missing <script> tag, a wrong order or a duplicate top-level name only shows
up in a browser.

It needs NO backend and can't reach Seek:
- every request that isn't to the extension itself (chrome-extension:, blob:, data:) is
  aborted, so the panel sees "Backend not running", which proves loadJobs ran. The panel
  is pointed at the TEST port (8001) anyway;
- ``chrome.tabs.create`` is stubbed to record the URL (installed with add_init_script, so
  it is in place before the panel's scripts run): a real tab can start loading before
  Playwright's routing attaches to it;
- ``EventSource`` is replaced by a fake that records its instances and can fire events;
- it never presses Scan Page or Import from Seek profile (those are owner hand checks).

Checks: no page error on load; every top-level name of every script in sidebar.html can
be read (a script that never ran, or died part-way, leaves names missing or in the
temporal dead zone); each tab draws; Personalise opens; zoom in and out; each SSE event
the panel listens for fires without a page error; nothing opened a tab or left the
machine. Exit code 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
EXTENSION = ROOT / "extension"
PORT = 8001
SSE_EVENTS = ["job_processed", "cover_letter_ready", "letter_run_started", "letter_run_waiting",
              "letter_run_done", "letter_run_failed", "llm_budget_blocked", "job_applied",
              "evidence_changed"]

# Runs in every page before its own scripts. Only the panel's names are touched.
INIT_SCRIPT = """
(() => {
  window.__opened = [];
  if (globalThis.chrome && chrome.tabs) {
    chrome.tabs.create = (o) => { window.__opened.push((o && o.url) || ''); return Promise.resolve({}); };
  }
  class FakeEventSource {
    constructor(url) {
      this.url = url; this.listeners = {}; this.onopen = null; this.onerror = null;
      this.readyState = 0;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
    removeEventListener(name, fn) {
      this.listeners[name] = (this.listeners[name] || []).filter(f => f !== fn);
    }
    close() { this.readyState = 2; }
    open() { this.readyState = 1; if (this.onopen) this.onopen(new Event('open')); }
    emit(name, data = '{}') {
      for (const fn of this.listeners[name] || []) fn(new MessageEvent(name, { data }));
    }
  }
  FakeEventSource.instances = [];
  window.EventSource = FakeEventSource;
  window.__FakeEventSource = FakeEventSource;
})();
"""


class Checks:
    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, what: str) -> None:
        self.results.append((bool(ok), what))
        print(("  ok   " if ok else "  FAIL ") + what)

    def failed(self) -> int:
        return sum(1 for ok, _ in self.results if not ok)


def wait_for(fn, timeout_s: float = 10, every: float = 0.1):
    end = time.time() + timeout_s
    value = fn()
    while not value and time.time() < end:
        time.sleep(every)
        value = fn()
    return value


def run(headed: bool) -> int:
    from playwright.sync_api import sync_playwright

    import js_globals

    scripts = js_globals.load()
    names = {s.name: list(s.top) for s in scripts}
    work = Path(tempfile.mkdtemp(prefix="sidebar_load_e2e_"))
    c = Checks()
    requested: list[str] = []
    errors: list[str] = []

    def abort(route):
        requested.append(route.request.url)
        return route.abort()

    def off_extension(u: str) -> bool:
        return urlparse(u).scheme not in ("chrome-extension", "blob", "data")

    def fire(js: str, arg=None) -> None:
        """Run js in the panel; an exception it throws counts as a page error."""
        try:
            side.evaluate(js, arg)
        except Exception as e:  # noqa: BLE001 - recorded, and fails the run
            errors.append(str(e).splitlines()[0])

    def settle(what: str, seconds: float = 0.8) -> None:
        """Let async work (aborted fetches, debounced reloads) finish, then check errors."""
        before = len(errors)
        time.sleep(seconds)
        c.check(len(errors) == before, f"no page error after {what} {errors[before:before + 3]}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(work / "profile"), channel="chromium", headless=not headed,
            viewport={"width": 380, "height": 900},
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
        )
        ctx.route(off_extension, abort)
        ctx.add_init_script(INIT_SCRIPT)
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        sw.evaluate("chrome.storage.local.set({ backendEnv: 'test' })")
        ext_id = sw.url.split("/")[2]

        side = ctx.new_page()
        side.on("pageerror", lambda e: errors.append(str(e)))
        side.goto(f"chrome-extension://{ext_id}/sidebar.html")

        # --- load ---------------------------------------------------------------
        print("Load")
        c.check(side.evaluate("chrome.tabs.create.toString().includes('__opened')"),
                "chrome.tabs.create is stubbed before the panel's scripts ran")
        c.check(side.evaluate("window.EventSource === window.__FakeEventSource"), "EventSource is the fake")
        c.check(wait_for(lambda: "Backend not running" in side.inner_text("#job-status")),
                "with no backend the Jobs list says 'Backend not running' (loadJobs ran)")
        settle("load", 1.5)
        c.check(not errors, f"no page error on load {errors[:3]}")
        order = js_globals.panel_scripts()
        c.check(order[-2:] == ["sidebar.js", "dashboard.js"], f"sidebar.js then dashboard.js load last ({order})")
        for script, top in names.items():
            bad = side.evaluate("""(names) => names.filter(n => {
                try { (0, eval)(n); return false; } catch (e) { return true; } })""", top)
            c.check(top and not bad, f"{script}: all {len(top)} top-level names are defined {bad[:5]}")
        c.check(side.evaluate("document.documentElement.style.zoom") == "1", "zoom applied at load (1)")
        c.check(side.evaluate("document.querySelector('#env-pill').textContent") == "TEST",
                "env pill rendered (TEST)")

        # --- tabs ---------------------------------------------------------------
        print("Tabs")
        c.check(side.locator("nav#tabs .tab.active").inner_text() == "Overview", "opens on the Overview tab")
        sections = {"overview": "#overview-section", "jobs": "#jobs-section",
                    "applied": "#applied-section", "profile": "#profile-section"}
        for tab in ("jobs", "applied", "profile", "overview"):
            side.click(f"nav#tabs .tab[data-tab={tab}]")
            shown = [t for t, sel in sections.items() if side.is_visible(sel)]
            c.check(shown == [tab], f"{tab} tab: only its section shows ({shown})")
            settle(f"opening the {tab} tab")

        # --- Personalise --------------------------------------------------------
        print("Personalise")
        side.click("nav#tabs .tab[data-tab=jobs]")
        side.click("#personalise-toggle")
        c.check(side.get_attribute("#personalise-toggle", "aria-expanded") == "true"
                and side.is_visible("#personalise-panel"), "Personalise opens")
        settle("opening Personalise")
        side.click("#personalise-toggle")
        c.check(side.is_hidden("#personalise-panel"), "Personalise closes")

        # --- zoom ---------------------------------------------------------------
        print("Zoom")
        zoom = lambda: side.evaluate("document.documentElement.style.zoom")  # noqa: E731
        side.click("#zoom-in")
        c.check(zoom() == "1.1", f"zoom in -> 1.1 ({zoom()})")
        side.click("#zoom-out")
        side.click("#zoom-out")
        c.check(zoom() == "0.9", f"zoom out twice -> 0.9 ({zoom()})")
        side.click("#zoom-in")
        c.check(zoom() == "1", f"back to 1 ({zoom()})")
        settle("zooming", 0.3)

        # --- live updates (fake EventSource) ------------------------------------
        print("Live updates")
        src = side.evaluate("__FakeEventSource.instances.map(s => s.url)")
        c.check(src == [f"http://localhost:{PORT}/events"], f"one EventSource on the TEST backend ({src})")
        listened = side.evaluate("Object.keys(__FakeEventSource.instances[0]?.listeners || {}).sort()")
        c.check(listened == sorted(SSE_EVENTS), f"the panel listens for exactly the expected events ({listened})")
        fire("__FakeEventSource.instances[0].open()")
        fire("__FakeEventSource.instances[0].open()")  # the second open is a reconnect: reload
        settle("SSE open and reconnect")
        for name in SSE_EVENTS:
            fire("(n) => __FakeEventSource.instances[0].emit(n)", name)
            settle(f"SSE {name}")
        c.check(wait_for(lambda: "Backend not running" in side.inner_text("#job-status")),
                "the Jobs list still says 'Backend not running' after the reloads")

        # --- nothing escaped ----------------------------------------------------
        opened = side.evaluate("window.__opened")
        c.check(opened == [], f"no tab was opened ({opened})")
        hosts = {urlparse(u).hostname for u in requested}
        c.check(hosts <= {"localhost", "127.0.0.1"},
                f"every request was to this machine, and was aborted ({sorted(hosts)})")
        c.check(not any("seek" in (h or "") for h in hosts), "no request to Seek")
        c.check(not errors, f"no page errors in the whole run {errors[:3]}")
        ctx.close()

    failed = c.failed()
    print(f"\n{len(c.results) - failed}/{len(c.results)} checks passed.")
    if not failed:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"Browser profile kept at {work}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    sys.exit(run(parser.parse_args().headed))
