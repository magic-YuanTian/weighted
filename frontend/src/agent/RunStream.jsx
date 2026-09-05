import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PILOT } from './pilot';
import api from './api';
import { isDelimited, sepFor, shapeTable } from './delimited';
import { useScrollTelemetry } from './telemetry';

const DEV = window.location.hash.includes('dev');

/* One short plain sentence per step — what happened, not which tool ran on
   which path. The filename becomes words: cover_letter.md -> "the cover
   letter". The expanded view still holds the agent's own note and the raw
   checker output for anyone who opens it. */
const fileWords = (f) => {
  const stem = String(f || '').replace(/\.[A-Za-z0-9]+$/, '').replace(/[_-]+/g, ' ').trim();
  return stem ? `the ${stem}` : 'a file';
};

function describeStep(ev) {
  const meta = ev.meta || {};
  const f = fileWords(meta.path || ev.argSummary);
  if (meta.blocked === 'gate') return 'Tried to finish, but not everything is met yet';
  if (meta.blocked === 'tier0') return 'An edit tried to change locked text and was rejected';
  if (meta.ok === false) {
    if (ev.action === 'edit_file' || ev.action === 'write_file'
        || ev.action === 'insert_file') return `An edit to ${f} didn't apply`;
    if (ev.action === 'read_file') {
      /* "Couldn't find the report" while report.md sits in the workspace is
         an alarm, not a summary. Only a genuinely missing file earns it — a
         read past the end of a real file says exactly that instead. */
      if (meta.reason === 'range') return `Tried to read past the end of ${f}`;
      if (meta.reason === 'missing') return `Couldn't find ${f}`;
      return `Couldn't read ${f}`;
    }
    /* A failed shell command is routine agent work, not an app problem —
       "Something went wrong" reads as the latter. Name what happened; the
       output is one click away in the expanded step. ('command' is what the
       old engine logged; sessions recorded under it still replay.) */
    if (ev.action === 'run_command' || ev.action === 'command') {
      const cmd = (ev.argSummary || '').trim();
      return cmd ? `Ran: ${cmd} — it failed` : 'A command failed';
    }
    if (ev.action === 'run_check') return 'Tried to check the requirements — the check failed';
    return 'Something went wrong';
  }
  switch (ev.action) {
    case 'write_file': return `Wrote ${f}`;
    case 'edit_file': return `Revised ${f}`;
    case 'insert_file': return `Added to ${f}`;
    case 'read_file': return `Read ${f}`;
    case 'list_files': return 'Looked over the files';
    case 'run_command':
    case 'command': {
      const cmd = (ev.argSummary || '').trim();
      return cmd ? `Ran: ${cmd}` : 'Ran a command';
    }
    case 'run_check': {
      const head = (ev.summary || '').split('\n')[0];
      return head ? `Checked the requirements — ${head}` : 'Checked the requirements';
    }
    case 'finish': return 'Finished';
    default: return ev.action;
  }
}

function ChipRow({ chips, onSelectReq }) {
  return (
    <span className="tail">
      {(chips || []).map((c) => (
        <button
          key={c.id}
          className="rchip"
          data-v={c.verdict}
          title={`${c.id} is now ${c.verdict}`}
          onClick={(e) => { e.stopPropagation(); onSelectReq(c.id); }}
        >
          {c.id}
        </button>
      ))}
    </span>
  );
}

const StepCard = React.memo(function StepCard({ ev, open, onToggle, onSelectReq, hit, related }) {
  const meta = ev.meta || {};
  const failed = meta.ok === false;
  return (
    <div className={`step ${open ? 'open' : ''} ${failed ? 'fail' : ''} ${hit ? 'hit' : ''} ${related ? 'rel' : ''}`}
         data-step={ev.step}>
      {/* a div, not a button: the verdict chips inside are themselves buttons,
          and a nested button is hoisted out of its parent by the parser */}
      <div className="row" role="button" tabIndex={0} aria-expanded={open}
           onClick={onToggle}
           onKeyDown={(e) => {
             if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
           }}>
        <span className="caret">▸</span>
        <span className="id">Step {ev.step}</span>
        <span className="act">{describeStep(ev)}</span>
        {/* The agent says which requirements a change is for; the loop counts
            attempts per requirement from exactly this, so it is shown where
            the counting happens rather than kept in the drawer. */}
        {(meta.targets || []).length > 0 && (
          <span className="aim">
            for{' '}
            {meta.targets.map((id, i) => (
              <React.Fragment key={id}>
                {i > 0 && ', '}
                <button className="linkbtn" onClick={(e) => { e.stopPropagation(); onSelectReq(id); }}>
                  {id}
                </button>
              </React.Fragment>
            ))}
          </span>
        )}
        <ChipRow chips={ev.chips} onSelectReq={onSelectReq} />
      </div>
      {/* The agent says why it is about to do a thing, in its own words, on
          every step. That sentence used to live behind the caret, so the run
          read as a list of verbs — "Revised the biography", "Ran a command" —
          and a watcher could see what was touched without ever learning what
          the agent thought it was doing. It is the one part of a step that is
          addressed to a person, so it belongs in the stream, not in a drawer.
          The drawer keeps what is addressed to a reader who went looking: the
          checker's own words and, in dev, the raw observation. */}
      {ev.thought && <div className="said">{ev.thought}</div>}
      {open && (
        <div className="body">
          {ev.summary && (
            <div className="note">
              {ev.summary.split('\n').map((line, i) => <div key={i}>{line}</div>)}
            </div>
          )}
          {DEV && ev.observation && <div className="obs">{ev.observation}</div>}
        </div>
      )}
    </div>
  );
});

/* The task message is the source of every requirement, so it stays legible as
   one: each extracted span keeps a faint underline, and the selected
   requirement lights its own sentence up. Clicking a sentence selects its
   requirement — the correspondence has to work in both directions or it reads
   as decoration. */
const PromptText = React.memo(function PromptText({ text, reqs, selected, onSelectReq }) {
  const marks = [];
  (reqs || []).forEach((r) => {
    const span = (r.source || {}).briefSpan;
    if (span && span.length === 2 && span[1] > span[0]) {
      marks.push({ start: span[0], end: span[1], id: r.id });
    }
  });
  marks.sort((a, b) => a.start - b.start || b.end - a.end);

  const parts = [];
  let pos = 0;
  marks.forEach((m) => {
    const start = Math.max(pos, Math.min(m.start, text.length));
    const end = Math.max(start, Math.min(m.end, text.length));
    if (end <= start) return;
    if (start > pos) parts.push(<span key={`t${pos}`}>{text.slice(pos, start)}</span>);
    parts.push(
      <span
        key={m.id}
        className={`bspan ${selected === m.id ? 'lit' : ''}`}
        data-brief={m.id}
        title={`${m.id} came from here`}
        onClick={() => onSelectReq(selected === m.id ? null : m.id)}
      >
        {text.slice(start, end)}
      </span>,
    );
    pos = end;
  });
  if (pos < text.length) parts.push(<span key="tail">{text.slice(pos)}</span>);
  return <>{parts}</>;
});

/* Clicking a file chip opens the attachment as the user would expect: the real
   contents, in a code face, scrollable. A CSV is a table, so it is rendered as
   one — the alternative is a wall of commas nobody can read a column out of.

   A page at a time, though: the whole table is what made the click feel slow.
   The largest shipped file is 414 rows by 6 columns, and a run replaces the
   snapshot on every step, so an open viewer reconciled all 2,500 cells again
   each time. The agent still reads the file whole. */
const PAGE = 120;

const AttachmentViewer = React.memo(function AttachmentViewer({ sessionId, name, onClose }) {
  const [text, setText] = useState(null);
  const [err, setErr] = useState('');
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    let live = true;
    setText(null);
    setErr('');
    setShown(PAGE);
    api.attachment(sessionId, name)
      .then((d) => { if (live) setText(d.text); })
      .catch((e) => { if (live) setErr(e.message); });
    return () => { live = false; };
  }, [sessionId, name]);

  useEffect(() => {
    const esc = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  const isCsv = isDelimited(name);
  /* Parsed once per file, not once per parent render. */
  const table = useMemo(
    () => (isCsv && text !== null ? shapeTable(text, sepFor(name)) : null),
    [text, isCsv, name],
  );

  const lines = useMemo(() => (text || '').split('\n'), [text]);
  const hidden = table ? Math.max(0, table.body.length - shown) : 0;

  let meta = 'loading…';
  if (text !== null) {
    meta = table
      ? `${table.body.length.toLocaleString()} rows · ${table.width} columns · ${text.length.toLocaleString()} characters`
      : `${lines.length.toLocaleString()} lines · ${text.length.toLocaleString()} characters`;
  }

  return (
    <div className="sheet" role="dialog" aria-label={name} onClick={onClose}>
      <div className="sheetbox" onClick={(e) => e.stopPropagation()}>
        <div className="sheethead">
          <span className="ic" aria-hidden="true">▤</span>
          <b>{name}</b>
          <span className="muted">{meta}</span>
          <button className="linkbtn" onClick={onClose}>close</button>
        </div>
        <div className="sheetbody">
          {err && <div className="err">{err}</div>}
          {table && (
            <table className="csv">
              <thead>
                <tr>
                  <th className="ln" scope="col" />
                  {table.head.map((c, j) => (
                    <th key={j} scope="col" className={table.numeric[j] ? 'num' : ''}>
                      {c ? c.v : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.body.slice(0, shown).map((row, i) => (
                  <tr key={i}>
                    <td className="ln">{i + 1}</td>
                    {row.map((c, j) => (
                      <td key={j} className={table.numeric[j] ? 'num' : ''}>{c ? c.v : ''}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {text !== null && !table && <pre>{text}</pre>}
          {hidden > 0 && (
            <p className="muted">
              {hidden.toLocaleString()} more rows not shown.{' '}
              <button className="linkbtn" onClick={() => setShown((n) => n + PAGE * 4)}>
                show more
              </button>
              {' '}The agent reads the whole file.
            </p>
          )}
        </div>
      </div>
    </div>
  );
});


/* The condition, as a setting rather than as a name. A participant should be
   able to find and change it — it is the one control the study asks them to
   set — without being told which of the three is the treatment, so the
   options are numbered and the screen never says "baseline" anywhere.

   SETTINGS is the numbering, in order. Changing it renames what participants
   see; it does not change what the server is asked for. The numbers are the
   URL tokens' (s1, s2, s3), so a bookmarked link keeps meaning what it did
   when the third condition was added after the first two. */
const SETTINGS = [
  { mode: 'weighted', label: 'Setting 1' },
  { mode: 'baseline', label: 'Setting 2' },
  { mode: 'insitu', label: 'Setting 3' },
];

/* The task clock, mm:ss, from the first message. Amber past the soft limit
   the server announced; nothing is hidden or stopped by it — the hard stop is
   AgentApp's, and it hands the work in. */
function Clock({ seconds, soft }) {
  const s = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(s / 60)).padStart(2, '0');
  const ss = String(s % 60).padStart(2, '0');
  return (
    <span className="clock" data-over={String(!!(soft && s >= soft))}
          title="time since the task began">
      {mm}:{ss}
    </span>
  );
}

/* Handing in ends the task, so it takes two clicks, five seconds apart at
   most — a confirm dialog would do the same job, but jsdom has none and a
   modal over a running agent is one more thing to explain. */
function HandIn({ onHandIn }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return undefined;
    const t = setTimeout(() => setArmed(false), 5000);
    return () => clearTimeout(t);
  }, [armed]);
  return (
    <button
      className="handin"
      data-armed={String(armed)}
      title="Hand the work in as it stands. This ends the task."
      onClick={() => { if (armed) { setArmed(false); onHandIn(); } else setArmed(true); }}
    >
      {armed ? 'Hand in — click again to confirm' : 'Hand in'}
    </button>
  );
}

function SettingPicker({ mode, onSwitch }) {
  return (
    <label className="setting">
      <span>Setting</span>
      <select
        value={mode}
        onChange={(e) => onSwitch(e.target.value)}
        title="Switching starts this setting's own session. The one you leave is kept, and comes back if you switch back."
      >
        {SETTINGS.map((s) => (
          <option key={s.mode} value={s.mode}>{s.label}</option>
        ))}
      </select>
    </label>
  );
}

export default function RunStream({ snap, focus, running, pending, busy, outbox, onRun, onPause,
                                    onSend, onAnswer, onSelectReq, onJump, selected,
                                    mode, onSwitchMode, clock, closed, onHandIn }) {
  const [openSteps, setOpenSteps] = useState(() => new Set());
  const [text, setText] = useState('');
  // The benchmark tasks are served from disk, not bundled: several ship
  // without a licence and stay out of the repo. No tasks, no picker.
  const [tasks, setTasks] = useState([]);
  const [picked, setPicked] = useState('');
  const [preview, setPreview] = useState(null);
  // stable, so React.memo on the viewer actually holds across step updates
  const closePreview = useCallback(() => setPreview(null), []);
  const [hitStep, setHitStep] = useState(null);
  const scrollRef = useRef(null);
  const sessionId = snap && snap.sessionId;
  const onScroll = useScrollTelemetry(sessionId, 'chat');
  // Opening an attachment is a look at the source data — a verification act,
  // and one MUSE's timelines count. Logged with the file's name.
  const openPreview = useCallback((name) => {
    setPreview(name);
    if (sessionId) api.telemetry(sessionId, 'attachment-open', { name });
  }, [sessionId]);

  useEffect(() => {
    let live = true;
    api.presets().then((d) => { if (live) setTasks(d.tasks || []); }).catch(() => {});
    return () => { live = false; };
  }, []);
  const bottomRef = useRef(null);
  const events = (snap && snap.events) || [];

  /* What the run offers when it is not running. All of it belongs at the FOOT
     of the stream: that is where the last thing said is, where the scroll
     lands after every update, and where the eye already is. "Start the agent"
     spent a version above the conversation — above the user's own message, on
     a pane that auto-scrolls to the bottom — which is to say off-screen, on a
     screen whose only instruction was to press it.

     Two ways to arrive with a list and no work done: the staged flow
     (#agent/review) commits before any message exists, and the ordinary flow
     stops after extracting from the first one. Both want the same button. */
  let idleControl = null;
  if (snap && snap.status !== 'done') {
    if (events.some((e) => e.type === 'step')) {
      idleControl = (
        <div className="thinking" data-idle="true">
          <button className="linkbtn" onClick={onRun}>continue</button>
        </div>
      );
    } else if (events.some((e) => e.type === 'commit' || e.type === 'extracted')) {
      idleControl = (
        <div className="kickoff">
          <button className="startbtn" onClick={onRun} disabled={running || !!busy}>
            Start the agent
          </button>
        </div>
      );
    }
  }

  const firstUser = events.find((e) => e.type === 'user');
  const firstUserIndex = firstUser ? firstUser.i : -1;

  const toggle = (step) => setOpenSteps((prev) => {
    const next = new Set(prev);
    const open = !next.has(step);
    if (open) next.add(step); else next.delete(step);
    // "Expand activity" in MUSE's terms: the participant went looking at what
    // a step actually did, which is the reading the summary line exists to
    // make unnecessary. Both directions are logged; only opens are counted.
    if (sessionId) api.telemetry(sessionId, 'step-expand', { step, open });
    return next;
  });

  /* evidence jump: open the step, scroll it into view, flash it */
  useEffect(() => {
    if (!focus || focus.kind !== 'step' || !focus.stepId) return;
    setOpenSteps((prev) => new Set(prev).add(focus.stepId));
    setHitStep(focus.stepId);
    const t = setTimeout(() => {
      const el = scrollRef.current &&
        scrollRef.current.querySelector(`[data-step="${focus.stepId}"]`);
      if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 30);
    const t2 = setTimeout(() => setHitStep(null), 2400);
    return () => { clearTimeout(t); clearTimeout(t2); };
  }, [focus]);

  /* Selecting a requirement brings its source sentence into view — unless the
     selection came from an evidence jump, which is already scrolling somewhere
     on purpose (a jump always bumps focus.nonce). */
  const lastNonce = useRef(0);
  useEffect(() => {
    const jumped = focus && focus.nonce !== lastNonce.current;
    if (focus) lastNonce.current = focus.nonce;
    if (jumped || !selected || !scrollRef.current) return;
    const el = scrollRef.current.querySelector(`[data-brief="${selected}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [selected, focus]);

  /* follow the run while it is running — instantly, not smoothly: a smooth
     scroll re-queued on every step animates against itself and reads as lag */
  useEffect(() => {
    if ((running || pending) && bottomRef.current) {
      bottomRef.current.scrollIntoView({ block: 'end' });
    }
  }, [events.length, running, pending]);

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    const chosen = tasks.find((x) => x.id === picked);
    const files = (chosen && chosen.attachments) || [];
    setText('');                     // the message moves to the stream at once
    // attach first: the agent reads the data on its very first step, so it has
    // to be there before the brief arrives
    if (files.length && snap && snap.sessionId) {
      try {
        await api.attach(snap.sessionId, files.map((f) => f.name));
      } catch (e) { /* the run can still proceed; the digest will show nothing */ }
    }
    const ok = await onSend(t);
    if (ok === false) setText(t);    // …and comes back if the send failed
  };

  return (
    <div className="col">
      <div className="colhead">
        Chat
        {onSwitchMode && (
          <div className="right">
            <SettingPicker mode={mode} onSwitch={onSwitchMode} />
          </div>
        )}
      </div>
      <div className="scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="stream">
          {/* An empty session is a conversation that has not started, not a
              form waiting to be filled: the composer's placeholder says what
              to type. Requirements are extracted from this first message. */}
          {!events.some((e) => e.type === 'user') && !pending && !outbox && (
            <div className="kickoff">
              {tasks.length > 0 && (
                <div className="taskpicker">
                  <label htmlFor="taskpick">Benchmark task</label>
                  <select
                    id="taskpick"
                    value={picked}
                    onChange={(e) => {
                      const t = tasks.find((x) => x.id === e.target.value);
                      setPicked(e.target.value);
                      // the task id goes into the session's study record, so
                      // the analysis never has to recognise a task by its brief
                      if (t && sessionId) {
                        api.telemetry(sessionId, 'task-pick',
                                      { id: t.id, n: t.n, domain: t.domain, label: t.label });
                      }
                      if (t) setText(t.brief);
                    }}
                  >
                    <option value="">Choose one, or describe your own…</option>
                    {tasks.map((t) => (
                      <option key={t.id} value={t.id}>
                        {`Task ${t.n} · ${t.domain} · ${t.label}`}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <button className="linkbtn" onClick={() => { setText(PILOT); setPicked(''); }}>
                load an example task
              </button>
            </div>
          )}
          {events.map((ev) => {
            switch (ev.type) {
              case 'user':
                return (
                  <div className="turn" key={ev.i}>
                    <div className="who">You</div>
                    {ev.i === firstUserIndex ? (
                      <PromptText
                        text={ev.text || ''}
                        reqs={(snap && snap.requirements) || []}
                        selected={selected}
                        onSelectReq={onSelectReq}
                      />
                    ) : ev.text}
                  </div>
                );
              case 'attach':
                return (
                  <div className="attachrow" key={ev.i}>
                    {(ev.names || []).map((n) => {
                      const a = ((snap && snap.attachments) || []).find((x) => x.name === n);
                      return (
                        <button
                          key={n}
                          className="filechip"
                          onClick={() => openPreview(n)}
                          title="open the attached file"
                        >
                          <span className="ic" aria-hidden="true">▤</span>
                          <span className="nm">{n}</span>
                          {a && <span className="sz">{a.lines.toLocaleString()} lines</span>}
                        </button>
                      );
                    })}
                  </div>
                );
              case 'steer':
              case 'steer-queued':
                return (
                  <div className="inject" key={ev.i}>
                    told the agent{ev.requirementId ? ` (about ${ev.requirementId})` : ''}: {ev.text}
                  </div>
                );
              case 'step':
                return (
                  <StepCard
                    key={ev.i}
                    ev={ev}
                    open={openSteps.has(ev.step)}
                    hit={hitStep === ev.step}
                    related={!!selected && (ev.chips || []).some((c) => c.id === selected)}
                    onToggle={() => toggle(ev.step)}
                    onSelectReq={onSelectReq}
                  />
                );
              case 'gate':
                return (
                  <div className="notice" data-kind="gate" key={ev.i}>
                    The agent tried to finish, but {(ev.blocked || []).length} requirement
                    {(ev.blocked || []).length === 1 ? ' is' : 's are'} not met yet:{' '}
                    {(ev.blocked || []).map((id, i) => (
                      <React.Fragment key={id}>
                        {i > 0 && ', '}
                        <button className="linkbtn" onClick={() => onJump({ kind: 'req', reqId: id })}>{id}</button>
                      </React.Fragment>
                    ))}
                    . It keeps working instead.
                  </div>
                );
              case 'recheck':
                return (ev.chips || []).length ? (
                  <div className="step" key={ev.i}>
                    <div className="row">
                      <span className="caret" style={{ visibility: 'hidden' }}>▸</span>
                      <span className="act">
                        {ev.judge ? 'Reviewed the remaining requirements' : 'Re-checked the requirements'}
                      </span>
                      <ChipRow chips={ev.chips} onSelectReq={onSelectReq} />
                    </div>
                  </div>
                ) : null;
              case 'user-edit':
                return (
                  <div className="step" key={ev.i}>
                    <div className="row">
                      <span className="caret" style={{ visibility: 'hidden' }}>▸</span>
                      <span className="act">You edited {fileWords(ev.path)}</span>
                      <ChipRow chips={ev.chips} onSelectReq={onSelectReq} />
                    </div>
                  </div>
                );
              case 'notice':
                return <div className="notice" key={ev.i}>{ev.text}</div>;
              case 'resume':
                // The user pressed continue after a pause. It is a line in the
                // stream because the attempt counts start over here, and a
                // later pause on the same requirement reads differently if
                // the reader can see that they already waved it through once.
                return <div className="inject" key={ev.i}>you continued the run — attempts counted from here</div>;
              case 'trace':
                // Something the run recovered from on its own, kept for the
                // record. Amber is for what a person can act on; this is not
                // that, so it stays out of the participant's chat entirely and
                // shows only where the raw observations already do.
                return DEV ? <div className="trace" key={ev.i}>{ev.text}</div> : null;
              case 'error':
                return <div className="notice" data-kind="error" key={ev.i}>{ev.text}</div>;
              case 'assistant':
                // A turn with no text is not speech. The backend no longer
                // records one, but old runs replayed from disk still hold them
                // and an empty bubble is worse than no bubble.
                return ev.text
                  ? <div className="assistant" key={ev.i}>{ev.text}</div>
                  : null;
              case 'commit':
                return (
                  <div className="inject" key={ev.i}>
                    committed {(ev.ids || []).length} requirements
                  </div>
                );
              case 'extracted':
                // extraction is not a stage any more, but it is still visible:
                // what was taken from the message, and what is still unclear
                return (
                  <React.Fragment key={ev.i}>
                    {/* The agent talking, so it is dressed as the agent
                        talking: 13px prose in the same bubble its closing
                        message uses. It spent a version in `.inject` — the
                        11px mono label built for "committed 5 requirements" —
                        which put the one sentence a person has to read at the
                        size reserved for machine notes. */}
                    <div className="assistant">
                      Here is what I read the task as asking for — the list is on
                      the right, and you can edit it. Start me when it looks right.
                    </div>
                    {(snap.questions || []).map((q) => (
                      <div key={q.id} className={`qcard ${q.answer ? 'done' : ''}`}>
                        <div className="q">{q.text}</div>
                        {q.answer ? (
                          <div className="answer">
                            {q.answer === 'skipped' ? '⚠ skipped' : `✓ ${q.answer}`}
                          </div>
                        ) : (
                          <div className="opts">
                            {q.options.map((o) => (
                              <button key={o} disabled={!!busy}
                                      onClick={() => onAnswer(q, o)}>{o}</button>
                            ))}
                            <button disabled={!!busy}
                                    onClick={() => onAnswer(q, 'skipped')}>skip</button>
                          </div>
                        )}
                      </div>
                    ))}
                  </React.Fragment>
                );
              default:
                return null;
            }
          })}
          {outbox && (
            <div className="turn">
              <div className="who">You</div>{outbox}
            </div>
          )}
          {pending || running || busy ? (
            <div className="thinking">
              <span className="spin" />{busy || 'working'}…
              {(running || pending) && (
                <button className="linkbtn" onClick={onPause}>pause</button>
              )}
            </div>
          ) : idleControl}
          <div ref={bottomRef} />
        </div>
      </div>
      {preview && snap && (
        <AttachmentViewer
          sessionId={snap.sessionId}
          name={preview}
          onClose={closePreview}
        />
      )}
      <div className="composer">
        <textarea
          value={text}
          disabled={!!closed}
          placeholder={closed ? 'The work has been handed in.' : 'Describe the task, or ask for a change…'}
          onChange={(e) => { setText(e.target.value); setPicked(''); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
        />
        <div className="bar">
          <span>{closed ? 'handed in' : (snap && snap.status === 'done' ? 'run finished' : '')}</span>
          {clock !== null && clock !== undefined && (
            <Clock seconds={clock} soft={snap && snap.limits && snap.limits.soft} />
          )}
          {clock !== null && clock !== undefined && !closed && onHandIn && (
            <HandIn onHandIn={onHandIn} />
          )}
          <button className="sendbtn" disabled={!!closed || !text.trim() || !!busy}
                  onClick={submit}>Send ⏎</button>
        </div>
      </div>
    </div>
  );
}
