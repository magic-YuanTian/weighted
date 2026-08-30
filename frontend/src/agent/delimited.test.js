import { parseDelimited, shapeTable, writeField, encodeField } from './delimited';

const vals = (rows) => rows.map((r) => r.map((c) => c.v));

test('a quoted field keeps its commas, its quotes and its newlines', () => {
  const text = 'name,note\n"Doe, Jane","she said ""hi""\nagain"\n';
  expect(vals(parseDelimited(text, ','))).toEqual([
    ['name', 'note'],
    ['Doe, Jane', 'she said "hi"\nagain'],
  ]);
});

test('a file ending in a newline has no phantom last row', () => {
  expect(parseDelimited('a,b\n1,2\n', ',')).toHaveLength(2);
  expect(parseDelimited('a,b\n1,2', ',')).toHaveLength(2);
});

test('every cell carries the span it occupied, CRLF included', () => {
  const text = 'a,b\r\n1,"x,y"\r\n';
  const rows = parseDelimited(text, ',');
  expect(vals(rows)).toEqual([['a', 'b'], ['1', 'x,y']]);
  expect(text.slice(rows[0][1].start, rows[0][1].end)).toBe('b');
  expect(text.slice(rows[1][1].start, rows[1][1].end)).toBe('"x,y"');
});

test('a short row is padded, and the padding is not a field', () => {
  const t = shapeTable('a,b,c\n1,2\n', ',');
  expect(t.width).toBe(3);
  expect(t.body[0][2]).toBeNull();
});

test('a column is numeric only when the whole column is', () => {
  const t = shapeTable('n,mixed\n1,1\n2,two\n', ',');
  expect(t.numeric).toEqual([true, false]);
});

test('writing one field leaves the rest of the file byte for byte', () => {
  const text = 'name,city\r\n"Doe, Jane",  MOBILE  \r\nAmy,ERIE\r\n';
  const rows = parseDelimited(text, ',');
  const out = writeField(text, rows[1][1], 'Mobile', ',');
  expect(out).toBe('name,city\r\n"Doe, Jane",Mobile\r\nAmy,ERIE\r\n');
});

test('a written field is quoted only when it has to be', () => {
  expect(encodeField('  MOBILE  ', ',')).toBe('  MOBILE  ');
  expect(encodeField('Doe, Jane', ',')).toBe('"Doe, Jane"');
  expect(encodeField('say "hi"', ',')).toBe('"say ""hi"""');
  expect(encodeField('a,b', '\t')).toBe('a,b');
});

test('an edited field survives the round trip through the parser', () => {
  const text = 'a,b\n1,2\n';
  const rows = parseDelimited(text, ',');
  const out = writeField(text, rows[1][0], 'x,"y"', ',');
  expect(vals(parseDelimited(out, ','))).toEqual([['a', 'b'], ['x,"y"', '2']]);
});
