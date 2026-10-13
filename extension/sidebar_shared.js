// Shared panel basics: PROFILE_ID, score tiers, label tables, the DOM helper mk, tab
// switching, markApplied. Classic script: sidebar.html loads it before sidebar.js and
// dashboard.js, which share these globals. announceSidebarOpen() runs first.

// Each environment is its own database with exactly one profile, and that
// profile is id 1 in both — so this constant is correct for either.
const PROFILE_ID = 1;

// Tell the background worker while this side panel is open. When the panel is
// closed the port drops, and the worker hides the Quick Apply questions panel on
// Seek tabs (content_script.js). Reconnects if the worker was restarted.
function announceSidebarOpen() {
  try {
    const port = chrome.runtime.connect({ name: 'sidebar' });
    port.onDisconnect.addListener(() => setTimeout(announceSidebarOpen, 1000));
  } catch { /* extension context gone: the panel is closing anyway */ }
}
announceSidebarOpen();

// Score tiers (docs/extension-revamp-plan.md §1) — purely visual colouring.
const GOLD_MIN = 95;
const BLUE_MIN = 90;
const GREEN_MIN = 75;
const AMBER_MIN = 60;

// Minimum score for an auto-generated cover letter. User-tunable in the
// "Personalise metrics" panel and stored server-side (profiles.preferences),
// since the backend idle loop is what acts on it. 75 is only the pre-load default.
let autoLetterMin = GREEN_MIN;

function tierOf(score) {
  if (score == null) return 'amber';
  if (score >= GOLD_MIN) return 'gold';
  if (score >= BLUE_MIN) return 'blue';
  if (score >= GREEN_MIN) return 'green';
  if (score >= AMBER_MIN) return 'amber';
  return 'hidden';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const QUAL_TYPE_LABELS   = { degree: 'Degree', certificate: 'Certificate', diploma: 'Diploma', other: 'Other' };
const QUAL_STATUS_LABELS = { completed: 'Completed', in_progress: 'In Progress', withdrawn: 'Withdrawn' };
const EXP_TYPE_LABELS    = {
  job: 'Job', internship: 'Internship', volunteer: 'Volunteer', project: 'Project',
  // The cover-letter flow (ask_user) can add these; the editor must offer them or a save
  // would turn the row into a 'job'.
  university_project: 'University project', assignment: 'Assignment', personal_project: 'Personal project',
};

function fmtDate(ym) {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1)
    .toLocaleDateString('en-AU', { month: 'short', year: 'numeric' });
}

// Month fields are plain text ("YYYY-MM"), not <input type="month">: the native
// control makes you scroll or click through segments to change a year, where a
// typed field lets you just retype it. The value stays "YYYY-MM", so the
// save/load code is unchanged.
function monthInputHtml(cls) {
  return `<input class="${cls} month-input" type="text" inputmode="numeric" maxlength="7" placeholder="YYYY-MM" autocomplete="off">`;
}

// "2025-3" / "2025-03" -> "2025-03"; anything else (incl. month 13) -> null.
function normaliseMonth(raw) {
  const m = /^(\d{4})-(\d{1,2})$/.exec((raw || '').trim());
  if (!m) return null;
  const month = Number(m[2]);
  return month >= 1 && month <= 12 ? `${m[1]}-${String(month).padStart(2, '0')}` : null;
}

// The value to save/summarise for a month input: valid "YYYY-MM" or ''. A
// half-typed or invalid date is treated as empty rather than sent to the backend.
function monthValue(input) {
  return normaliseMonth(input.value) || '';
}

document.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.classList?.contains('month-input')) return;
  if (/[^\d-]/.test(el.value)) el.value = el.value.replace(/[^\d-]/g, '');
  // Add the dash as the 4th digit is typed at the end. Never on delete or
  // mid-string, so fixing the year in place ("2026-03" -> "2025-03") isn't
  // reformatted under the caret.
  if (e.inputType === 'insertText' && /^\d{4}$/.test(el.value)) el.value += '-';
  el.classList.remove('invalid');
});

document.addEventListener('focusout', (e) => {
  const el = e.target;
  if (!el.classList?.contains('month-input')) return;
  const normalised = normaliseMonth(el.value);
  if (normalised) el.value = normalised;
  el.classList.toggle('invalid', !!el.value.trim() && !normalised);
});

function truncate(str, n) {
  if (!str || str.length <= n) return str;
  return str.slice(0, n).replace(/\s\S*$/, '') + '…';
}

// Small DOM helper: an element with a class and text (always textContent, never HTML).
// The .del-btn icon, shared by the Jobs cards and the Applied tab (dashboard.js). Inline
// SVG (not the 🗑 emoji) so the icon can take the hover colour via currentColor.
const TRASH_SVG =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
  'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>';

function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
const tabBtns = document.querySelectorAll('nav#tabs .tab');
const overviewSection = document.getElementById('overview-section');
const jobsSection = document.getElementById('jobs-section');
const appliedSection = document.getElementById('applied-section');
const profileSection = document.getElementById('profile-section');
const hdrJobsBtns = document.getElementById('hdr-jobs-btns');
let profileLoaded = false;

tabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    tabBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    overviewSection.hidden = tab !== 'overview';
    jobsSection.hidden = tab !== 'jobs';
    appliedSection.hidden = tab !== 'applied';
    profileSection.hidden = tab !== 'profile';
    hdrJobsBtns.style.display = (tab === 'jobs' || tab === 'overview') ? '' : 'none';
    if (tab === 'profile' && !profileLoaded) loadProfile();
    if (tab === 'profile') loadToWorkOn(); // counts move as new ads are scanned — always refresh
    if (tab === 'overview') loadOverview(); // always refresh: counts move as you apply
    if (tab === 'applied') loadAppliedPeriods(); // always refresh — cheap query, keeps it current
  });
});

function showTab(tab) {
  document.querySelector(`nav#tabs .tab[data-tab="${tab}"]`)?.click();
}

// Mark a job applied (stamps applied_at once, server-side). Shared by the Jobs cards and
// the Overview's Keep Applying cards. Throws on failure; the caller says so.
async function markApplied(jobId) {
  const res = await fetch(`${BACKEND}/jobs/${jobId}/status?profile_id=${PROFILE_ID}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'applied' }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
