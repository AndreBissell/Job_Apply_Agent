// Side panel: tabbed Overview / Jobs / Applied / Profile. Vanilla JS, no build step: the
// panel is the classic scripts listed in sidebar.html (sidebar_*.js, then this file, then
// dashboard.js), sharing one global scope, so their order is the contract. This file holds
// the env pill, zoom and startup. BACKEND comes from config.js (real 8000 / test 8001).

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
