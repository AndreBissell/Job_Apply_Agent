// Applied-tab evidence helpers shared with dashboard.js (evidenceNote, csvEscape) and the
// Export-evidence button. Classic script: sidebar.html loads it after sidebar_letter_card.js
// and before sidebar.js; all share one global scope.
// ---------------------------------------------------------------------------
// Applied tab — Centrelink mutual-obligation reporting. The monthly periods and the CSV
// exports live in dashboard.js; the helpers below are shared with it.
// ---------------------------------------------------------------------------

// Screenshot files are deleted ~5 months (150 days by default) after they are taken,
// but only once reported (in a Centrelink PDF from the Overview), while the
// application record stays. Say so on the card rather than letting the image
// vanish silently: "waiting to be reported", an expiry date, a warning inside the
// last week, and a distinct 'expired' state (screenshot_taken_at kept, no file).
function evidenceNote(job) {
  const fmt = iso => new Date(iso).toLocaleDateString('en-AU', { day: 'numeric', month: 'short' });
  const note = document.createElement('div');
  note.className = 'evidence-note';
  if (job.screenshot_url && (job.screenshot_expires_at || job.screenshot_reported === false)) {
    if (!job.screenshot_expires_at) {
      note.textContent = 'Screenshot saved · not yet reported';
    } else {
      const daysLeft = (new Date(job.screenshot_expires_at) - Date.now()) / 86400000;
      note.textContent = `Reported · screenshot kept until ${fmt(job.screenshot_expires_at)}`;
      if (daysLeft <= 7) {
        note.classList.add('soon');
        note.textContent += ' — export it first';
      }
    }
    // The saved file, served by the backend under /screenshots.
    const view = document.createElement('a');
    view.className = 'evidence-view';
    view.href = `${BACKEND}${job.screenshot_url}`;
    view.target = '_blank';
    view.textContent = 'View';
    view.addEventListener('click', (e) => e.stopPropagation()); // the row itself opens the ad
    note.append(' · ', view);
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
