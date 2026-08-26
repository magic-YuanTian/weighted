"""Session state: workspace, requirement store, append-only event log.

The v3 backend is stateless because the client can hold a document. An agent
loop cannot be — it owns files on disk and a trajectory. So state lives here,
and the price is paid back in study data: the event log *is* the interaction
record, written to runs/<id>/session.json after every step.
"""

import json
import os
import time
import uuid

from .tools import Attachments, Workspace
from . import requirements as R

RUNS_DIR = os.environ.get(
    "WT_RUNS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs"))

_SESSIONS = {}


class Session:
    def __init__(self, brief="", session_id=None):
        self.id = session_id or uuid.uuid4().hex[:12]
        self.brief = brief or ""
        self.root = os.path.join(RUNS_DIR, self.id)
        self.workspace = Workspace(os.path.join(self.root, "workspace"))
        # reference material, deliberately outside the workspace so the
        # requirement checker never sees it
        self.attachments = Attachments(os.path.join(self.root, "attachments"))
        self.requirements = []
        self.events = []
        self.llm_messages = []
        self.step_count = 0
        self.status = "idle"          # idle | running | paused | done
        self.gate_on = True
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
                "sessionId": self.id, "brief": self.brief, "created": self.created,
                "status": self.status, "stepCount": self.step_count,
                "requirements": self.requirements, "events": self.events,
                "questions": self.questions,
                "files": {f: self.workspace.read(f) for f in self.workspace.list()},
                "attachments": [a["name"] for a in self.attachments.meta()],
            }, fh, ensure_ascii=False, indent=2)


def create(brief=""):
    s = Session(brief)
    _SESSIONS[s.id] = s
    s.log("session", brief=brief)
    s.save()
    return s


def get(session_id):
    return _SESSIONS.get(session_id)


def all_sessions():
    return list(_SESSIONS.values())
