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
                                      class name, and vice versa. A rule about
                                      INTERFACE names names no Python construct:
                                      that one is `content`.)
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
                       max_functions {{"max": 1}}
                                     (a cap on HOW MANY functions the file
                                      defines. "no more than one function" is
                                      this one, never max_function_lines —
                                      that caps how LONG a body is, and the two
                                      read alike and check nothing alike.)
                       max_function_lines {{"max": 8}}
                                     (a cap on the lines in one function's
                                      body, and only ever from a brief that
                                      talks about a function's length.)
                       max_total_lines {{"max": 15}}
                                     (a cap on the WHOLE FILE's line count —
                                      "the answer in total must not exceed 15
                                      lines". Three caps read alike and check
                                      nothing alike: this one counts the file's
                                      lines, max_function_lines counts one
                                      body's, max_line_length counts a line's
                                      characters. Take the one the brief's own
                                      unit names.)
                       max_line_length    {{"max": 80}}
                                     (characters in a line, only ever from a
                                      brief that says characters or width.)
                       no_blank_lines_in_body {{}}
                       docstrings    {{"kind":"function"|"class"|"both"}}
                                     (presence and one-line-ness ONLY. "the
                                      docstring must say what it returns" is a
                                      separate `content` requirement.)
                       max_classes   {{"max": 2}}
                       returns       {{"call":"max_subsequence([1,2,3,7,2,10])",
                                      "expect":"5"}}
                                     (ONLY for a worked example the brief itself
                                      states, input and output both — ">>> f(3)
                                      True", "Input: nums = [8,6,1,5,3] Output:
                                      9". Copy the call and the result exactly,
                                      one requirement per example; never invent
                                      one, and never write the answer to the
                                      task itself as an example.)
                     A code constraint none of these fits is `content`,
                     never a code-prop with no prop: a property the
                     checker does not have cannot be checked at all.
                     Never substitute a near-miss for a property or a
                     construct the list lacks. A brief that bans `switch`
                     bans `switch` — write that word, and let the checker
                     report it unverified; rewriting it as a ban on `if`
                     fails correct code over a rule nobody stated. The same
                     goes for a property: "returns a str" is not `uses`
                     return, and "takes no arguments" is not `defines`.
                     Those are `content`.
    table-prop       params {{"prop": "<one of the below>", ...}}  a property of
                     a CSV deliverable, decided by comparing it with the table
                     the brief attached. Every promise a wrangling brief makes
                     ABOUT its source belongs here and not in `content`: these
                     are settled by a differ, and reading a hundred rows to
                     answer them is exactly what nobody can do reliably.
                       rows          {{}}    ("keep every row")
                       columns       {{}}    ("keep the header", "keep every
                                             column")
                       row_order     {{}}    ("keep the original row order")
                       only_columns_change {{"columns":["event"]}}
                                     ("leave every other column exactly as it
                                      is", "change only the event column" —
                                      list the columns the brief SAYS may
                                      change, spelled as the brief spells them)
                       column_case   {{"column":"event","case":"upper"|"lower"}}
                       count_matches {{"column":"event","value":"DINNER"}}
                                     (the deliverable has to REPORT how many
                                      rows hold that value. Scope this one to
                                      the file the answer goes in; the count
                                      itself is taken from the table.)
                       trimmed_copy  {{"columns":["event","notes"]}}
                                     ("trim the padded whitespace and change
                                      nothing else": every cell is the source
                                      cell trimmed. Only where trimming is the
                                      ONLY thing the brief does to the column.)
                       no_padding    {{"columns":["event"]}}   (no cell starts or
                                      ends with whitespace; use it where the
                                      column is trimmed AND changed otherwise)
                       single_spaces {{"columns":["event"]}}   ("collapse runs of
                                      spaces into one")
                       value_map     {{"column":"sponsor","from":["Adams' Restaurant",
                                      "Adam's Restaurant"],"to":"ADAM'S RESTAURANT"}}
                                     (a rule that says WHICH source values become
                                      what: "BREAKFAST MENU is BREAKFAST", "the
                                      placeholder [...] is emptied" — `to` is ""
                                      then —, "1949-23-12 belongs at
                                      1949-12-23T00:00:00Z". One requirement per
                                      target value, `from` spelled exactly as the
                                      brief spells the source values, `to` the
                                      final value the cell must hold.)
                       no_characters {{"columns":["sponsor"],"characters":";_-"}}
                                     ("turn every semicolon, underscore and
                                      hyphen into a space": none may remain)
                       column_pattern {{"column":"date",
                                       "pattern":"\\d{{4}}-\\d{{2}}-\\d{{2}}T00:00:00Z"}}
                                     (every non-blank value has this shape —
                                      a date format, a code without its
                                      decoration. A Python regex, matched in
                                      full; blank cells are skipped.)
                     A rule stated as a PROCEDURE over every value — turn
                     hyphens into spaces, collapse the spaces, trim, uppercase;
                     write every date as YYYY-MM-DDT00:00:00Z — is written as
                     the properties it decomposes into, one requirement each:
                     no_characters, single_spaces, no_padding, column_case,
                     column_pattern. Write the examples the brief gives for it
                     as value_map requirements too, because those are exact.
                     Only what none of these can state — how an ambiguous date
                     is read, which spelling a fold keeps — is `content`.
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
# The table properties name columns, and a column the brief never mentions is
# the extractor's invention exactly as an invented function name is.
_TABLE_NAMED_PARAMS = {
    "only_columns_change": ("columns",),
    "column_case": ("column",),
    "count_matches": ("column", "value"),
    "trimmed_copy": ("columns",),
    "no_padding": ("columns",),
    "single_spaces": ("columns",),
    "no_characters": ("columns",),
    "column_pattern": ("column",),
    # `to` is deliberately not here: "emptied" is a `to` of "", which no brief
    # spells out, and the value a cell must END as is the brief's claim to
    # make. The source spellings it maps FROM must be the brief's own.
    "value_map": ("column", "from"),
}

_NAMED_PARAMS = {
    "defines": ("name",),
    "assigned_once": ("name",),
    "module_level": ("name",),
    "initializes": ("name", "call"),
}


# The words a brief uses for each construct `uses`/`forbids` can name. A
# construct is a legal parameter whatever the brief said, so nothing downstream
# can tell that "do not use switch statements" was checked as a ban on `if` —
# the checker answers the question it was handed, confidently, and fails
# correct code over a rule nobody wrote. Grounding is the only place this is
# catchable: if the brief never says the word, the requirement is not about
# that construct.
_CONSTRUCT_WORDS = {
    "for": ("for",), "while": ("while",), "if": ("if",), "return": ("return",),
    "list": ("list",), "listcomp": ("comprehension",),
    "comprehension": ("comprehension",), "not": ("not",), "class": ("class",),
    "lambda": ("lambda",), "try": ("try", "except"), "with": ("with",),
    "yield": ("yield",), "global": ("global",),
    "raise": ("rais", "throw", "error", "exception"),
}

# A naming rule is about how something is SPELLED. A requirement that never
# mentions a convention is not one — "make it take no arguments" came back as
# naming/function/snake_case, which passes on a function that is already
# snake_case while the thing actually asked for goes unchecked. A green chip
# nobody verified is worse than a red one that is wrong.
_NAMING_WORDS = ("snake_case", "snake case", "camelcase", "camel case",
                 "pascalcase", "pascal case", "upper_snake", "uppercase",
                 "naming", "convention", "spelled", "named in", "case")


def _says(text, words):
    """Does the text use one of these words — allowing the endings English puts
    on them? "comprehensions" and "raising" are the brief naming a
    comprehension and a raise; a matcher that demands the bare stem calls both
    ungrounded and demotes a correct requirement to the judge. Words of three
    letters or fewer keep both boundaries, because `for` inside `format` and
    `if` inside `iframe` are not the brief naming anything."""
    for w in words:
        edge = r"\b" if len(w) > 3 else r"\b"
        tail = "" if len(w) > 3 else r"\b"
        if re.search(edge + re.escape(w) + tail, text):
            return True
    return False


# The properties that answer "this is PRESENT". A brief sentence that NEGATES
# one of them has nothing here to answer it — there is no negated module_level
# and no negated assigned_once — and the checker, asked the positive question,
# reports correct code as violating it. A chip no edit can settle.
#
# Measured on 2026-09-04 over sixteen CodeIF instances: four of the eleven run
# against the agent carried one ("variable x should not be a global variable",
# "should not be a constant", "should not use the bisect module"), and in each
# the chip stayed red on code that obeyed the brief. One run spent sixteen
# steps chasing it.
#
# The caps are deliberately NOT in this set. "No more than three functions" is
# negated language and max_functions is exactly the right property for it, as
# forbids and forbids_names are for a banned construct or name. Only the
# presence claims are here, and only because the schema has no opposite for
# them.
_PRESENCE_PROPS = ("imports", "module_level", "assigned_once", "defines",
                   "initializes", "uses", "single_return", "docstrings")
_WS = re.compile(r"\s+")
_NEGATION = re.compile(r"\b(?:not|never|no|without|avoid|cannot|nor)\b"
                       r"|\bn't\b|\bout of\b", re.I)


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
    rtype = raw.get("type") or ""
    if rtype == "table-prop":
        params = raw.get("params") or {}
        keys = _TABLE_NAMED_PARAMS.get(
            str(params.get("prop") or "").strip().lower())
        if not keys:
            return True
        # Whitespace collapsed on both sides: the brief wraps its lines, so a
        # value it spells across a line break — "[Restaurant name\nand/or
        # location not given]" — is still the brief's own spelling.
        haystack = _WS.sub(" ", brief.lower())
        for key in keys:
            value = params.get(key)
            values = value if isinstance(value, (list, tuple)) else [value]
            for v in values:
                v = _WS.sub(" ", str(v or "").strip().lower())
                if v and v not in haystack:
                    return False
        return True
    if rtype != "code-prop":
        return True
    params = raw.get("params") or {}
    prop = str(params.get("prop") or "").strip().lower()
    # The requirement's own sentence, not the whole brief: a construct named
    # somewhere else in a long task says nothing about THIS rule.
    said = f"{raw.get('text') or ''} {raw.get('quote') or ''}".lower()

    # A ban the schema cannot state. Judged, not parsed: the requirement is
    # real, it is only this property that cannot answer it.
    if prop in _PRESENCE_PROPS and _NEGATION.search(said):
        return False

    # "Define an interface named X" names no Python construct. The agent may
    # answer it with a class, a Protocol or a function, and `defines` has to be
    # told which before it can look — asked for a function, it reports a class
    # of that exact name as missing, which is what CodeIF 1087's first draft
    # was failed for.
    if prop == "defines" and _says(said, ("interface",)):
        return False

    if prop in ("uses", "forbids"):
        construct = str(params.get("construct") or "").strip().lower()
        words = _CONSTRUCT_WORDS.get(construct)
        # An unknown construct is the checker's problem, not grounding's — it
        # already answers `unverified` and says so.
        return not words or _says(said, words)

    if prop == "naming":
        return _says(said, _NAMING_WORDS)

    # Three caps that read alike and check nothing alike. "Should not exceed
    # 14 lines" came back as max_line_length (every line failed), "at most two
    # parameters" as max_functions (four functions, cap two). Each cap has to
    # be grounded in the unit its own sentence names.
    if prop == "max_line_length":
        return _says(said, ("character", "width", "column", "wide", "long"))
    if prop == "max_total_lines" or prop == "max_function_lines":
        return _says(said, ("line",))
    if prop == "max_functions":
        return _says(said, ("function", "method")) and not _says(said, ("parameter", "argument"))
    if prop == "max_classes":
        return _says(said, ("class",))

    # An example is the brief's or it is nobody's: the expected value has to
    # be written in the brief, or the check is grading code against a number
    # the extractor made up.
    if prop == "returns":
        expect = _WS.sub(" ", str(params.get("expect") if params.get("expect") is not None else "")).strip()
        return bool(expect) and expect.lower() in _WS.sub(" ", brief.lower())

    keys = _NAMED_PARAMS.get(prop)
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
