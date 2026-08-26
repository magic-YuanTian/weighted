import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PILOT } from './pilot';
import api from './api';

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
    if (ev.action === 'edit_file' || ev.action === 'write_file') return `An edit to ${f} didn't apply`;
    if (ev.action === 'read_file') return `Couldn't find ${f}`;
    return 'Something went wrong';
  }
  switch (ev.action) {
    case 'write_file': return `Wrote ${f}`;
    case 'edit_file': return `Revised ${f}`;
    case 'read_file': return `Read ${f}`;
    case 'list_files': return 'Looked over the files';
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
        <ChipRow chips={ev.chips} onSelectReq={onSelectReq} />
      </div>
      {open && (
        <div className="body">
          {ev.thought && <div>{ev.thought}</div>}
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
   contents, in a code face, scrollable. CSV gets light column alignment so a
   data table is readable rather than a wall of commas.

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

  const isCsv = /\.(csv|tsv)$/i.test(name);
  /* Parsed once per file, not once per parent render. */
  const { rows, cells } = useMemo(() => {
    const r = (text || '').split('\n');
    return { rows: r, cells: isCsv ? r.map((line) => line.split(',')) : null };
  }, [text, isCsv]);

  const hidden = cells ? Math.max(0, cells.length - shown) : 0;

  return (
    <div className="sheet" role="dialog" aria-label={name} onClick={onClose}>
      <div className="sheetbox" onClick={(e) => e.stopPropagation()}>
        <div className="sheethead">
          <span className="ic" aria-hidden="true">▤</span>
          <b>{name}</b>
          <span className="muted">
            {text === null ? 'loading…' : `${rows.length.toLocaleString()} lines · ${text.length.toLocaleString()} characters`}
          </span>
          <button className="linkbtn" onClick={onClose}>close</button>
        </div>
        <div className="sheetbody">
          {err && <div className="err">{err}</div>}
          {text !== null && isCsv && (
            <table className="csv">
              <tbody>
                {cells.slice(0, shown).map((row, i) => (
                  <tr key={i} className={i === 0 ? 'hd' : ''}>
                    <td className="ln">{i === 0 ? '' : i}</td>
                    {row.map((c, j) => <td key={j}>{c}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {text !== null && !isCsv && <pre>{text}</pre>}
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


export default function RunStream({ snap, focus, running, pending, busy, outbox, onRun, onPause,
                                    onSend, onAnswer, onSelectReq, onJump, selected }) {
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

  useEffect(() => {
    let live = true;
    api.presets().then((d) => { if (live) setTasks(d.tasks || []); }).catch(() => {});
    return () => { live = false; };
  }, []);
  const bottomRef = useRef(null);
  const events = (snap && snap.events) || [];

  const firstUser = events.find((e) => e.type === 'user');
  const firstUserIndex = firstUser ? firstUser.i : -1;

  const toggle = (step) => setOpenSteps((prev) => {
    const next = new Set(prev);
    if (next.has(step)) next.delete(step); else next.add(step);
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
      <div className="colhead">Chat</div>
      <div className="scroll" ref={scrollRef}>
        <div className="stream">
          {/* An empty session is a conversation that has not started, not a
              form waiting to be filled: say what to type, and get out of the
              way. Requirements are extracted from this first message. */}
          {!events.some((e) => e.type === 'user') && !pending && !outbox && (
            <div className="kickoff">
              <span>Describe the task. The agent starts working on it, and the
                requirements it must meet appear on the right.</span>
              {tasks.length > 0 && (
                <div className="taskpicker">
                  <label htmlFor="taskpick">Benchmark task</label>
                  <select
                    id="taskpick"
                    value={picked}
                    onChange={(e) => {
                      const t = tasks.find((x) => x.id === e.target.value);
                      setPicked(e.target.value);
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
                  {picked && (() => {
                    const t = tasks.find((x) => x.id === picked);
                    return t ? (
                      <p className="taskmeta">
                        <span>{t.source}</span>
                        <span>{t.words.toLocaleString()} words</span>
                        <em>{t.note}</em>
                      </p>
                    ) : null;
                  })()}
                </div>
              )}
              <button className="linkbtn" onClick={() => { setText(PILOT); setPicked(''); }}>
                load an example task
              </button>
            </div>
          )}
          {/* the staged flow (#agent/review) commits requirements before any
              message exists, so it still needs an explicit start */}
          {events.some((e) => e.type === 'commit')
            && !events.some((e) => e.type === 'step') && !pending && (
            <div className="kickoff">
              <button className="startbtn" onClick={onRun} disabled={running || !!busy}>
                Start the agent
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
                          onClick={() => setPreview(n)}
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
              case 'error':
                return <div className="notice" data-kind="error" key={ev.i}>{ev.text}</div>;
              case 'assistant':
                return <div className="assistant" key={ev.i}>{ev.text}</div>;
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
                    <div className="inject">
                      Starting on it. The requirements to meet are on the right.
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
          ) : (events.some((e) => e.type === 'step') && snap && snap.status !== 'done' && (
            <div className="thinking" data-idle="true">
              <button className="linkbtn" onClick={onRun}>continue</button>
            </div>
          ))}
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
          placeholder="Describe the task, or ask for a change…"
          onChange={(e) => { setText(e.target.value); setPicked(''); }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
        />
        <div className="bar">
          <span>{snap && snap.status === 'done' ? 'run finished' : ''}</span>
          <button className="sendbtn" disabled={!text.trim() || !!busy}
                  onClick={submit}>Send ⏎</button>
        </div>
      </div>
    </div>
  );
}
