// Minimal service worker: relays ingest notifications and keeps a small badge
// count of jobs captured this session. (The action has a popup, so opening the
// side panel is triggered from the popup button, not from an action click.)

let capturedThisSession = 0;

// The side panel keeps a port open while it is showing (sidebar.js). Seek tabs are
// told when the last one closes or the first opens, so the questions panel on the
// page can hide and return with it. Only Seek tabs are messaged.
let openSidebars = 0;

function tellSeekTabs(type) {
  chrome.tabs.query({ url: ['https://au.seek.com/*', 'https://www.seek.com.au/*'] }, (tabs) => {
    for (const tab of tabs || []) {
      chrome.tabs.sendMessage(tab.id, { type }, () => void chrome.runtime.lastError);
    }
  });
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'sidebar') return;
  openSidebars += 1;
  tellSeekTabs('SIDEBAR_OPENED');
  port.onDisconnect.addListener(() => {
    openSidebars = Math.max(0, openSidebars - 1);
    if (openSidebars === 0) tellSeekTabs('SIDEBAR_CLOSED');
  });
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === 'INGEST_DONE') {
    capturedThisSession += (msg.new || 0) + (msg.updated || 0);
    chrome.action.setBadgeText({ text: capturedThisSession ? String(capturedThisSession) : '' });
    chrome.action.setBadgeBackgroundColor({ color: '#2557a7' });
    console.log('[SeekAssistant BG] Ingest done:', msg);
  } else if (msg && msg.type === 'GET_SESSION_COUNT') {
    sendResponse({ count: capturedThisSession });
  } else if (msg && msg.type === 'BACKEND_FETCH') {
    // The content script's backend calls, made here as the extension so Chrome's
    // Local Network Access doesn't block them (see content_script.js backendFetch).
    // Only the local backend is reachable this way; never Seek, never anything else.
    let allowed = false;
    try {
      const u = new URL(msg.url);
      allowed = u.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(u.hostname)
        && ['8000', '8001'].includes(u.port);
    } catch { /* malformed url */ }
    if (!allowed) {
      sendResponse({ error: 'blocked: not the local backend' });
    } else {
      fetch(msg.url, msg.init)
        .then(async (res) => sendResponse({ ok: res.ok, status: res.status, body: await res.text() }))
        .catch((e) => sendResponse({ error: e.message }));
    }
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
