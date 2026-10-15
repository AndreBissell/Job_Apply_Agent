// Applied tab: the read-only record of one application (docs/overview-applied-rework-plan.md,
// Step 2, D1/D2). Clicking a row opens it in place: the ad as captured, the match and the
// letter as last saved, all from the backend, so it still reads after Seek takes the ad down.
// It makes no request to Seek; "Open ad on Seek" is a tab the user asks for.
//
// Classic script loaded before sidebar.js and dashboard.js. Everything it uses runs at click time, never
// at load: mk, truncate, tierOf, fetchLetterInfo, renderLetterNotes, copyButton, and from
// dashboard.js getJson, fmtDay and localDay. BACKEND / PROFILE_ID come from earlier scripts.

const apRecordOpen = new Set();  // job ids whose record is open; kept across reloads
const apRecordCache = new Map(); // job id -> {data, info}: a reload redraws it without a fetch

function toggleAppliedRecord(li, job) {
  if (apRecordOpen.has(job.job_id)) {
    apRecordOpen.delete(job.job_id);
    apRecordCache.delete(job.job_id); // reopening fetches it fresh
    li.classList.remove('open');
    li.querySelector(':scope > .ap-record')?.remove();
  } else {
    apRecordOpen.add(job.job_id);
    showAppliedRecord(li, job);
  }
}

// On a click, and from renderPeriodJob for a row that was open before a reload. With the
// record cached it draws synchronously, so a reload during a scan doesn't flicker or jump.
async function showAppliedRecord(li, job) {
  li.classList.add('open');
  const rec = mk('div', 'ap-record');
  rec.addEventListener('click', e => e.stopPropagation()); // clicks inside don't collapse it
  li.appendChild(rec);

  let got = apRecordCache.get(job.job_id);
  if (!got) {
    rec.appendChild(mk('div', 'ar-note', 'Loading…'));
    try {
      const [data, info] = await Promise.all([
        getJson(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`),
        fetchLetterInfo(job.job_id),
      ]);
      got = { data, info };
    } catch {
      rec.textContent = '';
      rec.appendChild(mk('div', 'ar-note', 'Could not load this application — is the backend running?'));
      return;
    }
    if (!rec.isConnected || !apRecordOpen.has(job.job_id)) return; // closed or redrawn meanwhile
    apRecordCache.set(job.job_id, got);
    rec.textContent = '';
  }
  fillAppliedRecord(rec, job, got.data, got.info);
}

function fillAppliedRecord(rec, job, data, info) {
  const top = mk('div', 'ar-top');
  const open = mk('button', 'btn btn-sm', 'Open ad on Seek ↗');
  open.addEventListener('click', () => chrome.tabs.create({ url: data.url || job.url }));
  top.appendChild(open);
  // A label from what the availability checks already stored; nothing is fetched for it.
  if (data.expired_detected_at) {
    top.appendChild(mk('span', 'ar-status closed', `Ad closed (noticed ${dayMonth(data.expired_detected_at)})`));
  } else if (data.last_seen_live_at) {
    top.appendChild(mk('span', 'ar-status', `Last seen live ${dayMonth(data.last_seen_live_at)}`));
  }
  rec.appendChild(top);

  rec.appendChild(recordAd(data));
  rec.appendChild(recordMatch(data.match));
  rec.appendChild(recordLetter(data, info));
}

// "2026-10-12T03:00:00+00:00" -> "12/10" (the local day)
function dayMonth(iso) {
  return fmtDay(localDay(iso)).slice(0, 5);
}

function recordSection(title) {
  const sec = mk('div', 'ar-sec');
  sec.appendChild(mk('h5', null, title));
  return sec;
}

function recordAd(data) {
  const sec = recordSection('The ad');
  const kind = [data.classification, data.subclassification].filter(Boolean).join(' / ');
  const facts = [data.location, data.work_type, data.salary, kind].filter(Boolean);
  if (facts.length) sec.appendChild(mk('div', 'ar-facts', facts.join(' · ')));

  const raw = data.raw_description;
  const summary = data.summary || truncate(raw, 600);
  if (summary) sec.appendChild(mk('div', 'ar-summary', summary));
  // Plain rows, not <ul>/<li>: the .p-jobs li rules would turn them into job rows.
  for (const r of data.key_responsibilities || []) sec.appendChild(mk('div', 'note-row', r));
  const skills = [...(data.skills?.hard || []), ...(data.skills?.soft || [])];
  if (skills.length) sec.appendChild(mk('div', 'ar-skills', `Skills: ${skills.join(', ')}`));

  if (raw) {
    const full = mk('details', 'ar-full');
    const when = data.date_scraped ? ` (as captured ${fmtDay(localDay(data.date_scraped))})` : '';
    full.appendChild(mk('summary', null, `Full ad text${when}`));
    full.appendChild(mk('div', 'ar-text', raw));
    sec.appendChild(full);
  } else {
    sec.appendChild(mk('div', 'ar-note', "The ad text wasn't kept."));
  }
  return sec;
}

function recordMatch(match) {
  const sec = recordSection('The match');
  if (!match || match.score == null) {
    sec.appendChild(mk('div', 'ar-note', 'This application was never scored.'));
    return sec;
  }
  const line = mk('div', 'ar-reason');
  line.appendChild(mk('span', `score t-${tierOf(match.score)}`, String(Math.round(match.score))));
  if (match.reasoning) line.append(' ', match.reasoning);
  sec.appendChild(line);
  if (match.gaps?.length) sec.appendChild(mk('div', 'ar-gaps', `Gaps: ${match.gaps.join(' · ')}`));
  return sec;
}

// The letter as last saved, read-only (D2): editing a sent letter would rewrite the record.
function recordLetter(data, info) {
  const cl = data.cover_letter;
  const text = cl && (cl.edited_content || cl.generated_content);
  if (!text) {
    const sec = recordSection('Cover letter');
    sec.appendChild(mk('div', 'ar-note', 'No cover letter was saved for this application.'));
    return sec;
  }
  const edited = cl.edited_content && cl.edited_content !== cl.generated_content;
  const sec = recordSection(`Cover letter (${edited ? 'your edited version' : 'as generated'})`);
  sec.firstChild.appendChild(copyButton(() => text));
  sec.appendChild(mk('div', 'ar-letter', text));
  renderLetterNotes(sec, info, cl, data);
  return sec;
}
