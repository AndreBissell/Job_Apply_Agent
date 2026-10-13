// Suggested-searches banner on the Jobs tab: free phrase mining, seekSearchUrl (shared with
// dashboard.js). Classic script: sidebar.html loads it after sidebar_evidence.js and before
// sidebar.js; all share one global scope.
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
