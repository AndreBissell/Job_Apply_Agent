// Side panel: fetch matched jobs from the backend and render them ranked by
// score. Clicking a job expands its cover letter (if generated). Vanilla JS,
// no build step.

const BACKEND = 'http://localhost:8000';
const PROFILE_ID = 1;

const statusEl = document.getElementById('status');
const listEl = document.getElementById('jobs');

async function loadJobs() {
  statusEl.textContent = 'Loading…';
  listEl.innerHTML = '';
  let jobs;
  try {
    const res = await fetch(`${BACKEND}/jobs?profile_id=${PROFILE_ID}`);
    jobs = await res.json();
  } catch (e) {
    statusEl.textContent = 'Backend not running — start run_api.py.';
    return;
  }

  if (!Array.isArray(jobs) || jobs.length === 0) {
    statusEl.textContent =
      'No matched jobs yet. Browse Seek with the extension active to capture listings.';
    return;
  }

  statusEl.textContent = `${jobs.length} matched job(s).`;
  for (const job of jobs) {
    listEl.appendChild(renderJob(job));
  }
}

function renderJob(job) {
  const li = document.createElement('li');

  const row = document.createElement('div');
  row.className = 'row';

  const title = document.createElement('span');
  title.className = 'title';
  title.textContent = job.title || '(untitled)';
  row.appendChild(title);

  if (job.score !== null && job.score !== undefined) {
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = Math.round(job.score);
    row.appendChild(score);
  }
  li.appendChild(row);

  const meta = document.createElement('div');
  meta.className = 'meta';
  meta.textContent = [job.company, job.location].filter(Boolean).join(' · ') || '—';
  li.appendChild(meta);

  // Expandable detail (cover letter), loaded on first click.
  let expanded = false;
  let detailEl = null;
  li.addEventListener('click', async (ev) => {
    if (ev.target.tagName === 'A') return; // let the Seek link work normally
    expanded = !expanded;
    if (!detailEl) {
      detailEl = document.createElement('div');
      detailEl.className = 'detail';
      detailEl.textContent = 'Loading…';
      li.appendChild(detailEl);
      await fillDetail(detailEl, job);
    }
    detailEl.style.display = expanded ? 'block' : 'none';
  });

  return li;
}

async function fillDetail(detailEl, job) {
  try {
    const res = await fetch(`${BACKEND}/jobs/${job.job_id}?profile_id=${PROFILE_ID}`);
    const data = await res.json();
    detailEl.innerHTML = '';

    const link = document.createElement('a');
    link.href = data.url;
    link.target = '_blank';
    link.textContent = 'Open on Seek ↗';
    detailEl.appendChild(link);

    if (data.cover_letter && data.cover_letter.generated_content) {
      const cl = document.createElement('div');
      cl.style.marginTop = '6px';
      cl.textContent = data.cover_letter.generated_content;
      detailEl.appendChild(cl);
    } else {
      const none = document.createElement('div');
      none.style.marginTop = '6px';
      none.style.color = '#6b7280';
      none.textContent = 'No cover letter yet.';
      detailEl.appendChild(none);
    }
  } catch (e) {
    detailEl.textContent = 'Could not load detail.';
  }
}

document.getElementById('refresh').addEventListener('click', loadJobs);
loadJobs();
