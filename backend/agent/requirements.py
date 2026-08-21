"""The requirement model and weighted prompt weaving.

AGENT_UI_DESIGN.md §2 (object shape) and §4 (what weight actually does).

Weight is prompt-level, and this module is the whole of it — there is no hidden
mechanism elsewhere:

    0  paused    not sent, not checked
    1  normal    listed once in the standing block, checked at end of turn
    2  high      standing block + REMINDERS re-injected immediately before
                 every action (recency), checked after every touching step
    3  critical  as 2, plus repeated in the action instruction, plus the loop
                 pauses on violation
"""

import copy
import re

# type -> (kind, default verification mode)
TYPE_ROUTING = {
    "length":          ("artifact", "code"),
    "lexical-ban":     ("artifact", "code"),
    "lexical-require": ("artifact", "code"),
    "preserve":        ("artifact", "code"),
    "structure":       ("artifact", "code"),
    "content":         ("artifact", "judge"),
    "tone":            ("artifact", "judge"),
    "custom":          ("artifact", "judge"),
    "tool-use":        ("process",  "rule"),
    "ordering":        ("process",  "rule"),
    "prohibition":     ("process",  "rule"),
}

VERDICTS = ("satisfied", "violated", "partial", "unverified", "stale")


def normalize(raw, index=0):
    """Coerce anything client- or LLM-shaped into a canonical requirement."""
    r = dict(raw or {})
    rtype = r.get("type") if r.get("type") in TYPE_ROUTING else "custom"
    kind, verify = TYPE_ROUTING[rtype]
    scope = r.get("scope") or {"kind": "global"}
    if not isinstance(scope, dict) or scope.get("kind") not in ("global", "section", "file", "trajectory"):
        scope = {"kind": "global"}
    if kind == "process":
        scope = {"kind": "trajectory"}

    weight = r.get("weight", 1)
    try:
        weight = max(0, min(3, int(weight)))
    except (TypeError, ValueError):
        weight = 1
    status = "paused" if (r.get("status") == "paused" or weight == 0) else "active"
    if status == "paused":
        weight = 0
    elif weight == 0:
        weight = 1

    params = r.get("params") or {}
    verify = r.get("verify") if r.get("verify") in ("code", "judge", "rule", "manual") else verify
    # A structure requirement without a pattern is not code-checkable. Routing
    # it to `code` anyway would park it on "unverified" forever and quietly
    # drop it out of the loop; a judge can at least answer.
    if rtype == "structure" and verify == "code" and not params.get("pattern") \
            and "subject" not in (r.get("text") or "").lower():
        verify = "judge"

    return {
        "id": str(r.get("id") or f"R{index + 1}"),
        "kind": r.get("kind") if r.get("kind") in ("artifact", "process") else kind,
        "type": rtype,
        "text": (r.get("text") or "").strip(),
        "params": params,
        "scope": scope,
        "verify": verify,
        "weight": weight,
        "status": status,
        "enforce": bool(r.get("enforce")),          # Tier-0: hard-block edits
        "source": r.get("source") or {"kind": "user"},
        "assumed": r.get("assumed") or None,        # unanswered clarification
        "report": r.get("report") or {
            "verdict": "unverified", "detail": "", "evidence": [],
            "checkedAtStep": None, "confidence": None, "override": None,
            "scopeHash": None,
        },
    }


def normalize_all(raws):
    return [normalize(r, i) for i, r in enumerate(raws or [])]


def active(reqs):
    return [r for r in reqs if r.get("status") == "active"]


def pinned(reqs):
    """weight >= 2 — the ones that get re-injected before every action."""
    return [r for r in active(reqs) if r.get("weight", 1) >= 2]


def critical(reqs):
    return [r for r in active(reqs) if r.get("weight", 1) >= 3]


def protected_phrases(reqs):
    """Phrases the edit tool must refuse to destroy (Tier-0)."""
    out = []
    for r in active(reqs):
        if r.get("type") == "preserve" and r.get("enforce"):
            out.extend(phrases_of(r))
    return out


_QUOTED = re.compile(r'["“”\'‘’](.+?)["“”\'‘’]')


def phrases_of(r):
    """Phrase list for lexical/preserve requirements: explicit params win,
    else any quoted fragments in the human-readable text."""
    params = r.get("params") or {}
    explicit = [str(p).strip() for p in (params.get("phrases") or []) if str(p).strip()]
    if explicit:
        return explicit
    return [m.strip() for m in _QUOTED.findall(r.get("text", "")) if m.strip()]


def _scope_label(r):
    scope = r.get("scope") or {}
    kind = scope.get("kind")
    if kind == "section":
        return f" | section: {scope.get('name')}"
    if kind == "file":
        return f" | file: {scope.get('name')}"
    if kind == "trajectory":
        return " | process"
    return ""


def _line(r):
    return f"  {r['id']} [{r['type']}{_scope_label(r)}] {r['text']}".rstrip()


def standing_block(task_brief, reqs):
    """The always-present preamble: brief + every active requirement once."""
    lines = []
    if task_brief:
        lines += ["Task brief:", "---", task_brief.strip(), "---", ""]
    act = active(reqs)
    if act:
        lines.append("Standing requirements (they apply to EVERY action you take):")
        for r in sorted(act, key=lambda x: -x.get("weight", 1)):
            lines.append(_line(r))
            if r.get("assumed"):
                lines.append(f"      (assumption on file: {r['assumed']})")
        lines.append("")
        lines.append("A requirement is not satisfied because you say so — it is "
                     "checked. Prefer actions whose result you can verify.")
    return "\n".join(lines)


def reminder_block(reqs):
    """The weight>=2 re-injection. Empty string when nothing is pinned."""
    pins = pinned(reqs)
    if not pins:
        return ""
    lines = ["REMINDERS — check these before every action:"]
    for r in sorted(pins, key=lambda x: -x.get("weight", 1)):
        detail = ""
        rep = r.get("report") or {}
        if rep.get("verdict") in ("violated", "partial", "stale") and rep.get("detail"):
            detail = f"  << currently {rep['verdict']}: {rep['detail']}"
        lines.append(_line(r) + detail)
    crit = critical(reqs)
    if crit:
        lines.append(f"{', '.join(r['id'] for r in crit)} are critical: violating "
                     "one stops the run.")
    return "\n".join(lines)


def blocking(reqs):
    """Requirements that hold the finish gate shut."""
    out = []
    for r in active(reqs):
        rep = r.get("report") or {}
        if (rep.get("override") or {}).get("verdict") == "satisfied":
            continue
        if rep.get("verdict") in ("violated", "partial", "stale"):
            out.append(r)
    return out


def apply_report(reqs, reports):
    """Merge verifier output into the store, preserving user overrides."""
    by_id = {rep["id"]: rep for rep in reports}
    for r in reqs:
        rep = by_id.get(r["id"])
        if not rep:
            continue
        old = r.get("report") or {}
        merged = copy.deepcopy(rep)
        merged["override"] = old.get("override")
        if merged["override"] and merged["override"].get("verdict"):
            merged["verdict"] = merged["override"]["verdict"]
            merged["detail"] = (merged["override"].get("note")
                                or "set by you") + f" (auto: {rep['verdict']})"
        r["report"] = merged
    return reqs


def counts(reqs):
    out = {v: 0 for v in VERDICTS}
    out["paused"] = 0
    for r in reqs:
        if r.get("status") == "paused":
            out["paused"] += 1
            continue
        verdict = (r.get("report") or {}).get("verdict") or "unverified"
        out[verdict] = out.get(verdict, 0) + 1
    return out
