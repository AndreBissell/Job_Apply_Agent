// Popup logic: show backend health + session count, and open the side panel.

const BACKEND = 'http://localhost:8000';

async function checkBackend() {
  const dot = document.getElementById('dot');
  const label = document.getElementById('backend');
  try {
    const res = await fetch(`${BACKEND}/health`);
    const data = await res.json();
    if (data.status === 'ok') {
      dot.classList.add('ok');
      label.textContent = `Backend running (profile ${data.profile_id ?? '—'})`;
      return;
    }
    throw new Error('unexpected response');
  } catch (e) {
    dot.classList.add('bad');
    label.textContent = 'Backend not running — start run_api.py';
  }
}

function loadSessionCount() {
  try {
    chrome.runtime.sendMessage({ type: 'GET_SESSION_COUNT' }, (resp) => {
      if (resp && typeof resp.count === 'number') {
        document.getElementById('captured').textContent =
          `Captured this session: ${resp.count}`;
      }
    });
  } catch (e) { /* background may be asleep; ignore */ }
}

document.getElementById('openSidebar').addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) {
    await chrome.sidePanel.open({ tabId: tab.id });
    window.close();
  }
});

checkBackend();
loadSessionCount();
