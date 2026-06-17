// Injected into Seek pages the user opened themselves. Reads the already-rendered
// DOM and POSTs the job data to the local backend. Makes NO request to Seek — it
// only reads the page the user is already viewing.

const BACKEND = 'http://localhost:8000';

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

function parseSearchPage() {
  const cards = document.querySelectorAll(SELECTORS.JOB_CARD);
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
  return [{
    source_job_id: job_id,
    url: window.location.href,
    title,
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

async function main() {
  const path = window.location.pathname;

  // Detail page first: a standalone /job/{id} page. Everything else that looks like
  // a results page (Seek uses SEO slugs like /software-engineer-jobs/in-... as well
  // as the legacy /jobs path) is treated as a search page.
  if (path.startsWith('/job/')) {
    const listings = await waitFor(parseDetailPage, SELECTORS.DETAIL_DESCRIPTION, 8000);
    if (!listings) {
      console.warn('[SeekAssistant] No description found — selectors may need updating.');
      return;
    }
    const result = await ingest(listings);
    if (result) {
      console.log(`[SeekAssistant] Captured detail for job ${listings[0].source_job_id}.`);
      chrome.runtime.sendMessage({ type: 'INGEST_DONE', ...result });
    }
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

main();
