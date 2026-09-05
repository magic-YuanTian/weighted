"""Scripted replay of a recorded pre-run through the ordinary weighted flow.

The weighted condition opens on ten unattended agent steps that every
participant should see alike. Rather than showing them a finished transcript,
the recording is played back through the same routes a live run uses: the
participant pastes the brief and sends it, the requirement list that was
extracted at recording time comes back as the extraction, "start the agent"
hands out the recorded steps one at a time at roughly the pace they were
taken — each with the files as they stood and the rail as it read afterwards
— and the run stops where the recording stopped: the agent's finish, a pause,
or a reply. From there the live agent continues, its transcript cut to the
same point, so it remembers exactly what the recording shows.

Anything the participant does that would have changed what the agent did —
a message, a steer, a hand edit, a change to the requirement list, the gate,
an answered question — ends the replay early and hands the run to the live
agent at that point (divert). Pausing does not: the recording simply waits.

A recording is the run directory of a weighted screening run made with
screen_fixed.py in recording mode (CONTINUE=0): trace.json (the extraction as
committed and one group per loop.step() call), llm_messages.json (the
transcript), session.json (the requirement list after the end-of-run
recheck), attachments/ and scratch/. One directory per benchmark task id under
tasks/traces/, matched to a session by the brief's text.
"""

import copy
import json
import os
import re
import shutil
import time

from . import requirements as R

TRACES_DIR = os.environ.get(
    "WT_TRACES_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "tasks", "traces"))

# How long a replayed step takes, relative to how long it took when recorded.
# 0 replays instantly (tests); 1 is the recorded pace, capped so a forty-second
# biography draft does not hold the screen for forty seconds.
PACE = float(os.environ.get("WEIGHTTEXT_REPLAY_PACE", "1") or 0)
STEP_CAP_SECONDS = 12.0
EXTRACT_SECONDS = 3.0

_CACHE = {}


def _norm(text):
    return " ".join((text or "").split())


def _read(task, name):
    path = os.path.join(TRACES_DIR, task, name)
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    _CACHE[path] = (mtime, data)
    return data


def trace(task):
    if not task or not re.fullmatch(r"[A-Za-z0-9_-]+", task):
        return None
    return _read(task, "trace.json")


def trace_meta(task):
    """{"steps", "status"} of a task's recording, or None."""
    t = trace(task)
    if not t:
        return None
    return {"steps": t.get("step_count"), "status": t.get("status"),
            "groups": len(t.get("groups") or []), "cut": t.get("cut")}


def tasks():
    if not os.path.isdir(TRACES_DIR):
        return []
    return sorted(d for d in os.listdir(TRACES_DIR)
                  if os.path.exists(os.path.join(TRACES_DIR, d, "trace.json")))


def match(brief):
    """The recording whose brief is this text, whitespace aside -> (task,
    trace) or None. The brief is the key so that the picker, a paste from the
    printed sheet and a retyped message all land on the same recording."""
    key = _norm(brief)
    if not key:
        return None
    for task in tasks():
        t = trace(task)
        if t and _norm(t.get("brief")) == key:
            return task, t
    return None


def active(session):
    """A recording is being handed out: armed, not diverted, steps left."""
    rp = getattr(session, "replay", None)
    return bool(rp) and not rp.get("live") and rp["cursor"] < rp["total"]


def armed(session):
    rp = getattr(session, "replay", None)
    return bool(rp) and not rp.get("live")


def arm(session, task, t):
    """Attach a recording to a weighted session at its first message."""
    session.replay = {"task": task, "cursor": 0, "total": len(t["groups"]),
                      "live": False, "source": t.get("session")}
    src = os.path.join(TRACES_DIR, task)
    att = os.path.join(src, "attachments")
    if os.path.isdir(att):
        for name in sorted(os.listdir(att)):
            p = os.path.join(att, name)
            if os.path.isfile(p) and session.attachments.read(name) is None:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    session.attachments.add(name, fh.read())
    scratch = os.path.join(src, "scratch")
    if os.path.isdir(scratch):
        shutil.rmtree(session.scratch, ignore_errors=True)
        shutil.copytree(scratch, session.scratch)
    if not session.study.get("task"):
        session.study["task"] = task
    # for the record, not for the participant: `trace` draws only under #dev
    session.log("trace", kind="replay-armed", task=task, source=t.get("session"),
                steps=t.get("step_count"),
                text=f"Replaying the recorded pre-run of {task}: {len(t['groups'])} "
                     f"turn(s), {t.get('step_count')} step(s), then live.")


def first_message(session, t):
    """What /message does after extraction, from the recording."""
    if PACE:
        time.sleep(EXTRACT_SECONDS * PACE)
    session.requirements = copy.deepcopy(t["requirements"])
    session.questions = copy.deepcopy(t.get("questions") or [])
    session.unmapped = copy.deepcopy(t.get("unmapped") or [])
    session.log("extracted", ids=[r["id"] for r in session.requirements],
                questions=session.questions, unmapped=len(session.unmapped),
                coverage=t.get("coverage") or [None, None])


def _apply_files(session, files):
    for name in session.workspace.list():
        if name not in files:
            os.remove(session.workspace.path(name))
    for name, text in files.items():
        if session.workspace.read(name) != text:
            session.workspace.write(name, text)


def step(session):
    """Hand out the next recorded turn as if the agent had just taken it.
    Returns the events it produced, re-stamped into this session's log."""
    rp = session.replay
    t = trace(rp["task"])
    g = t["groups"][rp["cursor"]]
    if PACE:
        time.sleep(min(float(g.get("secs") or 0), STEP_CAP_SECONDS) * PACE)
    _apply_files(session, g["files"])
    session.requirements = copy.deepcopy(g["requirements"])
    session.step_count = g["step_count"]
    session.status = g["status"]
    events = []
    now = time.time()
    for e in g["events"]:
        ev = {**e, "i": len(session.events), "ts": now}
        session.events.append(ev)
        events.append(ev)
    rp["cursor"] += 1
    if rp["cursor"] >= rp["total"]:
        # the recording is spent: from here the live agent continues, and it
        # must remember exactly what the participant has just watched
        transcript = _read(rp["task"], "llm_messages.json")
        if transcript:
            session.llm_messages = copy.deepcopy(transcript)
        # Returned with the turn's events so /step can see it: a recording
        # cut mid-run (a fixed prefix of steps, which is how the code tasks
        # are recorded, rather than a halt) has nothing in it that stops the
        # auto-runner, and the runner must stop here all the same — the live
        # agent is the participant's to start. Nothing is said in the chat:
        # `trace` draws only under #dev, and the status stays as recorded, so
        # no pause and no "you continued" line mark the seam.
        events.append(session.log(
            "trace", kind="replay-end",
            text="The recorded pre-run is over; the agent is live from here."))
    session.save()
    return events


def recheck(session, judge=False):
    """The end-of-run recheck during a replay: the recorded verdicts, not a
    fresh judge call over the same bytes. Returns the chips it changed."""
    rp = session.replay
    before = {r["id"]: (r.get("report") or {}).get("verdict") for r in session.requirements}
    if rp["cursor"] >= rp["total"]:
        final = (_read(rp["task"], "session.json") or {}).get("requirements")
        if final:
            session.requirements = copy.deepcopy(final)
    chips = [{"id": r["id"], "verdict": (r.get("report") or {}).get("verdict"),
              "from": before.get(r["id"])}
             for r in session.requirements
             if r.get("status") == "active"
             and (r.get("report") or {}).get("verdict") != before.get(r["id"])]
    session.log("recheck", judge=bool(judge), chips=chips, counts=R.counts(session.requirements))
    if rp["cursor"] >= rp["total"]:
        rp["live"] = True          # the run is the participant's now
    session.save()
    return chips


def divert(session, why):
    """The participant acted before the recording ran out. The live agent
    takes over here, with the transcript cut to the last step shown."""
    rp = getattr(session, "replay", None)
    if not rp or rp.get("live"):
        return False
    t = trace(rp["task"]) or {}
    transcript = _read(rp["task"], "llm_messages.json") or []
    k = rp["cursor"]
    if k > 0 and t.get("groups"):
        cut = t["groups"][k - 1].get("llm_len") or len(transcript)
        session.llm_messages = copy.deepcopy(transcript[:cut])
    # k == 0: nothing handed out yet; the brief the participant sent is the
    # whole transcript, and it is already there
    rp["live"] = True
    rp["diverted_at"] = k
    rp["diverted_by"] = why
    session.log("trace", kind="replay-diverted", why=why, after=k,
                text=f"The participant acted ({why}) after {k} of {rp['total']} "
                     f"recorded turn(s); the agent is live from here.")
    return True
