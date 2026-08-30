import React, { useCallback, useEffect, useRef, useState } from 'react';
import api from './api';
import BriefStage from './BriefStage';
import ReviewStage from './ReviewStage';
import RunStream from './RunStream';
import Workspace from './Workspace';
import RequirementRail from './RequirementRail';
import ContextInspector from './ContextInspector';
import './agent.css';

const MAX_AUTO_STEPS = 24;

/* Researcher-only variants of the same session, chosen by the URL:
     #agent          the study screen — one screen, chat first
     #agent/review   the staged flow (brief -> requirement review -> run),
                     kept because "did an explicit review screen help?" is an
                     experimental condition, not a settled question
     #agent/dev      single-stepping and session export                       */
export const DEV = () => window.location.hash.includes('dev');
const REVIEW_FLOW = () => window.location.hash.includes('review');

export default function AgentApp() {
  const [stage, setStage] = useState(REVIEW_FLOW() ? 'brief' : 'run');
  const [sessionId, setSessionId] = useState(null);
  const [snap, _setSnap] = useState(null);
  const [review, setReview] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const [focus, setFocus] = useState({ kind: null, nonce: 0 });
  const [running, setRunning] = useState(false);
  const [pending, setPending] = useState(false);
  const [outbox, setOutbox] = useState(null);   // shown before the server answers
  const [net, setNet] = useState('');           // connection weather — self-clearing
  const [ctx, setCtx] = useState(null);
  const runRef = useRef(false);
  // the ref must track the snapshot synchronously: the run loop reads it in
  // the same tick as setSnap, before any effect has had a chance to fire
  const snapRef = useRef(null);
  const setSnap = useCallback((s) => { snapRef.current = s; _setSnap(s); }, []);

  /* A dropped connection is weather, not an application error: it goes in the
     amber pill and clears itself the moment a request gets through. Only real
     failures earn the red banner — which now also has a close button, because
     an error that outlives its moment reads as "the app is broken". */
  const fail = (e) => {
    if (e && e.network) setNet(String(e.message || e));
    else setError(String(e.message || e));
  };

  // Nothing may fail silently. A dead click with no explanation is the worst
  // possible state for a study screen: the participant cannot tell a slow model
  // from a broken app, and neither can we from the logs.
  useEffect(() => {
    // Browser noise that is not an app failure: layout observers running a
    // frame late, opaque cross-origin "Script error." with no content.
    const benign = /ResizeObserver|^Script error\.?$/;
    const onErr = (e) => {
      const m = String(e.message || e.reason || e);
      if (!benign.test(m)) setError(`JS error: ${m}`);
    };
    const onRej = (e) => {
      const r = e.reason;
      if (r && (r.name === 'AbortError' || r.network)) return;
      setError(`Unhandled promise: ${(r && r.message) || r}`);
    };
    window.addEventListener('error', onErr);
    window.addEventListener('unhandledrejection', onRej);
    return () => {
      window.removeEventListener('error', onErr);
      window.removeEventListener('unhandledrejection', onRej);
    };
  }, []);

  // Chat-first: the session exists before the user types, so the first message
  // is a message and not a form submission.
  //
  // A reload must not eat a run: mid-task refreshes restore the same session
  // (the backend keeps it on disk). Only a finished one starts fresh, so
  // "refresh after the task" still begins the next task cleanly — and
  // sessionStorage scopes this to the tab, so a new tab is always a new run.
  const createdRef = useRef(false);   // StrictMode runs effects twice; one session is enough
  useEffect(() => {
    if (REVIEW_FLOW() || sessionId || createdRef.current) return;
    createdRef.current = true;
    const stored = (() => {
      try { return window.sessionStorage.getItem('wt-session'); } catch (e) { return null; }
    })();
    const boot = async () => {
      if (stored) {
        try {
          const s = await api.state(stored);
          if (s.status !== 'done') { setSessionId(stored); setSnap(s); return; }
        } catch (e) { /* stale or unknown id — start fresh */ }
      }
      const s = await api.createSession('');
      setSessionId(s.sessionId);
      setSnap(s);
      try { window.sessionStorage.setItem('wt-session', s.sessionId); } catch (e) {}
    };
    boot().catch((e) => { createdRef.current = false; fail(e); });
  }, [sessionId, setSnap]);

  // A step can hold the connection for a while — a model call, and then a
  // shell command with a timeout of its own. Polling the state while one is in
  // flight keeps the three panes live instead of frozen until it returns.
  // …and also while the backend says "running" with no local step in flight —
  // that is a restored session whose step is still going server-side.
  const live = pending || !!(snap && snap.status === 'running');
  useEffect(() => {
    if (!live || !sessionId) return undefined;
    const t = setInterval(() => {
      api.state(sessionId).then((s) => { setSnap(s); setNet(''); }).catch(() => {});
    }, 2500);
    return () => clearInterval(t);
  }, [live, sessionId, setSnap]);

  /* ------------------------------------------------------------- the loop */

  /* After a dropped /step response the turn usually keeps running server-side.
     Poll until the backend answers and the turn is over, then say what the run
     should do: true/false = keep going / stop, 'retry' = the request never
     landed at all, null = gave up. */
  const waitForIdle = useCallback(async (evBefore) => {
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline && runRef.current) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 4000));
      try {
        // eslint-disable-next-line no-await-in-loop
        const s = await api.state(sessionId);
        setSnap(s);
        if (s.status === 'running') {
          setNet('Reconnected — the agent is still working…');
          continue;
        }
        setNet('');
        if (((s.events || []).length) <= evBefore) return 'retry';
        return s.canContinue !== false;
      } catch (e) {
        setNet('Connection lost — retrying…');
      }
    }
    return null;
  }, [sessionId, setSnap]);

  const stepOnce = useCallback(async () => {
    setPending(true);
    try {
      const res = await api.step(sessionId);
      if (res.busy) {
        // a turn is already running for this session (a reconnect race, or a
        // double click) — don't start another, just wait for it
        const more = await waitForIdle(-1);
        return more === true || more === 'retry';
      }
      setSnap(res.snapshot);
      setNet('');
      return res.canContinue;
    } finally { setPending(false); }
  }, [sessionId, waitForIdle, setSnap]);

  const doStep = useCallback(async () => {
    setBusy('taking one step');
    try { await stepOnce(); } catch (e) { fail(e); } finally { setBusy(''); }
  }, [stepOnce]);

  const doRun = useCallback(async () => {
    if (runRef.current) return;
    runRef.current = true;
    setRunning(true);
    setError('');
    try {
      for (let i = 0; i < MAX_AUTO_STEPS && runRef.current; i += 1) {
        const evBefore = ((snapRef.current && snapRef.current.events) || []).length;
        let more;
        try {
          // eslint-disable-next-line no-await-in-loop
          more = await stepOnce();
        } catch (e) {
          if (!e.network) throw e;
          // The wire dropped mid-step. The turn is likely still running on
          // the server — reconnect and pick the run back up instead of
          // stopping with a red banner.
          setNet('Connection unstable — reconnecting…');
          // eslint-disable-next-line no-await-in-loop
          more = await waitForIdle(evBefore);
          if (more === null) {
            if (!runRef.current) break;   // paused while reconnecting — just stop
            throw e;
          }
          if (more === 'retry') continue;   // the step never landed; send it again
        }
        if (!more) break;
      }
      // Judged requirements cost a model call, so they are not re-run per step
      // — but leaving them "unverified" forever would be a rail full of
      // question marks. The moment the agent stops is when it is worth paying.
      setSnap(await api.recheck(sessionId, true));
    } catch (e) { fail(e); } finally {
      runRef.current = false;
      setRunning(false);
      setNet('');
    }
  }, [stepOnce, waitForIdle, sessionId, setSnap]);

  /* Pause must reach the backend: the server's status is what actually stops
     the run, a restored session may have no local loop at all, and a shell
     command in flight is killed through its process handle — stopping only the
     client flag reads as a dead button. Best-effort: the local loop stops
     regardless, even if the pause request rides bad wifi. */
  const pause = useCallback(() => {
    runRef.current = false;
    setRunning(false);
    if (sessionId) api.pause(sessionId).then(setSnap).catch(() => {});
  }, [sessionId, setSnap]);

  /* The first message costs a model call (it is also the requirement
     extraction), so the screen must answer the keystroke, not the round trip:
     the message appears immediately and the run says what it is doing. */
  const send = useCallback(async (text) => {
    if (!text.trim()) return true;
    setOutbox(text.trim());
    setBusy('reading the task');
    try {
      setSnap(await api.message(sessionId, text.trim()));
      setOutbox(null);
      setBusy('');
      await doRun();
      return true;
    } catch (e) {
      // A dropped response does not mean a dropped message: if the backend
      // already logged it, carry on with the run instead of bouncing the text
      // back (a resend would duplicate the message and the extraction).
      if (e.network && sessionId) {
        try {
          const s = await api.state(sessionId);
          const landed = (s.events || []).some(
            (ev) => ev.type === 'user' && ev.text === text.trim(),
          );
          if (landed) {
            setSnap(s);
            setOutbox(null);
            setBusy('');
            await doRun();
            return true;
          }
        } catch (e2) { /* backend truly unreachable — fall through */ }
      }
      fail(e);
      setOutbox(null);
      return false;              // the composer keeps the text so it is not lost
    } finally { setBusy(''); }
  }, [sessionId, doRun, setSnap]);

  // "Fix this"-style scoped instructions and anchored edits both ride this:
  // queue at the head of the next step, then run — one intention, one outcome.
  const steer = useCallback(async (text, requirementId) => {
    setBusy('sending your instruction');
    try {
      setSnap(await api.steer(sessionId, text, requirementId));
      setBusy('');
      await doRun();
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, doRun, setSnap]);

  /* --------------------------------------------------------------- store */
  const requirementAction = useCallback(async (payload) => {
    setBusy('updating requirements');
    try { setSnap(await api.requirement(sessionId, payload)); }
    catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, setSnap]);

  const answerQuestion = useCallback(async (question, option) => {
    const target = (snap.requirements || []).find((r) => r.id === question.affects)
      || (snap.requirements || [])[0];
    if (!target) return;
    setBusy('updating the requirement');
    try {
      const res = await api.answer(sessionId, target, question.text, option);
      if (res.snapshot) setSnap(res.snapshot);
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, snap, setSnap]);

  /* v3's freeze, carried into the agent world: a frozen span becomes a
     `preserve` requirement with Tier-0 enforcement, so the edit tool — not the
     model — refuses any write that would remove it. */
  const freeze = useCallback(async (text, file) => {
    setBusy('freezing text');
    try {
      setSnap(await api.requirement(sessionId, {
        action: 'add',
        requirement: {
          type: 'preserve', text: `Keep unchanged: “${text.slice(0, 60)}”`,
          params: { phrases: [text] }, enforce: true, weight: 1,
          scope: file ? { kind: 'file', name: file } : { kind: 'global' },
          source: { kind: 'annotation' },
        },
      }));
      // freezing must be visible immediately, and the code checks are free —
      // this is what gives the span its ❄ mark without waiting for a step
      setSnap(await api.recheck(sessionId, false));
      api.telemetry(sessionId, 'freeze', { file, chars: text.length });
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, setSnap]);

  const saveFile = useCallback(async (path, text) => {
    setBusy('saving your edit');
    try {
      setSnap(await api.writeFile(sessionId, path, text));
      api.telemetry(sessionId, 'edit-file', { path, chars: text.length });
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, setSnap]);

  /* replace / insert: an instruction anchored to an exact quote, so "this
     paragraph" is never ambiguous the way it is in chat. */
  const anchoredEdit = useCallback((kind, text, file, instruction) => {
    const where = file ? ` in ${file}` : '';
    const msg = kind === 'replace'
      ? `Replace exactly this passage${where}, changing nothing else:\n"${text}"\n\nHow: ${instruction}`
      : `Insert new text immediately after this passage${where}, leaving it unchanged:\n"${text}"\n\nWhat: ${instruction}`;
    api.telemetry(sessionId, kind, { file, chars: text.length });
    return steer(msg);
  }, [sessionId, steer]);

  const toggleGate = useCallback(async () => {
    try { setSnap(await api.gate(sessionId, !snap.gateOn)); } catch (e) { fail(e); }
  }, [sessionId, snap, setSnap]);

  const jump = useCallback((target) => {
    setFocus({ ...target, nonce: Date.now() });
    if (target.reqId) setSelected(target.reqId);
    api.telemetry(sessionId, 'evidence-jump', target);
  }, [sessionId]);

  const openContext = useCallback(async () => {
    try { setCtx(await api.context(sessionId)); } catch (e) { fail(e); }
  }, [sessionId]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setCtx(null); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  /* --------------------------------------------- staged flow (#agent/review) */
  const startExtraction = useCallback(async (brief) => {
    setBusy('extracting');
    setError('');
    try {
      const session = await api.createSession(brief);
      setSessionId(session.sessionId);
      setSnap(session);
      const res = await api.extract(brief, session.sessionId);
      setReview({
        brief,
        rows: res.requirements.map((r) => ({ req: r, on: true })),
        questions: res.questions,
        coverage: res.coverage,
      });
      setStage('review');
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [setSnap]);

  const commit = useCallback(async () => {
    if (!review) return;
    setBusy('committing');
    try {
      const reqs = review.rows.filter((r) => r.on).map((r) => r.req);
      setSnap(await api.commit(sessionId, reqs, review.questions, review.brief));
      setStage('run');
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [review, sessionId, setSnap]);

  if (stage === 'brief') {
    return (
      <div className="wt4">
        <TopBar snap={null} title="New session" />
        {error && <div className="err">{error}</div>}
        <BriefStage busy={busy === 'extracting'} onExtract={startExtraction} />
      </div>
    );
  }

  if (stage === 'review') {
    return (
      <div className="wt4">
        <TopBar snap={snap} title="Requirement review" />
        {error && <div className="err">{error}</div>}
        <ReviewStage
          review={review}
          setReview={setReview}
          sessionId={sessionId}
          busy={busy}
          onStart={commit}
          onBack={() => setStage('brief')}
        />
      </div>
    );
  }

  /* ------------------------------------------------------------ the screen */
  return (
    <div className="wt4">
      {/* No title bar. Everything it held was either redundant (a status word
          next to a spinner), or a control nobody could explain to themselves
          (the finish gate) — that one now introduces itself in the stream, in
          words, at the moment it actually stops something. */}
      {DEV() && (
        <TopBar
          snap={snap}
          title={(snap && snap.brief ? snap.brief.split('\n')[0] : 'New session')}
          running={running}
          busy={busy}
          onRun={doRun}
          onPause={pause}
          onStep={doStep}
          onGate={toggleGate}
          onContext={openContext}
          onExport={() => window.open(api.exportUrl(sessionId), '_blank')}
        />
      )}
      {error && (
        <div className="err">
          <span>{error}</span>
          <button className="x" onClick={() => setError('')} aria-label="dismiss error">×</button>
        </div>
      )}
      {net && !error && <div className="net">{net}</div>}
      <div className="cols">
        <RunStream
          snap={snap}
          focus={focus}
          running={running}
          pending={live}
          busy={busy}
          outbox={outbox}
          onRun={doRun}
          onPause={pause}
          onSend={send}
          onAnswer={answerQuestion}
          selected={selected}
          onSelectReq={setSelected}
          onJump={jump}
        />
        <Workspace
          snap={snap}
          selected={selected}
          focus={focus}
          onSelectReq={setSelected}
          onFreeze={freeze}
          onAnchor={anchoredEdit}
          onSave={saveFile}
        />
        <RequirementRail
          snap={snap}
          selected={selected}
          setSelected={setSelected}
          onJump={jump}
          onAction={requirementAction}
          busy={busy}
        />
      </div>
      {ctx && <ContextInspector ctx={ctx} onClose={() => setCtx(null)} />}
    </div>
  );
}

/* Participants see: what the agent is doing, one Run/Pause, and the gate.
   Single-stepping and the session export are researcher tools — they live
   behind #agent/dev so the study screen stays a screen, not a cockpit. */
function TopBar({ snap, title, running, busy, onRun, onPause, onStep, onGate, onContext, onExport }) {
  const status = running ? 'running' : (snap ? snap.status : 'idle');
  const started = snap && snap.stepCount > 0;
  return (
    <div className="topbar">
      <div className="task">
        <span className="dot" data-s={status} />
        <span className="t" title={title}>{title}</span>
      </div>
      {onRun && started && (
        <div className="transport">
          <button onClick={running ? onPause : onRun} className={running ? 'on' : ''}
                  disabled={snap && snap.status === 'done'}>
            {running ? '⏸ Pause' : '▶ Run'}
          </button>
          {DEV() && (
            <button onClick={onStep} disabled={running || !!busy || (snap && snap.status === 'done')}>
              ⏭ Step
            </button>
          )}
        </div>
      )}
      <div className="meters">
        {started && <span>step <b>{snap.stepCount}</b></span>}
        {busy && <span>{busy}</span>}
        {onContext && <button className="linkbtn" onClick={onContext}>context</button>}
        {onExport && DEV() && <button className="linkbtn" onClick={onExport}>export</button>}
        {onGate && started && (
          <button className="gate" data-on={String(!!(snap && snap.gateOn))}
                  onClick={onGate}
                  title="Hold the agent's finish action while any requirement is not satisfied">
            <span className="switch" />Finish gate
          </button>
        )}
      </div>
    </div>
  );
}
