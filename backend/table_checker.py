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
         "column_case", "count_matches")

_NEEDS = {
    "rows": (),
    "columns": (),
    "row_order": (),
    "only_columns_change": ("columns",),
    "column_case": ("column",),
    "count_matches": ("column", "value"),
}


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

    ref_head, ref_rows = _parse(reference)
    if ref_head is None:
        return "unverified", "the source table is not available to compare against", []

    if prop == "rows":
        return _rows(rows, ref_rows)
    if prop == "columns":
        return _columns(head, ref_head)
    if prop == "row_order":
        return _row_order(head, rows, ref_head, ref_rows)
    return _only_columns_change(head, rows, ref_head, ref_rows, params)


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
