// Overview + Applied tabs: Centrelink mutual-obligation progress
// (docs/centrelink-dashboard-plan.md). JobSeeker asks for a number of applications
// (default 20) in each monthly period; the backend (GET /obligation) buckets every
// application into its period and this file draws it.
//
// Classic script loaded after sidebar.js, sharing its globals: BACKEND, PROFILE_ID, mk,
// tierOf, coverLetterState, autoLetterMin, seekSearchUrl, markApplied, loadJobs, showTab,
// typingIn, overviewSection, appliedSection.

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------
// "2026-10-07" -> "07/10/2026" (the user's own format for a period)
function fmtDay(iso) {
  const [y, m, d] = iso.slice(0, 10).split('-');
  return `${d}/${m}/${y}`;
}

function fmtRange(period) {
  return `${fmtDay(period.start)} – ${fmtDay(period.end)}`;
}

function plural(n, word, many) {
  return `${n} ${n === 1 ? word : (many || `${word}s`)}`;
}

async function getJson(url, init) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Reload whichever dashboard tab is showing, debounced (job_processed fires once per job
// during a scan). Waits while the user is editing the start date or typing in the tab.
let dashTimer = null;
function refreshDashboard() {
  clearTimeout(dashTimer);
  dashTimer = setTimeout(function tryRefresh() {
    if (!overviewSection.hidden) {
      if (ovEditing || typingIn(overviewSection)) {
        dashTimer = setTimeout(tryRefresh, 5000);
        return;
      }
      loadOverview();
    } else if (!appliedSection.hidden && !typingIn(appliedSection)) {
      loadApplied();
    }
  }, 300);
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------
const ovStatusEl = document.getElementById('ov-status');
const ovProgressEl = document.getElementById('ov-progress');
const ovReadyCountEl = document.getElementById('ov-ready-count');
const ovScroller = document.getElementById('ov-scroller');
const ovNav = document.getElementById('ov-nav');
const ovPrev = document.getElementById('ov-prev');
const ovNext = document.getElementById('ov-next');
const ovSearchesEl = document.getElementById('ov-searches');
const ovSearchHint = document.getElementById('ov-search-hint');

let ovData = null;     // the last GET /obligation payload
let ovEditing = false; // the start-date editor is open: reloads wait so it isn't wiped
let ovSeq = 0;         // drops a slow response that a newer load has overtaken

async function loadOverview() {
  const seq = ++ovSeq;
  let obligation, ready, suggestions;
  try {
    [obligation, ready, suggestions] = await Promise.all([
      getJson(`${BACKEND}/obligation?profile_id=${PROFILE_ID}`),
      getJson(`${BACKEND}/jobs?profile_id=${PROFILE_ID}&ready=true&limit=200`),
      // A nicety: the rest of the tab still works without it.
      getJson(`${BACKEND}/jobs/suggested-searches?profile_id=${PROFILE_ID}`).catch(() => null),
    ]);
  } catch {
    if (seq !== ovSeq) return;
    ovStatusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }
  if (seq !== ovSeq) return;
  ovStatusEl.textContent = '';
  ovData = obligation;
  if (!ovEditing) renderProgress();
  // A letter being rewritten, or paused on a question, isn't ready to send yet.
  renderReady(ready.filter(job => coverLetterState(job) === 'ready'));
  renderSuggestions(suggestions);
}

// "7 / 20 applied this period", the bar, the period and what's left.
function renderProgress() {
  const d = ovData;
  const el = ovProgressEl;
  el.innerHTML = '';
  el.className = 'ov-card';
  if (!d.cycle_start) {
    renderStartDateEditor(el, { setup: true });
    return;
  }
  const { applied, days_left: daysLeft } = d.current;
  const met = applied >= d.target;
  el.classList.toggle('met', met);

  const count = mk('div', 'ov-count');
  count.append(
    mk('span', 'big', String(applied)),
    mk('span', 'of', `/ ${d.target}`),
    mk('span', 'lbl', met ? 'applied this period ✓' : 'applied this period'),
  );
  el.appendChild(count);

  const bar = mk('div', 'ov-bar');
  bar.setAttribute('role', 'progressbar');
  bar.setAttribute('aria-valuemin', '0');
  bar.setAttribute('aria-valuemax', String(d.target));
  bar.setAttribute('aria-valuenow', String(applied));
  const fill = mk('div', 'ov-fill');
  fill.style.width = `${Math.min(100, (applied / d.target) * 100)}%`;
  bar.appendChild(fill);
  el.appendChild(bar);

  const line = mk('div', 'ov-period');
  const toGo = met ? 'target met' : `${d.target - applied} to go`;
  line.appendChild(mk('span', null, `${fmtRange(d.current)} · ${plural(daysLeft, 'day')} left · ${toGo}`));
  const edit = mk('button', 'ov-edit-btn', '✎');
  edit.title = 'Change the start date or the target';
  edit.setAttribute('aria-label', edit.title);
  edit.addEventListener('click', () => {
    ovEditing = true;
    el.innerHTML = '';
    el.className = 'ov-card';
    renderStartDateEditor(el, { setup: false });
  });
  line.appendChild(edit);
  el.appendChild(line);

  if (d.waiting_count) {
    const n = d.waiting_count;
    const waiting = mk('button', 'ov-waiting');
    waiting.append(mk('span', null, `❓ ${plural(n, 'letter')} need${n === 1 ? 's' : ''} your answer`), mk('span', null, '›'));
    waiting.title = 'Open the Jobs tab, where the questions are';
    waiting.addEventListener('click', () => showTab('jobs'));
    el.appendChild(waiting);
  }
}

// The start date + target form: the whole progress card until a date is set (setup), or
// the ✎ editor afterwards.
function renderStartDateEditor(el, { setup }) {
  const d = ovData;
  const wrap = mk('div', 'ov-setup');
  if (setup) {
    wrap.appendChild(mk('h3', null, 'When does your Centrelink month start?'));
    wrap.appendChild(mk('p', null,
      'Your mutual-obligation period runs one month from this date (7 Oct → 6 Nov), then resets. '
      + `Until it's set, applications are counted by calendar month: ${d.current.applied} so far in ${fmtRange(d.current)}.`));
  } else {
    wrap.appendChild(mk('h3', null, 'Your Centrelink period'));
    wrap.appendChild(mk('p', null, 'Pick any date one of your periods started on; earlier and later periods follow from it.'));
  }

  const form = mk('form', 'ov-form');
  const dateLabel = mk('label', null, 'Period starts');
  const dateInput = document.createElement('input');
  dateInput.type = 'date';
  dateInput.required = true;
  dateInput.value = d.cycle_start || '';
  dateLabel.appendChild(dateInput);

  const targetLabel = mk('label', null, 'Target');
  const targetInput = document.createElement('input');
  targetInput.type = 'number';
  targetInput.min = '1';
  targetInput.max = '100';
  targetInput.step = '1';
  targetInput.value = String(d.target);
  targetLabel.appendChild(targetInput);

  const save = mk('button', 'btn btn-primary', 'Save');
  save.type = 'submit';
  form.append(dateLabel, targetLabel, save);
  if (!setup) {
    const cancel = mk('button', 'btn', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', () => { ovEditing = false; renderProgress(); });
    form.appendChild(cancel);
  }
  const err = mk('div', 'ov-err');
  form.appendChild(err);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const target = Number(targetInput.value);
    if (!dateInput.value) { err.textContent = 'Pick the date a period starts.'; return; }
    if (!Number.isInteger(target) || target < 1 || target > 100) { err.textContent = 'The target is a whole number from 1 to 100.'; return; }
    save.disabled = true;
    err.textContent = '';
    try {
      await getJson(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ obligation_cycle_start: dateInput.value, obligation_target: target }),
      });
    } catch {
      save.disabled = false;
      err.textContent = "Couldn't save — check the date, or is the backend running?";
      return;
    }
    ovEditing = false;
    loadOverview();
  });

  wrap.appendChild(form);
  el.appendChild(wrap);
}

// The Keep Applying row: one mini-card per job with a letter, best match first.
function renderReady(jobs) {
  const keepLeft = ovScroller.scrollLeft;
  ovScroller.innerHTML = '';
  setReadyCount(jobs.length);
  if (!jobs.length) {
    ovScroller.appendChild(mk('div', 'ov-empty',
      `No cover letters waiting. Letters are written for matches scoring ${autoLetterMin}+, so scan more jobs below.`));
  } else {
    for (const job of jobs) ovScroller.appendChild(renderMiniCard(job));
  }
  // Keep the user's place across live reloads (no smooth scroll back to it).
  ovScroller.style.scrollBehavior = 'auto';
  ovScroller.scrollLeft = keepLeft;
  ovScroller.style.scrollBehavior = '';
  updateArrows();
}

function setReadyCount(n) {
  ovReadyCountEl.textContent = n ? `${plural(n, 'letter')} ready to send` : 'No letters ready';
  ovReadyCountEl.classList.toggle('none', !n);
}

function renderMiniCard(job) {
  const card = mk('div', 'mini-card');
  card.dataset.tier = tierOf(job.score);
  card.dataset.jobId = job.job_id;

  const top = mk('div', 'mc-top');
  if (job.score != null) top.appendChild(mk('span', 'score', String(Math.round(job.score))));
  if (job.location) {
    const loc = mk('span', 'mc-loc', job.location);
    loc.title = job.location;
    top.appendChild(loc);
  }
  card.appendChild(top);

  const title = mk('div', 'mc-title', job.title || '(untitled)');
  title.title = job.title || '';
  card.appendChild(title);
  if (job.company) card.appendChild(mk('div', 'mc-company', job.company));

  const actions = mk('div', 'mc-actions');
  const open = mk('button', 'btn btn-sm', 'Open ad');
  open.title = 'Open the ad on Seek; the Quick Apply panel there has the letter';
  open.addEventListener('click', () => chrome.tabs.create({ url: job.url }));
  const copy = mk('button', 'btn btn-sm', '⧉ Letter');
  copy.title = 'Copy the cover letter (your edits if you made any)';
  copy.addEventListener('click', () => copyLetter(job.job_id, copy));
  actions.append(open, copy);
  card.appendChild(actions);

  // Two clicks: an application can't be un-marked (it's Centrelink evidence), so a
  // stray click mustn't count one.
  const applied = mk('button', 'mc-applied', '✓ Mark applied');
  applied.title = 'Count this job as applied for this period';
  let disarm = null;
  const reset = () => { applied.classList.remove('confirm'); applied.textContent = '✓ Mark applied'; };
  applied.addEventListener('click', async () => {
    if (!applied.classList.contains('confirm')) {
      applied.classList.add('confirm');
      applied.textContent = 'Click again to confirm';
      disarm = setTimeout(reset, 4000);
      return;
    }
    clearTimeout(disarm);
    applied.disabled = true;
    applied.textContent = 'Saving…';
    try {
      await markApplied(job.job_id);
    } catch {
      applied.disabled = false;
      reset();
      alert('Could not mark applied — is the backend running?');
      return;
    }
    countApplied();
    loadJobs(); // the Jobs card shows ✓ Applied too
    card.classList.add('leaving');
    setTimeout(() => {
      card.remove();
      const left = ovScroller.querySelectorAll('.mini-card').length;
      setReadyCount(left);
      if (!left) renderReady([]);
      updateArrows();
    }, 260);
  });
  card.appendChild(applied);
  return card;
}

// Bump the counter at once rather than waiting for a reload.
function countApplied() {
  if (!ovData) return;
  ovData.current.applied += 1;
  if (!ovEditing) renderProgress();
}

async function copyLetter(jobId, btn) {
  const label = btn.textContent;
  try {
    const job = await getJson(`${BACKEND}/jobs/${jobId}?profile_id=${PROFILE_ID}`);
    const text = job.cover_letter && (job.cover_letter.edited_content || job.cover_letter.generated_content);
    if (!text) throw new Error('no letter');
    await navigator.clipboard.writeText(text);
    btn.textContent = 'Copied ✓';
  } catch {
    btn.textContent = 'Copy failed';
  }
  setTimeout(() => { btn.textContent = label; }, 1500);
}

// ‹ › for the row: a mouse wheel doesn't scroll sideways. A press moves one screenful.
function updateArrows() {
  const s = ovScroller;
  ovNav.hidden = s.scrollWidth <= s.clientWidth + 2;
  ovPrev.disabled = s.scrollLeft <= 2;
  ovNext.disabled = s.scrollLeft + s.clientWidth >= s.scrollWidth - 2;
}

function scrollStep() {
  const card = 210; // 200px card + 10px gap
  return Math.max(1, Math.floor(ovScroller.clientWidth / card)) * card;
}

ovPrev.addEventListener('click', () => ovScroller.scrollBy({ left: -scrollStep(), behavior: 'smooth' }));
ovNext.addEventListener('click', () => ovScroller.scrollBy({ left: scrollStep(), behavior: 'smooth' }));
ovScroller.addEventListener('scroll', updateArrows, { passive: true });
window.addEventListener('resize', updateArrows);

// Scan More Jobs: the next 3 suggested searches. Ignores the Jobs-tab banner's 7-day
// dismissal: this tab is where you come to pick the next search. Opening one is a normal
// user-initiated tab (no request to Seek from here).
function renderSuggestions(data) {
  ovSearchesEl.innerHTML = '';
  const list = (data?.suggestions || []).slice(0, 3);
  ovSearchHint.hidden = !list.length;
  if (!list.length) {
    ovSearchesEl.appendChild(mk('div', 'ov-empty',
      'No suggestions yet. They come from the jobs you match well: search Seek yourself, then press Scan Page.'));
    return;
  }
  const where = data.location || '';
  for (const phrase of list) {
    const row = mk('button', 'ov-search');
    row.title = `Open a Seek search for "${phrase}"${where ? ` in ${where}` : ''}`;
    row.append(mk('span', null, '🔍'), mk('span', 'q', phrase));
    if (where) row.appendChild(mk('span', 'where', where));
    row.appendChild(mk('span', 'chev', '›'));
    row.addEventListener('click', () => chrome.tabs.create({ url: seekSearchUrl(phrase, where) }));
    ovSearchesEl.appendChild(row);
  }
}

// Scan Page from the Overview: the Jobs list is the scan's progress display (and where
// its notices appear), so go there.
scanBtn.addEventListener('click', () => {
  if (!overviewSection.hidden) showTab('jobs');
});
