import React, { useEffect, useMemo, useRef, useState } from 'react';
import { isDelimited, parseDelimited, sepFor, shapeTable, writeField } from './delimited';

const words = (t) => (t || '').split(/\s+/).filter(Boolean).length;

/* Prose gets the serif; a source file gets a code face. Getting this wrong
   makes indentation invisible, which is most of what reading code is.

   A delimited file is not either one: cleaned.csv is a table, and a table read
   as lines of commas is a table nobody can read a column out of -- monospace
   only lines the commas up, not the fields between them. So a CSV is drawn as
   a real table here too, and stays editable, cell by cell. Text is one click
   away for the edits a grid has no gesture for: a new row, a moved column. */
const CODE_EXT = /\.(py|js|jsx|ts|tsx|java|go|rb|rs|c|h|cpp|cc|hpp|cs|php|sh|sql|json|ya?ml|toml|ini|css|html?|xml|r|swift|kt|scala|pl|lua|csv|tsv)$/i;
const isCode = (path) => CODE_EXT.test(path || '');
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
      className={`filetext ${isCode(path) ? 'code' : ''}`}
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

/* A page at a time. The shipped tables run to 414 rows and a run replaces the
   snapshot on every step, so the whole grid is a few thousand cells React
   would reconcile again on each one. The agent still reads the file whole. */
const CSV_PAGE = 200;

/* Which decoration, if any, lands on this cell. Evidence is character offsets
   into the file, so a span is matched against the span the field occupied in
   the source — the cell is the smallest thing a table can underline. */
function markFor(decorations, cell) {
  if (!cell || !decorations) return null;
  for (let i = 0; i < decorations.length; i += 1) {
    const d = decorations[i];
    if (d.start < cell.end && d.end > cell.start) return d;
  }
  return null;
}

function Cell({ tag: Tag, cell, numeric, mark, hitKey, onPick, onFocus, onCommit }) {
  const key = mark ? `${mark.reqId}:${mark.start}` : null;
  const cls = [
    numeric ? 'num' : '',
    cell ? '' : 'pad',
    mark ? 'mark' : '',
    mark && hitKey === key ? 'hit' : '',
  ].filter(Boolean).join(' ');
  return (
    <Tag
      className={cls}
      data-v={mark ? mark.verdict : undefined}
      data-mark={key || undefined}
      data-req={mark ? mark.reqId : undefined}
      title={mark ? `${mark.reqId} · ${mark.verdict}` : undefined}
      contentEditable={!!cell}
      suppressContentEditableWarning
      spellCheck={false}
      onFocus={onFocus}
      onBlur={(e) => onCommit(cell, e.currentTarget.textContent || '')}
      /* Enter commits the cell rather than opening a second line inside it —
         a field that spans two lines is a quoted newline, not a keystroke.
         Escape puts the cell back the way the file has it. */
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); e.currentTarget.blur(); }
        if (e.key === 'Escape') {
          e.preventDefault();
          e.currentTarget.textContent = cell ? cell.v : '';
          e.currentTarget.blur();
        }
      }}
      onClick={() => { if (mark) onPick(mark.reqId); }}
    >
      {cell ? cell.v : ''}
    </Tag>
  );
}

/* The same surface as FileText, in two dimensions: always editable, always
   decorated, never a mode. A committed cell is spliced back at that field's
   own offsets, so the other rows, the line endings and the quoting the file
   arrived with survive an edit untouched — the agent reads this file again,
   and a diff nobody made is a diff it has to explain.

   The contract with React is FileText's, one level down. `draft` is our own
   text, ahead of the server, and it always wins: it is what keeps the offsets
   of the next cell correct when two cells are edited before the save lands.
   A server update while a cell has focus is held back instead, because that
   is the one repaint that would eat keystrokes mid-word. */
const CsvTable = React.memo(function CsvTable({ path, text, decorations, hitKey, onPick, onSave }) {
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState(null);
  const [shown, setShown] = useState(CSV_PAGE);
  const held = useRef(text);

  // The server is authoritative once it has spoken: its echo of our own save
  // and a write by the agent both retire the draft.
  useEffect(() => { setDraft(null); }, [text]);

  const source = draft !== null ? draft : text;
  const live = focused && draft === null ? held.current : source;
  held.current = live;

  const sep = sepFor(path);
  const table = useMemo(() => shapeTable(live, sep), [live, sep]);

  if (!table) return null;

  const commit = (cell, next) => {
    setFocused(false);
    if (!cell || next === cell.v) return;
    const out = writeField(table.text, cell, next, sep);
    setDraft(out);
    onSave(path, out);
  };
  const focus = () => setFocused(true);
  const rows = table.body.slice(0, shown);
  const hidden = table.body.length - rows.length;

  return (
    <>
      <div className="csvwrap">
        <table className="csv" data-path={path}>
          <thead>
            <tr>
              <th className="ln" scope="col" />
              {table.head.map((c, j) => (
                <Cell
                  key={j} tag="th" cell={c} numeric={table.numeric[j]}
                  mark={markFor(decorations, c)} hitKey={hitKey}
                  onPick={onPick} onFocus={focus} onCommit={commit}
                />
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td className="ln">{i + 1}</td>
                {row.map((c, j) => (
                  <Cell
                    key={j} tag="td" cell={c} numeric={table.numeric[j]}
                    mark={markFor(decorations, c)} hitKey={hitKey}
                    onPick={onPick} onFocus={focus} onCommit={commit}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <p className="csvmore">
          {hidden.toLocaleString()} more rows not shown.{' '}
          <button className="linkbtn" onClick={() => setShown((n) => n + CSV_PAGE * 4)}>
            show more
          </button>
        </p>
      )}
    </>
  );
});


export default function Workspace({ snap, selected, focus, onSelectReq, onFreeze, onAnchor, onSave }) {
  // memoised for the shape pass below: a fresh [] each render re-parses every file
  const files = useMemo(() => (snap && snap.files) || [], [snap]);
  const { byFile, scopes } = useDecorations(snap && snap.requirements, selected);
  const [hitKey, setHitKey] = useState(null);
  const [sel, setSel] = useState(null);       // {text, file, x, y}
  const [anchor, setAnchor] = useState(null); // {kind, text, file}
  const [instruction, setInstruction] = useState('');
  const [asText, setAsText] = useState({});   // per file: the grid, or the raw text
  const scrollRef = useRef(null);

  /* The header count tells the truth about a delimited file: rows and columns.
     A line count is the wrong unit for a file whose fields may hold newlines,
     and it counts the header row as data. */
  const shapes = useMemo(() => {
    const out = {};
    files.forEach((f) => {
      if (!isDelimited(f.path)) return;
      const rows = parseDelimited(f.text || '', sepFor(f.path));
      out[f.path] = {
        rows: Math.max(0, rows.length - 1),
        cols: rows.reduce((w, r) => Math.max(w, r.length), 0),
      };
    });
    return out;
  }, [files]);

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
        {sel && onFreeze && onAnchor && (
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
            {files.map((f) => {
              const shape = shapes[f.path];
              // an empty file has no columns to draw: it stays text until it has some
              const grid = !asText[f.path] && shape && shape.cols > 0;
              return (
                <div
                  key={f.path}
                  className={`sec ${isCode(f.path) ? 'codesec' : ''} `
                    + `${scopes[f.path] ? `scope-${scopes[f.path]}` : ''}`}
                  data-file={f.path}
                >
                  <h2>
                    {f.path}
                    <span className="count">
                      {grid
                        ? `${shape.rows.toLocaleString()} rows · ${shape.cols} cols`
                        : (isCode(f.path)
                          ? `${(f.text || '').split('\n').length} lines`
                          : `${words(f.text)} words`)}
                    </span>
                    {isDelimited(f.path) && (
                      <button
                        className="linkbtn viewtoggle"
                        title={grid
                          ? 'edit the raw file — for a new row or a moved column'
                          : 'back to the table'}
                        onClick={() => setAsText((m) => ({ ...m, [f.path]: !m[f.path] }))}
                      >
                        {grid ? 'text' : 'table'}
                      </button>
                    )}
                  </h2>
                  {grid ? (
                    <CsvTable
                      path={f.path}
                      text={f.text || ''}
                      decorations={byFile[f.path]}
                      hitKey={hitKey}
                      onPick={onSelectReq}
                      onSave={onSave}
                    />
                  ) : (
                    <FileText
                      path={f.path}
                      text={f.text || ''}
                      decorations={byFile[f.path]}
                      hitKey={hitKey}
                      onPick={onSelectReq}
                      onSave={onSave}
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
