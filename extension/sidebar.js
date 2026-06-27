// Side panel: tabbed Jobs + Profile editor.
// Vanilla JS, no build step. Talks to the FastAPI backend on localhost:8000.

const BACKEND = 'http://localhost:8000';
const PROFILE_ID = 1;

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

function makeQualCard(data = {}) {
  const card = document.createElement('div');
  card.className = 'entry-card';
  card.innerHTML = `
    <button class="remove-btn" title="Remove">×</button>
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
  `;
  card.querySelector('.remove-btn').addEventListener('click', () => card.remove());

  if (data.qualification_type) card.querySelector('.f-type').value = data.qualification_type;
  if (data.title)              card.querySelector('.f-title').value = data.title;
  if (data.institution)        card.querySelector('.f-institution').value = data.institution;
  if (data.field_of_study)     card.querySelector('.f-field').value = data.field_of_study;
  if (data.grade)              card.querySelector('.f-grade').value = data.grade;
  if (data.start_date)         card.querySelector('.f-start').value = data.start_date;
  if (data.end_date)           card.querySelector('.f-end').value = data.end_date;
  if (data.status)             card.querySelector('.f-status').value = data.status;

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

function makeExpCard(data = {}) {
  const card = document.createElement('div');
  card.className = 'entry-card';
  card.innerHTML = `
    <button class="remove-btn" title="Remove">×</button>
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
      <input class="f-current" type="checkbox">
      <label>Current role</label>
    </div>
    <label class="field"><span>Description</span>
      <textarea class="f-desc" placeholder="Key responsibilities and achievements…"></textarea>
    </label>
    <label class="field"><span>Skills used (comma-separated)</span>
      <input class="f-skills" type="text" placeholder="Python, SQL, React…">
    </label>
  `;

  const currentCb = card.querySelector('.f-current');
  const endLabel  = card.querySelector('.f-end-label');
  const endInput  = card.querySelector('.f-end');

  function toggleEnd() {
    endLabel.style.opacity = currentCb.checked ? '0.35' : '1';
    endInput.disabled = currentCb.checked;
  }
  currentCb.addEventListener('change', toggleEnd);
  card.querySelector('.remove-btn').addEventListener('click', () => card.remove());

  if (data.experience_type) card.querySelector('.f-type').value = data.experience_type;
  if (data.title)           card.querySelector('.f-title').value = data.title;
  if (data.organization)    card.querySelector('.f-org').value = data.organization;
  if (data.start_date)      card.querySelector('.f-start').value = data.start_date;
  if (data.end_date)        card.querySelector('.f-end').value = data.end_date;
  if (data.description)     card.querySelector('.f-desc').value = data.description;
  if (data.skills?.length)  card.querySelector('.f-skills').value = data.skills.join(', ');
  if (data.is_current) { currentCb.checked = true; toggleEnd(); }

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
  for (const q of (data.qualifications || [])) qualsList.appendChild(makeQualCard(q));

  const expsList = document.getElementById('exps-list');
  expsList.innerHTML = '';
  for (const e of (data.experiences || [])) expsList.appendChild(makeExpCard(e));

  document.getElementById('p-skills').value = (data.skills || []).join('\n');
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
    const skills = document.getElementById('p-skills').value
      .split('\n').map(s => s.trim()).filter(Boolean);

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
  document.getElementById('quals-list').appendChild(makeQualCard()));

document.getElementById('add-exp-btn').addEventListener('click', () =>
  document.getElementById('exps-list').appendChild(makeExpCard()));

document.getElementById('save-profile-btn').addEventListener('click', saveProfile);

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
