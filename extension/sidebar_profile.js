// Profile tab: the profile editor (cards, skills, writing sample, save, dirty state) and
// the To work on list. Classic script: sidebar.html loads it after sidebar_personalise.js
// and before sidebar.js; all share one global scope (loadProfile writes profileLoaded).
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
