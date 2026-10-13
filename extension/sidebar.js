// Side panel: tabbed Overview / Jobs / Applied / Profile. The Overview and Applied tabs
// live in dashboard.js (loaded after this file, sharing its globals).
// Vanilla JS, no build step. Talks to the FastAPI backend — BACKEND comes from
// config.js and is either the real (8000) or test (8001) environment.

// ---------------------------------------------------------------------------
// Import from Seek Profile
// ---------------------------------------------------------------------------

// Runs INSIDE the au.seek.com/profile/me tab — extracts profile data.
// All extraction happens in the user's own browser/session; nothing goes server-side.
async function seekProfileExtract() {
  // Seek is a SPA — wait until the profile content has actually rendered
  await new Promise(resolve => {
    const start = Date.now();
    function check() {
      const ready = document.querySelector('[data-automation="personal-details-card"]')
                 || document.querySelector('[data-automation="read-role"]')
                 || document.querySelector('[data-automation="skills-items"]');
      if (ready || Date.now() - start > 15000) resolve();
      else setTimeout(check, 500);
    }
    check();
  });

  function da(attr)    { return document.querySelector(`[data-automation="${attr}"]`); }
  function daAll(attr) { return [...document.querySelectorAll(`[data-automation="${attr}"]`)]; }

  // Clone el, remove noisy child nodes, return trimmed innerText
  function cleanText(el, removeSelectors) {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll(removeSelectors).forEach(n => n.remove());
    return c.innerText.trim();
  }

  // "Jan 2022" / "January 2022" / "2022" → "YYYY-MM"
  function parseDate(str) {
    if (!str) return null;
    const MONTHS = {jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
    const m = str.trim().match(/([a-z]+)\s+(\d{4})/i);
    if (m) {
      const mon = MONTHS[m[1].toLowerCase().slice(0, 3)];
      if (mon) return `${m[2]}-${String(mon).padStart(2, '0')}`;
    }
    const y = str.trim().match(/^(\d{4})$/);
    if (y) return `${y[1]}-01`;
    return null;
  }

  // "Dec 2020 - Present (5 years 7 months)" → [startYYYY-MM, endYYYY-MM|null, isCurrent]
  function parseDateRange(raw) {
    if (!raw) return [null, null, false];
    const str = raw.replace(/\s*\(.*?\)\s*$/, '').trim(); // strip "(5 years 7 months)"
    const parts = str.split(/\s*[-–—]\s*/); // hyphen, en-dash, em-dash
    const start = parseDate(parts[0]);
    const endRaw = (parts[1] || '').trim();
    const isCurrent = /present|current/i.test(endRaw);
    return [start, isCurrent ? null : parseDate(endRaw), isCurrent];
  }

  const NOISE = 'button, svg, [aria-hidden="true"], [role="button"]';

  const out = {
    name: null, location: null, email: null, summary: null,
    experiences: [], qualifications: [], skills: [],
    unrecognised: {},  // read-* sections seen on the page but not imported: { 'read-x': count }
  };

  // ── Personal details ──────────────────────────────────────────────────────
  // Confirmed from live HTML: name is in [data-automation="inline-nudge-name"]
  const nameEl = da('inline-nudge-name');
  if (nameEl) out.name = nameEl.innerText.trim() || null;
  const locEl = da('inline-nudge-location');
  if (locEl) {
    const t = cleanText(locEl, NOISE);
    if (t && !/^add\s/i.test(t)) out.location = t; // skip "Add location" nudge
  }
  const emailEl = da('personal-detail-email');
  if (emailEl) out.email = emailEl.innerText.trim() || null;

  // ── Summary ───────────────────────────────────────────────────────────────
  const summaryCard = da('summary-card');
  if (summaryCard) {
    out.summary = cleanText(summaryCard,
      `${NOISE}, [data-automation="summary-read-title"], [data-automation="summary-edit"], [data-automation="summary-empty-nudge"]`
    ) || null;
  }

  // ── Career history ────────────────────────────────────────────────────────
  // Confirmed from live HTML:
  //   h4                → job title  ("Team Member")
  //   time              → date range ("Dec 2020 - Present (5 years 7 months)")
  //   [data-hj-masked]  → description (appears twice for clamp/expand; take first visible)
  //   remaining text    → company name (after stripping all above + buttons)
  daAll('read-role').forEach(item => {
    const title   = item.querySelector('h4')?.innerText?.trim() || '';
    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate, isCurrent] = parseDateRange(dateRaw);

    const descEl = item.querySelector(':not([aria-hidden="true"]) [data-hj-masked]')
                || item.querySelector('[data-hj-masked]');
    const description = descEl?.innerText?.trim().replace(/^[•·]\s*/, '') || '';

    // Company: remove all known elements; first remaining line is the company name
    const company = cleanText(item, `h4, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    out.experiences.push({
      experience_type: 'job', title, organization: company,
      start_date: startDate, end_date: endDate, is_current: isCurrent,
      description, skills: [],
    });
  });

  // ── Education ─────────────────────────────────────────────────────────────
  // Same pattern as roles: h4 = degree title, time = dates, remainder = institution
  daAll('read-qualification').forEach(item => {
    const title   = item.querySelector('h4, h3')?.innerText?.trim() || '';
    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate] = parseDateRange(dateRaw);
    const institution = cleanText(item, `h4, h3, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    out.qualifications.push({
      qualification_type: 'degree', title, institution,
      field_of_study: '', grade: '',
      start_date: startDate, end_date: endDate, status: 'completed',
    });
  });

  // ── Other sections (projects, volunteering, certifications, …) ────────────
  // NOT confirmed against live HTML: only read-role / read-qualification have
  // been seen. Seek's profile sections appear to share the read-<thing> naming
  // and the h4 + time layout, so any OTHER read-* block is parsed the same way
  // when its name says what it is (below). Anything whose name doesn't say — and
  // any block with no h4/h3 title — is left out of the import and reported by
  // name in `unrecognised`, so the status line tells us exactly what to add
  // rather than silently dropping it.
  const KNOWN_READ = new Set(['read-role', 'read-qualification']);
  const EXP_TYPE_BY_NAME  = [[/volunteer/i, 'volunteer'], [/intern/i, 'internship'], [/project/i, 'project']];
  const CERT_NAME = /licen[cs]e|certif|course|training/i;

  document.querySelectorAll('[data-automation^="read-"]').forEach(item => {
    const key = item.getAttribute('data-automation');
    if (KNOWN_READ.has(key)) return;

    const title = item.querySelector('h4, h3')?.innerText?.trim() || '';
    const expType = EXP_TYPE_BY_NAME.find(([re]) => re.test(key))?.[1] || null;
    const isCert  = CERT_NAME.test(key);
    if (!title || (!expType && !isCert)) {
      out.unrecognised[key] = (out.unrecognised[key] || 0) + 1;
      return;
    }

    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate, isCurrent] = parseDateRange(dateRaw);
    const descEl = item.querySelector(':not([aria-hidden="true"]) [data-hj-masked]')
                || item.querySelector('[data-hj-masked]');
    const description = descEl?.innerText?.trim().replace(/^[•·]\s*/, '') || '';
    const rest = cleanText(item, `h4, h3, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    if (expType) {
      out.experiences.push({
        experience_type: expType, title, organization: rest,
        start_date: startDate, end_date: endDate, is_current: isCurrent,
        description, skills: [],
      });
    } else {
      out.qualifications.push({
        qualification_type: 'certificate', title, institution: rest,
        field_of_study: '', grade: '',
        start_date: startDate, end_date: endDate, status: 'completed',
      });
    }
  });

  // ── Skills ────────────────────────────────────────────────────────────────
  // Confirmed from live HTML: <li><div title="PHP Programming">...</div></li>
  // The title attribute is cleanest — no leading spaces.
  const skillsEl = da('skills-items');
  if (skillsEl) {
    out.skills = [...skillsEl.querySelectorAll('div[title]')]
      .map(el => el.getAttribute('title'))
      .filter(Boolean);
  }

  return out;
}

function setImportStatus(msg, color) {
  const el = document.getElementById('import-status');
  if (el) { el.textContent = msg; el.style.color = color || '#6b7280'; }
}

async function importFromSeekProfile() {
  const btn = document.getElementById('seek-import-btn');
  btn.disabled = true;
  setImportStatus('Opening Seek profile…', '#6b7280');

  let tab;
  try {
    tab = await chrome.tabs.create({ url: 'https://au.seek.com/profile/me', active: false });
    await waitForTabComplete(tab.id, 20000);
    setImportStatus('Waiting for page to render…', '#6b7280');
    // Extra buffer — Seek SPA often fires 'complete' before React has rendered content
    await sleep(2000);

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: seekProfileExtract,
    });

    console.log('[SeekImport] Extracted:', result);

    if (!result) throw new Error('No data returned from page.');

    // Log debug info so we can refine selectors if needed
    if (result._debug) {
      console.log('[SeekImport] Debug:', result._debug);
    }

    // Merge into the profile form — only overwrite fields that have data
    const p = result;
    if (p.name)     document.getElementById('p-name').value     = p.name;
    if (p.email)    document.getElementById('p-email').value    = p.email;
    if (p.location) document.getElementById('p-location').value = p.location;
    if (p.summary)  document.getElementById('p-summary').value  = p.summary;

    // Qualifications — prepend imported ones, keeping existing
    const qualsList = document.getElementById('quals-list');
    for (const q of (p.qualifications || [])) {
      if (q.title) qualsList.prepend(makeQualCard(q, false));
    }

    // Experiences — prepend imported ones, keeping existing
    const expsList = document.getElementById('exps-list');
    for (const e of (p.experiences || [])) {
      if (e.title) expsList.prepend(makeExpCard(e, false));
    }

    // Skills — merge without duplicates
    for (const s of (p.skills || [])) addSkill(s);
    // Programmatic .value writes fire no input event — re-check explicitly.
    refreshProfileDirty();

    const counts = [
      p.experiences?.length && `${p.experiences.length} experience(s)`,
      p.qualifications?.length && `${p.qualifications.length} qualification(s)`,
      p.skills?.length && `${p.skills.length} skill(s)`,
    ].filter(Boolean);

    // Sections the page has that the importer couldn't classify — surfaced by
    // name (not silently dropped) so the right selector can be added.
    const skippedSections = Object.entries(p.unrecognised || {})
      .map(([k, n]) => `${k} ×${n}`).join(', ');

    if (!p.name && !counts.length) {
      setImportStatus('Nothing extracted — page may not have rendered. Check browser console (F12).', '#d97706');
    } else {
      const skipped = skippedSections ? ` Not imported (unrecognised): ${skippedSections}.` : '';
      setImportStatus(
        `Imported: ${[p.name && 'name', ...counts].filter(Boolean).join(', ')}. Review & save.${skipped}`,
        skippedSections ? '#d97706' : '#059669',
      );
    }
  } catch (e) {
    console.error('[SeekImport] Error:', e);
    setImportStatus(`Import failed: ${e.message}`, '#dc2626');
  } finally {
    if (tab) await chrome.tabs.remove(tab.id).catch(() => {});
    btn.disabled = false;
  }
}

document.getElementById('seek-import-btn').addEventListener('click', importFromSeekProfile);

document.getElementById('export-profile-btn').addEventListener('click', async () => {
  try {
    const res = await fetch(`${BACKEND}/profile-ui/data`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `profile-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    setImportStatus(`Export failed: ${e.message}`, '#dc2626');
  }
});

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
