# WeightText

A requirement-steered writing agent. The user states a task in chat; the system
extracts the task's requirements into an editable list, an agent works on the
task step by step, and every step is checked against the requirements — so the
user always sees what is met, what is not, and which step made it so.

## How it works

- **Extract** — the first chat message is parsed into a requirement list
  (length limits, banned/required phrases, structural rules, tone, process
  rules). Each requirement stays linked to the sentence it came from, and the
  list is fully editable: add, reword, delete.
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
      setup_tasks.py   fetches the six benchmark tasks (see below)
      agent/           extraction, verification, tools, the step loop
      tasks/           the fetched tasks and their data (git-ignored)
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

The composer offers a picker of six evaluation tasks — two each from CodeIF,
T2R-bench and LongWeave. They are not in this repository: two of the three
benchmarks ship without a licence file, so nothing is redistributed here.
Fetch and assemble them once:

    cd backend && ../.venv/bin/python setup_tasks.py

This writes `backend/tasks/`, which is git-ignored. Instances are pinned by id,
so re-running reproduces the same six tasks. Neither large source archive is
downloaded whole — the T2R tables are ranged out of a 234 MB zip (~0.3 MB
transferred) and the LongWeave file is streamed only as far as the rows needed
(~159 MB of 224 MB).

Skip this and the app still runs: `/api/agent/presets` returns an empty list
and the picker hides itself. The backend reads the directory per request, so
no restart is needed after running it — reload the page and the six tasks are
there.

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
