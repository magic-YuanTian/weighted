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
      llm_api.py       model access — implement this (see below)
      agent_routes.py  HTTP surface: /api/agent/*
      checker.py       deterministic text checks
      agent/           extraction, verification, tools, the step loop
    frontend/
      src/agent/       the three-pane UI: chat, workspace, requirements

## Setup

Backend (Python 3.10+):

    cd backend
    pip install -r requirements.txt
    python server.py            # serves on :5091

**Before the first run**, implement `client()` in `backend/llm_api.py` and set
`MODEL`. The module docstring specifies the exact interface the rest of the
code expects; any model client that satisfies it works. The model must support
tool/function calling. Keep credentials out of the repository.

Frontend:

    cd frontend
    npm install
    npm start                   # dev server on :3000, proxies /api to :5091

or `npm run build`, after which the backend serves the app on :5091 directly.

## Notes

- Sessions are held in memory and written to `backend/runs/<id>/session.json`
  as a full event log (steps, verdicts, user actions) for later analysis.
- `#dev` in the URL enables researcher tools: single-stepping, raw model
  observations, a context inspector, and session export.
