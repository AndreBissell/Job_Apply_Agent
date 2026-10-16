// Settings (the ⚙ view; its controls keep their old "Personalise" ids): when letters are
// written and how, the extras beside a letter, Quick Apply help, scanning, availability
// checks and AI search suggestions (docs/settings-simplify-plan.md). Every control saves on
// change: there are no Save buttons. Classic script: sidebar.html loads it after
// sidebar_scan.js and before sidebar.js; all share one global scope (it writes autoLetterMin
// and scanMaxPages).
//
// Not shown here on purpose (D3): the letter engine and the per-letter limits (max drafts,
// max tool calls, max spend). They stay preferences with code defaults (app/preferences.py),
// so a value set earlier still applies.
// ---------------------------------------------------------------------------
// Settings — opened by ⚙ (openSettings in sidebar_shared.js)
// ---------------------------------------------------------------------------
const autoLetterInput = document.getElementById('auto-letter-min');
const scanPagesInput = document.getElementById('scan-pages');
const letterQualitySelect = document.getElementById('letter-quality');
const letterQualityHint = document.getElementById('letter-quality-hint');
const llmSuggestRefreshBtn = document.getElementById('llm-suggest-refresh');
const llmSuggestResult = document.getElementById('llm-suggest-result');

// On/off settings: preference key -> its checkbox. A non-bool stored value reads as the
// default, which is on for all of these but the AI suggestions.
const prefToggles = {
  resume_advice_enabled: document.getElementById('side-resume'),
  screening_answers_enabled: document.getElementById('side-screening'),
  learning_suggestions_enabled: document.getElementById('side-learning'),
  screening_question_help_enabled: document.getElementById('qa-question-help'),
  availability_checks_enabled: document.getElementById('availability-checks'),
  llm_search_suggestions: document.getElementById('llm-suggest'),
};
const TOGGLES_OFF_BY_DEFAULT = new Set(['llm_search_suggestions']);

// The full cover-letter pipeline (docs/cover-letter-loop-plan.md §6). Stored server-side
// (profiles.preferences) because the idle loop is what acts on them. Pre-load defaults only.
let letterLoopEnabled = true;
let letterLoopMin = 85;
// The one-pass estimate shown in the hint (GET /jobs/letter-bands, from llm_usage); null = unknown.
let quickLetterUsd = null;
const CHECKED_LETTER_CENTS = 20; // a full run measured 22.8¢ on REAL (11 runs, 2026-10-16)
const LETTER_QUALITY_PRESETS = [85, 90, 95];

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

// "Saved ✓" (or "Not saved") beside the control for a moment.
function flashSaved(control, ok, text) {
  const el = control.closest('.pref-row')?.querySelector('.pref-saved');
  if (!el) return;
  el.textContent = text || (ok ? 'Saved ✓' : 'Not saved');
  el.classList.toggle('err', !ok);
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.textContent = ''; }, 1500);
}

// ---------------------------------------------------------------------------
// "Fact-check & revise" (D1): one dropdown for the master switch and the pipeline bar.
// The backend's bar is max(auto score, pipeline score), so a pipeline score at or below the
// auto score means "every letter"; options that would do nothing are never offered.
// ---------------------------------------------------------------------------
function letterQualityValue() {
  if (!letterLoopEnabled) return 'never';
  return letterLoopMin <= autoLetterMin ? 'all' : String(letterLoopMin);
}

function renderLetterQuality() {
  const current = letterQualityValue();
  const scores = new Set(LETTER_QUALITY_PRESETS.filter(s => s > autoLetterMin));
  if (/^\d+$/.test(current)) scores.add(Number(current)); // a stored non-preset isn't lost
  const options = [['all', 'Every letter'],
    ...[...scores].sort((a, b) => a - b).map(s => [String(s), `Score ${s}+`]),
    ['never', 'Never']];
  letterQualitySelect.innerHTML = '';
  for (const [value, label] of options) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    letterQualitySelect.appendChild(opt);
  }
  letterQualitySelect.value = current;
  renderLetterQualityHint();
}

function renderLetterQualityHint() {
  const quick = quickLetterUsd == null ? 'quick pass' : `quick pass ≈ ${Math.max(1, Math.round(quickLetterUsd * 100))}¢`;
  const checked = `fact-checked ≈ ${CHECKED_LETTER_CENTS}¢`;
  const value = letterQualityValue();
  letterQualityHint.textContent = value === 'never'
    ? `Every letter is a ${quick}, with no checks.`
    : value === 'all'
      ? `Every automatic letter: ${checked}, a few minutes each.`
      : `${autoLetterMin}–${letterLoopMin - 1}: ${quick} · ${letterLoopMin}+: ${checked}`;
}

letterQualitySelect.addEventListener('change', async () => {
  const value = letterQualitySelect.value;
  // "Every letter" stores 0, not the auto score, so it stays true if the auto score moves.
  const body = value === 'never' ? { letter_loop_enabled: false }
    : { letter_loop_enabled: true, letter_loop_min_score: value === 'all' ? 0 : Number(value) };
  letterQualitySelect.disabled = true;
  const ok = await savePref(body);
  if (ok) {
    letterLoopEnabled = body.letter_loop_enabled;
    if ('letter_loop_min_score' in body) letterLoopMin = body.letter_loop_min_score;
  }
  renderLetterQuality(); // on failure this puts the old choice back
  letterQualitySelect.disabled = false;
  flashSaved(letterQualitySelect, ok);
});

// Asks for the one-pass estimate when Settings opens (one cheap query, no model call).
async function refreshLetterEstimate() {
  try {
    const res = await fetch(`${BACKEND}/jobs/letter-bands?profile_id=${PROFILE_ID}`);
    if (!res.ok) return;
    const { est_usd_per_letter: est } = await res.json();
    if (typeof est === 'number') quickLetterUsd = est;
    renderLetterQualityHint();
  } catch { /* backend down — the hint just leaves the figure out */ }
}
settingsBtn.addEventListener('click', () => { if (!settingsSection.hidden) refreshLetterEstimate(); });

// ---------------------------------------------------------------------------
// Number settings: save on change (Enter or leaving the field); out of range snaps back.
// ---------------------------------------------------------------------------
function bindNumberPref(input, key, { min, max, current, apply }) {
  input.addEventListener('change', async () => {
    const value = Number(input.value);
    if (!Number.isInteger(value) || value < min || value > max) {
      input.value = current();
      flashSaved(input, false, `${min}–${max} only`);
      return;
    }
    if (value === current()) return;
    input.disabled = true;
    const ok = await savePref({ [key]: value });
    input.disabled = false;
    if (ok) apply(value);
    else input.value = current();
    flashSaved(input, ok);
  });
}

bindNumberPref(autoLetterInput, 'auto_cover_letter_min_score', {
  min: 0, max: 100, current: () => autoLetterMin,
  apply: (value) => {
    autoLetterMin = value;
    renderLetterQuality(); // the offered bars and the hint move with it
    loadJobs(); // rows move between "pending" and "no letter"
    refreshDashboard(); // the Overview's "no letter yet" bands are anchored on this score
  },
});

bindNumberPref(scanPagesInput, 'scan_max_pages', {
  min: 1, max: SCAN_PAGES_CEILING, current: () => scanMaxPages,
  apply: (value) => { scanMaxPages = value; },
});

// ---------------------------------------------------------------------------
// On/off settings: save on change; put the box back if it didn't stick.
// ---------------------------------------------------------------------------
for (const [key, box] of Object.entries(prefToggles)) {
  box.addEventListener('change', async () => {
    const enabled = box.checked;
    box.disabled = true;
    const ok = await savePref({ [key]: enabled });
    if (!ok) box.checked = !enabled;
    box.disabled = false;
    flashSaved(box, ok);
  });
}

// (i): shows or hides the note under its row.
for (const btn of document.querySelectorAll('#personalise-panel .pref-info')) {
  btn.addEventListener('click', () => {
    const note = document.getElementById(btn.getAttribute('aria-controls'));
    note.hidden = !note.hidden;
    btn.setAttribute('aria-expanded', String(!note.hidden));
  });
}

// Resolves once the stored preferences (if reachable) have been applied, so the
// first loadJobs() can classify "pending" letters against the real threshold.
async function loadPreferences() {
  try {
    const res = await fetch(`${BACKEND}/profile/${PROFILE_ID}/preferences`);
    if (!res.ok) return;
    const prefs = await res.json();
    if (Number.isInteger(prefs.auto_cover_letter_min_score)) autoLetterMin = prefs.auto_cover_letter_min_score;
    autoLetterInput.value = autoLetterMin;
    if (Number.isInteger(prefs.scan_max_pages)
        && prefs.scan_max_pages >= 1 && prefs.scan_max_pages <= SCAN_PAGES_CEILING) {
      scanMaxPages = prefs.scan_max_pages;
    }
    scanPagesInput.value = scanMaxPages;
    letterLoopEnabled = prefs.letter_loop_enabled !== false;
    if (Number.isInteger(prefs.letter_loop_min_score)) letterLoopMin = prefs.letter_loop_min_score;
    for (const [key, box] of Object.entries(prefToggles)) {
      box.checked = TOGGLES_OFF_BY_DEFAULT.has(key) ? prefs[key] === true : prefs[key] !== false;
    }
    renderLetterQuality();
  } catch { /* backend down — keep defaults; loadJobs shows its own error */ }
}
renderLetterQuality(); // the pre-load defaults, so the dropdown is never empty

// Forces a refine even when the cache is still warm, and even when the
// checkbox is off (a one-off look, without turning the feature on). The result
// shows under the row, since there is no banner any more.
llmSuggestRefreshBtn.addEventListener('click', async () => {
  llmSuggestRefreshBtn.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/jobs/suggested-searches?profile_id=${PROFILE_ID}&use_llm=true`);
    if (!res.ok) throw new Error();
    const { suggestions, llm_used } = await res.json();
    llmSuggestResult.textContent = suggestions?.length
      ? `${llm_used ? '✨ AI picks' : 'Mined (the AI step fell back)'}: ${suggestions.join(' · ')}`
      : 'No suggestions yet: score a few more jobs first.';
  } catch {
    llmSuggestResult.textContent = 'Could not reach the backend.';
  }
  llmSuggestResult.hidden = false;
  llmSuggestRefreshBtn.disabled = false;
});
