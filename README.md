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
