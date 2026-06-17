// Side panel: fetch matched jobs from the backend and render them ranked by
// score. Clicking a job expands its cover letter (if generated). Vanilla JS,
// no build step.

const BACKEND = 'http://localhost:8000';
const PROFILE_ID = 1;

const statusEl = document.getElementById('status');
const listEl = document.getElementById('jobs');

async function loadJobs() {
  statusEl.textContent = 'Loading…';
  listEl.innerHTML = '';
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}`);
    jobs = await res.json();
  } catch (e) {
    statusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }

  if (!Array.isArray(jobs) || jobs.length === 0) {
    statusEl.textContent =
      'No matched jobs yet. Browse Seek with the extension active to capture listings.';
    return;
  }

  statusEl.textContent = `${jobs.length} matched job(s).`;
  for (const job of jobs) {
    listEl.appendChild(renderJob(job));
  }
}

function renderJob(job) {
  const li = document.createElement('li');

  const row = document.createElement('div');
  row.className = 'row';

  const title = document.createElement('span');
  title.className = 'title';
  title.textContent = job.title || '(untitled)';
  row.appendChild(title);

  if (job.score !== null && job.score !== undefined) {
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = Math.round(job.score);
    row.appendChild(score);
  }
  li.appendChild(row);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = [job.company, job.location].filter(Boolean).join(' · ') || '—';
  li.appendChild(meta);

  // Expandable detail (cover letter), loaded on first click.
  let expanded = false;
  let detailEl = null;
  li.addEventListener('click', async (ev) => {
    if (ev.target.tagName === 'A') return; // let the Seek link work normally
    expanded = !expanded;
    if (!detailEl) {
      detailEl = document.createElement('div');
      detailEl.className = 'detail';
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
    link.target = '_blank';
    link.textContent = 'Open on Seek ↗';
    detailEl.appendChild(link);

    if (data.cover_letter && data.cover_letter.generated_content) {
      const cl = document.createElement('div');
      cl.style.marginTop = '6px';
      cl.textContent = data.cover_letter.generated_content;
      detailEl.appendChild(cl);
    } else {
      const none = document.createElement('div');
      none.style.marginTop = '6px';
      none.style.color = '#6b7280';
      none.textContent = 'No cover letter yet.';
      detailEl.appendChild(none);
    }
  } catch (e) {
    detailEl.textContent = 'Could not load detail.';
  }
}

// --- Limited "Scan Page" ----------------------------------------------------
// From a Seek search results page the user opened, navigate the active tab to the
// job links ON THAT PAGE — one at a time, 5s apart, capped at MAX_SCAN_PAGES — and
// capture each detail page (full description), POSTing it to /ingest. The scan NEVER
// follows links found on the detail pages themselves: max 1 hop from a page the user
// opened. It runs in the side panel (survives the tab navigating) and injects its own
// code with chrome.scripting, so it does NOT depend on the auto-injected content
// script being present.

const MAX_SCAN_PAGES = 3;
const SCAN_DELAY_MS = 5000;

const scanBtn = document.getElementById('scan');
const scanLogEl = document.getElementById('scanlog');
let scanning = false;

function scanLog(msg) {
  const line = document.createElement('div');
  line.textContent = msg;
  scanLogEl.appendChild(line);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// --- Functions injected INTO the Seek page (run in the page, no closures) ---

// Every distinct /job/{id} link already on the page — selector-independent.
function pageCollectJobLinks() {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/job/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/job\/(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    out.push(href.startsWith('http') ? href : location.origin + href);
  }
  return out;
}

// Scrape the current /job/{id} detail page. Polls because Seek renders with React.
async function pageScrapeDetail() {
  const m = location.pathname.match(/\/job\/(\d+)/);
  if (!m) return null;
  const start = Date.now();
  let descEl = null;
  while (Date.now() - start < 8000) {
    descEl = document.querySelector('[data-automation="jobAdDetails"]');
    if (descEl && descEl.innerText.trim()) break;
    await new Promise((r) => setTimeout(r, 400));
  }
  const titleEl = document.querySelector('[data-automation="job-detail-title"]');
  return {
    source_job_id: m[1],
    url: location.href,
    title: (titleEl?.innerText || document.title || 'Untitled').replace(/\s*[|-]\s*SEEK.*$/i, '').trim(),
    raw_description: descEl && descEl.innerText.trim() ? descEl.innerText.trim() : null,
  };
}

// --- Helpers that run in the side panel ------------------------------------

// Resolve once the tab finishes loading (status 'complete'), or after timeoutMs.
function waitForTabComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(onUpd);
      resolve();
    }, timeoutMs);
    function onUpd(id, info) {
      if (id === tabId && info.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(onUpd);
        resolve();
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
  } catch (e) {
    return false;
  }
}

async function scanPage() {
  if (scanning) return;
  scanning = true;
  scanBtn.disabled = true;
  scanLogEl.innerHTML = '';
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    // Seek search pages: au.seek.com/<slug>-jobs/... (SEO) or the legacy /jobs path.
    if (!tab || !/^https:\/\/(au\.seek\.com|www\.seek\.com\.au)\/[^?#]*jobs/i.test(tab.url || '')) {
      scanLog('Open a Seek search results page in this tab first, then click Scan Page.');
      return;
    }
    const originalUrl = tab.url;

    let allUrls;
    try {
      allUrls = await injectFn(tab.id, pageCollectJobLinks);
    } catch (e) {
      scanLog(`Could not read the page: ${e.message}`);
      return;
    }
    allUrls = allUrls || [];
    const urls = allUrls.slice(0, MAX_SCAN_PAGES);
    if (!urls.length) {
      scanLog('No job links found on this page.');
      return;
    }
    scanLog(`Found ${allUrls.length} link(s); scanning ${urls.length} (5s apart)…`);

    for (let i = 0; i < urls.length; i++) {
      const label = `(${i + 1}/${urls.length})`;
      scanLog(`${label} opening job page…`);
      const loaded = waitForTabComplete(tab.id, 15000);
      await chrome.tabs.update(tab.id, { url: urls[i] });
      await loaded;

      let payload = null;
      try {
        payload = await injectFn(tab.id, pageScrapeDetail);
      } catch (e) {
        scanLog(`${label} could not scrape: ${e.message}`);
      }
      if (payload && payload.source_job_id) {
        const ok = await ingestListing(payload);
        const desc = payload.raw_description
          ? `${payload.raw_description.length} chars`
          : 'NO DESCRIPTION';
        scanLog(ok ? `${label} captured ✓ (${desc})` : `${label} backend error ✗`);
      } else {
        scanLog(`${label} no data scraped ✗`);
      }
      if (i < urls.length - 1) await sleep(SCAN_DELAY_MS);
    }

    await chrome.tabs.update(tab.id, { url: originalUrl }); // back to the search page
    scanLog('Scan complete — refreshing matches…');
    loadJobs();
  } finally {
    scanning = false;
    scanBtn.disabled = false;
  }
}

scanBtn.addEventListener('click', scanPage);

document.getElementById('refresh').addEventListener('click', loadJobs);
loadJobs();
