# WeightText

A requirement-steered writing agent. The user states a task in chat; the system
extracts the task's requirements into an editable list, an agent works on the
task step by step, and every step is checked against the requirements — so the
user always sees what is met, what is not, and which step made it so.

## How it works

- **Extract** — the first chat message is parsed into a requirement list
  (length limits, banned/required phrases, structural rules, tone, process
  rules). Each requirement stays linked to the sentence it came from, and the
  list is fully editable: add, reword, delete. The run stops there and waits to
  be started — that first list is the thing the work will be measured against,
  and it should be read before there is work to read it against. Only there:
  every later message goes straight to an agent already working. (This is not
  the `#agent/review` condition, which is a separate screen before the session
  exists; this is the ordinary flow pausing once.)
- **Verify** — after every agent step, requirements are re-checked.
  Deterministic properties (word counts, phrases, structure) are checked by
  code; subjective ones (tone, content) are judged by the model. Every verdict
  carries evidence: a span in the document or a step in the log, one click away.
- **Steer** — highlight a requirement to have it repeated to the agent before
  every action; select text in the workspace to freeze it (edits that would
  remove it are rejected in the edit tool), or to ask for an anchored replace /
  insert. The finish gate holds the agent's "done" until everything checks out.

## Layout

    backend/
      server.py        Flask entry point (static build + API)
      llm_api.py       model access — the single place a model is wired in
      agent_routes.py  HTTP surface: /api/agent/*
      checker.py       deterministic text checks
      code_checker.py  deterministic checks on a Python deliverable
      table_checker.py deterministic checks on a CSV deliverable, read
                       against the source table it was made from
      setup_tasks.py   fetches the six benchmark tasks (see below)
      agent/           extraction, verification, tools, the step loop
      tasks/           the fetched tasks, their attachments and the
                       graders' gold answers (git-ignored)
      runs/            one directory per session: workspace, attachments,
                       and the event log (git-ignored)
    frontend/
      src/agent/       the three-pane UI: chat, workspace, requirements

## Setup

**Credentials.** Put your key in a `.env` at the repository root. It is
git-ignored; keep it that way.

    OPENAI_API_KEY=sk-...

`backend/llm_api.py` reads it from there and is the single place the model is
wired in. It targets `gpt-5.6-luna` by default; override with `WEIGHTTEXT_MODEL`
(and `OPENAI_BASE_URL` for an OpenAI-compatible gateway) rather than editing
the file. Any client satisfying the interface in that module's docstring works,
but **the model must support tool calling** — the agent has no other way to act.

**Backend** (Python 3.10+):

    uv venv --python 3.12 .venv                      # or: python -m venv .venv
    uv pip install --python .venv/bin/python -r backend/requirements.txt
    cd backend && ../.venv/bin/python server.py      # serves on :5091

**Frontend:**

    cd frontend
    npm install
    npm start                   # dev server on :3000, proxies /api to :5091

or `npm run build`, after which the backend serves the app on :5091 directly.

### The agent's terminal

The agent can run shell commands in its workspace, which is how the data tasks
get done properly: copy the attached source verbatim, then repair the copy one
column at a time with a real program, so the parts nobody asked it to change
stay byte-identical instead of being retyped from memory. Helper scripts belong
in `$SCRATCH` and the attachments are read-only at `$ATTACHMENTS` — every file
in the workspace itself is a deliverable and is checked as one.

A shell goes around the tool-level guarantees, so it does not get to keep what
it wrote unexamined. Every command is bracketed by a workspace snapshot: a file
that lost a protected phrase is put back — the same contract `edit_file` states
as a refusal, enforced one moment later because a shell cannot be asked first —
and whatever stands is verified by the step that ran it, so a `python3` script
that rewrites a table produces the same chips a hand edit would.

    WEIGHTTEXT_SHELL_TIMEOUT=120   # seconds one command may run (default)
    WEIGHTTEXT_SHELL=0             # withhold the tool entirely

On by default locally. Off by default as soon as `WEIGHTTEXT_PASSWORD` is set,
for the reason in the next section; `WEIGHTTEXT_SHELL=1` overrides that.

### Sharing it with other people

`server.py` serves the built frontend and the API from one port, so a single
process is the whole app. What it does not have is accounts: every run is a
long chain of large model calls billed to whoever owns `OPENAI_API_KEY`. Set a
shared password before the app is reachable from anywhere but this machine.

    export WEIGHTTEXT_PASSWORD='something-only-they-know'
    cd backend && ../.venv/bin/python server.py

Unset, there is no gate — local development behaves as it always did. Set, the
whole app answers 401 until the browser sends the password, over HTTP Basic,
so there is nothing to log into and no session to leak. Anyone with the
password can spend the key, so treat it as a key.

Setting it also withholds the agent's terminal, because a shell in the
workspace is a shell on this machine: reachable over a tunnel, with a password
shared among a few people, it is remote code execution for whoever holds that
password. The writing tasks do not need it. Turn it back on with
`WEIGHTTEXT_SHELL=1` only when you know who is on the other end.

On the same network that is already enough: the server binds `0.0.0.0`, so
others reach it at `http://<your-ip>:5091`. Leaving that network is the access
control.

For a link that works anywhere, put a tunnel in front of it rather than
opening a port on your router. The tunnel supplies HTTPS, which Basic auth
needs to not be sent in the clear:

    brew install cloudflared
    cloudflared tunnel --url http://localhost:5091

It prints a `https://<random>.trycloudflare.com` address. Send that and the
password. Stopping the tunnel withdraws the link immediately.

Two things this setup is not built for. Sessions live in `backend/runs/` on
local disk, so everyone shares one server and the work does not survive a
machine that goes to sleep. And a password shared among a few people is not a
substitute for per-user keys — for anything genuinely public, users need to
supply their own credentials and the API needs a rate limit.

### Benchmark tasks (optional)

The composer offers a picker of six evaluation tasks: three domains, two
instances each. They are not in this repository — two of the three source
benchmarks ship without a licence file, so nothing is redistributed here. Fetch
and assemble them once:

    cd backend && ../.venv/bin/python setup_tasks.py

| # | Task | Domain | Artifact | Reqs | What the agent's own run produced | How that was checked |
|---|------|--------|----------|------|------|------|
| 1 | CodeIF 358 | Code generation | longest subarray not divisible by k | 14 | **wrong** — returned 3 for `[-5,1,0], k=4` where the answer is 2; a second run left a file that does not parse | executed against a brute force |
| 2 | CodeIF 1087 | Code generation | 2×N tilings with dominos and trominos | 16 | **wrong** — 12, 28, 65 for N=4,5,6 where the answers are 11, 24, 53 | executed against known counts |
| 3 | AutoDCWorkflow menu p26 | Data wrangling | 100 × 21 table, dates in four formats | 11 | **wrong** in 2 runs of 3, in the same 9 cells — `1949-23-12` left as written where the answer is `1949-12-23` | gold table, cell by cell |
| 4 | AutoDCWorkflow menu p18 | Data wrangling | 100 × 20 table, 2 dirty columns | 17 | **wrong** — a placeholder uppercased where the brief empties it | gold table, cell by cell |
| 5 | LongWeave KG_TO_TEXT #1 | Writing | 2,048-word biography from 119 facts | 7 | **wrong** — 2,372 and 2,393 words against a 2,000–2,100 budget | word count |
| 6 | LongWeave KG_TO_TEXT #6 | Writing | 2,048-word biography from 81 facts | 6 | **wrong** in 3 runs of 3 — 1,881 and 3,661 words against a 2,000–2,100 budget; the one in budget repeats itself across 31 paragraphs | word count, and read |

Two rules picked these, and both are measured. **Requirements**: how many chips
the rail holds — a list nobody can read is a screen nobody uses, so nothing
above twenty survives. Each count is the median of three runs of the real
extractor over the brief the script writes. **What the agent's own run
produced**: the real agent is run over the brief with nobody steering it until
it stops, and then the *deliverable* is checked against ground truth — programs
executed against known answers, tables compared with the benchmark's gold table
cell by cell, biographies counted and read. A task whose deliverable comes out
correct is finished, and the study has nothing to ask about it, whatever the
requirement list says.

That last distinction is not pedantic. Checking the deliverable rather than the
verdicts is what removed the previous task 1, task 4 and task 5 from this set:
all three were being reported as unfinished while what the agent had actually
written was right. It is also what keeps task 2 in: that one stops with every
chip green on a tiling count that is wrong.

Tasks 1-2 replace LongWeave CODE_FIXING/4k, which was dropped for being
unreadable — a 352-line broken Python file is not something a participant can
hold in their head, and the benchmark's smaller tiers do not help, because
LongWeave sizes the *output*: CODE_FIXING/1k still has a median of 223 lines.
CodeIF puts the difficulty in a constraint list instead of a wall of code, and
it also closes the gap this file used to name as the highest-value change left:
every task in that set sent **100%** of its requirements to the judge, where
the CodeIF pair sends most of them to a parser.

The screen ruled out more than it kept, and executing the output is what ruled
most of it out. Eleven CodeIF instances produced code that passes an
independent oracle — 630, 652, 682, 683, 687, 744, 851, 933, 957, 975 among
them — including the prime-counting thread that was task 1 for a day and came
back correct three runs out of three. CodeIF 891 is wrong but only in the
fourth decimal (it returns −0.16952 for the minimum of GeLU, which is
−0.16997), too fine for anyone to see. 367/738/745/1034/1075 are over the
twenty-requirement cap. Three menu instances — p4, p9 and p12 — were repaired
**perfectly**, cell for cell against gold and with the right answer; what
separates p13 and p18 is the width of the repair, 97 and 96 changed cells with
named corrections inside a mechanical rule. And KG_TO_TEXT #0, task 5 for a
day, wrote a 2,009-word biography with every claim traceable; 81 facts in a
2,000-word budget does not strain, which is why task 5 is now the 119-fact row.

**`backend/table_checker.py`** decides what a wrangling brief promises about its
source table — keep every row, keep the order, keep the header, change only
these columns, uppercase this column, report this count — by comparing the
deliverable with the attachment. It is the one check in the codebase that is
handed source material, which crosses the line `tools.Attachments` draws on
purpose; a relational promise is the only thing that cannot be checked on the
other side of it. The gold table is never involved. Rows 3 and 4 above are the
before and after: the same two tasks previously produced ten false verdicts
between them on deliverables identical to the benchmark's gold table, and p13's
single verdict now is a real error (three stray cells in a frozen column) that
nothing had ever caught.

**`loop._stuck`** pauses a run after three edits that leave the same requirement
red, because either the agent cannot settle it or the verdict is wrong and both
want a person. It matters because `finish` is rejected while anything is
violated, so before this a false verdict made an unattended run
non-terminating — task 5 spent forty steps editing a biography that was already
finished.

Three older checker defects the screen turned up have also been fixed. A brief that says
"variable x should not be a global variable" states a ban the checker has no
property for — there is no negated `module_level`, no negated `assigned_once` —
and the extractor answered it with the positive property, so correct code was
reported as violating it and no edit could settle the chip; it appeared in four
of the eleven CodeIF instances run against the agent. `extract._grounded` now
routes a negated presence-property to the judge, and the same guard catches
"define an interface named X", which names no Python construct and was failing a
class of that exact name. `initializes` now accepts a literal spelling the
argument and an argument list, so `AnalyzeDwa("dwa", "info")` is no longer
reported as not built from `dwa`. The cost lands on **code%**: those guards move
two to three requirements per CodeIF brief off the parser and onto the judge.

What is left. The judge still cannot read a 100-row table, but on the wrangling
pair it is no longer asked to: the relational requirements go to the parser now,
and what remains for it there is the prose answer. The two places it is still
load-bearing and still wrong are task 5, where it reads "the spousal
relationship beginning on 1992-07-05" as an inference from a triple that says
`spouse of start date - 1992-07-05`, and task 2, where it passed code that
computes the wrong answer.

Reference material is attached rather than pasted into the brief, so the
requirement checker never sees it — a table cannot be counted by a word limit,
and it is what keeps the writing pair's list at six while its work is 81 facts
long. Gold answers land in `backend/tasks/gold/` and are never attached to a
session — the agent has no route to them through the tools, though its shell
can still walk the filesystem, so treat that directory as out of bounds rather
than sealed.

This writes `backend/tasks/`, which is git-ignored. Instances are pinned — by
question id for CodeIF, by purpose id for AutoDCWorkflow, and for LongWeave by
ordinal within a (task, tier) group — so re-running reproduces the same six.
The 224 MB LongWeave file is streamed once and read only as far as the last row
needed, which is now the KG pair alone.

Skip this and the app still runs: `/api/agent/presets` returns an empty list
and the picker hides itself. The backend reads the directory per request, so
no restart is needed after running it — reload the page and the tasks are
there.

`setup_tasks.py`'s docstring carries the measurements these six were chosen on,
what each rejected candidate failed, and every edit made to a benchmark brief.

## Notes

- Sessions are held in memory and written to `backend/runs/<id>/session.json`
  as a full event log (steps, verdicts, user actions) for later analysis.
- `#dev` in the URL enables researcher tools: single-stepping, raw model
  observations, a context inspector, and session export.
- Reference material (a data table, a source document) is attached rather than
  pasted into the brief. Attachments live beside the workspace, not in it, so
  the requirement checker never sees them — a table cannot be counted by a word
  limit or searched by a banned-phrase rule. The agent reads them with
  `read_attachment`; the chat shows a file chip that opens the contents.
