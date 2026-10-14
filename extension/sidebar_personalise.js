// Personalise panel: user-tunable thresholds, letter limits, side-output toggles, search
// suggestions, bulk hide. Classic script: sidebar.html loads it after sidebar_scan.js and
// before sidebar.js; all share one global scope (it writes autoLetterMin and scanMaxPages).
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
// Per-letter limits of the pipeline (plan §7): preference key -> [input, save button, min, max,
// whole numbers only?]. The bounds match the API's (preferences.LIMIT_BOUNDS).
const letterLimits = {
  letter_max_drafts: [document.getElementById('limit-drafts'), document.getElementById('limit-drafts-save'), 1, 5, true],
  letter_max_tool_calls: [document.getElementById('limit-tools'), document.getElementById('limit-tools-save'), 6, 40, true],
  llm_run_budget_usd: [document.getElementById('limit-usd'), document.getElementById('limit-usd-save'), 0.05, 5, false],
};
const limitValues = { letter_max_drafts: 3, letter_max_tool_calls: 15, llm_run_budget_usd: 0.5 };
const limitsNote = document.getElementById('limits-note');

function showLimit(key) {
  const [input, , , , whole] = letterLimits[key];
  input.value = whole ? String(limitValues[key]) : limitValues[key].toFixed(2);
}

// Warn when the tool-call cap can't fit the drafts allowed (4 per draft + 2).
function updateLimitsNote() {
  const needed = 4 * limitValues.letter_max_drafts + 2;
  const warn = limitsNote.querySelector('.warn') || limitsNote.appendChild(mk('span', 'warn'));
  warn.textContent = limitValues.letter_max_tool_calls < needed
    ? ` Note: ${limitValues.letter_max_drafts} drafts need about ${needed} tool calls, so a run may stop before its last draft.`
    : '';
}

for (const [key, [input, btn, min, max, whole]] of Object.entries(letterLimits)) {
  btn.addEventListener('click', async () => {
    const value = whole ? Number(input.value) : Math.round(Number(input.value) * 100) / 100;
    if (!Number.isFinite(value) || value < min || value > max || (whole && !Number.isInteger(value))) {
      showLimit(key); // out of range — snap back, don't save
      return;
    }
    btn.disabled = true;
    btn.textContent = '…';
    if (await savePref({ [key]: value })) {
      limitValues[key] = value;
      showLimit(key);
      updateLimitsNote();
      btn.textContent = 'Saved ✓';
    } else {
      btn.textContent = 'Error';
    }
    setTimeout(() => { btn.textContent = 'Save'; btn.disabled = false; }, 1500);
  });
}

// The pipeline's side outputs (plan §6): preference key -> its checkbox. All default on.
const sideOutputToggles = {
  screening_answers_enabled: document.getElementById('side-screening'),
  learning_suggestions_enabled: document.getElementById('side-learning'),
  resume_advice_enabled: document.getElementById('side-resume'),
  // Not a side output (it runs outside the letter run), but the same on/off shape.
  screening_question_help_enabled: document.getElementById('qa-question-help'),
  // Likewise not a side output: the panel's background "is it still advertised?" checks
  // (sidebar_availability.js) and the freshness gate on the AI work (app/availability.py).
  availability_checks_enabled: document.getElementById('availability-checks'),
};

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
    for (const [key, box] of Object.entries(sideOutputToggles)) box.checked = prefs[key] !== false;
    for (const [key, [, , min, max, whole]] of Object.entries(letterLimits)) {
      const v = prefs[key];
      if (typeof v === 'number' && v >= min && v <= max && (!whole || Number.isInteger(v))) limitValues[key] = v;
      showLimit(key);
    }
    updateLimitsNote();
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

for (const [key, box] of Object.entries(sideOutputToggles)) {
  box.addEventListener('change', async () => {
    const enabled = box.checked;
    box.disabled = true;
    if (!(await savePref({ [key]: enabled }))) box.checked = !enabled; // revert — it didn't stick
    box.disabled = false;
  });
}

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
