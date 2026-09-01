"""Fetch the six benchmark tasks and build them into backend/tasks/.

The tasks are not committed. One of the two source benchmarks ships without a
licence file, so this repository does not redistribute them — it fetches them
from their own publishers and assembles the briefs locally. Run once:

    python setup_tasks.py

Afterwards the picker in the composer offers the six tasks plus a warm-up.
Without them the /api/agent/presets endpoint returns an empty list and the
picker hides itself, so the app works either way.

Sources
    LongWeave       hf.co/datasets/zikaixiao1/LongWeave (MIT)
    AutoDCWorkflow  github.com/LanLi2017/LLM4DC         (no licence file)

Why these six (2026-09-01, replacing the CodeIF / AP-style / small-table set).
Every task was screened over the runs already in backend/runs/ on two numbers,
and the old set passed neither of them together:

  headroom  what a run still has unmet when it stops, over runs nobody touched.
            Zero means the agent finishes the job alone and no interface can
            show a difference — the two 20- and 50-row cleaning tasks and one
            of the CodeIF pair sat at zero.
  code%     the share of requirements a parser decides rather than the judge.
            The cleaning tasks routed 100% of their requirements to the judge —
            "keep every row", "uppercase this column" — because there is no
            table-shaped requirement type, and the AP-style writing pair routed
            over 90%. A verdict a second model call produces is not the claim
            this app makes.

So the set moves to tasks whose constraints a program can settle and whose
artifacts are too big to check by eye:

  1-2  LongWeave CODE_FIXING/4k    a 350-line file to repair against flake8's
                                   own rule categories — the grader is flake8,
                                   not a judge, and its line numbers are the
                                   evidence spans. Fixing one rule breaks
                                   another (E501 against indentation, N802
                                   against F821), which is the regression
                                   pressure the tape exists to show.
  3-4  LongWeave SALES_REPORT/2k   a 300-row transaction table and 30 questions
                                   whose gold answers are exact figures. The
                                   table is attached, so it cannot be counted
                                   by a word limit, and it is far too large to
                                   total by hand: the agent has to compute.
                                   The failure mode is a report whose numbers
                                   are wrong and look right.
  5-6  AutoDCWorkflow menu p13/p28 100 rows x 20-21 columns, gold aligned
                                   row for row, so "leave every other column
                                   exactly as it is" is decidable to the cell.

What this set is NOT: 500- and 1000-row cleaning tasks. Files that size exist
in the AutoDCWorkflow repository under purpose-prepared-datasets/, but they are
not benchmark instances — the gold tables paired with them are 20- to 50-row
samples that do not line up row for row or column for column. The benchmark's
own instances top out at menu's 100 x 21, and that is what is used here rather
than a ground truth this repository would have had to invent.

Only the LongWeave file is large. It is streamed once and stopped as soon as
all four rows are in hand; everything else is a handful of small direct fetches.
"""

import csv
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")
DATA = os.path.join(TASKS, "data")      # attachments — reachable by the agent
GOLD = os.path.join(TASKS, "gold")      # graders' answers — never attached

DCW_BASE = "https://raw.githubusercontent.com/LanLi2017/LLM4DC/main/"
LONGWEAVE_URL = ("https://huggingface.co/datasets/zikaixiao1/LongWeave/"
                 "resolve/main/longweave.jsonl")

# The exact instances, pinned so a re-run reproduces the same six tasks.
# LongWeave has no per-row id, so an instance is pinned by its ordinal within
# its (task, tier) group, counted in file order. CODE_FIXING rows also carry
# metadata.original_file_path, which the manifest records as a cross-check.
LONGWEAVE_PICKS = [
    ("longweave/CODE_FIXING/4k", [0, 1]),
    ("longweave/SALES_REPORT_GENERATION/2k", [0, 1]),
]


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=600):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def dcw_fetch(path):
    return fetch(DCW_BASE + urllib.parse.quote(path)).decode("utf-8", "replace")


def cut(prompt, marker, task):
    """Split a LongWeave prompt at a marker that must appear exactly once.

    The prompts are templated, so a marker that appears twice — or not at all —
    means this build script is reading a row it was not written against, and
    slicing on the wrong offset would ship a brief with half the task missing.
    """
    hits = prompt.count(marker)
    if hits != 1:
        raise RuntimeError(f"{task}: marker {marker!r} appears {hits} times "
                           f"in the upstream prompt, expected exactly 1")
    i = prompt.index(marker)
    return prompt[:i], prompt[i + len(marker):]


def fenced(text, lang, task):
    """The body of the one ```lang fenced block in text."""
    open_fence = f"```{lang}"
    _, rest = cut(text, open_fence, task)
    end = rest.find("```")
    if end < 0:
        raise RuntimeError(f"{task}: the ```{lang} block is never closed")
    return rest[:end].strip("\n")


def md_table_to_csv(block, task):
    """LongWeave labels the sales table ```csv and then writes a markdown pipe
    table inside it. Attaching that verbatim would hand the agent a file no CSV
    reader can open, so it is converted here — once, at build time, where a
    mistake is visible — rather than left for the agent to guess at."""
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set(":- ") and c for c in cells):
            continue                                   # the |:---|---:| rule
        rows.append(cells)
    if len(rows) < 2:
        raise RuntimeError(f"{task}: the sales table came out with {len(rows)} rows")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise RuntimeError(f"{task}: ragged sales table, header has {width} columns")
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue(), len(rows) - 1, width


# ------------------------------------------------------------ 1-2  CODE_FIXING

CODEFIX_BRIEF = """{head}

The file is attached as {stem}.py — {lines} lines. The attachment is read-only,
so the first move is to copy it into the workspace and repair the copy; what is
not a violation must survive that copy unchanged.

**Instructions:**
- **Fix Syntax Errors:** Ensure the code is valid Python.
- **Correct Style Violations:** Fix all style issues under the categories above.
- **Preserve Functionality:** Keep the original behavior, **keep the number of
  functions unchanged**, prioritize runnability.

Write the corrected file to solution.py. The file is the deliverable — the
whole of it is graded, so nothing belongs in the chat that belongs in the file.
"""


def build_codefix(meta, briefs, picked):
    rows = picked["longweave/CODE_FIXING/4k"]
    for i, (ordinal, row) in enumerate(rows):
        n = 1 + i
        stem = f"broken_{n}"
        task = f"codefix_{n}"
        head, _ = cut(row["prompt"], "**Input Python Code:**", task)
        code = row["metadata"]["original_code"]
        # The prompt embeds the same file between markers. If the two ever
        # disagree the metadata is the one the benchmark scores against, so it
        # wins — but a disagreement means the row changed shape and the brief
        # would describe a file the agent never sees.
        if code.strip() not in row["prompt"]:
            raise RuntimeError(f"{task}: metadata.original_code is not the "
                               f"file embedded in the prompt")
        with open(os.path.join(DATA, stem + ".py"), "w", encoding="utf-8") as fh:
            fh.write(code if code.endswith("\n") else code + "\n")

        lines = code.count("\n") + 1
        briefs[task] = CODEFIX_BRIEF.format(head=head.strip(), stem=stem,
                                            lines=lines)
        meta.append(dict(
            id=task, n=n, domain="Code repair", benchmark="LongWeave",
            attachments=[stem + ".py"],
            label=f"CODE_FIXING {n} — {lines}-line file, flake8 E/W·F·B·N·SIM·C4",
            dropped=[],
            source=(f"LongWeave CODE_FIXING/4k (arXiv:2510.24345), MIT, "
                    f"instance {ordinal} in file order, upstream "
                    f"{row['metadata'].get('original_file_path')}"),
            grader=("flake8 (or ruff) over the six categories the brief names, "
                    "plus `python -c 'import ast; ast.parse(...)'` for "
                    "runnability and a function count against the attachment. "
                    "No judge is involved in scoring this task."),
            note=("Replaces the CodeIF pair. The artifact is 8x larger, the "
                  "constraints are flake8's own rather than a house style "
                  "written here, and the rules interact: E501 against "
                  "indentation, N802 against F821, B006 against behaviour. "
                  "Not yet measured — screen it before the study."),
            tested=False))
        log(f"            task {n}: {stem}.py, {lines} lines, "
            f"{len(code):,} chars")


# --------------------------------------------------------- 3-4  SALES_REPORT

SALES_BRIEF = """{head}

The data is attached as {stem}.csv — {rows} transactions, {cols} columns. It is
read-only and far too large to total by hand: work it with a program, not from
memory, and quote figures you have actually computed.

{tail}

Write the report to report.md. For the target length, treat "around 2048 words"
as between 2,000 and 2,100 words.
"""


def build_sales(meta, briefs, picked):
    rows = picked["longweave/SALES_REPORT_GENERATION/2k"]
    for i, (ordinal, row) in enumerate(rows):
        n = 3 + i
        stem = f"sales_{n}"
        task = f"sales_{n}"
        head, rest = cut(row["prompt"], "**Input Sales Data (CSV Format):**", task)
        block = fenced(rest, "csv", task)
        table, nrows, ncols = md_table_to_csv(block, task)
        _, tail = cut(rest, "```\n\n**Analysis Structure Guidance:**", task)
        tail = "**Analysis Structure Guidance:**" + tail
        # The prompt signs off by inviting the model to start writing. A brief
        # that ends mid-invitation reads as a chat turn, and the extractor has
        # pulled a requirement out of it before now.
        tail = tail.split("You may now begin your analysis")[0].rstrip()

        with open(os.path.join(DATA, stem + ".csv"), "w", encoding="utf-8") as fh:
            fh.write(table)
        qa = row["metadata"]["qa_pairs"]
        with open(os.path.join(GOLD, stem + ".json"), "w", encoding="utf-8") as fh:
            json.dump(qa, fh, indent=2, ensure_ascii=False)

        briefs[task] = SALES_BRIEF.format(head=head.strip(), stem=stem,
                                          rows=nrows, cols=ncols, tail=tail)
        meta.append(dict(
            id=task, n=n, domain="Data analysis", benchmark="LongWeave",
            attachments=[stem + ".csv"],
            label=f"SALES_REPORT {n-2} — {nrows}-row table, {len(qa)} figures to get right",
            dropped=[],
            source=(f"LongWeave SALES_REPORT_GENERATION/2k (arXiv:2510.24345), "
                    f"MIT, instance {ordinal} in file order, {len(qa)} gold "
                    f"question/answer pairs"),
            gold=f"gold/{stem}.json",
            grader=(f"the {len(qa)} gold answers in gold/{stem}.json, each one "
                    f"an exact figure — numeric match against what the report "
                    f"states for that question, plus the word budget."),
            note=("The flagship. Every requirement is a number that is either "
                  "right or wrong, so the verdicts are code's to give, and the "
                  "baseline's failure is a report that reads perfectly and is "
                  "wrong. The word budget and the 30 answers compete, which is "
                  "where the regressions come from. Not yet measured — screen "
                  "it before the study."),
            tested=False))
        log(f"            task {n}: {stem}.csv, {nrows} rows x {ncols} cols, "
            f"{len(qa)} gold answers")


# ------------------------------------------------------------- 5-6  the tables

# AutoDCWorkflow's unit is (dirty table, purpose) with a gold cleaned table.
# Edit policy, unchanged from the 2026-08-27 revision: benchmark requirements
# may be DELETED (reason recorded) or RESTATED, never added. The cleaning
# instructions below verbalize each instance's actual dirty->clean diff, read
# off the gold table cell by cell, and the deliverables are the benchmark's
# own: the cleaned table plus the answer to the purpose question.
#
# menu is the family used because it is the largest instance the benchmark
# actually ships with a row-aligned gold table — a hundred rows across twenty
# or twenty-one columns, of which two to five are dirty. The fifteen-odd
# untouched columns over a hundred rows are the point: "leave every other
# column exactly as it is" is a promise nobody can verify by reading, and a
# differ settles it in a millisecond.
#
# The two purposes deliberately sit on DIFFERENT dirty tables. menu ships
# thirty purposes over ten distinct tables, and thirteen of those purposes
# share one table — p20 and p28 among them. A pair drawn from that group would
# hand the same hundred rows to a participant twice, and the second sitting
# would be reading a table it had already learned. p13 and p28 are the two
# richest instances that do not share a table.
MENU = [
    dict(
        n=5, pid=13, stem="menu_p13",
        label="AutoDCWorkflow 1 — menu currencies, 100 x 20, 2 dirty columns",
        intro="The attached table menu_p13.csv holds a hundred historical menu "
              "records across twenty columns; the two the question depends on — "
              "the currency each menu prices in and the sponsor who issued it — "
              "are dirty.",
        clean="""In cleaned.csv's currency column, trim the padded whitespace,
put every value in uppercase, and fold the misspelled dollar variants onto one
name: dolar, dolars, dolr, DOLARS$ and dolars$, in any capitalization, all mean
DOLLARS. The other currencies are only trimmed and uppercased, so Italian Lire
becomes ITALIAN LIRE and Deutsche Marks becomes DEUTSCHE MARKS. In
cleaned.csv's sponsor column, put every name in uppercase, and empty the two
bracketed placeholders that are not sponsors: [Restaurant name and/or location
not given] and [Restaurant And/Or Location Not Given]. Two names are corrected
as they are folded: Adams' Restaurant and Adam's Restaurant both become ADAM'S
RESTAURANT, and NORDDEUTSCHERRR LLOYD BREMEN becomes NORDDEUTSCHER LLOYD
BREMEN. In cleaned.csv, leave every other column exactly as it is.""",
        dropped=[]),
    dict(
        n=6, pid=28, stem="menu_p28",
        label="AutoDCWorkflow 2 — menu venues, 100 x 21, 5 dirty columns",
        intro="The attached table menu_p28.csv holds a hundred historical menu "
              "records across twenty-one columns; the venue codes and the page "
              "counts the question depends on are dirty, and three more columns "
              "carry padding.",
        clean="""In cleaned.csv's venue column, write each code out in full and
drop the semicolon and .? decoration: COM and COMMERCIAL.? are COMMERCIAL,
SOC; and SOC;.? are SOCIAL, SOCIAL.? is SOCIAL, POL; is POLITICAL, PROF; and
PROF;.? are PROFESSIONAL, GOVT;.? and GOV; are GOVERNMENT, PATR; and PATR;.?
are PATRONAGE, EDUC; is EDUCATION, RESTAURANT.? is RESTAURANT, and SS; FOR is
SS. In cleaned.csv's page_count column, drop the trailing " pages" and keep
the number written exactly as it stands, so 2.0 pages becomes 2.0. In
cleaned.csv's event, occasion and notes columns, trim the padded whitespace
and change nothing else. In cleaned.csv, leave every other column exactly as
it is.""",
        # The gold table gives this instance's blank first header cell the name
        # "Column". That is a header edit no purpose asks for and no answer
        # depends on, so it is dropped and the brief keeps the header as it
        # arrives. p13 has no such cell, which is why this is recorded per
        # instance rather than against the pair.
        dropped=[dict(
            step="rename the unnamed first column to \"Column\"",
            reason="the gold table names the dirty table's blank first header "
                   "cell \"Column\"; nothing in the purpose depends on it and "
                   "the requirement would fail a correct table over a header "
                   "nobody asked about")]),
]

DCW_BRIEF = """{intro} The question, quoted from the benchmark: "{purpose}"

{clean}

Write the cleaned table to cleaned.csv, keeping the header, every row, every
column, and the original row order — the repairs above are the only changes.
Then write the answer to the quoted question to answer.md, worked from
cleaned.csv.

The data is attached as {stem}.csv."""


def build_menu(meta, briefs, purposes):
    for t in MENU:
        purpose = purposes.get(t["pid"])
        if not purpose:
            raise RuntimeError(f"AutoDCWorkflow purpose {t['pid']} not found upstream")
        table = dcw_fetch(f"datasets/menu_datasets/{t['stem']}.csv")
        gold = dcw_fetch(f"datasets/menu_datasets/clean_tables/"
                         f"menu_sample_p{t['pid']}.csv")
        rows = list(csv.reader(io.StringIO(table)))
        grows = list(csv.reader(io.StringIO(gold)))
        if len(rows) != len(grows) or len(rows[0]) != len(grows[0]):
            raise RuntimeError(
                f"menu p{t['pid']}: dirty is {len(rows)-1}x{len(rows[0])} but "
                f"gold is {len(grows)-1}x{len(grows[0])}; the diff the brief "
                f"restates is only defined when they line up")
        with open(os.path.join(DATA, t["stem"] + ".csv"), "w", encoding="utf-8") as fh:
            fh.write(table)
        with open(os.path.join(GOLD, t["stem"] + ".csv"), "w", encoding="utf-8") as fh:
            fh.write(gold)

        dirty = sum(1 for a, b in zip(rows[1:], grows[1:])
                    for x, y in zip(a, b) if x != y)
        n = t["n"]
        briefs[f"menu_{n}"] = DCW_BRIEF.format(intro=t["intro"], purpose=purpose,
                                               clean=t["clean"], stem=t["stem"])
        meta.append(dict(
            id=f"menu_{n}", n=n, domain="Data wrangling", benchmark="AutoDCWorkflow",
            attachments=[t["stem"] + ".csv"],
            label=t["label"], dropped=t["dropped"],
            source=(f"AutoDCWorkflow (arXiv:2412.06724) purpose {t['pid']}, "
                    f"{len(rows)-1} x {len(rows[0])} dirty table, gold clean "
                    f"table row-aligned in LanLi2017/LLM4DC"),
            gold=f"gold/{t['stem']}.csv",
            grader=(f"cell-for-cell diff against gold/{t['stem']}.csv "
                    f"({dirty} cells differ from the dirty table), plus the "
                    f"answer to the purpose question."),
            note=(f"Replaces the 20- and 50-row pair, which converged unattended "
                  f"in every logged run. {dirty} cells change out of "
                  f"{(len(rows)-1) * len(rows[0]):,}; the other "
                  f"{(len(rows)-1) * len(rows[0]) - dirty:,} are the promise "
                  f"the freeze exists to keep. Not yet measured — screen it "
                  f"before the study."),
            tested=False))
        log(f"            task {n}: {t['stem']}.csv, {len(rows)-1} rows x "
            f"{len(rows[0])} cols, {dirty} cells dirty, purpose {t['pid']}")


# ------------------------------------------------------------------ warm-up

# Kept from the old set, and kept only for this: it converges unattended in one
# to two minutes, every logged run, which makes it useless as a study task and
# ideal as the one a participant does first. They watch a chip go green and an
# evidence span light up without losing anything if it goes wrong. n=0 so it
# sorts ahead of the six and never reads as a seventh condition.
WARMUP = dict(
    n=0, pid=148, stem="hos_data_p148",
    table="datasets/hospital/hos_data_p148.csv",
    label="Warm-up — hospital registry, 20 rows, 2 dirty columns",
    intro="The attached table hos_data_p148.csv lists twenty hospital records, "
          "and the two columns the question depends on are dirty.",
    clean="""In cleaned.csv's CountyName column, trim the
padded whitespace, put every value in uppercase, and fix the two corrupted
names: CHILTuN is CHILTON and COmFEE is COFFEE. In cleaned.csv's
EmergencyService column, trim and uppercase every value, then repair the
corrupted entries so the column holds only YES or NO — YsS, YEz, MES and rES
all mean YES. In cleaned.csv, leave every other column exactly as it is.""")


def build_warmup(meta, briefs, purposes):
    t = WARMUP
    purpose = purposes.get(t["pid"])
    if not purpose:
        raise RuntimeError(f"AutoDCWorkflow purpose {t['pid']} not found upstream")
    table = dcw_fetch(t["table"])
    with open(os.path.join(DATA, t["stem"] + ".csv"), "w", encoding="utf-8") as fh:
        fh.write(table)
    briefs["warmup_0"] = DCW_BRIEF.format(intro=t["intro"], purpose=purpose,
                                          clean=t["clean"], stem=t["stem"])
    meta.append(dict(
        id="warmup_0", n=0, domain="Warm-up", benchmark="AutoDCWorkflow",
        attachments=[t["stem"] + ".csv"], label=t["label"], dropped=[],
        role="warm-up",
        source=f"AutoDCWorkflow (arXiv:2412.06724) purpose {t['pid']}",
        grader="not graded — this one is the tutorial.",
        note=("Not a study task. Converges unattended in two turns in every "
              "logged run, which is exactly what a first sitting should do: "
              "the participant sees a chip turn green and an evidence span "
              "light up before anything is at stake."),
        tested=True))
    log(f"            warm-up: {t['stem']}.csv, purpose {t['pid']}")


# ----------------------------------------------------------- the long stream

def stream_longweave():
    """One pass over the 224 MB file, stopping as soon as every picked row is
    in hand. The rows are grouped by task and the sales family sits late in the
    file, so most of it does get read — but only the lines whose task string
    matches are ever parsed."""
    want = {task: set(ix) for task, ix in LONGWEAVE_PICKS}
    seen = {task: -1 for task, _ in LONGWEAVE_PICKS}
    picked = {task: [] for task, _ in LONGWEAVE_PICKS}
    needed = sum(len(ix) for _, ix in want.items())

    log(f"LongWeave   streaming for {needed} rows across "
        f"{len(LONGWEAVE_PICKS)} tasks…")
    with urllib.request.urlopen(LONGWEAVE_URL, timeout=1800) as r:
        read = 0
        for line in r:
            read += len(line)
            for task in want:
                if f'"{task}"'.encode() not in line:
                    continue
                row = json.loads(line)
                if row.get("task") != task:
                    continue
                seen[task] += 1
                if seen[task] in want[task]:
                    picked[task].append((seen[task], row))
                    log(f"            {task} #{seen[task]} "
                        f"after {read/1e6:.0f} MB")
                break
            if sum(len(v) for v in picked.values()) == needed:
                break

    for task, ix in want.items():
        if len(picked[task]) != len(ix):
            raise RuntimeError(f"{task}: found {len(picked[task])} of "
                               f"{len(ix)} picked rows upstream")
    return picked


def main():
    os.makedirs(TASKS, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(GOLD, exist_ok=True)
    meta, briefs = [], {}
    try:
        log("AutoDCWork  fetching purposes and tables…")
        purposes = {int(r["ID"]): r["Purposes"].strip() for r in
                    csv.DictReader(io.StringIO(dcw_fetch("purposes/all_purposes.csv")))}
        build_warmup(meta, briefs, purposes)
        build_menu(meta, briefs, purposes)
        picked = stream_longweave()
        build_codefix(meta, briefs, picked)
        build_sales(meta, briefs, picked)
    except Exception as e:                                   # noqa: BLE001
        print(f"\nfailed: {type(e).__name__}: {e}", file=sys.stderr)
        print("Nothing was written. The app still runs; the picker stays hidden.",
              file=sys.stderr)
        return 1

    meta.sort(key=lambda m: m["n"])
    for m in meta:
        text = briefs[m["id"]]
        m["chars"], m["words"] = len(text), len(text.split())
        with open(os.path.join(TASKS, m["id"] + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(TASKS, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    log(f"\nwrote {len(meta)} tasks to {os.path.relpath(TASKS, os.getcwd())}/")
    for m in meta:
        log(f"  Task {m['n']}  {m['benchmark']:14} {m['words']:>5}w  {m['label']}")
    log("\nGold answers are in tasks/gold/ and are never attached to a session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
