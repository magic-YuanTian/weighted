// Thin wrapper over /api/agent/*. The UI drives the loop one step per request.
const BASE = '/api/agent';

// A step is one model call; extraction is one too. Long, but not unbounded —
// an unbounded wait is indistinguishable from a dead app.
const TIMEOUT_MS = 180000;

async function post(path, body) {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), TIMEOUT_MS) : null;
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      signal: ctrl ? ctrl.signal : undefined,
    });
  } catch (e) {
    throw new Error(e.name === 'AbortError'
      ? `${path} timed out after ${TIMEOUT_MS / 1000}s — is the backend running?`
      : `${path} could not reach the backend (${e.message})`);
  } finally { if (timer) clearTimeout(timer); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${path}`);
  return data;
}

async function get(path, params) {
  const qs = new URLSearchParams(params || {}).toString();
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ''}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${path}`);
  return data;
}

export const api = {
  createSession: (brief) => post('/session', { brief }),
  state: (sessionId) => get('/state', { sessionId }),
  extract: (brief, sessionId) => post('/extract', { brief, sessionId }),
  answer: (sessionId, requirement, question, answer) =>
    post('/answer', { sessionId, requirement, question, answer }),
  commit: (sessionId, requirements, questions, brief) =>
    post('/commit', { sessionId, requirements, questions, brief }),
  message: (sessionId, text, highlights) => post('/message', { sessionId, text, highlights }),
  step: (sessionId) => post('/step', { sessionId }),
  writeFile: (sessionId, path, text) => post('/file', { sessionId, path, text }),
  steer: (sessionId, text, requirementId) => post('/steer', { sessionId, text, requirementId }),
  gate: (sessionId, on) => post('/gate', { sessionId, on }),
  requirement: (sessionId, payload) => post('/requirement', { sessionId, ...payload }),
  recheck: (sessionId, judge) => post('/recheck', { sessionId, judge }),
  context: (sessionId) => get('/context', { sessionId }),
  presets: () => get('/presets'),
  telemetry: (sessionId, action, payload) =>
    post('/telemetry', { sessionId, action, payload }).catch(() => {}),
  exportUrl: (sessionId) => `${BASE}/export?sessionId=${sessionId}`,
};

export default api;
