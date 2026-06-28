// Side panel: tabbed Jobs + Profile editor.
// Vanilla JS, no build step. Talks to the FastAPI backend on localhost:8000.

const BACKEND = 'http://localhost:8000';
const PROFILE_ID = 1;

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
const profileSection = document.getElementById('profile-section');
const hdrJobsBtns = document.getElementById('hdr-jobs-btns');
let profileLoaded = false;

tabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    tabBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    jobsSection.hidden = tab !== 'jobs';
    profileSection.hidden = tab !== 'profile';
    hdrJobsBtns.style.display = tab === 'jobs' ? '' : 'none';
    if (tab === 'profile' && !profileLoaded) loadProfile();
  });
});

// ---------------------------------------------------------------------------
// Jobs tab
// ---------------------------------------------------------------------------
const jobStatusEl = document.getElementById('job-status');
const jobListEl = document.getElementById('job-list');

async function loadJobs() {
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
  jobStatusEl.textContent = `${jobs.length} matched job(s).`;
  for (const job of jobs) jobListEl.appendChild(renderJob(job));
}

function renderJob(job) {
  const li = document.createElement('li');
  li.dataset.jobId = job.job_id;

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

  const clBtn = document.createElement('button');
  clBtn.className = 'del-btn';
  clBtn.title = 'Generate cover letter';
  clBtn.textContent = '✚';
  clBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    clBtn.textContent = '…';
    clBtn.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}/regenerate`, { method: 'POST' });
      if (!res.ok) throw new Error();
      pollForCoverLetter(job.job_id, (content) => {
        clBtn.textContent = '✔';
        clBtn.disabled = false;
        // If the detail panel is open, update it in place
        if (detailEl) {
          const blurb = detailEl.querySelector('.cl-blurb');
          if (blurb) {
            blurb.style.color = '';
            blurb.textContent = content;
          }
        }
      }, () => {
        clBtn.textContent = '✚';
        clBtn.disabled = false;
      });
    } catch {
      clBtn.textContent = '✖';
      setTimeout(() => { clBtn.textContent = '✚'; clBtn.disabled = false; }, 2000);
    }
  });
  row.appendChild(clBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'del-btn';
  delBtn.title = 'Delete';
  delBtn.textContent = '🗑';
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

  let expanded = false;
  let detailEl = null;
  li.addEventListener('click', async (ev) => {
    if (ev.target.tagName === 'A') return;
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

async function pollForCoverLetter(jobId, onFound, onTimeout) {
  const MAX_ATTEMPTS = 24; // ~2 minutes at 5s intervals
  for (let i = 0; i < MAX_ATTEMPTS; i++) {
    await sleep(5000);
    try {
      const res = await fetch(`${BACKEND}/jobs/${jobId}?profile_id=${PROFILE_ID}`);
      const data = await res.json();
      if (data.cover_letter?.generated_content) {
        onFound(data.cover_letter.generated_content);
        return;
      }
    } catch { /* ignore transient errors, keep polling */ }
  }
  onTimeout();
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

    const cl = data.cover_letter?.generated_content;
    const blurb = document.createElement('div');
    blurb.className = 'cl-blurb';
    blurb.style.marginTop = '6px';
    if (cl) {
      blurb.textContent = cl;
    } else {
      blurb.style.color = '#6b7280';
      blurb.textContent = 'No cover letter yet.';
    }
    detailEl.appendChild(blurb);
  } catch {
    detailEl.textContent = 'Could not load detail.';
  }
}

// ---------------------------------------------------------------------------
// Scan Page (1-hop rule: links from a page the user opened, ≥5s apart, capped)
// ---------------------------------------------------------------------------
const MAX_SCAN_PAGES = 10;
const SCAN_DELAY_MS = 5000;

const scanBtn = document.getElementById('scan-btn');
const scanLogEl = document.getElementById('scanlog');
let scanning = false;

function scanLog(msg) {
  const line = document.createElement('div');
  line.textContent = msg;
  scanLogEl.appendChild(line);
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

async function ingestListing(listing) {
  try {
    const res = await fetch(`${BACKEND}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listings: [listing], profile_id: PROFILE_ID }),
    });
    return res.ok;
  } catch { return false; }
}

async function scanPage() {
  if (scanning) return;
  scanning = true;
  scanBtn.disabled = true;
  scanLogEl.innerHTML = '';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !/^https:\/\/(au\.seek\.com|www\.seek\.com\.au)\/[^?#]*jobs/i.test(tab.url || '')) {
      scanLog('Open a Seek search results page in this tab first, then click Scan Page.');
      return;
    }
    let allUrls;
    try { allUrls = await injectFn(tab.id, pageCollectJobLinks); }
    catch (e) { scanLog(`Could not read the page: ${e.message}`); return; }

    allUrls = allUrls || [];
    if (!allUrls.length) { scanLog('No job links found on this page.'); return; }

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
    if (!urls.length) { scanLog('All jobs on this page already captured.'); return; }
    scanLog(`Found ${newUrls.length} new link(s); scanning ${urls.length} (5s apart)…`);

    for (let i = 0; i < urls.length; i++) {
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
        const ok = await ingestListing(payload);
        const desc = payload.raw_description ? `${payload.raw_description.length} chars` : 'NO DESCRIPTION';
        scanLog(ok ? `${label} captured ✓ (${desc})` : `${label} backend error ✗`);
      } else {
        scanLog(`${label} no data scraped ✗`);
      }
      if (i < urls.length - 1) await sleep(SCAN_DELAY_MS);
    }

    scanLog('Scan complete — refreshing matches…');
    loadJobs();
  } finally {
    scanning = false;
    scanBtn.disabled = false;
  }
}

scanBtn.addEventListener('click', scanPage);
document.getElementById('refresh-btn').addEventListener('click', loadJobs);

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
      btn.textContent = `Deleted ${data.deleted}`;
      setTimeout(() => { btn.textContent = 'Delete'; btn.disabled = false; }, 2000);
      loadJobs();
    } else {
      throw new Error();
    }
  } catch {
    btn.textContent = 'Error';
    setTimeout(() => { btn.textContent = 'Delete'; btn.disabled = false; }, 2000);
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

  // Cover letter ready → update the card in place (no full reload needed)
  source.addEventListener('cover_letter_ready', (e) => {
    const { job_id, content } = JSON.parse(e.data);

    // Update blurb if the card is already expanded
    const li = jobListEl.querySelector(`li[data-job-id="${job_id}"]`);
    if (li) {
      const blurb = li.querySelector('.cl-blurb');
      if (blurb) {
        blurb.style.color = '';
        blurb.textContent = content;
      }
      // Reset the ✚ button to ✔ to indicate it's done
      const clBtn = li.querySelector('.del-btn[title="Generate cover letter"]');
      if (clBtn && clBtn.textContent === '…') {
        clBtn.textContent = '✔';
        clBtn.disabled = false;
        setTimeout(() => { clBtn.textContent = '✚'; }, 2000);
      }
    }
  });

  source.onerror = () => {
    // EventSource auto-reconnects; onopen will fire again and trigger a reload
  };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
loadJobs();
connectEvents();
