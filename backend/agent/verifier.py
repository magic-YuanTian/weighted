"""Verification: code / rule / judge, with typed evidence and staleness.

The contract from AGENT_UI_DESIGN.md §0 and §5:

* a verdict always carries the evidence it was computed from — an artifact span
  or a trajectory step, never a bare claim;
* code beats judgement wherever code can decide (Tier-1 lives in the v3
  ``checker.py``, reused verbatim);
* a verdict computed before a later edit to its scope is **stale**, not
  satisfied. Agents keep editing; a green check from nine steps ago is a lie
  with a checkmark on it.
"""

import hashlib
import re

import checker  # v3 Tier-1 checks, reused as-is
from . import llm
from . import requirements as R

_slug = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())

_STOP = {"the", "a", "an", "of", "for", "md", "txt", "final", "draft"}


def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in _STOP}


def _kin(a, b):
    """True when two names plainly denote the same deliverable — 'Recruiter
    Outreach Email' and recruiter_email.md. Agents do not copy scope names
    verbatim into filenames, and refusing to check a file over a missing word
    is worse than checking the obviously intended one."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


# ---------------------------------------------------------------- document

def build_document(workspace):
    """Concatenate the workspace into one plain text, remembering where each
    file landed so artifact evidence can be reported per file."""
    text_parts, ranges, pos = [], [], 0
    for name in workspace.list():
        body = workspace.read(name) or ""
        if not body.endswith("\n"):
            body += "\n"
        ranges.append({"file": name, "start": pos, "end": pos + len(body)})
        text_parts.append(body)
        pos += len(body) + 1
        text_parts.append("\n")
    text = "".join(text_parts)
    return {"text": text, "files": ranges, "sections": checker.build_sections(text)}


def locate(doc, start, end):
    """Combined-text offsets -> {file, start, end} in that file's own space."""
    for f in doc["files"]:
        if f["start"] <= start < f["end"]:
            return {"file": f["file"], "start": start - f["start"], "end": end - f["start"]}
    return {"file": None, "start": start, "end": end}


def resolve_scope(req, doc):
    """(start, end) of a requirement's scope in the combined text, or None when
    the scope does not exist yet (a section the agent has not written)."""
    scope = req.get("scope") or {}
    kind, name = scope.get("kind", "global"), scope.get("name")
    if kind == "global" or not name:
        return 0, len(doc["text"])
    if kind == "file":
        for f in doc["files"]:
            if _slug(f["file"]) == _slug(name) or _kin(name, f["file"]):
                return f["start"], f["end"]
        return None
    if kind == "section":
        # A deliverable usually *is* a file here, and a filename is the
        # unambiguous signal: two files can both contain a "Cover Letter"
        # heading, and measuring the wrong one is worse than not measuring.
        # Exact matches win; near-misses only when nothing exact exists.
        stem = lambda f: f.rsplit(".", 1)[0]
        for f in doc["files"]:
            if _slug(stem(f["file"])) == _slug(name):
                return f["start"], f["end"]
        for sec in doc["sections"]:
            if _slug(sec["name"]) == _slug(name):
                return sec["start"], sec["end"]
        near = [f for f in doc["files"] if _kin(name, stem(f["file"]))]
        if len(near) == 1:
            return near[0]["start"], near[0]["end"]
        near_sec = [s for s in doc["sections"] if _kin(name, s["name"])]
        if len(near_sec) == 1:
            return near_sec[0]["start"], near_sec[0]["end"]
        return None
    return 0, len(doc["text"])


def _hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- Tier-1

def _artifact_evidence(doc, locations, limit=6):
    out = []
    for loc in (locations or [])[:limit]:
        placed = locate(doc, loc["start"], loc["end"])
        out.append({
            "kind": "artifact",
            "file": placed["file"],
            "start": placed["start"],
            "end": placed["end"],
            "quote": (loc.get("text") or "")[:120],
        })
    return out


def _check_code(req, doc, scope_range):
    """Dispatch to the v3 checker. Adds `partial`, which v3 has no need for:
    a multi-phrase preserve requirement can be three-quarters kept, and
    collapsing that to 'violated' throws away the information the user needs."""
    start, end = scope_range
    scope_text = doc["text"][start:end]
    rtype = req["type"]
    shim = dict(req)
    if rtype == "preserve":
        shim["type"] = "lexical-require"

    if shim["type"] == "length":
        verdict, detail, locs = checker._check_length(shim, scope_text)
    elif shim["type"] == "lexical-ban":
        verdict, detail, locs = checker._check_lexical_ban(shim, scope_text, start)
    elif shim["type"] == "lexical-require":
        verdict, detail, locs = checker._check_lexical_require(shim, scope_text, start)
    elif shim["type"] == "structure":
        verdict, detail, locs = checker._check_structure(shim, scope_text, start)
    else:
        return "unverified", "no deterministic check for this type", []

    if verdict == "unchecked":      # v3's word for it; v4 distinguishes stale
        verdict = "unverified"

    if shim["type"] == "lexical-require" and verdict == "violated":
        phrases = R.phrases_of(req)
        kept = len(locs)
        if phrases and 0 < kept < len(phrases):
            verdict = "partial"
            detail = f"{kept} of {len(phrases)} kept — {detail}"
    return verdict, detail, _artifact_evidence(doc, locs)


# ---------------------------------------------------------------- Tier-3 (process)

_TOOL_WORDS = {
    "run_check": ("word count", "word-count", "checker", "check", "verify", "count"),
    "read_file": ("read", "re-read", "reread", "review the file"),
    "list_files": ("list files",),
    "edit_file": ("edit",),
}


def _infer_tool(text):
    low = (text or "").lower()
    for tool, words in _TOOL_WORDS.items():
        if any(w in low for w in words):
            return tool
    return "run_check"


def _step_evidence(step, note=""):
    return {
        "kind": "step",
        "stepId": step["step"],
        "action": step.get("action"),
        "quote": (note or step.get("argSummary") or "")[:120],
    }


def _check_rule(req, session):
    """Process requirements: the trajectory is the only possible evidence."""
    params = req.get("params") or {}
    steps = [e for e in session.events if e.get("type") == "step"]

    if req["type"] == "prohibition":
        if req.get("enforce"):
            blocked = [s for s in steps if (s.get("meta") or {}).get("blocked") == "tier0"]
            ev = [_step_evidence(s, "edit rejected by the workspace") for s in blocked[-3:]]
            return ("satisfied",
                    "enforced in the edit tool" + (f" ({len(blocked)} edit(s) rejected)" if blocked else ""),
                    ev)
        return "unverified", "not enforced — steering only (turn on Enforce for Tier-0)", []

    tool = params.get("tool") or _infer_tool(req["text"])
    before = params.get("before", "finish")

    hits = [s for s in steps if s.get("action") == tool and (s.get("meta") or {}).get("ok")]
    edits = [s for s in steps if (s.get("meta") or {}).get("kind") == "edit"
             and (s.get("meta") or {}).get("ok")]
    finish_tries = [s for s in steps if s.get("action") == "finish"]

    if not hits:
        if before == "finish" and finish_tries:
            return ("violated", f"tried to finish without calling {tool}",
                    [_step_evidence(finish_tries[-1], "finish attempted")])
        return "unverified", f"{tool} not called yet", []

    last_hit, last_edit = hits[-1], (edits[-1] if edits else None)
    if last_edit and last_edit["step"] > last_hit["step"]:
        return ("stale",
                f"{tool} ran at #{last_hit['step']}, then #{last_edit['step']} edited the workspace",
                [_step_evidence(last_hit, f"{tool} ran here"),
                 _step_evidence(last_edit, "invalidated by this edit")])
    return "satisfied", f"{tool} ran at #{last_hit['step']}", [_step_evidence(last_hit)]


# ---------------------------------------------------------------- Tier-2 (judge)

JUDGE_SYSTEM = ("You are a strict, skeptical reviewer. You judge only what the text "
                "shows. When in doubt you answer \"violated\" and say what is missing. "
                "Never praise, never soften.")


def judge(reqs, doc, task_brief=""):
    """One batched call for every judge-verified requirement. Returns
    {id: (verdict, detail, quote, confidence)}."""
    targets = [r for r in reqs if r.get("verify") == "judge" and r.get("status") == "active"]
    if not targets or not doc["text"].strip():
        return {}

    lines = []
    if task_brief:
        lines += [f"Task brief: {task_brief}", ""]
    lines += ["Document under review:", "---", doc["text"][:12000], "---", "",
              "Judge each requirement:"]
    for r in targets:
        scope = r.get("scope") or {}
        tag = f" (only the \"{scope.get('name')}\" part)" if scope.get("name") else ""
        lines.append(f"  {r['id']}: [{r['type']}] {r['text']}{tag}")
    lines += ["",
              'Return ONLY JSON: {"R1": {"verdict": "satisfied"|"violated", '
              '"detail": "<one short sentence>", "quote": "<exact phrase from the '
              'document that shows it, or empty>", "confidence": 0.0-1.0}, ...}']

    data = llm.chat_json(
        [{"role": "system", "content": JUDGE_SYSTEM},
         {"role": "user", "content": "\n".join(lines)}],
        temperature=0)

    out = {}
    for r in targets:
        item = data.get(r["id"]) if isinstance(data, dict) else None
        if not isinstance(item, dict):
            continue
        verdict = item.get("verdict")
        if verdict not in ("satisfied", "violated"):
            continue
        try:
            conf = min(1.0, max(0.0, float(item.get("confidence", 0.6))))
        except (TypeError, ValueError):
            conf = 0.6
        out[r["id"]] = (verdict, (item.get("detail") or "").strip(),
                        (item.get("quote") or "").strip(), conf)
    return out


def _quote_evidence(doc, quote):
    if not quote:
        return []
    idx = doc["text"].find(quote)
    if idx < 0:
        idx = doc["text"].lower().find(quote.lower())
    if idx < 0:
        return []
    return _artifact_evidence(doc, [{"start": idx, "end": idx + len(quote), "text": quote}])


# ---------------------------------------------------------------- entry point

def verify(session, judge_pass=False, **kw):
    """Verify every active requirement. `judge_pass=True` also spends one LLM
    call on the Tier-2 requirements — off by default so a step is never blocked
    on a judgement."""
    judge_pass = kw.get("judge", judge_pass)
    doc = build_document(session.workspace)
    step_no = session.step_count
    reports = []

    if judge_pass:
        try:
            judged = judge(session.requirements, doc, session.brief)
        except Exception as e:                                # noqa: BLE001
            print(f"[verifier] judge pass failed, keeping prior verdicts: {e}")
            judged = {}
    else:
        judged = {}
    edits = [e for e in session.events
             if e.get("type") == "step" and (e.get("meta") or {}).get("kind") == "edit"
             and (e.get("meta") or {}).get("ok")]

    for req in session.requirements:
        prev = req.get("report") or {}
        if req.get("status") == "paused":
            reports.append({**prev, "id": req["id"], "verdict": "unverified",
                            "detail": "paused — out of the prompt and out of checking",
                            "evidence": [], "checkedAtStep": prev.get("checkedAtStep")})
            continue

        if req.get("kind") == "process" or req.get("verify") == "rule":
            verdict, detail, evidence = _check_rule(req, session)
            reports.append({"id": req["id"], "verdict": verdict, "detail": detail,
                            "evidence": evidence, "checkedAtStep": step_no,
                            "confidence": None, "scopeHash": None})
            continue

        rng = resolve_scope(req, doc)
        if rng is None:
            name = (req.get("scope") or {}).get("name")
            reports.append({"id": req["id"], "verdict": "unverified",
                            "detail": f'scope "{name}" does not exist in the workspace yet',
                            "evidence": [], "checkedAtStep": step_no,
                            "confidence": None, "scopeHash": None})
            continue

        scope_hash = _hash(doc["text"][rng[0]:rng[1]])
        confidence = None

        if req.get("verify") == "code":
            verdict, detail, evidence = _check_code(req, doc, rng)
        elif req["id"] in judged:
            verdict, detail, quote, confidence = judged[req["id"]]
            evidence = _quote_evidence(doc, quote)
        else:
            # Not judged this pass: keep the old verdict, but only while the
            # text it was computed from has not moved underneath it.
            verdict = prev.get("verdict", "unverified")
            detail, evidence = prev.get("detail", ""), prev.get("evidence", [])
            confidence = prev.get("confidence")
            if verdict in ("satisfied", "violated", "partial"):
                if prev.get("scopeHash") and prev["scopeHash"] != scope_hash:
                    verdict = "stale"
                    at = prev.get("checkedAtStep")
                    detail = (f"judged at #{at} — the text has changed since"
                              if at is not None else "the text has changed since the last check")
                elif not prev.get("scopeHash"):
                    verdict = "unverified"
                    detail = "judge has not run on this scope"
            checked_at = prev.get("checkedAtStep")
            report = {"id": req["id"], "verdict": verdict, "detail": detail,
                      "evidence": evidence, "checkedAtStep": checked_at,
                      "confidence": confidence, "scopeHash": prev.get("scopeHash")}
            reports.append(report)
            continue

        # Say which text was measured. "412 words" is not a finding until the
        # user knows which file it counted.
        where = locate(doc, rng[0], rng[1])["file"]
        if where and verdict != "unverified" and detail:
            detail = f"[{where}] {detail}"

        # A length verdict has no phrase to point at. Give it the scope itself
        # so "click the verdict" still lands somewhere in the document.
        scope_kind = (req.get("scope") or {}).get("kind")
        if (scope_kind in ("file", "section") and verdict != "unverified"
                and not any(e.get("kind") == "artifact" for e in evidence)):
            placed = locate(doc, rng[0], rng[1])
            if placed["file"]:
                evidence = [{"kind": "artifact", "file": placed["file"],
                             "start": placed["start"],
                             "end": placed["start"] + (rng[1] - rng[0]),
                             "quote": "", "scope": True}] + list(evidence)

        # Which step last touched this scope? That is the process half of an
        # artifact requirement's evidence, and it is what the rail links to.
        touching = [s for s in edits
                    if _slug((s.get("meta") or {}).get("path") or "") in
                    {_slug(f["file"]) for f in doc["files"]
                     if not (f["end"] <= rng[0] or f["start"] >= rng[1])}]
        if touching:
            evidence = list(evidence) + [_step_evidence(touching[-1], "last edit to this scope")]

        reports.append({"id": req["id"], "verdict": verdict, "detail": detail,
                        "evidence": evidence, "checkedAtStep": step_no,
                        "confidence": confidence, "scopeHash": scope_hash})
    return reports


ICON = {"satisfied": "ok", "violated": "FAIL", "partial": "PARTIAL",
        "stale": "STALE", "unverified": "--"}


def report_text(reqs):
    """The observation the agent reads back from run_check."""
    lines = []
    for r in reqs:
        if r.get("status") != "active":
            continue
        rep = r.get("report") or {}
        lines.append(f"{r['id']:<4} {ICON.get(rep.get('verdict'), '--'):<8} "
                     f"{r['text'][:60]:<62} {rep.get('detail', '')}")
    blocked = R.blocking(reqs)
    lines.append("")
    lines.append(f"{len(blocked)} requirement(s) would block finish: "
                 f"{', '.join(r['id'] for r in blocked) or 'none'}")
    return "\n".join(lines)
