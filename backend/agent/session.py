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


# The two conditions this app is run in, decided when the session is created
# and never afterwards. "weighted" is WeightText: the brief is extracted into a
# requirement list, every step is verified, the gate holds finish. "baseline"
# is the same model, the same tools and the same screen with all of that
# removed — the comparison the study rests on, so it is a property of the
# session rather than a switch anyone can flip mid-run, and it is written into
# the event log and session.json so no run's condition has to be inferred.
MODES = ("weighted", "baseline")


class Session:
    def __init__(self, brief="", session_id=None, mode="weighted"):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.mode = mode if mode in MODES else "weighted"
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
        self.created = time.time()

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
        }

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, "session.json"), "w", encoding="utf-8") as fh:
            json.dump({
                "sessionId": self.id, "mode": self.mode, "brief": self.brief,
                "created": self.created,
                "status": self.status, "stepCount": self.step_count,
                "requirements": self.requirements, "events": self.events,
                "questions": self.questions,
                "files": {f: self.workspace.read(f) for f in self.workspace.list()},
                "attachments": [a["name"] for a in self.attachments.meta()],
            }, fh, ensure_ascii=False, indent=2)


def create(brief="", mode="weighted"):
    s = Session(brief, mode=mode)
    _SESSIONS[s.id] = s
    s.log("session", brief=brief, mode=s.mode)
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
        s.requirements = data.get("requirements") or []
        s.events = data.get("events") or []
        s.questions = data.get("questions") or []
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
