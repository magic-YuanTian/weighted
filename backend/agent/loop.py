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

SYSTEM = """You are a writing agent working in a small file workspace.

Rules of this workspace:
- Exactly one file per deliverable, named after it (cover_letter.md,
  recruiter_email.md). Start each file with a '# Title' heading line. Never keep
  the same content in two files — a duplicate makes every check ambiguous.
- Prefer edit_file over write_file when revising: it cannot disturb text you
  did not name.
- run_check runs a deterministic checker. It is free and it does not flatter
  you. Run it after a substantive edit and before you try to finish.
- Take exactly one action at a time and say, in one short sentence, why.
- Do not claim a requirement is met. Make it checkable, then check it.
- Every edit comes back with the checker's verdicts and a "blocking finish"
  line. When it says nothing is blocking, run_check once and then finish. Do
  not keep polishing a package that already passes.
- Close the run with the finish tool, never with a message. A reply is not an
  ending: it stops the run without closing it, and leaves the work sitting at
  "in progress" with every requirement green. The one reason to reply instead
  of acting is being genuinely stuck — a requirement you cannot satisfy, or a
  decision only the user can make. Then say so in one sentence, and say what
  you need.
- The user reads your messages beside the workspace, with every file already
  open in front of them. Never paste a file's contents into one, in whole or
  in part. Say what you did, not what it says.
"""

MAX_HISTORY_BLOCKS = 14


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
        lines.append(f"  {name} — {tools.word_count(session.workspace.read(name))} words")

    att = session.attachments.meta()
    if att:
        lines.append("Attached by the user — read-only reference material, not part of")
        lines.append("the deliverable and not seen by the checker. Use read_attachment:")
        for a in att:
            lines.append(f"  {a['name']} — {a['lines']} lines, {a['chars']} characters")

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
    system = SYSTEM + ("\n" + standing if standing else "") + "\n" + digest
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
        {"key": "system", "label": "Agent instructions", "tokens": llm.estimate_tokens(SYSTEM)},
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
    if name in ("read_file", "write_file", "edit_file"):
        return args.get("path") or ""
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
        if name in ("write_file", "edit_file"):
            return "The change didn't match the current text, so nothing was changed."
        return ""
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
    if name in ("write_file", "edit_file"):
        lines = []
        for chg in changed:
            req = by_id.get(chg["id"])
            word = _VWORDS.get(chg["verdict"], chg["verdict"])
            detail = _plain_detail(req)
            lines.append(f"{chg['id']} is now {word}" + (f": {detail}" if detail else ""))
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
        msg = llm.chat(messages, tools=tools.TOOL_SCHEMAS)
    except Exception as e:                                    # noqa: BLE001
        session.status = "paused"
        session.log("error", text=f"{type(e).__name__}: {e}")
        session.save()
        return session.events[start_index:]

    thought = (msg.content or "").strip()
    calls = list(msg.tool_calls or [])

    if not calls and not thought:
        # Neither a call nor a word. This is not the agent talking to the user,
        # and it happens most often once every requirement is satisfied and the
        # model has nothing left to say. Logged as speech it renders an empty
        # bubble in the chat, and pressing continue only adds another one --
        # the prompt is unchanged, so the next turn comes back just as empty.
        # Name it for what it is, and give the next turn something to answer.
        session.llm_messages.append({
            "role": "user",
            "content": ("That turn was empty: no tool call and no message. If "
                        "the work is complete, call finish. Otherwise take the "
                        "next concrete action."),
        })
        session.status = "idle"
        session.log("notice",
                    text=("The agent returned an empty turn — nothing to do and "
                          "nothing to say. Continue asks it again; if everything "
                          "is satisfied, it should finish."))
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

        if name == "finish":
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
