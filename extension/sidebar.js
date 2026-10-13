// Side panel: tabbed Overview / Jobs / Applied / Profile. The Overview and Applied tabs
// live in dashboard.js (loaded after this file, sharing its globals).
// Vanilla JS, no build step. Talks to the FastAPI backend — BACKEND comes from
// config.js and is either the real (8000) or test (8001) environment.

// ---------------------------------------------------------------------------
// LLM budget banner — shown while the backend's USD cap is blocking cover
// letters (mid/strong calls). Scanning, extraction and matching keep running.
// ---------------------------------------------------------------------------
async function refreshBudgetBanner() {
  const banner = document.getElementById('budget-banner');
  try {
    const res = await fetch(`${BACKEND}/llm/usage`);
    if (!res.ok) return;
    const status = await res.json();
    banner.textContent = status.blocked
      ? `${status.reason} Cover letters are paused; scanning and scoring continue.`
      : '';
    banner.hidden = !status.blocked;
  } catch { /* the banner is advisory — fail silently */ }
}

// ---------------------------------------------------------------------------
// SSE — live updates from the backend
// ---------------------------------------------------------------------------
let _eventsEverOpened = false;

// True while the user is typing inside `el` (an answer, a letter edit, the start date):
// a reload would wipe what they typed.
function typingIn(el) {
  const active = document.activeElement;
  return !!(active && el.contains(active) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(active.tagName));
}

// Reload the job list (and the visible Overview / Applied tab) after a letter event,
// debounced. Never while the user is typing in them: it tries again a few seconds later.
let reloadTimer = null;
function scheduleReload(delay = 400) {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(function tryReload() {
    if (typingIn(jobListEl) || typingIn(overviewSection) || typingIn(appliedSection)) {
      reloadTimer = setTimeout(tryReload, 5000);
      return;
    }
    loadJobs();
    refreshDashboard();
  }, delay);
}

function connectEvents() {
  let source;
  try {
    source = new EventSource(`${BACKEND}/events`);
  } catch {
    return; // backend not running; jobs tab will show its own error
  }

  source.onopen = () => {
    if (_eventsEverOpened) { loadJobs(); refreshDashboard(); } // reconnect — catch up on missed events
    _eventsEverOpened = true;
  };

  // New job scored → reload the jobs list so it appears
  source.addEventListener('job_processed', () => {
    loadJobs();
    refreshDashboard();
  });

  // Cover letter ready → update the badge and, if expanded, the editor in place.
  // A full reload is the simplest way to move the card between the "pending"
  // and "ready" badge states, and jobs lists are small enough that it's cheap.
  source.addEventListener('cover_letter_ready', () => scheduleReload());

  // A cover-letter run started, is waiting on a question, finished or failed: move the
  // card between "Writing…", "Needs your answer" and "Ready".
  for (const name of ['letter_run_started', 'letter_run_waiting', 'letter_run_done', 'letter_run_failed']) {
    source.addEventListener(name, () => scheduleReload());
  }

  // The idle loop hit the daily/total LLM budget cap and paused cover letters.
  source.addEventListener('llm_budget_blocked', () => {
    refreshBudgetBanner();
  });

  source.onerror = () => {
    // EventSource auto-reconnects; onopen will fire again and trigger a reload
  };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
const envPill = document.getElementById('env-pill');

function renderEnvPill() {
  envPill.textContent = BACKEND_ENV.toUpperCase();
  document.body.classList.toggle('env-test', BACKEND_ENV === 'test');
}

// Flipping the switch swaps the whole data set (a different database), so
// reload the panel rather than patch state in place — nothing from the old
// environment can linger on screen.
envPill.addEventListener('click', async () => {
  const next = BACKEND_ENV === 'real' ? 'test' : 'real';
  await chrome.storage.local.set({ backendEnv: next });
  location.reload();
});

// ---------------------------------------------------------------------------
// Zoom + width classes
// ---------------------------------------------------------------------------
// CSS `zoom` doesn't change the viewport width, so the layout breakpoints
// can't be @media queries — they're body classes computed from the width the
// content actually has once zoom is applied (window.innerWidth / currentZoom).
const ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.5];
let currentZoom = 1;
const zoomInBtn = document.getElementById('zoom-in');
const zoomOutBtn = document.getElementById('zoom-out');

function updateWidthClasses() {
  const w = window.innerWidth / currentZoom;
  document.body.classList.toggle('w-wide', w >= 640);
  document.body.classList.toggle('w-mid', w >= 420);
}

function applyZoom(z) {
  currentZoom = z;
  document.documentElement.style.zoom = z;
  const idx = ZOOM_STEPS.indexOf(z);
  zoomOutBtn.disabled = idx <= 0;
  zoomInBtn.disabled = idx === ZOOM_STEPS.length - 1;
  const pct = Math.round(z * 100);
  zoomOutBtn.title = `Smaller (now ${pct}%)`;
  zoomInBtn.title = `Larger (now ${pct}%)`;
  updateWidthClasses();
}

function stepZoom(dir) {
  const idx = ZOOM_STEPS.indexOf(currentZoom);
  const next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, idx + dir));
  if (next === idx) return;
  applyZoom(ZOOM_STEPS[next]);
  try {
    chrome.storage.local.set({ sidebarZoom: ZOOM_STEPS[next] });
  } catch (e) { /* zoom just won't persist */ }
}

zoomOutBtn.addEventListener('click', () => stepZoom(-1));
zoomInBtn.addEventListener('click', () => stepZoom(1));
window.addEventListener('resize', updateWidthClasses);

// Apply the saved zoom straight away — deliberately not inside backendReady,
// so it doesn't wait on (or depend on) the local backend being up.
applyZoom(1);
(async () => {
  try {
    const { sidebarZoom } = await chrome.storage.local.get('sidebarZoom');
    if (ZOOM_STEPS.includes(sidebarZoom)) applyZoom(sidebarZoom);
  } catch (e) { /* fall back to 1 */ }
})();

backendReady.then(() => {
  renderEnvPill();
  loadPreferences().then(() => { loadJobs(); loadOverview(); }); // Overview is the opening tab
  refreshBudgetBanner();
  connectEvents();
});
