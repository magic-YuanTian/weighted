"""Codex engine: the agent is OpenAI's Codex CLI, run headlessly per turn.

`codex exec --json` runs one full turn in the session's workspace and streams
items (file changes, commands, messages); this module translates them into the
same event log, verification cadence, Tier-0 enforcement and finish gate the
native loop produces. The UI and the study instrumentation cannot tell which
engine ran a turn — only the trajectory can.

What moves where, compared to loop.py:

* The tools are Codex's own (shell, patches, real program execution) — the
  data-wrangling tasks can be solved by running pandas instead of rewriting a
  table by hand.
* Standing requirements ride in AGENTS.md, rewritten before every turn, which
  Codex reads from its working root. The workspace listing hides that file so
  the verifier and the UI never mistake it for a deliverable.
* Per-edit verdicts: every file_change item is code-checked the moment it
  lands and the chips go on that step. The judge runs once per turn, at the
  end, where the gate needs it.
* In-turn checking: run_check becomes a curl to /api/agent/check — the
  sandbox is granted network access for exactly this reason.
* Tier-0 becomes detect-and-restore: Codex applies its own patches, so a
  write that destroys a protected phrase is reverted right after it lands and
  the rejection is logged, which is the same contract ("the file is
  untouched") enforced one step later.
* The finish gate becomes a bounce: a turn that ends while requirements block
  is answered with FINISH REJECTED and the checker's report as the next turn.
  A bounced turn that edits nothing ends the run instead — the agent is stuck,
  and arguing with it costs money.
"""

import json
import os
import re
import subprocess
import threading

from . import loop
from . import requirements as R
from . import verifier

TURN_TIMEOUT = int(os.environ.get("WEIGHTTEXT_CODEX_TIMEOUT", "900"))
PORT = int(os.environ.get("PORT", 5091))


def enabled():
    return os.environ.get("WEIGHTTEXT_ENGINE", "codex").lower() == "codex"


def check_command(session):
    return (f'curl -s "http://127.0.0.1:{PORT}/api/agent/check'
            f'?sessionId={session.id}"')


# ---------------------------------------------------------------- AGENTS.md

RULES = """You are a writing agent working in this directory. Rules:

- Every file in this directory is a deliverable and is checked against the
  requirements below. Exactly one file per deliverable, named after it. Never
  create helper scripts, notes or intermediates here — put those in the
  system temp directory.
- Do not modify AGENTS.md.
- Attached reference material (read-only, never a deliverable, not seen by
  the checker) lives at the absolute paths listed below. Read it from there.
- After each substantive edit, and before you stop, run the check command
  below and read its report. It is deterministic where it can be and it does
  not flatter you. Do not claim a requirement is met — check it.
- Make the work legible. The requirement panel beside your workspace
  attributes each verdict to the step that caused it, so: apply repairs and
  transformations ONE AT A TIME — one file write per repair, checking in
  between — never folded into a single script that does everything at once.
  Run the check command on its own, never chained onto another command.
- When a deliverable transforms an attached source (a cleaned table, a
  revised document), start by writing an exact copy of the source, then apply
  each repair as its own in-place edit. One-shot rewrites are where rows get
  dropped and reordered without anyone noticing — including you.
- Your work is only accepted when nothing blocks finish. If the report says
  requirements are violated, fix them before stopping. Stop with a short
  message saying what you delivered — never paste file contents into it.
"""


def _agents_md(session):
    parts = [RULES]
    att = session.attachments
    names = att.list()
    if names:
        parts.append("Attachments:")
        for n in names:
            parts.append(f"  {os.path.join(att.root, n)}")
        parts.append("")
    parts.append("Check command:")
    parts.append(f"  {check_command(session)}")
    parts.append("")
    standing = R.standing_block(session.brief, session.requirements)
    if standing:
        parts.append(standing)
    return "\n".join(parts) + "\n"


def _write_agents_md(session):
    with open(os.path.join(session.workspace.root, "AGENTS.md"), "w",
              encoding="utf-8") as fh:
        fh.write(_agents_md(session))


# ---------------------------------------------------------------- events

def _short_cmd(command):
    cmd = re.sub(r"^/bin/\w+ -l?c '?", "", command or "").rstrip("'")
    return cmd[:80]


def _rel(session, path):
    root = os.path.abspath(session.workspace.root)
    p = os.path.abspath(path)
    return os.path.relpath(p, root) if p.startswith(root + os.sep) else None


def _absorb(session, current):
    """Diff the workspace against `current`: enforce Tier-0 on every changed
    file (restore, like the native edit tool's refusal, one step later) and
    return per-file records for logging. This is the single funnel for BOTH
    ways Codex writes — apply_patch items and shell commands — so a pandas
    script that rewrites a table is verified exactly like a patch."""
    records = []
    names = set(session.workspace.list())
    for rel in sorted(names | set(current)):
        after = session.workspace.read(rel) if rel in names else None
        before = current.get(rel)
        if after == before:
            continue
        lost = [p for p in R.protected_phrases(session.requirements)
                if p and p in (before or "") and p not in (after or "")]
        if lost:
            if before is None:
                try:
                    os.remove(session.workspace.path(rel))
                except OSError:
                    pass
            else:
                session.workspace.write(rel, before)
            records.append((rel, "tier0", lost, 0, 0))
            continue
        kind = ("add" if before is None else
                "delete" if after is None else "update")
        from .tools import _diff_stat
        add, dele = _diff_stat(before, after)
        if after is None:
            current.pop(rel, None)
        else:
            current[rel] = after
        records.append((rel, kind, None, add, dele))
    return records


def _log_file_steps(session, records, note=""):
    """One step event per changed file. The verifier runs once over the whole
    batch; its chips ride the first accepted file's event."""
    from .tools import word_count
    accepted = [r for r in records if r[1] != "tier0"]
    changed = loop._verify_and_apply(session, judge=False) if accepted else []
    chips_left = list(changed)
    for rel, kind, lost, add, dele in records:
        session.step_count += 1
        pinned = [r["id"] for r in R.pinned(session.requirements)]
        if kind == "tier0":
            quoted = ", ".join(f'"{p}"' for p in lost)
            meta = {"ok": False, "kind": "edit", "path": rel, "blocked": "tier0"}
            session.log("step", step=session.step_count, action="edit_file",
                        args={"path": rel}, argSummary=rel, thought="",
                        observation=(f"REJECTED by the workspace: this change "
                                     f"would remove protected text ({quoted}). "
                                     f"The file was restored."),
                        meta=meta, chips=[],
                        summary=loop._summarize_step(session, "edit_file",
                                                     {"path": rel}, meta, []),
                        pinned=pinned)
            continue
        action = "write_file" if kind == "add" else "edit_file"
        if kind == "delete":
            observation = f"deleted {rel}"
        else:
            observation = (f"wrote {rel} "
                           f"({word_count(session.workspace.read(rel))} words, "
                           f"+{add} \u2212{dele})")
            if note:
                observation += f" — {note}"
            if chips_left:
                observation += "\n" + loop._verdict_lines(session, chips_left)
        meta = {"ok": True, "kind": "edit", "path": rel, "add": add, "del": dele}
        chips, chips_left = chips_left, []
        session.log("step", step=session.step_count, action=action,
                    args={"path": rel}, argSummary=rel, thought="",
                    observation=observation[:6000], meta=meta, chips=chips,
                    summary=loop._summarize_step(session, action, {"path": rel},
                                                 meta, chips),
                    pinned=pinned)


def _handle_file_change(session, item, current):
    for chg in item.get("changes") or []:
        rel = _rel(session, chg.get("path", ""))
        if rel == "AGENTS.md":
            _write_agents_md(session)
            session.log("notice", text="The agent edited AGENTS.md; restored.")
        elif rel is not None and os.sep in rel:
            session.log("notice", text=f"The agent wrote {rel} — the workspace "
                                       "is flat, so nested files are ignored "
                                       "by the checker and the UI.")
    _log_file_steps(session, _absorb(session, current))


def _handle_command(session, item, current):
    command = item.get("command") or ""
    ok = item.get("exit_code") == 0
    contains_check = "/api/agent/check" in command
    # Agents chain the check with other commands ("curl … && python …"), and
    # the chain's exit code is the tail's. If the report demonstrably printed,
    # the check ran — the step is the check succeeding, not the tail failing,
    # which stays visible in the observation.
    if contains_check and not ok and "would block finish" in (
            item.get("aggregated_output") or ""):
        ok = True
    # A compound command ("awk …clean the table… && curl …check") is work
    # first and check second: labeling it "Checked the requirements" hides
    # the work. It reads as the check only when little else rode along.
    rest = re.sub(r"^/bin/\w+ -l?c '?", "", command)
    rest = re.sub(r"curl[^|&;]*api/agent/check[^|&;]*", "", rest)
    rest = re.sub(r"[|&;'\"\s]+", " ", rest).strip()
    is_check = contains_check and len(rest) <= 20
    action = "run_check" if is_check else "command"
    chips = getattr(session, "_check_chips", None) if contains_check else None
    if contains_check:
        session._check_chips = None
    meta = {"ok": ok, "kind": "check" if is_check else "command"}
    session.step_count += 1
    session.log("step", step=session.step_count, action=action, args={},
                argSummary=_short_cmd(command), thought="",
                observation=(item.get("aggregated_output") or "")[:6000],
                meta=meta, chips=chips or [],
                summary=loop._summarize_step(session, action, {}, meta,
                                             chips or []) if is_check else "",
                pinned=[r["id"] for r in R.pinned(session.requirements)])
    # A command can write files just as well as a patch can (that is the point
    # of this engine). Whatever it changed goes through the same funnel.
    _log_file_steps(session, _absorb(session, current),
                    note="changed by the command above")


# ---------------------------------------------------------------- the turn

def _next_prompt(session):
    if session.pending_steer:
        text = session.pending_steer
        session.pending_steer = None
        session.log("steer", text=text,
                    pinned=[r["id"] for r in R.pinned(session.requirements)])
        return text
    bounce = getattr(session, "pending_gate", None)
    if bounce:
        session.pending_gate = None
        return bounce
    if not getattr(session, "codex_thread_id", None):
        for m in reversed(session.llm_messages):
            if m.get("role") == "user":
                return m["content"]
        return session.brief
    return ("Continue. Run the check command, fix anything it reports, and "
            "stop when nothing blocks finish.")


def run_turn(session):
    """Run one Codex turn. Returns the events it produced, like loop.step."""
    if session.status == "done":
        return []
    session.status = "running"
    session._pause_requested = False   # a new turn consumes any stale pause
    start_index = len(session.events)

    prompt = _next_prompt(session)
    reminder = R.reminder_block(session.requirements)
    if reminder:
        prompt = prompt + "\n\n" + reminder
    _write_agents_md(session)
    current = {f: session.workspace.read(f) for f in session.workspace.list()}

    cmd = ["codex", "exec"]
    thread = getattr(session, "codex_thread_id", None)
    if thread:
        cmd += ["resume", thread]
    cmd += ["--json", "--skip-git-repo-check",
            "-c", 'sandbox_mode="workspace-write"',
            "-c", "sandbox_workspace_write.network_access=true"]
    if not thread:
        # resume restores the session's own working root and rejects -C
        cmd += ["-C", session.workspace.root]
    # Pinned, not inherited from ~/.codex/config.toml: a study condition must
    # not change because the machine's global codex defaults did.
    model = os.environ.get("WEIGHTTEXT_CODEX_MODEL", "gpt-5.6-luna")
    effort = os.environ.get("WEIGHTTEXT_CODEX_EFFORT", "none")
    cmd += ["-m", model, "-c", f'model_reasoning_effort="{effort}"']
    cmd += [prompt]

    print(f"[codex] session={session.id} turn "
          f"({'resume ' + thread if thread else 'new thread'})…", flush=True)
    stderr_path = os.path.join(session.root, "codex_stderr.log")
    edits_before = session.step_count
    try:
        with open(stderr_path, "a", encoding="utf-8") as errfh:
            # cwd matters even though new threads get -C: `resume` rejects -C
            # and inherits this process's cwd, and a relative-path write from
            # a resumed turn then lands outside the workspace (it happened —
            # a stray cleaned.csv in backend/). The workspace is the cwd, always.
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfh,
                                    stdin=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace",
                                    cwd=session.workspace.root)
            # the /pause route kills through this handle, so Pause interrupts
            # the turn now instead of after minutes of remaining work
            session._codex_proc = proc
            # The deadline must cover the STREAMING phase: reading stdout
            # blocks for as long as codex keeps the pipe open, and a model
            # call stalled on a dead connection keeps it open forever. A
            # timeout on the post-stream wait() alone never fires.
            timed_out = []
            timer = threading.Timer(
                TURN_TIMEOUT, lambda: (timed_out.append(True), proc.kill()))
            timer.start()
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    _dispatch(session, ev, current)
                    session.save()
                    # fallback interruption point, in case /pause raced the
                    # process handle: the flag alone stops the turn here
                    if getattr(session, "_pause_requested", False):
                        proc.kill()
                        break
                proc.wait()
            finally:
                timer.cancel()
                session._codex_proc = None
            if getattr(session, "_pause_requested", False):
                session._pause_requested = False
                session.log("notice", text="Paused. The work so far is saved "
                                           "— press Run to continue.")
                session.status = "paused"
                session.save()
                return session.events[start_index:]
            if timed_out:
                print(f"[codex] session={session.id} turn hit the "
                      f"{TURN_TIMEOUT}s timeout", flush=True)
                session.log("error", text="The agent was taking too long and "
                                          "was stopped. Its work so far is "
                                          "saved — press Run to continue.")
                session.status = "paused"
                session.save()
                return session.events[start_index:]
    except FileNotFoundError:
        session.log("error", text="codex CLI not found on PATH — install it or "
                                  "set WEIGHTTEXT_ENGINE=native.")
        session.status = "paused"
        session.save()
        return session.events[start_index:]

    if proc.returncode != 0:
        # The participant can act on "press Run"; the exit code and stderr
        # path are for us, on the server console.
        print(f"[codex] session={session.id} exited with {proc.returncode} — "
              f"see runs/{session.id}/codex_stderr.log", flush=True)
        session.log("error", text="The agent stopped unexpectedly. Its work so "
                                  "far is saved — press Run to continue.")
        session.status = "paused"
        session.save()
        return session.events[start_index:]

    # End of turn: judge pass + gate, exactly like a finish attempt.
    changed = loop._verify_and_apply(session, judge=True)
    session.log("recheck", judge=True, chips=changed,
                counts=R.counts(session.requirements))
    blocked = R.blocking(session.requirements)
    edited = session.step_count > edits_before
    if session.gate_on and blocked:
        ids = ", ".join(r["id"] for r in blocked)
        session.log("gate", blocked=[r["id"] for r in blocked])
        if edited or not getattr(session, "_bounced", False):
            session._bounced = True
            session.pending_gate = (
                f"FINISH REJECTED — these requirements are not satisfied: {ids}."
                f"\n\n{verifier.report_text(session.requirements)}"
                "\n\nFix them, run the check command again, and stop when "
                "nothing blocks finish.")
            session.status = "idle"
        else:
            # Two turns in a row with no edits and the same wall: stop paying.
            session.log("notice", text="The agent made no further edits while "
                                       f"{ids} still block finish. Stopped — "
                                       "steer it, or edit the requirements.")
            session.status = "idle"
    else:
        session.status = "done"
    session.save()
    return session.events[start_index:]


def _dispatch(session, ev, current):
    etype = ev.get("type")
    if etype == "thread.started":
        session.codex_thread_id = ev.get("thread_id")
        return
    if etype == "item.completed":
        item = ev.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            text = (item.get("text") or "").strip()
            if text:
                session.log("assistant", text=text)
        elif itype == "file_change":
            _handle_file_change(session, item, current)
        elif itype == "command_execution":
            _handle_command(session, item, current)
        elif itype == "error":
            session.log("error", text=_plain_error(item.get("message") or item))
        return
    if etype in ("turn.failed", "error"):
        session.log("error", text=_plain_error(ev))


def _plain_error(ev):
    """One readable sentence, not a JSON dump — this lands in the chat."""
    msg = ev
    if isinstance(ev, dict):
        err = ev.get("error")
        msg = ((err or {}).get("message") if isinstance(err, dict) else None) \
            or ev.get("message") or ev
    msg = str(msg)[:400]
    if re.search(r"stream (error|disconnected)|timed? ?out|connection|429|"
                 r"rate.?limit|overloaded", msg, re.I):
        return ("The connection to the model hiccuped; the agent retries on "
                f"its own. ({msg})")
    return f"The agent engine hit a problem: {msg}"


def can_continue(session):
    return (session.status not in ("done", "paused")
            and bool(getattr(session, "pending_gate", None)
                     or session.pending_steer))
