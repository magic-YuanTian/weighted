import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import api from './api';
import BriefStage from './BriefStage';
import ReviewStage from './ReviewStage';
import RunStream from './RunStream';
import Workspace from './Workspace';
import RequirementRail from './RequirementRail';
import ContextInspector from './ContextInspector';
import './agent.css';

const MAX_AUTO_STEPS = 24;

/* Variants of the same session, chosen by the URL:
     #agent/s1     setting 1 — the weighted condition: the brief becomes a
                   requirement list, every step is verified, the gate holds
                   finish
     #agent/s2     setting 2 — the chat baseline: the same model, the same
                   tools and this same screen, with the requirement rail, the
                   verifier and the gate removed, and the workspace read-only.
                   The one way to reach the agent is the message box
     #agent/s3     setting 3 — in-situ: the baseline plus the two ways of
                   acting on the text without describing it — select a passage
                   for an anchored replace/insert, or type into the file. No
                   rail, no verdicts, no freeze. The backend enforces all three
                   — the token only decides what is asked for and what is
                   drawn
     #agent/p7     the participant's id, for the study log. Not a setting:
                   switching settings keeps it, and the server writes it into
                   the session so a run can be joined to a questionnaire row
     #agent/review the staged flow (brief -> requirement review -> run), kept
                   because "did an explicit review screen help?" is an
                   experimental condition, not a settled question
     #agent/dev    single-stepping and session export

   The URL names the settings the way the screen does, and for the same reason.
   It used to spell the condition out — `#agent/baseline` — which put the answer
   to the study's own question in the address bar, above a screen built not to
   give it away. Numbering the tokens is not decoration: it is the same blind
   the picker keeps, held one level up. */
const MODE_TOKEN = { weighted: 's1', baseline: 's2', insitu: 's3' };
// What a hash part means. `baseline` is the old spelling, still read so that a
// bookmarked link resolves to the condition it was saved for — a link that
// silently ran the other one would be worse than a broken link. It is never
// written back: normalizeHash rewrites it on arrival.
const TOKEN_MODE = { s1: 'weighted', s2: 'baseline', s3: 'insitu', baseline: 'baseline' };

// Exact parts, not a substring of the whole hash: `s1` and `s2` are short
// enough that `includes` would start matching things that are not flags.
const hashParts = (hash) => String(hash || '').replace(/^#/, '').split('/').filter(Boolean);

export const DEV = () => hashParts(window.location.hash).includes('dev');
// `p7` -> "P7". Anything else in the hash is not a participant.
export const PARTICIPANT = () => {
  const p = hashParts(window.location.hash).find((x) => /^p\d+$/i.test(x));
  return p ? p.toUpperCase() : null;
};
const WANTED_MODE = () => {
  const found = hashParts(window.location.hash).map((p) => TOKEN_MODE[p]).find(Boolean);
  return found || 'weighted';
};

/* The hash that means `target`, keeping every other flag it already carries:
   switching setting must not silently drop `dev` or `review` and land the next
   session in a different flow than the one it left. Both settings carry a
   token — a bare `#agent` beside an `#agent/s2` is a tell of its own, since the
   one without a flag reads as the ordinary version and so as the treatment. */
export function hashForMode(hash, target) {
  const rest = hashParts(hash).filter((p) => !TOKEN_MODE[p]);
  if (!rest.length) rest.push('agent');
  return `#${[...rest, MODE_TOKEN[target] || MODE_TOKEN.weighted].join('/')}`;
}

/* The URL the app then lives at. A legacy `#agent/baseline`, and a bare
   `#agent`, both become the neutral form — with replaceState, so the word is
   not left behind one press of the back button away. The condition it resolves
   to never changes here; only how the URL spells it. */
function normalizeHash() {
  const want = hashForMode(window.location.hash, WANTED_MODE());
  if (window.location.hash === want) return;
  try { window.history.replaceState(null, '', want); }
  catch (e) { window.location.hash = want; }   // no history API: still not the word
}

// The staged flow is a requirement review, so there is nothing for it to show
// in the control — and its first request would be refused. Setting 2 wins.
const REVIEW_FLOW = () => hashParts(window.location.hash).includes('review')
  && WANTED_MODE() === 'weighted';

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
    // Before anything else reads it: the address bar is the one part of the
    // screen this app does not draw, and it is the first thing a participant
    // sees. Cheap and idempotent, so it rides along with the boot effect.
    normalizeHash();
    if (REVIEW_FLOW() || sessionId || createdRef.current) return;
    createdRef.current = true;
    // Keyed by condition. Switching the hash in one tab must not resume the
    // other condition's session — that would put a baseline participant in
    // front of a requirement rail, and the run would be neither condition.
    const key = `wt-session-${WANTED_MODE()}`;
    const stored = (() => {
      try { return window.sessionStorage.getItem(key); } catch (e) { return null; }
    })();
    const boot = async () => {
      if (stored) {
        try {
          const s = await api.state(stored);
          // ...and the server's answer decides, not the key it was filed under
          if (s.status !== 'done' && (s.mode || 'weighted') === WANTED_MODE()) {
            setSessionId(stored); setSnap(s); return;
          }
        } catch (e) { /* stale or unknown id — start fresh */ }
      }
      const s = await api.createSession('', WANTED_MODE(), PARTICIPANT());
      setSessionId(s.sessionId);
      setSnap(s);
      try { window.sessionStorage.setItem(key, s.sessionId); } catch (e) {}
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
      //
      // There is nothing to re-verify in the control conditions, and /recheck
      // refuses them (409) rather than quietly doing nothing — so asking anyway
      // ended every baseline run by dropping "this session is a baseline run"
      // into the error banner, right where the participant was reading the
      // agent's last step. The mode comes from the server's snapshot, which is
      // the same thing every other branch on this screen trusts.
      const s = snapRef.current;
      if (!s || (s.mode || 'weighted') === 'weighted') {
        setSnap(await api.recheck(sessionId, true));
      }
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
     the message appears immediately and the run says what it is doing.

     That first message is also the one turn the run does NOT continue through
     on its own. Extraction is the moment the task becomes a list someone is
     about to be held to, and it used to scroll past under the agent's first
     step — read, if at all, against work already done. So the run stops there
     and waits to be started. Only there: every later message is an instruction
     to an agent already working, and pausing on those would just be a button
     between the user and the thing they asked for.

     The pause is keyed on requirements actually arriving, so the control
     condition — which extracts nothing — is untouched and still runs straight
     through. */
  const send = useCallback(async (text) => {
    if (!text.trim()) return true;
    const first = !(((snapRef.current && snapRef.current.events) || [])
      .some((e) => e.type === 'user'));
    setOutbox(text.trim());
    setBusy('reading the task');
    try {
      const s = await api.message(sessionId, text.trim());
      setSnap(s);
      setOutbox(null);
      setBusy('');
      if (!(first && ((s.requirements || []).length))) await doRun();
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
            if (!(first && ((s.requirements || []).length))) await doRun();
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

  /* A session's condition is fixed for its life, so this cannot flip the run
     in front of you: it rewrites the hash and reloads, which boots the other
     condition from a clean slate. Each condition keeps its own session in the
     tab, so switching back returns to the run you left rather than discarding
     it. The control is the setting picker in the chat header — the one place
     it is offered, and it numbers the conditions rather than naming them. */
  const switchMode = useCallback((target) => {
    if (target === (snap ? snap.mode : WANTED_MODE())) return;
    window.location.hash = hashForMode(window.location.hash, target);
    window.location.reload();
  }, [snap]);

  const toggleGate = useCallback(async () => {
    try { setSnap(await api.gate(sessionId, !snap.gateOn)); } catch (e) { fail(e); }
  }, [sessionId, snap, setSnap]);

  const jump = useCallback((target) => {
    setFocus({ ...target, nonce: Date.now() });
    if (target.reqId) setSelected(target.reqId);
    api.telemetry(sessionId, 'evidence-jump', target);
  }, [sessionId]);

  /* Selecting a requirement is the one rail interaction that used to leave no
     trace: a click on a chip in the chat, on a marked span in the workspace
     or on a row in the rail all landed in setSelected and nowhere else. Each
     surface gets its own setter so the log says where the click came from. */
  const selectFrom = useMemo(() => {
    const make = (source) => (id) => {
      setSelected(id);
      if (id && sessionId) api.telemetry(sessionId, 'select-req', { id, source });
    };
    return { chat: make('chat'), workspace: make('workspace'), rail: make('rail') };
  }, [sessionId]);

  // "Start the agent" and "continue" are the participant's, and the log
  // should show them as such; every other call into doRun follows a message
  // or a steer that is already logged.
  const runClick = useCallback(() => {
    api.telemetry(sessionId, 'run-click', { step: snapRef.current ? snapRef.current.stepCount : 0 });
    return doRun();
  }, [sessionId, doRun]);

  /* The task clock. It starts at the first message and is the server's: the
     snapshot carries when the task started and what time the server thinks it
     is, so a session restored into a second tab reads the same clock, and a
     laptop whose clock is wrong does not measure a different task. Between
     snapshots the client counts on from the last one it received. */
  const [, setTick] = useState(0);
  // Derived in render, not in an effect: the clock and the hand-in button
  // have to be on the screen in the same paint as the snapshot that started
  // the task, not one tick later.
  const anchor = useMemo(() => (snap && snap.startedAt && snap.now
    ? { base: snap.now - snap.startedAt, at: Date.now() } : null), [snap]);
  const anchorRef = useRef(null);
  anchorRef.current = anchor;
  const closed = !!(snap && snap.submitted);
  const started = !!(snap && snap.startedAt);
  useEffect(() => {
    if (!started || closed) return undefined;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [started, closed]);
  const liveElapsed = () => (anchorRef.current
    ? anchorRef.current.base + (Date.now() - anchorRef.current.at) / 1000 : null);
  const elapsed = closed ? snap.submitted - snap.startedAt : liveElapsed();

  /* Handing in ends the task: the files as they stand are what gets graded,
     and the server refuses every change after it. The hard stop hands in the
     same way, once, when the limit the server announced has passed. */
  const submit = useCallback(async (reason) => {
    runRef.current = false;
    setRunning(false);
    setBusy('handing in');
    try {
      setSnap(await api.submit(sessionId, reason, liveElapsed()));
    } catch (e) { fail(e); } finally { setBusy(''); }
  }, [sessionId, setSnap]);   // eslint-disable-line react-hooks/exhaustive-deps
  const hard = snap && snap.limits && snap.limits.hard;
  const autoRef = useRef(false);
  useEffect(() => {
    if (!hard || closed || elapsed === null || elapsed < hard || autoRef.current) return;
    autoRef.current = true;
    submit('hard-stop');
  });

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
  /* The server's answer, not the URL: a session restored into this tab carries
     the condition it was created in, and that is what the screen must match.
     Two questions decide what is drawn, the same two the server asks of a
     session (verified, steerable): is there a requirement layer, and does the
     workspace answer to the user. */
  const mode = (snap && snap.mode) || WANTED_MODE();
  const verified = mode === 'weighted';
  const steerable = mode !== 'baseline';

  return (
    <div className="wt4" data-mode={mode}>
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
          onGate={verified ? toggleGate : null}
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
      <div className="cols" data-panes={verified ? 3 : 2}>
        <RunStream
          snap={snap}
          mode={mode}
          onSwitchMode={switchMode}
          focus={focus}
          running={running}
          pending={live}
          busy={busy}
          outbox={outbox}
          onRun={runClick}
          onPause={pause}
          onSend={send}
          onAnswer={answerQuestion}
          selected={selected}
          onSelectReq={selectFrom.chat}
          onJump={jump}
          clock={elapsed}
          closed={closed}
          onHandIn={() => submit('user')}
        />
        <Workspace
          snap={snap}
          selected={selected}
          focus={focus}
          onSelectReq={selectFrom.workspace}
          /* Freeze is a requirement, so it belongs to the weighted condition
             alone; anchored edits and typing into the file belong to both
             steerable conditions. Without any of them the selection toolbar
             has nothing to offer and Workspace draws no toolbar; without
             onSave the document is read-only. A task that has been handed in
             is read-only everywhere. */
          onFreeze={verified && !closed ? freeze : null}
          onAnchor={steerable && !closed ? anchoredEdit : null}
          onSave={steerable && !closed ? saveFile : null}
        />
        {verified && (
          <RequirementRail
            snap={snap}
            selected={selected}
            setSelected={selectFrom.rail}
            onJump={jump}
            onAction={requirementAction}
            busy={busy}
          />
        )}
      </div>
      {ctx && <ContextInspector ctx={ctx} onClose={() => setCtx(null)} />}
    </div>
  );
}

/* Participants see: what the agent is doing, one Run/Pause, and the gate.
   Single-stepping and the session export are researcher tools — they live
   behind #agent/dev so the study screen stays a screen, not a cockpit. */
function TopBar({ snap, title, running, busy, onRun, onPause,
                 onStep, onGate, onContext, onExport }) {
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
