// Pipeline tab (docs/pipeline-tab-plan.md; its ids keep the old "jobs" name): every scored job
// not applied for, as compact rows in a capped scroll box with filter chips, search and sort,
// and a drill-in detail view (fillDetail) with the job's actions.
// Classic script: sidebar.html loads it after sidebar_shared.js and before sidebar.js;
// all of them share one global scope, so load order is the contract.
// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const jobStatusEl = document.getElementById('job-status');
const jobListEl = document.getElementById('job-list');
const plPage = document.getElementById('pl-page');
const plBox = document.getElementById('pl-box');
const plBoxWrap = document.getElementById('pl-box-wrap');
const plChips = document.getElementById('pl-chips');
const plCount = document.getElementById('pl-count');
const plSearch = document.getElementById('pl-search');
const plSort = document.getElementById('pl-sort');
const plDetailView = document.getElementById('job-detail-view');
const plDetailEl = document.getElementById('pl-detail');

const PL_SEARCH_FROM = 10; // the search box appears once there are more jobs than this
const PL_PREFS_KEY = 'pipelineView';

let plJobs = null;      // the last GET /jobs?applied=false rows; null until the first load
let plOpenJob = null;   // the job whose detail view is open: {id, state}
let plListScroll = 0;   // the box's scrollTop when the detail view opened
let plSeq = 0;          // drops a slow response a newer load has overtaken
const plView = { filter: 'all', sort: 'score', query: '' };

// Filter chips: the row statuses each holds. "Answer" shows only when a job needs one.
const PL_FILTERS = [
  { key: 'all', label: 'All', has: null },
  { key: 'question', label: '❓ Answer', has: ['question'], onlyIfAny: true },
  { key: 'letter', label: '✅ Letter', has: ['letter'] },
  { key: 'writing', label: '✍ Writing', has: ['writing', 'queued'] },
  { key: 'none', label: 'No letter', has: ['none', 'failed'] },
  { key: 'check', label: '🕓 Check due', has: ['check'] },
];

// The filter and sort are a per-viewer convenience: remembered, never relied on.
const plPrefsReady = (async () => {
  try {
    const saved = (await chrome.storage.local.get(PL_PREFS_KEY))[PL_PREFS_KEY] || {};
    if (PL_FILTERS.some(f => f.key === saved.filter)) plView.filter = saved.filter;
    if (['score', 'newest'].includes(saved.sort)) plView.sort = saved.sort;
  } catch { /* defaults */ }
  plSort.value = plView.sort;
})();

function savePlView() {
  try {
    chrome.storage.local.set({ [PL_PREFS_KEY]: { filter: plView.filter, sort: plView.sort } });
  } catch { /* not remembered */ }
}

// True while the user is typing somewhere a Pipeline reload could wipe (a letter edit, an
// answer). The search box doesn't count: the rows redraw around it.
function plBusy() {
  return typingIn(jobsSection) && document.activeElement !== plSearch;
}

// ---------------------------------------------------------------------------
// Cover-letter state and the row's one status chip
// ---------------------------------------------------------------------------
// Cover-letter card state (docs/extension-revamp-plan.md §5.1; the run states are
// Phase 8 of docs/cover-letter-loop-plan.md). job.letter_run is the newest pipeline
// run: {run_id, status, open_questions} or null. Shared with the Overview (dashboard.js).
//  - 'question' a run is paused on questions for the user (even if an older letter exists)
//  - 'writing'  a run is working, or its answers are in and it resumes shortly
//  - 'ready'    letter generated, has_cover_letter is true
//  - 'failed'   the last run produced nothing; the idle loop retries after a cooldown
//  - 'pending'  score >= autoLetterMin, idle loop hasn't reached it yet
//  - 'none'     below autoLetterMin and no letter
function coverLetterState(job) {
  const run = job.letter_run;
  if (run?.status === 'waiting_user') return 'question';
  if (run?.status === 'running' || run?.status === 'answered') return 'writing';
  if (job.letter_queued) return 'writing'; // queued by "+ letters" or "Write a letter"
  if (job.has_cover_letter) return 'ready';
  if (run?.status === 'failed' || run?.status === 'budget_stopped') return 'failed';
  if (job.score != null && job.score >= autoLetterMin) return 'pending';
  return 'none';
}

// One status per row, in priority order (plan D4/D5). A job waiting on a letter it can't get
// until an availability check sees it live (fresh=false) says so instead of "queued".
function rowStatus(job) {
  const s = coverLetterState(job);
  if (s === 'question') return 'question';
  if (s === 'writing') return 'writing';
  if (s === 'ready') return 'letter';
  if (s === 'failed') return 'failed';
  if (job.fresh === false) return 'check';
  return s === 'pending' ? 'queued' : 'none';
}

const PL_STATUS = {
  question: { text: '❓ Answer', title: 'A cover letter is waiting on your answer' },
  writing:  { text: '✍ Writing', title: 'A cover letter is being written' },
  queued:   { text: '⏳ Queued', title: 'Scores at or above your letter bar: a letter is on its way' },
  letter:   { text: '✅ Letter', title: 'A cover letter is ready' },
  failed:   { text: '⚠ Failed', title: 'The last letter attempt failed; it retries on its own' },
  check:    { text: '🕓 Check due', title: 'Not seen live in the last day: no letter until a check sees the ad' },
  none:     { text: 'No letter', title: 'Below your letter bar: no letter unless you ask for one' },
};

const PL_EMPTY = {
  all: 'No scored jobs yet. Scan a Seek search from the Overview to fill this list.',
  question: 'No letter is waiting on an answer.',
  letter: () => `No jobs with a letter ready. Letters are written for scores of ${autoLetterMin}+, or open a job and use "Write a letter".`,
  writing: 'No letters being written right now.',
  none: 'Every job here has a letter or one on its way.',
  check: 'Nothing waiting for an availability check.',
};

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------
// Jobs captured but still waiting on the LLM pass have no match row, so /jobs
// can't list them: a count line under the box instead.
const analysingEl = document.getElementById('analysing-row');

async function refreshPendingCount() {
  try {
    const res = await fetch(`${BACKEND}/jobs/pending-count?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { pending } = await res.json();
    if (!pending) { analysingEl.hidden = true; return; }
    analysingEl.textContent = `⏳ ${pending} more job${pending === 1 ? '' : 's'} scanned, still being analysed…`;
    analysingEl.hidden = false;
  } catch {
    analysingEl.hidden = true;
  }
}

// Grey placeholder rows on the first load, so nothing jumps when the data arrives.
function renderSkeleton() {
  jobListEl.innerHTML = '';
  for (let i = 0; i < 4; i++) {
    const li = mk('li', 'pl-skel');
    li.setAttribute('aria-hidden', 'true');
    li.append(mk('span', 'pl-skel-pill'), mk('span', 'pl-skel-lines'));
    jobListEl.appendChild(li);
  }
}

async function loadJobs() {
  const seq = ++plSeq;
  refreshPendingCount();
  await plPrefsReady;
  if (plJobs === null) {
    jobStatusEl.textContent = '';
    renderSkeleton();
  }
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}&applied=false&limit=500`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    jobs = await res.json();
  } catch {
    if (seq !== plSeq) return;
    jobListEl.innerHTML = '';
    plChips.innerHTML = '';
    plCount.textContent = '';
    jobStatusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }
  if (seq !== plSeq) return;
  jobStatusEl.textContent = '';
  plJobs = Array.isArray(jobs) ? jobs : [];
  renderPipeline();
  refreshOpenDetail();
}

// ---------------------------------------------------------------------------
// The list: chips, count, rows
// ---------------------------------------------------------------------------
function plMatches(job) {
  const filter = PL_FILTERS.find(f => f.key === plView.filter);
  if (filter?.has && !filter.has.includes(rowStatus(job))) return false;
  const q = plView.query.trim().toLowerCase();
  return !q || `${job.title || ''} ${job.company || ''}`.toLowerCase().includes(q);
}

function jobTime(job) {
  return Date.parse(job.date_scraped || job.extracted_at || '') || 0;
}

function plSorted(jobs) {
  const byScore = (a, b) => (b.score ?? -1) - (a.score ?? -1) || jobTime(b) - jobTime(a);
  const byNewest = (a, b) => jobTime(b) - jobTime(a) || (b.score ?? -1) - (a.score ?? -1);
  return [...jobs].sort(plView.sort === 'newest' ? byNewest : byScore);
}

// "2d", "5h", "just now": how long ago the ad was captured.
function ageText(job) {
  const t = jobTime(job);
  if (!t) return '';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 60) return mins < 2 ? 'just now' : `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function renderPipeline() {
  if (plJobs === null) return;
  const counts = {};
  for (const job of plJobs) counts[rowStatus(job)] = (counts[rowStatus(job)] || 0) + 1;
  const countOf = (f) => (f.has ? f.has.reduce((n, s) => n + (counts[s] || 0), 0) : plJobs.length);

  // A filter that has emptied (its last job got its answer) falls back to All.
  const current = PL_FILTERS.find(f => f.key === plView.filter);
  if (current?.onlyIfAny && !countOf(current)) plView.filter = 'all';

  plChips.innerHTML = '';
  for (const f of PL_FILTERS) {
    const n = countOf(f);
    if (f.onlyIfAny && !n) continue;
    const chip = mk('button', 'pl-chip-btn' + (n ? '' : ' empty'));
    chip.type = 'button';
    chip.dataset.filter = f.key;
    chip.setAttribute('aria-pressed', String(plView.filter === f.key));
    chip.append(mk('span', null, f.label), mk('span', 'pl-chip-n', String(n)));
    chip.addEventListener('click', () => {
      plView.filter = f.key;
      savePlView();
      renderPipeline();
      plBox.scrollTop = 0;
      updateScrollShadows();
    });
    plChips.appendChild(chip);
  }

  plSearch.hidden = plJobs.length <= PL_SEARCH_FROM && !plView.query;

  const shown = plSorted(plJobs.filter(plMatches));
  const filtered = shown.length !== plJobs.length;
  plCount.textContent = !plJobs.length ? ''
    : filtered ? `Showing ${shown.length} of ${plJobs.length}` : `${plJobs.length} job${plJobs.length === 1 ? '' : 's'}`;

  const scroll = plBox.scrollTop;
  jobListEl.innerHTML = '';
  if (!shown.length) {
    const msg = PL_EMPTY[plView.filter];
    const empty = plView.query.trim() && plJobs.length
      ? `No job matches "${plView.query.trim()}".`
      : (typeof msg === 'function' ? msg() : msg);
    jobListEl.appendChild(mk('li', 'pl-empty', plJobs.length ? empty : PL_EMPTY.all));
  } else {
    for (const job of shown) jobListEl.appendChild(renderJobRow(job));
  }
  plBox.scrollTop = scroll;
  updateScrollShadows();
}

function renderJobRow(job) {
  const li = document.createElement('li');
  li.dataset.jobId = job.job_id;
  const status = rowStatus(job);
  const btn = mk('button', 'pl-row');
  btn.type = 'button';

  const score = mk('span', 'pl-score', job.score != null ? String(Math.round(job.score)) : '–');
  score.dataset.tier = tierOf(job.score);
  btn.appendChild(score);

  const main = mk('span', 'pl-main');
  main.appendChild(mk('span', 'pl-title', job.title || '(untitled)'));
  const sub = [job.company, job.location, ageText(job)].filter(Boolean).join(' · ');
  main.appendChild(mk('span', 'pl-sub', sub || '—'));
  btn.appendChild(main);

  const chip = mk('span', `pl-status st-${status}`, PL_STATUS[status].text);
  chip.title = PL_STATUS[status].title;
  btn.appendChild(chip);

  btn.title = job.title || '';
  const word = PL_STATUS[status].text.replace(/^[^A-Za-z]+/, '');
  btn.setAttribute('aria-label', `${job.title || 'Untitled'}, ${job.company || 'unknown company'}, `
    + `score ${job.score != null ? Math.round(job.score) : 'none'}, ${word}`);
  btn.addEventListener('click', () => openJobDetail(job.job_id));
  li.appendChild(btn);
  return li;
}

// Soft shadows at the box's top / bottom edge while there is more to scroll that way.
function updateScrollShadows() {
  const { scrollTop, scrollHeight, clientHeight } = plBox;
  plBoxWrap.classList.toggle('can-up', scrollTop > 2);
  plBoxWrap.classList.toggle('can-down', scrollTop + clientHeight < scrollHeight - 2);
}

plBox.addEventListener('scroll', updateScrollShadows, { passive: true });
window.addEventListener('resize', updateScrollShadows);

plSearch.addEventListener('input', () => {
  plView.query = plSearch.value;
  renderPipeline();
  plBox.scrollTop = 0;
  updateScrollShadows();
});

plSort.addEventListener('change', () => {
  plView.sort = plSort.value;
  savePlView();
  renderPipeline();
  plBox.scrollTop = 0;
  updateScrollShadows();
});

// ---------------------------------------------------------------------------
// Detail view (plan D6/D7): replaces the page; "← All jobs" or Esc comes back
// ---------------------------------------------------------------------------
function openJobDetail(jobId) {
  const job = plJobs?.find(j => j.job_id === jobId);
  if (!job) return;
  plListScroll = plBox.scrollTop;
  plOpenJob = { id: jobId, state: rowStatus(job) };
  plPage.hidden = true;
  plDetailView.hidden = false;
  window.scrollTo(0, 0);
  renderDetail(job);
  document.getElementById('pl-back').focus();
}

function closeJobDetail() {
  if (plDetailView.hidden) return;
  const id = plOpenJob?.id;
  plOpenJob = null;
  plDetailView.hidden = true;
  plDetailEl.innerHTML = '';
  plPage.hidden = false;
  plBox.scrollTop = plListScroll;
  updateScrollShadows();
  const row = jobListEl.querySelector(`li[data-job-id="${id}"] .pl-row`);
  if (row) {
    row.focus({ preventScroll: true });
    row.classList.add('pl-flash');
    setTimeout(() => row.classList.remove('pl-flash'), 1200);
  }
}

document.getElementById('pl-back').addEventListener('click', closeJobDetail);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !plDetailView.hidden && !jobsSection.hidden) closeJobDetail();
});

// After a reload: redraw an open detail when its job's letter state moved (a letter landed,
// a run started), unless the user is typing in it; say so when the job left the list.
function refreshOpenDetail() {
  if (!plOpenJob || plDetailView.hidden) return;
  const job = plJobs.find(j => j.job_id === plOpenJob.id);
  if (!job) {
    if (!plDetailEl.querySelector('.pl-gone')) {
      plDetailEl.prepend(mk('div', 'pl-gone letter-flag ok',
        'This job has left your list (applied, hidden or deleted). "← All jobs" goes back.'));
    }
    return;
  }
  const state = rowStatus(job);
  if (state !== plOpenJob.state && !typingIn(plDetailView)) {
    plOpenJob.state = state;
    renderDetail(job);
  }
}

function renderDetail(job) {
  plDetailEl.innerHTML = '';
  const head = mk('div', 'pl-d-head');
  head.appendChild(mk('h2', 'pl-d-title', job.title || '(untitled)'));
  if (job.score != null) {
    const s = mk('span', 'pl-score', String(Math.round(job.score)));
    s.dataset.tier = tierOf(job.score);
    head.appendChild(s);
  }
  plDetailEl.appendChild(head);
  const captured = job.date_scraped
    ? `captured ${new Date(job.date_scraped).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })}` : null;
  plDetailEl.appendChild(mk('div', 'pl-d-meta', [job.company, job.location, captured].filter(Boolean).join(' · ') || '—'));
  const status = rowStatus(job);
  const chip = mk('span', `pl-status st-${status}`, PL_STATUS[status].text);
  chip.title = PL_STATUS[status].title;
  plDetailEl.appendChild(chip);

  plDetailEl.appendChild(renderDetailActions(job));

  if (job.reasoning || job.gaps?.length) {
    const why = mk('div', 'pl-d-why');
    if (job.reasoning) {
      why.appendChild(mk('h5', null, `Why ${job.score != null ? Math.round(job.score) : 'this score'}`));
      why.appendChild(mk('div', null, job.reasoning));
    }
    if (job.gaps?.length) {
      why.appendChild(mk('h5', null, 'Gaps the scorer saw'));
      for (const g of job.gaps) why.appendChild(mk('div', 'note-row', g));
    }
    plDetailEl.appendChild(why);
  }
  if (job.eligibility_notes?.length) {
    plDetailEl.appendChild(mk('div', 'elig-note', `⚠ Check: ${job.eligibility_notes.join(' · ')}`));
  }

  const body = mk('div', 'job-detail');
  body.textContent = 'Loading…';
  plDetailEl.appendChild(body);
  fillDetail(body, job);
}

// Two clicks for anything destructive or paid: the first arms the button with what will
// happen, the second within 4 s does it.
function plTwoClick(btn, idleText, armedText, onConfirm) {
  let timer = null;
  btn.textContent = idleText;
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    if (!btn.classList.contains('armed')) {
      const text = typeof armedText === 'function' ? await armedText() : armedText;
      btn.textContent = text;
      btn.classList.add('armed');
      timer = setTimeout(() => { btn.classList.remove('armed'); btn.textContent = idleText; }, 4000);
      return;
    }
    clearTimeout(timer);
    btn.classList.remove('armed');
    btn.disabled = true;
    await onConfirm();
  });
}

const WRITE_REASONS = {
  queued: 'Queued ✍',
  writing: 'Already being written',
  has_letter: 'It already has a letter',
  at_or_above_bar: 'The idle loop writes this one',
  waiting_check: 'Waiting for an availability check first',
  not_eligible: "Can't write a letter for this job",
};

function renderDetailActions(job) {
  const bar = mk('div', 'pl-d-actions');

  const open = mk('button', 'btn btn-sm', 'Open ad on Seek ↗');
  open.type = 'button';
  open.addEventListener('click', () => chrome.tabs.create({ url: job.url }));
  bar.appendChild(open);

  // Below the bar with no letter on its way: the band writer for this one job (D7).
  const state = coverLetterState(job);
  if ((state === 'none' || state === 'failed') && job.score != null && job.score < autoLetterMin && job.extracted_at) {
    const write = mk('button', 'btn btn-sm btn-primary pl-write');
    write.type = 'button';
    plTwoClick(write, 'Write a letter', async () => {
      try {
        const bands = await (await fetch(`${BACKEND}/jobs/letter-bands?profile_id=${PROFILE_ID}`)).json();
        return `Write letter? ${fmtEstimate(bands.est_usd_per_letter)}`;
      } catch {
        return 'Write letter?';
      }
    }, async () => {
      write.textContent = '…';
      try {
        const res = await fetch(`${BACKEND}/jobs/${job.job_id}/write-letter?profile_id=${PROFILE_ID}`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        write.textContent = WRITE_REASONS[data.reason] || data.reason;
        if (data.queued) {
          job.letter_queued = true;
          loadJobs();
          refreshDashboard();
        }
      } catch (err) {
        write.textContent = 'Could not queue it';
        write.title = String(err.message || err);
        write.disabled = false;
      }
    });
    bar.appendChild(write);
  }

  const apply = mk('button', 'btn btn-sm pl-apply');
  apply.type = 'button';
  plTwoClick(apply, 'Mark applied', 'Confirm applied?', async () => {
    apply.textContent = '…';
    try {
      await markApplied(job.job_id);
      apply.textContent = '✓ Applied: on the Applied tab now';
      loadJobs();
      refreshDashboard();
    } catch {
      apply.textContent = 'Could not mark it — is the backend running?';
      apply.disabled = false;
    }
  });
  bar.appendChild(apply);

  const del = mk('button', 'del-btn pl-d-del');
  del.type = 'button';
  del.title = 'Delete this job';
  del.innerHTML = TRASH_SVG;
  let armTimer = null;
  del.addEventListener('click', async () => {
    if (!del.classList.contains('armed')) {
      del.classList.add('armed');
      del.textContent = 'Delete?';
      armTimer = setTimeout(() => { del.classList.remove('armed'); del.innerHTML = TRASH_SVG; }, 4000);
      return;
    }
    clearTimeout(armTimer);
    del.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`, { method: 'DELETE' });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      plJobs = plJobs.filter(j => j.job_id !== job.job_id);
      closeJobDetail();
      renderPipeline();
      refreshDashboard();
    } catch (err) {
      del.textContent = 'Delete failed';
      del.title = String(err.message || err);
      del.disabled = false;
    }
  });
  bar.appendChild(del);
  return bar;
}

// The button that asks the backend for a fresh letter (POST /jobs/{id}/regenerate): only a
// failed run's "Try again now".
function makeGenerateButton(job, label) {
  const btn = document.createElement('button');
  btn.className = 'btn btn-sm';
  btn.style.marginTop = '6px';
  btn.textContent = label;
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    btn.disabled = true;
    btn.textContent = 'Generating…';
    try {
      const r = await fetch(`${BACKEND}/jobs/${job.job_id}/regenerate?profile_id=${PROFILE_ID}`, { method: 'POST' });
      if (!r.ok) throw new Error();
      btn.textContent = 'Queued — will appear when ready.';
    } catch {
      btn.textContent = label;
      btn.disabled = false;
    }
  });
  return btn;
}

// What the pipeline says about this job's letter (open issues, what it leaves out, a run
// waiting on the user, a failure). null when the backend can't say; the view still works.
async function fetchLetterInfo(jobId) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${jobId}/letter-info?profile_id=${PROFILE_ID}`);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

// The detail body: a waiting question, the letter (editable) and its notes, or where the
// letter stands, then the Quick Apply questions.
async function fillDetail(detailEl, job) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`);
    const data = await res.json();
    detailEl.innerHTML = '';

    const info = await fetchLetterInfo(job.job_id);
    if (info?.waiting?.status === 'waiting_user') {
      renderQuestionCard(detailEl, info.waiting.run_id);
    } else if (info?.waiting) {
      detailEl.appendChild(mk('div', 'pref-note', 'Thanks — your answers are in. The letter resumes shortly.'));
    }

    const cl = data.cover_letter;
    const run = job.letter_run;
    if (cl?.generated_content) {
      renderCoverLetterEditor(detailEl, job.job_id, cl);
      renderLetterNotes(detailEl, info, cl, data);
    } else if (run?.status === 'running' || run?.status === 'answered' || job.letter_queued) {
      detailEl.appendChild(mk('div', 'pref-note', '✍ Writing your letter. It appears here when it lands.'));
    } else if (info?.failure) {
      const why = info.failure.error ? ` (${truncate(info.failure.error, 140)})` : '';
      detailEl.appendChild(mk('div', 'letter-flag', `The last attempt didn't produce a letter${why}. It retries on its own after a short wait, or:`));
      detailEl.appendChild(makeGenerateButton(job, 'Try again now'));
    } else if (info?.waiting) {
      // the question card above is the next step
    } else if (job.score != null && job.score >= autoLetterMin) {
      detailEl.appendChild(mk('div', 'pref-note', job.fresh === false
        ? 'A letter is written once an availability check sees this ad live.'
        : 'Cover letter pending: the idle loop will write it shortly.'));
    } else {
      detailEl.appendChild(mk('div', 'pref-note',
        `No cover letter: this job scores below your letter bar (${autoLetterMin}). "Write a letter" above asks for one.`));
    }
    if (!cl?.generated_content && data.eligibility_notes?.length) {
      renderLetterNotes(detailEl, null, null, data);
    }
    await renderQuickApplyQuestions(detailEl, job.job_id);
  } catch {
    detailEl.textContent = 'Could not load detail.';
  }
}
