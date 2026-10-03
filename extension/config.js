// Which backend environment the extension talks to. Loaded FIRST in every
// context (content script, sidebar, popup) so BACKEND is defined once.
//
//   real -> port 8000  (python scripts/run_api.py real  -> real.db)
//   test -> port 8001  (python scripts/run_api.py test  -> app.db)
//
// The choice lives in chrome.storage.local so it is sticky across restarts and
// shared by every context. It is read asynchronously: callers `await
// backendReady` before their first request. Default is 'real' (fresh install).

const BACKENDS = {
  real: 'http://localhost:8000',
  test: 'http://localhost:8001',
};

let BACKEND_ENV = 'real';
let BACKEND = BACKENDS[BACKEND_ENV];

function applyBackendEnv(env) {
  if (!Object.hasOwn(BACKENDS, env)) return false;
  BACKEND_ENV = env;
  BACKEND = BACKENDS[env];
  return true;
}

const backendReady = chrome.storage.local
  .get('backendEnv')
  .then(({ backendEnv }) => applyBackendEnv(backendEnv))
  .catch(() => {}); // storage unavailable -> stay on the default

// Keep long-lived contexts (content scripts on open Seek tabs) in step when the
// switch is flipped from the sidebar.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.backendEnv) applyBackendEnv(changes.backendEnv.newValue);
});
