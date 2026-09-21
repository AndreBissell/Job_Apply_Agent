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
  } else if (msg && msg.type === 'CAPTURE_SCREENSHOT') {
    // captureVisibleTab only works from an extension page (not a content script
    // directly), and only captures whichever tab is currently active in the
    // given window — that's why this is relayed through the background worker
    // rather than called straight from content_script.js. Covered by the
    // existing au.seek.com/www.seek.com.au host_permissions, no extra grant needed.
    const windowId = sender.tab ? sender.tab.windowId : chrome.windows.WINDOW_ID_CURRENT;
    chrome.tabs.captureVisibleTab(windowId, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        sendResponse({ error: chrome.runtime.lastError.message });
      } else {
        sendResponse({ dataUrl });
      }
    });
  }
  return true; // keep the message channel open for async sendResponse
});
