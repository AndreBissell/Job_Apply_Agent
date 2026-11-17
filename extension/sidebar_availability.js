// Availability checks: is a stored listing still advertised? The backend picks which one and in
// which mode (GET /availability/next) and records the verdict (POST /availability/{id}); this file
// does the only part that touches Seek, in the user's own browser, one background tab at a time.
// Classic script: sidebar.html loads it after sidebar_scan.js (it reuses sleep, injectFn,
// waitForTabComplete, seekPace and `scanning`) and before sidebar_personalise.js; all share one
// global scope. Nothing here runs at load except arming the timer.
//
// Two modes (docs/availability-fast-mode-plan.md). The BACKEND decides the mode and holds its caps
// (fast: 10 top jobs seen live or 15 pages a run, one run an hour; DAILY_CAP for all checks); this
// file only paces the pages: fast ≥ CHECK_FAST_DELAY_MS apart and started as soon as the panel is
// ready, background ≥ CHECK_DELAY_MS apart, ≤ CHECK_SESSION_CAP pages a session, first one
// CHECK_START_DELAY_MS after load. CHECK_SESSION_HARD_LIMIT is a backstop whatever the backend says.
//
// Policy (CLAUDE.md, "Availability re-checks"): a stored listing's own /job/{id} page only, never
// a link found on it; never alongside a scan; stop for CHECK_COOLDOWN_MS if Seek looks like it is
// challenging the browser. A scan always wins (cancelAvailabilityCheck).
//
// Why the side panel and not background.js: the pacing is setTimeout and a tab handle, which an
// MV3 service worker loses when it is killed, and the scan flag lives here.
// ---------------------------------------------------------------------------
const CHECK_DELAY_MS = 15000;                       // background pace; the policy floor is SCAN_DELAY_MS
const CHECK_FAST_DELAY_MS = 5000;                   // fast-mode pace: the policy floor itself (= SCAN_DELAY_MS)
const CHECK_SESSION_CAP = 8;                        // background pages per session (fast pages: the backend's cap)
const CHECK_SESSION_HARD_LIMIT = 25;                // pages per session in all, whatever the backend says (15 + 8 is the most it should give)
const CHECK_START_DELAY_MS = 60000;                 // background only: after the panel loads, so Scan Page can go first
const CHECK_SESSION_INTERVAL_MS = 30 * 60 * 1000;   // between sessions while the panel stays open
const CHECK_MISS_STREAK_LIMIT = 5;                  // pages opened in a row that were not live (missing or unknown) = blocked?
const CHECK_COOLDOWN_MS = 6 * 60 * 60 * 1000;       // pause after a suspected block
const CHECK_COOLDOWN_KEY = 'availabilityCooldownUntil';
const CHECK_HOSTS = ['au.seek.com', 'www.seek.com.au'];

let checkingAvailability = false;   // a session is running
let activeCheck = null;             // { tabId, cancelled, signal, cancel, done } for the page open now

// Runs inside the checked tab. Same signal as the scan's detail scraper (the description
// selector, up to 8 s), but it reports what the page IS so a redirect or a challenge page is
// told apart from a closed ad. The Cloudflare markers are the well-known interstitial title
// and elements, not Seek selectors; verify them live (see the hand-check list).
async function pageCheckListing(sel) {
  const start = Date.now();
  let descEl = null;
  while (Date.now() - start < 8000) {
    descEl = document.querySelector(sel.description);
    if (descEl && descEl.innerText.trim()) break;
    await new Promise(r => setTimeout(r, 400));
  }
  const title = document.title || '';
  return {
    host: location.hostname,
    path: location.pathname,
    title,
    descLength: descEl ? descEl.innerText.trim().length : 0,
    challenge: /just a moment|attention required|verify you are human|access denied/i.test(title)
      || !!document.querySelector('#challenge-form, #cf-challenge-running, iframe[src*="challenges.cloudflare.com"]'),
  };
}

// What a visit means. 'missing' is only a candidate until the session proves the browser can
// still see descriptions (see runAvailabilitySession). Anything odd is 'unknown', never 'missing'.
function classifyCheck(ev, sourceJobId) {
  if (!ev) return 'unknown';
  if (ev.challenge) return 'challenge';
  const stillThere = CHECK_HOSTS.includes(ev.host) && new RegExp(`^/job/${sourceJobId}(/|$)`).test(ev.path);
  if (!stillThere) return 'unknown'; // redirected away: we don't know what that means, so don't flag
  return ev.descLength > 0 ? 'live' : 'missing';
}

// Only ever open a listing's own page on Seek, rebuilt from its id (drops any query string).
function checkUrlFor(job) {
  try {
    const u = new URL(job.url);
    const m = u.pathname.match(/^\/job\/(\d+)/);
    if (u.protocol !== 'https:' || !CHECK_HOSTS.includes(u.hostname) || !m || m[1] !== String(job.source_job_id)) {
      return null;
    }
    return `${u.origin}/job/${m[1]}`;
  } catch {
    return null;
  }
}

async function fetchNextCheck(tried) {
  try {
    const res = await fetch(`${BACKEND}/availability/next?profile_id=${PROFILE_ID}&exclude=${tried.join(',')}`);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

// `mode` is the mode the page was OPENED in (the backend starts a fast run on the first fast one).
async function postVerdict(jobId, verdict, mode) {
  try {
    const res = await fetch(`${BACKEND}/availability/${jobId}?profile_id=${PROFILE_ID}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict, mode }),
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

// Visit one listing. Returns 'live' | 'missing' | 'unknown' | 'challenge', 'refused' when its URL
// isn't a Seek listing page so nothing was opened (recorded as 'unknown'), or null when a scan
// cancelled it (then nothing is recorded: the job simply comes up again later).
async function checkOne(job, mode) {
  const url = checkUrlFor(job);
  if (!url) {
    console.log(`[availability] job ${job.job_id}: not a Seek listing URL, not opened`);
    return 'refused';
  }
  const handle = { tabId: null, cancelled: false };
  handle.signal = new Promise(r => { handle.cancel = r; });
  handle.done = new Promise(r => { handle.finish = r; });
  activeCheck = handle;
  let ev = null;
  let tab = null;
  try {
    tab = await chrome.tabs.create({ url, active: false });
    handle.tabId = tab.id;
    // Tell the worker this is not a page to capture or flag from the content script's side.
    await chrome.runtime.sendMessage({ type: 'SCAN_TAB_ADD', tabId: tab.id }).catch(() => {});
    if (!handle.cancelled) await Promise.race([waitForTabComplete(tab.id, 15000), handle.signal]);
    if (!handle.cancelled) {
      ev = await injectFn(tab.id, pageCheckListing, [{ description: SELECTORS.DETAIL_DESCRIPTION }]);
    }
  } catch (e) {
    console.log(`[availability] job ${job.job_id}: could not read the tab (${e.message})`);
  } finally {
    if (tab) {
      await chrome.tabs.remove(tab.id).catch(() => {});
      await chrome.runtime.sendMessage({ type: 'SCAN_TAB_REMOVE', tabId: tab.id }).catch(() => {});
    }
    activeCheck = null;
    handle.finish();
  }
  if (handle.cancelled) return null;
  const verdict = classifyCheck(ev, job.source_job_id);
  console.log(`[availability] job ${job.job_id} (${mode}, ${job.group}) -> ${verdict}`, ev);
  return verdict;
}

// Called by scanPage: close the page being checked right now and wait until it is gone.
async function cancelAvailabilityCheck() {
  const c = activeCheck;
  if (!c) return;
  c.cancelled = true;
  c.cancel();
  if (c.tabId != null) await chrome.tabs.remove(c.tabId).catch(() => {});
  await c.done;
}

async function cooldownActive() {
  try {
    const got = await chrome.storage.local.get(CHECK_COOLDOWN_KEY);
    return Number(got[CHECK_COOLDOWN_KEY]) > Date.now();
  } catch {
    return false;
  }
}

async function startCooldown(why) {
  console.warn(`[availability] ${why} — pausing checks for ${CHECK_COOLDOWN_MS / 3600000} h.`);
  try {
    await chrome.storage.local.set({ [CHECK_COOLDOWN_KEY]: Date.now() + CHECK_COOLDOWN_MS });
  } catch { /* the pause just won't outlive this panel */ }
}

// One session. Each page's mode comes from the backend: fast pages are paced CHECK_FAST_DELAY_MS
// and capped by the backend (it switches to background when the fast run is over); background
// pages are paced CHECK_DELAY_MS, at most CHECK_SESSION_CAP of them. Returns 'busy' when it could
// not start because a scan or another session is running, else 'done'.
//
// False-positive guard: a "no description" result is only a candidate. It is sent as 'missing'
// once this session has also seen a LIVE page (proof this browser can still see descriptions);
// if the session ends without one, every held result is sent as 'unknown' instead. So a blocked
// or logged-out browser, where every page looks empty, can never flag anything.
//
// Block guard (plan D7): CHECK_MISS_STREAK_LIMIT pages opened in a row that were not live
// ('missing' or 'unknown', e.g. a redirect everywhere) pause the checks; a live page resets the
// count, and a 'refused' URL opened nothing so says nothing about Seek.
async function runAvailabilitySession() {
  if (checkingAvailability || scanning) return 'busy';
  checkingAvailability = true;
  const tried = [];
  const heldMisses = [];   // { id, mode } of 'missing' results not yet corroborated by a live page
  let sawLive = false;
  let notLiveStreak = 0;
  let backgroundPages = 0;
  let changed = false;
  try {
    if (await cooldownActive()) return 'done';
    while (tried.length < CHECK_SESSION_HARD_LIMIT) {
      if (scanning) break;
      const next = await fetchNextCheck(tried);
      if (!next || !next.enabled || !next.job || next.remaining_today <= 0) break;
      const job = next.job;
      const mode = next.mode === 'fast' ? 'fast' : 'background';
      if (mode === 'background' && backgroundPages >= CHECK_SESSION_CAP) break;

      await seekPace(mode === 'fast' ? CHECK_FAST_DELAY_MS : CHECK_DELAY_MS);
      if (scanning) break; // a scan started while we waited our turn
      if (mode === 'background') backgroundPages += 1;
      tried.push(job.job_id);
      const verdict = await checkOne(job, mode);
      if (verdict === null) break; // a scan took over

      if (verdict === 'live') {
        sawLive = true;
        notLiveStreak = 0;
        const out = await postVerdict(job.job_id, 'live', mode);
        if (out) changed = true;
        for (const held of heldMisses.splice(0)) {
          const r = await postVerdict(held.id, 'missing', held.mode);
          if (r?.expired) changed = true;
        }
        continue;
      }
      if (verdict === 'missing') {
        if (sawLive) {
          const r = await postVerdict(job.job_id, 'missing', mode);
          if (r?.expired) changed = true;
        } else {
          heldMisses.push({ id: job.job_id, mode });
        }
      } else {
        await postVerdict(job.job_id, 'unknown', mode); // 'unknown', 'challenge' and 'refused'
        if (verdict === 'challenge') {
          await startCooldown('Seek showed a challenge page');
          break;
        }
      }
      if (verdict !== 'refused') notLiveStreak += 1;
      if (notLiveStreak >= CHECK_MISS_STREAK_LIMIT) {
        await startCooldown(`${notLiveStreak} pages in a row were not live (no description or a redirect: a block, or a bad selector?)`);
        break;
      }
    }
  } finally {
    // Never corroborated: prove nothing.
    for (const held of heldMisses) await postVerdict(held.id, 'unknown', held.mode);
    checkingAvailability = false;
    if (changed) scheduleReload(); // a job was flagged or un-flagged: refresh the list
  }
  return 'done';
}

// D6: fast mode starts as soon as the panel is ready (a Scan Page click still wins: it cancels
// the page in flight); the background trickle waits CHECK_START_DELAY_MS so a scan can go first.
// Asking /availability/next changes nothing, so this first look costs no page and starts no run.
function armAvailabilityChecks() {
  const tick = async (delay) => {
    setTimeout(async () => {
      let state = 'done';
      try { state = await runAvailabilitySession(); }
      catch (e) { console.warn('[availability] session failed:', e); }
      tick(state === 'busy' ? 2 * 60 * 1000 : CHECK_SESSION_INTERVAL_MS);
    }, delay);
  };
  fetchNextCheck([]).then((next) => {
    const fastNow = !!(next && next.enabled && next.job && next.mode === 'fast');
    tick(fastNow ? 0 : CHECK_START_DELAY_MS);
  });
}

backendReady.then(armAvailabilityChecks);
