/* CSV/TSV, in one place. Both surfaces that show a delimited file — the
   read-only attachment sheet and the editable workspace — need the same three
   things: a parser that does not lie about the columns, a rule for which
   columns are numbers, and a way to write one field back without rewriting the
   rest of the file. */

const DELIMITED = /\.(csv|tsv)$/i;

export const isDelimited = (name) => DELIMITED.test(name || '');
export const sepFor = (name) => (/\.tsv$/i.test(name || '') ? '\t' : ',');

/* RFC 4180, not split(','). A quoted field keeps its commas and its newlines
   and "" is one literal quote; splitting on every comma shifts every column to
   the right of the first quoted field — silently, and only on some files.

   Every cell also carries the span it occupied in the source text, quotes
   included: {v, start, end}. That span is what lets an edit be spliced back
   into the file, and what a verifier's character offsets are matched against. */
export function parseDelimited(text, sep) {
  const rows = [];
  let row = [];
  let value = '';
  let start = 0;
  let quoted = false;
  const close = (end) => { row.push({ v: value, start, end }); value = ''; };
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch !== '"') { value += ch; continue; }
      if (text[i + 1] === '"') { value += '"'; i += 1; continue; }
      quoted = false;
      continue;
    }
    if (ch === '"' && value === '') { quoted = true; continue; }
    if (ch === sep) { close(i); start = i + 1; continue; }
    if (ch === '\r') continue;
    if (ch === '\n') {
      close(text[i - 1] === '\r' ? i - 1 : i);
      rows.push(row);
      row = [];
      start = i + 1;
      continue;
    }
    value += ch;
  }
  // A file that ends in a newline has already closed its last row. Pushing
  // here unconditionally is what hangs a phantom empty row under every table.
  if (value !== '' || row.length) { close(text.length); rows.push(row); }
  return rows;
}

/* Rows padded to a common width, so a ragged file cannot slide values under
   the wrong heading. A pad is null, not an empty cell: nothing in the file
   stands there, so there is nothing to edit or to point evidence at. */
export function shapeTable(text, sep) {
  const rows = parseDelimited(text, sep);
  if (!rows.length) return null;
  const width = rows.reduce((w, r) => Math.max(w, r.length), 0);
  const pad = (r) => (r.length === width
    ? r
    : r.concat(Array.from({ length: width - r.length }, () => null)));
  const body = rows.slice(1).map(pad);
  return { text, sep, head: pad(rows[0]), body, width, numeric: numericColumns(body, width) };
}

/* A column of numbers reads as a column only when the digits line up, so the
   alignment is decided per column over the whole file, never per cell: one
   right-aligned value in a column of left-aligned ones is worse than neither. */
const NUM = /^-?\$?[\d,]*\.?\d+%?$/;

export function numericColumns(rows, width) {
  const out = [];
  for (let c = 0; c < width; c += 1) {
    let seen = 0;
    let numeric = 0;
    for (let r = 0; r < rows.length; r += 1) {
      const cell = rows[r][c];
      const v = cell ? cell.v.trim() : '';
      if (!v) continue;
      seen += 1;
      if (NUM.test(v)) numeric += 1;
    }
    out.push(seen > 0 && seen === numeric);
  }
  return out;
}

/* Quote only what has to be quoted. A value carrying the separator, a quote or
   a newline would otherwise come back as two fields or two rows; a value with
   nothing but stray spaces is left exactly as typed, because on these tasks
   the stray spaces are the point. */
export function encodeField(v, sep) {
  return /["\r\n]/.test(v) || v.indexOf(sep) !== -1 ? `"${v.replace(/"/g, '""')}"` : v;
}

/* One field back into the file, at its own offsets. Everything else — the
   other rows, the line endings, the quoting style the file arrived with —
   survives byte for byte, which matters because the agent reads this file
   again and a diff it did not make is noise it has to explain. */
export function writeField(text, cell, next, sep) {
  return text.slice(0, cell.start) + encodeField(next, sep) + text.slice(cell.end);
}
