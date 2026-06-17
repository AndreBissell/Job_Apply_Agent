// Minimal service worker: relays ingest notifications and keeps a small badge
// count of jobs captured this session. (The action has a popup, so opening the
// side panel is triggered from the popup button, not from an action click.)

let capturedThisSession = 0;

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'INGEST_DONE') {
    capturedThisSession += (msg.new || 0) + (msg.updated || 0);
    chrome.action.setBadgeText({ text: capturedThisSession ? String(capturedThisSession) : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#2557a7' });
    console.log('[SeekAssistant BG] Ingest done:', msg);
  } else if (msg && msg.type === 'GET_SESSION_COUNT') {
    sendResponse({ count: capturedThisSession });
  }
  return true; // keep the message channel open for async sendResponse
});
