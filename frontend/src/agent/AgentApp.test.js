/* Drives the v4 agent UI against a faked /api/agent the way a participant
   would: type the task, watch it run, click a verdict, land on its evidence.
   A build only proves it compiles; this proves it runs. */
import React from 'react';
import '@testing-library/jest-dom';   // no src/setupTests.js in this project
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import AgentApp from './AgentApp';

const REQS = [
  {
    id: 'R1', type: 'length', kind: 'artifact', verify: 'code', weight: 1, status: 'active',
    text: 'Cover letter is 350-500 words', params: { min: 350, max: 500 },
    scope: { kind: 'section', name: 'Cover Letter' }, enforce: false,
    source: { kind: 'extracted', briefSpan: [0, 39] },
    report: {
      verdict: 'violated', detail: '[cover_letter.md] 512 words (target 350-500)',
      checkedAtStep: 2, evidence: [
        { kind: 'artifact', file: 'cover_letter.md', start: 2, end: 14, quote: 'cutting-edge' },
        { kind: 'step', stepId: 2, action: 'edit_file', quote: 'last edit to this scope' },
      ],
    },
  },
  {
    id: 'R2', type: 'tool-use', kind: 'process', verify: 'rule', weight: 1, status: 'active',
    text: 'Run the word-count check before finishing', params: {}, enforce: false,
    scope: { kind: 'trajectory' }, source: { kind: 'extracted', briefSpan: [40, 70] },
    report: { verdict: 'unverified', detail: 'run_check not called yet', evidence: [] },
  },
];

const EMPTY = {
  sessionId: 's1', brief: '', status: 'idle', stepCount: 0, gateOn: true,
  requirements: [], counts: {}, blocking: [], files: [], questions: [], events: [],
};

const RUNNING = {
  ...EMPTY,
  brief: 'Write a package.', stepCount: 2,
  requirements: REQS, counts: { violated: 1, unverified: 1 }, blocking: ['R1'],
  files: [{ path: 'cover_letter.md', text: 'A cutting-edge letter body.' }],
  questions: [{
    id: 'Q1', text: 'Hard limit or guideline?', options: ['hard limit', 'guideline'],
    affects: 'R1', answer: null,
  }],
  events: [
    { i: 0, type: 'user', text: 'The cover letter must be 350-500 words. Then run the word-count check.' },
    { i: 1, type: 'extracted', ids: ['R1', 'R2'], unmapped: 2 },
    {
      i: 2, type: 'step', step: 2, action: 'edit_file', argSummary: 'cover_letter.md',
      thought: 'Rewrote paragraph two.', observation: 'wrote cover_letter.md (512 words)',
      meta: { ok: true, kind: 'edit', path: 'cover_letter.md', add: 12, del: 4 },
      chips: [{ id: 'R1', verdict: 'violated', from: 'satisfied' }], pinned: [],
    },
  ],
};

const CONTEXT = {
  total: 900,
  parts: [{
    key: 'reminders', label: 'Reminders (pinned)', tokens: 60, detail: 'R1',
    text: 'REMINDERS\n  R1 [length] 350-500',
  }],
};

const body = (obj) => Promise.resolve({ ok: true, json: () => Promise.resolve(obj) });

beforeAll(() => { Element.prototype.scrollIntoView = jest.fn(); });

beforeEach(() => {
  window.location.hash = '#agent';
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/context')) return body(CONTEXT);
    if (url.includes('/step')) return body({ events: [], snapshot: RUNNING, canContinue: false });
    if (url.includes('/answer')) return body({ requirement: REQS[0], snapshot: RUNNING });
    return body(RUNNING);
  });
});

async function startSession() {
  render(<AgentApp />);
  const composer = await screen.findByPlaceholderText(/Describe the task/);
  fireEvent.change(composer, { target: { value: 'Write an application package.' } });
  fireEvent.keyDown(composer, { key: 'Enter' });
  return composer;
}

test('typing the task starts the run: message, then steps', async () => {
  await startSession();
  await waitFor(() => {
    const urls = global.fetch.mock.calls.map((c) => String(c[0]));
    expect(urls.some((u) => u.includes('/message'))).toBe(true);
    expect(urls.some((u) => u.includes('/step'))).toBe(true);
  });
  // requirements extracted from that first message show up in the rail
  expect(await screen.findByText('Cover letter is 350-500 words')).toBeInTheDocument();
  expect(screen.getByText(/Starting on it/)).toBeInTheDocument();
});

test('a clarification question is asked in the chat and never blocks the run', async () => {
  await startSession();
  expect(await screen.findByText('Hard limit or guideline?')).toBeInTheDocument();
  expect(global.fetch.mock.calls.some((c) => String(c[0]).includes('/step'))).toBe(true);

  fireEvent.click(screen.getByText('hard limit'));
  await waitFor(() => expect(global.fetch.mock.calls.some(
    (c) => String(c[0]).includes('/answer'))).toBe(true));
});

test('a requirement has exactly two actions: open it, and highlight it', async () => {
  await startSession();
  const row = await screen.findByText('Cover letter is 350-500 words');
  expect(screen.getAllByText(/512 words/).length).toBeGreaterThan(0);

  fireEvent.click(screen.getByLabelText('highlight R1'));
  await waitFor(() => {
    const call = global.fetch.mock.calls.find((c) => String(c[0]).includes('/requirement'));
    expect(JSON.parse(call[1].body)).toMatchObject({ action: 'weight', id: 'R1', weight: 2 });
  });

  fireEvent.click(row);
  // the drawer slides open (it is always in the tree; opening reveals it)
  await waitFor(() => expect(document.querySelector('.drawerwrap.open')).not.toBeNull());
  const drawer = document.querySelector('.drawerwrap.open');
  expect(drawer.textContent).toMatch(/Not met. 512 words/);
  expect(drawer.textContent).toMatch(/word count is checked/);
  expect(screen.queryByText('show in the document')).toBeNull();

  // one click synchronised every view: the document scrolled to the evidence,
  // and the step that caused the verdict is marked in the log
  await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
  expect(document.querySelector('.step.rel')).not.toBeNull();

  const marks = document.querySelectorAll('.mark[data-v="violated"]');
  expect(marks.length).toBe(1);
  expect(marks[0].textContent).toBe('cutting-edge');
});

test('selecting text in the workspace offers freeze, replace and insert', async () => {
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');

  // jsdom does not implement selection geometry, so stand in for the drag
  const pre = document.querySelector('.filetext');
  const realGetSelection = window.getSelection;
  window.getSelection = () => ({
    isCollapsed: false,
    rangeCount: 1,
    anchorNode: pre,
    toString: () => 'cutting-edge',
    getRangeAt: () => ({ getBoundingClientRect: () => ({ left: 10, top: 10, width: 40, height: 12 }) }),
    removeAllRanges: () => {},
  });
  fireEvent.mouseUp(pre);

  expect(await screen.findByText('Replace…')).toBeInTheDocument();
  expect(screen.getByText('Insert after…')).toBeInTheDocument();
  fireEvent.click(screen.getByText('❄ Freeze'));

  await waitFor(() => {
    const call = global.fetch.mock.calls.find((c) => String(c[0]).includes('/requirement'));
    const payload = JSON.parse(call[1].body);
    expect(payload.action).toBe('add');
    expect(payload.requirement.type).toBe('preserve');
    expect(payload.requirement.enforce).toBe(true);
    expect(payload.requirement.params.phrases[0]).toBe('cutting-edge');
    expect(payload.requirement.scope).toEqual({ kind: 'file', name: 'cover_letter.md' });
  });
  window.getSelection = realGetSelection;
});

test('the context inspector shows the exact reminder text that gets injected', async () => {
  window.location.hash = '#agent/dev';   // researcher tool, not on the study screen
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');
  fireEvent.click(screen.getByText('context'));
  const panel = await screen.findByRole('dialog');
  expect(within(panel).getByText(/REMINDERS/)).toBeInTheDocument();
});

test('#agent/review still runs the staged flow, for the other study condition', async () => {
  window.location.hash = '#agent/review';
  global.fetch = jest.fn((url) => {
    if (url.includes('/extract')) {
      return body({ requirements: REQS, questions: [], coverage: { mapped: 2, total: 3, unmapped: [] } });
    }
    if (url.includes('/session')) return body(EMPTY);
    return body(RUNNING);
  });
  render(<AgentApp />);
  fireEvent.click(screen.getByText('load an example task'));
  fireEvent.click(screen.getByText(/Extract requirements/));
  fireEvent.click(await screen.findByText(/Start run/));
  expect(await screen.findByText('Cover letter is 350-500 words')).toBeInTheDocument();
});

test('typing straight into the document saves it — no edit mode', async () => {
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');

  const surface = document.querySelector('.filetext');
  expect(surface.getAttribute('contenteditable')).toBe('true');
  expect(surface.textContent).toContain('cutting-edge');

  surface.textContent = 'A plain letter body.';
  fireEvent.input(surface);
  fireEvent.blur(surface);

  await waitFor(() => {
    const call = global.fetch.mock.calls.find((c) => String(c[0]).includes('/file'));
    expect(call).toBeTruthy();
    expect(JSON.parse(call[1].body)).toMatchObject({
      path: 'cover_letter.md', text: 'A plain letter body.',
    });
  });
});

test('no title bar, and the finish gate explains itself where it happens', async () => {
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');
  expect(document.querySelector('.topbar')).toBeNull();
  expect(screen.queryByText('Finish gate')).toBeNull();
  expect(screen.queryByText(/Run stream/)).toBeNull();
  expect(screen.queryByText(/context/)).toBeNull();
});

test('a sent message shows up instantly, before the server answers', async () => {
  // hold /message open: this is the window in which the screen used to look dead
  let release;
  const held = new Promise((r) => { release = r; });
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/message')) return held.then(() => ({ ok: true, json: () => Promise.resolve(RUNNING) }));
    if (url.includes('/step')) return body({ events: [], snapshot: RUNNING, canContinue: false });
    return body(RUNNING);
  });

  render(<AgentApp />);
  const composer = await screen.findByPlaceholderText(/Describe the task/);
  fireEvent.change(composer, { target: { value: 'Write an application package.' } });
  fireEvent.keyDown(composer, { key: 'Enter' });

  // still in flight: the message is on screen and the app says what it is doing
  expect(await screen.findByText('Write an application package.')).toBeInTheDocument();
  expect(screen.getByText(/reading the task/)).toBeInTheDocument();

  release();
  await waitFor(() => expect(global.fetch.mock.calls.some(
    (c) => String(c[0]).includes('/step'))).toBe(true));
});

test('the footer shows every step, and a column jumps back to it', async () => {
  const MULTI = {
    ...RUNNING,
    stepCount: 4,
    events: [
      ...RUNNING.events,
      { i: 3, type: 'step', step: 3, action: 'run_check', argSummary: '', meta: { ok: true, kind: 'check' }, chips: [{ id: 'R2', verdict: 'satisfied' }], pinned: [] },
      { i: 4, type: 'step', step: 4, action: 'edit_file', argSummary: 'cover_letter.md', meta: { ok: true, kind: 'edit' }, chips: [{ id: 'R1', verdict: 'satisfied' }], pinned: [] },
    ],
  };
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/step')) return body({ events: [], snapshot: MULTI, canContinue: false });
    return body(MULTI);
  });

  await startSession();
  await screen.findByText('Cover letter is 350-500 words');

  const cols = document.querySelectorAll('.stepcol');
  expect(cols.length).toBe(4);                        // steps 2, 3, 4, then now
  expect(cols[2].getAttribute('title')).toMatch(/step 4/);
  // the closing column is the live state, so a verdict set by the final judge
  // pass (not by any step) still fills the tape
  expect(cols[3].getAttribute('title')).toMatch(/^now/);

  fireEvent.click(cols[0]);
  await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
});

test('requirements keep their extraction order, whatever their verdicts are', async () => {
  // R1 satisfied, R2 violated: triage would put R2 first — position must win
  const FLIPPED = {
    ...RUNNING,
    requirements: [
      { ...REQS[0], report: { ...REQS[0].report, verdict: 'satisfied' } },
      { ...REQS[1], report: { ...REQS[1].report, verdict: 'violated' } },
    ],
  };
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/step')) return body({ events: [], snapshot: FLIPPED, canContinue: false });
    return body(FLIPPED);
  });

  await startSession();
  await screen.findByText('Cover letter is 350-500 words');
  const ids = Array.from(document.querySelectorAll('.rail .req .rid')).map((n) => n.textContent);
  expect(ids).toEqual(['R1', 'R2']);
});

test('clicking a requirement lights up the sentence it came from', async () => {
  await startSession();
  const row = await screen.findByText('Cover letter is 350-500 words');

  const span = document.querySelector('[data-brief="R1"]');
  expect(span).toBeTruthy();
  expect(span.textContent).toBe('The cover letter must be 350-500 words.');
  expect(span.className).not.toMatch(/lit/);

  fireEvent.click(row);
  await waitFor(() => expect(
    document.querySelector('[data-brief="R1"]').className).toMatch(/lit/));

  // and back the other way: clicking the sentence selects the requirement
  fireEvent.click(document.querySelector('[data-brief="R2"]'));
  await waitFor(() => expect(
    document.querySelector('[data-brief="R2"]').className).toMatch(/lit/));
});

test('when everything is satisfied, the last column of the history is full', async () => {
  const ALL_MET = {
    ...RUNNING,
    // the steps only ever reported R1 — R2 was satisfied by the judge pass
    // after the run, which no step carries
    requirements: REQS.map((r) => ({ ...r, report: { ...r.report, verdict: 'satisfied' } })),
    counts: { satisfied: 2 },
    events: [
      ...RUNNING.events,
      { i: 3, type: 'step', step: 3, action: 'run_check', argSummary: '', meta: { ok: true, kind: 'check' }, chips: [{ id: 'R1', verdict: 'satisfied' }], pinned: [] },
    ],
  };
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/step')) return body({ events: [], snapshot: ALL_MET, canContinue: false });
    return body(ALL_MET);
  });

  await startSession();
  await screen.findByText('Cover letter is 350-500 words');
  const cols = document.querySelectorAll('.stepcol');
  const last = cols[cols.length - 1];
  expect(last.getAttribute('title')).toBe('now · 2 of 2 met');
  expect(last.querySelector('i[data-v="satisfied"]').style.height).toBe('100%');
});

test('the list edits like a list: hover icons, inline rename, add at the foot', async () => {
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');

  // rename in place
  fireEvent.click(screen.getByLabelText('edit R1'));
  const box = screen.getByDisplayValue('Cover letter is 350-500 words');
  fireEvent.change(box, { target: { value: 'Cover letter is 300-400 words' } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => {
    const call = global.fetch.mock.calls.find((c) => String(c[0]).includes('/requirement'));
    expect(JSON.parse(call[1].body)).toMatchObject({
      action: 'update', id: 'R1', requirement: { text: 'Cover letter is 300-400 words' },
    });
  });

  // delete from the row
  fireEvent.click(screen.getByLabelText('delete R2'));
  await waitFor(() => {
    const calls = global.fetch.mock.calls.filter((c) => String(c[0]).includes('/requirement'));
    expect(JSON.parse(calls[calls.length - 1][1].body)).toMatchObject({ action: 'delete', id: 'R2' });
  });

  // grow the list where lists grow: at the bottom
  fireEvent.click(screen.getByText('Add a requirement'));
  const add = screen.getByPlaceholderText('what must be true of the result?');
  fireEvent.change(add, { target: { value: 'Mention the team by name' } });
  fireEvent.keyDown(add, { key: 'Enter' });
  await waitFor(() => {
    const calls = global.fetch.mock.calls.filter((c) => String(c[0]).includes('/requirement'));
    expect(JSON.parse(calls[calls.length - 1][1].body)).toMatchObject({
      action: 'add', requirement: { text: 'Mention the team by name' },
    });
  });
});

test('the log counts its steps out loud', async () => {
  await startSession();
  await screen.findByText('Cover letter is 350-500 words');
  expect(screen.getByText('Step 2')).toBeInTheDocument();
  expect(screen.getByText('Revised the cover letter')).toBeInTheDocument();
});

test('a step explains itself in words, and the raw checker output stays out of sight', async () => {
  const SUMMARIZED = {
    ...RUNNING,
    events: [
      ...RUNNING.events,
      {
        i: 3, type: 'step', step: 3, action: 'run_check', argSummary: '',
        observation: 'R1  FAIL  [cover_letter.md] 342 words (target 350-500)',
        summary: '3 met · 1 not met\nR1: 342 words (target 350-500)',
        meta: { ok: true, kind: 'check' }, chips: [], pinned: [],
      },
    ],
  };
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/step')) return body({ events: [], snapshot: SUMMARIZED, canContinue: false });
    return body(SUMMARIZED);
  });

  await startSession();
  // the collapsed row already carries the headline
  const row = await screen.findByText('Checked the requirements — 3 met · 1 not met');
  fireEvent.click(row);
  // expanded: the plain-language line, and no raw checker table
  expect(await screen.findByText('R1: 342 words (target 350-500)')).toBeInTheDocument();
  expect(document.querySelector('.obs')).toBeNull();
});

test('verdicts set outside any step still get their labels in the log', async () => {
  const WITH_RECHECK = {
    ...RUNNING,
    events: [
      ...RUNNING.events,
      { i: 3, type: 'recheck', judge: true,
        chips: [{ id: 'R2', verdict: 'satisfied', from: 'unverified' }] },
      { i: 4, type: 'user-edit', path: 'cover_letter.md',
        chips: [{ id: 'R1', verdict: 'satisfied', from: 'violated' }] },
    ],
  };
  global.fetch = jest.fn((url) => {
    if (url.includes('/session')) return body(EMPTY);
    if (url.includes('/step')) return body({ events: [], snapshot: WITH_RECHECK, canContinue: false });
    return body(WITH_RECHECK);
  });

  await startSession();
  const judged = await screen.findByText('Reviewed the remaining requirements');
  expect(judged.parentElement.querySelector('.rchip').textContent).toBe('R2');
  const edited = screen.getByText('You edited the cover letter');
  expect(edited.parentElement.querySelector('.rchip').textContent).toBe('R1');
});
