import React, { useMemo, useState } from 'react';

const DEV = window.location.hash.includes('dev');

/* Two actions on a requirement, and no others:
     · click it     -> why it has that verdict, and the evidence behind it
     · highlight it -> repeated to the agent before every action (steering)
   Deleting lives inside the opened row, because owning the store means being
   able to drop a badly extracted rule — but it is not a third thing competing
   for attention in the list. Everything else the backend can do (pause,
   override a verdict, weight 3, Tier-0 toggles) stays off the study screen. */

const GLYPH = {
  satisfied: '✓', violated: '⚠', partial: '◐', stale: '◌', unverified: '○', paused: '⏸',
};
const idNum = (id) => {
  const m = /(\d+)/.exec(id || '');
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
};
const verdictOf = (r) => (r.status === 'paused' ? 'paused'
  : ((r.report || {}).verdict || 'unverified'));

/* Satisfaction over steps, rebuilt from the step events: each step carries the
   verdict changes it caused, so the tape is the run's history, not a snapshot. */
function useTape(snap) {
  return useMemo(() => {
    if (!snap) return { cols: [], ids: [] };
    const ids = snap.requirements.map((r) => r.id);
    const state = {};
    ids.forEach((id) => { state[id] = 'unverified'; });
    const cols = [];
    (snap.events || []).filter((e) => e.type === 'step').forEach((e) => {
      (e.chips || []).forEach((c) => { state[c.id] = c.verdict; });
      cols.push({ step: e.step, state: { ...state } });
    });
    const trimmed = cols.slice(-21);
    /* Some verdicts change outside any step — the judge pass that runs when
       the agent stops, a hand edit, a freeze. Without a closing "now" column
       the tape would show the last step's state while the bar above shows
       today's, and the mismatch reads as a bug (it was one). */
    if (trimmed.length) {
      const now = {};
      snap.requirements.forEach((r) => {
        now[r.id] = r.status === 'paused' ? 'unverified'
          : ((r.report || {}).verdict || 'unverified');
      });
      trimmed.push({ step: 'now', state: now });
    }
    return { cols: trimmed, ids };
  }, [snap]);
}

/* The opened row answers three questions, in prose, in this order:
   where it stands, how that is known, where to look. Nothing else. */
const stripFile = (d) => (d || '').replace(/^\[[^\]]+\]\s*/, '');

function statusSentence(r, v) {
  const detail = stripFile((r.report || {}).detail);
  switch (v) {
    case 'satisfied': return detail ? `Met. ${detail}.` : 'Met.';
    case 'violated': return detail ? `Not met. ${detail}.` : 'Not met.';
    case 'partial': return detail ? `Partly met. ${detail}.` : 'Partly met.';
    case 'stale': return 'The text changed after the last check, so this result may be outdated.';
    case 'paused': return 'Paused. Not being checked right now.';
    default: return 'Not checked yet.';
  }
}

function methodSentence(r) {
  if (r.type === 'preserve' && r.enforce) {
    return 'This text is locked. Any edit that removes it is rejected.';
  }
  if (r.verify === 'code') {
    if (r.type === 'length') return 'The word count is checked after every change.';
    if (r.type === 'lexical-ban') return 'The text is scanned for these phrases after every change.';
    if (r.type === 'lexical-require' || r.type === 'preserve') {
      return 'The text is checked for these phrases after every change.';
    }
    return 'Checked automatically after every change.';
  }
  if (r.verify === 'rule') return "Checked from the agent's steps, not from the text.";
  return 'The model reviews this when the agent stops.';
}

export default function RequirementRail({ snap, selected, setSelected, onJump, onAction,
                                          busy }) {
  const [adding, setAdding] = useState(null);
  const [editing, setEditing] = useState(null);   // {id, text}
  const reqs = (snap && snap.requirements) || [];
  const counts = (snap && snap.counts) || {};
  const { cols, ids } = useTape(snap);

  /* Extraction order, always. Sorting by verdict put the problems on top, but
     it also moved every row whenever a verdict changed — so the list you
     learned two steps ago was never the list in front of you. Position is
     worth more than triage here; the summary bar does the triage. */
  const selectRow = (r, open, evidence) => {
    if (open) { setSelected(null); return; }
    setSelected(r.id);
    const art = evidence.find((ev) => ev.kind === 'artifact' && !ev.scope)
      || evidence.find((ev) => ev.kind === 'artifact');
    if (art) {
      onJump({ kind: 'artifact', file: art.file, start: art.start, end: art.end, reqId: r.id });
    }
  };

  const commitEdit = () => {
    if (!editing) return;
    const target = reqs.find((r) => r.id === editing.id);
    const text = editing.text.trim();
    setEditing(null);
    if (target && text && text !== target.text) {
      // wording is the requirement: the backend re-derives type/params from it
      onAction({ action: 'update', id: editing.id, requirement: { text } });
    }
  };

  const sorted = [...reqs].sort((a, b) => (idNum(a.id) - idNum(b.id))
    || String(a.id).localeCompare(String(b.id)));
  const total = reqs.filter((r) => r.status !== 'paused').length;
  const met = counts.satisfied || 0;
  const unchecked = (counts.unverified || 0) + (counts.partial || 0);

  return (
    <div className="col">
      <div className="colhead">Requirements</div>

      <div className="scroll">
        <div className="rail">
          {sorted.map((r) => {
            const rep = r.report || {};
            const v = verdictOf(r);
            const open = selected === r.id;
            const evidence = rep.evidence || [];
            const focused = (r.weight || 1) >= 2;
            return (
              <div key={r.id} className={`req ${open ? 'sel' : ''} ${focused ? 'hl' : ''}`} data-v={v}>
                <div
                  className="head"
                  role="button"
                  tabIndex={0}
                  aria-expanded={open}
                  onClick={() => selectRow(r, open, evidence)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault(); selectRow(r, open, evidence);
                    }
                  }}
                >
                  <span className="glyph">{GLYPH[v]}</span>
                  <span>
                    {editing && editing.id === r.id ? (
                      <input
                        className="textedit"
                        autoFocus
                        value={editing.text}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => setEditing({ id: r.id, text: e.target.value })}
                        onBlur={() => commitEdit()}
                        onKeyDown={(e) => {
                          e.stopPropagation();
                          if (e.key === 'Enter') commitEdit();
                          if (e.key === 'Escape') setEditing(null);
                        }}
                      />
                    ) : (
                      <>
                        {/* the id stays: it is the shared vocabulary between the
                            rail, the gate message and the chips on each step */}
                        <span className="text" title={r.text}><span className="rid">{r.id}</span>{r.text}</span>
                        {v !== 'satisfied' && rep.detail && (
                          <span className="detail">{rep.detail}</span>
                        )}
                      </>
                    )}
                  </span>
                  <span className="meta">
                    <button
                      className="rowact"
                      title="Edit the wording"
                      aria-label={`edit ${r.id}`}
                      onClick={(e) => { e.stopPropagation(); setEditing({ id: r.id, text: r.text }); }}
                    >
                      ✎
                    </button>
                    <button
                      className="rowact danger"
                      title="Remove this requirement"
                      aria-label={`delete ${r.id}`}
                      onClick={(e) => { e.stopPropagation(); onAction({ action: 'delete', id: r.id }); }}
                    >
                      ✕
                    </button>
                    {/* the steering control says what it is: an unlabelled
                        glyph makes the one novel mechanism a guessing game */}
                    <button
                      className={`rowact ${focused ? 'always' : ''}`}
                      title={focused
                        ? 'Highlighted: repeated to the agent before every action. Click to stop.'
                        : 'Highlight: repeat this to the agent before every action.'}
                      aria-label={`highlight ${r.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onAction({ action: 'weight', id: r.id, weight: focused ? 1 : 2 });
                      }}
                    >
                      🖍
                    </button>
                  </span>
                </div>

                <div className={`drawerwrap ${open ? 'open' : ''}`} aria-hidden={!open}>
                  <div className="drawer">
                    <p className="expl strong">{statusSentence(r, v)}</p>
                    <p className="expl">{methodSentence(r)}</p>
                    {focused && (
                      <p className="expl">Highlighted. The agent is reminded of this before every action.</p>
                    )}
                    {r.assumed && <p className="expl">Assumed: {r.assumed}</p>}
                  </div>
                </div>
              </div>
            );
          })}

          {adding === null ? (
            <button className="addrow" onClick={() => setAdding('')}>
              <span className="plus">＋</span> Add a requirement
            </button>
          ) : (
            <div className="addrow">
              <span className="plus">＋</span>
              <input
                autoFocus
                value={adding}
                placeholder="what must be true of the result?"
                onChange={(e) => setAdding(e.target.value)}
                onBlur={() => !adding.trim() && setAdding(null)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') setAdding(null);
                  if (e.key === 'Enter' && adding.trim()) {
                    onAction({ action: 'add', requirement: { text: adding.trim(), type: 'custom' } });
                    setAdding(null);
                  }
                }}
              />
            </div>
          )}
        </div>
      </div>

      <div className="railfoot">
        {total > 0 && (
          <div className="progress" title="every requirement, by where it stands right now">
            <div className="plabel">
              <b>{met}</b> of {total} requirements met
              {counts.violated ? ` · ${counts.violated} not met` : ''}
              {counts.stale ? ` · ${counts.stale} out of date` : ''}
              {unchecked ? ` · ${unchecked} unchecked` : ''}
            </div>
            <div className="pbar">
              {['satisfied', 'violated', 'stale', 'partial', 'unverified'].map((k) => (
                counts[k] ? <i key={k} data-v={k} style={{ flexGrow: counts[k] }} /> : null
              ))}
            </div>
          </div>
        )}

        {cols.length > 1 && (
          <div className="history">
            <div className="evlabel" style={{ marginTop: 12 }}>
              step by step · #{cols[0].step} → now
            </div>
            <div className="steps">
              {cols.map((c) => {
                const vals = reqs.filter((r) => r.status !== 'paused')
                  .map((r) => c.state[r.id] || 'unverified');
                const n = Math.max(vals.length, 1);
                const pc = (k) => (vals.filter((x) => k.includes(x)).length / n) * 100;
                const metPc = pc(['satisfied']);
                return (
                  <button
                    key={c.step}
                    className="stepcol"
                    title={`${c.step === 'now' ? 'now' : `step ${c.step}`} · ${Math.round(metPc / 100 * n)} of ${n} met`}
                    aria-label={`${c.step === 'now' ? 'now' : `step ${c.step}`}, ${Math.round(metPc / 100 * n)} of ${n} met`}
                    onClick={() => c.step !== 'now' && onJump({ kind: 'step', stepId: c.step })}
                  >
                    <i data-v="satisfied" style={{ height: `${metPc}%` }} />
                    <i data-v="stale" style={{ height: `${pc(['stale', 'partial'])}%` }} />
                    <i data-v="violated" style={{ height: `${pc(['violated'])}%` }} />
                  </button>
                );
              })}
            </div>
            {selected && (
              <div className="reqhist" title={`${selected} at each step`}>
                <span className="lbl">{selected}</span>
                {cols.map((c) => (
                  <button
                    key={c.step}
                    data-v={c.state[selected]}
                    title={`${selected} · ${c.step === 'now' ? 'now' : `step #${c.step}`} · ${c.state[selected]}`}
                    aria-label={`${selected} ${c.step === 'now' ? 'now' : `step ${c.step}`} ${c.state[selected]}`}
                    onClick={() => c.step !== 'now' && onJump({ kind: 'step', stepId: c.step, reqId: selected })}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {DEV && cols.length > 1 && (
          <>
            <div className="evlabel" style={{ marginTop: 12 }}>
              each verdict, step {cols[0].step} → {cols[cols.length - 1].step}
            </div>
            <div className="tape" style={{ gridTemplateColumns: `26px repeat(${cols.length}, 1fr)` }}>
              {ids.map((id) => (
                <React.Fragment key={id}>
                  <span className="lbl">{id}</span>
                  {cols.map((c) => (
                    <button
                      key={`${id}-${c.step}`}
                      data-v={c.state[id]}
                      title={`${id} · step #${c.step} · ${c.state[id]}`}
                      aria-label={`${id} step ${c.step} ${c.state[id]}`}
                      onClick={() => { setSelected(id); onJump({ kind: 'step', stepId: c.step, reqId: id }); }}
                    />
                  ))}
                </React.Fragment>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
