"""One step of the agent loop.

The UI drives stepping (one HTTP request per step) rather than the server
running to completion: ⏸ / ⏭ / ⏹ then mean exactly what they say, every step is
verified before the next one is planned, and nothing depends on a streaming
transport surviving a proxy.
"""

from . import llm
from . import requirements as R
from . import tools
from . import verifier

# The agent's standing instructions, in two conditions.
#
# The study compares WeightText against a baseline that is the same model, the
# same tools and the same screen with the requirement machinery removed — so
# the prompt has to differ by exactly that machinery and nothing else. The
# pieces below are composed twice rather than written out twice: a rule about
# how to use edit_file is shared verbatim, and only the rules that talk about
# checking, verdicts and requirements have a second version. What is NOT
# withheld from the baseline is tool advice — copy the source before repairing
# it, one repair per command — because a baseline that is badly prompted
# measures prompt engineering, not the interface.
#
# The weighted composition below is byte-identical to the single SYSTEM string
# that preceded the split, so every run measured before this change was
# measured under the same prompt this one sends.

_HEAD = """You are a writing agent working in a small file workspace.

Rules of this workspace:
"""

_FILE_RULES = """- Exactly one file per deliverable, named after it (cover_letter.md,
  recruiter_email.md). Start each file with a '# Title' heading line. Never keep
  the same content in two files — a duplicate makes every check ambiguous.
- Three ways to change a file, and they do different jobs. edit_file replaces
  one exact passage and cannot add anything. insert_file adds text at a line
  without touching what is there — insert_line 0 for the top, the last line
  number to append. write_file replaces the whole file, so reach for it last.
  Needing more words is a job for insert_file, never for an empty old_str.
- read_file numbers the lines it returns. Copy an anchor for edit_file out of
  that listing rather than from memory, and strip the "12: " prefix — the
  numbers are the listing's, not the file's.
"""

_RUN_CHECK = """- run_check runs a deterministic checker. It is free and it does not flatter
  you. Run it after a substantive edit and before you try to finish.
"""

_ONE_ACTION = """- Take exactly one action at a time and say, in one short sentence, why.
"""

_VERDICTS = """- Do not claim a requirement is met. Make it checkable, then check it.
- Every edit comes back with the checker's verdicts and a "blocking finish"
  line. When it says nothing is blocking, run_check once and then finish. Do
  not keep polishing a package that already passes.
"""

# The baseline's stopping rule. It has to say something, or the loop runs to
# the turn cap on every task; what it must not do is point at a checker.
_STOP = """- When the brief is done, finish. Do not keep polishing work that already
  meets it.
"""

_CLOSE = """- Close the run with the finish tool, never with a message. A reply is not an
  ending: it stops the run without closing it, and leaves the work sitting at
  "in progress" with every requirement green. The one reason to reply instead
  of acting is being genuinely stuck — a requirement you cannot satisfy, or a
  decision only the user can make. Then say so in one sentence, and say what
  you need.
"""

_CLOSE_BASELINE = """- Close the run with the finish tool, never with a message. A reply is not an
  ending: it stops the run without closing it, and leaves the work sitting at
  "in progress". The one reason to reply instead of acting is being genuinely
  stuck — something the brief asks for that you cannot do, or a decision only
  the user can make. Then say so in one sentence, and say what you need.
"""

_CHAT = """- The user reads your messages beside the workspace, with every file already
  open in front of them. Never paste a file's contents into one, in whole or
  in part. Say what you did, not what it says.
"""

SYSTEM = (_HEAD + _FILE_RULES + _RUN_CHECK + _ONE_ACTION + _VERDICTS
          + _CLOSE + _CHAT)

SYSTEM_BASELINE = (_HEAD + _FILE_RULES + _ONE_ACTION + _STOP
                   + _CLOSE_BASELINE + _CHAT)

# Appended only when the shell is actually on (tools.shell_enabled). Rules for
# a tool the model has not been given are worse than useless: it plans around
# them and then cannot act.
_SHELL_HEAD = """
Working with a terminal:
- run_command runs a shell command in the workspace. Reach for it when a
  program does the job better than retyping text does: transforming a data
  table, computing a figure you would otherwise estimate, running code you
  just wrote. python3 and the usual text tools are there. pandas is not, and
  for a small table you do not want it — the standard library's csv module
  leaves the columns you were told not to touch exactly as they were.
"""

_SHELL_SCRATCH = """- Every file in the workspace is a deliverable and is checked as one. Helper
  scripts, intermediates and scratch output belong in $SCRATCH, never here.
  Attachments are read-only at $ATTACHMENTS: read them there, never clean one
  in place — it is the only record of what the source said.
"""

_SHELL_SCRATCH_BASELINE = """- Every file in the workspace is a deliverable. Helper scripts, intermediates
  and scratch output belong in $SCRATCH, never here. Attachments are read-only
  at $ATTACHMENTS: read them there, never clean one in place — it is the only
  record of what the source said.
"""

_SHELL_COPY = """- When a deliverable transforms an attached source — a cleaned table, a
  revised document — begin by copying the source verbatim, then apply each
  repair to the copy as its own command. One-shot rewrites are where rows get
  dropped and reordered without anyone noticing, including you.
"""

_SHELL_ONE_REPAIR = """- One repair per command, checked in between. The requirement panel beside
  your workspace attributes every verdict to the step that caused it, so a
  script that does everything at once tells the person watching nothing about
  which change did what.
"""

_SHELL_ONE_REPAIR_BASELINE = """- One repair per command. A script that does everything at once tells the
  person watching nothing about which change did what.
"""

SHELL_RULES = (_SHELL_HEAD + _SHELL_SCRATCH + _SHELL_COPY + _SHELL_ONE_REPAIR)

SHELL_RULES_BASELINE = (_SHELL_HEAD + _SHELL_SCRATCH_BASELINE + _SHELL_COPY
                        + _SHELL_ONE_REPAIR_BASELINE)

MAX_HISTORY_BLOCKS = 14


def instructions(mode="weighted"):
    """The agent's standing instructions for this server's configuration and
    this session's condition."""
    baseline = mode == "baseline"
    base = SYSTEM_BASELINE if baseline else SYSTEM
    if not tools.shell_enabled():
        return base
    return base + (SHELL_RULES_BASELINE if baseline else SHELL_RULES)


def _condense(messages):
    """Drop whole assistant+tool blocks from the front. Splitting a block would
    leave orphan tool messages and the API would reject the request."""
    blocks, current = [], []
    for m in messages:
        if m["role"] in ("user", "assistant") and current:
            blocks.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        blocks.append(current)
    if len(blocks) <= MAX_HISTORY_BLOCKS:
        return messages, 0
    dropped = len(blocks) - MAX_HISTORY_BLOCKS
    kept = [m for b in blocks[dropped:] for m in b]
    return kept, dropped


def workspace_digest(session):
    """Files, sizes, and — the part that matters — where each scoped
    requirement currently resolves. Without this the agent invents a filename,
    the scope fails to bind, and the rail honestly reports "unverified" for
    something the agent believes it has written."""
    doc = verifier.build_document(session.workspace)
    lines = ["Workspace right now:"]
    files = session.workspace.list()
    if not files:
        lines.append("  (empty — nothing has been written yet)")
    for name in files:
        # Lines as well as words: insert_file takes a line number, and the last
        # one is the append anchor. Without it here the agent estimates, and
        # both LongWeave runs spent a step being told the number it guessed was
        # past the end of the file.
        text = session.workspace.read(name) or ""
        lines.append(f"  {name} — {tools.word_count(text)} words, "
                     f"{tools.line_count(text)} lines")

    att = session.attachments.meta()
    if att:
        lines.append("Attached by the user — read-only reference material, not part of")
        lines.append("the deliverable" + ("" if session.mode == "baseline"
                                          else " and not seen by the checker")
                     + ". Use read_attachment:")
        for a in att:
            lines.append(f"  {a['name']} — {a['lines']} lines, {a['chars']} characters")
        if tools.shell_enabled():
            lines.append(f'  in a command these are "$ATTACHMENTS/<name>" '
                         f"({session.attachments.root})")
    if tools.shell_enabled():
        lines.append(f'$SCRATCH is "{session.scratch}" — helper scripts and '
                     "intermediates go there, where nothing checks them.")

    seen = set()
    for req in R.active(session.requirements):
        scope = req.get("scope") or {}
        name = scope.get("name")
        if scope.get("kind") not in ("section", "file") or not name or name in seen:
            continue
        seen.add(name)
        rng = verifier.resolve_scope(req, doc)
        where = verifier.locate(doc, rng[0], rng[1])["file"] if rng else None
        lines.append(f'  scope "{name}" -> '
                     + (where or "NO FILE YET — create one named after it"))
    return "\n".join(lines)


def build_messages(session):
    """The exact message list sent for the next action, plus the parts list the
    context inspector renders. Nothing is added anywhere else."""
    standing = R.standing_block(session.brief, session.requirements)
    digest = workspace_digest(session)
    base = instructions(session.mode)
    system = base + ("\n" + standing if standing else "") + "\n" + digest
    history, dropped = _condense(session.llm_messages)
    msgs = [{"role": "system", "content": system}] + history
    if dropped:
        msgs.insert(1, {"role": "system",
                        "content": f"({dropped} earlier step(s) condensed away. The "
                                   "workspace files and the requirement list are the "
                                   "durable state — re-read a file if you need it.)"})
    reminder = R.reminder_block(session.requirements)
    if reminder:
        # Last position = most recent = what the model is most likely to obey.
        # This is the whole of "attention steering" through an API model.
        msgs.append({"role": "system", "content": reminder})

    parts = [
        {"key": "system", "label": "Agent instructions", "tokens": llm.estimate_tokens(base)},
        {"key": "brief", "label": "Task brief", "tokens": llm.estimate_tokens(session.brief),
         "detail": (session.brief or "")[:400]},
        {"key": "requirements", "label": "Standing requirements",
         "tokens": llm.estimate_tokens(standing),
         "detail": f"{len(R.active(session.requirements))} active · "
                   f"{len(R.pinned(session.requirements))} pinned · "
                   f"{len([r for r in session.requirements if r['status'] == 'paused'])} paused"},
        {"key": "trajectory", "label": "Trajectory",
         "tokens": sum(llm.estimate_tokens(str(m.get("content") or "")) for m in history),
         "detail": f"last {len(history)} messages · {dropped} block(s) condensed"},
        {"key": "workspace", "label": "Workspace digest",
         "tokens": llm.estimate_tokens(digest),
         "detail": digest.split("\n", 1)[1][:200] if "\n" in digest else ""},
        {"key": "reminders", "label": "Reminders (pinned)",
         "tokens": llm.estimate_tokens(reminder),
         "detail": ", ".join(r["id"] for r in R.pinned(session.requirements)) or "none pinned",
         "text": reminder},
    ]
    return msgs, parts


def context_preview(session):
    _, parts = build_messages(session)
    return {"parts": parts, "total": sum(p["tokens"] for p in parts)}


def _arg_summary(name, args):
    if name in ("read_file", "write_file", "edit_file", "insert_file"):
        return args.get("path") or ""
    if name == "run_command":
        # The run stream shows this on one line beside the step; the whole
        # command, newlines and all, is in the observation underneath.
        return " ".join((args.get("command") or "").split())[:80]
    if name == "finish":
        return (args.get("summary") or "")[:80]
    return ""


_VWORDS = {"satisfied": "met", "violated": "not met", "stale": "needs re-checking",
           "partial": "partly met", "unverified": "not checked yet"}
_FILE_TAG = __import__("re").compile(r"^\[[^\]]+\]\s*")


def _plain_detail(req):
    return _FILE_TAG.sub("", ((req or {}).get("report") or {}).get("detail") or "")


def _summarize_step(session, name, args, meta, changed):
    """What this step meant, for the person watching. The agent keeps reading
    the precise observation; nobody else should have to."""
    by_id = {r["id"]: r for r in session.requirements}
    if meta.get("blocked") == "gate":
        ids = ", ".join(r["id"] for r in R.blocking(session.requirements))
        return f"Not everything is met yet ({ids}). The agent kept working."
    if meta.get("blocked") == "tier0":
        return "The change would have removed frozen text, so the file was left as it was."
    if not meta.get("ok"):
        if name in ("write_file", "edit_file", "insert_file"):
            return "The change didn't match the current text, so nothing was changed."
        # A command that exits non-zero may still have written a file before it
        # fell over. When it did, the verdicts below are the honest summary of
        # the step; only a command that changed nothing is just a failure.
        if name != "run_command" or meta.get("kind") != "edit":
            return ("The command failed; the workspace is unchanged."
                    if name == "run_command" else "")
    if session.mode == "baseline":
        # Nothing checked this step, so there is nothing to say about it that
        # the step's own observation does not already say. The line stays empty
        # rather than reassuring: "everything looks met" is exactly the claim
        # this condition exists to withhold, and a tool failure above has
        # already had its say.
        return (args.get("summary") or "").strip() if name == "finish" else ""
    if name == "run_check":
        c = R.counts(session.requirements)
        unchecked = c.get("unverified", 0) + c.get("partial", 0)
        lines = [f"{c.get('satisfied', 0)} met · {c.get('violated', 0)} not met"
                 + (f" · {unchecked} not checked yet" if unchecked else "")]
        for r in session.requirements:
            rep = r.get("report") or {}
            if r.get("status") == "active" and rep.get("verdict") == "violated":
                lines.append(f"{r['id']}: {_plain_detail(r)}")
        return "\n".join(lines)
    if meta.get("kind") == "edit":
        lines = []
        # An edit marks every judged requirement it touches "stale" at once, so
        # naming each one prints a wall of near-identical lines that reads like
        # a page of warnings. One sentence carries the same information.
        stale = [chg["id"] for chg in changed if chg["verdict"] == "stale"]
        for chg in changed:
            if chg["verdict"] == "stale":
                continue
            req = by_id.get(chg["id"])
            word = _VWORDS.get(chg["verdict"], chg["verdict"])
            detail = _plain_detail(req)
            lines.append(f"{chg['id']} is now {word}" + (f": {detail}" if detail else ""))
        if stale:
            names = ", ".join(stale) if len(stale) <= 4 else f"{len(stale)} requirements"
            lines.append(f"{names} will be re-checked when the agent stops.")
        if not R.blocking(session.requirements):
            lines.append("Everything checked so far is met.")
        return "\n".join(lines)
    if name == "finish":
        return (args.get("summary") or "").strip()
    return ""


def _verdict_lines(session, changed):
    """The verifier's answer to an edit, in the observation the agent reads."""
    lines = []
    for c in changed:
        req = next((r for r in session.requirements if r["id"] == c["id"]), None)
        if not req:
            continue
        detail = (req.get("report") or {}).get("detail", "")
        lines.append(f"verifier: {c['id']} {c['verdict']} — {detail}")
    blocked = R.blocking(session.requirements)
    lines.append("blocking finish: " +
                 (", ".join(f"{r['id']} ({(r.get('report') or {}).get('verdict')})"
                            for r in blocked) if blocked else "nothing — run_check, then finish"))
    return "\n".join(lines)


def _verify_and_apply(session, judge=False):
    reports = verifier.verify(session, judge_pass=judge)
    before = {r["id"]: (r.get("report") or {}).get("verdict") for r in session.requirements}
    R.apply_report(session.requirements, reports)
    changed = []
    for r in session.requirements:
        v = (r.get("report") or {}).get("verdict")
        if r.get("status") == "active" and v != before.get(r["id"]):
            changed.append({"id": r["id"], "verdict": v,
                            "from": before.get(r["id"]), "weight": r.get("weight", 1)})
    return changed


def step(session):
    """Plan and take exactly one action. Returns the events it produced."""
    if session.status == "done":
        return []
    session.status = "running"
    start_index = len(session.events)

    if session.pending_steer:
        text = session.pending_steer
        session.pending_steer = None
        session.llm_messages.append({"role": "user", "content": text})
        session.log("steer", text=text,
                    pinned=[r["id"] for r in R.pinned(session.requirements)])

    messages, _ = build_messages(session)
    # The access log only prints on completion, so an in-flight step is
    # invisible there — say so up front instead.
    print(f"[step] session={session.id} step={session.step_count + 1} "
          f"calling {llm.MODEL}…", flush=True)
    try:
        msg = llm.chat(messages, tools=tools.schemas(session.mode))
    except Exception as e:                                    # noqa: BLE001
        session.status = "paused"
        session.log("error", text=f"{type(e).__name__}: {e}")
        session.save()
        return session.events[start_index:]

    thought = (msg.content or "").strip()
    calls = list(msg.tool_calls or [])

    if not calls and not thought:
        # Neither a call nor a word. This is not the agent talking to the user,
        # and it happens most often right after a step that looks at the state
        # -- run_check, a verifying command -- when the model finds nothing to
        # change but has not decided to stop either. Logged as speech it
        # renders an empty bubble in the chat, and a bare retry only adds
        # another one: the prompt is unchanged, so the next turn comes back
        # just as empty. Naming it in the history is what breaks the tie.
        session.llm_messages.append({
            "role": "user",
            "content": ("That turn was empty: no tool call and no message. If "
                        "the work is complete, call finish. Otherwise take the "
                        "next concrete action."),
        })
        session.status = "idle"
        # Recorded, not announced. The nudge above settles it within one turn
        # -- across 76 runs this fired six times, never twice in a row, and the
        # next turn was always a real action -- so the participant's chat has
        # nothing to tell them: the run simply carries on, which is what the
        # runner does with canContinue here. A warning for something that
        # corrects itself before anyone can act on it teaches people to read
        # the amber blocks as noise, and two of those are real. `trace` keeps
        # it in session.json and the export, where the analysis wants it, and
        # draws only under #dev.
        session.log("trace", kind="empty-turn",
                    text=("The model returned a turn with no tool call and no "
                          "message; the loop asked it to finish or act."))
        session.save()
        return session.events[start_index:]

    if not calls:
        # No action: the agent is talking to the user. That ends the run and
        # hands control back — it is not a failure.
        session.llm_messages.append({"role": "assistant", "content": thought})
        session.status = "idle"
        session.log("assistant", text=thought)
        session.save()
        return session.events[start_index:]

    session.llm_messages.append({
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [{"id": c.id, "type": "function",
                        "function": {"name": c.function.name,
                                     "arguments": c.function.arguments}} for c in calls],
    })

    for call in calls:
        name = call.function.name
        truncated = False
        try:
            args = __import__("json").loads(call.function.arguments or "{}")
        except ValueError:
            # A truncated tool call parses as nothing. Saying so beats handing
            # the agent an empty-argument error it will misdiagnose.
            args, truncated = {}, True

        session.step_count += 1
        if truncated:
            observation = (f"{name} was cut off mid-call — its arguments did not "
                           "parse. Nothing was written. Split the work into "
                           "smaller edits and try again.")
            meta = {"ok": False, "kind": "error", "blocked": "truncated"}
        else:
            observation, meta = tools.execute(session, name, args)
        gate_event = None

        if name == "finish" and session.mode == "baseline":
            # There is nothing to verify and nothing to hold the finish on.
            # The agent's word that it is done is the whole of the baseline's
            # stopping rule, which is the condition, not an oversight.
            changed = []
            session.status = "done"
        elif name == "finish":
            # judge included: the gate must not wave tone requirements through
            # just because nobody had judged them yet
            changed = _verify_and_apply(session, judge=True)
            blocked = R.blocking(session.requirements)
            if session.gate_on and blocked:
                ids = ", ".join(r["id"] for r in blocked)
                observation = ("FINISH REJECTED — the gate is on and these are not "
                               f"satisfied: {ids}.\n\n"
                               + verifier.report_text(session.requirements)
                               + "\n\nFix them, run_check again, then finish.")
                meta = {**meta, "ok": False, "blocked": "gate"}
                gate_event = {"blocked": [r["id"] for r in blocked]}
            else:
                session.status = "done"
        elif session.mode == "baseline":
            # No verifier, so no chips and no verdict lines appended to the
            # observation. The agent sees what its own tool call returned and
            # nothing else — which is the point of the comparison.
            changed = []
        else:
            # Every successful edit is judged as well as code-checked. One
            # extra model call per step buys the thing the rail is for: each
            # step's chips show exactly which requirements that step settled,
            # from step 1 — not a bulk "reviewed the rest" at the end.
            do_judge = meta.get("kind") == "edit" and bool(meta.get("ok"))
            changed = (_verify_and_apply(session, judge=do_judge)
                       if meta.get("kind") in ("edit", "check") else [])
            if meta.get("kind") == "edit" and meta.get("ok"):
                # Hand the deterministic verdicts straight back as part of the
                # observation. Making the agent call run_check to discover a
                # word count it just changed wastes a step and teaches it to
                # trust its own estimate — which is the failure this system
                # exists to prevent.
                observation += "\n" + _verdict_lines(session, changed)

        session.llm_messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": observation[:6000]})
        session.log("step", step=session.step_count, action=name, args=args,
                    argSummary=_arg_summary(name, args), thought=thought,
                    observation=observation, meta=meta, chips=changed,
                    summary=_summarize_step(session, name, args, meta, changed),
                    pinned=[r["id"] for r in R.pinned(session.requirements)])
        thought = ""   # only the first call of a batch carries the thought

        if gate_event:
            session.log("gate", **gate_event)

        # Weight 3 halts the run on a *regression* — something that was
        # satisfied just broke. Not-yet-satisfied is the normal state of a
        # draft in progress and stopping for it would make the loop useless.
        regressions = [c for c in changed
                       if c.get("weight", 1) >= 3 and c["verdict"] == "violated"
                       and c.get("from") == "satisfied"]
        if regressions:
            session.status = "paused"
            session.log("notice",
                        text="Loop paused: critical requirement broke — "
                             + ", ".join(c["id"] for c in regressions))
            break

    if session.status == "running":
        session.status = "idle"
    session.save()
    return session.events[start_index:]
