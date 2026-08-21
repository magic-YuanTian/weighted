"""Deterministic (Tier-1) constraint checking over assembled document text.

Pure functions, no LLM, no Flask. The design contract (CONSTRAINT_DESIGN.md §3.3):
never trust the LLM to check what code can check. Length, banned/required
phrases and structural patterns are verified here after every deterministic
reassembly; tone/content/custom constraints are prompt-steered only and come
back as "unchecked" (the optional Tier-2 judge handles them on demand).
"""

import re

# A heading is a short line (≤ 8 words, ≤ 60 chars) that starts with an
# uppercase letter and does not end like a sentence. Markdown prefixes and
# bold markers are tolerated since drafts often carry them.
_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|\*\*)?"
    r"(?P<name>[A-Z][A-Za-z0-9&/+'\- ]{0,58}?)"
    r"(?:\*\*)?\s*:?\s*$"
)
_SENTENCE_END = (".", "!", "?", ",", ";")


def build_sections(plain_text):
    """Split *plain_text* into named sections at heading-like lines.

    Returns [{"name", "start", "end"}] where start/end delimit the section
    BODY (heading line excluded, so word counts don't include the title).
    Text before the first heading belongs to no section. May be empty.
    """
    sections = []
    offset = 0
    for line in plain_text.split("\n"):
        line_end = offset + len(line)
        stripped = line.strip()
        m = _HEADING_RE.match(line) if stripped else None
        is_heading = (
            m is not None
            and not stripped.endswith(_SENTENCE_END)
            and len(m.group("name").split()) <= 8
        )
        if is_heading:
            if sections:
                sections[-1]["end"] = offset
            sections.append({
                "name": m.group("name").strip(),
                "start": min(line_end + 1, len(plain_text)),
                "end": len(plain_text),
            })
        offset = line_end + 1  # +1 for the split newline
    return sections


def resolve_scope(constraint, plain_text, sections):
    """Return (start, end) char range for a constraint's scope, or None if a
    named section does not exist in the current document."""
    scope = constraint.get("scope") or {}
    if scope.get("kind") == "section" and scope.get("name"):
        wanted = scope["name"].strip().lower()
        for sec in sections:
            if sec["name"].strip().lower() == wanted:
                return sec["start"], sec["end"]
        return None
    return 0, len(plain_text)


_QUOTED_RE = re.compile(r'["“‘\'](.+?)["”’\']')


def constraint_phrases(constraint):
    """Phrase list for lexical constraints: explicit params win, otherwise any
    quoted fragments inside the human-readable text."""
    params = constraint.get("params") or {}
    phrases = [p for p in (params.get("phrases") or []) if str(p).strip()]
    if phrases:
        return [str(p).strip() for p in phrases]
    return [m.strip() for m in _QUOTED_RE.findall(constraint.get("text", "")) if m.strip()]


def _find_all(haystack, needle, base_offset):
    """Case-insensitive occurrences of *needle*, as absolute char ranges."""
    locations = []
    low_hay, low_needle = haystack.lower(), needle.lower()
    start = 0
    while True:
        idx = low_hay.find(low_needle, start)
        if idx < 0:
            break
        locations.append({
            "start": base_offset + idx,
            "end": base_offset + idx + len(needle),
            "text": haystack[idx:idx + len(needle)],
        })
        start = idx + 1
    return locations


def _check_length(constraint, scope_text):
    params = constraint.get("params") or {}
    unit = params.get("unit", "words")
    count = len(re.findall(r"\S+", scope_text)) if unit == "words" else len(scope_text)
    lo, hi = params.get("min"), params.get("max")
    target = (
        f"{lo}–{hi}" if lo is not None and hi is not None
        else (f"≥ {lo}" if lo is not None else (f"≤ {hi}" if hi is not None else "?"))
    )
    ok = (lo is None or count >= lo) and (hi is None or count <= hi)
    return ("satisfied" if ok else "violated",
            f"{count} {unit} (target {target})", [])


def _check_lexical_ban(constraint, scope_text, base):
    phrases = constraint_phrases(constraint)
    if not phrases:
        return "unchecked", "no phrases specified", []
    locations = []
    hits = []
    for p in phrases:
        found = _find_all(scope_text, p, base)
        if found:
            hits.append(f'"{p}"')
            locations.extend(found)
    if locations:
        return "violated", f"found {', '.join(hits)}", locations
    return "satisfied", "none of the banned phrases appear", []


def _check_lexical_require(constraint, scope_text, base):
    phrases = constraint_phrases(constraint)
    if not phrases:
        return "unchecked", "no phrases specified", []
    missing = []
    locations = []
    for p in phrases:
        found = _find_all(scope_text, p, base)
        if found:
            locations.append(found[0])
        else:
            missing.append(f'"{p}"')
    if missing:
        return "violated", f"missing {', '.join(missing)}", locations
    return "satisfied", "all required phrases present", locations


def _check_structure(constraint, scope_text, base):
    params = constraint.get("params") or {}
    pattern = params.get("pattern")
    if not pattern and "subject" in constraint.get("text", "").lower():
        pattern = r"(?mi)^\s*subject\s*:"
    if not pattern:
        return "unchecked", "no pattern specified", []
    try:
        m = re.search(pattern, scope_text)
    except re.error as e:
        return "unchecked", f"bad pattern: {e}", []
    if m:
        return ("satisfied", "pattern present",
                [{"start": base + m.start(), "end": base + m.end(),
                  "text": m.group(0)}])
    return "violated", f"pattern not found: {pattern}", []


# Types this module can decide. Everything else is Tier-2 (LLM-steered).
CHECKABLE_TYPES = {"length", "lexical-ban", "lexical-require", "structure"}


def check_constraints(constraints, plain_text, sections=None):
    """Run Tier-1 checks. Returns a report per constraint:
    {"id", "verdict": satisfied|violated|unchecked, "detail", "locations"}.
    """
    if sections is None:
        sections = build_sections(plain_text)
    reports = []
    for c in constraints:
        cid = c.get("id")
        ctype = c.get("type", "custom")
        if ctype not in CHECKABLE_TYPES:
            reports.append({"id": cid, "verdict": "unchecked",
                            "detail": "steered via prompt (run Deep check to judge)",
                            "locations": []})
            continue
        rng = resolve_scope(c, plain_text, sections)
        if rng is None:
            reports.append({"id": cid, "verdict": "unchecked",
                            "detail": f"section \"{(c.get('scope') or {}).get('name')}\" not found",
                            "locations": []})
            continue
        start, end = rng
        scope_text = plain_text[start:end]
        if ctype == "length":
            verdict, detail, locations = _check_length(c, scope_text)
        elif ctype == "lexical-ban":
            verdict, detail, locations = _check_lexical_ban(c, scope_text, start)
        elif ctype == "lexical-require":
            verdict, detail, locations = _check_lexical_require(c, scope_text, start)
        else:
            verdict, detail, locations = _check_structure(c, scope_text, start)
        scope = c.get("scope") or {}
        if scope.get("kind") == "section":
            detail = f"[{scope.get('name')}] {detail}"
        reports.append({"id": cid, "verdict": verdict,
                        "detail": detail, "locations": locations})
    return reports
