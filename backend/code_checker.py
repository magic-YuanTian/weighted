"""Deterministic checks over Python source — Tier-1 for code deliverables.

The v3 `checker.py` speaks about prose: a word count, a phrase present or
absent, one regex marker. A code brief speaks about something else entirely
("snake_case variable names", "one return statement per function", "no line
over 80 characters"), and none of it had a type to land in, so all of it went
to the judge. That is how a run ended with the judge reporting

    R12  Use snake_case naming for all variables.
         -> "The class name SignalProcessor is not snake_case."

against a requirement whose own text says *variables*. A parser cannot make
that mistake: `ast.Name`, `ast.FunctionDef` and `ast.ClassDef` are three
different nodes, and this module never asks one about the other.

Everything here answers from the syntax tree or from the raw lines. What the
code *means* — whether it really computes an FFT, whether a docstring says
what it returns — is not in here and belongs to the judge.

`check(prop, params, source)` returns (verdict, detail, spans), where spans are
(start, end) character offsets into `source` for the evidence the UI shows.
"""

import ast
import keyword
import re

# Names bound by an import are the library's choice, not the author's, so no
# naming convention is checked against them.
CONVENTIONS = {
    "snake_case": re.compile(r"^_{0,2}[a-z][a-z0-9_]*_{0,2}$"),
    "camelcase": re.compile(r"^_?[A-Z][A-Za-z0-9]*$"),
    "upper_snake": re.compile(r"^_?[A-Z][A-Z0-9_]*$"),
}

_CONVENTION_ALIASES = {
    "pascalcase": "camelcase", "camel_case": "camelcase",
    "upper_snake_case": "upper_snake", "screaming_snake_case": "upper_snake",
    "upper": "upper_snake", "constant_case": "upper_snake",
}

_CONSTRUCTS = {
    "for": (ast.For, ast.AsyncFor),
    "while": (ast.While,),
    "if": (ast.If,),
    "return": (ast.Return,),
    "try": (ast.Try,),
    "class": (ast.ClassDef,),
    "lambda": (ast.Lambda,),
    "listcomp": (ast.ListComp,),
    "dictcomp": (ast.DictComp,),
    "setcomp": (ast.SetComp,),
    "comprehension": (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
    "with": (ast.With, ast.AsyncWith),
    "yield": (ast.Yield, ast.YieldFrom),
    "assert": (ast.Assert,),
    "raise": (ast.Raise,),
}

_CONSTRUCT_ALIASES = {
    "for-loop": "for", "for_loop": "for", "forloop": "for",
    "while-loop": "while", "while_loop": "while", "whileloop": "while",
    "if-statement": "if", "if_statement": "if",
    "list-comprehension": "listcomp", "list_comprehension": "listcomp",
    "listcomprehension": "listcomp", "comprehensions": "comprehension",
    "try-except": "try", "try_except": "try",
    "return-keyword": "return", "return_keyword": "return",
}

# Spelled out rather than singularized: str.rstrip takes a character set, not a
# suffix, so "class".rstrip("s") is "cla" — which matched no branch and turned
# every class-scoped check into a silent "unverified".
_KINDS = {
    "variable": "variable", "variables": "variable", "var": "variable",
    "vars": "variable", "name": "variable", "names": "variable",
    "function": "function", "functions": "function", "func": "function",
    "funcs": "function", "method": "function", "methods": "function",
    "def": "function", "defs": "function",
    "class": "class", "classes": "class", "cls": "class",
    "both": "both", "all": "both", "any": "both",
}


def _kind(params, default):
    raw = (params.get("kind") or default or "").strip().lower()
    return _KINDS.get(raw, raw)


def _cap(params, *keys):
    """An integer cap, or None when none was given. Distinguished from 0 on
    purpose: "no more than 0 classes" is a real constraint, and `or 0` read it
    as an absent one."""
    for k in keys:
        if params.get(k) is None:
            continue
        try:
            return int(params[k])
        except (TypeError, ValueError):
            return None
    return None


PROPS = ("naming", "defines", "imports", "assigned_once", "module_level",
         "initializes", "uses", "forbids", "forbids_names", "single_return",
         "max_function_lines", "max_line_length", "no_blank_lines_in_body",
         "docstrings", "max_classes")


# ---------------------------------------------------------------- offsets

def _line_starts(source):
    starts, pos = [0], 0
    for line in source.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    return starts


def _span(starts, node):
    """A node's (start, end) as offsets into the source it was parsed from."""
    lo = node.lineno - 1
    start = starts[lo] + node.col_offset
    hi = getattr(node, "end_lineno", node.lineno) - 1
    col = getattr(node, "end_col_offset", None)
    if col is None or hi + 1 >= len(starts):
        return start, starts[min(hi + 1, len(starts) - 1)]
    return start, starts[hi] + col


def _line_span(starts, lineno):
    return starts[lineno - 1], starts[min(lineno, len(starts) - 1)]


# ---------------------------------------------------------------- walking

def _functions(tree):
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _own_body(node):
    """Every node under `node` except those inside a nested function or lambda.

    "Exactly one return statement" is a claim about *this* function; a nested
    helper's return is not this function's. Walking blindly counts it and
    fails a function that is correct."""
    out = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(child)
        out.extend(_own_body(child))
    return out


def _bound_names(tree):
    """(name, node) for every identifier the author binds as a variable —
    assignment targets, loop variables, comprehension targets, parameters.
    Class and function names are NOT here: they have their own nodes, and
    conflating them is precisely the bug this module exists to prevent."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.append((node.id, node))
        elif isinstance(node, ast.arg):
            out.append((node.arg, node))
    return out


def _module_level_targets(tree):
    names = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            targets = [stmt.target]
        else:
            continue
        for t in targets:
            for node in ast.walk(t):
                if isinstance(node, ast.Name):
                    names.setdefault(node.id, stmt)
    return names


def _imported_modules(tree):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def _body_line_range(fn):
    """(first, last) source lines of a function body, docstring included.

    Counted from the first body statement so a signature wrapped over three
    lines is not charged to the body — the brief says "the lines between the
    def line and the end of the function"."""
    if not fn.body:
        return None
    return fn.body[0].lineno, getattr(fn, "end_lineno", fn.body[-1].lineno)


def _wanted_name(params, *keys, label="name"):
    """The identifier a check is asked to look for -> (name, why not).

    Two failures, one guard. No name at all is the one the checks already had.
    The other is a name that is not a name: the extractor writes a placeholder
    whenever the brief describes something it never names — "<unspecified>",
    "<name>" — and a placeholder walks straight past `if not name`, so the
    check goes on to report "no function named <unspecified> is defined" at
    every step for the rest of the run. That is a violation no edit can clear
    and nothing can route away, because `usable` only ever sees the verdict.
    Both cases are unverified, which is what `usable` reads to send the
    requirement to a judge instead.
    """
    raw = ""
    for key in keys:
        if params.get(key):
            raw = str(params[key]).strip()
            break
    if not raw:
        return None, f"no {label} given to look for"
    if not raw.isidentifier() or keyword.iskeyword(raw):
        return None, f"{raw!r} is not a {label} a parser can look for"
    return raw, None


# Distribution name -> import name. `imports` compares the word the brief uses
# for a library against the words the file actually imports, and for these two
# the answer is not the same word: asked for "pytorch" it reports "pytorch is
# not imported" over a file that imports torch, for the whole run.
_IMPORT_ALIASES = {
    "pytorch": "torch",
    "scikit-learn": "sklearn", "scikit_learn": "sklearn", "scikitlearn": "sklearn",
    "beautifulsoup4": "bs4", "beautifulsoup": "bs4",
    "pillow": "PIL",
    "opencv-python": "cv2", "opencv": "cv2",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "attrs": "attr",
}

# Not libraries. "Your entire response should be written in python" is a claim
# about the language, and the extractor turns it into {"prop": "imports",
# "module": "python"} — a check that asks whether the file says `import
# python`. No working program satisfies it, and a broken one does: an agent
# told before every action that "python is not imported" eventually writes the
# line, and the deliverable stops running while the chip turns green.
_NOT_MODULES = {"python", "python2", "python3"}


# ---------------------------------------------------------------- the checks

def check(prop, params, source):
    """Run one property against one Python source. -> (verdict, detail, spans).

    A file that does not parse fails every property it is asked about, and
    says so: a syntax error is not "unverified", it is a broken deliverable.
    """
    prop = (prop or "").strip().lower().replace("-", "_")
    if prop not in PROPS:
        return "unverified", f"no code check named {prop!r}", []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        starts = _line_starts(source)
        line = min(max(e.lineno or 1, 1), max(len(starts) - 1, 1))
        return "violated", f"the file does not parse: {e.msg} (line {e.lineno})", \
            [_line_span(starts, line)]
    starts = _line_starts(source)
    return _PROPS[prop](tree, params or {}, source, starts)


def usable(prop, params):
    """Can this property answer at all, with these parameters?

    Asked before a requirement is routed, not after. A check that says
    "unverified" because its parameters are missing — no name to look for, no
    cap, an empty list of forbidden names — says it for the whole run, and
    nothing blocks the finish gate on unverified: the requirement would simply
    never be checked, and the run would end green with it unexamined.

    Answered by running the property against an empty module rather than from a
    second table of what each one needs. Over no code every real verdict is
    still reachable — "no function named X is defined" is a violation, "no
    variable names to check" is a pass — and the only way to get "unverified"
    out of it is a parameter the check cannot work with: one that is missing,
    or one that is not what it claims to be (a placeholder where a name should
    be, a language where a module should be).
    """
    return check(prop, params, "")[0] != "unverified"


def _naming(tree, params, source, starts):
    kind = _kind(params, "variable")
    want = (params.get("convention") or "snake_case").lower().replace("-", "_")
    want = _CONVENTION_ALIASES.get(want, want)
    rx = CONVENTIONS.get(want)
    if rx is None:
        return "unverified", f"unknown naming convention {want!r}", []

    if kind == "variable":
        pairs, label = _bound_names(tree), "variable"
    elif kind == "function":
        pairs = [(f.name, f) for f in _functions(tree)]
        label = "function"
    elif kind == "class":
        pairs = [(n.name, n) for n in ast.walk(tree)
                 if isinstance(n, ast.ClassDef)]
        label = "class"
    else:
        return "unverified", f"unknown naming kind {kind!r}", []

    if not pairs:
        return "satisfied", f"no {label} names to check", []
    bad, seen = [], set()
    for name, node in pairs:
        if name in seen or rx.match(name):
            continue
        seen.add(name)
        bad.append((name, node))
    if not bad:
        return "satisfied", f"all {len(pairs)} {label} names are {want}", []
    names = ", ".join(n for n, _ in bad[:6])
    return "violated", f"{label} names not {want}: {names}", \
        [_span(starts, node) for _, node in bad[:6]]


def _defines(tree, params, source, starts):
    kind = _kind(params, "function")
    name, why = _wanted_name(params, "name")
    if name is None:
        return "unverified", why, []
    if kind == "function":
        hits = [f for f in _functions(tree) if f.name == name]
    elif kind == "class":
        hits = [n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == name]
    elif kind == "variable":
        hits = [node for n, node in _bound_names(tree) if n == name]
    else:
        return "unverified", f"unknown definition kind {kind!r}", []
    if hits:
        return "satisfied", f"{kind} {name} is defined", \
            [_span(starts, hits[0])]
    return "violated", f"no {kind} named {name} is defined", []


def _imports(tree, params, source, starts):
    raw = str(params.get("module") or params.get("name") or "").strip()
    if raw.lower() in _NOT_MODULES:
        return "unverified", f"{raw!r} is a language, not a module to import", []
    head = _IMPORT_ALIASES.get(raw.lower(), raw.split(".")[0])
    module, why = _wanted_name({"module": head}, "module", label="module")
    if module is None:
        return "unverified", why, []
    mods = _imported_modules(tree)
    if module in mods:
        return "satisfied", f"{module} is imported", []
    have = ", ".join(sorted(mods)) or "nothing"
    return "violated", f"{module} is not imported (imports: {have})", []


def _assigned_once(tree, params, source, starts):
    name, why = _wanted_name(params, "name")
    if name is None:
        return "unverified", why, []
    nodes = [node for n, node in _bound_names(tree) if n == name]
    if len(nodes) == 1:
        return "satisfied", f"{name} is assigned exactly once", \
            [_span(starts, nodes[0])]
    if not nodes:
        return "violated", f"{name} is never assigned", []
    return "violated", f"{name} is assigned {len(nodes)} times", \
        [_span(starts, n) for n in nodes[:6]]


def _module_level(tree, params, source, starts):
    name, why = _wanted_name(params, "name")
    if name is None:
        return "unverified", why, []
    at = _module_level_targets(tree).get(name)
    if at is not None:
        return "satisfied", f"{name} is assigned at module level", \
            [_span(starts, at)]
    if any(n == name for n, _ in _bound_names(tree)):
        return "violated", f"{name} is assigned, but never at module level", []
    return "violated", f"{name} is never assigned", []


def _initializes(tree, params, source, starts):
    """`name = Call(arg)` — satisfied by ANY assignment that matches.

    Any, not the last: the brief asks for the object to be built from a given
    input, and one built inside the function that has that input is exactly
    that. Demanding the module-level binding instead is what turned a working
    solution into a reported contradiction."""
    name, why = _wanted_name(params, "name")
    call, call_why = _wanted_name(params, "call", "class")
    arg = params.get("arg") or ""
    if name is None or call is None:
        return "unverified", (why or call_why
                              or "needs both a name and the class to instantiate"), []

    matches, wrong_arg, assigns = [], [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        assigns.append(node)
        value = node.value
        called = getattr(getattr(value, "func", None), "id", None) \
            or getattr(getattr(value, "func", None), "attr", None)
        if not isinstance(value, ast.Call) or called != call:
            continue
        if not arg:
            matches.append(node)
            continue
        names = {a.id for a in value.args if isinstance(a, ast.Name)}
        names |= {k.value.id for k in value.keywords
                  if isinstance(k.value, ast.Name)}
        (matches if arg in names else wrong_arg).append(node)

    if matches:
        return "satisfied", f"{name} = {call}({arg})".replace("()", "(…)"), \
            [_span(starts, matches[0])]
    if wrong_arg:
        return "violated", \
            f"{name} is built from {call}, but not from {arg}", \
            [_span(starts, wrong_arg[0])]
    if assigns:
        return "violated", f"{name} is assigned, but not as {call}(…)", \
            [_span(starts, assigns[0])]
    return "violated", f"no object named {name} is initialized", []


def _construct_nodes(tree, construct):
    key = (construct or "").strip().lower().replace(" ", "_")
    key = _CONSTRUCT_ALIASES.get(key, key)
    if key == "global":
        # "use global variables" is about module scope, not the `global`
        # keyword — a module-level binding is what the brief is asking for.
        return "global variables", [n for n in tree.body
                                    if isinstance(n, (ast.Assign, ast.AnnAssign))]
    if key == "list":
        return "a list", [n for n in ast.walk(tree)
                          if isinstance(n, ast.List)
                          or (isinstance(n, ast.Call)
                              and getattr(n.func, "id", None) == "list")]
    if key == "not":
        return "the not keyword", [n for n in ast.walk(tree)
                                   if isinstance(n, ast.UnaryOp)
                                   and isinstance(n.op, ast.Not)]
    types = _CONSTRUCTS.get(key)
    if types is None:
        return None, None
    return key, [n for n in ast.walk(tree) if isinstance(n, types)]


def _uses(tree, params, source, starts):
    label, nodes = _construct_nodes(tree, params.get("construct"))
    if nodes is None:
        return "unverified", f"unknown construct {params.get('construct')!r}", []
    if nodes:
        return "satisfied", f"{label} is used ({len(nodes)}×)", \
            [_span(starts, nodes[0])]
    return "violated", f"{label} is never used", []


def _forbids(tree, params, source, starts):
    label, nodes = _construct_nodes(tree, params.get("construct"))
    if nodes is None:
        return "unverified", f"unknown construct {params.get('construct')!r}", []
    if not nodes:
        return "satisfied", f"{label} does not appear", []
    return "violated", f"{label} appears {len(nodes)}×", \
        [_span(starts, n) for n in nodes[:6]]


def _forbids_names(tree, params, source, starts):
    wanted = params.get("names") or params.get("phrases") or []
    if isinstance(wanted, str):
        wanted = [wanted]
    wanted = {str(w).strip() for w in wanted if str(w).strip()}
    if not wanted:
        return "unverified", "no names given to forbid", []
    hits = []
    for node in ast.walk(tree):
        found = None
        if isinstance(node, ast.Name) and node.id in wanted:
            found = node.id
        elif isinstance(node, ast.Attribute) and node.attr in wanted:
            found = node.attr
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name.split(".")[-1] in wanted or (a.asname in wanted):
                    found = a.name
        if found:
            hits.append((found, node))
    if not hits:
        return "satisfied", f"none of {len(wanted)} forbidden names appear", []
    names = ", ".join(sorted({n for n, _ in hits})[:6])
    return "violated", f"forbidden names used: {names}", \
        [_span(starts, node) for _, node in hits[:6]]


def _single_return(tree, params, source, starts):
    offenders = []
    checked = 0
    for fn in _functions(tree):
        returns = [n for n in _own_body(fn)
                   if isinstance(n, ast.Return) and n.value is not None]
        if not returns:
            continue                       # returns nothing — rule is silent
        checked += 1
        if len(returns) > 1:
            offenders.append((fn, returns))
    if not checked:
        return "satisfied", "no function returns a value", []
    if not offenders:
        return "satisfied", \
            f"each of the {checked} value-returning functions has one return", []
    worst = ", ".join(f"{fn.name} ({len(rs)})" for fn, rs in offenders[:4])
    return "violated", f"more than one return: {worst}", \
        [_span(starts, rs[1]) for _, rs in offenders[:6]]


def _max_function_lines(tree, params, source, starts):
    cap = _cap(params, "max", "lines")
    if cap is None or cap <= 0:
        return "unverified", "no line cap given", []
    over = []
    for fn in _functions(tree):
        rng = _body_line_range(fn)
        if rng is None:
            continue
        length = rng[1] - rng[0] + 1
        if length > cap:
            over.append((fn, length))
    if not over:
        return "satisfied", f"every function body is at most {cap} lines", []
    worst = ", ".join(f"{fn.name} ({n})" for fn, n in
                      sorted(over, key=lambda p: -p[1])[:4])
    return "violated", f"function bodies over {cap} lines: {worst}", \
        [_span(starts, fn) for fn, _ in over[:6]]


def _max_line_length(tree, params, source, starts):
    cap = _cap(params, "max", "chars")
    if cap is None or cap <= 0:
        return "unverified", "no character cap given", []
    over = [(i + 1, len(line))
            for i, line in enumerate(source.split("\n")) if len(line) > cap]
    if not over:
        return "satisfied", f"every line is under {cap} characters", []
    worst = ", ".join(f"line {n} ({w})" for n, w in
                      sorted(over, key=lambda p: -p[1])[:4])
    return "violated", f"{len(over)} line(s) over {cap} characters: {worst}", \
        [_line_span(starts, n) for n, _ in over[:6]]


def _no_blank_lines_in_body(tree, params, source, starts):
    lines = source.split("\n")
    blanks = []
    for fn in _functions(tree):
        rng = _body_line_range(fn)
        if rng is None:
            continue
        for n in range(rng[0], rng[1] + 1):
            if n - 1 < len(lines) and not lines[n - 1].strip():
                blanks.append((fn.name, n))
    if not blanks:
        return "satisfied", "no blank lines inside any function body", []
    where = ", ".join(f"{name} (line {n})" for name, n in blanks[:4])
    return "violated", f"{len(blanks)} blank line(s) in function bodies: {where}", \
        [_line_span(starts, n) for _, n in blanks[:6]]


def _docstrings(tree, params, source, starts):
    """Presence and one-line-ness only. Whether the sentence says what the
    function takes and returns is a claim about meaning — that stays judged."""
    kind = _kind(params, "both")
    targets = []
    if kind in ("function", "both"):
        targets += [("function", f) for f in _functions(tree)]
    if kind in ("class", "both"):
        targets += [("class", n) for n in ast.walk(tree)
                    if isinstance(n, ast.ClassDef)]
    if not targets:
        return "satisfied", "nothing to document", []
    missing, multiline = [], []
    for label, node in targets:
        doc = ast.get_docstring(node)
        if doc is None:
            missing.append((label, node))
        elif len(doc.strip().split("\n")) > 1:
            multiline.append((label, node))
    if not missing and not multiline:
        return "satisfied", \
            f"all {len(targets)} have a one-line docstring", []
    parts = []
    if missing:
        parts.append("no docstring: "
                     + ", ".join(n.name for _, n in missing[:4]))
    if multiline:
        parts.append("docstring is not one line: "
                     + ", ".join(n.name for _, n in multiline[:4]))
    return "violated", "; ".join(parts), \
        [_span(starts, n) for _, n in (missing + multiline)[:6]]


def _max_classes(tree, params, source, starts):
    cap = _cap(params, "max")
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if cap is None or cap < 0:
        return "unverified", "no class cap given", []
    if len(classes) <= cap:
        return "satisfied", f"{len(classes)} class(es), cap is {cap}", []
    return "violated", f"{len(classes)} classes, cap is {cap}", \
        [_span(starts, n) for n in classes[:6]]


_PROPS = {
    "naming": _naming,
    "defines": _defines,
    "imports": _imports,
    "assigned_once": _assigned_once,
    "module_level": _module_level,
    "initializes": _initializes,
    "uses": _uses,
    "forbids": _forbids,
    "forbids_names": _forbids_names,
    "single_return": _single_return,
    "max_function_lines": _max_function_lines,
    "max_line_length": _max_line_length,
    "no_blank_lines_in_body": _no_blank_lines_in_body,
    "docstrings": _docstrings,
    "max_classes": _max_classes,
}
