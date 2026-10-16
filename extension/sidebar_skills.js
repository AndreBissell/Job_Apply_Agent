// Pipeline tab, below the job list (docs/pipeline-tab-plan.md D10/D11): "Skills ads ask for
// that you don't list" (GET /gaps/market, code only, no model call) with I have it / Not yet /
// ignore, and the To work on list (moved here from the Profile tab).
// Classic script: sidebar.html loads it after sidebar_jobs.js and before sidebar.js; all share
// one global scope.
// ---------------------------------------------------------------------------
// Skills ads ask for
// ---------------------------------------------------------------------------
const SKILLS_SHOWN = 8; // the rest behind "Show all N"
const skListEl = document.getElementById('pl-skills-list');
const skNoteEl = document.getElementById('pl-skills-note');
const skCountEl = document.getElementById('pl-skills-count');
const skMoreBtn = document.getElementById('pl-skills-more');
let skShowAll = false;
let skData = null;

async function loadSkillGaps() {
  if (typingIn(document.getElementById('pl-skills'))) return;
  try {
    const res = await fetch(`${BACKEND}/gaps/market?profile_id=${PROFILE_ID}`);
    if (!res.ok) throw new Error();
    skData = await res.json();
  } catch {
    skData = null;
    skListEl.innerHTML = '';
    skCountEl.textContent = '';
    skMoreBtn.hidden = true;
    skNoteEl.textContent = 'Could not load this list — is the backend running?';
    return;
  }
  renderSkillGaps();
}

function renderSkillGaps() {
  const { items, ads, window_days: days } = skData;
  skListEl.innerHTML = '';
  skCountEl.textContent = items.length ? `${items.length} skill${items.length === 1 ? '' : 's'}` : '';
  if (!ads) {
    skNoteEl.textContent = 'Skills appear here once a few scanned jobs are scored.';
  } else if (!items.length) {
    skNoteEl.textContent = `Every skill the ${ads} ads scored in the last ${days} days ask for is in your profile.`;
  } else {
    skNoteEl.textContent = `From the ${ads} ads scored in the last ${days} days. "I have it" adds the skill to your `
      + 'profile; "Not yet" puts it on To work on; ✕ hides it from this list.';
  }
  const shown = skShowAll ? items : items.slice(0, SKILLS_SHOWN);
  for (const item of shown) skListEl.appendChild(renderSkillRow(item, ads));
  skMoreBtn.hidden = items.length <= SKILLS_SHOWN;
  skMoreBtn.textContent = skShowAll ? 'Show fewer' : `Show all ${items.length} ›`;
}

skMoreBtn.addEventListener('click', () => {
  skShowAll = !skShowAll;
  renderSkillGaps();
});

function renderSkillRow(item, ads) {
  const li = mk('li', 'sk-row');
  li.dataset.key = item.key;
  const main = mk('div', 'sk-main');
  main.appendChild(mk('strong', null, item.skill));
  main.appendChild(mk('span', 'sk-meta', `asked in ${item.ads} of ${ads} ads · avg score ${item.avg_score}`));
  li.appendChild(main);

  const actions = mk('div', 'sk-actions');
  const have = mk('button', 'btn btn-sm', 'I have it');
  have.type = 'button';
  have.title = `Add ${item.skill} to your profile's skills`;
  const notYet = mk('button', 'btn btn-sm', 'Not yet');
  notYet.type = 'button';
  notYet.title = 'Put it on your To work on list';
  const ignore = mk('button', 'sk-ignore', '✕');
  ignore.type = 'button';
  ignore.title = 'Hide it from this list (not a skill you care about)';
  ignore.setAttribute('aria-label', `Hide ${item.skill} from this list`);
  actions.append(have, notYet, ignore);
  li.appendChild(actions);

  const answer = async (path, done) => {
    for (const b of [have, notYet, ignore]) b.disabled = true;
    try {
      const res = await fetch(`${BACKEND}/gaps/market/${path}?profile_id=${PROFILE_ID}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill: item.skill }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
      skData.items = skData.items.filter(i => i.key !== item.key);
      done();
    } catch (err) {
      for (const b of [have, notYet, ignore]) b.disabled = false;
      main.querySelector('.sk-meta').textContent = `Didn't save: ${err.message || err}`;
    }
  };

  // The row stays where it is with a one-line result (nothing jumps), until the next load.
  const settle = (text, muted, undo) => {
    li.innerHTML = '';
    const line = mk('span', 'sk-done' + (muted ? ' muted' : ''), text);
    if (undo) line.appendChild(undo);
    li.appendChild(line);
    skCountEl.textContent = skData.items.length ? `${skData.items.length} skill${skData.items.length === 1 ? '' : 's'}` : '';
  };

  have.addEventListener('click', () => answer('have', () => {
    profileLoaded = false; // the Profile editor reloads its skills next time it opens
    settle(`✓ ${item.skill} added to your profile. Add where you used it in Profile.`);
  }));
  notYet.addEventListener('click', () => answer('not-yet', () => {
    settle(`${item.skill} is on To work on below.`, true);
    loadToWorkOn();
  }));
  ignore.addEventListener('click', () => answer('ignore', () => {
    const undo = mk('button', 'pl-link', 'Undo');
    undo.type = 'button';
    undo.addEventListener('click', async () => {
      undo.disabled = true;
      try {
        const res = await fetch(`${BACKEND}/gaps/market/unignore?profile_id=${PROFILE_ID}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill: item.skill }),
        });
        if (!res.ok) throw new Error();
        loadSkillGaps();
      } catch {
        undo.disabled = false;
        undo.textContent = 'Undo failed — try again';
      }
    });
    settle(`${item.skill} hidden.`, true, undo);
  }));
  return li;
}

// ---------------------------------------------------------------------------
// To work on (plan §5.9): skills you said no to, ranked by how often ads ask for them
// ---------------------------------------------------------------------------
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
      'Nothing yet. "Not yet" on a skill above, or a No to a cover letter\'s question, puts it here.'));
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
      sub.push(`You said not yet on ${new Date(it.said_no_at).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })}`);
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
        loadSkillGaps(); // a cleared skill can show up above again
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

document.getElementById('towork-refresh').addEventListener('click', () => { loadToWorkOn(); loadSkillGaps(); });
