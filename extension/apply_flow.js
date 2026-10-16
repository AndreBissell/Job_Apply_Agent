// Quick Apply's "application sent" page (docs/centrelink-ease-apply-plan.md). Loaded
// before content_script.js, whose apply watcher calls checkApplySent() after every DOM
// change; at call time this file uses its backendFetch, resolveOrCreateJob and cleanText.
//
// On /job/{id}/apply/success with h1#applicationSent on the page, Seek has accepted the
// application, so it is recorded without a click (POST /jobs/{id}/quick-apply-sent) and the
// page is screenshotted as Centrelink evidence (POST /jobs/{id}/screenshot; the file stays
// in the app's local screenshots folder, nothing goes to Downloads). External ads never
// reach this page, so they stay manual Mark Applied.
//
// A pure read of a page the user opened: no click, no navigation, no request to Seek, and
// the "You might also like" links below the heading are never read. The heading's text
// greets the user by first name; only its presence is checked, never its text.

const APPLY_SENT_HOST_ID = 'seek-assistant-apply-sent-host';
const APPLY_SENT_RETRY_MS = 10000;   // backend unreachable: try again this much later
const APPLY_SENT_AUTO_TRIES = 3;     // then wait for the panel's Retry button
const APPLY_SENT_SETTLE_MS = 800;    // let the page finish drawing before the screenshot

// One recording per page: { sourceJobId, state: 'working' | 'done' | 'failed', tries,
// jobId, shot: 'saved' | 'capture_failed' | 'upload_failed' | null }.
let applySent = null;

// A new stub row's fields, for a job never captured before (the user reached Quick Apply
// without its ad being saved here). The page has no job title, so a placeholder is used;
// the ad page replaces it on a later capture (POST /ingest). The employer is in
// "Your application has been sent to <employer>".
function applySentStub(sourceJobId) {
  let company = null;
  const box = document.querySelector(SELECTORS.APPLY_SENT_HEADING)?.parentElement?.parentElement;
  for (const span of box ? box.querySelectorAll('span') : []) {
    const m = cleanText(span.textContent).match(/application has been sent to (.+?)\.?$/i);
    if (m) { company = m[1]; break; }
  }
  return { title: `Seek job ${sourceJobId}`, company };
}

async function checkApplySent() {
  const sourceJobId = extractApplySentJobId(location.pathname);
  const onSentPage = !!(sourceJobId && document.querySelector(SELECTORS.APPLY_SENT_HEADING));
  if (!onSentPage) {
    // Not (or no longer) the sent page; a half-drawn sent page is checked again on the
    // next DOM change.
    if (applySent && applySent.sourceJobId !== sourceJobId) {
      applySent = null;
      document.getElementById(APPLY_SENT_HOST_ID)?.remove();
    }
    return;
  }
  if (applySent && applySent.sourceJobId === sourceJobId && applySent.state !== 'failed') return;
  if (applySent?.state === 'failed' && applySent.tries >= APPLY_SENT_AUTO_TRIES) return;
  await recordApplySent(sourceJobId);
}

async function recordApplySent(sourceJobId) {
  const tries = applySent?.sourceJobId === sourceJobId ? applySent.tries : 0;
  applySent = { sourceJobId, state: 'working', tries: tries + 1, jobId: null, shot: null };
  const mine = applySent;

  const jobId = await resolveOrCreateJob(sourceJobId, applySentStub(sourceJobId));
  let recorded = null;
  if (jobId) {
    try {
      const res = await backendFetch(`${BACKEND}/jobs/${jobId}/quick-apply-sent?profile_id=1`, { method: 'POST' });
      if (res.ok) recorded = await res.json();
      else console.warn('[SeekAssistant] Recording the application failed', res.status);
    } catch (e) {
      console.warn('[SeekAssistant] Backend not reachable (is run_api.py running?):', e.message);
    }
  }
  if (applySent !== mine) return; // the page moved on meanwhile

  if (!recorded) {
    mine.state = 'failed';
    renderApplySentPanel();
    if (mine.tries < APPLY_SENT_AUTO_TRIES) setTimeout(checkApplySent, APPLY_SENT_RETRY_MS);
    return;
  }
  mine.state = 'done';
  mine.jobId = jobId;
  console.log(`[SeekAssistant] Quick Apply sent for job ${sourceJobId}: recorded as applied`
    + (recorded.newly_applied ? '.' : ' (already recorded).'));

  // The screenshot comes BEFORE the panel is drawn, so the panel isn't in it (a panel
  // left from a failed try goes first).
  document.getElementById(APPLY_SENT_HOST_ID)?.remove();
  mine.shot = await captureApplyEvidence(jobId);
  if (applySent === mine) renderApplySentPanel();
}

// Resolves once this tab is the one on screen: captureVisibleTab takes whatever tab is
// active in the window, so a capture while the user looks at another tab would save the
// wrong page.
function whenTabVisible() {
  if (document.visibilityState === 'visible') return Promise.resolve();
  return new Promise((resolve) => {
    const onChange = () => {
      if (document.visibilityState !== 'visible') return;
      document.removeEventListener('visibilitychange', onChange);
      resolve();
    };
    document.addEventListener('visibilitychange', onChange);
  });
}

// Screenshot this page and store it against the job. 'saved', or what went wrong.
async function captureApplyEvidence(jobId) {
  await whenTabVisible();
  await new Promise((r) => setTimeout(r, APPLY_SENT_SETTLE_MS));
  let dataUrl = null;
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'CAPTURE_SCREENSHOT' });
    dataUrl = resp?.dataUrl || null;
    if (!dataUrl) console.warn('[SeekAssistant] Screenshot capture failed:', resp?.error);
  } catch (e) {
    console.warn('[SeekAssistant] Screenshot capture failed:', e.message);
  }
  if (!dataUrl) return 'capture_failed';
  try {
    const res = await backendFetch(`${BACKEND}/jobs/${jobId}/screenshot?profile_id=1`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data_url: dataUrl }),
    });
    if (res.ok) return 'saved';
    console.warn('[SeekAssistant] Screenshot upload failed', res.status);
  } catch (e) {
    console.warn('[SeekAssistant] Screenshot upload failed (backend unreachable):', e.message);
  }
  return 'upload_failed';
}

// Retake: hide the panel for the capture so it isn't in the picture, then show it again.
async function retakeApplyEvidence(btn) {
  const mine = applySent;
  if (!mine?.jobId) return;
  btn.disabled = true;
  btn.textContent = 'Saving…';
  const host = document.getElementById(APPLY_SENT_HOST_ID);
  if (host) host.style.visibility = 'hidden';
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  mine.shot = await captureApplyEvidence(mine.jobId);
  if (host) host.style.visibility = '';
  if (applySent === mine) renderApplySentPanel();
}

const APPLY_SENT_SHOT_TEXT = {
  saved: 'Screenshot saved in the app.',
  capture_failed: 'The screenshot could not be taken.',
  upload_failed: 'The screenshot could not be saved (is run_api.py running?).',
};

function renderApplySentPanel() {
  document.getElementById(APPLY_SENT_HOST_ID)?.remove();
  const s = applySent;
  if (!s) return;

  const host = document.createElement('div');
  host.id = APPLY_SENT_HOST_ID;
  host.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;';
  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = `
    :host { all: initial; }
    .panel { font-family: -apple-system, Segoe UI, Roboto, sans-serif; font-size: 13px;
      width: 280px; max-width: calc(100vw - 32px); background: #fff; color: #1c2330;
      border: 1px solid #cbd5e1; border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.18);
      overflow: hidden; }
    .hdr { display: flex; align-items: center; justify-content: space-between;
      padding: 8px 10px; background: #2557a7; color: #fff; }
    .hdr strong { font-size: 12px; }
    .x { background: none; border: none; color: #fff; font-size: 16px; cursor: pointer; line-height: 1; }
    .body { padding: 10px; }
    .line { margin: 0 0 6px; }
    .line.ok { color: #047857; font-weight: 600; }
    .line.bad { color: #b45309; font-weight: 600; }
    button.act { font-size: 11px; padding: 5px 9px; border: 1px solid #cbd5e1;
      border-radius: 6px; background: #fff; cursor: pointer; color: #374151; }
    button.act:hover { background: #f3f4f6; }
  `;
  shadow.appendChild(style);

  const panel = document.createElement('div');
  panel.className = 'panel';
  const hdr = document.createElement('div');
  hdr.className = 'hdr';
  const title = document.createElement('strong');
  title.textContent = 'Seek Job Assistant';
  const x = document.createElement('button');
  x.className = 'x';
  x.title = 'Hide';
  x.textContent = '×';
  x.addEventListener('click', () => host.remove());
  hdr.append(title, x);

  const body = document.createElement('div');
  body.className = 'body';
  const line = (text, cls) => {
    const p = document.createElement('p');
    p.className = `line ${cls || ''}`;
    p.textContent = text;
    body.appendChild(p);
  };
  const button = (text, onClick) => {
    const b = document.createElement('button');
    b.className = 'act';
    b.textContent = text;
    b.addEventListener('click', () => onClick(b));
    body.appendChild(b);
  };

  if (s.state === 'failed') {
    line('Could not record this application yet. Is run_api.py running?', 'bad');
    button('Retry', () => recordApplySent(s.sourceJobId));
  } else if (s.state === 'done') {
    line('✓ Application recorded as applied.', 'ok');
    line(APPLY_SENT_SHOT_TEXT[s.shot] || '', s.shot === 'saved' ? '' : 'bad');
    button(s.shot === 'saved' ? 'Retake screenshot' : 'Try the screenshot again', retakeApplyEvidence);
  } else {
    return; // still working: nothing to show yet
  }

  panel.append(hdr, body);
  shadow.appendChild(panel);
  document.body.appendChild(host);
}
