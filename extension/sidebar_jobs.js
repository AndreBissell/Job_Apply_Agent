// Jobs tab: the card list (loadJobs, renderJob, fillDetail) and a card's expanded detail.
// Classic script: sidebar.html loads it after sidebar_shared.js and before sidebar.js;
// all of them share one global scope, so load order is the contract.
// ---------------------------------------------------------------------------
// Jobs tab
// ---------------------------------------------------------------------------
const jobStatusEl = document.getElementById('job-status');
const jobListEl = document.getElementById('job-list');

// A plain heading row inside #job-list, splitting the flat list into the two
// groups that actually matter for "what can I act on right now": jobs with a
// cover letter ready to copy and submit, vs everything else still waiting on
// one. Score tier still colors each individual card within its group (see
// renderJob) — this is the coarser, primary grouping on top of that.
function renderSectionHeader(text, kind) {
  const li = document.createElement('li');
  li.className = `section-header ${kind}`;
  li.textContent = text;
  return li;
}

// Jobs captured but still waiting on the LLM pass have no match row, so /jobs
// can't list them — show a count under the list instead. Separate element from
// the <ul> so it can update mid-scan without re-rendering (and collapsing) cards.
const analysingEl = document.getElementById('analysing-row');

async function refreshPendingCount() {
  try {
    const res = await fetch(`${BACKEND}/jobs/pending-count?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { pending } = await res.json();
    if (!pending) { analysingEl.hidden = true; return; }
    analysingEl.textContent = `⏳ ${pending} more job app${pending === 1 ? '' : 's'} scanned — analysing…`;
    analysingEl.hidden = false;
  } catch {
    analysingEl.hidden = true;
  }
}

async function loadJobs() {
  refreshPendingCount();
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
  jobStatusEl.textContent = ''; // status line is only for loading/error/empty states

  const asking = [];
  const ready = [];
  const waiting = [];
  const longTail = [];
  for (const job of jobs) {
    const tier = tierOf(job.score);
    const clState = coverLetterState(job);
    if (clState === 'question') {
      asking.push(job); // a run is paused on a question only the user can answer
    } else if (clState === 'ready') {
      ready.push(job); // a cover letter exists regardless of score/tier — always "ready"
    } else if (tier === 'hidden' && clState !== 'writing') {
      longTail.push(job);
    } else {
      waiting.push(job);
    }
  }

  if (asking.length) {
    jobListEl.appendChild(renderSectionHeader(`❓ Needs Your Answer (${asking.length})`, 'question'));
    for (const job of asking) jobListEl.appendChild(renderJob(job, tierOf(job.score)));
  }
  if (ready.length) {
    jobListEl.appendChild(renderSectionHeader(`✅ Ready to Apply (${ready.length})`, 'ready'));
    for (const job of ready) jobListEl.appendChild(renderJob(job, tierOf(job.score)));
  }
  if (waiting.length || longTail.length) {
    jobListEl.appendChild(renderSectionHeader(`⏳ Waiting on Cover Letter (${waiting.length + longTail.length})`, 'waiting'));
    for (const job of waiting) jobListEl.appendChild(renderJob(job, tierOf(job.score)));
    if (longTail.length) jobListEl.appendChild(renderFold(longTail));
  }

  maybeShowSuggestions();
}

// Long-tail (<60) jobs aren't rendered individually up front — just a count.
// The real rows are built lazily on first click (so a backlog of 50 low
// scorers doesn't cost render time until asked for); after that, the fold
// bar just toggles their visibility back and forth.
function renderFold(jobs) {
  const li = document.createElement('li');
  li.className = 'fold';
  const showText = `${jobs.length} other role${jobs.length === 1 ? '' : 's'} — click to show`;
  const hideText = `▲ Hide ${jobs.length} other role${jobs.length === 1 ? '' : 's'}`;
  li.textContent = showText;

  let rows = null;
  let expanded = false;

  li.addEventListener('click', () => {
    if (!rows) {
      rows = jobs.map(job => renderJob(job, 'amber'));
      for (const row of rows) jobListEl.insertBefore(row, li);
    }
    expanded = !expanded;
    for (const row of rows) row.style.display = expanded ? '' : 'none';
    li.textContent = expanded ? hideText : showText;
  });

  return li;
}

// Cover-letter card state (docs/extension-revamp-plan.md §5.1; the run states are
// Phase 8 of docs/cover-letter-loop-plan.md). job.letter_run is the newest pipeline
// run: {run_id, status, open_questions} or null.
//  - 'question' a run is paused on questions for the user (even if an older letter exists)
//  - 'writing'  a run is working, or its answers are in and it resumes shortly
//  - 'ready'    letter generated, has_cover_letter is true
//  - 'failed'   the last run produced nothing; the idle loop retries after a cooldown
//  - 'pending'  score >= autoLetterMin, idle loop hasn't reached it yet
//  - 'none'     below autoLetterMin and no letter — no collapsed-card affordance;
//               a manual force-generate lives only in the expanded detail view
function coverLetterState(job) {
  const run = job.letter_run;
  if (run?.status === 'waiting_user') return 'question';
  if (run?.status === 'running' || run?.status === 'answered') return 'writing';
  if (job.letter_queued) return 'writing'; // queued by the Overview's "+ letters"
  if (job.has_cover_letter) return 'ready';
  if (run?.status === 'failed' || run?.status === 'budget_stopped') return 'failed';
  if (job.score != null && job.score >= autoLetterMin) return 'pending';
  return 'none';
}

const CL_BADGES = {
  ready:    '✅ Ready',
  pending:  'Cover letter pending…',
  writing:  '✍ Writing…',
  failed:   '⚠ Letter failed',
};

function renderJob(job, tier) {
  const li = document.createElement('li');
  li.dataset.jobId = job.job_id;
  if (tier) li.dataset.tier = tier;

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

  const clState = coverLetterState(job);
  if (clState === 'question') {
    const n = job.letter_run.open_questions || 1;
    row.appendChild(mk('span', 'cl-badge question', `❓ ${n} question${n === 1 ? '' : 's'} for you`));
  } else if (CL_BADGES[clState]) {
    row.appendChild(mk('span', `cl-badge ${clState}`, CL_BADGES[clState]));
  }

  const applyBtn = document.createElement('button');
  const applied = !!job.applied_at; // applied_at is the one definition of "applied"
  applyBtn.className = 'apply-btn' + (applied ? ' applied' : '');
  applyBtn.title = applied ? 'Applied' : 'Mark Applied';
  applyBtn.textContent = applied ? '✓ Applied' : 'Mark Applied';
  applyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (job.applied_at) return;
    applyBtn.disabled = true;
    try {
      const res = await markApplied(job.job_id);
      job.status = 'applied';
      job.applied_at = res.applied_at;
      applyBtn.className = 'apply-btn applied';
      applyBtn.textContent = '✓ Applied';
      applyBtn.title = 'Applied';
      row.querySelector('.del-btn')?.remove(); // applied = evidence; delete lives on the Applied tab
    } catch {
      alert('Could not mark applied — is the backend running?');
    } finally {
      applyBtn.disabled = false;
    }
  });
  row.appendChild(applyBtn);

  // No Delete for an applied job (applied_at is the one definition): it is Centrelink
  // evidence, deleted only from the Applied tab. The backend refuses it anyway (409).
  if (!job.applied_at) {
    const delBtn = document.createElement('button');
    delBtn.className = 'del-btn';
    delBtn.title = 'Delete';
    delBtn.innerHTML = TRASH_SVG;
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${job.title}"?`)) return;
      try {
        const res = await fetch(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`, { method: 'DELETE' });
        if (res.ok) li.remove();
        else alert((await res.json().catch(() => ({}))).detail || `Delete failed (HTTP ${res.status})`);
      } catch {
        alert('Delete failed — is the backend running?');
      }
    });
    row.appendChild(delBtn);
  }

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

  // Admin/eligibility asks the letter never mentions (work rights, licence, clearance):
  // a heads-up so they aren't a surprise at the application form.
  if (job.eligibility_notes?.length) {
    const shown = job.eligibility_notes.slice(0, 2).map(t => truncate(t, 60)).join(' · ');
    const more = job.eligibility_notes.length > 2 ? ` (+${job.eligibility_notes.length - 2} more)` : '';
    const note = mk('div', 'elig-note', `⚠ Check: ${shown}${more}`);
    note.title = job.eligibility_notes.join('\n');
    li.appendChild(note);
  }

  // Gold- and blue-tier cards show a reasoning snippet + top skill chips inline, no
  // click needed — everything else stays click-to-expand as before.
  if ((tier === 'blue' || tier === 'gold') && (job.reasoning || job.top_skills?.length)) {
    const preview = document.createElement('div');
    preview.className = 'job-preview';
    if (job.reasoning) preview.appendChild(document.createTextNode(truncate(job.reasoning, 90)));
    if (job.top_skills?.length) {
      const chips = document.createElement('div');
      chips.className = 'chips';
      for (const skill of job.top_skills.slice(0, 3)) {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.textContent = skill;
        chips.appendChild(chip);
      }
      preview.appendChild(chips);
    }
    li.appendChild(preview);
  }

  let expanded = false;
  let detailEl = null;
  li.addEventListener('click', async (ev) => {
    if (ev.target.tagName === 'A' || ev.target.tagName === 'TEXTAREA') return;
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

// The button that asks the backend for a fresh letter (POST /jobs/{id}/regenerate).
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
// waiting on the user, a failure). null when the backend can't say; the card still works.
async function fetchLetterInfo(jobId) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${jobId}/letter-info?profile_id=${PROFILE_ID}`);
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
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
    } else if (run?.status === 'running' || run?.status === 'answered') {
      detailEl.appendChild(mk('div', 'pref-note', '✍ Writing your letter — a full run takes a few minutes.'));
    } else if (info?.failure) {
      const why = info.failure.error ? ` (${truncate(info.failure.error, 140)})` : '';
      detailEl.appendChild(mk('div', 'letter-flag', `The last attempt didn't produce a letter${why}. It retries on its own after a short wait, or:`));
      detailEl.appendChild(makeGenerateButton(job, 'Try again now'));
    } else if (info?.waiting) {
      // the question card above is the next step
    } else if (job.score != null && job.score >= autoLetterMin) {
      const note = document.createElement('div');
      note.style.cssText = 'margin-top:6px;color:#6b7280;';
      note.textContent = 'Cover letter pending — the idle loop will generate it shortly.';
      detailEl.appendChild(note);
    } else {
      detailEl.appendChild(makeGenerateButton(job, 'Generate cover letter anyway'));
    }
    if (!cl?.generated_content && data.eligibility_notes?.length) {
      renderLetterNotes(detailEl, null, null, data);
    }
    await renderQuickApplyQuestions(detailEl, job.job_id);
  } catch {
    detailEl.textContent = 'Could not load detail.';
  }
}
