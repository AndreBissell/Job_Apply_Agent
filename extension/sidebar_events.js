// Live updates: the LLM budget banner and the SSE connection (connectEvents, scheduleReload,
// typingIn, shared with dashboard.js). Classic script: sidebar.html loads it after
// sidebar_seek_import.js and before sidebar.js; all share one global scope.
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
    if (plBusy() || typingIn(overviewSection) || typingIn(appliedSection)) {
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

  // A Quick Apply was recorded from Seek's "application sent" page (apply_flow.js):
  // the Overview count, the Applied tab and the job's card all change.
  source.addEventListener('job_applied', () => scheduleReload());

  // A screenshot was saved, or a Centrelink PDF made or undone: the Overview's evidence
  // card and the Applied tab's notes change.
  source.addEventListener('evidence_changed', () => refreshDashboard());

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
