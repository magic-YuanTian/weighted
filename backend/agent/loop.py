"""One step of the agent loop.

The UI drives stepping (one HTTP request per step) rather than the server
running to completion: ⏸ / ⏭ / ⏹ then mean exactly what they say, every step is
verified before the next one is planned, and nothing depends on a streaming
transport surviving a proxy.
"""

import os
import re

from . import llm
from . import requirements as R
from . import tools
from . import verifier

# The agent's standing instructions, in two compositions for three conditions.
#
# The study compares WeightText against two controls that are the same model,
# the same tools and the same screen with the requirement machinery removed —
# so the prompt has to differ by exactly that machinery and nothing else. The
# pieces below are composed twice rather than written out twice: a rule about
# how to use edit_file is shared verbatim, and only the rules that talk about
# checking, verdicts and requirements have a second version. What is NOT
# withheld from the controls is tool advice — copy the source before repairing
# it, one repair per command — because a control that is badly prompted
# measures prompt engineering, not the interface.
#
# The in-situ condition gets the baseline's composition: what it adds over the
# chat baseline (anchored replace/insert, typing into the file) is a way for
# the user to reach the agent, and it arrives as ordinary user messages and
# hand edits the agent is told about — nothing in the standing instructions
# has to change for it.
#
# The weighted composition below was byte-identical to the single SYSTEM string
# that preceded the split until the `targets` rule was added to _VERDICTS on
# 2026-09-04; runs measured before that were measured without that sentence.

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
- Every change to a file names, in targets, the ids of the requirements it is
  aimed at — the ones you expect it to settle, not everything the file has to
  meet. Never leave targets empty while a requirement is red. Attempts are
  counted per requirement: three changes aimed at one requirement that leave
  it unmet pause the run for the user to look at.
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
    this session's condition. Only the weighted condition has a checker to be
    told about; both controls get the composition that never mentions one."""
    plain = mode != "weighted"
    base = SYSTEM_BASELINE if plain else SYSTEM
    if not tools.shell_enabled():
        return base
    return base + (SHELL_RULES_BASELINE if plain else SHELL_RULES)


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
        lines.append("the deliverable" + (" and not seen by the checker"
                                          if session.verified else "")
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
    if not session.verified:
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
        for rid in meta.get("targets") or []:
            req = by_id.get(rid)
            verdict = ((req or {}).get("report") or {}).get("verdict")
            if verdict in ("violated", "partial"):
                n = attempts(session, rid) + 1
                lines.append(f"This change was aimed at {rid}, which is still "
                             f"{_VWORDS.get(verdict, verdict)} — attempt {n} of "
                             f"{STUCK_AFTER}.")
        if not R.blocking(session.requirements):
            lines.append("Everything checked so far is met.")
        return "\n".join(lines)
    if name == "finish":
        return (args.get("summary") or "").strip()
    return ""


def _aim_lines(session, targets):
    """What the change just made was aimed at, and how many attempts that is.
    The step is not in the event log yet when this runs, so the count is the
    log's plus this one."""
    lines = []
    for rid in targets or []:
        req = next((r for r in session.requirements if r["id"] == rid), None)
        if not req:
            continue
        verdict = (req.get("report") or {}).get("verdict")
        if verdict in ("violated", "partial"):
            n = attempts(session, rid) + 1
            lines.append(f"aimed at {rid}: still {verdict} — attempt {n} of "
                         f"{STUCK_AFTER}"
                         + ("; the run pauses here for the user"
                            if n >= STUCK_AFTER else ""))
        else:
            lines.append(f"aimed at {rid}: now {verdict}")
    return lines


def _verdict_lines(session, changed, targets=None):
    """The verifier's answer to an edit, in the observation the agent reads."""
    lines = _aim_lines(session, targets)
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


# How many attempts a requirement may survive before the loop gives up on it.
STUCK_AFTER = int(os.environ.get("WEIGHTTEXT_STUCK_AFTER", "3"))


def _slug(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def attempts(session, req_id, limit=None):
    """How many successful changes AIMED AT `req_id` have gone by since it was
    last satisfied, or since the user last continued the run — capped at
    `limit`, because past the cap the count no longer changes anything.

    "Aimed at" is the agent's own word: every file-changing tool in the
    weighted condition carries `targets`, the requirement ids the change is
    meant to settle, and only those changes count. An edit that fixes the
    date column is not an attempt at the whitespace rule, however many times
    it touches the file — counting it as one paused runs three steps in over
    requirements the agent had not started on.

    Counted from the event log rather than from a field on the session, so a
    reloaded session gets the same answer. A `resume` event — the user pressed
    continue after a pause — is a floor: what came before it was the user's to
    look at, and they have looked.
    """
    limit = STUCK_AFTER if limit is None else limit
    req = next((r for r in session.requirements if r["id"] == req_id), None) or {}
    scope = req.get("scope") or {}
    want = scope.get("name") if scope.get("kind") == "file" else None
    n = 0
    for ev in reversed(session.events):
        if ev.get("type") == "resume":
            break
        if ev.get("type") != "step":
            continue
        if any(c.get("id") == req_id and c.get("verdict") == "satisfied"
               for c in ev.get("chips") or []):
            break
        meta = ev.get("meta") or {}
        if meta.get("kind") != "edit" or not meta.get("ok"):
            continue
        targets = meta.get("targets") or []
        if targets:
            aimed = req_id in targets
        else:
            # The agent said nothing about what the change was for. That is
            # not a way out of the count: an unattributed change counts
            # against every requirement open at the time, in the file it is
            # scoped to — the rule this one replaced, kept as the floor for an
            # agent that will not name its targets (gpt-5-nano, at first, on
            # every edit). Only edits with no targets field at all — the
            # baseline's — count for nothing, and the baseline has no rail.
            path = meta.get("path")
            aimed = ("targets" in meta
                     and not (want and path and _slug(path) != _slug(want)))
        if aimed:
            n += 1
            if n >= limit:
                break
    return n


def _stuck(session, limit=None):
    """Requirements still violated after `limit` attempts aimed at them."""
    limit = STUCK_AFTER if limit is None else limit
    return sorted(
        r["id"] for r in session.requirements
        if r.get("status") == "active"
        and (r.get("report") or {}).get("verdict") in ("violated", "partial")
        and attempts(session, r["id"], limit) >= limit)


_STUCK_WORDS = re.compile(r"\b(stuck|blocked|cannot|can't|unable|need|permission|"
                          r"clarif|which|should i|contradict)\b|\?", re.I)
_ANNOUNCES = re.compile(r"^\s*(i will|i'll|i am going to|i'm going to|next,? i|let me)\b", re.I)


RESUME_TEXT = ("Continue. The user has read the pause and chose to go on; the "
               "attempt count starts over. Take the next concrete action now — "
               "do not reply with a message.")
RESUME_RETRY = ("The user has already read that and pressed continue. Do not "
                "reply again; act — make the next tool call now.")
PAUSE_ASK = ("The run has paused: {notice} Tell the user, in one sentence, where "
             "you are stuck and what from them would unblock you. One sentence, "
             "no tool call.")

_SENTENCE_END = re.compile(r"[.!?。！？](?=\s+[A-Z“\"(]|$)")
_ABBREV = re.compile(r"(?:^|[\s(“\"])(?:[A-Z]\.)+$|\b(?:e\.g|i\.e|etc|vs|Mr|Ms|Dr|No)\.$")


def _one_sentence(text):
    """The first sentence of a reply, whole — gpt-5.6 at its lowest effort
    likes to say the same thing twice in a row, and the pause wants one line.
    A period inside an abbreviation ("S.S. Nieuw Amsterdam", "e.g.") does not
    end the sentence."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = line.strip("\"'“”‘’ ")
    for m in _SENTENCE_END.finditer(line):
        head = line[:m.end()]
        if _ABBREV.search(head):
            continue
        return head.strip()
    return line


def pause_word(session, notice):
    """At a pause, one sentence from the agent to the user: where it is stuck
    and what would unblock it. Spoken here rather than on continue — the
    participant reads it beside the notice, and continue then acts. The ask
    itself is not kept in the transcript; the agent's sentence is, so what the
    model last said is what the participant last read. Returns the sentence
    or None (a failed call just leaves the pause with the notice alone)."""
    messages, _ = build_messages(session)
    messages = messages + [{"role": "user", "content": PAUSE_ASK.format(notice=notice)}]
    try:
        msg = llm.chat(messages, model=llm.AGENT_MODEL, max_tokens=300)
    except Exception as e:                                    # noqa: BLE001
        print(f"[loop] pause_word failed: {type(e).__name__}: {e}", flush=True)
        return None
    text = _one_sentence((msg.content or "").strip())
    if not text:
        return None
    session.llm_messages.append({"role": "assistant", "content": text})
    session.log("assistant", text=text, pause=True)
    return text


def _not_speech(session, text):
    """A reply that is not the agent talking to the user, and what to tell it.

    Three shapes, all seen from gpt-5-nano: the deliverable pasted into the
    chat (a wall of code where a sentence belongs, or anything at all while
    the workspace is still empty); a narration of the next action ("I will
    create a local copy…") in place of the action; and a closing summary ("I
    have delivered solution.py…") in place of finish, with nothing blocking.
    None of those is the one thing a reply is for — saying it is stuck — and
    a reply that says that (stuck, blocked, need, a question mark) is left
    alone. Returns the nudge to send, or None.
    """
    if not session.workspace.list() or text.count("\n") >= 15:
        return ("That reply is not a file. Nothing written in a message reaches "
                "the workspace or the checker: create or change the deliverable "
                "with write_file, edit_file or insert_file, and use a message only "
                "to say you are stuck. Take the next concrete action now.")
    if _STUCK_WORDS.search(text):
        return None
    if _ANNOUNCES.match(text):
        return ("That reply announces an action instead of taking it. Take it: "
                "call the tool now.")
    if session.verified and not R.blocking(session.requirements):
        return ("Nothing is blocking. If the work is complete, call finish; "
                "otherwise take the next concrete action. A message does not "
                "close the run.")
    if not session.verified:
        return ("If the work is complete, call finish; otherwise take the next "
                "concrete action. A message does not close the run.")
    return None


def step(session):
    """Plan and take exactly one action. Returns the events it produced."""
    if session.status == "done":
        return []
    start_index = len(session.events)
    if session.status == "paused":
        # The user pressed continue (or steered) after a pause. Attempt counts
        # start over from here: whatever the rail had to say about the pause,
        # they have seen it. The model has to be told so: the last thing it
        # read was an observation ending "the run pauses here for the user",
        # and left there it answers the pause instead of the user — "I cannot
        # continue without review" — and the first press of continue produces
        # a sentence, not a step.
        session.log("resume")
        session.llm_messages.append({"role": "user", "content": RESUME_TEXT})
    session.status = "running"

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
          f"calling {llm.AGENT_MODEL}…", flush=True)
    try:
        msg = llm.chat(messages, tools=tools.schemas(session.mode),
                       model=llm.AGENT_MODEL)
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
        last_user = next((m for m in reversed(session.llm_messages)
                          if m.get("role") == "user"), None)
        if last_user and last_user.get("content") == RESUME_TEXT:
            # The user pressed continue and the agent answered with words
            # anyway. Once, it is sent back to act; the second time it is
            # speech like any other and ends the turn.
            session.llm_messages.append({"role": "assistant", "content": thought})
            session.llm_messages.append({"role": "user", "content": RESUME_RETRY})
            session.status = "idle"
            session.log("trace", kind="resume-reply",
                        text=("The agent replied instead of acting after continue; "
                              "the loop asked it to act."))
            session.save()
            return session.events[start_index:]
        # A message that IS the deliverable — the file's contents pasted into
        # the chat, a wall of code where a sentence belongs — is the model
        # failing to use its tools, not a decision to talk to the user.
        # gpt-5-nano does this on about one first turn in four, and a run that
        # ends there has nothing on disk and nothing checked. So it is sent
        # back to the tools, at most twice in a row; a reply that survives
        # that is treated as speech, as any other reply is.
        why = _not_speech(session, thought)
        nudged = sum(1 for e in session.events[-6:]
                     if e.get("type") == "trace" and e.get("kind") == "paste")
        if why and nudged < 2:
            session.llm_messages.append({"role": "assistant", "content": thought})
            session.llm_messages.append({"role": "user", "content": why})
            session.status = "idle"
            session.log("trace", kind="paste",
                        text=("The model replied instead of acting — a pasted file, "
                              "an announced action, or a summary in place of finish; "
                              "the loop asked it to use the tools."))
            session.save()
            return session.events[start_index:]

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

        if name == "finish" and not session.verified:
            # There is nothing to verify and nothing to hold the finish on.
            # The agent's word that it is done is the whole of the controls'
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
        elif not session.verified:
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
                observation += "\n" + _verdict_lines(session, changed,
                                                     meta.get("targets"))

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
            text = ("Loop paused: critical requirement broke — "
                    + ", ".join(c["id"] for c in regressions))
            session.log("notice", text=text)
            pause_word(session, text)
            break

        # Three changes aimed at the same requirement and it is still red.
        # Either the agent cannot settle it or the verdict is wrong, and both
        # of those are for a person to look at — going round a fourth time
        # only spends the budget.
        stuck = _stuck(session)
        if stuck:
            session.status = "paused"
            text = (f"Loop paused: {', '.join(stuck)} still not met "
                    f"after {STUCK_AFTER} changes aimed at "
                    f"{'it' if len(stuck) == 1 else 'them'}. Either "
                    f"steer it, or check whether the verdict is "
                    f"right. Continue starts the count over.")
            session.log("notice", text=text, stuck=stuck)
            pause_word(session, text)
            break

    if session.status == "running":
        session.status = "idle"
    session.save()
    return session.events[start_index:]
