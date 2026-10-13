// Scan Page: the 1-hop scan (SCAN_DELAY_MS, SCAN_PAGES_CEILING, scanPage) and the two functions
// injected into Seek tabs. Classic script: sidebar.html loads it after sidebar_suggest.js and
// before sidebar.js and dashboard.js (which uses scanBtn at load time); all share one scope.
// ---------------------------------------------------------------------------
// Scan Page (1-hop rule: links from a page the user opened, ≥5s apart, capped)
// ---------------------------------------------------------------------------
// How many job pages one scan opens. User-tunable in "Personalise metrics"
// (stored server-side as scan_max_pages) between 1 and SCAN_PAGES_CEILING; the
// ceiling mirrors the API's own validation and is the policy cap — the setting
// can move under it but never past it. WEAK_STREAK_LIMIT below still does the
// real work of cutting a bad search short.
const SCAN_PAGES_CEILING = 25;
let scanMaxPages = 10;
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

// Runs inside a Seek detail page — scrape the job data. An injected function
// can't see this panel's globals (SELECTORS), so the selectors arrive as the
// `sel` argument, passed through injectFn's `args`.
async function pageScrapeDetail(sel) {
  const m = location.pathname.match(/\/job\/(\d+)/);
  if (!m) return null;
  const start = Date.now();
  let descEl = null;
  while (Date.now() - start < 8000) {
    descEl = document.querySelector(sel.description);
    if (descEl && descEl.innerText.trim()) break;
    await new Promise(r => setTimeout(r, 400));
  }
  const titleEl = document.querySelector(sel.title);
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

async function injectFn(tabId, func, args = []) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
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
    const urls = newUrls.slice(0, scanMaxPages);

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
          // Tell the worker this is a scan tab, so the content script running in it
          // doesn't also capture the job (this scan ingests it below). The page can't
          // be idle yet, so this lands first; if it ever lost, the result is just
          // the old double capture.
          await chrome.runtime.sendMessage({ type: 'SCAN_TAB_ADD', tabId: bgTab.id }).catch(() => {});
          await waitForTabComplete(bgTab.id, 15000);
        } catch (e) {
          scanLog(`${label} could not open tab: ${e.message}`);
          if (bgTab) {
            await chrome.tabs.remove(bgTab.id).catch(() => {});
            await chrome.runtime.sendMessage({ type: 'SCAN_TAB_REMOVE', tabId: bgTab.id }).catch(() => {});
          }
          continue;
        }

        let payload = null;
        try {
          payload = await injectFn(bgTab.id, pageScrapeDetail, [{
            description: SELECTORS.DETAIL_DESCRIPTION,
            title: SELECTORS.DETAIL_TITLE,
          }]);
        }
        catch (e) { scanLog(`${label} could not scrape: ${e.message}`); }
        finally {
          await chrome.tabs.remove(bgTab.id).catch(() => {});
          await chrome.runtime.sendMessage({ type: 'SCAN_TAB_REMOVE', tabId: bgTab.id }).catch(() => {});
        }

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
    refreshDashboard();
  } finally {
    scanning = false;
    scanBtn.disabled = false;
  }
}

scanBtn.addEventListener('click', scanPage);
document.getElementById('refresh-btn').addEventListener('click', () => { loadJobs(); refreshDashboard(); });
