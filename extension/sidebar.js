// Side panel: tabbed Jobs + Profile editor.
// Vanilla JS, no build step. Talks to the FastAPI backend — BACKEND comes from
// config.js and is either the real (8000) or test (8001) environment.

// Each environment is its own database with exactly one profile, and that
// profile is id 1 in both — so this constant is correct for either.
const PROFILE_ID = 1;

// Score tiers (docs/extension-revamp-plan.md §1) — purely visual colouring.
const GOLD_MIN = 95;
const BLUE_MIN = 90;
const GREEN_MIN = 75;
const AMBER_MIN = 60;

// Minimum score for an auto-generated cover letter. User-tunable in the
// "Personalise metrics" panel and stored server-side (profiles.preferences),
// since the backend idle loop is what acts on it. 75 is only the pre-load default.
let autoLetterMin = GREEN_MIN;

function tierOf(score) {
  if (score == null) return 'amber';
  if (score >= GOLD_MIN) return 'gold';
  if (score >= BLUE_MIN) return 'blue';
  if (score >= GREEN_MIN) return 'green';
  if (score >= AMBER_MIN) return 'amber';
  return 'hidden';
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const QUAL_TYPE_LABELS   = { degree: 'Degree', certificate: 'Certificate', diploma: 'Diploma', other: 'Other' };
const QUAL_STATUS_LABELS = { completed: 'Completed', in_progress: 'In Progress', withdrawn: 'Withdrawn' };
const EXP_TYPE_LABELS    = {
  job: 'Job', internship: 'Internship', volunteer: 'Volunteer', project: 'Project',
  // The cover-letter flow (ask_user) can add these; the editor must offer them or a save
  // would turn the row into a 'job'.
  university_project: 'University project', assignment: 'Assignment', personal_project: 'Personal project',
};

function fmtDate(ym) {
  if (!ym) return '';
  const [y, m] = ym.split('-');
  return new Date(Number(y), Number(m) - 1, 1)
    .toLocaleDateString('en-AU', { month: 'short', year: 'numeric' });
}

// Month fields are plain text ("YYYY-MM"), not <input type="month">: the native
// control makes you scroll or click through segments to change a year, where a
// typed field lets you just retype it. The value stays "YYYY-MM", so the
// save/load code is unchanged.
function monthInputHtml(cls) {
  return `<input class="${cls} month-input" type="text" inputmode="numeric" maxlength="7" placeholder="YYYY-MM" autocomplete="off">`;
}

// "2025-3" / "2025-03" -> "2025-03"; anything else (incl. month 13) -> null.
function normaliseMonth(raw) {
  const m = /^(\d{4})-(\d{1,2})$/.exec((raw || '').trim());
  if (!m) return null;
  const month = Number(m[2]);
  return month >= 1 && month <= 12 ? `${m[1]}-${String(month).padStart(2, '0')}` : null;
}

// The value to save/summarise for a month input: valid "YYYY-MM" or ''. A
// half-typed or invalid date is treated as empty rather than sent to the backend.
function monthValue(input) {
  return normaliseMonth(input.value) || '';
}

document.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.classList?.contains('month-input')) return;
  if (/[^\d-]/.test(el.value)) el.value = el.value.replace(/[^\d-]/g, '');
  // Add the dash as the 4th digit is typed at the end. Never on delete or
  // mid-string, so fixing the year in place ("2026-03" -> "2025-03") isn't
  // reformatted under the caret.
  if (e.inputType === 'insertText' && /^\d{4}$/.test(el.value)) el.value += '-';
  el.classList.remove('invalid');
});

document.addEventListener('focusout', (e) => {
  const el = e.target;
  if (!el.classList?.contains('month-input')) return;
  const normalised = normaliseMonth(el.value);
  if (normalised) el.value = normalised;
  el.classList.toggle('invalid', !!el.value.trim() && !normalised);
});

function truncate(str, n) {
  if (!str || str.length <= n) return str;
  return str.slice(0, n).replace(/\s\S*$/, '') + '…';
}

// Small DOM helper: an element with a class and text (always textContent, never HTML).
function mk(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------
const tabBtns = document.querySelectorAll('nav#tabs .tab');
const jobsSection = document.getElementById('jobs-section');
const appliedSection = document.getElementById('applied-section');
const profileSection = document.getElementById('profile-section');
const hdrJobsBtns = document.getElementById('hdr-jobs-btns');
let profileLoaded = false;

tabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    tabBtns.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    jobsSection.hidden = tab !== 'jobs';
    appliedSection.hidden = tab !== 'applied';
    profileSection.hidden = tab !== 'profile';
    hdrJobsBtns.style.display = tab === 'jobs' ? '' : 'none';
    if (tab === 'profile' && !profileLoaded) loadProfile();
    if (tab === 'profile') loadToWorkOn(); // counts move as new ads are scanned — always refresh
    if (tab === 'applied') loadApplied(); // always refresh — cheap query, keeps it current
  });
});

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
  applyBtn.className = 'apply-btn' + (job.status === 'applied' ? ' applied' : '');
  applyBtn.title = job.status === 'applied' ? 'Applied' : 'Mark Applied';
  applyBtn.textContent = job.status === 'applied' ? '✓ Applied' : 'Mark Applied';
  applyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (job.status === 'applied') return;
    applyBtn.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}/status?profile_id=${PROFILE_ID}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'applied' }),
      });
      if (!res.ok) throw new Error();
      job.status = 'applied';
      applyBtn.className = 'apply-btn applied';
      applyBtn.textContent = '✓ Applied';
      applyBtn.title = 'Applied';
    } catch {
      alert('Could not mark applied — is the backend running?');
    } finally {
      applyBtn.disabled = false;
    }
  });
  row.appendChild(applyBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'del-btn';
  delBtn.title = 'Delete';
  // Inline SVG (not the 🗑 emoji) so the icon can take the hover colour via currentColor.
  delBtn.innerHTML =
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>';
  delBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${job.title}"?`)) return;
    try {
      const res = await fetch(`${BACKEND}/jobs/${job.job_id}`, { method: 'DELETE' });
      if (res.ok) li.remove();
    } catch {
      alert('Delete failed — is the backend running?');
    }
  });
  row.appendChild(delBtn);

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
  } catch {
    detailEl.textContent = 'Could not load detail.';
  }
}

// ---------------------------------------------------------------------------
// The letter's notes: open issues, what it leaves out, notes from the ad
// ---------------------------------------------------------------------------
function renderLetterNotes(container, info, cl, data) {
  const wrap = mk('div');
  wrap.addEventListener('click', e => e.stopPropagation()); // don't collapse the card
  const run = info?.run;

  if (run) {
    const flag = mk('div', 'letter-flag' + (run.clean ? ' ok' : ''));
    if (run.clean) {
      flag.textContent = `✓ Passed every check (draft ${run.draft_version}).`;
    } else {
      flag.appendChild(mk('div', null, '⚠ Read this before you send it — the letter did not pass every check:'));
      for (const issue of run.open_issues) flag.appendChild(mk('div', 'note-row', issue));
    }
    if (run.warnings?.length) {
      flag.appendChild(mk('div', 'warn', 'Suggestions: ' + run.warnings.slice(0, 3).join(' · ')));
    }
    if (cl?.edited_content && cl.edited_content !== cl.generated_content) {
      flag.appendChild(mk('div', null, 'These notes describe the generated draft, not your edits.'));
    }
    wrap.appendChild(flag);
  }

  const sections = [];
  if (run?.not_claimed?.length) {
    sections.push(['Left out of the letter', run.not_claimed.map(r => [r.text, r.reason])]);
  }
  const eligibility = data?.eligibility_notes?.length ? data.eligibility_notes : (run?.eligibility_notes || []);
  if (eligibility.length) sections.push(['Check before you apply (never in the letter)', eligibility.map(t => [t, null])]);
  if (run?.application_instructions?.length) {
    sections.push(['The ad also asks you to', run.application_instructions.map(t => [t, null])]);
  }
  if (sections.length) {
    const details = mk('details', 'letter-notes');
    details.appendChild(mk('summary', null, 'What the letter leaves out, and notes from the ad'));
    for (const [heading, rows] of sections) {
      details.appendChild(mk('h5', null, heading));
      // Plain rows, not <ul>/<li>: the job list's li rules would restyle them as cards.
      for (const [text, why] of rows) {
        const row = mk('div', 'note-row', text);
        if (why) row.appendChild(mk('span', 'why', ` — ${why}`));
        details.appendChild(row);
      }
    }
    wrap.appendChild(details);
  }
  if (wrap.childNodes.length) container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// The question card (ask_user): a run is paused on a must-have gap
// ---------------------------------------------------------------------------
const EXP_TYPES = ['job', 'internship', 'university_project', 'assignment', 'personal_project', 'volunteer'];
const QUAL_TYPES = ['degree', 'diploma', 'certificate', 'license'];
const QUAL_STATUSES = ['completed', 'in_progress', 'expected'];

function labelled(text, control) {
  const l = mk('label', null, text);
  l.appendChild(control);
  return l;
}
function textInput(value, placeholder) {
  const i = mk('input');
  i.type = 'text';
  i.value = value || '';
  if (placeholder) i.placeholder = placeholder;
  return i;
}
function selectOf(options, value) {
  const sel = mk('select');
  for (const o of options) {
    const opt = mk('option', null, o.replace(/_/g, ' '));
    opt.value = o;
    sel.appendChild(opt);
  }
  sel.value = options.includes(value) ? value : options[0];
  return sel;
}
const splitList = (text) => text.split(',').map(t => t.trim()).filter(Boolean);

// The rows a Yes would add to the profile, as editable fields (plan Q11: the user sees
// and confirms them before anything is saved). Returns {node, read}: read() gives the
// ProposedRows the confirm endpoint takes.
function proposalEditor(proposal) {
  const root = mk('div', 'proposal');
  const readers = { experiences: [], qualifications: [] };

  for (const e of proposal.experiences || []) {
    root.appendChild(mk('h5', null, 'Experience'));
    const type = selectOf(EXP_TYPES, e.experience_type);
    const title = textInput(e.title);
    const org = textInput(e.organization, 'employer, university or client');
    const start = textInput(e.start, 'YYYY-MM');
    const end = textInput(e.end, 'YYYY-MM');
    const desc = mk('textarea');
    desc.value = e.description || '';
    const skills = textInput((e.skills || []).join(', '), 'skills used, comma-separated');
    root.append(labelled('Type', type), labelled('Title', title), labelled('Organisation', org));
    const row = mk('div', 'row2');
    row.append(labelled('Start', start), labelled('End', end));
    root.append(row, labelled('What you did', desc), labelled('Skills', skills));
    readers.experiences.push(() => ({
      experience_type: type.value, title: title.value.trim(), organization: org.value.trim(),
      start: start.value.trim(), end: end.value.trim(), description: desc.value.trim(),
      skills: splitList(skills.value),
    }));
  }
  for (const q of proposal.qualifications || []) {
    root.appendChild(mk('h5', null, 'Qualification'));
    const type = selectOf(QUAL_TYPES, q.qualification_type);
    const title = textInput(q.title);
    const inst = textInput(q.institution, 'institution');
    const status = selectOf(QUAL_STATUSES, q.status);
    root.append(labelled('Type', type), labelled('Title', title), labelled('Institution', inst), labelled('Status', status));
    readers.qualifications.push(() => ({
      qualification_type: type.value, title: title.value.trim(), institution: inst.value.trim(), status: status.value,
    }));
  }
  let skillsInput = null;
  if ((proposal.skills || []).length) {
    root.appendChild(mk('h5', null, 'Skills'));
    skillsInput = textInput(proposal.skills.join(', '));
    root.appendChild(skillsInput);
  }
  return {
    node: root,
    read: () => ({
      experiences: readers.experiences.map(r => r()),
      qualifications: readers.qualifications.map(r => r()),
      skills: skillsInput ? splitList(skillsInput.value) : [],
    }),
  };
}

// GET/POST helper for the letter-run endpoints: {ok, status, data}; data.detail is the message.
async function runRequest(url, body) {
  try {
    const res = await fetch(url, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: { detail: 'Could not reach the backend — is it running?' } };
  }
}
const errorText = (r) =>
  (typeof r.data?.detail === 'string' && r.data.detail) || 'Something went wrong — check the fields and try again.';

async function renderQuestionCard(container, runId) {
  const card = mk('div', 'qcard');
  card.addEventListener('click', e => e.stopPropagation()); // the card itself is not a toggle
  container.appendChild(card);
  const base = `${BACKEND}/letter-runs/${runId}`;
  let view = null;

  function questionBlock(q) {
    const block = mk('div', 'q');
    block.appendChild(mk('div', 'q-prompt', q.prompt));
    const err = mk('div', 'q-err');

    if (q.status === 'answered') {
      block.appendChild(mk('div', 'q-done', q.choice === 'no'
        ? '✓ Left out of your letter, and added to "To work on" in your profile.'
        : '✓ Added to your profile.'));
      return block;
    }

    if (q.status === 'needs_confirm') {
      block.appendChild(mk('div', 'pref-note', 'Here is what we would add to your profile. Fix anything that is wrong, then confirm.'));
      const editor = proposalEditor(q.proposal || {});
      block.appendChild(editor.node);
      const actions = mk('div', 'q-actions');
      const ok = mk('button', 'btn btn-sm btn-primary', 'Add to my profile');
      const no = mk('button', 'btn btn-sm', "That's not right");
      ok.addEventListener('click', async () => {
        ok.disabled = no.disabled = true;
        ok.textContent = 'Saving…';
        const r = await runRequest(`${base}/questions/${q.id}/confirm?profile_id=${PROFILE_ID}`, { accept: true, rows: editor.read() });
        if (r.ok) return applyView(r.data);
        err.textContent = errorText(r);
        ok.disabled = no.disabled = false;
        ok.textContent = 'Add to my profile';
      });
      no.addEventListener('click', async () => {
        ok.disabled = no.disabled = true;
        const r = await runRequest(`${base}/questions/${q.id}/confirm?profile_id=${PROFILE_ID}`, { accept: false });
        if (r.ok) return applyView(r.data);
        err.textContent = errorText(r);
        ok.disabled = no.disabled = false;
      });
      actions.append(ok, no);
      block.append(actions, err);
      return block;
    }

    // open: Yes (with a text box) or No
    const text = mk('textarea');
    text.placeholder = view.answer_hint || 'Tell us what you did, where and roughly how long.';
    block.appendChild(text);
    const actions = mk('div', 'q-actions');
    const yes = mk('button', 'btn btn-sm btn-primary', 'Yes, I have this');
    const no = mk('button', 'btn btn-sm', 'No — leave it out');
    yes.addEventListener('click', async () => {
      if (!text.value.trim()) { err.textContent = 'Tell us what you did, where and roughly how long — or answer No.'; return; }
      err.textContent = '';
      yes.disabled = no.disabled = true;
      yes.textContent = 'Reading your answer…';
      const r = await runRequest(`${base}/answers?profile_id=${PROFILE_ID}`,
        { answers: [{ question_id: q.id, choice: 'yes', text: text.value.trim() }] });
      if (r.ok) return applyView(r.data);
      err.textContent = errorText(r);
      yes.disabled = no.disabled = false;
      yes.textContent = 'Yes, I have this';
    });
    no.addEventListener('click', async () => {
      yes.disabled = no.disabled = true;
      const r = await runRequest(`${base}/answers?profile_id=${PROFILE_ID}`,
        { answers: [{ question_id: q.id, choice: 'no' }] });
      if (r.ok) return applyView(r.data);
      err.textContent = errorText(r);
      yes.disabled = no.disabled = false;
    });
    actions.append(yes, no);
    block.append(actions, err);
    return block;
  }

  function draw() {
    card.innerHTML = '';
    const open = view.questions.filter(q => q.status !== 'answered').length;
    if (view.status !== 'waiting_user') {
      card.appendChild(mk('h4', null, 'Thanks — your answers are in.'));
      card.appendChild(mk('div', 'pref-note', 'The letter resumes shortly and will appear here when it is ready.'));
    } else {
      card.appendChild(mk('h4', null, `${open} question${open === 1 ? '' : 's'} before your letter`));
    }
    for (const q of view.questions) card.appendChild(questionBlock(q));
  }

  function applyView(next) {
    view = next;
    draw();
    // Every question answered: the card moves to "Writing…" once the list reloads.
    if (view.status !== 'waiting_user') scheduleReload(1500);
  }

  const first = await runRequest(`${base}?profile_id=${PROFILE_ID}`);
  if (!first.ok) {
    card.appendChild(mk('div', 'q-err', errorText(first)));
    return;
  }
  applyView(first.data);
}

// Cover-letter editor: Copy + editable textarea that saves via
// PATCH /jobs/{id}/cover-letter (shared with the Quick-Apply overlay on Seek).
function renderCoverLetterEditor(container, jobId, cl) {
  const wrap = document.createElement('div');
  wrap.style.marginTop = '6px';

  const badge = document.createElement('div');
  badge.style.cssText = 'color:#059669;font-weight:600;font-size:11px;margin-bottom:4px;';
  badge.textContent = '✅ Cover letter ready';
  wrap.appendChild(badge);

  const textarea = document.createElement('textarea');
  textarea.value = cl.edited_content || cl.generated_content || '';
  wrap.appendChild(textarea);

  const actions = document.createElement('div');
  actions.className = 'cl-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-sm';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(textarea.value);
      copyBtn.textContent = 'Copied ✓';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    } catch { /* clipboard permission denied — ignore */ }
  });
  actions.appendChild(copyBtn);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn btn-sm';
  saveBtn.textContent = 'Save edits';
  saveBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const res = await fetch(`${BACKEND}/jobs/${jobId}/cover-letter?profile_id=${PROFILE_ID}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_content: textarea.value }),
      });
      if (!res.ok) throw new Error();
      saveBtn.textContent = 'Saved ✓';
    } catch {
      saveBtn.textContent = 'Save failed';
    } finally {
      setTimeout(() => { saveBtn.textContent = 'Save edits'; saveBtn.disabled = false; }, 1500);
    }
  });
  actions.appendChild(saveBtn);

  // A regenerate never overwrites the user's edits, so a newer generated draft can sit
  // behind them. This puts it in the box to compare or use; nothing is saved until Save.
  if (cl.edited_content && cl.generated_content && cl.edited_content !== cl.generated_content) {
    const genBtn = document.createElement('button');
    genBtn.className = 'btn btn-sm';
    genBtn.textContent = 'Load generated draft';
    genBtn.title = 'Replaces the text in the box with the latest generated draft. Nothing is saved until you click Save edits.';
    genBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      textarea.value = cl.generated_content;
    });
    actions.appendChild(genBtn);
  }

  wrap.appendChild(actions);
  container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// Applied tab — Centrelink mutual-obligation reporting
// ---------------------------------------------------------------------------
const appliedStatusEl = document.getElementById('applied-status');
const appliedListEl = document.getElementById('applied-list');
let lastAppliedJobs = [];

async function loadApplied() {
  appliedStatusEl.textContent = 'Loading…';
  appliedListEl.innerHTML = '';
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}&min_score=0&status=applied`);
    jobs = await res.json();
  } catch {
    appliedStatusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }
  lastAppliedJobs = Array.isArray(jobs) ? jobs : [];
  if (!lastAppliedJobs.length) {
    appliedStatusEl.textContent = 'No applications logged yet. Use "Mark Applied" on a job in the Jobs tab.';
    return;
  }
  appliedStatusEl.textContent = `${lastAppliedJobs.length} application(s).`;
  for (const job of lastAppliedJobs) appliedListEl.appendChild(renderAppliedRow(job));
}

function renderAppliedRow(job) {
  const li = document.createElement('li');
  const row = document.createElement('div');
  row.className = 'job-row';

  const title = document.createElement('span');
  title.className = 'job-title';
  title.textContent = job.title || '(untitled)';
  row.appendChild(title);

  if (job.applied_at) {
    const date = document.createElement('span');
    date.className = 'applied-date';
    date.textContent = new Date(job.applied_at).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' });
    row.appendChild(date);
  }
  li.appendChild(row);

  const meta = document.createElement('div');
  meta.className = 'job-meta';
  meta.textContent = [job.company, job.location].filter(Boolean).join(' · ') || '—';
  li.appendChild(meta);

  const evidence = evidenceNote(job);
  if (evidence) li.appendChild(evidence);

  li.addEventListener('click', () => chrome.tabs.create({ url: job.url }));
  return li;
}

// Screenshot files are deleted after a fixed TTL (30 days by default) while the
// application record stays. Say so on the card rather than letting the image
// vanish silently: an expiry date, a warning inside the last week, and a
// distinct 'expired' state (screenshot_taken_at kept, no file).
function evidenceNote(job) {
  const fmt = iso => new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
  const note = document.createElement('div');
  note.className = 'evidence-note';
  if (job.screenshot_url && job.screenshot_expires_at) {
    const daysLeft = (new Date(job.screenshot_expires_at) - Date.now()) / 86400000;
    note.textContent = `Screenshot kept until ${fmt(job.screenshot_expires_at)}`;
    if (daysLeft <= 7) {
      note.classList.add('soon');
      note.textContent += ' — export it first';
    }
    return note;
  }
  if (job.screenshot_taken_at) {
    note.textContent = `Screenshot taken ${fmt(job.screenshot_taken_at)} — file since expired`;
    return note;
  }
  return null;
}

function csvEscape(val) {
  const s = String(val ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

document.getElementById('export-applied-btn').addEventListener('click', () => {
  const rows = [['Date Applied', 'Job Title', 'Employer', 'Location', 'Source URL', 'Screenshot Evidence']];
  for (const job of lastAppliedJobs) {
    rows.push([
      job.applied_at ? job.applied_at.slice(0, 10) : '',
      job.title || '',
      job.company || '',
      job.location || '',
      job.url || '',
      // taken_at outlives the file, so distinguish expired from never-captured.
      job.screenshot_url ? job.screenshot_taken_at.slice(0, 10)
        : job.screenshot_taken_at ? `${job.screenshot_taken_at.slice(0, 10)} (file expired)` : 'No',
    ]);
  }
  const csv = rows.map(r => r.map(csvEscape).join(',')).join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `applied-jobs-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

document.getElementById('export-evidence-btn').addEventListener('click', async () => {
  try {
    const res = await fetch(`${BACKEND}/jobs/evidence-export?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error(res.status);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `application-evidence-${new Date().toISOString().slice(0, 10)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    alert('Could not export evidence — is the backend running?');
  }
});

// ---------------------------------------------------------------------------
// Suggested searches — free (non-LLM) phrase mining from good matches (§7)
// ---------------------------------------------------------------------------
const SUGGESTION_COOLDOWN_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

// Builds the Seek search a suggestion opens. Keeps the SEO-slug path form
// ("/data-analyst-jobs") and adds the location as ?where=, because that is the
// combination content_script.js's currentSearchQuery() already normalises back
// to the bare phrase — so a job found this way is attributed to "data analyst",
// not "data analyst brisbane", and search-performance stats stay comparable
// across locations.
function seekSearchUrl(phrase, searchLocation) {
  const slug = phrase.toLowerCase().trim().replace(/\s+/g, '-') + '-jobs';
  const where = (searchLocation || '').trim();
  return `https://au.seek.com/${slug}`
    + (where ? `?where=${encodeURIComponent(where)}` : '');
}

// Renders whatever the backend ranked. `forceLlm` is only set by the explicit
// "Refresh now" button, which re-runs the LLM layer regardless of its cache
// and regardless of the saved preference.
async function maybeShowSuggestions({ forceLlm = false, ignoreCooldown = false } = {}) {
  const banner = document.getElementById('suggestion-banner');
  try {
    if (!ignoreCooldown) {
      const stored = await chrome.storage.local.get('suggestionsDismissedUntil');
      if (stored.suggestionsDismissedUntil && Date.now() < stored.suggestionsDismissedUntil) return;
    }

    let url = `${BACKEND}/jobs/suggested-searches?profile_id=${PROFILE_ID}`;
    if (forceLlm) url += '&use_llm=true';
    const res = await fetch(url);
    if (!res.ok) return;
    // Named searchLocation, not location — `location` would shadow
    // window.location inside this function.
    const { suggestions, llm_used, location: searchLocation } = await res.json();
    if (!suggestions?.length) return;

    const scope = searchLocation ? ` in ${searchLocation}` : '';
    banner.querySelector('span').textContent =
      `${llm_used ? '✨' : '💡'} Try searching${scope}:`;
    const linksEl = document.getElementById('sugg-links');
    linksEl.innerHTML = '';
    suggestions.forEach((phrase, i) => {
      const a = document.createElement('a');
      a.textContent = `"${phrase}"`;
      a.title = `Search Seek for "${phrase}"${scope}`;
      a.addEventListener('click', () => {
        chrome.tabs.create({ url: seekSearchUrl(phrase, searchLocation) }); // normal user-initiated open, not a fetch — still 0-hop
      });
      linksEl.appendChild(a);
      if (i < suggestions.length - 1) linksEl.appendChild(document.createTextNode(' · '));
    });
    banner.hidden = false;
  } catch { /* suggestions are a nicety — fail silently */ }
}

document.getElementById('sugg-close').addEventListener('click', async () => {
  document.getElementById('suggestion-banner').hidden = true;
  await chrome.storage.local.set({ suggestionsDismissedUntil: Date.now() + SUGGESTION_COOLDOWN_MS });
});

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

// Runs inside a Seek detail page — scrape the job data.
async function pageScrapeDetail() {
  const m = location.pathname.match(/\/job\/(\d+)/);
  if (!m) return null;
  const start = Date.now();
  let descEl = null;
  while (Date.now() - start < 8000) {
    descEl = document.querySelector('[data-automation="jobAdDetails"]');
    if (descEl && descEl.innerText.trim()) break;
    await new Promise(r => setTimeout(r, 400));
  }
  const titleEl = document.querySelector('[data-automation="job-detail-title"]');
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

async function injectFn(tabId, func) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func });
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
          await waitForTabComplete(bgTab.id, 15000);
        } catch (e) {
          scanLog(`${label} could not open tab: ${e.message}`);
          if (bgTab) await chrome.tabs.remove(bgTab.id).catch(() => {});
          continue;
        }

        let payload = null;
        try { payload = await injectFn(bgTab.id, pageScrapeDetail); }
        catch (e) { scanLog(`${label} could not scrape: ${e.message}`); }
        finally { await chrome.tabs.remove(bgTab.id).catch(() => {}); }

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
  } finally {
    scanning = false;
    scanBtn.disabled = false;
  }
}

scanBtn.addEventListener('click', scanPage);
document.getElementById('refresh-btn').addEventListener('click', loadJobs);

// ---------------------------------------------------------------------------
// Personalise metrics — collapsible panel of user-tunable thresholds
// ---------------------------------------------------------------------------
const personaliseToggle = document.getElementById('personalise-toggle');
const personalisePanel = document.getElementById('personalise-panel');
const autoLetterInput = document.getElementById('auto-letter-min');
const autoLetterSaveBtn = document.getElementById('auto-letter-save');
const scanPagesInput = document.getElementById('scan-pages');
const scanPagesSaveBtn = document.getElementById('scan-pages-save');
const llmSuggestCheckbox = document.getElementById('llm-suggest');
const llmSuggestRefreshBtn = document.getElementById('llm-suggest-refresh');
const letterLoopCheckbox = document.getElementById('letter-loop-enabled');
const letterLoopInput = document.getElementById('letter-loop-min');
const letterLoopSaveBtn = document.getElementById('letter-loop-save');
const letterEngineSelect = document.getElementById('letter-engine');
const letterLoopNote = document.getElementById('letter-loop-note');

// The full cover-letter pipeline (docs/cover-letter-loop-plan.md §6). Stored server-side
// (profiles.preferences) because the idle loop is what acts on them. Pre-load defaults only.
let letterLoopEnabled = true;
let letterLoopMin = 85;
let letterEngine = 'agent';

// Letters from the auto-generate score up to the bar get the one-shot writer; the bar and
// above get the pipeline. The bar is the higher of the two settings (the backend applies
// the same rule), so lowering this below the auto-generate score changes nothing.
function updateLetterLoopNote() {
  const bar = Math.max(autoLetterMin, letterLoopMin);
  letterLoopNote.textContent = !letterLoopEnabled
    ? 'Off: every letter is written in one quick pass (about 3¢), with no fact-check or revision.'
    : bar > autoLetterMin
      ? `Scores ${autoLetterMin}–${bar - 1}: one quick pass (about 3¢). From ${bar} up: fact-checks and revisions (about 20¢ and a few minutes each). The bar is the higher of this score and the auto-generate score above. The agent picks each step itself; the fixed workflow takes the same steps in a set order.`
      : `Every automatic letter gets fact-checks and revisions (about 20¢ and a few minutes each). The agent picks each step itself; the fixed workflow takes the same steps in a set order.`;
}

async function savePref(body) {
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.ok;
  } catch {
    return false;
  }
}

personaliseToggle.addEventListener('click', () => {
  const open = personalisePanel.hidden; // about to open
  personalisePanel.hidden = !open;
  personaliseToggle.setAttribute('aria-expanded', String(open));
});

// Resolves once the stored preferences (if reachable) have been applied, so the
// first loadJobs() can classify "pending" letters against the real threshold.
async function loadPreferences() {
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`);
    if (!res.ok) return;
    const prefs = await res.json();
    if (Number.isInteger(prefs.auto_cover_letter_min_score)) {
      autoLetterMin = prefs.auto_cover_letter_min_score;
      autoLetterInput.value = autoLetterMin;
    }
    if (Number.isInteger(prefs.scan_max_pages)
        && prefs.scan_max_pages >= 1 && prefs.scan_max_pages <= SCAN_PAGES_CEILING) {
      scanMaxPages = prefs.scan_max_pages;
    }
    scanPagesInput.value = scanMaxPages;
    llmSuggestCheckbox.checked = prefs.llm_search_suggestions === true;
    letterLoopEnabled = prefs.letter_loop_enabled !== false;
    letterLoopCheckbox.checked = letterLoopEnabled;
    if (Number.isInteger(prefs.letter_loop_min_score)) letterLoopMin = prefs.letter_loop_min_score;
    letterLoopInput.value = letterLoopMin;
    if (prefs.letter_engine === 'agent' || prefs.letter_engine === 'workflow') letterEngine = prefs.letter_engine;
    letterEngineSelect.value = letterEngine;
    updateLetterLoopNote();
  } catch { /* backend down — keep defaults; loadJobs shows its own error */ }
}

autoLetterSaveBtn.addEventListener('click', async () => {
  const value = parseInt(autoLetterInput.value, 10);
  if (!Number.isInteger(value) || value < 0 || value > 100) {
    autoLetterInput.value = autoLetterMin;
    return;
  }
  autoLetterSaveBtn.disabled = true;
  autoLetterSaveBtn.textContent = '…';
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_cover_letter_min_score: value }),
    });
    if (!res.ok) throw new Error();
    autoLetterMin = value;
    updateLetterLoopNote();
    autoLetterSaveBtn.textContent = 'Saved ✓';
    loadJobs(); // cards move between "pending" and "no letter" states
  } catch {
    autoLetterSaveBtn.textContent = 'Error';
  }
  setTimeout(() => { autoLetterSaveBtn.textContent = 'Save'; autoLetterSaveBtn.disabled = false; }, 1500);
});

letterLoopCheckbox.addEventListener('change', async () => {
  const enabled = letterLoopCheckbox.checked;
  letterLoopCheckbox.disabled = true;
  if (await savePref({ letter_loop_enabled: enabled })) {
    letterLoopEnabled = enabled;
    updateLetterLoopNote();
  } else {
    letterLoopCheckbox.checked = !enabled; // revert — the setting didn't stick
  }
  letterLoopCheckbox.disabled = false;
});

letterEngineSelect.addEventListener('change', async () => {
  const engine = letterEngineSelect.value;
  letterEngineSelect.disabled = true;
  if (await savePref({ letter_engine: engine })) letterEngine = engine;
  else letterEngineSelect.value = letterEngine;
  letterEngineSelect.disabled = false;
});

letterLoopSaveBtn.addEventListener('click', async () => {
  const value = parseInt(letterLoopInput.value, 10);
  if (!Number.isInteger(value) || value < 0 || value > 100) {
    letterLoopInput.value = letterLoopMin; // out of range — snap back, don't save
    return;
  }
  letterLoopSaveBtn.disabled = true;
  letterLoopSaveBtn.textContent = '…';
  if (await savePref({ letter_loop_min_score: value })) {
    letterLoopMin = value;
    updateLetterLoopNote();
    letterLoopSaveBtn.textContent = 'Saved ✓';
  } else {
    letterLoopSaveBtn.textContent = 'Error';
  }
  setTimeout(() => { letterLoopSaveBtn.textContent = 'Save'; letterLoopSaveBtn.disabled = false; }, 1500);
});

scanPagesSaveBtn.addEventListener('click', async () => {
  const value = parseInt(scanPagesInput.value, 10);
  if (!Number.isInteger(value) || value < 1 || value > SCAN_PAGES_CEILING) {
    scanPagesInput.value = scanMaxPages; // out of range — snap back, don't save
    return;
  }
  scanPagesSaveBtn.disabled = true;
  scanPagesSaveBtn.textContent = '…';
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scan_max_pages: value }),
    });
    if (!res.ok) throw new Error();
    scanMaxPages = value;
    scanPagesSaveBtn.textContent = 'Saved ✓';
  } catch {
    scanPagesSaveBtn.textContent = 'Error';
  }
  setTimeout(() => { scanPagesSaveBtn.textContent = 'Save'; scanPagesSaveBtn.disabled = false; }, 1500);
});

// Layer 4 opt-in. Unticked, /jobs/suggested-searches never reaches the LLM;
// ticked, it spends one cached call. Ticking it also refreshes the banner
// immediately so the effect is visible rather than deferred to the next load.
llmSuggestCheckbox.addEventListener('change', async () => {
  const enabled = llmSuggestCheckbox.checked;
  llmSuggestCheckbox.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ llm_search_suggestions: enabled }),
    });
    if (!res.ok) throw new Error();
    await maybeShowSuggestions({ ignoreCooldown: true });
  } catch {
    llmSuggestCheckbox.checked = !enabled; // revert — the setting didn't stick
  } finally {
    llmSuggestCheckbox.disabled = false;
  }
});

// Forces a refine even when the cache is still warm, and even when the
// checkbox is off (a one-off look, without turning the feature on).
llmSuggestRefreshBtn.addEventListener('click', async () => {
  llmSuggestRefreshBtn.disabled = true;
  llmSuggestRefreshBtn.textContent = '…';
  try {
    await maybeShowSuggestions({ forceLlm: true, ignoreCooldown: true });
    llmSuggestRefreshBtn.textContent = 'Done ✓';
  } catch {
    llmSuggestRefreshBtn.textContent = 'Error';
  }
  setTimeout(() => {
    llmSuggestRefreshBtn.textContent = 'Refresh now';
    llmSuggestRefreshBtn.disabled = false;
  }, 1500);
});

// Layer 3: which searches actually paid off. Volume is also the LLM cost of a
// search, so a big-volume/low-yield row is the one worth retiring.
const searchPerfEl = document.getElementById('search-perf');
document.getElementById('search-perf-btn').addEventListener('click', async () => {
  const btn = document.getElementById('search-perf-btn');
  if (!searchPerfEl.hidden) {
    searchPerfEl.hidden = true;
    btn.textContent = 'Show';
    return;
  }
  btn.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/jobs/search-performance?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { performance, underperforming } = await res.json();
    if (!performance.length) {
      searchPerfEl.textContent =
        'No data yet — searches are tracked from the next results page you open.';
    } else {
      const weak = new Set(underperforming);
      searchPerfEl.innerHTML = '';
      const table = document.createElement('table');
      const head = table.insertRow();
      ['Search', 'Jobs', 'Good', 'Yield'].forEach((label, i) => {
        const th = document.createElement('th');
        th.textContent = label;
        if (i > 0) th.className = 'num';
        head.appendChild(th);
      });
      for (const row of performance) {
        const tr = table.insertRow();
        if (weak.has(row.query)) tr.className = 'weak';
        tr.insertCell().textContent = row.query;
        [row.volume, row.hits, `${Math.round(row.yield * 100)}%`].forEach((v) => {
          const td = tr.insertCell();
          td.textContent = v;
          td.className = 'num';
        });
      }
      searchPerfEl.appendChild(table);
    }
    searchPerfEl.hidden = false;
    btn.textContent = 'Hide';
  } catch {
    searchPerfEl.textContent = 'Could not load search performance.';
    searchPerfEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('bulk-delete-btn').addEventListener('click', async () => {
  const score = parseFloat(document.getElementById('bulk-score').value);
  if (isNaN(score)) return;
  const btn = document.getElementById('bulk-delete-btn');
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const res = await fetch(`${BACKEND}/jobs?below_score=${score}&profile_id=${PROFILE_ID}`, { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      btn.textContent = `Hid ${data.hidden ?? data.deleted}`;
      setTimeout(() => { btn.textContent = 'Hide'; btn.disabled = false; }, 2000);
      loadJobs();
    } else {
      throw new Error();
    }
  } catch {
    btn.textContent = 'Error';
    setTimeout(() => { btn.textContent = 'Hide'; btn.disabled = false; }, 2000);
  }
});

// ---------------------------------------------------------------------------
// Profile tab — dynamic cards
// ---------------------------------------------------------------------------
const profileMsg = document.getElementById('profile-msg');

function showMsg(text, type, ms = 3000) {
  profileMsg.textContent = text;
  profileMsg.className = type;
  setTimeout(() => { profileMsg.className = ''; }, ms);
}

// Unsaved-changes tracking. The profile form is only persisted by Save Profile
// (a card's "Done" merely collapses it). "Dirty" means the form's data differs
// from what was last loaded from or accepted by the backend — a snapshot
// comparison, not "something changed in the DOM". Flagging on any mutation
// re-dirtied the form straight after every save (the "Saved ✓" button text and
// the status message are mutations too), and also on Edit/Done or an edit typed
// then undone. Input/change events and DOM mutations now just re-run the check.
const dirtyNote = document.getElementById('dirty-note');
const saveBar = document.getElementById('save-bar');
let savedSnapshot = null; // JSON of the last loaded/saved form data; null = not loaded yet

function setProfileDirty(dirty) {
  dirtyNote.hidden = !dirty;
  saveBar.classList.toggle('dirty', dirty);
}

function refreshProfileDirty() {
  if (savedSnapshot === null) return setProfileDirty(false);
  setProfileDirty(JSON.stringify(readProfileForm()) !== savedSnapshot);
}

// Called after the form is (re)filled from the backend or saved. `snapshot` is
// the JSON of what the backend now holds; it defaults to the form as it stands.
function markProfileClean(snapshot = JSON.stringify(readProfileForm())) {
  savedSnapshot = snapshot;
  refreshProfileDirty();
}

const profileSectionEl = document.getElementById('profile-section');
new MutationObserver(refreshProfileDirty)
  .observe(profileSectionEl, { childList: true, subtree: true });
profileSectionEl.addEventListener('input', refreshProfileDirty);
profileSectionEl.addEventListener('change', refreshProfileDirty);

// Rows the cover-letter flow added (ask_user, plan Q12) carry a plain tag, so you can
// tell them from rows you entered. The server keeps the tag across profile saves.
function addOriginBadge(summaryEl, data) {
  if (!data?.origin_label) return;
  const label = data.origin_label;
  const badge = mk('div', 'origin-badge', `✦ ${label.charAt(0).toUpperCase()}${label.slice(1)}`);
  badge.style.cssText = 'display:inline-block;margin-top:4px;';
  summaryEl.querySelector('.summary-meta').after(badge);
}

// -- Qualification card --

function makeQualCard(data = {}, isNew = false) {
  const card = document.createElement('div');
  card.className = 'entry-card';

  // Summary panel
  const summaryEl = document.createElement('div');
  summaryEl.className = 'card-summary';
  summaryEl.innerHTML = `
    <div class="summary-header">
      <span class="summary-title"></span>
      <span class="type-badge"></span>
    </div>
    <div class="summary-meta"></div>
    <div class="card-actions">
      <button class="btn btn-sm edit-btn">Edit</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  // Form panel
  const formEl = document.createElement('div');
  formEl.className = 'card-form';
  formEl.innerHTML = `
    <label class="field"><span>Type</span>
      <select class="f-type">
        <option value="degree">Degree</option>
        <option value="certificate">Certificate</option>
        <option value="diploma">Diploma</option>
        <option value="other">Other</option>
      </select>
    </label>
    <label class="field"><span>Title</span>
      <input class="f-title" type="text" placeholder="e.g. Bachelor of Computer Science">
    </label>
    <label class="field"><span>Institution</span>
      <input class="f-institution" type="text" placeholder="University / provider">
    </label>
    <div class="two-col">
      <label class="field"><span>Field of Study</span>
        <input class="f-field" type="text" placeholder="e.g. Computer Science">
      </label>
      <label class="field"><span>Grade</span>
        <input class="f-grade" type="text" placeholder="e.g. Distinction">
      </label>
    </div>
    <div class="two-col">
      <label class="field"><span>Start</span>${monthInputHtml('f-start')}</label>
      <label class="field"><span>End</span>${monthInputHtml('f-end')}</label>
    </div>
    <label class="field"><span>Status</span>
      <select class="f-status">
        <option value="completed">Completed</option>
        <option value="in_progress">In Progress</option>
        <option value="withdrawn">Withdrawn</option>
      </select>
    </label>
    <div class="card-actions">
      <button class="btn btn-sm done-btn" style="flex:1">Done</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  card.appendChild(summaryEl);
  card.appendChild(formEl);
  addOriginBadge(summaryEl, data);

  // Populate form
  if (data.qualification_type) formEl.querySelector('.f-type').value = data.qualification_type;
  if (data.title)              formEl.querySelector('.f-title').value = data.title;
  if (data.institution)        formEl.querySelector('.f-institution').value = data.institution;
  if (data.field_of_study)     formEl.querySelector('.f-field').value = data.field_of_study;
  if (data.grade)              formEl.querySelector('.f-grade').value = data.grade;
  if (data.start_date)         formEl.querySelector('.f-start').value = data.start_date;
  if (data.end_date)           formEl.querySelector('.f-end').value = data.end_date;
  if (data.status)             formEl.querySelector('.f-status').value = data.status;

  function updateSummary() {
    const type   = formEl.querySelector('.f-type').value;
    const title  = formEl.querySelector('.f-title').value || '(untitled)';
    const inst   = formEl.querySelector('.f-institution').value;
    const field  = formEl.querySelector('.f-field').value;
    const start  = monthValue(formEl.querySelector('.f-start'));
    const end    = monthValue(formEl.querySelector('.f-end'));
    const status = formEl.querySelector('.f-status').value;

    summaryEl.querySelector('.summary-title').textContent = title;
    summaryEl.querySelector('.type-badge').textContent = QUAL_TYPE_LABELS[type] || type;

    const metaEl = summaryEl.querySelector('.summary-meta');
    metaEl.innerHTML = '';
    const line1 = [inst, field].filter(Boolean).join(' · ');
    if (line1) { const d = document.createElement('div'); d.textContent = line1; metaEl.appendChild(d); }
    const dateParts = [start ? fmtDate(start) : '', end ? fmtDate(end) : (status === 'in_progress' ? 'Present' : '')].filter(Boolean);
    const line2 = [dateParts.join(' – '), QUAL_STATUS_LABELS[status] || status].filter(Boolean).join(' · ');
    if (line2) { const d = document.createElement('div'); d.textContent = line2; metaEl.appendChild(d); }
  }

  function showForm() { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  function showSummary() { updateSummary(); formEl.style.display = 'none'; summaryEl.style.display = ''; }

  summaryEl.querySelector('.edit-btn').addEventListener('click', showForm);
  formEl.querySelector('.done-btn').addEventListener('click', showSummary);
  card.querySelectorAll('.card-rm-btn').forEach(b => b.addEventListener('click', () => card.remove()));

  if (isNew) { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  else       { updateSummary(); summaryEl.style.display = ''; formEl.style.display = 'none'; }

  return card;
}

function readQualCard(card) {
  return {
    qualification_type: card.querySelector('.f-type').value,
    title:              card.querySelector('.f-title').value.trim(),
    institution:        card.querySelector('.f-institution').value.trim() || null,
    field_of_study:     card.querySelector('.f-field').value.trim() || null,
    grade:              card.querySelector('.f-grade').value.trim() || null,
    start_date:         monthValue(card.querySelector('.f-start')) || null,
    end_date:           monthValue(card.querySelector('.f-end')) || null,
    status:             card.querySelector('.f-status').value,
  };
}

// -- Experience card --

function makeExpCard(data = {}, isNew = false) {
  const card = document.createElement('div');
  card.className = 'entry-card';

  // Summary panel
  const summaryEl = document.createElement('div');
  summaryEl.className = 'card-summary';
  summaryEl.innerHTML = `
    <div class="summary-header">
      <span class="summary-title"></span>
      <span class="type-badge"></span>
    </div>
    <div class="summary-meta"></div>
    <div class="card-actions">
      <button class="btn btn-sm edit-btn">Edit</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  // Form panel
  const formEl = document.createElement('div');
  formEl.className = 'card-form';
  formEl.innerHTML = `
    <label class="field"><span>Type</span>
      <select class="f-type">
        <option value="job">Job</option>
        <option value="internship">Internship</option>
        <option value="volunteer">Volunteer</option>
        <option value="project">Project</option>
        <option value="university_project">University project</option>
        <option value="assignment">Assignment</option>
        <option value="personal_project">Personal project</option>
      </select>
    </label>
    <label class="field"><span>Title / Role</span>
      <input class="f-title" type="text" placeholder="e.g. Software Engineer">
    </label>
    <label class="field"><span>Organisation</span>
      <input class="f-org" type="text" placeholder="Company or project name">
    </label>
    <div class="two-col">
      <label class="field"><span>Start</span>${monthInputHtml('f-start')}</label>
      <label class="field f-end-label"><span>End</span>${monthInputHtml('f-end')}</label>
    </div>
    <div class="check-row">
      <input class="f-current" type="checkbox"><label>Current role</label>
    </div>
    <label class="field"><span>Description</span>
      <textarea class="f-desc" placeholder="Key responsibilities and achievements…"></textarea>
    </label>
    <label class="field"><span>Skills used (comma-separated)</span>
      <input class="f-skills" type="text" placeholder="Python, SQL, React…">
    </label>
    <div class="card-actions">
      <button class="btn btn-sm done-btn" style="flex:1">Done</button>
      <button class="btn btn-sm btn-remove card-rm-btn">Remove</button>
    </div>
  `;

  card.appendChild(summaryEl);
  card.appendChild(formEl);
  addOriginBadge(summaryEl, data);

  const currentCb = formEl.querySelector('.f-current');
  const endLabel  = formEl.querySelector('.f-end-label');
  const endInput  = formEl.querySelector('.f-end');

  function toggleEnd() {
    endLabel.style.opacity = currentCb.checked ? '0.35' : '1';
    endInput.disabled = currentCb.checked;
  }
  currentCb.addEventListener('change', toggleEnd);

  // Populate form
  if (data.experience_type) formEl.querySelector('.f-type').value = data.experience_type;
  if (data.title)           formEl.querySelector('.f-title').value = data.title;
  if (data.organization)    formEl.querySelector('.f-org').value = data.organization;
  if (data.start_date)      formEl.querySelector('.f-start').value = data.start_date;
  if (data.end_date)        formEl.querySelector('.f-end').value = data.end_date;
  if (data.description)     formEl.querySelector('.f-desc').value = data.description;
  if (data.skills?.length)  formEl.querySelector('.f-skills').value = data.skills.join(', ');
  if (data.is_current)      { currentCb.checked = true; toggleEnd(); }

  function updateSummary() {
    const type   = formEl.querySelector('.f-type').value;
    const title  = formEl.querySelector('.f-title').value || '(untitled)';
    const org    = formEl.querySelector('.f-org').value;
    const start  = monthValue(formEl.querySelector('.f-start'));
    const end    = monthValue(formEl.querySelector('.f-end'));
    const isCur  = formEl.querySelector('.f-current').checked;
    const desc   = formEl.querySelector('.f-desc').value;

    summaryEl.querySelector('.summary-title').textContent = title;
    summaryEl.querySelector('.type-badge').textContent = EXP_TYPE_LABELS[type] || type;

    const metaEl = summaryEl.querySelector('.summary-meta');
    metaEl.innerHTML = '';
    if (org) { const d = document.createElement('div'); d.textContent = org; metaEl.appendChild(d); }
    const dateParts = [start ? fmtDate(start) : '', isCur ? 'Present' : (end ? fmtDate(end) : '')].filter(Boolean);
    if (dateParts.length) { const d = document.createElement('div'); d.textContent = dateParts.join(' – '); metaEl.appendChild(d); }
    if (desc) {
      const d = document.createElement('div');
      d.className = 'desc-preview';
      d.textContent = truncate(desc, 80);
      metaEl.appendChild(d);
    }
  }

  function showForm() { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  function showSummary() { updateSummary(); formEl.style.display = 'none'; summaryEl.style.display = ''; }

  summaryEl.querySelector('.edit-btn').addEventListener('click', showForm);
  formEl.querySelector('.done-btn').addEventListener('click', showSummary);
  card.querySelectorAll('.card-rm-btn').forEach(b => b.addEventListener('click', () => card.remove()));

  if (isNew) { summaryEl.style.display = 'none'; formEl.style.display = ''; }
  else       { updateSummary(); summaryEl.style.display = ''; formEl.style.display = 'none'; }

  return card;
}

function readExpCard(card) {
  const isCurrent = card.querySelector('.f-current').checked;
  return {
    experience_type: card.querySelector('.f-type').value,
    title:           card.querySelector('.f-title').value.trim(),
    organization:    card.querySelector('.f-org').value.trim() || null,
    start_date:      monthValue(card.querySelector('.f-start')) || null,
    end_date:        isCurrent ? null : (monthValue(card.querySelector('.f-end')) || null),
    is_current:      isCurrent,
    description:     card.querySelector('.f-desc').value.trim() || null,
    skills:          card.querySelector('.f-skills').value.split(',').map(s => s.trim()).filter(Boolean),
  };
}

// -- Skills chips --

let skillsData = [];
let skillOrigins = {}; // skill name -> origin label, for skills the cover-letter flow added

function renderSkillPills() {
  const container = document.getElementById('skills-pills');
  container.innerHTML = '';
  for (const skill of skillsData) {
    const chip = document.createElement('span');
    chip.className = 'skill-chip';
    chip.appendChild(document.createTextNode(skill));
    if (skillOrigins[skill]) {
      const dot = mk('span', 'origin-dot', '✦');
      dot.title = skillOrigins[skill].charAt(0).toUpperCase() + skillOrigins[skill].slice(1);
      chip.appendChild(dot);
    }
    const rm = document.createElement('button');
    rm.className = 'rm-skill';
    rm.textContent = '×';
    rm.title = 'Remove';
    rm.addEventListener('click', () => {
      skillsData = skillsData.filter(s => s !== skill);
      renderSkillPills();
    });
    chip.appendChild(rm);
    container.appendChild(chip);
  }
}

function addSkill(name) {
  const trimmed = name.trim();
  if (!trimmed || skillsData.includes(trimmed)) return false;
  skillsData.push(trimmed);
  renderSkillPills();
  return true;
}

// -- Load / Save --

async function loadProfile() {
  profileLoaded = true;
  try {
    const res = await fetch(`${BACKEND}/profile-ui/data`);
    if (res.status === 404) return markProfileClean(); // no profile yet — blank form is the baseline
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    populateForm(await res.json());
  } catch (e) {
    showMsg(`Could not load profile: ${e.message}`, 'err');
    markProfileClean(); // still flag edits typed into the (blank) form
  }
}

// The form's data in the PUT /profile-ui/data shape. Every card is included,
// titled or not, so an untitled card (which save skips) keeps the form dirty.
function readProfileForm() {
  const val = id => document.getElementById(id).value.trim();
  return {
    profile: {
      name:            val('p-name'),
      email:           val('p-email'),
      phone:           val('p-phone')           || null,
      location:        val('p-location')        || null,
      summary:         val('p-summary')         || null,
      writing_sample:  val('p-writing-sample')  || null,
      target_role:     val('p-target-role')     || null,
      target_location: val('p-target-location') || null,
    },
    qualifications: [...document.querySelectorAll('#quals-list .entry-card')].map(readQualCard),
    experiences:    [...document.querySelectorAll('#exps-list .entry-card')].map(readExpCard),
    skills:         [...skillsData],
  };
}

function populateForm(data) {
  const p = data.profile || {};
  document.getElementById('p-name').value            = p.name            || '';
  document.getElementById('p-email').value           = p.email           || '';
  document.getElementById('p-phone').value           = p.phone           || '';
  document.getElementById('p-location').value        = p.location        || '';
  document.getElementById('p-summary').value         = p.summary         || '';
  document.getElementById('p-target-role').value     = p.target_role     || '';
  document.getElementById('p-target-location').value = p.target_location || '';
  document.getElementById('p-writing-sample').value  = p.writing_sample  || '';

  const qualsList = document.getElementById('quals-list');
  qualsList.innerHTML = '';
  for (const q of (data.qualifications || [])) qualsList.appendChild(makeQualCard(q, false));

  const expsList = document.getElementById('exps-list');
  expsList.innerHTML = '';
  for (const e of (data.experiences || [])) expsList.appendChild(makeExpCard(e, false));

  skillsData = data.skills || [];
  skillOrigins = data.skill_origins || {};
  renderSkillPills();
  updateWritingStats();
  markProfileClean();
}

// -- Your Writing view --
// The sample is often several pages, so the form shows a button with a word
// count and the text gets a view of its own. The textarea stays inside
// #profile-section, so saving and the unsaved-changes note work unchanged.

const writingInput = document.getElementById('p-writing-sample');
const profileMain  = document.getElementById('profile-main');
const writingView  = document.getElementById('writing-view');

function updateWritingStats() {
  const words = (writingInput.value.match(/\S+/g) || []).length;
  const text = words ? `${words.toLocaleString()} word${words === 1 ? '' : 's'}` : 'Nothing added yet';
  document.getElementById('writing-stats').textContent = text;
  document.getElementById('writing-view-stats').textContent = text;
}

function showWritingView(open) {
  profileMain.hidden = open;
  writingView.hidden = !open;
  profileSection.scrollIntoView({ block: 'start' });
  if (open) writingInput.focus({ preventScroll: true });
}

writingInput.addEventListener('input', updateWritingStats);
document.getElementById('open-writing-btn').addEventListener('click', () => showWritingView(true));
document.getElementById('close-writing-btn').addEventListener('click', () => showWritingView(false));

async function saveProfile() {
  const btn = document.getElementById('save-profile-btn');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  let savedLabel = 'Save Profile';
  try {
    const form  = readProfileForm();
    const quals = form.qualifications.filter(q => q.title);
    const exps  = form.experiences.filter(e => e.title);
    // A card with no title can't be stored (the backend skips it too) — say so
    // instead of letting it vanish on the next load.
    const dropped = (form.qualifications.length - quals.length) + (form.experiences.length - exps.length);
    const body = { ...form, qualifications: quals, experiences: exps };
    const bodyJson = JSON.stringify(body);

    const res = await fetch(`${BACKEND}/profile-ui/data`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: bodyJson,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    // Baseline = exactly what was sent, so edits typed while the request was in
    // flight, and any skipped untitled cards, still show as unsaved.
    markProfileClean(bodyJson);
    loadToWorkOn(); // saving a skill you said no to takes it off the list (auto-clear)
    if (dropped) {
      showMsg(`Saved — but ${dropped} entr${dropped === 1 ? 'y' : 'ies'} with no title ${dropped === 1 ? 'was' : 'were'} skipped. Give ${dropped === 1 ? 'it' : 'them'} a title and save again.`, 'err', 8000);
    } else {
      showMsg('Profile saved.', 'ok');
    }
    savedLabel = dropped ? 'Save Profile' : 'Saved ✓';
  } catch (e) {
    showMsg(`Save failed: ${e.message}`, 'err');
    savedLabel = 'Save failed — retry';
  } finally {
    // The message above renders at the TOP of the panel, off-screen when you
    // click the pinned button at the bottom — so the button itself confirms.
    btn.disabled = false;
    btn.textContent = savedLabel;
    if (savedLabel !== 'Save Profile') setTimeout(() => { btn.textContent = 'Save Profile'; }, 2500);
  }
}

document.getElementById('add-qual-btn').addEventListener('click', () =>
  document.getElementById('quals-list').appendChild(makeQualCard({}, true)));

document.getElementById('add-exp-btn').addEventListener('click', () =>
  document.getElementById('exps-list').appendChild(makeExpCard({}, true)));

document.getElementById('save-profile-btn').addEventListener('click', saveProfile);

// -- To work on (plan §5.9): skills you said no to, ranked by how often ads ask for them --

async function loadToWorkOn() {
  const list = document.getElementById('towork-list');
  try {
    const res = await fetch(`${BACKEND}/gaps/to-work-on?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    const { window_days, items } = await res.json();
    renderToWorkOn(list, items, window_days);
  } catch {
    list.textContent = 'Could not load this list — is the backend running?';
  }
}

function renderToWorkOn(list, items, windowDays) {
  list.innerHTML = '';
  if (!items.length) {
    list.appendChild(mk('div', 'towork-empty',
      'Nothing yet. When a cover letter asks whether you have a skill and you answer No, it lands here.'));
    return;
  }
  for (const it of items) {
    const card = mk('div', 'towork-item');
    const head = mk('div', 'towork-head');
    head.appendChild(mk('strong', null, it.label));
    const parts = [`${it.recent} ad${it.recent === 1 ? '' : 's'} in the last ${windowDays} days`];
    if (it.essential) parts.push(`${it.essential} essential`);
    head.appendChild(mk('span', 'towork-count', parts.join(', ')));
    card.appendChild(head);

    const sub = [];
    if (it.recent_titles?.length) sub.push(`Recently: ${it.recent_titles.join('; ')}`);
    if (it.total > it.recent) sub.push(`${it.total} ads in all`);
    if (it.said_no_at) {
      sub.push(`You said no on ${new Date(it.said_no_at).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })}`);
    }
    if (sub.length) card.appendChild(mk('div', 'towork-sub', sub.join(' · ')));

    const actions = mk('div', 'card-actions');
    const clear = mk('button', 'btn btn-sm', "I've learned this — clear");
    clear.title = 'Takes it off this list. A later cover letter that needs it will ask you again.';
    clear.addEventListener('click', async () => {
      clear.disabled = true;
      try {
        const res = await fetch(`${BACKEND}/gaps/${it.id}/clear?profile_id=${PROFILE_ID}`, { method: 'POST' });
        if (!res.ok) throw new Error();
        loadToWorkOn();
      } catch {
        clear.disabled = false;
        clear.textContent = 'Could not clear — try again';
      }
    });
    actions.appendChild(clear);
    card.appendChild(actions);
    list.appendChild(card);
  }
}

document.getElementById('towork-refresh').addEventListener('click', loadToWorkOn);

// Skill add UI
function confirmSkill() {
  const inp = document.getElementById('skill-input');
  if (addSkill(inp.value)) inp.value = '';
  inp.focus();
}
function cancelSkillInput() {
  document.getElementById('skill-input').value = '';
  document.getElementById('add-skill-row').style.display = 'none';
}

document.getElementById('add-skill-btn').addEventListener('click', () => {
  const row = document.getElementById('add-skill-row');
  row.style.display = 'flex';
  document.getElementById('skill-input').focus();
});
document.getElementById('skill-ok-btn').addEventListener('click', confirmSkill);
document.getElementById('skill-cancel-btn').addEventListener('click', cancelSkillInput);
document.getElementById('skill-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); confirmSkill(); }
  if (e.key === 'Escape') cancelSkillInput();
});

// ---------------------------------------------------------------------------
// Import from Seek Profile
// ---------------------------------------------------------------------------

// Runs INSIDE the au.seek.com/profile/me tab — extracts profile data.
// All extraction happens in the user's own browser/session; nothing goes server-side.
async function seekProfileExtract() {
  // Seek is a SPA — wait until the profile content has actually rendered
  await new Promise(resolve => {
    const start = Date.now();
    function check() {
      const ready = document.querySelector('[data-automation="personal-details-card"]')
                 || document.querySelector('[data-automation="read-role"]')
                 || document.querySelector('[data-automation="skills-items"]');
      if (ready || Date.now() - start > 15000) resolve();
      else setTimeout(check, 500);
    }
    check();
  });

  function da(attr)    { return document.querySelector(`[data-automation="${attr}"]`); }
  function daAll(attr) { return [...document.querySelectorAll(`[data-automation="${attr}"]`)]; }

  // Clone el, remove noisy child nodes, return trimmed innerText
  function cleanText(el, removeSelectors) {
    if (!el) return '';
    const c = el.cloneNode(true);
    c.querySelectorAll(removeSelectors).forEach(n => n.remove());
    return c.innerText.trim();
  }

  // "Jan 2022" / "January 2022" / "2022" → "YYYY-MM"
  function parseDate(str) {
    if (!str) return null;
    const MONTHS = {jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
    const m = str.trim().match(/([a-z]+)\s+(\d{4})/i);
    if (m) {
      const mon = MONTHS[m[1].toLowerCase().slice(0, 3)];
      if (mon) return `${m[2]}-${String(mon).padStart(2, '0')}`;
    }
    const y = str.trim().match(/^(\d{4})$/);
    if (y) return `${y[1]}-01`;
    return null;
  }

  // "Dec 2020 - Present (5 years 7 months)" → [startYYYY-MM, endYYYY-MM|null, isCurrent]
  function parseDateRange(raw) {
    if (!raw) return [null, null, false];
    const str = raw.replace(/\s*\(.*?\)\s*$/, '').trim(); // strip "(5 years 7 months)"
    const parts = str.split(/\s*[-–—]\s*/); // hyphen, en-dash, em-dash
    const start = parseDate(parts[0]);
    const endRaw = (parts[1] || '').trim();
    const isCurrent = /present|current/i.test(endRaw);
    return [start, isCurrent ? null : parseDate(endRaw), isCurrent];
  }

  const NOISE = 'button, svg, [aria-hidden="true"], [role="button"]';

  const out = {
    name: null, location: null, email: null, summary: null,
    experiences: [], qualifications: [], skills: [],
    unrecognised: {},  // read-* sections seen on the page but not imported: { 'read-x': count }
  };

  // ── Personal details ──────────────────────────────────────────────────────
  // Confirmed from live HTML: name is in [data-automation="inline-nudge-name"]
  const nameEl = da('inline-nudge-name');
  if (nameEl) out.name = nameEl.innerText.trim() || null;
  const locEl = da('inline-nudge-location');
  if (locEl) {
    const t = cleanText(locEl, NOISE);
    if (t && !/^add\s/i.test(t)) out.location = t; // skip "Add location" nudge
  }
  const emailEl = da('personal-detail-email');
  if (emailEl) out.email = emailEl.innerText.trim() || null;

  // ── Summary ───────────────────────────────────────────────────────────────
  const summaryCard = da('summary-card');
  if (summaryCard) {
    out.summary = cleanText(summaryCard,
      `${NOISE}, [data-automation="summary-read-title"], [data-automation="summary-edit"], [data-automation="summary-empty-nudge"]`
    ) || null;
  }

  // ── Career history ────────────────────────────────────────────────────────
  // Confirmed from live HTML:
  //   h4                → job title  ("Team Member")
  //   time              → date range ("Dec 2020 - Present (5 years 7 months)")
  //   [data-hj-masked]  → description (appears twice for clamp/expand; take first visible)
  //   remaining text    → company name (after stripping all above + buttons)
  daAll('read-role').forEach(item => {
    const title   = item.querySelector('h4')?.innerText?.trim() || '';
    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate, isCurrent] = parseDateRange(dateRaw);

    const descEl = item.querySelector(':not([aria-hidden="true"]) [data-hj-masked]')
                || item.querySelector('[data-hj-masked]');
    const description = descEl?.innerText?.trim().replace(/^[•·]\s*/, '') || '';

    // Company: remove all known elements; first remaining line is the company name
    const company = cleanText(item, `h4, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    out.experiences.push({
      experience_type: 'job', title, organization: company,
      start_date: startDate, end_date: endDate, is_current: isCurrent,
      description, skills: [],
    });
  });

  // ── Education ─────────────────────────────────────────────────────────────
  // Same pattern as roles: h4 = degree title, time = dates, remainder = institution
  daAll('read-qualification').forEach(item => {
    const title   = item.querySelector('h4, h3')?.innerText?.trim() || '';
    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate] = parseDateRange(dateRaw);
    const institution = cleanText(item, `h4, h3, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    out.qualifications.push({
      qualification_type: 'degree', title, institution,
      field_of_study: '', grade: '',
      start_date: startDate, end_date: endDate, status: 'completed',
    });
  });

  // ── Other sections (projects, volunteering, certifications, …) ────────────
  // NOT confirmed against live HTML: only read-role / read-qualification have
  // been seen. Seek's profile sections appear to share the read-<thing> naming
  // and the h4 + time layout, so any OTHER read-* block is parsed the same way
  // when its name says what it is (below). Anything whose name doesn't say — and
  // any block with no h4/h3 title — is left out of the import and reported by
  // name in `unrecognised`, so the status line tells us exactly what to add
  // rather than silently dropping it.
  const KNOWN_READ = new Set(['read-role', 'read-qualification']);
  const EXP_TYPE_BY_NAME  = [[/volunteer/i, 'volunteer'], [/intern/i, 'internship'], [/project/i, 'project']];
  const CERT_NAME = /licen[cs]e|certif|course|training/i;

  document.querySelectorAll('[data-automation^="read-"]').forEach(item => {
    const key = item.getAttribute('data-automation');
    if (KNOWN_READ.has(key)) return;

    const title = item.querySelector('h4, h3')?.innerText?.trim() || '';
    const expType = EXP_TYPE_BY_NAME.find(([re]) => re.test(key))?.[1] || null;
    const isCert  = CERT_NAME.test(key);
    if (!title || (!expType && !isCert)) {
      out.unrecognised[key] = (out.unrecognised[key] || 0) + 1;
      return;
    }

    const dateRaw = item.querySelector('time')?.innerText?.trim() || '';
    const [startDate, endDate, isCurrent] = parseDateRange(dateRaw);
    const descEl = item.querySelector(':not([aria-hidden="true"]) [data-hj-masked]')
                || item.querySelector('[data-hj-masked]');
    const description = descEl?.innerText?.trim().replace(/^[•·]\s*/, '') || '';
    const rest = cleanText(item, `h4, h3, time, [data-hj-masked], ${NOISE}`)
      .split('\n')[0]?.trim() || '';

    if (expType) {
      out.experiences.push({
        experience_type: expType, title, organization: rest,
        start_date: startDate, end_date: endDate, is_current: isCurrent,
        description, skills: [],
      });
    } else {
      out.qualifications.push({
        qualification_type: 'certificate', title, institution: rest,
        field_of_study: '', grade: '',
        start_date: startDate, end_date: endDate, status: 'completed',
      });
    }
  });

  // ── Skills ────────────────────────────────────────────────────────────────
  // Confirmed from live HTML: <li><div title="PHP Programming">...</div></li>
  // The title attribute is cleanest — no leading spaces.
  const skillsEl = da('skills-items');
  if (skillsEl) {
    out.skills = [...skillsEl.querySelectorAll('div[title]')]
      .map(el => el.getAttribute('title'))
      .filter(Boolean);
  }

  return out;
}

function setImportStatus(msg, color) {
  const el = document.getElementById('import-status');
  if (el) { el.textContent = msg; el.style.color = color || '#6b7280'; }
}

async function importFromSeekProfile() {
  const btn = document.getElementById('seek-import-btn');
  btn.disabled = true;
  setImportStatus('Opening Seek profile…', '#6b7280');

  let tab;
  try {
    tab = await chrome.tabs.create({ url: 'https://au.seek.com/profile/me', active: false });
    await waitForTabComplete(tab.id, 20000);
    setImportStatus('Waiting for page to render…', '#6b7280');
    // Extra buffer — Seek SPA often fires 'complete' before React has rendered content
    await sleep(2000);

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: seekProfileExtract,
    });

    console.log('[SeekImport] Extracted:', result);

    if (!result) throw new Error('No data returned from page.');

    // Log debug info so we can refine selectors if needed
    if (result._debug) {
      console.log('[SeekImport] Debug:', result._debug);
    }

    // Merge into the profile form — only overwrite fields that have data
    const p = result;
    if (p.name)     document.getElementById('p-name').value     = p.name;
    if (p.email)    document.getElementById('p-email').value    = p.email;
    if (p.location) document.getElementById('p-location').value = p.location;
    if (p.summary)  document.getElementById('p-summary').value  = p.summary;

    // Qualifications — prepend imported ones, keeping existing
    const qualsList = document.getElementById('quals-list');
    for (const q of (p.qualifications || [])) {
      if (q.title) qualsList.prepend(makeQualCard(q, false));
    }

    // Experiences — prepend imported ones, keeping existing
    const expsList = document.getElementById('exps-list');
    for (const e of (p.experiences || [])) {
      if (e.title) expsList.prepend(makeExpCard(e, false));
    }

    // Skills — merge without duplicates
    for (const s of (p.skills || [])) addSkill(s);
    // Programmatic .value writes fire no input event — re-check explicitly.
    refreshProfileDirty();

    const counts = [
      p.experiences?.length && `${p.experiences.length} experience(s)`,
      p.qualifications?.length && `${p.qualifications.length} qualification(s)`,
      p.skills?.length && `${p.skills.length} skill(s)`,
    ].filter(Boolean);

    // Sections the page has that the importer couldn't classify — surfaced by
    // name (not silently dropped) so the right selector can be added.
    const skippedSections = Object.entries(p.unrecognised || {})
      .map(([k, n]) => `${k} ×${n}`).join(', ');

    if (!p.name && !counts.length) {
      setImportStatus('Nothing extracted — page may not have rendered. Check browser console (F12).', '#d97706');
    } else {
      const skipped = skippedSections ? ` Not imported (unrecognised): ${skippedSections}.` : '';
      setImportStatus(
        `Imported: ${[p.name && 'name', ...counts].filter(Boolean).join(', ')}. Review & save.${skipped}`,
        skippedSections ? '#d97706' : '#059669',
      );
    }
  } catch (e) {
    console.error('[SeekImport] Error:', e);
    setImportStatus(`Import failed: ${e.message}`, '#dc2626');
  } finally {
    if (tab) await chrome.tabs.remove(tab.id).catch(() => {});
    btn.disabled = false;
  }
}

document.getElementById('seek-import-btn').addEventListener('click', importFromSeekProfile);

document.getElementById('export-profile-btn').addEventListener('click', async () => {
  try {
    const res = await fetch(`${BACKEND}/profile-ui/data`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `profile-backup-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    setImportStatus(`Export failed: ${e.message}`, '#dc2626');
  }
});

// ---------------------------------------------------------------------------
// LLM budget banner — shown while the backend's USD cap is blocking cover
// letters (mid/strong calls). Scanning, extraction and matching keep running.
// ---------------------------------------------------------------------------
async function refreshBudgetBanner() {
  const banner = document.getElementById('budget-banner');
  try {
    const res = await fetch(`${BACKEND}/llm/usage`);
    if (!res.ok) return;
    const status = await res.json();
    banner.textContent = status.blocked
      ? `${status.reason} Cover letters are paused; scanning and scoring continue.`
      : '';
    banner.hidden = !status.blocked;
  } catch { /* the banner is advisory — fail silently */ }
}

// ---------------------------------------------------------------------------
// SSE — live updates from the backend
// ---------------------------------------------------------------------------
let _eventsEverOpened = false;

// Reload the job list after a letter event, debounced. Never while the user is typing
// in the list (an answer to a question, a letter edit): a reload would wipe it, so it
// tries again a few seconds later.
let reloadTimer = null;
function scheduleReload(delay = 400) {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(function tryReload() {
    const active = document.activeElement;
    if (active && jobListEl.contains(active) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(active.tagName)) {
      reloadTimer = setTimeout(tryReload, 5000);
      return;
    }
    loadJobs();
  }, delay);
}

function connectEvents() {
  let source;
  try {
    source = new EventSource(`${BACKEND}/events`);
  } catch {
    return; // backend not running; jobs tab will show its own error
  }

  source.onopen = () => {
    if (_eventsEverOpened) loadJobs(); // reconnect — reload to catch up on missed events
    _eventsEverOpened = true;
  };

  // New job scored → reload the jobs list so it appears
  source.addEventListener('job_processed', () => {
    loadJobs();
  });

  // Cover letter ready → update the badge and, if expanded, the editor in place.
  // A full reload is the simplest way to move the card between the "pending"
  // and "ready" badge states, and jobs lists are small enough that it's cheap.
  source.addEventListener('cover_letter_ready', () => scheduleReload());

  // A cover-letter run started, is waiting on a question, finished or failed: move the
  // card between "Writing…", "Needs your answer" and "Ready".
  for (const name of ['letter_run_started', 'letter_run_waiting', 'letter_run_done', 'letter_run_failed']) {
    source.addEventListener(name, () => scheduleReload());
  }

  // The idle loop hit the daily/total LLM budget cap and paused cover letters.
  source.addEventListener('llm_budget_blocked', () => {
    refreshBudgetBanner();
  });

  source.onerror = () => {
    // EventSource auto-reconnects; onopen will fire again and trigger a reload
  };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
const envPill = document.getElementById('env-pill');

function renderEnvPill() {
  envPill.textContent = BACKEND_ENV.toUpperCase();
  document.body.classList.toggle('env-test', BACKEND_ENV === 'test');
}

// Flipping the switch swaps the whole data set (a different database), so
// reload the panel rather than patch state in place — nothing from the old
// environment can linger on screen.
envPill.addEventListener('click', async () => {
  const next = BACKEND_ENV === 'real' ? 'test' : 'real';
  await chrome.storage.local.set({ backendEnv: next });
  location.reload();
});

// ---------------------------------------------------------------------------
// Zoom + width classes
// ---------------------------------------------------------------------------
// CSS `zoom` doesn't change the viewport width, so the layout breakpoints
// can't be @media queries — they're body classes computed from the width the
// content actually has once zoom is applied (window.innerWidth / currentZoom).
const ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.5];
let currentZoom = 1;
const zoomInBtn = document.getElementById('zoom-in');
const zoomOutBtn = document.getElementById('zoom-out');

function updateWidthClasses() {
  const w = window.innerWidth / currentZoom;
  document.body.classList.toggle('w-wide', w >= 640);
  document.body.classList.toggle('w-mid', w >= 420);
}

function applyZoom(z) {
  currentZoom = z;
  document.documentElement.style.zoom = z;
  const idx = ZOOM_STEPS.indexOf(z);
  zoomOutBtn.disabled = idx <= 0;
  zoomInBtn.disabled = idx === ZOOM_STEPS.length - 1;
  const pct = Math.round(z * 100);
  zoomOutBtn.title = `Smaller (now ${pct}%)`;
  zoomInBtn.title = `Larger (now ${pct}%)`;
  updateWidthClasses();
}

function stepZoom(dir) {
  const idx = ZOOM_STEPS.indexOf(currentZoom);
  const next = Math.min(ZOOM_STEPS.length - 1, Math.max(0, idx + dir));
  if (next === idx) return;
  applyZoom(ZOOM_STEPS[next]);
  try {
    chrome.storage.local.set({ sidebarZoom: ZOOM_STEPS[next] });
  } catch (e) { /* zoom just won't persist */ }
}

zoomOutBtn.addEventListener('click', () => stepZoom(-1));
zoomInBtn.addEventListener('click', () => stepZoom(1));
window.addEventListener('resize', updateWidthClasses);

// Apply the saved zoom straight away — deliberately not inside backendReady,
// so it doesn't wait on (or depend on) the local backend being up.
applyZoom(1);
(async () => {
  try {
    const { sidebarZoom } = await chrome.storage.local.get('sidebarZoom');
    if (ZOOM_STEPS.includes(sidebarZoom)) applyZoom(sidebarZoom);
  } catch (e) { /* fall back to 1 */ }
})();

backendReady.then(() => {
  renderEnvPill();
  loadPreferences().then(loadJobs);
  refreshBudgetBanner();
  connectEvents();
});
