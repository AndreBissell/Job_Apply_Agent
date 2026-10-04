// Injected into Seek pages the user opened themselves. Reads the already-rendered
// DOM and POSTs the job data to the local backend. Makes NO request to Seek — it
// only reads the page the user is already viewing.

// BACKEND comes from config.js (real vs test environment).

// Every call to the local backend goes through the background service worker
// (background.js, BACKEND_FETCH). A request made from this content script carries
// the Seek page's origin, and Chrome's Local Network Access blocks a public page
// reaching localhost ("Permission was denied ... loopback address space"). The
// worker runs as the extension, with the localhost host_permissions, so it isn't
// subject to that rule. Returns the bits of Response the callers use.
async function backendFetch(url, init = {}) {
  const reply = await chrome.runtime.sendMessage({
    type: 'BACKEND_FETCH',
    url,
    init: { method: init.method || 'GET', headers: init.headers, body: init.body },
  });
  if (!reply || reply.error) throw new Error((reply && reply.error) || 'no reply from the background worker');
  return {
    ok: reply.ok,
    status: reply.status,
    json: async () => JSON.parse(reply.body),
    text: async () => reply.body,
  };
}

async function ingest(listings) {
  try {
    const res = await backendFetch(`${BACKEND}/ingest`, {
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

// schema.org fields come in several shapes: a string, an object with .name,
// or an array of either. Returns the first usable string or null.
function ldText(value, key = 'name') {
  if (!value) return null;
  if (typeof value === 'string') return value.trim() || null;
  if (Array.isArray(value)) {
    for (const v of value) { const t = ldText(v, key); if (t) return t; }
    return null;
  }
  return typeof value === 'object' ? ldText(value[key], key) : null;
}

// "FULL_TIME" -> "Full time", matching the wording search cards use.
function humaniseEmploymentType(raw) {
  const t = ldText(raw);
  if (!t) return null;
  const s = t.replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function ldLocation(jobLocation) {
  const loc = Array.isArray(jobLocation) ? jobLocation[0] : jobLocation;
  const addr = loc && loc.address;
  if (!addr || typeof addr !== 'object') return ldText(loc);
  const parts = [addr.addressLocality, addr.addressRegion].map((p) => ldText(p)).filter(Boolean);
  return parts.length ? parts.join(', ') : null;
}

// Employer, location and work type for a detail page: JSON-LD first (Seek's own
// structured data), then the on-page elements. Before 2026-10-02 the detail page
// sent none of these, and every captured job had company = NULL, so letters said
// "at Unknown". Any of them may still be null; the backend backfills company from
// a later search-card capture, or from the ad text at extraction.
function readDetailMeta() {
  const posting = readJsonLdJobPosting() || {};
  return {
    company:   ldText(posting.hiringOrganization) || textOrNull(document, SELECTORS.DETAIL_COMPANY),
    location:  ldLocation(posting.jobLocation) || textOrNull(document, SELECTORS.DETAIL_LOCATION),
    work_type: humaniseEmploymentType(posting.employmentType) || textOrNull(document, SELECTORS.DETAIL_WORK_TYPE),
  };
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
  const { company, location: jobLocation, work_type } = readDetailMeta();
  return [{
    source_job_id: job_id,
    url: window.location.href,
    title,
    company,
    location: jobLocation,
    work_type,
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

// The side panel closing hides the questions panel; reopening it brings it back.
chrome.runtime.onMessage.addListener((msg) => {
  if (!msg) return;
  if (msg.type === 'SIDEBAR_CLOSED') {
    questionsHidden = true;
    removeQuestionsPanel();
  } else if (msg.type === 'SIDEBAR_OPENED') {
    questionsHidden = false;
    if (lastQuestionsList) renderQuestionsPanel(lastQuestionsList);
  }
});

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
    const res = await backendFetch(`${BACKEND}/jobs/${jobId}?profile_id=1`);
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
    const res = await backendFetch(`${BACKEND}/jobs/by-source-id/${sourceJobId}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.job_id ?? null;
  } catch {
    return null;
  }
}

async function markExpired(jobId) {
  try {
    await backendFetch(`${BACKEND}/jobs/${jobId}/expired`, { method: 'PATCH' });
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
    const res = await backendFetch(`${BACKEND}/jobs/${jobId}/screenshot?profile_id=1`, {
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
      const res = await backendFetch(`${BACKEND}/jobs/${jobId}/cover-letter?profile_id=1`, {
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
      const res = await backendFetch(`${BACKEND}/jobs/${jobId}/status?profile_id=1`, {
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

// ---------------------------------------------------------------------------
// Quick Apply questions (plan §10.1, Phase 9a). On the "Answer employer questions"
// step of an apply flow the user opened, read each question's text, type and
// options and send them to the backend's question bank, then list them in a
// small overlay with their kind. A pure read of the rendered DOM: no navigation,
// no clicks, nothing filled in, no request to Seek. Never reads what is checked,
// selected or typed (Seek pre-fills answers), or anything under [data-adora-mask].
// Markup per docs/quick-apply-samples.md; unverified on a live page.
// ---------------------------------------------------------------------------
const QUESTIONS_HOST_ID = 'seek-assistant-questions-host';

function cleanText(s) {
  return (s || '').replace(/\s+/g, ' ').trim();
}

function isMasked(el) {
  return !!(el && el.closest(SELECTORS.PERSONAL_DATA_MASK));
}

function labelTextFor(id) {
  if (!id) return '';
  const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
  return label && !isMasked(label) ? cleanText(label.textContent) : '';
}

// The progress bar's current step label ("Answer employer questions"), or null.
function currentApplyStep() {
  const nav = document.querySelector(SELECTORS.APPLY_PROGRESS_NAV);
  const step = nav && nav.querySelector(SELECTORS.APPLY_CURRENT_STEP);
  return step ? cleanText(step.textContent) || null : null;
}

// Checkboxes have no fieldset or label for the question: its text is the first
// <strong> in the closest container that holds every input of the group.
function checkboxQuestionText(inputs) {
  let box = inputs[0].parentElement;
  while (box && !(inputs.every((i) => box.contains(i)) && box.querySelector('strong'))) {
    if (box.tagName === 'FORM') return '';
    box = box.parentElement;
  }
  const strong = box && box.querySelector('strong');
  return strong && !isMasked(strong) ? cleanText(strong.textContent) : '';
}

// One question per name="questionnaire.<id>" group, in document order. Option ids
// come from the value attribute (radios, <option>) or the input id (checkboxes):
// ids, never the user's answer.
function parseQuestionnaire() {
  const groups = new Map();
  for (const el of document.querySelectorAll(SELECTORS.QUESTION_FIELDS)) {
    if (isMasked(el)) continue;
    const name = el.getAttribute('name') || '';
    if (!name.startsWith(SELECTORS.QUESTION_NAME_PREFIX)) continue;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(el);
  }

  const questions = [];
  for (const [name, els] of groups) {
    const seek_question_id = name.slice(SELECTORS.QUESTION_NAME_PREFIX.length);
    const first = els[0];
    const tag = first.tagName.toLowerCase();
    const type = (first.getAttribute('type') || '').toLowerCase();
    let input_type;
    let text = '';
    let options = [];

    if (tag === 'select') {
      input_type = 'dropdown';
      text = labelTextFor(first.id);
      options = Array.from(first.querySelectorAll('option'))
        .filter((o) => (o.getAttribute('value') || '') !== '') // blank placeholder
        .map((o) => ({ value: o.getAttribute('value'), label: cleanText(o.textContent) }));
    } else if (tag === 'textarea') {
      input_type = 'text';
      text = labelTextFor(first.id);
    } else if (type === 'radio') {
      input_type = 'single';
      const legend = first.closest('fieldset')?.querySelector('legend');
      text = legend && !isMasked(legend) ? cleanText(legend.textContent) : '';
      options = els.map((r) => ({ value: r.getAttribute('value') || r.id, label: labelTextFor(r.id) }));
    } else if (type === 'checkbox') {
      input_type = 'multi';
      text = checkboxQuestionText(els);
      options = els.map((c) => ({ value: c.id, label: labelTextFor(c.id) }));
    } else {
      // Not seen in the samples (number, date, ...): take the label, treat as text.
      input_type = 'text';
      text = labelTextFor(first.id);
    }

    options = options.filter((o) => o.label);
    if (!text || (input_type !== 'text' && !options.length)) {
      console.warn('[SeekAssistant] Could not read question', seek_question_id, '— markup may have changed.');
      continue;
    }
    questions.push({ seek_question_id, field_name: name, text, input_type, options });
  }
  return questions;
}

// The apply page's job title, for the rare case the job was never captured
// before (the user reached Quick Apply without opening its detail page here).
function applyPageTitle() {
  const h1 = document.querySelector(`${SELECTORS.APPLY_JOB_HEADER} h1`) || document.querySelector('h1');
  const title = h1 && !isMasked(h1) ? cleanText(h1.textContent) : '';
  return title || (document.title || '').replace(/\s*[|-]\s*SEEK.*$/i, '').trim() || 'Untitled';
}

async function resolveOrCreateJob(sourceJobId) {
  const known = await resolveInternalJobId(sourceJobId);
  if (known) return known;
  // A stub row (no description): the detail page fills it in on a later visit.
  const result = await ingest([{
    source_job_id: sourceJobId,
    url: `${location.origin}/job/${sourceJobId}`,
    title: applyPageTitle(),
    raw_description: null,
  }]);
  return result?.job_ids?.[0] ?? null;
}

async function postScreeningQuestions(jobId, questions, step) {
  try {
    const res = await backendFetch(`${BACKEND}/jobs/${jobId}/screening-questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questions, step }),
    });
    if (!res.ok) {
      console.warn('[SeekAssistant] Question capture rejected', res.status, await res.text());
      return null;
    }
    return await res.json();
  } catch (e) {
    console.warn('[SeekAssistant] Backend not reachable (is run_api.py running?):', e.message);
    return null;
  }
}

const KIND_LABELS = {
  user: 'Yours to answer',
  assisted: 'We’ll help — coming in 9b',
  unknown: 'New — not sorted yet',
};

function removeQuestionsPanel() {
  document.getElementById(QUESTIONS_HOST_ID)?.remove();
}

// Where the user dragged the panel to (viewport px, top-left corner). Kept for the
// life of the page so a redraw (the step re-rendering) doesn't snap it back.
let questionsPanelPos = null;
// Hidden by the user's X or because the side panel was closed; the last list read,
// so the panel can come back when the side panel reopens.
let questionsHidden = false;
let lastQuestionsList = null;

function placeQuestionsHost(host) {
  if (!questionsPanelPos) { // default: bottom left
    host.style.cssText = 'position:fixed;bottom:16px;left:16px;z-index:2147483647;';
    return;
  }
  const maxLeft = Math.max(0, window.innerWidth - 80);
  const maxTop = Math.max(0, window.innerHeight - 40);
  const left = Math.min(Math.max(0, questionsPanelPos.left), maxLeft);
  const top = Math.min(Math.max(0, questionsPanelPos.top), maxTop);
  host.style.cssText = `position:fixed;top:${top}px;left:${left}px;z-index:2147483647;`;
}

// Drag the panel by its header. A press that moves less than a few pixels is a
// click (collapse/expand), not a drag.
function makeDraggable(host, handle, onClick) {
  handle.style.cursor = 'grab';
  handle.style.touchAction = 'none';
  handle.addEventListener('pointerdown', (down) => {
    if (down.button !== 0) return;
    const rect = host.getBoundingClientRect();
    const dx = down.clientX - rect.left;
    const dy = down.clientY - rect.top;
    let dragging = false;
    handle.setPointerCapture(down.pointerId);
    const move = (e) => {
      if (!dragging && Math.hypot(e.clientX - down.clientX, e.clientY - down.clientY) < 5) return;
      dragging = true;
      handle.style.cursor = 'grabbing';
      questionsPanelPos = { left: e.clientX - dx, top: e.clientY - dy };
      placeQuestionsHost(host);
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      handle.style.cursor = 'grab';
      if (!dragging) onClick();
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
  });
}

// A smaller window must not strand the panel off-screen.
window.addEventListener('resize', () => {
  const host = document.getElementById(QUESTIONS_HOST_ID);
  if (host && questionsPanelPos) placeQuestionsHost(host);
});

function renderQuestionsPanel(questions) {
  lastQuestionsList = questions;
  removeQuestionsPanel();
  if (questionsHidden) return;
  const host = document.createElement('div');
  host.id = QUESTIONS_HOST_ID;
  placeQuestionsHost(host);
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });

  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    .panel { font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 13px;
      width: 340px; max-width: calc(100vw - 32px); max-height: 60vh;
      display: flex; flex-direction: column; background: #fff; border: 1px solid #cbd5e1;
      border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.18); overflow: hidden;
      color: #1c2330; }
    .panel.collapsed .body { display: none; }
    .hdr { display: flex; align-items: center; justify-content: space-between;
      padding: 8px 10px; background: #2557a7; color: #fff; cursor: pointer; user-select: none; }
    .hdr strong { font-size: 12px; }
    .body { padding: 8px 10px; overflow-y: auto; }
    .summary { font-size: 11px; color: #4b5563; margin-bottom: 6px; }
    ol { margin: 0; padding-left: 18px; }
    li { margin: 0 0 8px; }
    .q { font-size: 12px; line-height: 1.35; }
    .badge { display: inline-block; margin-top: 3px; font-size: 10px; padding: 1px 6px;
      border-radius: 9px; border: 1px solid; }
    .badge.user { color: #374151; border-color: #9ca3af; background: #f3f4f6; }
    .badge.assisted { color: #065f46; border-color: #10b981; background: #ecfdf5; }
    .badge.unknown { color: #92400e; border-color: #f59e0b; background: #fffbeb; }
    .note { font-size: 10px; color: #6b7280; margin-top: 4px; }
  `;
  shadow.appendChild(style);

  const panel = document.createElement('div');
  panel.className = 'panel';
  const hdr = document.createElement('div');
  hdr.className = 'hdr';
  const title = document.createElement('strong');
  title.textContent = `Employer questions (${questions.length})`;
  const caret = document.createElement('span');
  caret.textContent = '▾';
  const close = document.createElement('span');
  close.textContent = '✕';
  close.title = 'Hide until the next step or until you reopen the side panel';
  close.style.cssText = 'margin-left:10px;padding:0 4px;cursor:pointer;font-size:13px;line-height:1;';
  // The X must not start a drag or toggle the collapse underneath it.
  close.addEventListener('pointerdown', (e) => e.stopPropagation());
  close.addEventListener('click', (e) => {
    e.stopPropagation();
    questionsHidden = true;
    removeQuestionsPanel();
  });
  const right = document.createElement('span');
  right.style.cssText = 'display:flex;align-items:center;';
  right.append(caret, close);
  hdr.append(title, right);
  hdr.title = 'Drag to move, click to collapse';
  makeDraggable(host, hdr, () => panel.classList.toggle('collapsed'));
  panel.appendChild(hdr);

  const body = document.createElement('div');
  body.className = 'body';
  const counts = { user: 0, assisted: 0, unknown: 0 };
  for (const q of questions) counts[q.kind in counts ? q.kind : 'unknown'] += 1;
  const summary = document.createElement('div');
  summary.className = 'summary';
  summary.textContent = `${counts.user} yours to answer · ${counts.assisted} we'll help · ${counts.unknown} new`;
  body.appendChild(summary);

  const list = document.createElement('ol');
  for (const q of questions) {
    const kind = q.kind in KIND_LABELS ? q.kind : 'unknown';
    const li = document.createElement('li');
    li.dataset.kind = kind;
    const text = document.createElement('div');
    text.className = 'q';
    text.textContent = q.text;
    const badge = document.createElement('span');
    badge.className = `badge ${kind}`;
    const topic = kind === 'user' && q.parameters?.topic ? ` (${q.parameters.topic.replace(/_/g, ' ')})` : '';
    badge.textContent = KIND_LABELS[kind] + topic;
    li.append(text, badge);
    list.appendChild(li);
  }
  body.appendChild(list);
  const note = document.createElement('div');
  note.className = 'note';
  note.textContent = 'Nothing is filled in for you. New questions can be sorted in the profile editor.';
  body.appendChild(note);
  panel.appendChild(body);
  shadow.appendChild(panel);
}

// The apply flow is a single-page app: the steps (and the jump from the detail
// page into the flow) can change without a page load, so watch the DOM and
// re-check after it settles. A capture is only sent when what was read changed.
let lastQuestionsSig = null;
let applyCheckRunning = false;
let applyCheckAgain = false;

async function checkApplyStep() {
  if (applyCheckRunning) { applyCheckAgain = true; return; }
  applyCheckRunning = true;
  try {
    do {
      applyCheckAgain = false;
      const sourceJobId = extractApplyJobId(location.pathname);
      const questions = sourceJobId ? parseQuestionnaire() : [];
      if (!questions.length) {
        // Another step, or not an apply page: drop the panel. Coming back to the
        // questions step shows it again, even if the user had hidden it.
        lastQuestionsSig = null;
        lastQuestionsList = null;
        questionsHidden = false;
        removeQuestionsPanel();
        continue;
      }
      const sig = `${sourceJobId}|${JSON.stringify(questions)}`;
      if (sig === lastQuestionsSig) continue;
      lastQuestionsSig = sig;
      const jobId = await resolveOrCreateJob(sourceJobId);
      if (!jobId) { lastQuestionsSig = null; continue; }
      const result = await postScreeningQuestions(jobId, questions, currentApplyStep());
      if (!result) { lastQuestionsSig = null; continue; }
      console.log(`[SeekAssistant] Captured ${questions.length} employer questions for job ${sourceJobId}.`);
      if (lastQuestionsSig === sig) renderQuestionsPanel(result.questions);
    } while (applyCheckAgain);
  } finally {
    applyCheckRunning = false;
  }
}

function startApplyWatcher() {
  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(checkApplyStep, 700);
  };
  // Our own overlay being added or removed is not a page change.
  const ownOverlay = (m) => {
    const nodes = [...m.addedNodes, ...m.removedNodes];
    return m.type === 'childList' && nodes.length > 0 && nodes.every((n) => n.id === QUESTIONS_HOST_ID);
  };
  new MutationObserver((mutations) => {
    if (mutations.every(ownOverlay)) return;
    schedule();
  }).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['aria-current'],
  });
  schedule();
}

async function main() {
  const path = window.location.pathname;
  startApplyWatcher();

  // A Quick Apply page (/job/{id}/apply/...) is not a detail page: it has no
  // description, and treating it as one would mark the job expired. The watcher
  // above handles it.
  if (extractApplyJobId(path)) return;

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
