// Injected into Seek pages the user opened themselves. Reads the already-rendered
// DOM and POSTs the job data to the local backend. Makes NO request to Seek — it
// only reads the page the user is already viewing.

// BACKEND comes from config.js (real vs test environment).

async function ingest(listings) {
  try {
    const res = await fetch(`${BACKEND}/ingest`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ listings, profile_id: 1 }),
    });
    if (!res.ok) {
      console.warn('[SeekAssistant] Backend returned', res.status);
      return null;
    }
    return await res.json();
  } catch (e) {
    console.warn('[SeekAssistant] Backend not reachable (is run_api.py running?):', e.message);
    return null;
  }
}

function textOrNull(root, selector) {
  const el = root.querySelector(selector);
  const text = el && el.innerText ? el.innerText.trim() : '';
  return text || null;
}

// Seek embeds a schema.org JobPosting block on detail pages. It's part of the
// already-rendered DOM (no extra request to Seek) and it's the most stable
// source of the classification/subclassification pair, since data-automation
// attribute names change more often than the JSON-LD contract does. Returns
// null whenever the block is absent or unparseable, so callers fall back to
// the selectors above.
function readJsonLdJobPosting() {
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    let data;
    try { data = JSON.parse(script.textContent || ''); } catch { continue; }
    // Seek sometimes wraps the posting in an array or an @graph list.
    const nodes = Array.isArray(data) ? data : (data['@graph'] || [data]);
    for (const node of nodes) {
      if (node && node['@type'] === 'JobPosting') return node;
    }
  }
  return null;
}

// "Developers/Programmers (Information & Communication Technology)" splits into
// a subclassification and a classification. Seek writes it that way on the
// detail page and as occupationalCategory in the JSON-LD.
function splitOccupationalCategory(raw) {
  const text = (raw || '').trim();
  if (!text) return { classification: null, subclassification: null };
  const m = text.match(/^(.*?)\s*\((.+)\)\s*$/);
  if (m) return { subclassification: m[1].trim(), classification: m[2].trim() };
  return { classification: text, subclassification: null };
}

function readClassification() {
  const posting = readJsonLdJobPosting();
  const fromLd = posting && (
    typeof posting.occupationalCategory === 'string' ? posting.occupationalCategory : null
  );
  if (fromLd) return splitOccupationalCategory(fromLd);
  // Fallback: the on-page classification links.
  const sub = textOrNull(document, SELECTORS.DETAIL_SUBCLASSIFICATION);
  const cls = textOrNull(document, SELECTORS.DETAIL_CLASSIFICATION);
  if (sub && !cls) return splitOccupationalCategory(sub);
  return { classification: cls, subclassification: sub };
}

// The search that produced this results page, normalised to a comparable key
// so the backend can aggregate yield per query. Seek expresses the same search
// two ways — an SEO slug (/software-engineer-jobs/in-Perth) and a ?keywords=
// param — and both must reduce to the same string or the stats fragment.
// Reads only location.*; makes no request to Seek.
function currentSearchQuery() {
  const params = new URLSearchParams(location.search);
  const keywords = (params.get('keywords') || '').trim();
  if (keywords) return keywords.toLowerCase();
  const slug = (location.pathname.match(/\/([^/]+)-jobs(?:\/|$)/) || [])[1];
  if (slug) return decodeURIComponent(slug).replace(/-/g, ' ').trim().toLowerCase();
  return null;
}

function parseSearchPage() {
  const cards = document.querySelectorAll(SELECTORS.JOB_CARD);
  const discovered_query = currentSearchQuery();
  const listings = [];
  for (const card of cards) {
    const link = card.querySelector(SELECTORS.CARD_TITLE_LINK);
    if (!link) continue;
    const href = link.getAttribute('href');
    const job_id = extractJobId(href);
    if (!job_id) continue;
    const title = link.innerText.trim();
    if (!title) continue;
    listings.push({
      source_job_id: job_id,
      url: href.startsWith('http') ? href : `${location.origin}${href}`,
      title,
      company:   textOrNull(card, SELECTORS.CARD_COMPANY),
      location:  textOrNull(card, SELECTORS.CARD_LOCATION),
      work_type: textOrNull(card, SELECTORS.CARD_WORK_TYPE),
      salary:    textOrNull(card, SELECTORS.CARD_SALARY),
      discovered_query,
      raw_description: null,
    });
  }
  return listings;
}

function parseDetailPage() {
  const job_id = extractJobId(window.location.pathname);
  if (!job_id) return null;
  const descEl = document.querySelector(SELECTORS.DETAIL_DESCRIPTION);
  if (!descEl) return null;
  const raw_description = descEl.innerText.trim() || null;
  // Prefer the on-page title element; fall back to the document title.
  const title = textOrNull(document, SELECTORS.DETAIL_TITLE)
    || (document.title || '').replace(/\s*[|-]\s*SEEK.*$/i, '').trim()
    || 'Untitled';
  const { classification, subclassification } = readClassification();
  return [{
    source_job_id: job_id,
    url: window.location.href,
    title,
    classification,
    subclassification,
    raw_description,
  }];
}

// Poll for the expected content for up to ~timeoutMs (the page is React-rendered,
// so content may not be present at document_idle). Resolves with the listings or
// null if it never appears.
async function waitFor(parseFn, readySelector, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (document.querySelector(readySelector)) {
      const result = parseFn();
      if (result && result.length) return result;
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return null;
}

// Every distinct /job/{id} link already on this page, in document order. Selector-
// independent (just matches the href), so it survives Seek renaming data-automation
// attributes. The scan only ever follows THESE links (max 1 hop from a page the user
// opened) — it never collects links from the detail pages it then visits. See the
// networking policy in CLAUDE.md.
function collectJobLinks() {
  const urls = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/job/"]')) {
    const href = a.getAttribute('href') || '';
    const id = extractJobId(href);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    urls.push(href.startsWith('http') ? href : location.origin + href);
  }
  return urls;
}

// Hand the side panel the job links on this page (for the limited scan).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'COLLECT_LINKS') {
    sendResponse({ urls: collectJobLinks() });
  }
  // synchronous response; no need to keep the channel open
});

// ---------------------------------------------------------------------------
// Quick-Apply floating panel — injected on a /job/{id} detail page when a
// cover letter already exists for it. Reads the backend (own localhost API,
// not Seek) for job/cover-letter/match state; makes no extra Seek requests.
// Shadow DOM keeps its styles isolated from Seek's page CSS in both directions.
// See docs/extension-revamp-plan.md §5.2 — apply-flow-specific detection
// (auto-filling Seek's own Apply button) is a deliberate fast-follow, not
// implemented here, since it requires inspecting the real apply DOM live
// rather than guessing it.
// ---------------------------------------------------------------------------
const QUICK_APPLY_HOST_ID = 'seek-assistant-quick-apply-host';

async function fetchJobDetail(jobId) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${jobId}?profile_id=1`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// Seek's own numeric job id (from the URL) is never this app's internal id —
// resolve it via GET /jobs/by-source-id/{id}. Every backend call that acts on
// a specific job (detail, status, screenshot, expired) needs the internal id.
async function resolveInternalJobId(sourceJobId) {
  try {
    const res = await fetch(`${BACKEND}/jobs/by-source-id/${sourceJobId}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.job_id ?? null;
  } catch {
    return null;
  }
}

async function markExpired(jobId) {
  try {
    await fetch(`${BACKEND}/jobs/${jobId}/expired`, { method: 'PATCH' });
  } catch { /* best-effort — a missed flag just means the job stays visible */ }
}

// Screenshot evidence for Centrelink reporting: captures whatever's visible in
// THIS tab right now (the actual Seek job/application page) and saves it as a
// PNG. Relayed through background.js — captureVisibleTab isn't callable from
// a content script directly. Best-effort: a failure here should never block
// Mark Applied itself.
//
// Two copies are kept, deliberately: the local download is the user's own
// immediate copy; the backend upload (POST /jobs/{id}/screenshot) makes it
// durable, queryable, and exportable via the Applied-tab CSV — a file sitting
// only in Downloads has no link back to the Match row. The upload is
// best-effort and never undoes or blocks the local download on failure.
async function captureAndDownloadScreenshot(jobId) {
  let dataUrl = null;
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'CAPTURE_SCREENSHOT' });
    dataUrl = resp?.dataUrl || null;
  } catch { /* relay failed */ }
  if (!dataUrl) return false;

  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = `applied-${jobId}-${new Date().toISOString().slice(0, 10)}.png`;
  document.body.appendChild(a);
  a.click();
  a.remove();

  try {
    const res = await fetch(`${BACKEND}/jobs/${jobId}/screenshot?profile_id=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data_url: dataUrl }),
    });
    if (!res.ok) console.warn('[SeekAssistant] Screenshot upload failed', res.status);
  } catch (e) {
    console.warn('[SeekAssistant] Screenshot upload failed (backend unreachable):', e.message);
  }
  return true;
}

function buildQuickApplyPanel(jobId, data) {
  if (document.getElementById(QUICK_APPLY_HOST_ID)) return; // already injected

  const host = document.createElement('div');
  host.id = QUICK_APPLY_HOST_ID;
  host.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;';
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    .panel { font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 13px;
      width: 320px; max-width: calc(100vw - 32px); max-height: 70vh;
      display: flex; flex-direction: column; background: #fff; border: 1px solid #cbd5e1;
      border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.18); overflow: hidden;
      color: #1c2330; }
    .panel.collapsed .body { display: none; }
    .hdr { display: flex; align-items: center; justify-content: space-between;
      padding: 8px 10px; background: #2557a7; color: #fff; cursor: pointer; user-select: none; }
    .hdr strong { font-size: 12px; }
    .body { padding: 10px; overflow-y: auto; }
    .job-title { font-weight: 600; margin-bottom: 6px; font-size: 12px; }
    textarea { width: 100%; min-height: 160px; box-sizing: border-box; padding: 6px 8px;
      border: 1px solid #d1d5db; border-radius: 5px; font-size: 12px; font-family: inherit;
      color: #1c2330; resize: vertical; }
    .actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
    button.act { font-size: 11px; padding: 5px 9px; border: 1px solid #cbd5e1;
      border-radius: 6px; background: #fff; cursor: pointer; color: #374151; }
    button.act:hover { background: #f3f4f6; }
    button.act.applied { border-color: #10b981; color: #059669; }
  `;
  shadow.appendChild(style);

  const panel = document.createElement('div');
  panel.className = 'panel';

  const hdr = document.createElement('div');
  hdr.className = 'hdr';
  hdr.innerHTML = '<strong>✅ Cover letter ready</strong><span>▾</span>';
  hdr.addEventListener('click', () => panel.classList.toggle('collapsed'));
  panel.appendChild(hdr);

  const body = document.createElement('div');
  body.className = 'body';

  const titleEl = document.createElement('div');
  titleEl.className = 'job-title';
  titleEl.textContent = data.title || '';
  body.appendChild(titleEl);

  const textarea = document.createElement('textarea');
  textarea.value = data.cover_letter.edited_content || data.cover_letter.generated_content || '';
  body.appendChild(textarea);

  const actions = document.createElement('div');
  actions.className = 'actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'act';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(textarea.value);
      copyBtn.textContent = 'Copied ✓';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    } catch { /* clipboard permission denied — ignore */ }
  });
  actions.appendChild(copyBtn);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'act';
  saveBtn.textContent = 'Save edits';
  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const res = await fetch(`${BACKEND}/jobs/${jobId}/cover-letter?profile_id=1`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_content: textarea.value }),
      });
      saveBtn.textContent = res.ok ? 'Saved ✓' : 'Save failed';
    } catch {
      saveBtn.textContent = 'Save failed';
    } finally {
      setTimeout(() => { saveBtn.textContent = 'Save edits'; saveBtn.disabled = false; }, 1500);
    }
  });
  actions.appendChild(saveBtn);

  const shotBtn = document.createElement('button');
  shotBtn.className = 'act';
  shotBtn.textContent = '📸 Screenshot';
  shotBtn.addEventListener('click', async () => {
    shotBtn.disabled = true;
    shotBtn.textContent = 'Capturing…';
    const ok = await captureAndDownloadScreenshot(jobId);
    shotBtn.textContent = ok ? 'Saved ✓' : 'Failed';
    setTimeout(() => { shotBtn.textContent = '📸 Screenshot'; shotBtn.disabled = false; }, 1500);
  });
  actions.appendChild(shotBtn);

  const applyBtn = document.createElement('button');
  const isApplied = data.match?.status === 'applied';
  applyBtn.className = 'act' + (isApplied ? ' applied' : '');
  applyBtn.textContent = isApplied ? '✓ Applied' : 'Mark Applied';
  applyBtn.addEventListener('click', async () => {
    if (applyBtn.classList.contains('applied')) return;
    applyBtn.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/jobs/${jobId}/status?profile_id=1`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'applied' }),
      });
      if (res.ok) {
        applyBtn.classList.add('applied');
        applyBtn.textContent = '✓ Applied';
        // Best-effort evidence capture for Centrelink reporting — timed to the
        // actual "applied" action. Never blocks the button on failure.
        captureAndDownloadScreenshot(jobId);
      }
    } catch { /* transient network error — user can retry */ }
    finally {
      applyBtn.disabled = false;
    }
  });
  actions.appendChild(applyBtn);

  body.appendChild(actions);
  panel.appendChild(body);
  shadow.appendChild(panel);
}

async function maybeInjectQuickApply(jobId) {
  const data = await fetchJobDetail(jobId);
  if (data?.cover_letter?.generated_content) buildQuickApplyPanel(jobId, data);
}

async function main() {
  const path = window.location.pathname;

  // Detail page first: a standalone /job/{id} page. Everything else that looks like
  // a results page (Seek uses SEO slugs like /software-engineer-jobs/in-... as well
  // as the legacy /jobs path) is treated as a search page.
  if (path.startsWith('/job/')) {
    const sourceJobId = extractJobId(path);
    const listings = await waitFor(parseDetailPage, SELECTORS.DETAIL_DESCRIPTION, 8000);
    let internalJobId = null;

    if (listings) {
      const result = await ingest(listings);
      if (result) {
        console.log(`[SeekAssistant] Captured detail for job ${listings[0].source_job_id}.`);
        chrome.runtime.sendMessage({ type: 'INGEST_DONE', ...result });
        internalJobId = result.job_ids?.[0] ?? null;
      }
    } else {
      console.warn('[SeekAssistant] No description found — selectors may need updating.');
      // Ingest didn't run this visit, so resolve the internal id ourselves —
      // and use it to tell a genuinely closed listing apart from a fresh page
      // hitting a broken selector: only flag it expired if we successfully
      // captured a description for this exact job on some earlier visit.
      if (sourceJobId) {
        internalJobId = await resolveInternalJobId(sourceJobId);
        if (internalJobId) {
          const data = await fetchJobDetail(internalJobId);
          if (data?.raw_description) {
            await markExpired(internalJobId);
            console.log(`[SeekAssistant] Job ${internalJobId} appears to have closed — marking expired.`);
          }
        }
      }
    }

    // Independent of capture succeeding this visit — the job may already have
    // a cover letter from a prior capture, and that's the common case here.
    if (!internalJobId && sourceJobId) internalJobId = await resolveInternalJobId(sourceJobId);
    if (internalJobId) await maybeInjectQuickApply(internalJobId);
  } else if (path.includes('-jobs') || path.startsWith('/jobs')) {
    const listings = await waitFor(parseSearchPage, SELECTORS.JOB_CARD, 8000);
    if (!listings) {
      console.warn('[SeekAssistant] No job cards found — selectors may need updating.');
      return;
    }
    const result = await ingest(listings);
    if (result) {
      console.log(`[SeekAssistant] Captured ${listings.length} cards,`,
                  `${result.new} new, ${result.updated} updated.`);
      chrome.runtime.sendMessage({ type: 'INGEST_DONE', ...result });
    }
  }
}

backendReady.then(main);
