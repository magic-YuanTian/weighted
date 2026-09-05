"""Session state: workspace, requirement store, append-only event log.

The v3 backend is stateless because the client can hold a document. An agent
loop cannot be — it owns files on disk and a trajectory. So state lives here,
and the price is paid back in study data: the event log *is* the interaction
record, written to runs/<id>/session.json after every step.
"""

import json
import os
import re
import time
import uuid

from .tools import Attachments, Workspace
from . import requirements as R

RUNS_DIR = os.environ.get(
    "WT_RUNS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs"))

_SESSIONS = {}


# The three conditions this app is run in, decided when the session is created
# and never afterwards. "weighted" is WeightText: the brief is extracted into a
# requirement list, every step is verified, the gate holds finish. "insitu" is
# the same model, the same tools and the same screen with the requirement
# machinery removed but the workspace still answering to the user — select a
# passage to ask for an anchored replace or insert, or type into the file.
# "baseline" removes that too: a chat, a workspace to read, and a message box.
# The three nest (baseline ⊂ insitu ⊂ weighted), so the study can separate
# what the requirement object adds from what pointing at the text adds. A
# condition is a property of the session rather than a switch anyone can flip
# mid-run, and it is written into the event log and session.json so no run's
# condition has to be inferred.
MODES = ("weighted", "insitu", "baseline")


def _limit(name):
    """A study time limit in seconds, or None when unset. WEIGHTTEXT_SOFT_LIMIT
    turns the clock amber; WEIGHTTEXT_HARD_LIMIT hands the work in as it
    stands. Both are the client's to enforce — the server only reports them,
    so a session can be watched from a second tab without a second clock."""
    try:
        v = int(os.environ.get(name, "0") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else None


SOFT_LIMIT = _limit("WEIGHTTEXT_SOFT_LIMIT")
HARD_LIMIT = _limit("WEIGHTTEXT_HARD_LIMIT")


class Session:
    def __init__(self, brief="", session_id=None, mode="weighted", participant=None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.mode = mode if mode in MODES else "weighted"
        # Who and what, for the analysis: the participant id comes from the
        # URL the experimenter opened, the task id from the picker. Neither
        # changes what the run does; both are what lets a session.json be
        # joined to a questionnaire row without guessing.
        self.study = {"participant": (participant or None), "task": None}
        self.submitted = None         # when the work was handed in (epoch s)
        self.brief = brief or ""
        self.root = os.path.join(RUNS_DIR, self.id)
        self.workspace = Workspace(os.path.join(self.root, "workspace"))
        # reference material, deliberately outside the workspace so the
        # requirement checker never sees it
        self.attachments = Attachments(os.path.join(self.root, "attachments"))
        # where run_command's helper scripts and intermediates go. Outside the
        # workspace for the same reason attachments are: everything inside it
        # is a deliverable, and a stray clean.py would be checked as one.
        self.scratch = os.path.join(self.root, "scratch")
        self._proc = None             # the running run_command, for /pause
        self.requirements = []
        self.events = []
        self.llm_messages = []
        self.step_count = 0
        self.status = "idle"          # idle | running | paused | done
        # Nothing holds the baseline's finish: there is nothing to hold it on.
        self.gate_on = self.mode == "weighted"
        self.pending_steer = None
        self.questions = []
        self.unmapped = []
        # A recorded pre-run being handed out through this session, or None.
        # See agent/replay.py; written to session.json so a restored session
        # carries on where the recording stood.
        self.replay = None
        self.created = time.time()

    # The two things a condition decides, named once. Every branch in the
    # loop, the tools and the routes asks one of these rather than comparing
    # mode strings, so adding a condition is a change here and nowhere else.
    @property
    def verified(self):
        """The requirement machinery — extraction, the rail, the verifier and
        the gate — exists in this session."""
        return self.mode == "weighted"

    @property
    def steerable(self):
        """The workspace answers to the user: anchored replace/insert and
        direct editing. The chat baseline withholds both."""
        return self.mode != "baseline"

    def started_at(self):
        """When the task began: the first message. None before it."""
        return next((e["ts"] for e in self.events if e["type"] == "user"), None)

    # ---------------------------------------------------------------- log
    def log(self, etype, **payload):
        event = {"i": len(self.events), "type": etype, "ts": time.time(), **payload}
        self.events.append(event)
        return event

    def steps(self):
        return [e for e in self.events if e["type"] == "step"]

    # ---------------------------------------------------------------- state
    def snapshot(self):
        return {
            "sessionId": self.id,
            "mode": self.mode,
            "brief": self.brief,
            "status": self.status,
            "stepCount": self.step_count,
            "gateOn": self.gate_on,
            "requirements": self.requirements,
            "counts": R.counts(self.requirements),
            "blocking": [r["id"] for r in R.blocking(self.requirements)],
            "events": self.events,
            "files": [{"path": f, "text": self.workspace.read(f)}
                      for f in self.workspace.list()],
            "attachments": self.attachments.meta(),
            "questions": self.questions,
            "unmapped": self.unmapped,
            "study": self.study,
            "startedAt": self.started_at(),
            "now": time.time(),   # the clock the client counts on from
            "submitted": self.submitted,
            "limits": {"soft": SOFT_LIMIT, "hard": HARD_LIMIT},
        }

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "session.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "sessionId": self.id, "mode": self.mode, "brief": self.brief,
                "created": self.created, "study": self.study,
                "submitted": self.submitted,
                "status": self.status, "stepCount": self.step_count,
                "requirements": self.requirements, "events": self.events,
                "questions": self.questions,
                "replay": self.replay,
                "files": {f: self.workspace.read(f) for f in self.workspace.list()},
                "attachments": [a["name"] for a in self.attachments.meta()],
            }, fh, ensure_ascii=False, indent=2)


def create(brief="", mode="weighted", participant=None):
    s = Session(brief, mode=mode, participant=participant)
    _SESSIONS[s.id] = s
    s.log("session", brief=brief, mode=s.mode, participant=s.study["participant"])
    s.save()
    return s


def get(session_id):
    s = _SESSIONS.get(session_id)
    if s is not None:
        return s
    return _load(session_id)


def _load(session_id):
    """Restore a session from runs/<id>/session.json after a server restart.

    The durable state — brief, requirements, events, the workspace on disk —
    all comes back. The trajectory does not: llm_messages is a model transcript
    and is not written out. A restored session therefore resumes from the brief
    and the workspace as it stands, which is what the agent would re-read
    anyway; the event log keeps the record of how it got there."""
    if not session_id or not re.fullmatch(r"[0-9a-f]{12}", session_id):
        return None
    path = os.path.join(RUNS_DIR, session_id, "session.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        s = Session(brief=data.get("brief", ""), session_id=session_id,
                    mode=data.get("mode") or "weighted")
        s.study = {**s.study, **(data.get("study") or {})}
        s.submitted = data.get("submitted")
        s.requirements = data.get("requirements") or []
        s.events = data.get("events") or []
        s.questions = data.get("questions") or []
        s.replay = data.get("replay")
        s.step_count = data.get("stepCount", 0)
        s.status = data.get("status", "idle")
        if s.status == "running":       # the step died with the old server
            s.status = "idle"
        # Without this the next step is a system prompt and nothing else, and
        # the model answers an empty turn. The brief is the one message that
        # was always there.
        if s.brief:
            s.llm_messages = [{"role": "user", "content": s.brief}]
        s.created = data.get("created", s.created)
        _SESSIONS[session_id] = s
        print(f"[session] restored {session_id} ({s.mode}) from disk", flush=True)
        return s
    except Exception as e:                                    # noqa: BLE001
        print(f"[session] could not restore {session_id}: {e}", flush=True)
        return None


def all_sessions():
    return list(_SESSIONS.values())
