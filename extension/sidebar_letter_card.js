// The cover-letter card: the ask_user question card, its profile-row editors, and the
// letter editor (Copy / Save / Regenerate). Classic script: sidebar.html loads it after
// sidebar_letter_notes.js and before sidebar.js; all share one global scope.
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
