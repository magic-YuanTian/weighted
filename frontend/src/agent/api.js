// Thin wrapper over /api/agent/*. The UI drives the loop one step per request.
const BASE = '/api/agent';

// A step is one model call; extraction is one too. Long, but not unbounded —
// an unbounded wait is indistinguishable from a dead app.
const TIMEOUT_MS = 180000;
// A step that runs a shell command is the model call plus the command plus the
// verification pass, and the command has its own timeout on the server
// (WEIGHTTEXT_SHELL_TIMEOUT, 120s by default). This only has to outlast the sum.
const STEP_TIMEOUT_MS = 420000;

async function post(path, body, timeoutMs = TIMEOUT_MS) {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: ctrl ? ctrl.signal : undefined,
    });
  } catch (e) {
    // network: the request may or may not have reached the backend. The app
    // treats these as weather (reconnect, retry), not as application errors.
    const err = new Error(e.name === 'AbortError'
      ? `${path} timed out after ${timeoutMs / 1000}s — is the backend running?`
      : `${path} could not reach the backend (${e.message})`);
    err.network = true;
    throw err;
  } finally { if (timer) clearTimeout(timer); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${path}`);
  return data;
}

async function get(path, params) {
  const qs = new URLSearchParams(params || {}).toString();
  let res;
  try {
    res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`);
  } catch (e) {
    const err = new Error(`${path} could not reach the backend (${e.message})`);
    err.network = true;
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${path}`);
  return data;
}

export const api = {
  createSession: (brief, mode) => post('/session', { brief, mode }),
  state: (sessionId) => get('/state', { sessionId }),
  extract: (brief, sessionId) => post('/extract', { brief, sessionId }),
  answer: (sessionId, requirement, question, answer) =>
    post('/answer', { sessionId, requirement, question, answer }),
  commit: (sessionId, requirements, questions, brief) =>
    post('/commit', { sessionId, requirements, questions, brief }),
  message: (sessionId, text, highlights) => post('/message', { sessionId, text, highlights }),
  step: (sessionId) => post('/step', { sessionId }, STEP_TIMEOUT_MS),
  pause: (sessionId) => post('/pause', { sessionId }),
  writeFile: (sessionId, path, text) => post('/file', { sessionId, path, text }),
  steer: (sessionId, text, requirementId) => post('/steer', { sessionId, text, requirementId }),
  gate: (sessionId, on) => post('/gate', { sessionId, on }),
  requirement: (sessionId, payload) => post('/requirement', { sessionId, ...payload }),
  recheck: (sessionId, judge) => post('/recheck', { sessionId, judge }),
  context: (sessionId) => get('/context', { sessionId }),
  presets: () => get('/presets'),
  attach: (sessionId, names) => post('/attach', { sessionId, names }),
  attachment: (sessionId, name) => get('/attachment', { sessionId, name }),
  telemetry: (sessionId, action, payload) =>
    post('/telemetry', { sessionId, action, payload }).catch(() => {}),
  exportUrl: (sessionId) => `${BASE}/export?sessionId=${sessionId}`,
};

export default api;
