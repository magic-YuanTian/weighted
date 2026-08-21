import React, { useEffect, useMemo, useRef, useState } from 'react';

const words = (t) => (t || '').split(/\s+/).filter(Boolean).length;
const PERSISTENT = new Set(['violated', 'partial', 'stale', 'frozen']);

/* Decorations come straight from the reports' evidence. A violation stays
   underlined whether or not its requirement is selected; frozen spans are
   always drawn — they are a standing guarantee, not a finding. */
function useDecorations(requirements, selected) {
  return useMemo(() => {
    const byFile = {};
    const scopes = {};
    (requirements || []).forEach((r) => {
      const rep = r.report || {};
      const isSel = r.id === selected;
      const frozen = r.type === 'preserve' && r.enforce;
      const verdict = frozen ? 'frozen' : rep.verdict;
      if (r.status === 'paused') return;
      if (!isSel && !PERSISTENT.has(verdict)) return;
      (rep.evidence || []).forEach((ev) => {
        if (ev.kind !== 'artifact' || !ev.file) return;
        if (ev.scope) {
          if (isSel) scopes[ev.file] = rep.verdict;
          return;
        }
        (byFile[ev.file] = byFile[ev.file] || []).push({
          start: ev.start, end: ev.end, reqId: r.id, verdict,
        });
      });
    });
    Object.keys(byFile).forEach((f) => {
      byFile[f].sort((a, b) => a.start - b.start || b.end - a.end);
      const clean = [];
      let last = -1;
      byFile[f].forEach((d) => {
        if (d.start >= last && d.end > d.start) { clean.push(d); last = d.end; }
      });
      byFile[f] = clean;
    });
    return { byFile, scopes };
  }, [requirements, selected]);
}

const esc = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function buildHtml(text, decorations, hitKey) {
  const parts = [];
  let pos = 0;
  (decorations || []).forEach((d) => {
    const start = Math.max(pos, Math.min(d.start, text.length));
    const end = Math.max(start, Math.min(d.end, text.length));
    if (end <= start) return;
    if (start > pos) parts.push(esc(text.slice(pos, start)));
    const key = `${d.reqId}:${start}`;
    parts.push(`<span class="mark ${hitKey === key ? 'hit' : ''}" data-v="${d.verdict}"`
      + ` data-mark="${key}" data-req="${d.reqId}" title="${d.reqId} · ${d.verdict}">`
      + esc(text.slice(start, end)) + '</span>');
    pos = end;
  });
  if (pos < text.length) parts.push(esc(text.slice(pos)));
  return parts.join('');
}

/* One surface, no modes: the text is always editable in place, always
   decorated, always selectable.

   The contract with React: the decorated HTML is rendered synchronously (no
   first-frame blank), but the string is FROZEN while the user is typing or
   while a save is in flight — React only repaints when the __html value
   changes, so freezing the value is what keeps the caret and the in-progress
   keystrokes safe. The fresh decorations land on the first render after the
   server has echoed the user's text back. */
const FileText = React.memo(function FileText({ path, text, decorations, hitKey, onPick, onSave }) {
  const ref = useRef(null);
  const dirty = useRef(false);
  const timer = useRef(null);
  const pending = useRef(null);      // text sent to the server, awaiting echo
  const lastHtml = useRef('');
  const [focused, setFocused] = useState(false);

  if (pending.current !== null && pending.current === text) pending.current = null;
  const frozen = focused || pending.current !== null;
  const html = frozen ? lastHtml.current : buildHtml(text || '', decorations, hitKey);
  lastHtml.current = html;

  const flush = () => {
    clearTimeout(timer.current);
    if (!dirty.current || !ref.current) return;
    dirty.current = false;
    const now = ref.current.textContent || '';
    pending.current = now;
    onSave(path, now);
  };

  return (
    <div
      className="filetext"
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      ref={ref}
      data-path={path}
      dangerouslySetInnerHTML={{ __html: html }}
      onFocus={() => setFocused(true)}
      onInput={() => {
        dirty.current = true;
        clearTimeout(timer.current);
        timer.current = setTimeout(flush, 1200);        // save while you pause
      }}
      onBlur={() => { flush(); setFocused(false); }}
      onClick={(e) => {
        const m = e.target.closest && e.target.closest('[data-req]');
        if (m) onPick(m.dataset.req);
      }}
    />
  );
});

export default function Workspace({ snap, selected, focus, onSelectReq, onFreeze, onAnchor, onSave }) {
  const files = (snap && snap.files) || [];
  const { byFile, scopes } = useDecorations(snap && snap.requirements, selected);
  const [hitKey, setHitKey] = useState(null);
  const [sel, setSel] = useState(null);       // {text, file, x, y}
  const [anchor, setAnchor] = useState(null); // {kind, text, file}
  const [instruction, setInstruction] = useState('');
  const scrollRef = useRef(null);

  useEffect(() => {
    if (!focus || focus.kind !== 'artifact') return;
    const key = `${focus.reqId}:${focus.start}`;
    const root = scrollRef.current;
    if (!root) return;
    const el = root.querySelector(`[data-mark="${key}"]`)
      || root.querySelector(`[data-file="${focus.file}"]`);
    if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setHitKey(key);
    const t = setTimeout(() => setHitKey(null), 2400);
    return () => clearTimeout(t);
  }, [focus]);

  /* Select a passage -> act on it. The selection toolbar and direct typing
     live on the same surface; neither hides the other. */
  const readSelection = () => {
    const s = window.getSelection();
    if (!s || s.isCollapsed || !s.rangeCount) { setSel(null); return; }
    const text = s.toString().trim();
    const root = scrollRef.current;
    if (!text || !root || !root.contains(s.anchorNode)) { setSel(null); return; }
    let node = s.anchorNode;
    while (node && node !== root && !(node.dataset && (node.dataset.path || node.dataset.file))) {
      node = node.parentNode;
    }
    const file = node && node.dataset
      ? (node.dataset.path || node.dataset.file)
      : (files[0] || {}).path;
    const rect = s.getRangeAt(0).getBoundingClientRect();
    const box = root.getBoundingClientRect();
    setSel({
      text, file,
      x: rect.left - box.left + rect.width / 2 + root.scrollLeft,
      y: rect.top - box.top + root.scrollTop - 4,
    });
  };

  const act = (kind) => {
    if (!sel) return;
    if (kind === 'freeze') {
      onFreeze(sel.text, sel.file);
      setSel(null);
      window.getSelection().removeAllRanges();
      return;
    }
    setAnchor({ kind, text: sel.text, file: sel.file });
    setInstruction('');
    setSel(null);
  };

  const submitAnchor = () => {
    if (!anchor || !instruction.trim()) return;
    onAnchor(anchor.kind, anchor.text, anchor.file, instruction.trim());
    setAnchor(null);
    setInstruction('');
  };

  return (
    <div className="col">
      <div className="colhead">Workspace</div>
      {anchor && (
        <div className="anchorbar">
          <span className="muted" style={{ fontSize: 11 }}>
            {anchor.kind === 'replace' ? 'replace' : 'insert after'}
          </span>
          <q>{anchor.text.slice(0, 60)}</q>
          <input
            autoFocus
            value={instruction}
            placeholder={anchor.kind === 'replace' ? 'how should it change?' : 'what goes here?'}
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitAnchor();
              if (e.key === 'Escape') setAnchor(null);
            }}
          />
          <button className="ghostbtn" onClick={submitAnchor}>Send ⏎</button>
          <button className="linkbtn" onClick={() => setAnchor(null)}>esc</button>
        </div>
      )}
      <div
        className="scroll"
        ref={scrollRef}
        style={{ position: 'relative' }}
        onMouseUp={readSelection}
        onKeyUp={(e) => { if (e.shiftKey || e.key === 'Shift') readSelection(); }}
      >
        {sel && (
          <div className="seltools"
               style={{ left: sel.x, top: sel.y }}
               onMouseDown={(e) => e.preventDefault()} /* keep focus & selection */>
            <button onClick={() => act('freeze')}
                    title="Lock this text: the edit tool refuses any write that removes it">
              ❄ Freeze
            </button>
            <button onClick={() => act('replace')} title="Ask the agent to replace exactly this passage">
              Replace…
            </button>
            <button onClick={() => act('insert')} title="Ask the agent to add something right after this">
              Insert after…
            </button>
          </div>
        )}
        {files.length === 0 ? (
          <div className="empty">
            workspace is empty — the agent has not written a file yet
          </div>
        ) : (
          <div className="doc">
            {files.map((f) => (
              <div
                key={f.path}
                className={`sec ${scopes[f.path] ? `scope-${scopes[f.path]}` : ''}`}
                data-file={f.path}
              >
                <h2>
                  {f.path}
                  <span className="count">{words(f.text)} words</span>
                </h2>
                <FileText
                  path={f.path}
                  text={f.text || ''}
                  decorations={byFile[f.path]}
                  hitKey={hitKey}
                  onPick={onSelectReq}
                  onSave={onSave}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
