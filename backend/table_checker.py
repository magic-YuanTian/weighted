"""Tier-1 checks for a table deliverable, read against the table it came from.

Why this exists. The wrangling tasks hand the agent a hundred-row CSV and ask
for a repaired copy, and most of what the brief promises is *relational*: keep
every row, keep the original order, leave every column but these two exactly as
it is. None of that can be answered from the deliverable alone, so all of it was
going to the judge — and on 2026-09-04 the judge got it wrong ten times out of
ten. Both runs ended with a cleaned.csv identical to the benchmark's own gold
table and were told they had duplicated blocks, dropped a row, and uppercased a
column they had not touched. One of them was failed for answering 13 when the
answer is 13, by a judge that insisted on 14 — the same 14 it had marked
satisfied in an earlier run when the agent actually wrote it.

Every property here is a comparison a differ can make in a millisecond, and the
comparison is against the ATTACHMENT: the read-only source the brief handed
over. The gold table is never involved — it stays out of the session entirely,
as it must, because the agent could otherwise read the answer.

That is a deliberate crossing of the line drawn in tools.Attachments: nothing
else in the checker can see attachments, because the workspace is the
deliverable and the attachment is source material. A relational promise is the
one thing that cannot be checked on that side of the line, so this module gets
the source passed in and nothing else does.
"""

import csv
import io
import re

PROPS = ("rows", "columns", "row_order", "only_columns_change",
         "column_case", "count_matches",
         "trimmed_copy", "value_map", "no_padding", "single_spaces",
         "no_characters", "column_pattern")

_NEEDS = {
    "rows": (),
    "columns": (),
    "row_order": (),
    "only_columns_change": ("columns",),
    "column_case": ("column",),
    "count_matches": ("column", "value"),
    "trimmed_copy": ("columns",),
    "value_map": ("column", "from"),
    "no_padding": ("columns",),
    "single_spaces": ("columns",),
    "no_characters": ("columns", "characters"),
    "column_pattern": ("column", "pattern"),
}

# Four properties added on 2026-09-04 after the judge was watched failing
# correct tables over exactly these rules. "Trim the padding and change
# nothing else" was failed over punctuation the trimming had not touched;
# "BREAKFAST MENU becomes BREAKFAST" was failed because no BREAKFAST MENU was
# left to see; "the placeholder is emptied" was PASSED on a table where it had
# been uppercased instead. Each is a comparison against the source that a
# differ makes exactly, and each is safe under composition: whatever else the
# brief does to the column, the trimmed value, the final value of a mapped
# cell, the absence of padding are true of the finished table or they are not.
#
# value_map matches a source cell to the brief's spelling of it loosely in one
# respect only: runs of separators (whitespace ; _ -) count as one space, so
# the brief's "[Restaurant name and/or location not given]" is the source's
# "[Restaurant ;name ;and/or ;location ;not ;given]". Case is kept, because
# the same brief distinguishes that placeholder from "[Restaurant And/Or
# Location Not Given]" by case alone.
_SEPARATORS = re.compile(r"[\s;_\-]+")


def _loose(value):
    return _SEPARATORS.sub(" ", str(value or "")).strip()


def _parse(text):
    """CSV text -> (header, rows), or (None, None) when it is not a table."""
    if not (text or "").strip():
        return None, None
    try:
        table = list(csv.reader(io.StringIO(text)))
    except (csv.Error, ValueError):
        return None, None
    if len(table) < 2 or len(table[0]) < 2:
        return None, None
    return table[0], table[1:]


def _wanted_columns(params):
    raw = params.get("columns") or params.get("column") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(c).strip() for c in raw if str(c).strip()]


def usable(prop, params):
    """Can this property answer at all, with these parameters?

    Asked before a requirement is routed, for the reason requirements.normalize
    gives: a check that will say "unverified" for the whole run is a
    requirement that never gets examined, and nothing blocks the finish gate on
    unverified.
    """
    prop = (prop or "").strip().lower().replace("-", "_")
    if prop not in PROPS:
        return False
    params = params or {}
    for key in _NEEDS[prop]:
        value = params.get(key)
        if isinstance(value, str):
            if not value.strip():
                return False
        elif isinstance(value, (list, tuple)):
            if not [v for v in value if str(v).strip()]:
                return False
        elif value is None:
            return False
    return True


def check(prop, params, source, reference, stated=""):
    """Run one property. -> (verdict, detail, rows_to_blame).

    `source` is the table the agent produced, `reference` the attachment it was
    made from, and `stated` the prose the requirement is scoped to — which for
    every property but count_matches is unused, and for that one is where the
    agent writes the number being checked.

    The third return value is a list of 0-based data-row indexes, which the
    caller turns into evidence; a property that blames the whole table returns
    an empty list.
    """
    prop = (prop or "").strip().lower().replace("-", "_")
    if prop not in PROPS:
        return "unverified", f"no table check named {prop!r}", []
    params = params or {}

    head, rows = _parse(source)
    if head is None:
        return "unverified", "the deliverable is not a readable CSV table", []

    if prop == "column_case":
        return _column_case(head, rows, params)
    if prop == "count_matches":
        return _count_matches(head, rows, params, stated)
    if prop in ("no_padding", "single_spaces"):
        return _whitespace(head, rows, params, prop)
    if prop == "no_characters":
        return _no_characters(head, rows, params)
    if prop == "column_pattern":
        return _column_pattern(head, rows, params)

    ref_head, ref_rows = _parse(reference)
    if ref_head is None:
        return "unverified", "the source table is not available to compare against", []

    if prop == "rows":
        return _rows(rows, ref_rows)
    if prop == "columns":
        return _columns(head, ref_head)
    if prop == "row_order":
        return _row_order(head, rows, ref_head, ref_rows)
    if prop == "trimmed_copy":
        return _trimmed_copy(head, rows, ref_head, ref_rows, params)
    if prop == "value_map":
        return _value_map(head, rows, ref_head, ref_rows, params)
    return _only_columns_change(head, rows, ref_head, ref_rows, params)


def _aligned(head, rows, ref_head, ref_rows, names):
    """Column indexes for `names` in both tables, or a reason they cannot be
    compared cell for cell."""
    unknown = [c for c in names if c not in head]
    if unknown:
        return None, f"the deliverable has no column named {', '.join(unknown[:3])}"
    missing = [c for c in names if c not in ref_head]
    if missing:
        return None, f"the source has no column named {', '.join(missing[:3])}"
    if len(rows) != len(ref_rows):
        return None, (f"{len(rows)} rows against the source's {len(ref_rows)}, "
                      f"so the cells cannot be compared row for row")
    return [(c, head.index(c), ref_head.index(c)) for c in names], ""


def _cell(row, i):
    return row[i] if i < len(row) else ""


def _trimmed_copy(head, rows, ref_head, ref_rows, params):
    """Every cell of the named columns is the source cell with its surrounding
    whitespace removed — and nothing else about it changed."""
    names = _wanted_columns(params)
    cols, why = _aligned(head, rows, ref_head, ref_rows, names)
    if cols is None:
        return "unverified", why, []
    bad, blame = {}, []
    for name, di, ri in cols:
        hits = [(n, _cell(a, di), _cell(b, ri)) for n, (a, b) in enumerate(zip(rows, ref_rows))
                if _cell(a, di) != _cell(b, ri).strip()]
        if hits:
            bad[name] = hits
            blame.extend(n for n, _, _ in hits[:2])
    if not bad:
        return "satisfied", (f"every {', '.join(names)} value is the source value "
                             f"trimmed, across {len(rows)} rows"), []
    parts = []
    for name, hits in list(bad.items())[:3]:
        n, got, src = hits[0]
        still = sum(1 for _, g, s0 in hits if g == s0 and s0 != s0.strip())
        parts.append(f"{name}: {len(hits)} cell(s) differ from the trimmed source"
                     + (f", {still} still padded" if still else "")
                     + f", e.g. row {n + 1} {got!r} for {src!r}")
    return "violated", "; ".join(parts), blame[:6]


def _value_map(head, rows, ref_head, ref_rows, params):
    """Every row whose source cell is one of `from` now holds `to`.

    Exact on the outcome, loose on the match (see _loose above), and blind to
    everything else the column does — which is the point: a fold is a promise
    about particular cells, and it is kept or it is not whatever the other
    rules did around it.
    """
    name = str(params.get("column") or "").strip()
    raw = params.get("from")
    if isinstance(raw, str):
        raw = [raw]
    wanted = {_loose(v) for v in (raw or []) if str(v).strip()}
    to = str(params.get("to") if params.get("to") is not None else "")
    cols, why = _aligned(head, rows, ref_head, ref_rows, [name])
    if cols is None:
        return "unverified", why, []
    _, di, ri = cols[0]
    matched = [(n, _cell(a, di), _cell(b, ri)) for n, (a, b) in enumerate(zip(rows, ref_rows))
               if _loose(_cell(b, ri)) in wanted]
    if not matched:
        return ("satisfied",
                f"no {name} cell in the source reads {' / '.join(sorted(wanted))}, "
                f"so there is nothing to map", [])
    bad = [(n, got, src) for n, got, src in matched if got != to]
    if not bad:
        return "satisfied", (f"all {len(matched)} {name} cell(s) that read "
                             f"{' / '.join(sorted(wanted))} in the source now read "
                             f"{to!r}"), []
    n, got, src = bad[0]
    return "violated", (f"{len(bad)} of {len(matched)} {name} cell(s) that read "
                        f"{' / '.join(sorted(wanted))} in the source do not read "
                        f"{to!r}: row {n + 1} is {got!r} (source {src!r})"), \
        [n for n, _, _ in bad[:6]]


def _no_characters(head, rows, params):
    """No cell of the named columns contains any of `characters` — the
    deterministic half of "turn every semicolon, underscore and hyphen into a
    space": whatever else the rule did, none of those may be left."""
    names = _wanted_columns(params)
    chars = str(params.get("characters") or "")
    unknown = [c for c in names if c not in head]
    if unknown:
        return "unverified", f"the deliverable has no column named {', '.join(unknown[:3])}", []
    if not chars.strip():
        return "unverified", "no characters given to look for", []
    bad = []
    for name in names:
        i = head.index(name)
        for n, r in enumerate(rows):
            v = _cell(r, i)
            hit = [ch for ch in chars if ch in v]
            if hit:
                bad.append((n, name, v, "".join(hit)))
    if not bad:
        return "satisfied", f"no {', '.join(names)} cell contains any of {chars!r}", []
    shown = ", ".join(f"{c} row {n + 1} {v!r} ({h!r})" for n, c, v, h in bad[:3])
    return "violated", f"{len(bad)} cell(s) still contain one of {chars!r}: {shown}", \
        [n for n, _, _, _ in bad[:6]]


def _column_pattern(head, rows, params):
    """Every non-blank cell of the column matches `pattern` in full — a date
    written as YYYY-MM-DDT00:00:00Z, a code without its decoration. Blank
    cells are skipped unless params say "blank": false."""
    name = str(params.get("column") or "").strip()
    if name not in head:
        return "unverified", f"the deliverable has no column named {name}", []
    try:
        rx = re.compile(str(params.get("pattern") or ""))
    except re.error as e:
        return "unverified", f"the pattern does not compile: {e}", []
    skip_blank = params.get("blank", True) not in (False, "false", "no", 0)
    i = head.index(name)
    bad = []
    for n, r in enumerate(rows):
        v = _cell(r, i)
        if skip_blank and not v.strip():
            continue
        if not rx.fullmatch(v):
            bad.append((n, v))
    if not bad:
        return "satisfied", f"every non-blank {name} value matches {rx.pattern!r}", []
    shown = ", ".join(f"row {n + 1} {v!r}" for n, v in bad[:4])
    return "violated", (f"{len(bad)} {name} value(s) do not match "
                        f"{rx.pattern!r}: {shown}"), [n for n, _ in bad[:6]]


def _whitespace(head, rows, params, prop):
    """no_padding: no cell in the named columns starts or ends with whitespace.
    single_spaces: no cell holds two spaces in a row."""
    names = _wanted_columns(params)
    unknown = [c for c in names if c not in head]
    if unknown:
        return "unverified", f"the deliverable has no column named {', '.join(unknown[:3])}", []
    bad = []
    for name in names:
        i = head.index(name)
        for n, r in enumerate(rows):
            v = _cell(r, i)
            if prop == "no_padding" and v != v.strip():
                bad.append((n, name, v))
            elif prop == "single_spaces" and "  " in v:
                bad.append((n, name, v))
    what = "padded with whitespace" if prop == "no_padding" else "holding a run of spaces"
    if not bad:
        return "satisfied", f"no {', '.join(names)} cell is {what}", []
    shown = ", ".join(f"{c} row {n + 1} {v!r}" for n, c, v in bad[:3])
    return "violated", f"{len(bad)} cell(s) {what}: {shown}", [n for n, _, _ in bad[:6]]


# ------------------------------------------------------------ the properties

def _rows(rows, ref_rows):
    if len(rows) == len(ref_rows):
        return "satisfied", f"{len(rows)} data rows, same as the source", []
    lost = len(ref_rows) - len(rows)
    word = "fewer" if lost > 0 else "more"
    return "violated", (f"{len(rows)} data rows against the source's "
                        f"{len(ref_rows)} — {abs(lost)} {word}"), []


def _columns(head, ref_head):
    if head == ref_head:
        return "satisfied", f"the same {len(head)} columns, in order", []
    missing = [c for c in ref_head if c not in head]
    added = [c for c in head if c not in ref_head]
    if not missing and not added:
        return "violated", "the same columns, but in a different order", []
    parts = []
    if missing:
        parts.append("missing " + ", ".join(missing[:5]))
    if added:
        parts.append("added " + ", ".join(added[:5]))
    return "violated", "; ".join(parts), []


def _key_column(head, ref_head, ref_rows):
    """A column whose values identify a row, for checking order.

    Order is only a question if rows can be told apart. The first column of
    these tables is an id and that is the usual answer, but any column whose
    values are unique across the source will do; without one, order is not a
    claim this can settle and it says so rather than guessing.
    """
    for i, name in enumerate(ref_head):
        if name not in head:
            continue
        values = [r[i] for r in ref_rows if i < len(r)]
        if len(values) == len(ref_rows) and len(set(values)) == len(values) \
                and all(v.strip() for v in values):
            return name, i, head.index(name)
    return None, None, None


def _row_order(head, rows, ref_head, ref_rows):
    name, ri, di = _key_column(head, ref_head, ref_rows)
    if name is None:
        return ("unverified",
                "no column identifies a row, so the order cannot be compared", [])
    got = [r[di] if di < len(r) else "" for r in rows]
    want = [r[ri] if ri < len(r) else "" for r in ref_rows]
    if got == want:
        return "satisfied", f"rows are in the source's order, by {name}", []
    if sorted(got) == sorted(want):
        moved = [i for i, (a, b) in enumerate(zip(got, want)) if a != b]
        return "violated", (f"the same rows in a different order — first "
                            f"difference at row {moved[0] + 1} by {name}"), moved[:6]
    lost = [v for v in want if v not in set(got)]
    dupes = len(got) - len(set(got))
    parts = []
    if lost:
        parts.append(f"{len(lost)} source row(s) missing, e.g. {name} {lost[0]}")
    if dupes:
        parts.append(f"{dupes} duplicated row(s)")
    return "violated", "; ".join(parts) or f"the {name} values do not match the source", []


def _only_columns_change(head, rows, ref_head, ref_rows, params):
    allowed = _wanted_columns(params)
    unknown = [c for c in allowed if c not in head]
    if unknown:
        return ("unverified",
                f"the deliverable has no column named {', '.join(unknown[:3])}", [])
    if len(rows) != len(ref_rows):
        return ("unverified",
                f"{len(rows)} rows against the source's {len(ref_rows)}, so the "
                f"columns cannot be compared row for row", [])

    changed, blame = {}, []
    for name in ref_head:
        if name in allowed or name not in head:
            continue
        ri, di = ref_head.index(name), head.index(name)
        hits = [i for i, (a, b) in enumerate(zip(rows, ref_rows))
                if (a[di] if di < len(a) else "") != (b[ri] if ri < len(b) else "")]
        if hits:
            changed[name] = hits
            blame.extend(hits[:2])
    frozen = len([c for c in ref_head if c not in allowed])
    if not changed:
        return "satisfied", (f"{frozen} column(s) outside "
                             f"{', '.join(allowed) or 'the named ones'} are "
                             f"unchanged, across {len(rows)} rows"), []
    named = "; ".join(f"{c} ({len(h)} cells)" for c, h in list(changed.items())[:4])
    return "violated", f"changed outside the named columns: {named}", blame[:6]


def _column_case(head, rows, params):
    name = _wanted_columns(params)
    name = name[0] if name else ""
    if name not in head:
        return "unverified", f"the deliverable has no column named {name}", []
    want = str(params.get("case") or "upper").strip().lower()
    if want not in ("upper", "lower"):
        return "unverified", f"unknown case {want!r}", []
    i = head.index(name)
    bad = []
    for n, r in enumerate(rows):
        v = r[i] if i < len(r) else ""
        if not v.strip():
            continue
        if (v.upper() if want == "upper" else v.lower()) != v:
            bad.append((n, v))
    if not bad:
        return "satisfied", f"every {name} value is {want}case", []
    shown = ", ".join(repr(v) for _, v in bad[:4])
    return "violated", (f"{len(bad)} {name} value(s) are not {want}case: "
                        f"{shown}"), [n for n, _ in bad[:6]]


def _count_matches(head, rows, params, stated):
    """The number the deliverable REPORTS against the number the table holds.

    The one requirement in the wrangling pair that asks for an answer rather
    than a repair, and the one the judge got wrong in both directions: it
    passed an agent that wrote 14 and failed an agent that wrote 13, on the
    same table, where the count is 13. Counting is not a judgement.

    The reported number is read out of the requirement's own scope — the answer
    file — rather than passed in, because nobody knows it until the agent
    writes it. Any integer in that text will do as a candidate: an answer that
    contains the right number somewhere has stated it, and one that contains
    other numbers and not this one has stated something else.
    """
    name = str(params.get("column") or "").strip()
    if name not in head:
        return "unverified", f"the deliverable has no column named {name}", []
    value = str(params.get("value") or "")
    i = head.index(name)
    hits = [n for n, r in enumerate(rows)
            if (r[i] if i < len(r) else "") == value]

    numbers = [int(n) for n in re.findall(r"(?<![\d.,])(\d{1,6})(?![\d.,])",
                                          stated or "")]
    if not numbers:
        return ("unverified",
                f"{len(hits)} row(s) have {name} == {value!r}, but the answer "
                f"states no number to compare", [])
    if len(hits) in numbers:
        return "satisfied", (f"{len(hits)} row(s) have {name} == {value!r}, "
                             f"and the answer says {len(hits)}"), []
    return "violated", (f"{len(hits)} row(s) have {name} == {value!r}; the "
                        f"answer says {', '.join(str(n) for n in numbers[:4])}"),\
        hits[:6]
