"""Task brief -> proposed requirements, clarification questions, coverage.

Two things make this more than "ask the LLM for a list":

1. **Traceability.** Every proposal carries the exact sentence it came from, so
   the review screen can light up the brief and — more importantly — show which
   sentences mapped to *nothing*. Extraction recall is invisible otherwise: a
   model that silently drops a requirement leaves no trace at all.
2. **Params, not prose.** A proposal without ``params`` is a proposal no code
   can check. The prompt pushes hard for min/max, phrase lists and patterns,
   because a checkable requirement is worth several unfalsifiable ones.
"""

import re

from . import llm
from . import requirements as R

SYSTEM = ("You turn an assignment brief into a checkable requirement list. You are "
          "conservative: every requirement must be traceable to text in the brief, "
          "and you never invent constraints the brief does not state.")

PROMPT = """Brief:
---
{brief}
---

Extract the requirements. Rules:

- One requirement per idea. Keep `text` short, imperative and self-contained.
- Requirements are about the deliverable, not about the assignment's framing.
  Skip scene-setting ("the role is on a catalog team") and skip anything you
  cannot state as a testable property of the finished text or of the process.
- Use the most checkable `type` available:
    length           params {{"min": int, "max": int, "unit": "words"}}
                     (ONLY for the whole deliverable's word count. A limit on
                     lines, characters-per-line, sentences or paragraphs is
                     NOT length — in prose that is `tone`, judged by reading;
                     in code it is `code-prop`, which counts them exactly.)
    lexical-ban      params {{"phrases": ["...", "..."]}}
    lexical-require  params {{"phrases": ["..."]}}
    preserve         params {{"phrases": ["..."]}}   (ONLY for literal passages,
                     quoted verbatim, that must survive in the deliverable's
                     text. Keeping a source's rows, columns, structure or
                     order intact is NOT preserve — that is `content`.)
    structure        params {{"pattern": "<python regex>"}}  ONLY for a literal
                     formatting marker that must appear, like a "Subject:" line
                     or a heading. Never write a regex for "the text must talk
                     about X" — that is a `content` requirement.
    code-prop        params {{"prop": "<one of the below>", ...}}  a property of
                     PYTHON source a parser can settle. Use it for EVERY code
                     constraint in this list — a judge reading code guesses,
                     and guesses wrong about exactly these. One property per
                     requirement; split a sentence that states two.
                       naming        {{"kind":"variable"|"function"|"class",
                                      "convention":"snake_case"|"CamelCase"|"UPPER_SNAKE"}}
                                     (kind is exactly what the brief says. A rule
                                      about VARIABLE names says nothing about a
                                      class name, and vice versa.)
                       defines       {{"kind":"function"|"class"|"variable","name":"..."}}
                                     (the name has to be one the brief itself
                                      gives, spelled as the brief spells it. A
                                      brief that describes something without
                                      naming it — "a function to read the CSV"
                                      — hands you no name, and a name you pick
                                      is checked against code nobody told to
                                      use it: that one is `content`.)
                       imports       {{"module":"numpy"}}
                                     (a library the brief names as a dependency,
                                      under its import name. The language the
                                      code is written in is not an import —
                                      "written in Python" is `content`.)
                       assigned_once {{"name":"max_freq"}}   ("is a constant")
                       module_level  {{"name":"max_freq"}}   ("is a global variable")
                       initializes   {{"name":"obj","call":"MyClass","arg":"signal"}}
                       uses          {{"construct":"for"|"while"|"if"|"return"|"list"|
                                      "listcomp"|"comprehension"|"not"|"class"|"lambda"|
                                      "try"|"with"|"yield"|"raise"|"global"}}
                       forbids       {{"construct": one of the same}}
                       forbids_names {{"names":["deque","handle_message"]}}
                       single_return {{}}      (one return per value-returning function)
                       max_function_lines {{"max": 8}}
                       max_line_length    {{"max": 80}}
                       no_blank_lines_in_body {{}}
                       docstrings    {{"kind":"function"|"class"|"both"}}
                                     (presence and one-line-ness ONLY. "the
                                      docstring must say what it returns" is a
                                      separate `content` requirement.)
                       max_classes   {{"max": 2}}
                     A code constraint none of these fits is `content`,
                     never a code-prop with no prop: a property the
                     checker does not have cannot be checked at all.
    content          a claim about what the text must say, or about what code
                     MEANS — does it compute the right thing, does a sentence
                     say what it should                     (judged)
    tone             a claim about how it must read         (judged)
    tool-use         params {{"tool": "run_check", "before": "finish"}}  (about the
                     agent's process, not the text)
- `scope`: {{"kind":"global"}} or {{"kind":"section","name":"<deliverable name as
  the brief names it>"}}.
- `weight`: 1 normally; 2 when the brief stresses it; 3 only for something that
  would invalidate the deliverable.
- `quote`: the exact sentence from the brief this came from — copied verbatim.
- Ask a `question` only where the brief is genuinely ambiguous in a way that
  changes what a checker would do (a hard limit vs a guideline, who chooses
  something, which of two readings applies). At most 3. Give 2-3 short options.

Return ONLY JSON:
{{"requirements": [{{"type": "...", "text": "...", "params": {{}},
                    "scope": {{"kind": "..."}}, "weight": 1, "quote": "..."}}],
 "questions": [{{"text": "...", "options": ["...", "..."], "affects": "<the text of
                 the requirement it would change, or empty>"}}]}}"""

_SENT = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def sentences(brief):
    out = []
    for m in _SENT.finditer(brief or ""):
        s = m.group(0)
        if s.strip():
            out.append({"start": m.start(), "end": m.end(), "text": s.strip()})
    return out


def _locate(brief, quote):
    if not quote:
        return None
    idx = brief.find(quote)
    if idx < 0:
        idx = brief.lower().find(quote.lower())
    if idx < 0:                      # models paraphrase; fall back to a long prefix
        head = quote[:40]
        idx = brief.lower().find(head.lower()) if head else -1
        if idx < 0:
            return None
        return [idx, min(len(brief), idx + len(quote))]
    return [idx, idx + len(quote)]


# The code-props that check for a literal identifier, and where they keep it.
# The prompt asks for names the brief gave; this is what happens when the ask
# is not honoured, which for a describe-don't-name brief is most of the time.
_NAMED_PARAMS = {
    "defines": ("name",),
    "assigned_once": ("name",),
    "module_level": ("name",),
    "initializes": ("name", "call"),
}


def _grounded(brief, raw):
    """Does every identifier this code-prop checks for appear in the brief?

    A name the brief does not contain is the extractor's invention, not the
    assignment's requirement. Told "a function to scrape recent tweets" the
    model supplies `scrape_tweets`, and the run is then graded on a word the
    agent is never shown — `params` reach the checker, never the prompt — so
    it passes by coincidence or fails forever. Ungrounded, the requirement is
    still real; it is just a claim about the code's meaning, which a judge can
    answer and a parser cannot.
    """
    if (raw.get("type") or "") != "code-prop":
        return True
    params = raw.get("params") or {}
    keys = _NAMED_PARAMS.get(str(params.get("prop") or "").strip().lower())
    if not keys:
        return True
    haystack = brief.lower()
    for key in keys:
        value = str(params.get(key) or "").strip().lower()
        if value and value not in haystack:
            return False
    return True


def extract(brief):
    """Returns {requirements, questions, coverage} — nothing is committed here;
    the review screen owns what enters the store."""
    brief = (brief or "").strip()
    if not brief:
        return {"requirements": [], "questions": [], "coverage": {"mapped": 0, "total": 0,
                                                                  "unmapped": []}}

    data = llm.chat_json([{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": PROMPT.format(brief=brief[:40000])}],
                         temperature=0, max_tokens=12000)

    raw_reqs = data.get("requirements") if isinstance(data, dict) else None
    proposals = []
    for i, raw in enumerate(raw_reqs or []):
        if not isinstance(raw, dict) or not (raw.get("text") or "").strip():
            continue
        span = _locate(brief, (raw.get("quote") or "").strip())
        # Extractors mark everything important, which makes "important"
        # meaningless. Proposals always arrive unpinned: pinning is the user's
        # steering act, made once there are verdicts to steer against, and it
        # is only measurable as a user act if the system never does it first.
        raw = {**raw, "weight": 1}
        if not _grounded(brief, raw):
            raw = {**raw, "verify": "judge"}
        req = R.normalize({**raw, "id": f"R{len(proposals) + 1}",
                           "source": {"kind": "extracted",
                                      "quote": (raw.get("quote") or "").strip(),
                                      "briefSpan": span}},
                          len(proposals))
        proposals.append(req)

    by_text = {r["text"].lower(): r["id"] for r in proposals}
    questions = []
    for i, q in enumerate(data.get("questions") or []):
        if not isinstance(q, dict) or not (q.get("text") or "").strip():
            continue
        affects = (q.get("affects") or "").strip().lower()
        target = by_text.get(affects)
        if not target and affects:
            for text, rid in by_text.items():
                if affects[:30] in text or text[:30] in affects:
                    target = rid
                    break
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()][:4]
        questions.append({"id": f"Q{i + 1}", "text": q["text"].strip(),
                          "options": options or ["yes", "no"], "affects": target,
                          "answer": None})
        if len(questions) >= 3:
            break

    sents = sentences(brief)
    spans = [r["source"]["briefSpan"] for r in proposals if r["source"].get("briefSpan")]
    unmapped = []
    for s in sents:
        covered = any(not (sp[1] <= s["start"] or sp[0] >= s["end"]) for sp in spans)
        if not covered and len(s["text"].split()) >= 5:
            unmapped.append(s)

    return {
        "requirements": proposals,
        "questions": questions,
        "coverage": {"mapped": len(sents) - len(unmapped), "total": len(sents),
                     "unmapped": unmapped},
    }


ANSWER_PROMPT = """A requirement was extracted from an assignment brief:

  type: {type}
  text: {text}
  params: {params}

The user was asked: "{question}"
They answered: "{answer}"

Rewrite the requirement to reflect the answer. Keep it checkable — adjust
`params` if the answer changes a number, a phrase list or a pattern. If the
answer changes nothing, return the requirement unchanged.

Return ONLY JSON: {{"text": "...", "type": "...", "params": {{}}, "weight": 1}}"""


def apply_answer(req, question, answer):
    """Fold a clarification answer back into one requirement."""
    data = llm.chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": ANSWER_PROMPT.format(
             type=req.get("type"), text=req.get("text"),
             params=req.get("params"), question=question, answer=answer)}],
        temperature=0, max_tokens=500)
    if not isinstance(data, dict) or not (data.get("text") or "").strip():
        return req
    updated = R.normalize({**req,
                           "text": data.get("text") or req["text"],
                           "type": data.get("type") or req["type"],
                           "params": data.get("params") or req.get("params"),
                           "weight": data.get("weight", req.get("weight", 1))})
    updated["id"] = req["id"]
    updated["source"] = req.get("source") or {"kind": "extracted"}
    updated["assumed"] = None
    return updated
