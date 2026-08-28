"""HTTP surface for the v4 agent (blueprint mounted on the existing server).

The UI drives the loop: one POST per step. Everything the rail needs comes back
in a single `snapshot`, so the client never has to reconstruct state from a
stream of patches.
"""

import json
import os
import threading
import traceback

from flask import Blueprint, jsonify, request

from agent import codex
from agent import extract as extract_mod
from agent import loop
from agent import requirements as R
from agent import session as sessions
from agent import verifier

bp = Blueprint("agent", __name__, url_prefix="/api/agent")


def _session(required=True):
    data = request.json or {}
    s = sessions.get(data.get("sessionId") or request.args.get("sessionId"))
    if s is None and required:
        raise LookupError("unknown sessionId — create a session first")
    return s, data


def _fail(e, code=500):
    traceback.print_exc()
    return jsonify({"error": f"{type(e).__name__}: {e}"}), code


@bp.errorhandler(LookupError)
def _missing(e):
    return jsonify({"error": str(e)}), 404


# ------------------------------------------------------------------ session

TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")


@bp.route("/presets", methods=["GET"])
def presets():
    """The benchmark tasks, ready to load into the brief box. Absent when the
    tasks directory has not been populated — the UI just hides the picker."""
    try:
        manifest = os.path.join(TASKS_DIR, "manifest.json")
        if not os.path.exists(manifest):
            return jsonify({"tasks": []})
        with open(manifest, encoding="utf-8") as fh:
            tasks = json.load(fh)
        out = []
        for t in tasks:
            path = os.path.join(TASKS_DIR, f"{t['id']}.txt")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                brief = fh.read()
            att = []
            for name in (t.get("attachments") or []):
                p = os.path.join(TASKS_DIR, "data", name)
                if not os.path.exists(p):
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                att.append({"name": name, "chars": len(text),
                            "lines": text.count("\n") + 1})
            out.append({**t, "brief": brief, "attachments": att})
        return jsonify({"tasks": sorted(out, key=lambda t: t.get("n", 0))})
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)




@bp.route("/attach", methods=["POST"])
def attach():
    """Copy one of the shipped data files into this session as read-only
    reference material. Attachments never enter the workspace, so they are
    invisible to the requirement checker."""
    try:
        s, data = _session()
        names = data.get("names") or ([data["name"]] if data.get("name") else [])
        added = []
        for name in names:
            src = os.path.join(TASKS_DIR, "data", os.path.basename(name))
            if not os.path.exists(src):
                return jsonify({"error": f"no such data file: {name}"}), 404
            with open(src, encoding="utf-8", errors="replace") as fh:
                s.attachments.add(os.path.basename(name), fh.read())
            added.append(os.path.basename(name))
        if added:
            s.log("attach", names=added)
            s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/attachment", methods=["GET"])
def attachment():
    """The full text of one attachment, for the preview panel."""
    try:
        s = sessions.get(request.args.get("sessionId"))
        if s is None:
            raise LookupError("unknown sessionId")
        name = request.args.get("name") or ""
        text = s.attachments.read(name)
        if text is None:
            return jsonify({"error": f"no such attachment: {name}"}), 404
        return jsonify({"name": name, "text": text})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/session", methods=["POST"])
def create_session():
    try:
        data = request.json or {}
        s = sessions.create((data.get("brief") or "").strip())
        return jsonify(s.snapshot())
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


def _can_continue(s):
    if codex.enabled():
        return codex.can_continue(s)
    return s.status not in ("done", "paused")


@bp.route("/state", methods=["GET"])
def state():
    s = sessions.get(request.args.get("sessionId"))
    if s is None:
        return jsonify({"error": "unknown sessionId"}), 404
    # canContinue lets a client that lost its /step response mid-turn decide
    # whether to pick the run back up or leave it finished.
    snap = s.snapshot()
    snap["canContinue"] = _can_continue(s)
    return jsonify(snap)


@bp.route("/export", methods=["GET"])
def export():
    s = sessions.get(request.args.get("sessionId"))
    if s is None:
        return jsonify({"error": "unknown sessionId"}), 404
    snap = s.snapshot()
    snap["llmMessages"] = s.llm_messages
    return jsonify(snap)


# ------------------------------------------------------------------ review stage

@bp.route("/extract", methods=["POST"])
def extract():
    try:
        data = request.json or {}
        brief = (data.get("brief") or "").strip()
        s = sessions.get(data.get("sessionId"))
        if s is not None and brief:
            s.brief = brief
        result = extract_mod.extract(brief or (s.brief if s else ""))
        if s is not None:
            s.questions = result["questions"]
            s.unmapped = result["coverage"]["unmapped"]
            s.log("extract", proposed=len(result["requirements"]),
                  questions=len(result["questions"]),
                  coverage=[result["coverage"]["mapped"], result["coverage"]["total"]])
            s.save()
        return jsonify(result)
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/answer", methods=["POST"])
def answer():
    """Fold a clarification answer into one (not yet committed) requirement."""
    try:
        data = request.json or {}
        req = data.get("requirement")
        question = (data.get("question") or "").strip()
        ans = (data.get("answer") or "").strip()
        if not req or not question:
            return jsonify({"error": "requirement and question are required"}), 400
        if not ans or ans == "skipped":
            updated = R.normalize({**req, "assumed": data.get("assumed") or
                                   "unanswered — kept as extracted"})
            updated["id"] = req.get("id")
        else:
            updated = extract_mod.apply_answer(req, question, ans)
        s = sessions.get(data.get("sessionId"))
        if s is not None:
            # A question answered mid-run has to land in the live store, or the
            # answer changes nothing and the user has been asked for nothing.
            updated["report"] = next((r.get("report") for r in s.requirements
                                      if r["id"] == updated.get("id")), updated.get("report"))
            s.requirements = [updated if r["id"] == updated.get("id") else r
                              for r in s.requirements]
            s.questions = [{**q, "answer": ans or "skipped"}
                           if q.get("text") == question else q for q in s.questions]
            s.log("answer", question=question, answer=ans or "skipped",
                  requirementId=updated.get("id"))
            s.save()
            return jsonify({"requirement": updated, "snapshot": s.snapshot()})
        return jsonify({"requirement": updated})
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/commit", methods=["POST"])
def commit():
    """The review screen hands over the requirement list. Nothing steers the
    agent until this happens."""
    try:
        s, data = _session()
        s.requirements = R.normalize_all(data.get("requirements"))
        s.questions = data.get("questions") or s.questions
        if data.get("brief"):
            s.brief = data["brief"].strip()
        s.log("commit", ids=[r["id"] for r in s.requirements],
              pinned=[r["id"] for r in R.pinned(s.requirements)])
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


# ------------------------------------------------------------------ the loop

@bp.route("/message", methods=["POST"])
def message():
    """A turn of the conversation. The first one is also the task brief: it
    seeds the requirement store, in the same request, so the user never meets a
    setup screen. Extraction stays visible (the requirements appear, editable,
    in the rail) but it no longer blocks the work."""
    try:
        s, data = _session()
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "empty message"}), 400
        s.llm_messages.append({"role": "user", "content": text})
        s.status = "idle"
        s.log("user", text=text, highlights=data.get("highlights") or [])

        if not s.requirements:
            s.brief = s.brief or text
            result = extract_mod.extract(text)
            s.requirements = R.normalize_all(result["requirements"])
            s.questions = result["questions"]
            s.unmapped = result["coverage"]["unmapped"]
            s.log("extracted", ids=[r["id"] for r in s.requirements],
                  questions=s.questions,
                  unmapped=len(result["coverage"]["unmapped"]),
                  coverage=[result["coverage"]["mapped"], result["coverage"]["total"]])
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


# One turn per session at a time. A client whose /step response died on the
# wire retries while the first turn is still running server-side; without the
# lock that starts a second Codex turn against the same workspace.
_TURN_LOCKS = {}
_TURN_LOCKS_GUARD = threading.Lock()


def _turn_lock(session_id):
    with _TURN_LOCKS_GUARD:
        return _TURN_LOCKS.setdefault(session_id, threading.Lock())


@bp.route("/step", methods=["POST"])
def step():
    """One unit of agent work: a single action under the native loop, a whole
    Codex turn under the codex engine."""
    try:
        s, _ = _session()
        lock = _turn_lock(s.id)
        if not lock.acquire(blocking=False):
            # A turn is already in flight (a retry after a dropped connection,
            # or a double click). Report busy; the client polls /state.
            return jsonify({"events": [], "snapshot": s.snapshot(),
                            "canContinue": True, "busy": True})
        try:
            if codex.enabled():
                events = codex.run_turn(s)
                # A Codex turn speaks freely, so an assistant message does not
                # end the run; only the gate bounce (or a steer) asks for
                # another turn.
                can_continue = codex.can_continue(s)
            else:
                events = loop.step(s)
                kinds = {e["type"] for e in events}
                # The auto-runner stops on anything that wants a human: a
                # paused loop, a finished task, an error, or the agent replying
                # instead of acting.
                can_continue = (s.status not in ("done", "paused")
                                and not (kinds & {"assistant", "error"}))
        finally:
            lock.release()
        return jsonify({"events": events, "snapshot": s.snapshot(),
                        "canContinue": can_continue})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/pause", methods=["POST"])
def pause():
    """Stop the running turn NOW. Under codex a step is a whole multi-minute
    turn, so 'stop before the next step' reads as a dead button; this kills
    the turn's process instead. Finished items are already absorbed and
    verified, and Run resumes the same codex thread."""
    try:
        s, _ = _session()
        s._pause_requested = True
        proc = getattr(s, "_codex_proc", None)
        if proc is not None and proc.poll() is None:
            proc.kill()
        elif s.status != "running":
            s._pause_requested = False   # nothing in flight — nothing to stop
        snap = s.snapshot()
        snap["canContinue"] = _can_continue(s)
        return jsonify(snap)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/check", methods=["GET"])
def check():
    """The checker as a URL, for the Codex sandbox to curl. Deterministic
    checks only — the judge runs at end of turn, where the gate needs it.
    Returns plain text: this is the observation the agent reads."""
    from flask import Response
    s = sessions.get(request.args.get("sessionId"))
    if s is None:
        return Response("unknown sessionId", status=404, mimetype="text/plain")
    try:
        before = {r["id"]: (r.get("report") or {}).get("verdict")
                  for r in s.requirements}
        reports = verifier.verify(s, judge_pass=False)
        R.apply_report(s.requirements, reports)
        # Stash the flips so the step event for this curl can wear the chips.
        s._check_chips = [
            {"id": r["id"], "verdict": (r.get("report") or {}).get("verdict"),
             "from": before.get(r["id"]), "weight": r.get("weight", 1)}
            for r in s.requirements
            if r.get("status") == "active"
            and (r.get("report") or {}).get("verdict") != before.get(r["id"])]
        s.save()
        text = verifier.report_text(s.requirements)
        # A judge-verified requirement goes STALE the moment its text changes
        # and this endpoint cannot re-judge it. Without this line the agent
        # treats STALE as an error and retries the check in a loop.
        if any((r.get("report") or {}).get("verdict") == "stale"
               and r.get("verify") == "judge" for r in s.requirements):
            text += ("\n\nSTALE means the text changed after the last "
                     "judgement. It is re-judged automatically when you stop "
                     "— fix FAIL lines, ignore STALE ones, and stop.")
        return Response(text, mimetype="text/plain")
    except Exception as e:                                    # noqa: BLE001
        return Response(f"check failed: {type(e).__name__}: {e}",
                        status=500, mimetype="text/plain")


@bp.route("/file", methods=["POST"])
def write_file():
    """The user edits the document by hand. Their text is written straight to
    the workspace and re-checked immediately — the deterministic checks cost
    nothing, so the rail answers a human edit as fast as an agent one. The
    agent is told, so it re-reads instead of acting on a stale copy."""
    try:
        s, data = _session()
        path = (data.get("path") or "").strip()
        text = data.get("text")
        if not path or text is None:
            return jsonify({"error": "path and text are required"}), 400
        before = s.workspace.read(path)
        if before == text:
            return jsonify(s.snapshot())
        s.workspace.write(path, text)
        s.llm_messages.append({
            "role": "user",
            "content": f"(I edited {path} by hand. Re-read it before you change it.)"})
        before = {r["id"]: (r.get("report") or {}).get("verdict")
                  for r in s.requirements}
        R.apply_report(s.requirements, verifier.verify(s))
        chips = [{"id": r["id"], "verdict": (r.get("report") or {}).get("verdict"),
                  "from": before.get(r["id"])}
                 for r in s.requirements
                 if r.get("status") == "active"
                 and (r.get("report") or {}).get("verdict") != before.get(r["id"])]
        s.log("user-edit", path=path, chars=len(text), chips=chips)
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/steer", methods=["POST"])
def steer():
    """A one-shot instruction injected at the head of the next step."""
    try:
        s, data = _session()
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "empty steer"}), 400
        s.pending_steer = text
        if s.status == "paused":
            s.status = "idle"
        s.log("steer-queued", text=text, requirementId=data.get("requirementId"))
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/gate", methods=["POST"])
def gate():
    try:
        s, data = _session()
        s.gate_on = bool(data.get("on", not s.gate_on))
        s.log("gate-toggle", on=s.gate_on,
              blocking=[r["id"] for r in R.blocking(s.requirements)])
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


# ------------------------------------------------------------------ the rail

@bp.route("/requirement", methods=["POST"])
def requirement():
    """add | update | weight | pause | resume | delete | override — every one
    of them logged, because how people manage the store is the study data."""
    try:
        s, data = _session()
        action = data.get("action")
        rid = data.get("id")
        target = next((r for r in s.requirements if r["id"] == rid), None)

        if action == "add":
            raw = data.get("requirement") or {}
            used = {r["id"] for r in s.requirements}
            n = len(s.requirements) + 1
            while f"R{n}" in used:
                n += 1
            new = R.normalize({**raw, "id": raw.get("id") or f"R{n}",
                               "source": raw.get("source") or {"kind": "user"}})
            s.requirements.append(new)
            s.log("requirement", action="add", requirementId=new["id"], payload=new)
        elif target is None:
            return jsonify({"error": f"unknown requirement {rid}"}), 404
        elif action == "delete":
            s.requirements = [r for r in s.requirements if r["id"] != rid]
            s.log("requirement", action="delete", requirementId=rid)
        elif action == "weight":
            old = target["weight"]
            target["weight"] = max(0, min(3, int(data.get("weight", 1))))
            target["status"] = "paused" if target["weight"] == 0 else "active"
            s.log("requirement", action="weight", requirementId=rid,
                  payload={"from": old, "to": target["weight"]})
        elif action in ("pause", "resume"):
            target["status"] = "paused" if action == "pause" else "active"
            if action == "resume" and target["weight"] == 0:
                target["weight"] = 1
            s.log("requirement", action=action, requirementId=rid)
        elif action == "override":
            verdict = data.get("verdict")
            note = (data.get("note") or "set by you").strip()
            rep = target.setdefault("report", {})
            rep["override"] = None if not verdict else {"verdict": verdict, "note": note}
            if verdict:
                rep["verdict"] = verdict
                rep["detail"] = note
            s.log("requirement", action="override", requirementId=rid,
                  payload={"verdict": verdict, "note": note})
        elif action == "update":
            raw = data.get("requirement") or {}
            merged = R.normalize({**target, **raw, "id": rid})
            # An edited requirement is a different requirement: its old
            # verdict was computed against the old text/type/params, and the
            # verifier deliberately re-judges only when the *document* moves.
            # Clear the verdict and the scope hash so the next pass checks it
            # fresh, keeping only the user's override.
            old_report = target.get("report") or {}
            merged["report"] = {"verdict": "unverified",
                                "detail": "edited — not re-checked yet",
                                "evidence": [], "checkedAtStep": None,
                                "confidence": None, "scopeHash": None,
                                "override": old_report.get("override")}
            s.requirements = [merged if r["id"] == rid else r for r in s.requirements]
            s.log("requirement", action="update", requirementId=rid, payload=raw)
        else:
            return jsonify({"error": f"unknown action {action}"}), 400

        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/recheck", methods=["POST"])
def recheck():
    """Re-verify now. `judge: true` spends one LLM call on the Tier-2 ones."""
    try:
        s, data = _session()
        before = {r["id"]: (r.get("report") or {}).get("verdict")
                  for r in s.requirements}
        reports = verifier.verify(s, judge_pass=bool(data.get("judge")))
        R.apply_report(s.requirements, reports)
        # The chips are how the log answers "what did this do?" — a recheck
        # that changes verdicts silently is invisible work, and invisible work
        # reads as requirements that never show up.
        chips = [{"id": r["id"], "verdict": (r.get("report") or {}).get("verdict"),
                  "from": before.get(r["id"])}
                 for r in s.requirements
                 if r.get("status") == "active"
                 and (r.get("report") or {}).get("verdict") != before.get(r["id"])]
        s.log("recheck", judge=bool(data.get("judge")), chips=chips,
              counts=R.counts(s.requirements))
        s.save()
        return jsonify(s.snapshot())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)


@bp.route("/context", methods=["GET"])
def context():
    s = sessions.get(request.args.get("sessionId"))
    if s is None:
        return jsonify({"error": "unknown sessionId"}), 404
    return jsonify(loop.context_preview(s))


@bp.route("/telemetry", methods=["POST"])
def telemetry():
    """Evidence clicks, jumps, dwell — the rail interactions that make the UI
    an instrument as well as a tool."""
    try:
        s, data = _session()
        s.log("ui", action=data.get("action"), payload=data.get("payload"))
        return jsonify({"ok": True})
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:                                    # noqa: BLE001
        return _fail(e)
