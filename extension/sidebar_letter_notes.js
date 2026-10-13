// Notes under an expanded card: Quick Apply question help, letter notes, side outputs.
// Classic script: sidebar.html loads it after sidebar_jobs.js and before sidebar.js;
// all of them share one global scope, so load order is the contract.
// ---------------------------------------------------------------------------
// Quick Apply questions the user has already opened for this job (Phase 9b): the same
// help the overlay on the apply page shows, from the question bank. Adds nothing when
// the job has no captured questions or the request fails.
// ---------------------------------------------------------------------------
let screeningCssInjected = false;
function injectScreeningAssistCss() {
  if (screeningCssInjected) return;
  screeningCssInjected = true;
  const style = document.createElement('style');
  style.textContent = SCREENING_ASSIST_CSS;
  document.head.appendChild(style);
}

async function fetchScreeningAssist(jobId) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${jobId}/screening-assist?profile_id=${PROFILE_ID}`);
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data.questions) && data.questions.length ? data : null;
  } catch {
    return null;
  }
}

async function renderQuickApplyQuestions(detailEl, jobId) {
  const data = await fetchScreeningAssist(jobId);
  if (!data) return;
  injectScreeningAssistCss();
  const details = mk('details', 'letter-notes');
  details.addEventListener('click', e => e.stopPropagation()); // don't collapse the card
  details.appendChild(mk('summary', null, `Quick Apply questions (${data.questions.length})`));
  const body = mk('div');
  details.appendChild(body);
  detailEl.appendChild(details);

  async function refresh(notice) {
    const next = await fetchScreeningAssist(jobId);
    if (next) draw(next, notice);
  }
  function draw(d, notice) {
    renderScreeningAssist(body, d, {
      fetchJson: (url, init) => fetch(url, init), backend: BACKEND, profileId: PROFILE_ID,
      onChanged: refresh, notice,
    });
  }
  draw(data);
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
  if (run?.side_outputs) renderSideOutputs(wrap, run.side_outputs);
  if (wrap.childNodes.length) container.appendChild(wrap);
}

// ---------------------------------------------------------------------------
// Side outputs (plan §5.6): screening answers, learning suggestions, résumé notes.
// Each is a collapsed section beside the letter; a failed one says so.
// ---------------------------------------------------------------------------
function sideSection(title) {
  const d = mk('details', 'letter-notes side-output');
  d.appendChild(mk('summary', null, title));
  return d;
}

function copyButton(getText) {
  const btn = mk('button', 'btn btn-sm', 'Copy');
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(getText());
      btn.textContent = 'Copied ✓';
    } catch {
      btn.textContent = 'Copy failed';
    }
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  });
  return btn;
}

function renderSideOutputs(wrap, so) {
  const failed = (tool) => so.errors?.[tool]
    ? mk('div', 'letter-flag', `This section couldn't be made on this run (${truncate(so.errors[tool], 120)}).`)
    : null;

  const answers = so.screening_answers || [];
  if (answers.length || so.errors?.answer_screening) {
    const d = sideSection(`Screening answers (${answers.length})`);
    const err = failed('answer_screening');
    if (err) d.appendChild(err);
    if (answers.length) d.appendChild(mk('div', 'pref-note', 'Drafts from your profile, for the questions in the ad. Read each one before you paste it.'));
    for (const a of answers) {
      const box = mk('div', 'screening-answer');
      box.appendChild(mk('h5', null, a.question));
      if (a.answer) {
        const text = mk('div', 'answer-text', a.answer);
        box.appendChild(text);
        box.appendChild(copyButton(() => a.answer));
      } else {
        box.appendChild(mk('div', 'warn', 'Your profile doesn’t answer this: answer it yourself.'));
      }
      if (a.covered === 'partly') box.appendChild(mk('div', 'warn', 'Only partly covered by your profile.'));
      if (a.note) box.appendChild(mk('div', 'why', a.note));
      for (const issue of a.issues || []) box.appendChild(mk('div', 'note-row warn', `Check: ${issue}`));
      d.appendChild(box);
    }
    wrap.appendChild(d);
  }

  const learning = so.learning_suggestions || [];
  if (learning.length || so.errors?.suggest_learning) {
    const d = sideSection(`Ways to close your gaps (${learning.length})`);
    const err = failed('suggest_learning');
    if (err) d.appendChild(err);
    for (const s of learning) {
      const row = mk('div', 'note-row');
      row.appendChild(mk('strong', null, s.skill));
      row.appendChild(document.createTextNode(`: ${s.suggestion}`));
      const meta = [s.kind, s.effort, s.recent_ads ? `asked for in ${s.recent_ads} recent ad${s.recent_ads === 1 ? '' : 's'}` : null]
        .filter(Boolean).join(' · ');
      if (meta) row.appendChild(mk('span', 'why', ` (${meta})`));
      d.appendChild(row);
    }
    wrap.appendChild(d);
  }

  const notes = so.resume_notes;
  if (notes || so.errors?.suggest_resume_tweaks) {
    const d = sideSection('Résumé notes for this ad');
    const err = failed('suggest_resume_tweaks');
    if (err) d.appendChild(err);
    if (notes) {
      d.appendChild(mk('div', 'pref-note', notes.based_on === 'cv'
        ? 'Advice on your stored CV. Nothing is changed for you.'
        : 'No CV is stored, so this is advice on your profile entries. Nothing is changed for you.'));
      const groups = [
        ['Lead with', (notes.lead_with || []).map(i => [i.point, i.why])],
        ['Use the ad’s words (where true)', (notes.keywords_to_mirror || []).map(i => [i.keyword, null])],
        ['Consider cutting', (notes.consider_cutting || []).map(i => [i.item, i.why])],
        ['Gaps: how to handle them honestly', (notes.gaps_to_address || []).map(i => [i.requirement, i.advice])],
      ];
      for (const [heading, rows] of groups) {
        if (!rows.length) continue;
        d.appendChild(mk('h5', null, heading));
        for (const [text, why] of rows) {
          const row = mk('div', 'note-row', text);
          if (why) row.appendChild(mk('span', 'why', ` — ${why}`));
          d.appendChild(row);
        }
      }
    }
    wrap.appendChild(d);
  }
}
