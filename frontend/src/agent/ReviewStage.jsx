import React, { useMemo, useState } from 'react';
import api from './api';

/* The brief, painted by what each sentence turned into. Sentences that mapped
   to nothing get the dotted underline — extraction recall is invisible
   otherwise, and a silently dropped requirement is the failure users cannot
   see for themselves. */
function BriefText({ brief, rows, unmapped, lit, onPick }) {
  const marks = useMemo(() => {
    const out = [];
    rows.forEach(({ req, on }) => {
      const span = (req.source || {}).briefSpan;
      if (span && on) out.push({ start: span[0], end: span[1], id: req.id, kind: 'mapped' });
    });
    (unmapped || []).forEach((s, i) =>
      out.push({ start: s.start, end: s.end, id: `u${i}`, kind: 'unmapped', text: s.text }));
    out.sort((a, b) => a.start - b.start);
    const clean = [];
    let last = -1;
    out.forEach((m) => {
      if (m.start >= last && m.end > m.start) { clean.push(m); last = m.end; }
    });
    return clean;
  }, [rows, unmapped]);

  const parts = [];
  let pos = 0;
  marks.forEach((m) => {
    if (m.start > pos) parts.push(<span key={`t${pos}`}>{brief.slice(pos, m.start)}</span>);
    parts.push(
      <span
        key={m.id}
        className={`bspan ${m.kind === 'unmapped' ? 'unmapped' : ''} ${lit === m.id ? 'lit' : ''}`}
        title={m.kind === 'unmapped' ? 'mapped to no requirement — click to add one' : m.id}
        onClick={() => onPick(m)}
      >
        {brief.slice(m.start, m.end)}
      </span>,
    );
    pos = m.end;
  });
  if (pos < brief.length) parts.push(<span key="tail">{brief.slice(pos)}</span>);
  return <div className="brieftext">{parts}</div>;
}

export default function ReviewStage({ review, setReview, sessionId, busy, onStart, onBack }) {
  const [lit, setLit] = useState(null);
  const [answering, setAnswering] = useState(null);
  const { brief, rows, questions, coverage } = review;

  const setRow = (id, patch) => setReview({
    ...review,
    rows: rows.map((r) => (r.req.id === id ? { ...r, ...patch } : r)),
  });
  const patchReq = (id, patch) => setRow(id, {
    req: { ...rows.find((r) => r.req.id === id).req, ...patch },
  });

  const addRow = (text = '') => {
    const used = new Set(rows.map((r) => r.req.id));
    let n = rows.length + 1;
    while (used.has(`R${n}`)) n += 1;
    setReview({
      ...review,
      rows: [...rows, {
        on: true,
        req: {
          id: `R${n}`, type: 'custom', kind: 'artifact', verify: 'judge', text,
          params: {}, scope: { kind: 'global' }, weight: 1, status: 'active',
          source: { kind: 'user' }, report: { verdict: 'unverified', evidence: [] },
        },
      }],
    });
  };

  const answer = async (q, option) => {
    setAnswering(q.id);
    try {
      const target = rows.find((r) => r.req.id === q.affects);
      if (target && option !== 'skip') {
        const res = await api.answer(sessionId, target.req, q.text, option);
        patchReq(target.req.id, res.requirement);
      } else if (target) {
        patchReq(target.req.id, { assumed: `unanswered: ${q.text}` });
      }
      setReview((prev) => ({
        ...prev,
        questions: prev.questions.map((x) =>
          (x.id === q.id ? { ...x, answer: option } : x)),
      }));
    } finally { setAnswering(null); }
  };

  const included = rows.filter((r) => r.on).length;
  const pct = coverage && coverage.total
    ? Math.round((coverage.mapped / coverage.total) * 100) : 0;

  return (
    <div className="stage">
      <div className="pane">
        <h3>Task brief</h3>
        <BriefText
          brief={brief}
          rows={rows}
          unmapped={(coverage || {}).unmapped}
          lit={lit}
          onPick={(m) => { if (m.kind === 'unmapped') addRow(m.text); }}
        />
        {coverage && (
          <div className="coverage" title="dotted = mapped to no requirement; click one to add it">
            <span className="covbar"><i style={{ width: `${pct}%` }} /></span>
            {coverage.mapped}/{coverage.total} mapped
          </div>
        )}
        <div className="startrow">
          <button className="linkbtn" onClick={onBack}>← edit</button>
        </div>
      </div>

      <div className="pane right">
        <h3>Requirements</h3>

        {questions.map((q) => (
          <div key={q.id} className={`qcard ${q.answer ? (q.answer === 'skip' ? 'skipped done' : 'done') : ''}`}>
            <div className="q">{q.text}</div>
            {q.answer ? (
              <div className="answer">
                {q.answer === 'skip' ? '⚠ skipped' : `✓ ${q.answer}`}
              </div>
            ) : (
              <div className="opts">
                {q.options.map((o) => (
                  <button key={o} disabled={answering === q.id} onClick={() => answer(q, o)}>{o}</button>
                ))}
                <button disabled={answering === q.id} onClick={() => answer(q, 'skip')}>skip</button>
              </div>
            )}
          </div>
        ))}

        {rows.map(({ req, on }) => (
          <div
            key={req.id}
            className="reqrow"
            data-on={String(on)}
            onMouseEnter={() => setLit(req.id)}
            onMouseLeave={() => setLit(null)}
          >
            <button className="cb" aria-label="include" onClick={() => setRow(req.id, { on: !on })} />
            <span>
              <span className="t">
                <span className="rid">{req.id}</span>
                <input
                  value={req.text}
                  onChange={(e) => patchReq(req.id, { text: e.target.value })}
                />
              </span>
              {/* Type, scope and how it will be verified are all derived from
                  the wording, and they only matter once there is a verdict —
                  so they live in the rail, not on this screen. Here: include
                  it, word it, pin it. */}
              {req.assumed && <span className="field assumed" title={req.assumed}>assumed</span>}
            </span>
          </div>
        ))}

        <div className="startrow">
          <button className="startbtn" disabled={!!busy || !included} onClick={onStart}>
            {busy === 'committing' ? 'starting…' : `Start run → ${included}`}
          </button>
          <button className="linkbtn" onClick={() => addRow('')}>+ Add</button>
        </div>
      </div>
    </div>
  );
}
