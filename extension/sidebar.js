// Side panel: tabbed Jobs + Profile editor.
// Vanilla JS, no build step. Talks to the FastAPI backend on localhost:8000.

const BACKEND = 'http://localhost:8000';
const PROFILE_ID = 1;

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
const EXP_TYPE_LABELS    = { job: 'Job', internship: 'Internship', volunteer: 'Volunteer', project: 'Project' };

function fmtDate(ym) {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1)
    .toLocaleDateString('en-AU', { month: 'short', year: 'numeric' });
}

function truncate(str, n) {
  if (!str || str.length <= n) return str;
  return str.slice(0, n).replace(/\s\S*$/, '') + '…';
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
const tabBtns = document.querySelectorAll('nav#tabs .tab');
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
    jobsSection.hidden = tab !== 'jobs';
    appliedSection.hidden = tab !== 'applied';
    profileSection.hidden = tab !== 'profile';
    hdrJobsBtns.style.display = tab === 'jobs' ? '' : 'none';
    if (tab === 'profile' && !profileLoaded) loadProfile();
    if (tab === 'applied') loadApplied(); // always refresh — cheap query, keeps it current
  });
});

// ---------------------------------------------------------------------------
// Jobs tab
// ---------------------------------------------------------------------------
const jobStatusEl = document.getElementById('job-status');
const jobListEl = document.getElementById('job-list');

// A plain heading row inside #job-list, splitting the flat list into the two
// groups that actually matter for "what can I act on right now": jobs with a
// cover letter ready to copy and submit, vs everything else still waiting on
// one. Score tier still colors each individual card within its group (see
// renderJob) — this is the coarser, primary grouping on top of that.
function renderSectionHeader(text, kind) {
  const li = document.createElement('li');
  li.className = `section-header ${kind}`;
  li.textContent = text;
  return li;
}

// Jobs captured but still waiting on the LLM pass have no match row, so /jobs
// can't list them — show a count under the list instead. Separate element from
// the <ul> so it can update mid-scan without re-rendering (and collapsing) cards.
const analysingEl = document.getElementById('analysing-row');

async function refreshPendingCount() {
  try {
    const res = await fetch(`${BACKEND}/jobs/pending-count?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { pending } = await res.json();
    if (!pending) { analysingEl.hidden = true; return; }
    analysingEl.textContent = `⏳ ${pending} more job app${pending === 1 ? '' : 's'} scanned — analysing…`;
    analysingEl.hidden = false;
  } catch {
    analysingEl.hidden = true;
  }
}

async function loadJobs() {
  refreshPendingCount();
  jobStatusEl.textContent = 'Loading…';
  jobListEl.innerHTML = '';
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}`);
    jobs = await res.json();
  } catch {
    jobStatusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }
  if (!Array.isArray(jobs) || jobs.length === 0) {
    jobStatusEl.textContent = 'No matched jobs yet. Browse Seek with the extension active to capture listings.';
    return;
  }
  jobStatusEl.textContent = ''; // status line is only for loading/error/empty states

  const ready = [];
  const waiting = [];
  const longTail = [];
  for (const job of jobs) {
    const tier = tierOf(job.score);
    if (coverLetterState(job) === 'ready') {
      ready.push(job); // a cover letter exists regardless of score/tier — always "ready"
    } else if (tier === 'hidden') {
      longTail.push(job);
    } else {
      waiting.push(job);
    }
  }

  if (ready.length) {
    jobListEl.appendChild(renderSectionHeader(`✅ Ready to Apply (${ready.length})`, 'ready'));
    for (const job of ready) jobListEl.appendChild(renderJob(job, tierOf(job.score)));
  }
  if (waiting.length || longTail.length) {
    jobListEl.appendChild(renderSectionHeader(`⏳ Waiting on Cover Letter (${waiting.length + longTail.length})`, 'waiting'));
    for (const job of waiting) jobListEl.appendChild(renderJob(job, tierOf(job.score)));
    if (longTail.length) jobListEl.appendChild(renderFold(longTail));
  }

  maybeShowSuggestions();
}

// Long-tail (<60) jobs aren't rendered individually up front — just a count.
// The real rows are built lazily on first click (so a backlog of 50 low
// scorers doesn't cost render time until asked for); after that, the fold
// bar just toggles their visibility back and forth.
function renderFold(jobs) {
  const li = document.createElement('li');
  li.className = 'fold';
  const showText = `${jobs.length} other role${jobs.length === 1 ? '' : 's'} — click to show`;
  const hideText = `▲ Hide ${jobs.length} other role${jobs.length === 1 ? '' : 's'}`;
  li.textContent = showText;

  let rows = null;
  let expanded = false;

  li.addEventListener('click', () => {
    if (!rows) {
      rows = jobs.map(job => renderJob(job, 'amber'));
      for (const row of rows) jobListEl.insertBefore(row, li);
    }
    expanded = !expanded;
    for (const row of rows) row.style.display = expanded ? '' : 'none';
    li.textContent = expanded ? hideText : showText;
  });

  return li;
}

// Cover-letter card state (docs/extension-revamp-plan.md §5.1):
//  - 'ready'   letter generated, has_cover_letter is true
//  - 'pending' score >= autoLetterMin, idle loop hasn't reached it yet
//  - 'none'    below autoLetterMin and no letter — no collapsed-card affordance;
//              a manual force-generate lives only in the expanded detail view
function coverLetterState(job) {
  if (job.has_cover_letter) return 'ready';
  if (job.score != null && job.score >= autoLetterMin) return 'pending';
  return 'none';
}

function renderJob(job, tier) {
  const li = document.createElement('li');
  li.dataset.jobId = job.job_id;
  if (tier) li.dataset.tier = tier;

  const row = document.createElement('div');
  row.className = 'job-row';

  const title = document.createElement('span');
  title.className = 'job-title';
  title.textContent = job.title || '(untitled)';
  row.appendChild(title);

  if (job.score != null) {
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = Math.round(job.score);
    row.appendChild(score);
  }

  const clState = coverLetterState(job);
  if (clState === 'ready') {
    const badge = document.createElement('span');
    badge.className = 'cl-badge ready';
    badge.textContent = '✅ Ready';
    row.appendChild(badge);
  } else if (clState === 'pending') {
    const badge = document.createElement('span');
    badge.className = 'cl-badge pending';
    badge.textContent = 'Cover letter pending…';
    row.appendChild(badge);
  }

  const applyBtn = document.createElement('button');
  applyBtn.className = 'apply-btn' + (job.status === 'applied' ? ' applied' : '');
  applyBtn.title = job.status === 'applied' ? 'Applied' : 'Mark Applied';
  applyBtn.textContent = job.status === 'applied' ? '✓ Applied' : 'Mark Applied';
  applyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (job.status === 'applied') return;
    applyBtn.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}/status?profile_id=${PROFILE_ID}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'applied' }),
      });
      if (!res.ok) throw new Error();
      job.status = 'applied';
      applyBtn.className = 'apply-btn applied';
      applyBtn.textContent = '✓ Applied';
      applyBtn.title = 'Applied';
    } catch {
      alert('Could not mark applied — is the backend running?');
    } finally {
      applyBtn.disabled = false;
    }
  });
  row.appendChild(applyBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'del-btn';
  delBtn.title = 'Delete';
  // Inline SVG (not the 🗑 emoji) so the icon can take the hover colour via currentColor.
  delBtn.innerHTML =
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>';
  delBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${job.title}"?`)) return;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}`, { method: 'DELETE' });
      if (res.ok) li.remove();
    } catch {
      alert('Delete failed — is the backend running?');
    }
  });
  row.appendChild(delBtn);

  li.appendChild(row);

  const meta = document.createElement('div');
  meta.className = 'job-meta';
  const metaParts = [job.company, job.location].filter(Boolean);
  if (job.extracted_at) {
    const d = new Date(job.extracted_at);
    metaParts.push(d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' }));
  }
  meta.textContent = metaParts.join(' · ') || '—';
  li.appendChild(meta);

  // Gold- and blue-tier cards show a reasoning snippet + top skill chips inline, no
  // click needed — everything else stays click-to-expand as before.
  if ((tier === 'blue' || tier === 'gold') && (job.reasoning || job.top_skills?.length)) {
    const preview = document.createElement('div');
    preview.className = 'job-preview';
    if (job.reasoning) preview.appendChild(document.createTextNode(truncate(job.reasoning, 90)));
    if (job.top_skills?.length) {
      const chips = document.createElement('div');
      chips.className = 'chips';
      for (const skill of job.top_skills.slice(0, 3)) {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.textContent = skill;
        chips.appendChild(chip);
      }
      preview.appendChild(chips);
    }
    li.appendChild(preview);
  }

  let expanded = false;
  let detailEl = null;
  li.addEventListener('click', async (ev) => {
    if (ev.target.tagName === 'A' || ev.target.tagName === 'TEXTAREA') return;
    expanded = !expanded;
    if (!detailEl) {
      detailEl = document.createElement('div');
      detailEl.className = 'job-detail';
      detailEl.textContent = 'Loading…';
      li.appendChild(detailEl);
      await fillDetail(detailEl, job);
    }
    detailEl.style.display = expanded ? 'block' : 'none';
  });

  return li;
}

async function fillDetail(detailEl, job) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`);
    const data = await res.json();
    detailEl.innerHTML = '';

    const link = document.createElement('a');
    link.href = data.url;
    link.textContent = 'Open on Seek ↗';
    link.addEventListener('click', (e) => {
      e.preventDefault();
      chrome.tabs.create({ url: data.url });
    });
    detailEl.appendChild(link);

    const cl = data.cover_letter;
    if (cl?.generated_content) {
      renderCoverLetterEditor(detailEl, job.job_id, cl);
    } else if (job.score != null && job.score >= autoLetterMin) {
      const note = document.createElement('div');
      note.style.cssText = 'margin-top:6px;color:#6b7280;';
      note.textContent = 'Cover letter pending — the idle loop will generate it shortly.';
      detailEl.appendChild(note);
    } else {
      const forceBtn = document.createElement('button');
      forceBtn.className = 'btn btn-sm';
      forceBtn.style.marginTop = '6px';
      forceBtn.textContent = 'Generate cover letter anyway';
      forceBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        forceBtn.disabled = true;
        forceBtn.textContent = 'Generating…';
        try {
          const r = await fetch(`${BACKEND}/jobs/${job.job_id}/regenerate?profile_id=${PROFILE_ID}`, { method: 'POST' });
          if (!r.ok) throw new Error();
          forceBtn.textContent = 'Queued — will appear when ready.';
        } catch {
          forceBtn.textContent = 'Generate cover letter anyway';
          forceBtn.disabled = false;
        }
      });
      detailEl.appendChild(forceBtn);
    }
  } catch {
    detailEl.textContent = 'Could not load detail.';
  }
}

// Cover-letter editor: Copy + editable textarea that saves via
// PATCH /jobs/{id}/cover-letter (shared with the Quick-Apply overlay on Seek).
function renderCoverLetterEditor(container, jobId, cl) {
  const wrap = document.createElement('div');
  wrap.style.marginTop = '6px';

  const badge = document.createElement('div');
  badge.style.cssText = 'color:#059669;font-weight:600;font-size:11px;margin-bottom:4px;';
  badge.textContent = '✅ Cover letter ready';
  wrap.appendChild(badge);

  const textarea = document.createElement('textarea');
  textarea.value = cl.edited_content || cl.generated_content || '';
  wrap.appendChild(textarea);

  const actions = document.createElement('div');
  actions.className = 'cl-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-sm';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(textarea.value);
      copyBtn.textContent = 'Copied ✓';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    } catch { /* clipboard permission denied — ignore */ }
  });
  actions.appendChild(copyBtn);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn btn-sm';
  saveBtn.textContent = 'Save edits';
  saveBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const res = await fetch(`${BACKEND}/jobs/${jobId}/cover-letter?profile_id=${PROFILE_ID}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_content: textarea.value }),
      });
      if (!res.ok) throw new Error();
      saveBtn.textContent = 'Saved ✓';
    } catch {
      saveBtn.textContent = 'Save failed';
    } finally {
      setTimeout(() => { saveBtn.textContent = 'Save edits'; saveBtn.disabled = false; }, 1500);
    }
  });
  actions.appendChild(saveBtn);

  wrap.appendChild(actions);
  container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// Applied tab — Centrelink mutual-obligation reporting
// ---------------------------------------------------------------------------
const appliedStatusEl = document.getElementById('applied-status');
const appliedListEl = document.getElementById('applied-list');
let lastAppliedJobs = [];

async function loadApplied() {
  appliedStatusEl.textContent = 'Loading…';
  appliedListEl.innerHTML = '';
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}&min_score=0&status=applied`);
    jobs = await res.json();
  } catch {
    appliedStatusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }
  lastAppliedJobs = Array.isArray(jobs) ? jobs : [];
  if (!lastAppliedJobs.length) {
    appliedStatusEl.textContent = 'No applications logged yet. Use "Mark Applied" on a job in the Jobs tab.';
    return;
  }
  appliedStatusEl.textContent = `${lastAppliedJobs.length} application(s).`;
  for (const job of lastAppliedJobs) appliedListEl.appendChild(renderAppliedRow(job));
}

function renderAppliedRow(job) {
  const li = document.createElement('li');
  const row = document.createElement('div');
  row.className = 'job-row';

  const title = document.createElement('span');
  title.className = 'job-title';
  title.textContent = job.title || '(untitled)';
  row.appendChild(title);

  if (job.applied_at) {
    const date = document.createElement('span');
    date.className = 'applied-date';
    date.textContent = new Date(job.applied_at).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
    row.appendChild(date);
  }
  li.appendChild(row);

  const meta = document.createElement('div');
  meta.className = 'job-meta';
  meta.textContent = [job.company, job.location].filter(Boolean).join(' · ') || '—';
  li.appendChild(meta);

  li.addEventListener('click', () => chrome.tabs.create({ url: job.url }));
  return li;
}

function csvEscape(val) {
  const s = String(val ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

document.getElementById('export-applied-btn').addEventListener('click', () => {
  const rows = [['Date Applied', 'Job Title', 'Employer', 'Location', 'Source URL', 'Screenshot Evidence']];
  for (const job of lastAppliedJobs) {
    rows.push([
      job.applied_at ? job.applied_at.slice(0, 10) : '',
      job.title || '',
      job.company || '',
      job.location || '',
      job.url || '',
      job.screenshot_taken_at ? job.screenshot_taken_at.slice(0, 10) : 'No',
    ]);
  }
  const csv = rows.map(r => r.map(csvEscape).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `applied-jobs-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

// ---------------------------------------------------------------------------
// Suggested searches — free (non-LLM) phrase mining from good matches (§7)
// ---------------------------------------------------------------------------
const SUGGESTION_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// Builds the Seek search a suggestion opens. Keeps the SEO-slug path form
// ("/data-analyst-jobs") and adds the location as ?where=, because that is the
// combination content_script.js's currentSearchQuery() already normalises back
// to the bare phrase — so a job found this way is attributed to "data analyst",
// not "data analyst brisbane", and search-performance stats stay comparable
// across locations.
function seekSearchUrl(phrase, searchLocation) {
  const slug = phrase.toLowerCase().trim().replace(/\s+/g, '-') + '-jobs';
  const where = (searchLocation || '').trim();
  return `https://au.seek.com/${slug}`
    + (where ? `?where=${encodeURIComponent(where)}` : '');
}

// Renders whatever the backend ranked. `forceLlm` is only set by the explicit
// "Refresh now" button, which re-runs the LLM layer regardless of its cache
// and regardless of the saved preference.
async function maybeShowSuggestions({ forceLlm = false, ignoreCooldown = false } = {}) {
  const banner = document.getElementById('suggestion-banner');
  try {
    if (!ignoreCooldown) {
      const stored = await chrome.storage.local.get('suggestionsDismissedUntil');
      if (stored.suggestionsDismissedUntil && Date.now() < stored.suggestionsDismissedUntil) return;
    }

    let url = `${BACKEND}/jobs/suggested-searches?profile_id=${PROFILE_ID}`;
    if (forceLlm) url += '&use_llm=true';
    const res = await fetch(url);
    if (!res.ok) return;
    // Named searchLocation, not location — `location` would shadow
    // window.location inside this function.
    const { suggestions, llm_used, location: searchLocation } = await res.json();
    if (!suggestions?.length) return;

    const scope = searchLocation ? ` in ${searchLocation}` : '';
    banner.querySelector('span').textContent =
      `${llm_used ? '✨' : '💡'} Try searching${scope}:`;
    const linksEl = document.getElementById('sugg-links');
    linksEl.innerHTML = '';
    suggestions.forEach((phrase, i) => {
      const a = document.createElement('a');
      a.textContent = `"${phrase}"`;
      a.title = `Search Seek for "${phrase}"${scope}`;
      a.addEventListener('click', () => {
        chrome.tabs.create({ url: seekSearchUrl(phrase, searchLocation) }); // normal user-initiated open, not a fetch — still 0-hop
      });
      linksEl.appendChild(a);
      if (i < suggestions.length - 1) linksEl.appendChild(document.createTextNode(' · '));
    });
    banner.hidden = false;
  } catch { /* suggestions are a nicety — fail silently */ }
}

document.getElementById('sugg-close').addEventListener('click', async () => {
  document.getElementById('suggestion-banner').hidden = true;
  await chrome.storage.local.set({ suggestionsDismissedUntil: Date.now() + SUGGESTION_COOLDOWN_MS });
});

// ---------------------------------------------------------------------------
// Scan Page (1-hop rule: links from a page the user opened, ≥5s apart, capped)
// ---------------------------------------------------------------------------
// MAX_SCAN_PAGES is now mostly a safety ceiling, not the primary cost control —
// WEAK_STREAK_LIMIT below does the real work of cutting a bad search short.
const MAX_SCAN_PAGES = 10;
const SCAN_DELAY_MS = 5000;

// Early-exit: if this many consecutive scraped jobs come back weak on the
// cheap quick-screen (below WEAK_SCORE_THRESHOLD, same 0-100 scale as a full
// match), stop opening further links on this page — this search query isn't
// yielding relevant results, so paying to scrape (and later extract+match)
// more of it is wasted. Quick-screen is used here specifically because it's
// cheap enough to plausibly keep pace with scraping a new tab every 5s; the
// full match score requires extraction first and can take far longer per job.
const WEAK_SCORE_THRESHOLD = 50;
const WEAK_STREAK_LIMIT = 3;

const scanBtn = document.getElementById('scan-btn');
const scanLogEl = document.getElementById('scanlog');
let scanning = false;

// Per-page progress goes to the console only — the list itself is the progress
// display (the "N more scanned — analysing" row at the bottom). scanNotice is
// for the few messages the user needs to act on (wrong page, nothing found…).
function scanLog(msg) {
  console.log(`[scan] ${msg}`);
}

function scanNotice(msg) {
  scanLog(msg);
  scanLogEl.textContent = msg;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// Runs inside the Seek page — collect all /job/<id> hrefs already on it.
function pageCollectJobLinks() {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/job/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/job\/(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    out.push(href.startsWith('http') ? href : location.origin + href);
  }
  return out;
}

// Runs inside a Seek detail page — scrape the job data.
async function pageScrapeDetail() {
  const m = location.pathname.match(/\/job\/(\d+)/);
  if (!m) return null;
  const start = Date.now();
  let descEl = null;
  while (Date.now() - start < 8000) {
    descEl = document.querySelector('[data-automation="jobAdDetails"]');
    if (descEl && descEl.innerText.trim()) break;
    await new Promise(r => setTimeout(r, 400));
  }
  const titleEl = document.querySelector('[data-automation="job-detail-title"]');
  return {
    source_job_id: m[1],
    url: location.href,
    title: (titleEl?.innerText || document.title || 'Untitled').replace(/\s*[|-]\s*SEEK.*$/i, '').trim(),
    raw_description: descEl?.innerText.trim() || null,
  };
}

function waitForTabComplete(tabId, timeoutMs) {
  return new Promise(resolve => {
    const timer = setTimeout(() => { chrome.tabs.onUpdated.removeListener(onUpd); resolve(); }, timeoutMs);
    function onUpd(id, info) {
      if (id === tabId && info.status === 'complete') {
        clearTimeout(timer); chrome.tabs.onUpdated.removeListener(onUpd); resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(onUpd);
  });
}

async function injectFn(tabId, func) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func });
  return result;
}

// Returns the ingested listing's internal job_id on success, or null on
// failure — the caller needs the id to kick off a quick-screen check right
// after scraping (see scanPage's early-exit loop below).
async function ingestListing(listing) {
  try {
    const res = await fetch(`${BACKEND}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listings: [listing], profile_id: PROFILE_ID }),
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.job_ids?.[0] ?? null;
  } catch { return null; }
}

async function scanPage() {
  if (scanning) return;
  scanning = true;
  scanBtn.disabled = true;
  scanLogEl.textContent = '';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !/^https:\/\/(au\.seek\.com|www\.seek\.com\.au)\/[^?#]*jobs/i.test(tab.url || '')) {
      scanNotice('Open a Seek search results page in this tab first, then click Scan Page.');
      return;
    }
    let allUrls;
    try { allUrls = await injectFn(tab.id, pageCollectJobLinks); }
    catch (e) { scanNotice(`Could not read the page: ${e.message}`); return; }

    allUrls = allUrls || [];
    if (!allUrls.length) { scanNotice('No job links found on this page.'); return; }

    // Filter out URLs whose source_job_id is already in the database.
    let knownIds = new Set();
    try {
      const r = await fetch(`${BACKEND}/jobs/known-ids`);
      if (r.ok) knownIds = new Set((await r.json()).source_ids);
    } catch { /* backend down — proceed without filtering */ }

    function extractJobId(url) {
      const m = url.match(/\/job\/(\d+)/);
      return m ? m[1] : null;
    }

    const newUrls = allUrls.filter(u => !knownIds.has(extractJobId(u)));
    const skipped = allUrls.length - newUrls.length;
    const urls = newUrls.slice(0, MAX_SCAN_PAGES);

    if (skipped) scanLog(`Skipped ${skipped} already-captured job(s).`);
    if (!urls.length) { scanNotice('All jobs on this page already captured.'); return; }
    scanLog(`Found ${newUrls.length} new link(s); scraping up to ${urls.length} (5s apart), screening as we go…`);

    // Scraping (the "producer") and quick-screening (the "consumer") run
    // concurrently rather than one-at-a-time: scraping doesn't wait on a job's
    // screen result before opening the next tab, since screening (an LLM call)
    // is far slower than the 5s scrape pacing. The consumer works through
    // scraped jobs strictly in scrape order (so "3 in a row" means what it
    // sounds like) and can fall behind — that's expected. When it sees
    // WEAK_STREAK_LIMIT consecutive weak scores, it flips `abort`, which the
    // producer checks before opening its next tab.
    const screenQueue = [];   // { label, jobId } pushed by the producer
    let producing = true;     // producer still has links left to try
    let abort = false;        // consumer decided to stop this search early

    async function produce() {
      for (let i = 0; i < urls.length; i++) {
        if (abort) { scanLog('Stopping further scraping — search abandoned early.'); break; }
        const label = `(${i + 1}/${urls.length})`;
        scanLog(`${label} opening job page…`);
        let bgTab;
        try {
          bgTab = await chrome.tabs.create({ url: urls[i], active: false });
          await waitForTabComplete(bgTab.id, 15000);
        } catch (e) {
          scanLog(`${label} could not open tab: ${e.message}`);
          if (bgTab) await chrome.tabs.remove(bgTab.id).catch(() => {});
          continue;
        }

        let payload = null;
        try { payload = await injectFn(bgTab.id, pageScrapeDetail); }
        catch (e) { scanLog(`${label} could not scrape: ${e.message}`); }
        finally { await chrome.tabs.remove(bgTab.id).catch(() => {}); }

        if (payload?.source_job_id) {
          const jobId = await ingestListing(payload);
          const desc = payload.raw_description ? `${payload.raw_description.length} chars` : 'NO DESCRIPTION';
          if (jobId != null) {
            scanLog(`${label} captured ✓ (${desc})`);
            screenQueue.push({ label, jobId });
            refreshPendingCount();
          } else {
            scanLog(`${label} backend error ✗`);
          }
        } else {
          scanLog(`${label} no data scraped ✗`);
        }
        if (i < urls.length - 1) await sleep(SCAN_DELAY_MS);
      }
      producing = false;
    }

    async function consume() {
      let consecutiveWeak = 0;
      let idx = 0;
      while (true) {
        if (idx < screenQueue.length) {
          const { label, jobId } = screenQueue[idx++];
          try {
            const res = await fetch(`${BACKEND}/jobs/${jobId}/quick-screen?profile_id=${PROFILE_ID}`, { method: 'POST' });
            if (!res.ok) { scanLog(`${label} quick-screen failed (${res.status})`); continue; }
            const { score } = await res.json();
            if (score == null) { scanLog(`${label} quick-screen: no score (already processed or errored)`); continue; }
            const weak = score < WEAK_SCORE_THRESHOLD;
            scanLog(`${label} quick-screen: ${score}/100${weak ? ' (weak)' : ''}`);
            consecutiveWeak = weak ? consecutiveWeak + 1 : 0;
            if (consecutiveWeak >= WEAK_STREAK_LIMIT) {
              abort = true;
              scanNotice(`${WEAK_STREAK_LIMIT} weak results in a row — this search doesn't look productive. `
                + 'Stopped early; try a different search.');
            }
          } catch (e) {
            scanLog(`${label} quick-screen error: ${e.message}`);
          }
        } else if (!producing) {
          break; // producer is done and we've drained everything it added
        } else {
          await sleep(500); // wait for the producer to add more
        }
      }
    }

    await Promise.all([produce(), consume()]);

    scanLog(abort ? 'Scan stopped early — refreshing matches…' : 'Scan complete — refreshing matches…');
    loadJobs();
  } finally {
    scanning = false;
    scanBtn.disabled = false;
  }
}

scanBtn.addEventListener('click', scanPage);
document.getElementById('refresh-btn').addEventListener('click', loadJobs);

// ---------------------------------------------------------------------------
// Personalise metrics — collapsible panel of user-tunable thresholds
// ---------------------------------------------------------------------------
const personaliseToggle = document.getElementById('personalise-toggle');
const personalisePanel = document.getElementById('personalise-panel');
const autoLetterInput = document.getElementById('auto-letter-min');
const autoLetterSaveBtn = document.getElementById('auto-letter-save');
const llmSuggestCheckbox = document.getElementById('llm-suggest');
const llmSuggestRefreshBtn = document.getElementById('llm-suggest-refresh');

personaliseToggle.addEventListener('click', () => {
  const open = personalisePanel.hidden; // about to open
  personalisePanel.hidden = !open;
  personaliseToggle.setAttribute('aria-expanded', String(open));
});

// Resolves once the stored preferences (if reachable) have been applied, so the
// first loadJobs() can classify "pending" letters against the real threshold.
async function loadPreferences() {
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`);
    if (!res.ok) return;
    const prefs = await res.json();
    if (Number.isInteger(prefs.auto_cover_letter_min_score)) {
      autoLetterMin = prefs.auto_cover_letter_min_score;
      autoLetterInput.value = autoLetterMin;
    }
    llmSuggestCheckbox.checked = prefs.llm_search_suggestions === true;
  } catch { /* backend down — keep defaults; loadJobs shows its own error */ }
}

autoLetterSaveBtn.addEventListener('click', async () => {
  const value = parseInt(autoLetterInput.value, 10);
  if (!Number.isInteger(value) || value < 0 || value > 100) {
    autoLetterInput.value = autoLetterMin;
    return;
  }
  autoLetterSaveBtn.disabled = true;
  autoLetterSaveBtn.textContent = '…';
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_cover_letter_min_score: value }),
    });
    if (!res.ok) throw new Error();
    autoLetterMin = value;
    autoLetterSaveBtn.textContent = 'Saved ✓';
    loadJobs(); // cards move between "pending" and "no letter" states
  } catch {
    autoLetterSaveBtn.textContent = 'Error';
  }
  setTimeout(() => { autoLetterSaveBtn.textContent = 'Save'; autoLetterSaveBtn.disabled = false; }, 1500);
});

// Layer 4 opt-in. Unticked, /jobs/suggested-searches never reaches the LLM;
// ticked, it spends one cached call. Ticking it also refreshes the banner
// immediately so the effect is visible rather than deferred to the next load.
llmSuggestCheckbox.addEventListener('change', async () => {
  const enabled = llmSuggestCheckbox.checked;
  llmSuggestCheckbox.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm_search_suggestions: enabled }),
    });
    if (!res.ok) throw new Error();
    await maybeShowSuggestions({ ignoreCooldown: true });
  } catch {
    llmSuggestCheckbox.checked = !enabled; // revert — the setting didn't stick
  } finally {
    llmSuggestCheckbox.disabled = false;
  }
});

// Forces a refine even when the cache is still warm, and even when the
// checkbox is off (a one-off look, without turning the feature on).
llmSuggestRefreshBtn.addEventListener('click', async () => {
  llmSuggestRefreshBtn.disabled = true;
  llmSuggestRefreshBtn.textContent = '…';
  try {
    await maybeShowSuggestions({ forceLlm: true, ignoreCooldown: true });
    llmSuggestRefreshBtn.textContent = 'Done ✓';
  } catch {
    llmSuggestRefreshBtn.textContent = 'Error';
  }
  setTimeout(() => {
    llmSuggestRefreshBtn.textContent = 'Refresh now';
    llmSuggestRefreshBtn.disabled = false;
  }, 1500);
});

// Layer 3: which searches actually paid off. Volume is also the LLM cost of a
// search, so a big-volume/low-yield row is the one worth retiring.
const searchPerfEl = document.getElementById('search-perf');
document.getElementById('search-perf-btn').addEventListener('click', async () => {
  const btn = document.getElementById('search-perf-btn');
  if (!searchPerfEl.hidden) {
    searchPerfEl.hidden = true;
    btn.textContent = 'Show';
    return;
  }
  btn.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/jobs/search-performance?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { performance, underperforming } = await res.json();
    if (!performance.length) {
      searchPerfEl.textContent =
        'No data yet — searches are tracked from the next results page you open.';
    } else {
      const weak = new Set(underperforming);
      searchPerfEl.innerHTML = '';
      const table = document.createElement('table');
      const head = table.insertRow();
      ['Search', 'Jobs', 'Good', 'Yield'].forEach((label, i) => {
        const th = document.createElement('th');
        th.textContent = label;
        if (i > 0) th.className = 'num';
        head.appendChild(th);
      });
      for (const row of performance) {
        const tr = table.insertRow();
        if (weak.has(row.query)) tr.className = 'weak';
        tr.insertCell().textContent = row.query;
        [row.volume, row.hits, `${Math.round(row.yield * 100)}%`].forEach((v) => {
          const td = tr.insertCell();
          td.textContent = v;
          td.className = 'num';
        });
      }
      searchPerfEl.appendChild(table);
    }
    searchPerfEl.hidden = false;
    btn.textContent = 'Hide';
  } catch {
    searchPerfEl.textContent = 'Could not load search performance.';
    searchPerfEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('bulk-delete-btn').addEventListener('click', async () => {
  const score = parseFloat(document.getElementById('bulk-score').value);
  if (isNaN(score)) return;
  const btn = document.getElementById('bulk-delete-btn');
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const res = await fetch(`${BACKEND}/jobs?below_score=${score}&profile_id=${PROFILE_ID}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      btn.textContent = `Hid ${data.hidden ?? data.deleted}`;
      setTimeout(() => { btn.textContent = 'Hide'; btn.disabled = false; }, 2000);
      loadJobs();
    } else {
      throw new Error();
    }
  } catch {
    btn.textContent = 'Error';
    setTimeout(() => { btn.textContent = 'Hide'; btn.disabled = false; }, 2000);
  }
});

// ---------------------------------------------------------------------------
// Profile tab — dynamic cards
// ---------------------------------------------------------------------------
const profileMsg = document.getElementById('profile-msg');

function showMsg(text, type) {
  profileMsg.textContent = text;
  profileMsg.className = type;
  setTimeout(() => { profileMsg.className = ''; }, 3000);
}

// -- Qualification card --

function makeQualCard(data = {}, isNew = false) {
  const card = document.createElement('div');
  card.className = 'entry-card';

  // Summary panel
  const summaryEl = document.createElement('div');
  summaryEl.className = 'card-summary';
  summaryEl.innerHTML = `
    <div class="summary-header">
      <span class="summary-title"></span>
      <span class="type-badge"></span>
    </div>
    <div class="summary-meta"></div>
    <div class="card-actions">
      <button class="btn btn-sm edit-btn">Edit</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  // Form panel
  const formEl = document.createElement('div');
  formEl.className = 'card-form';
  formEl.innerHTML = `
    <label class="field"><span>Type</span>
      <select class="f-type">
        <option value="degree">Degree</option>
        <option value="certificate">Certificate</option>
        <option value="diploma">Diploma</option>
        <option value="other">Other</option>
      </select>
    </label>
    <label class="field"><span>Title</span>
      <input class="f-title" type="text" placeholder="e.g. Bachelor of Computer Science">
    </label>
    <label class="field"><span>Institution</span>
      <input class="f-institution" type="text" placeholder="University / provider">
    </label>
    <div class="two-col">
      <label class="field"><span>Field of Study</span>
        <input class="f-field" type="text" placeholder="e.g. Computer Science">
      </label>
      <label class="field"><span>Grade</span>
        <input class="f-grade" type="text" placeholder="e.g. Distinction">
      </label>
    </div>
    <div class="two-col">
      <label class="field"><span>Start</span><input class="f-start" type="month"></label>
      <label class="field"><span>End</span><input class="f-end" type="month"></label>
    </div>
    <label class="field"><span>Status</span>
      <select class="f-status">
        <option value="completed">Completed</option>
        <option value="in_progress">In Progress</option>
        <option value="withdrawn">Withdrawn</option>
      </select>
    </label>
    <div class="card-actions">
      <button class="btn btn-sm done-btn" style="flex:1">Done</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  card.appendChild(summaryEl);
  card.appendChild(formEl);

  // Populate form
  if (data.qualification_type) formEl.querySelector('.f-type').value = data.qualification_type;
  if (data.title)              formEl.querySelector('.f-title').value = data.title;
  if (data.institution)        formEl.querySelector('.f-institution').value = data.institution;
  if (data.field_of_study)     formEl.querySelector('.f-field').value = data.field_of_study;
  if (data.grade)              formEl.querySelector('.f-grade').value = data.grade;
  if (data.start_date)         formEl.querySelector('.f-start').value = data.start_date;
  if (data.end_date)           formEl.querySelector('.f-end').value = data.end_date;
  if (data.status)             formEl.querySelector('.f-status').value = data.status;

  function updateSummary() {
    const type   = formEl.querySelector('.f-type').value;
    const title  = formEl.querySelector('.f-title').value || '(untitled)';
    const inst   = formEl.querySelector('.f-institution').value;
    const field  = formEl.querySelector('.f-field').value;
    const start  = formEl.querySelector('.f-start').value;
    const end    = formEl.querySelector('.f-end').value;
    const status = formEl.querySelector('.f-status').value;

    summaryEl.querySelector('.summary-title').textContent = title;
    summaryEl.querySelector('.type-badge').textContent = QUAL_TYPE_LABELS[type] || type;

    const metaEl = summaryEl.querySelector('.summary-meta');
    metaEl.innerHTML = '';
    const line1 = [inst, field].filter(Boolean).join(' · ');
    if (line1) { const d = document.createElement('div'); d.textContent = line1; metaEl.appendChild(d); }
    const dateParts = [start ? fmtDate(start) : '', end ? fmtDate(end) : (status === 'in_progress' ? 'Present' : '')].filter(Boolean);
    const line2 = [dateParts.join(' – '), QUAL_STATUS_LABELS[status] || status].filter(Boolean).join(' · ');
    if (line2) { const d = document.createElement('div'); d.textContent = line2; metaEl.appendChild(d); }
  }

  function showForm() { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  function showSummary() { updateSummary(); formEl.style.display = 'none'; summaryEl.style.display = ''; }

  summaryEl.querySelector('.edit-btn').addEventListener('click', showForm);
  formEl.querySelector('.done-btn').addEventListener('click', showSummary);
  card.querySelectorAll('.card-rm-btn').forEach(b => b.addEventListener('click', () => card.remove()));

  if (isNew) { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  else       { updateSummary(); summaryEl.style.display = ''; formEl.style.display = 'none'; }

  return card;
}

function readQualCard(card) {
  return {
    qualification_type: card.querySelector('.f-type').value,
    title:              card.querySelector('.f-title').value.trim(),
    institution:        card.querySelector('.f-institution').value.trim() || null,
    field_of_study:     card.querySelector('.f-field').value.trim() || null,
    grade:              card.querySelector('.f-grade').value.trim() || null,
    start_date:         card.querySelector('.f-start').value || null,
    end_date:           card.querySelector('.f-end').value || null,
    status:             card.querySelector('.f-status').value,
  };
}

// -- Experience card --

function makeExpCard(data = {}, isNew = false) {
  const card = document.createElement('div');
  card.className = 'entry-card';

  // Summary panel
  const summaryEl = document.createElement('div');
  summaryEl.className = 'card-summary';
  summaryEl.innerHTML = `
    <div class="summary-header">
      <span class="summary-title"></span>
      <span class="type-badge"></span>
    </div>
    <div class="summary-meta"></div>
    <div class="card-actions">
      <button class="btn btn-sm edit-btn">Edit</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  // Form panel
  const formEl = document.createElement('div');
  formEl.className = 'card-form';
  formEl.innerHTML = `
    <label class="field"><span>Type</span>
      <select class="f-type">
        <option value="job">Job</option>
        <option value="internship">Internship</option>
        <option value="volunteer">Volunteer</option>
        <option value="project">Project</option>
      </select>
    </label>
    <label class="field"><span>Title / Role</span>
      <input class="f-title" type="text" placeholder="e.g. Software Engineer">
    </label>
    <label class="field"><span>Organisation</span>
      <input class="f-org" type="text" placeholder="Company or project name">
    </label>
    <div class="two-col">
      <label class="field"><span>Start</span><input class="f-start" type="month"></label>
      <label class="field f-end-label"><span>End</span><input class="f-end" type="month"></label>
    </div>
    <div class="check-row">
      <input class="f-current" type="checkbox"><label>Current role</label>
    </div>
    <label class="field"><span>Description</span>
      <textarea class="f-desc" placeholder="Key responsibilities and achievements…"></textarea>
    </label>
    <label class="field"><span>Skills used (comma-separated)</span>
      <input class="f-skills" type="text" placeholder="Python, SQL, React…">
    </label>
    <div class="card-actions">
      <button class="btn btn-sm done-btn" style="flex:1">Done</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  card.appendChild(summaryEl);
  card.appendChild(formEl);

  const currentCb = formEl.querySelector('.f-current');
  const endLabel  = formEl.querySelector('.f-end-label');
  const endInput  = formEl.querySelector('.f-end');

  function toggleEnd() {
    endLabel.style.opacity = currentCb.checked ? '0.35' : '1';
    endInput.disabled = currentCb.checked;
  }
  currentCb.addEventListener('change', toggleEnd);

  // Populate form
  if (data.experience_type) formEl.querySelector('.f-type').value = data.experience_type;
  if (data.title)           formEl.querySelector('.f-title').value = data.title;
  if (data.organization)    formEl.querySelector('.f-org').value = data.organization;
  if (data.start_date)      formEl.querySelector('.f-start').value = data.start_date;
  if (data.end_date)        formEl.querySelector('.f-end').value = data.end_date;
  if (data.description)     formEl.querySelector('.f-desc').value = data.description;
  if (data.skills?.length)  formEl.querySelector('.f-skills').value = data.skills.join(', ');
  if (data.is_current)      { currentCb.checked = true; toggleEnd(); }

  function updateSummary() {
    const type   = formEl.querySelector('.f-type').value;
    const title  = formEl.querySelector('.f-title').value || '(untitled)';
    const org    = formEl.querySelector('.f-org').value;
    const start  = formEl.querySelector('.f-start').value;
    const end    = formEl.querySelector('.f-end').value;
    const isCur  = formEl.querySelector('.f-current').checked;
    const desc   = formEl.querySelector('.f-desc').value;

    summaryEl.querySelector('.summary-title').textContent = title;
    summaryEl.querySelector('.type-badge').textContent = EXP_TYPE_LABELS[type] || type;

    const metaEl = summaryEl.querySelector('.summary-meta');
    metaEl.innerHTML = '';
    if (org) { const d = document.createElement('div'); d.textContent = org; metaEl.appendChild(d); }
    const dateParts = [start ? fmtDate(start) : '', isCur ? 'Present' : (end ? fmtDate(end) : '')].filter(Boolean);
    if (dateParts.length) { const d = document.createElement('div'); d.textContent = dateParts.join(' – '); metaEl.appendChild(d); }
    if (desc) {
      const d = document.createElement('div');
      d.className = 'desc-preview';
      d.textContent = truncate(desc, 80);
      metaEl.appendChild(d);
    }
  }

  function showForm() { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  function showSummary() { updateSummary(); formEl.style.display = 'none'; summaryEl.style.display = ''; }

  summaryEl.querySelector('.edit-btn').addEventListener('click', showForm);
  formEl.querySelector('.done-btn').addEventListener('click', showSummary);
  card.querySelectorAll('.card-rm-btn').forEach(b => b.addEventListener('click', () => card.remove()));

  if (isNew) { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  else       { updateSummary(); summaryEl.style.display = ''; formEl.style.display = 'none'; }

  return card;
}

function readExpCard(card) {
  const isCurrent = card.querySelector('.f-current').checked;
  return {
    experience_type: card.querySelector('.f-type').value,
    title:           card.querySelector('.f-title').value.trim(),
    organization:    card.querySelector('.f-org').value.trim() || null,
    start_date:      card.querySelector('.f-start').value || null,
    end_date:        isCurrent ? null : (card.querySelector('.f-end').value || null),
    is_current:      isCurrent,
    description:     card.querySelector('.f-desc').value.trim() || null,
    skills:          card.querySelector('.f-skills').value.split(',').map(s => s.trim()).filter(Boolean),
  };
}

// -- Skills chips --

let skillsData = [];

function renderSkillPills() {
  const container = document.getElementById('skills-pills');
  container.innerHTML = '';
  for (const skill of skillsData) {
    const chip = document.createElement('span');
    chip.className = 'skill-chip';
    chip.appendChild(document.createTextNode(skill));
    const rm = document.createElement('button');
    rm.className = 'rm-skill';
    rm.textContent = '×';
    rm.title = 'Remove';
    rm.addEventListener('click', () => {
      skillsData = skillsData.filter(s => s !== skill);
      renderSkillPills();
    });
    chip.appendChild(rm);
    container.appendChild(chip);
  }
}

function addSkill(name) {
  const trimmed = name.trim();
  if (!trimmed || skillsData.includes(trimmed)) return false;
  skillsData.push(trimmed);
  renderSkillPills();
  return true;
}

// -- Load / Save --

async function loadProfile() {
  profileLoaded = true;
  try {
    const res = await fetch(`${BACKEND}/profile-ui/data`);
    if (res.status === 404) return; // no profile yet — blank form is fine
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    populateForm(await res.json());
  } catch (e) {
    showMsg(`Could not load profile: ${e.message}`, 'err');
  }
}

function populateForm(data) {
  const p = data.profile || {};
  document.getElementById('p-name').value            = p.name            || '';
  document.getElementById('p-email').value           = p.email           || '';
  document.getElementById('p-phone').value           = p.phone           || '';
  document.getElementById('p-location').value        = p.location        || '';
  document.getElementById('p-summary').value         = p.summary         || '';
  document.getElementById('p-target-role').value     = p.target_role     || '';
  document.getElementById('p-target-location').value = p.target_location || '';

  const qualsList = document.getElementById('quals-list');
  qualsList.innerHTML = '';
  for (const q of (data.qualifications || [])) qualsList.appendChild(makeQualCard(q, false));

  const expsList = document.getElementById('exps-list');
  expsList.innerHTML = '';
  for (const e of (data.experiences || [])) expsList.appendChild(makeExpCard(e, false));

  skillsData = data.skills || [];
  renderSkillPills();
}

async function saveProfile() {
  const btn = document.getElementById('save-profile-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    const quals = [...document.querySelectorAll('#quals-list .entry-card')]
      .map(readQualCard).filter(q => q.title);
    const exps = [...document.querySelectorAll('#exps-list .entry-card')]
      .map(readExpCard).filter(e => e.title);
    const skills = [...skillsData];

    const body = {
      profile: {
        name:            document.getElementById('p-name').value.trim(),
        email:           document.getElementById('p-email').value.trim(),
        phone:           document.getElementById('p-phone').value.trim()           || null,
        location:        document.getElementById('p-location').value.trim()        || null,
        summary:         document.getElementById('p-summary').value.trim()         || null,
        target_role:     document.getElementById('p-target-role').value.trim()     || null,
        target_location: document.getElementById('p-target-location').value.trim() || null,
      },
      qualifications: quals,
      experiences: exps,
      skills,
    };

    const res = await fetch(`${BACKEND}/profile-ui/data`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showMsg('Profile saved.', 'ok');
  } catch (e) {
    showMsg(`Save failed: ${e.message}`, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Profile';
  }
}

document.getElementById('add-qual-btn').addEventListener('click', () =>
  document.getElementById('quals-list').appendChild(makeQualCard({}, true)));

document.getElementById('add-exp-btn').addEventListener('click', () =>
  document.getElementById('exps-list').appendChild(makeExpCard({}, true)));

document.getElementById('save-profile-btn').addEventListener('click', saveProfile);

// Skill add UI
function confirmSkill() {
  const inp = document.getElementById('skill-input');
  if (addSkill(inp.value)) inp.value = '';
  inp.focus();
}
function cancelSkillInput() {
  document.getElementById('skill-input').value = '';
  document.getElementById('add-skill-row').style.display = 'none';
}

document.getElementById('add-skill-btn').addEventListener('click', () => {
  const row = document.getElementById('add-skill-row');
  row.style.display = 'flex';
  document.getElementById('skill-input').focus();
});
document.getElementById('skill-ok-btn').addEventListener('click', confirmSkill);
document.getElementById('skill-cancel-btn').addEventListener('click', cancelSkillInput);
document.getElementById('skill-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); confirmSkill(); }
  if (e.key === 'Escape') cancelSkillInput();
});

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

    const counts = [
      p.experiences?.length && `${p.experiences.length} role(s)`,
      p.qualifications?.length && `${p.qualifications.length} qualification(s)`,
      p.skills?.length && `${p.skills.length} skill(s)`,
    ].filter(Boolean);

    if (!p.name && !counts.length) {
      setImportStatus('Nothing extracted — page may not have rendered. Check browser console (F12).', '#d97706');
    } else {
      setImportStatus(`Imported: ${[p.name && 'name', ...counts].filter(Boolean).join(', ')}. Review & save.`, '#059669');
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
// SSE — live updates from the backend
// ---------------------------------------------------------------------------
let _eventsEverOpened = false;

function connectEvents() {
  let source;
  try {
    source = new EventSource(`${BACKEND}/events`);
  } catch {
    return; // backend not running; jobs tab will show its own error
  }

  source.onopen = () => {
    if (_eventsEverOpened) loadJobs(); // reconnect — reload to catch up on missed events
    _eventsEverOpened = true;
  };

  // New job scored → reload the jobs list so it appears
  source.addEventListener('job_processed', () => {
    loadJobs();
  });

  // Cover letter ready → update the badge and, if expanded, the editor in place.
  // A full reload is the simplest way to move the card between the "pending"
  // and "ready" badge states, and jobs lists are small enough that it's cheap.
  source.addEventListener('cover_letter_ready', () => {
    loadJobs();
  });

  source.onerror = () => {
    // EventSource auto-reconnects; onopen will fire again and trigger a reload
  };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
loadPreferences().then(loadJobs);
connectEvents();
