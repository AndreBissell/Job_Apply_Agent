// Quick Apply question help (plan §10.1, Phase 9b): one renderer shared by the
// questions overlay on the Seek apply page (content_script.js, inside its shadow
// root) and the side panel's job card (sidebar.js).
//
//   renderScreeningAssist(container, data, opts)
//     data  the body of GET /jobs/{id}/screening-assist
//     opts  { fetchJson(url, init) -> Promise<{ok, status, json()}>,   backendFetch or window.fetch
//             backend, profileId,
//             onChanged(notice?)   re-fetch the assist data and render again,
//             notice?              one line shown at the top (what the last action did) }
//
// Plain DOM and textContent only: nothing from the server is ever parsed as HTML.
// Honour system (user decision): the panel shows what the job wants beside what the
// profile has and never recommends, pre-selects, ticks or highlights an answer. There
// are no checkboxes, radios or option inputs here; the only inputs are the text boxes
// of a gap's "Yes" flow, and nothing in this file touches Seek's own page. A 9c draft
// answer (open-ended questions, on the user's click) is shown as text with Copy only.

const SA_KIND_LABELS = {
  user: 'Yours to answer',
  assisted: 'We’ll help',
  unknown: 'New — not sorted yet',
};
const SA_IMPORTANCE = { essential: 'Essential', important: 'Important', nice_to_have: 'Nice to have' };
const SA_BASIS = { work: 'work', project: 'project', study: 'study', listed: 'listed skill' };
const SA_OPTION_TAG = {
  wanted_have: 'Wanted · you have it',
  wanted_missing: 'Wanted · not in your profile',
  have: 'You have it',
};
const SA_EXP_TYPES = ['job', 'internship', 'university_project', 'assignment', 'personal_project', 'volunteer'];
const SA_QUAL_TYPES = ['degree', 'diploma', 'certificate', 'license'];
const SA_QUAL_STATUSES = ['completed', 'in_progress', 'expected'];

const SCREENING_ASSIST_CSS = `
  .sa-root { font-size: 12px; line-height: 1.4; color: #1c2330; text-align: left; }
  .sa-root * { box-sizing: border-box; }
  .sa-summary { font-size: 11px; color: #4b5563; margin-bottom: 6px; }
  .sa-msg { margin: 0 0 8px; padding: 6px 8px; border-radius: 6px; font-size: 11px;
    background: #eff6ff; border: 1px solid #bfdbfe; color: #1e3a8a; }
  .sa-notice { margin: 0 0 8px; padding: 6px 8px; border-radius: 6px; font-size: 11px;
    background: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46; }
  .sa-btn { font: inherit; font-size: 11px; padding: 3px 8px; border: 1px solid #cbd5e1; border-radius: 6px;
    background: #fff; color: #1c2330; cursor: pointer; }
  .sa-btn:hover:not(:disabled) { background: #f3f4f6; }
  .sa-btn:disabled { opacity: .5; cursor: default; }
  .sa-btn.primary { background: #2557a7; border-color: #2557a7; color: #fff; }
  .sa-btn.primary:hover:not(:disabled) { background: #1d4684; }
  .sa-q { margin: 0 0 10px; padding: 0 0 10px; border-bottom: 1px solid #e5e7eb; }
  .sa-q:last-of-type { border-bottom: none; }
  .sa-qhead { display: flex; gap: 5px; }
  .sa-num { color: #6b7280; }
  .sa-qtext { font-size: 12px; line-height: 1.35; font-weight: 600; }
  .sa-badge { display: inline-block; margin-top: 3px; font-size: 10px; padding: 1px 6px;
    border-radius: 9px; border: 1px solid; }
  .sa-badge.user { color: #374151; border-color: #9ca3af; background: #f3f4f6; }
  .sa-badge.assisted { color: #065f46; border-color: #10b981; background: #ecfdf5; }
  .sa-badge.unknown { color: #92400e; border-color: #f59e0b; background: #fffbeb; }
  .sa-block { margin-top: 6px; padding: 6px 8px; border-radius: 6px; border: 1px solid #e5e7eb; background: #f9fafb; }
  .sa-block.wants { background: #fffdf5; border-color: #fde9b0; }
  .sa-block.has { background: #f6faff; border-color: #cfe0f7; }
  .sa-label { margin: 0 0 3px; font-size: 10px; font-weight: 700; letter-spacing: .05em;
    text-transform: uppercase; color: #6b7280; }
  .sa-row { margin-top: 3px; }
  .sa-quote { color: #1c2330; }
  .sa-chip { display: inline-block; margin-right: 5px; padding: 0 6px; border-radius: 8px; font-size: 10px;
    font-weight: 600; border: 1px solid #d1d5db; background: #fff; color: #4b5563; white-space: nowrap; }
  .sa-chip.essential { border-color: #f59e0b; color: #92400e; background: #fffbeb; }
  .sa-chip.important { border-color: #93c5fd; color: #1e40af; background: #eff6ff; }
  .sa-muted { color: #6b7280; font-size: 11px; }
  .sa-info { margin-top: 4px; font-size: 11px; color: #374151; }
  .sa-opts { margin-top: 6px; }
  .sa-opt { display: flex; justify-content: space-between; gap: 8px; padding: 2px 0;
    border-bottom: 1px dotted #e5e7eb; }
  .sa-opt .sa-tag { font-size: 10px; color: #4b5563; text-align: right; }
  .sa-gaps { margin-top: 6px; }
  .sa-remembered { margin-top: 4px; font-size: 11px; color: #6b7280; }
  .sa-link { padding: 0; border: 0; background: none; font: inherit; font-size: 11px;
    color: #2557a7; text-decoration: underline; cursor: pointer; }
  .sa-link:disabled { color: #9ca3af; cursor: default; }
  .sa-gap { margin-top: 6px; padding: 6px 8px; border: 1px solid #fcd34d; background: #fffbeb; border-radius: 6px; }
  .sa-gap .sa-ask { font-size: 12px; }
  .sa-gap .sa-note { margin-top: 4px; font-size: 10px; color: #78350f; }
  .sa-actions { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
  .sa-err { margin-top: 4px; font-size: 11px; color: #b91c1c; }
  .sa-root textarea, .sa-root input[type=text], .sa-root select {
    display: block; width: 100%; margin-top: 3px; padding: 4px 6px; border: 1px solid #d1d5db;
    border-radius: 5px; font: inherit; font-size: 12px; color: #1c2330; background: #fff; }
  .sa-root textarea { min-height: 52px; resize: vertical; }
  .sa-proposal { margin-top: 4px; padding: 6px 8px; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; }
  .sa-proposal h5 { margin: 6px 0 2px; font-size: 10px; color: #6b7280; text-transform: uppercase; letter-spacing: .04em; }
  .sa-proposal h5:first-child { margin-top: 0; }
  .sa-proposal label { display: block; margin-top: 4px; font-size: 11px; color: #6b7280; }
  .sa-two { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .sa-foot { margin-top: 4px; font-size: 10px; color: #6b7280; }
  .sa-draft { margin-top: 6px; padding: 6px 8px; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; }
  .sa-answer { margin-top: 3px; padding: 6px 8px; border-radius: 5px; background: #f9fafb; border: 1px solid #e5e7eb;
    white-space: pre-wrap; font-size: 12px; color: #1c2330; }
  .sa-ok { margin-top: 4px; font-size: 11px; color: #065f46; }
  .sa-warn { margin-top: 4px; padding: 5px 7px; border-radius: 5px; font-size: 11px;
    background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; }
  .sa-warn ul { margin: 2px 0 0; padding-left: 16px; }
  .sa-stale { margin-top: 4px; padding: 5px 7px; border-radius: 5px; font-size: 11px;
    background: #fffbeb; border: 1px solid #fcd34d; color: #78350f; }
`;

function saEl(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text != null) e.textContent = text;
  return e;
}

function saSplitList(text) {
  return text.split(',').map((t) => t.trim()).filter(Boolean);
}

// POST/GET through the host's fetch. Resolves {ok, status, data}; never throws.
async function saRequest(opts, url, body) {
  try {
    const init = body === undefined ? { method: 'GET' } : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    const res = await opts.fetchJson(url, init);
    let data = null;
    try { data = await res.json(); } catch { /* no body */ }
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: { detail: 'Could not reach the backend — is it running?' } };
  }
}

function saErrorText(r) {
  return (r && typeof r.data?.detail === 'string' && r.data.detail)
    || 'Something went wrong — check the fields and try again.';
}

function saLabelled(text, control) {
  const l = saEl('label', null, text);
  l.appendChild(control);
  return l;
}

function saTextInput(value, placeholder) {
  const i = saEl('input');
  i.type = 'text';
  i.value = value || '';
  if (placeholder) i.placeholder = placeholder;
  return i;
}

function saSelect(options, value) {
  const sel = saEl('select');
  for (const o of options) {
    const opt = saEl('option', null, o.replace(/_/g, ' '));
    opt.value = o;
    sel.appendChild(opt);
  }
  sel.value = options.includes(value) ? value : options[0];
  return sel;
}

// The rows a Yes would add, as editable fields. read() returns the ProposedRows the
// confirm endpoint takes; fields the editor doesn't show are carried over untouched.
function saProposalEditor(proposal) {
  const root = saEl('div', 'sa-proposal');
  const exps = [];
  const quals = [];

  for (const e of proposal.experiences || []) {
    root.appendChild(saEl('h5', null, 'Experience'));
    const type = saSelect(SA_EXP_TYPES, e.experience_type);
    const title = saTextInput(e.title);
    const org = saTextInput(e.organization, 'employer, university or client');
    const start = saTextInput(e.start, 'YYYY-MM');
    const end = saTextInput(e.end, 'YYYY-MM');
    const desc = saEl('textarea');
    desc.value = e.description || '';
    const skills = saTextInput((e.skills || []).join(', '), 'skills used, comma-separated');
    const two = saEl('div', 'sa-two');
    two.append(saLabelled('Start', start), saLabelled('End', end));
    root.append(saLabelled('Type', type), saLabelled('Title', title), saLabelled('Organisation', org),
      two, saLabelled('What you did', desc), saLabelled('Skills', skills));
    exps.push(() => ({
      experience_type: type.value, title: title.value.trim(), organization: org.value.trim(),
      start: start.value.trim(), end: end.value.trim(), description: desc.value.trim(),
      skills: saSplitList(skills.value),
    }));
  }
  for (const q of proposal.qualifications || []) {
    root.appendChild(saEl('h5', null, 'Qualification'));
    const type = saSelect(SA_QUAL_TYPES, q.qualification_type);
    const title = saTextInput(q.title);
    const inst = saTextInput(q.institution, 'institution');
    const status = saSelect(SA_QUAL_STATUSES, q.status);
    root.append(saLabelled('Type', type), saLabelled('Title', title), saLabelled('Institution', inst),
      saLabelled('Status', status));
    quals.push(() => ({
      qualification_type: type.value, title: title.value.trim(), institution: inst.value.trim(),
      status: status.value,
    }));
  }
  let skillsInput = null;
  if ((proposal.skills || []).length) {
    root.appendChild(saEl('h5', null, 'Skills'));
    skillsInput = saTextInput(proposal.skills.join(', '), 'comma-separated');
    root.appendChild(skillsInput);
  }
  return {
    node: root,
    read: () => ({
      experiences: exps.map((r) => r()),
      qualifications: quals.map((r) => r()),
      skills: skillsInput ? saSplitList(skillsInput.value) : [],
    }),
  };
}

// A remembered "no", with a way back: "Change my answer" takes it off the to-work-on
// list (POST /gaps/{id}/clear, history kept) and the Yes/No card comes back.
function saRememberedLine(g, opts) {
  const line = saEl('div', 'sa-remembered', `You said you don't have ${g.skill}. `);
  if (g.gap_id == null) return line;
  const undo = saEl('button', 'sa-link', 'Change my answer');
  undo.type = 'button';
  const err = saEl('div', 'sa-err');
  line.append(undo, err);
  undo.addEventListener('click', async () => {
    undo.disabled = true;
    const r = await saRequest(opts,
      `${opts.backend}/gaps/${g.gap_id}/clear?profile_id=${encodeURIComponent(opts.profileId)}`, {});
    if (r.ok) {
      return opts.onChanged && opts.onChanged(`Your "no" for ${g.skill} is undone. Answer again below.`);
    }
    err.textContent = saErrorText(r);
    undo.disabled = false;
  });
  return line;
}

// A wanted-but-missing skill: remembered "no" (a muted line you can undo) or a small
// Yes/No card. A Yes only offers to add the skill to the profile; it never changes the
// cover letter.
function saGapCard(g, jobId, opts) {
  if (g.remembered) return saRememberedLine(g, opts);

  const card = saEl('div', 'sa-gap');
  const importance = g.importance ? ` (${(SA_IMPORTANCE[g.importance] || g.importance).toLowerCase()})` : '';
  card.appendChild(saEl('div', 'sa-ask', `${g.skill} — wanted${importance}. Do you have it?`));
  const stage = saEl('div');
  card.appendChild(stage);
  card.appendChild(saEl('div', 'sa-note',
    'A Yes only adds it to your profile. It does not change your cover letter.'));

  const base = `${opts.backend}/jobs/${jobId}/screening-gaps`;
  const q = `profile_id=${encodeURIComponent(opts.profileId)}`;
  const common = { skill: g.skill };
  if (g.requirement_text) common.requirement_text = g.requirement_text;
  if (g.importance) common.importance = g.importance;

  function choose() {
    stage.textContent = '';
    const err = saEl('div', 'sa-err');
    const actions = saEl('div', 'sa-actions');
    const no = saEl('button', 'sa-btn', 'No');
    const yes = saEl('button', 'sa-btn primary', 'Yes');
    no.type = yes.type = 'button';
    actions.append(no, yes);
    stage.append(actions, err);

    no.addEventListener('click', async () => {
      no.disabled = yes.disabled = true;
      const r = await saRequest(opts, `${base}?${q}`, { ...common, choice: 'no' });
      if (r.ok) return opts.onChanged && opts.onChanged();
      err.textContent = saErrorText(r);
      no.disabled = yes.disabled = false;
    });
    yes.addEventListener('click', () => describe());
  }

  function describe() {
    stage.textContent = '';
    const err = saEl('div', 'sa-err');
    const text = saEl('textarea');
    text.placeholder = 'Where did you use it? (optional)';
    const actions = saEl('div', 'sa-actions');
    const go = saEl('button', 'sa-btn primary', 'Continue');
    const back = saEl('button', 'sa-btn', 'Back');
    go.type = back.type = 'button';
    actions.append(go, back);
    stage.append(text, actions, err);
    back.addEventListener('click', choose);
    go.addEventListener('click', async () => {
      err.textContent = '';
      go.disabled = back.disabled = true;
      const typed = text.value.trim();
      go.textContent = typed ? 'Reading your answer…' : 'Continue';
      const r = await saRequest(opts, `${base}?${q}`, { ...common, choice: 'yes', ...(typed ? { text: typed } : {}) });
      if (r.ok && r.data?.proposal) return confirm(r.data.proposal);
      err.textContent = saErrorText(r);
      go.disabled = back.disabled = false;
      go.textContent = 'Continue';
    });
  }

  function confirm(proposal) {
    stage.textContent = '';
    stage.appendChild(saEl('div', 'sa-muted',
      'Here is what we would add to your profile. Fix anything that is wrong, then add it.'));
    const editor = saProposalEditor(proposal);
    const err = saEl('div', 'sa-err');
    const actions = saEl('div', 'sa-actions');
    const ok = saEl('button', 'sa-btn primary', 'Add to my profile');
    const cancel = saEl('button', 'sa-btn', 'Cancel');
    ok.type = cancel.type = 'button';
    actions.append(ok, cancel);
    stage.append(editor.node, actions, err);
    cancel.addEventListener('click', choose);
    ok.addEventListener('click', async () => {
      ok.disabled = cancel.disabled = true;
      ok.textContent = 'Saving…';
      const r = await saRequest(opts, `${base}/confirm?${q}`, { skill: g.skill, rows: editor.read() });
      if (r.ok) {
        return opts.onChanged && opts.onChanged(
          `Added ${g.skill} to your profile. Your cover letter is unchanged.`);
      }
      err.textContent = saErrorText(r);
      ok.disabled = cancel.disabled = false;
      ok.textContent = 'Add to my profile';
    });
  }

  choose();
  return card;
}

// Copy into the user's clipboard. Our own text only; nothing is typed into Seek's page.
async function saCopy(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const SA_COVERED = { yes: 'Answers the question', partly: 'Answers part of the question', no: 'Not answered' };

// Phase 9c: a draft answer to an open-ended question, made only when the user clicks.
// Shown with Copy and nothing else: the user pastes it themselves. A draft with issues
// is never marked as checked; a stale one says why and offers a redraft.
function saDraftBlock(q, jobId, opts) {
  const box = saEl('div', 'sa-draft');
  box.appendChild(saEl('div', 'sa-label', 'Draft answer'));
  const d = q.draft;
  const err = saEl('div', 'sa-err');
  const actions = saEl('div', 'sa-actions');
  const stale = !!(d && d.stale && d.stale.length);

  if (d) {
    for (const reason of d.stale || []) box.appendChild(saEl('div', 'sa-stale', reason));
    if (d.answer) {
      box.appendChild(saEl('div', 'sa-answer', d.answer));
      if (d.covered && SA_COVERED[d.covered]) box.appendChild(saEl('div', 'sa-muted', SA_COVERED[d.covered]));
      if (d.issues && d.issues.length) {
        const warn = saEl('div', 'sa-warn', 'Check this before you use it:');
        const ul = saEl('ul');
        for (const i of d.issues) ul.appendChild(saEl('li', null, i));
        warn.appendChild(ul);
        box.appendChild(warn);
      } else if (d.verified && !stale) {
        box.appendChild(saEl('div', 'sa-ok', '✓ Every statement is tied to your profile.'));
      }
      const copy = saEl('button', 'sa-btn', 'Copy');
      copy.type = 'button';
      copy.addEventListener('click', async () => {
        copy.textContent = (await saCopy(d.answer)) ? 'Copied ✓' : 'Copy failed — select the text instead';
        setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
      });
      actions.appendChild(copy);
    } else {
      box.appendChild(saEl('div', 'sa-muted', 'No draft: your profile doesn’t answer this one.'));
    }
    if (d.note) box.appendChild(saEl('div', 'sa-info', d.note));
  } else {
    box.appendChild(saEl('div', 'sa-muted',
      'Ask for a draft built only from your profile (one model call, about 1¢).'));
  }

  const go = saEl('button', d ? 'sa-btn' : 'sa-btn primary', d ? 'Redraft' : 'Draft an answer');
  go.type = 'button';
  actions.appendChild(go);
  box.append(actions, err);
  go.addEventListener('click', async () => {
    err.textContent = '';
    go.disabled = true;
    go.textContent = 'Drafting…';
    const r = await saRequest(opts,
      `${opts.backend}/jobs/${jobId}/screening-drafts?profile_id=${encodeURIComponent(opts.profileId)}`,
      { bank_id: q.bank_id });
    if (r.ok) return opts.onChanged && opts.onChanged('Drafted an answer. Read it before you paste it.');
    err.textContent = saErrorText(r);
    go.disabled = false;
    go.textContent = d ? 'Redraft' : 'Draft an answer';
  });
  return box;
}

function saEvidenceRow(item) {
  const row = saEl('div', 'sa-row');
  row.appendChild(saEl('span', 'sa-chip', SA_BASIS[item.basis] || item.basis));
  row.appendChild(saEl('span', null, item.text));
  return row;
}

function saAssistBlocks(q, a, jobId, data, opts) {
  const out = [];

  // What the job wants: the ad's own words.
  const wants = saEl('div', 'sa-block wants');
  wants.appendChild(saEl('div', 'sa-label', 'What the job wants'));
  if (a.wanted.length) {
    for (const w of a.wanted) {
      const row = saEl('div', 'sa-row');
      row.appendChild(saEl('span', `sa-chip ${w.importance}`, SA_IMPORTANCE[w.importance] || w.importance));
      row.appendChild(saEl('span', 'sa-quote', `“${w.text}”`));
      wants.appendChild(row);
    }
  } else {
    wants.appendChild(saEl('div', 'sa-muted', 'Nothing in the ad’s requirements matches this question.'));
  }
  if (a.wanted_figure) wants.appendChild(saEl('div', 'sa-info', `The ad says: ${a.wanted_figure}`));
  out.push(wants);

  // What the profile has. Information only; never a recommended answer.
  const has = saEl('div', 'sa-block has');
  has.appendChild(saEl('div', 'sa-label', 'What your profile has'));
  if (a.have.summary) has.appendChild(saEl('div', null, a.have.summary));
  for (const item of a.have.evidence || []) has.appendChild(saEvidenceRow(item));
  if (a.have.years?.undated?.length) {
    has.appendChild(saEl('div', 'sa-muted',
      `No dates, so not counted in the years: ${a.have.years.undated.join(', ')}`));
  }
  if (a.profile_option) has.appendChild(saEl('div', 'sa-info', `Your dates fall in: ${a.profile_option}`));
  if (a.have.not_counted?.length) {
    has.appendChild(saEl('div', 'sa-muted', 'In your profile but doesn’t count for this question:'));
    for (const item of a.have.not_counted) {
      const row = saEvidenceRow(item);
      row.classList.add('sa-muted');
      has.appendChild(row);
    }
  }
  out.push(has);

  // Multi-select: every option with what the ad and the profile say about it. A plain
  // list: nothing ticked, nothing highlighted.
  if (a.options?.length) {
    const list = saEl('div', 'sa-opts');
    list.appendChild(saEl('div', 'sa-label', 'The options'));
    for (const o of a.options) {
      const row = saEl('div', 'sa-opt');
      row.appendChild(saEl('span', 'sa-optlabel', o.label));
      const tag = SA_OPTION_TAG[o.tag];
      if (tag) {
        const basis = o.have_basis ? ` (${SA_BASIS[o.have_basis] || o.have_basis})` : '';
        row.appendChild(saEl('span', 'sa-tag', tag + basis));
      }
      list.appendChild(row);
    }
    out.push(list);
  }

  if (a.open_ended) {
    out.push(saEl('div', 'sa-info', a.prompt || data.open_prompt || ''));
  }

  if (a.gaps?.length) {
    const box = saEl('div', 'sa-gaps');
    for (const g of a.gaps) box.appendChild(saGapCard(g, jobId, opts));
    out.push(box);
  }
  if (q.draftable) out.push(saDraftBlock(q, jobId, opts));
  return out;
}

function renderScreeningAssist(container, data, opts) {
  const o = opts || {};
  container.textContent = '';
  const root = saEl('div', 'sa-root');
  // Nothing typed or clicked in here may reach whatever hosts it (the overlay's drag and
  // collapse header, the side panel's job card toggle).
  for (const t of ['click', 'pointerdown', 'keydown', 'keyup']) {
    root.addEventListener(t, (e) => e.stopPropagation());
  }
  const full = data.help === 'full';
  const questions = data.questions || [];

  if (o.notice) root.appendChild(saEl('div', 'sa-notice', o.notice));
  if (data.message) {
    const msg = saEl('div', 'sa-msg', data.message);
    if (data.action === 'regenerate') {
      const btn = saEl('button', 'sa-btn primary', 'Create a cover letter');
      btn.type = 'button';
      btn.style.cssText = 'display:block;margin-top:6px;';
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = 'Creating…';
        const r = await saRequest(o, `${o.backend}/jobs/${data.job_id}/regenerate?profile_id=${encodeURIComponent(o.profileId)}`, {});
        btn.textContent = r.ok ? 'Queued — help appears when the letter is ready.' : 'Could not start — try again';
        btn.disabled = r.ok;
      });
      msg.appendChild(btn);
    }
    root.appendChild(msg);
  }

  const counts = { user: 0, assisted: 0, unknown: 0 };
  for (const q of questions) counts[q.kind in counts ? q.kind : 'unknown'] += 1;
  root.appendChild(saEl('div', 'sa-summary', data.help === 'off'
    ? `${questions.length} yours to answer`
    : `${counts.user} yours to answer · ${counts.assisted} we'll help · ${counts.unknown} new`));

  questions.forEach((q, i) => {
    const kind = q.kind in SA_KIND_LABELS ? q.kind : 'unknown';
    const block = saEl('div', 'sa-q');
    block.dataset.kind = kind;
    const head = saEl('div', 'sa-qhead');
    head.append(saEl('span', 'sa-num', `${i + 1}.`), saEl('span', 'sa-qtext', q.text));
    block.appendChild(head);

    let label = SA_KIND_LABELS[kind];
    if (kind === 'user' && q.parameters?.topic) label += ` (${q.parameters.topic.replace(/_/g, ' ')})`;
    if (kind === 'unknown' && full) label = 'Not sorted yet';
    // Help switched off (Personalise): every question is the user's own, with no help.
    const badgeKind = data.help === 'off' ? 'user' : kind;
    if (data.help === 'off' && kind !== 'user') label = 'Yours to answer (help is off)';
    block.appendChild(saEl('span', `sa-badge ${badgeKind}`, label));

    // `user` questions get nothing else: never any help, never sent to a model.
    if (full && kind === 'assisted' && q.assist) {
      for (const node of saAssistBlocks(q, q.assist, data.job_id, data, o)) block.appendChild(node);
    }
    root.appendChild(block);
  });

  root.appendChild(saEl('div', 'sa-foot',
    'Nothing is filled in for you. You choose every answer yourself.'));
  container.appendChild(root);
}
