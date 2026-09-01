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
import code_checker  # Tier-1 for Python deliverables
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


# ---------------------------------------------------------------- code defs

_CODE_EXT = re.compile(
    r"\.(py|pyi|js|jsx|mjs|cjs|ts|tsx|java|kt|scala|go|rs|rb|php|swift|"
    r"c|h|cc|cpp|hpp|cs|sh|sql|r|m)$", re.I)

# The AST checker parses Python and only Python. A brief in another language
# keeps its constraints on the judge, which is worse but honest.
_PY_EXT = re.compile(r"\.pyi?$", re.I)

# A named definition, in the shape the languages this agent writes use.
_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?:(?:export|default|public|private|protected|internal|static|final|"
    r"abstract|async|open|override|suspend)\s+)*"
    r"(?:def|class|function|interface|struct|enum|trait|impl|fn|func|type|sub)\s+"
    r"(?P<name>[A-Za-z_]\w*)")

# const foo = () => …, let bar = function …, var baz = async (…) => …
_ASSIGN_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*"
    r"(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_]\w*\s*=>)")

_CLOSERS = {"}", "};", "}),", "});", ")", ");", "]", "end", "END"}


def _indent(line):
    return len(line[:len(line) - len(line.lstrip())].expandtabs(4))


def _code_defs(text, files):
    """Named definitions in code files, as (name, start, end) spans.

    Without this, ``resolve_scope`` had two handles on a named thing — a
    filename and a heading — and a function is neither. Every requirement
    scoped to a function ("the function max_frequency_component returns a
    float") resolved to None and sat at *unverified* for the whole run, even
    though the function was sitting in the file the entire time.

    The end of a span is found by indentation, which is exactly right for
    Python and close enough for brace languages: the closing brace sits at the
    definition's own indent, so it ends the span and is pulled back in.
    """
    out = []
    for f in files:
        if not _CODE_EXT.search(f["file"]):
            continue
        lines = text[f["start"]:f["end"]].split("\n")
        starts, pos = [], 0
        for line in lines:
            starts.append(pos)
            pos += len(line) + 1
        for i, line in enumerate(lines):
            m = _DEF_RE.match(line) or _ASSIGN_DEF_RE.match(line)
            if not m:
                continue
            indent, last = _indent(m.group("indent") + "x"), len(lines) - 1
            for j in range(i + 1, len(lines)):
                if not lines[j].strip():
                    continue
                if _indent(lines[j]) <= indent:
                    last = j if lines[j].strip() in _CLOSERS else j - 1
                    break
            out.append({"name": m.group("name"),
                        "start": f["start"] + starts[i],
                        "end": min(f["start"] + starts[last] + len(lines[last]), f["end"])})
    return out


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
    return {"text": text, "files": ranges,
            "sections": checker.build_sections(text),
            "defs": _code_defs(text, ranges)}


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
        # A function is neither a file nor a heading, so it gets its own
        # exact pass before the fuzzy ones — a definition of precisely this
        # name is as unambiguous a signal as a filename.
        for d in doc.get("defs") or []:
            if _slug(d["name"]) == _slug(name):
                return d["start"], d["end"]
        near = [f for f in doc["files"] if _kin(name, stem(f["file"]))]
        if len(near) == 1:
            return near[0]["start"], near[0]["end"]
        near_sec = [s for s in doc["sections"] if _kin(name, s["name"])]
        if len(near_sec) == 1:
            return near_sec[0]["start"], near_sec[0]["end"]
        return None
    return 0, len(doc["text"])


_FILENAME = re.compile(
    r"\b([\w-]+\.(?:md|markdown|txt|py|csv|tsv|json|ya?ml|html?|jsx?|tsx?|sh|sql))\b",
    re.I)
_DELIVERS = re.compile(
    r"\b(?:writ|sav|deliver|output|produc|creat|nam|call|provid|submit|export|put|"
    r"stor|plac)\w*\b", re.I)


def _delivery(req, doc):
    """(verdict, detail, filename) for "put the deliverable in a file called X",
    or None when the requirement is not that.

    Whether a file exists under a given name is a fact about the workspace, and
    nothing in a document's prose can settle it. Sent to the judge it produced
    the worst kind of false negative -- report.md sitting in the workspace,
    judged "the text does not demonstrate that the report was written to a file
    named report.md". Naming the files in the judge's view stopped that in
    practice, but the routing is still wrong: this belongs to code.

    Deliberately narrow. One filename, a delivery verb, and a short sentence --
    a longer one is making a claim about the contents too, and that part is
    still the judge's.
    """
    text = (req.get("text") or "").strip()
    names = set(n.lower() for n in _FILENAME.findall(text))
    if len(names) != 1 or not _DELIVERS.search(text) or len(text.split()) > 14:
        return None
    want = names.pop()
    have = [f["file"] for f in doc["files"]]
    for f in have:
        if f.lower() == want:
            return "satisfied", f"{f} is in the workspace", f
    return ("violated",
            f"no file named {want} in the workspace"
            + (f" — there is {', '.join(have)}" if have else ", which is empty"),
            None)


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


def _reanchor(doc, evidence, rng):
    """Carried-forward evidence, re-pointed at the text it quotes.

    A scope hash proves the scope's CONTENT has not changed. It cannot prove
    the scope has not MOVED, and an artifact span is an absolute offset into
    a file. Edit a line above a function and the function still hashes the
    same, so the verdict on it is still true and is rightly kept — while every
    span inside it now points a few characters to the left, and the rail draws
    its underline through the middle of the previous word.

    Across the recorded runs that was 40 of 1,220 artifact spans. Every one of
    them sat on a judge verdict — code verdicts rebuild their spans from the
    parse on every pass, so they cannot drift — and within a session they were
    all displaced by the same amount: -4 here, -8 there, +16, +1720.

    The quote is the durable half of a span, so the span is rebuilt from it,
    keeping its original length; the occurrence nearest the old offset wins,
    because a phrase that appears twice must not jump to the other one just
    because a line above it was rewritten. A quote that is gone loses its span
    instead of keeping a wrong one — no underline is a smaller lie than an
    underline over the wrong words.
    """
    out = []
    for ev in evidence or []:
        if ev.get("kind") != "artifact":
            out.append(ev)
            continue
        if ev.get("scope"):
            # The whole-scope band is the scope; re-derive it from where the
            # scope is now rather than from where it was.
            placed = locate(doc, rng[0], rng[1])
            if placed["file"]:
                out.append({**ev, "file": placed["file"],
                            "start": placed["start"],
                            "end": placed["start"] + (rng[1] - rng[0])})
            continue
        quote = ev.get("quote") or ""
        base = next((f for f in doc["files"] if f["file"] == ev.get("file")), None)
        if not quote or base is None:
            continue
        body = doc["text"][base["start"]:base["end"]]
        hits, idx = [], body.find(quote)
        while idx >= 0:
            hits.append(idx)
            idx = body.find(quote, idx + 1)
        if not hits:
            continue
        was = ev.get("start") or 0
        best = min(hits, key=lambda i: abs(i - was))
        span = max(ev.get("end", 0) - was, len(quote))
        out.append({**ev, "start": best, "end": min(best + span, len(body))})
    return out


def _check_code_prop(req, doc, scope_range):
    """Dispatch to the AST checker, per Python file the scope covers.

    Per file, not over the scope text: a global scope is every workspace file
    concatenated, and two Python files run together do not parse. Each file is
    checked on its own and the verdicts are combined — any violation carries.
    """
    start, end = scope_range
    params = req.get("params") or {}
    targets = [f for f in doc["files"]
               if f["start"] < end and f["end"] > start
               and _PY_EXT.search(f["file"])]
    if not targets:
        return "unverified", "no Python file in scope yet", []

    verdicts, details, locs = [], [], []
    for f in targets:
        source = doc["text"][f["start"]:f["end"]]
        verdict, detail, spans = code_checker.check(
            params.get("prop"), params, source)
        verdicts.append(verdict)
        if detail:
            # Named here, not by verify()'s generic prefix: that one labels a
            # verdict with whichever file the *scope* starts in, which for a
            # global scope over several files is simply the first one — the
            # wrong name on a violation found in the third.
            details.append(f"[{f['file']}] {detail}")
        locs.extend({"start": f["start"] + a, "end": f["start"] + b,
                     "text": doc["text"][f["start"] + a:f["start"] + b]}
                    for a, b in spans)

    if "violated" in verdicts:
        verdict = "violated"
    elif all(v == "unverified" for v in verdicts):
        verdict = "unverified"
    else:
        verdict = "satisfied"
    # A violation names every file that caused it; a pass over a dozen files
    # does not need a dozen sentences saying so.
    if verdict != "violated":
        shown, rest = details[:2], len(details) - 2
        details = shown + ([f"and {rest} more file(s)"] if rest > 0 else [])
    return verdict, "; ".join(details), _artifact_evidence(doc, locs)


def _check_code(req, doc, scope_range):
    """Dispatch to the v3 checker. Adds `partial`, which v3 has no need for:
    a multi-phrase preserve requirement can be three-quarters kept, and
    collapsing that to 'violated' throws away the information the user needs."""
    start, end = scope_range
    scope_text = doc["text"][start:end]
    rtype = req["type"]
    if rtype == "code-prop":
        return _check_code_prop(req, doc, scope_range)
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
                "One exception, and only this one: a rule about how to handle "
                "something the document never does — capitalise brand names where "
                "no brand name appears, keep tables consistent where there are no "
                "tables — is \"satisfied\". There is nothing there to get wrong, and "
                "the absence of the occasion is not the absence of compliance. "
                "Never praise, never soften.")


def _judge_view(doc):
    """The document as the judge has to see it: each body under its filename.

    build_document() concatenates bodies only — the names are kept in
    doc["files"] for evidence mapping and never reach the text. A judge asked
    whether the deliverable is "a file named report.md" therefore has nothing
    to go on, and under the skeptical system prompt answers "violated" for a
    file that is sitting right there. The offsets in doc["text"] are load
    bearing (scope resolution, scope hashes, quote lookup), so this is a
    separate rendering used for the prompt alone.
    """
    if not doc["files"]:
        return doc["text"]
    parts = []
    for f in doc["files"]:
        body = doc["text"][f["start"]:f["end"]]
        words = len(re.findall(r"\S+", body))
        parts.append(f"=== file: {f['file']} ({words} words) ===\n{body}")
    return "\n".join(parts)


def _reference_view(session):
    """The attachments, rendered for the judge. They are invisible to every
    code check on purpose — they are source material, not the deliverable —
    but a requirement that *relates* the deliverable to its source (keep
    every row, repair value X) is unjudgeable without the source in view. In
    the first wrangling pilot the judge said exactly that, five times over:
    "the original table is not provided, so retention cannot be verified",
    and held the finish gate shut on work that was correct."""
    att = getattr(session, "attachments", None)
    if att is None:
        return ""
    parts = []
    for name in att.list():
        parts.append(f"=== attachment: {name} ===\n{att.read(name) or ''}")
    return "\n".join(parts)


def judge(reqs, doc, task_brief="", reference=""):
    """One batched call for every judge-verified requirement. Returns
    {id: (verdict, detail, quote, confidence)}."""
    targets = [r for r in reqs if r.get("verify") == "judge" and r.get("status") == "active"]
    if not targets or not doc["text"].strip():
        return {}

    # No length cap. A capped view once cut every T2R report past ~1,900
    # words mid-sentence, and the judge honestly ruled "ends abruptly"
    # against an ending it was never shown. The judge must see exactly what
    # the user sees, whole.
    lines = []
    if task_brief:
        lines += [f"Task brief: {task_brief}", ""]
    if reference:
        lines += ["The user attached reference material. It is not part of the",
                  "deliverable and is not itself under review — use it as the",
                  "source of truth when a requirement relates the document to",
                  "it: rows or columns to keep, values to repair, data to stay",
                  "faithful to.",
                  "---", reference, "---", ""]
    lines += ["Document under review. Each file appears under a '=== file: NAME ==='",
              "header; the headers are metadata, not part of the text.",
              "---", _judge_view(doc), "---", "",
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
        # A judge verdict is recomputed only when the text under it has
        # changed. Judging identical bytes twice lets the model flip a
        # borderline call: in the pilot the agent gave up on "max_freq is a
        # constant" under a violated verdict, and the end-of-run recheck
        # flipped it to satisfied over the same text — the chat then said
        # "cannot be satisfied" beside a green R12. Unverified, stale and
        # edited scopes are still judged; settled ones keep their verdict.
        fresh = []
        for r in session.requirements:
            if r.get("verify") != "judge" or r.get("status") != "active":
                continue
            prev_rep = r.get("report") or {}
            rng = resolve_scope(r, doc)
            if (rng is not None and prev_rep.get("scopeHash")
                    and prev_rep.get("verdict") in ("satisfied", "violated")
                    and prev_rep["scopeHash"] == _hash(doc["text"][rng[0]:rng[1]])):
                continue
            fresh.append(r)
        try:
            judged = judge(fresh, doc, session.brief,
                           reference=_reference_view(session))
        except Exception as e:                                # noqa: BLE001
            # One retry: a dropped connection right after a long turn is the
            # common case, and a silently-stale rail blocks the gate on
            # everything at once — which reads as 33 sudden failures.
            print(f"[verifier] judge pass failed, retrying once: {e}")
            try:
                judged = judge(fresh, doc, session.brief,
                               reference=_reference_view(session))
            except Exception as e2:                           # noqa: BLE001
                print(f"[verifier] judge pass failed twice, keeping prior "
                      f"verdicts: {e2}")
                session.log("notice", text="The reviewer could not check the "
                            "text just now (connection problem). Earlier "
                            "verdicts are kept — the next run re-checks them.")
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

        deliver = _delivery(req, doc)
        if deliver:
            verdict, detail, fname = deliver
            ev = []
            for f in doc["files"]:
                if f["file"] == fname:
                    ev = [{"kind": "artifact", "file": f["file"], "start": 0,
                           "end": min(120, f["end"] - f["start"]),
                           "quote": doc["text"][f["start"]:f["start"] + 120]}]
            reports.append({"id": req["id"], "verdict": verdict, "detail": detail,
                            "evidence": ev, "checkedAtStep": step_no,
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
            detail = prev.get("detail", "")
            evidence = _reanchor(doc, prev.get("evidence", []), rng)
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
        if where and verdict != "unverified" and detail \
                and req.get("type") != "code-prop":   # names its own files
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
