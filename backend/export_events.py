"""Flatten every session under backend/runs/ into two CSVs for analysis.

    python export_events.py [--runs DIR] [--out DIR]

events.csv    one row per event, with the session's condition, participant and
              task, the seconds since the first message (t_rel) and the same
              as a fraction of the task (t_norm, 0 = first message, 1 = hand-in
              or the last event), and a category that names what the row is
              in the vocabulary a timeline wants: agent-step, user-message,
              anchored-edit, direct-edit, highlight, select-requirement,
              evidence-jump, step-expand, scroll, ...
sessions.csv  one row per session: who, what, which condition, when it began
              and ended, how it ended, and a count of every category.

Nothing here reads a model or a workspace; it is the event log and the study
record, read back. A session with no first message (opened and abandoned) is
skipped.
"""

import argparse
import csv
import glob
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))

# The anchored edits arrive twice — as the ui event the toolbar sends and as
# the steer they become — so the steer is recognised by its opening words and
# not counted a second time.
_ANCHORED = ("Replace exactly this passage", "Insert new text immediately after")


def categorize(e):
    """(category, detail) for one event, or None to leave it out."""
    t = e.get("type")
    if t == "step":
        meta = e.get("meta") or {}
        if meta.get("blocked") == "gate":
            return "finish-held", e.get("action")
        if meta.get("blocked") == "tier0":
            return "freeze-enforced", e.get("action")
        return "agent-step", e.get("action")
    if t == "assistant":
        return "agent-message", ""
    if t == "user":
        return "user-message", str(len(e.get("text") or ""))
    if t == "steer-queued":
        text = e.get("text") or ""
        if text.startswith(_ANCHORED):
            return None                       # counted from the ui event
        return "steer", e.get("requirementId") or ""
    if t == "steer":
        return None                           # the queued one is the action
    if t == "user-edit":
        return "direct-edit", e.get("path") or ""
    if t == "ui":
        a = e.get("action")
        p = e.get("payload") or {}
        if a in ("replace", "insert"):
            return "anchored-edit", a
        if a == "freeze":
            return "freeze", p.get("file") or ""
        if a == "edit-file":
            return None                       # duplicate of user-edit
        if a == "evidence-jump":
            return "evidence-jump", p.get("kind") or ""
        if a == "select-req":
            return "select-requirement", f"{p.get('id')}@{p.get('source')}"
        if a == "step-expand":
            if not p.get("open", True):
                return None
            return "step-expand", str(p.get("step"))
        if a == "scroll":
            return "scroll", f"{p.get('pane')}:{p.get('n')}"
        if a == "attachment-open":
            return "attachment-open", p.get("name") or ""
        if a == "task-pick":
            return "task-pick", p.get("id") or ""
        if a == "run-click":
            return "run", str(p.get("step"))
        return "ui-" + str(a), ""
    if t == "requirement":
        a = e.get("action")
        p = e.get("payload") or {}
        if a == "weight":
            return ("highlight" if (p.get("to") or 0) >= 2 else "unhighlight"), e.get("requirementId")
        if a == "add" and ((p.get("source") or {}).get("kind") == "annotation"):
            return None                       # the freeze, already counted
        return "requirement-" + str(a), e.get("requirementId") or ""
    if t == "answer":
        return "clarify-answer", e.get("answer") or ""
    if t == "gate":
        return "finish-held", ",".join(e.get("blocked") or [])
    if t == "notice":
        return "agent-paused", ",".join(e.get("stuck") or [])
    if t in ("pause", "resume", "recheck", "extracted", "commit", "attach",
             "gate-toggle", "trace", "error", "submit"):
        return t, (e.get("reason") or e.get("kind") or "")
    if t == "session":
        return None
    return t, ""


def scroll_n(e):
    p = e.get("payload") or {}
    try:
        return int(p.get("n") or 0)
    except (TypeError, ValueError):
        return 0


def load(runs):
    for path in sorted(glob.glob(os.path.join(runs, "*", "session.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                yield json.load(fh)
        except (OSError, ValueError) as e:
            print(f"skip {path}: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", default=os.path.join(HERE, "runs"))
    ap.add_argument("--out", default=os.path.join(HERE, "analysis"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    ev_rows, sess_rows, cats = [], [], set()
    for d in load(args.runs):
        events = d.get("events") or []
        start = next((e["ts"] for e in events if e.get("type") == "user"), None)
        if start is None:
            continue
        study = d.get("study") or {}
        submit = next((e for e in events if e.get("type") == "submit"), None)
        end = submit["ts"] if submit else (events[-1]["ts"] if events else start)
        span = max(end - start, 1e-9)
        counts = Counter()
        for e in events:
            c = categorize(e)
            if not c:
                continue
            cat, detail = c
            cats.add(cat)
            counts[cat] += scroll_n(e) if cat == "scroll" else 1
            ev_rows.append({
                "session": d.get("sessionId"), "mode": d.get("mode"),
                "participant": study.get("participant") or "",
                "task": study.get("task") or "",
                "i": e.get("i"), "type": e.get("type"), "category": cat,
                "detail": detail, "ts": e.get("ts"),
                "t_rel": round(e["ts"] - start, 3),
                "t_norm": round(min(max((e["ts"] - start) / span, 0.0), 1.0), 4),
                "step": e.get("step") or "",
            })
        final = (submit or {}).get("counts") or {}
        sess_rows.append({
            "session": d.get("sessionId"), "mode": d.get("mode"),
            "participant": study.get("participant") or "",
            "task": study.get("task") or "",
            "created": d.get("created"), "startedAt": start,
            "endedAt": end, "elapsed_s": round(end - start, 1),
            "ended_by": (submit or {}).get("reason") or ("finish" if d.get("status") == "done" else "open"),
            "steps": d.get("stepCount"),
            "requirements": len(d.get("requirements") or []),
            "met_at_end": final.get("satisfied", ""),
            "not_met_at_end": final.get("violated", ""),
            "files": ";".join(sorted((d.get("files") or {}).keys())),
            **{f"n_{c}": counts.get(c, 0) for c in sorted(cats)},
        })

    # every session row carries every category column, in one order
    cols = ["session", "mode", "participant", "task", "created", "startedAt",
            "endedAt", "elapsed_s", "ended_by", "steps", "requirements",
            "met_at_end", "not_met_at_end", "files"] + [f"n_{c}" for c in sorted(cats)]
    with open(os.path.join(args.out, "sessions.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in sess_rows:
            w.writerow({**{c: 0 for c in cols if c.startswith("n_")}, **r})
    with open(os.path.join(args.out, "events.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["session", "mode", "participant", "task", "i",
                                           "type", "category", "detail", "ts", "t_rel",
                                           "t_norm", "step"])
        w.writeheader()
        w.writerows(ev_rows)
    print(f"{len(sess_rows)} sessions, {len(ev_rows)} events -> {args.out}/")


if __name__ == "__main__":
    main()
