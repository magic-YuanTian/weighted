"""Fetch the six benchmark tasks and build them into backend/tasks/.

The tasks are not committed. Two of the three source benchmarks ship without a
licence file, so this repository does not redistribute them — it fetches them
from their own publishers and assembles the briefs locally. Run once:

    python setup_tasks.py

Afterwards the picker in the composer offers the six tasks. Without them the
/api/agent/presets endpoint returns an empty list and the picker hides itself,
so the app works either way.

Sources
    CodeIF          github.com/lin-rany/codeIF           (no licence file)
    LongWeave       hf.co/datasets/zikaixiao1/LongWeave  (MIT)
    AutoDCWorkflow  github.com/LanLi2017/LLM4DC          (no licence file)

Why these six (2026-09-04). Three domains, two instances each, and two rules
the set has to obey. Both are measured, not argued:

  requirements  under twenty, always. The rail holds one chip per requirement,
                and a list nobody can read is a screen nobody uses. Each count
                below is the median of three runs of the real extractor over
                the brief this script writes.
  wrong first   the agent must not get the task right on its own. Measured by
                running the real agent over the brief with nobody steering it
                until it stops, and then checking the DELIVERABLE against
                ground truth — not against the app's own verdicts, which is
                the whole point. The programs are executed against known
                answers, the tables compared with the benchmark's gold table
                cell by cell, the biographies counted and read. If what the
                agent leaves behind is correct, the task is finished and there
                is nothing for a participant to steer, whatever the chips say.

                This is the third anchor this file has used and the first one
                that cannot be fooled. Measuring at the first draft credited
                every task with headroom, because a first draft is a draft.
                Measuring at the stop credited whatever the judge happened to
                say, and the judge held four correct deliverables red and
                passed two wrong ones.

What the unattended runs actually produced, and how each was caught:

  1  CodeIF 358         14 reqs. Wrong twice. One run returned 3 for
                        [-5, 1, 0] with k = 4, where the longest subarray
                        whose sum is not divisible by 4 is [1, 0] and the
                        answer is 2 — one term out in the arithmetic that
                        drops the prefix. The other run left a file that does
                        not parse: an insert dropped a line into the middle of
                        a match block. Both caught by running it.
  2  CodeIF 1087        16 reqs. Wrong twice. num_tilings returns 12, 28 and
                        65 for N = 4, 5, 6, where the answers are 11, 24 and
                        53; an earlier run returned 1, 5, 21, 89, which is a
                        different sequence again. Caught by running it against
                        the known counts.
  3  menu p26           11 reqs. Wrong in two runs of three, and wrong in
                        exactly the same nine cells both times. The date
                        column is written four ways — year first with hyphens
                        or dots, month first with hyphens or slashes — and has
                        to come out as YYYY-MM-DDT00:00:00Z. In nine of the
                        hundred rows the day sits where the month usually
                        does, so 1949-23-12 is December the twenty-third; the
                        agent parses by position and leaves all nine as they
                        are. That the failures repeat cell for cell is what
                        distinguishes this from menu p13, which it replaced:
                        p13 was repaired correctly in two of five runs and its
                        three failures had nothing in common — a stray column
                        once, a missing answer.md twice. A task with a random
                        outcome is not a hard task.
  4  menu p18           17 reqs. Wrong. Row 24's sponsor is the placeholder
                        [Restaurant ;name ;and/or ;location ;not ;given],
                        which the brief empties; the agent uppercased it
                        instead. One cell in 2,000. Caught by the gold table
                        only — no property covers a value-level fold, so the
                        run's own verdicts missed it.
  5  KG_TO_TEXT #1       7 reqs. Wrong twice, 2,372 and 2,393 words against a
                        2,000-2,100 budget. 119 triples in the same budget as
                        the 81 the old task 5 carried, which is why it
                        overruns. Counted, not judged.
  6  KG_TO_TEXT #6       6 reqs. Wrong in three runs of three, and the word
                        count alone catches two of them: 1,881 and 3,661
                        against a 2,000-2,100 budget, the second nearly double
                        the ceiling at 47 paragraphs. The third landed in
                        budget at 2,013 words and failed differently — 31
                        short paragraphs that restate each other, one naming
                        The Single Device's authored and publication years and
                        the next naming them again.

What this set gained over the one it replaces. The old pair 1-2 was LongWeave
CODE_FIXING/4k, and it was dropped for being unreadable: a 352-line broken
file is not something a participant can hold in their head, and the tiers do
not help — CODE_FIXING/1k is a median of 223 lines and its shortest instance
is 169, because LongWeave's tiers size the OUTPUT, not the input. CodeIF puts
the difficulty in a list of constraints instead of in a wall of code, and its
artifact is thirty lines.

It also closes the gap the previous revision of this file called the highest-
value change left. Every task in the old set scored 0% on code% — the share of
requirements a parser decides rather than the judge. These two do not: 12 of
652's 18 requirements and 9 of 1087's 16 are code properties the checker
answers by parsing the file, 67% and 53%. Tasks 3-6 are still 0% and 17%.

What the screen ruled out, and it ruled out a lot. Sixteen CodeIF instances
went through the extractor and fourteen were run against the real agent, with
their output executed:

  solved outright   630, 652, 682, 683, 687, 744, 851, 933, 1087's partner
                    candidates 957 and 975 — every one produced code that
                    passes an independent oracle. 652 was task 1 for a day: the
                    prime-counting thread came back correct in three runs out
                    of three, which is what took it out. A task the agent
                    finishes is a task this study cannot use, however many
                    constraints its brief carries.
  891               Wrong, but barely: it returns -0.16952 for the minimum of
                    GeLU where the answer is -0.16997, having guessed
                    -sqrt(2/pi) for a stationary point that sits at -0.7518.
                    A fourth-decimal slip is not something a participant can
                    see, so 358's plainly wrong integer wins.
  367/738, 745,     30, 28, 21, 33 and 25 requirements. Over the cap.
  1034, 1075
  menu p4, p9, p12  Four tables the agent repaired PERFECTLY — cell for cell
  and p14           against gold, with the right answer to the purpose
                    question. p9 is one mechanical rule over 82 cells, p4 is
                    five folds over 10, p12 adds three replacements and two
                    spelling corrections, p14 has 119 changed cells and a trap
                    (it keeps the misspelling that p13 and p18 correct) — and
                    all four were solved in three to six steps. Width of repair
                    alone does not do it.
  menu p13          Two of five unattended runs repaired it correctly. The
                    three that failed failed differently — three stray cells
                    in a frozen column once, a missing answer.md twice — which
                    is a task with a random outcome rather than a hard one.
  the small tables  Every AutoDCWorkflow instance at 20-50 rows — hospital
                    p148, flights p117, ppp p74/p78/p79 — finished with
                    nothing unmet in every logged run. Six instances, six
                    zeros.
  KG_TO_TEXT #0     Task 5 for a day. Its biography came in at 2,009 words with
                    every claim traceable to the attachment; the only thing
                    still red was the judge misreading a faithful rendering of
                    a triple. 81 triples in a 2,000-word budget is not enough
                    to strain.
  AP_STYLE          46 and 74 requirements.

One instruction genre used to be avoided here, and is no longer. A CodeIF
brief that says "variable x should not be a global variable" or "should not be
a constant" states a ban the checker has no property for — there is no negated
module_level and no negated assigned_once — and the extractor used to answer it
with the positive property, so the checker reported correct code as violating
it. It appeared in four of the eleven candidates run against the agent (630,
687, 808, 933) and burned sixteen steps in one of them.

That is fixed: extract._grounded now sends a negated presence-property to the
judge instead of letting the parser answer the opposite question, and the same
guard catches "define an interface named X", which has no Python construct to
look for and was failing a class of that exact name. The caps are untouched —
"no more than three functions" is negated language and max_functions is the
right property for it. Two checker over-readings went with it: initializes now
accepts a literal that spells the argument, and an argument LIST, so
AnalyzeDwa("dwa", "info") is no longer "built from AnalyzeDwa, but not from
dwa". The cost is real and is paid in code%: on the CodeIF pair those guards
move two or three requirements per brief off the parser and onto the judge.

So the pool the next screen can draw from is wider than the one this set came
out of.

What the audit found that the counts hid, and it is not about the tasks. Where
a red chip could be settled against the benchmark's own answer, the judge was
wrong about the tables far more often than it was right. Both wrangling runs
ended with a cleaned.csv IDENTICAL to the gold table — every cell, every row,
the original order — and both still showed red:

  menu p13   stopped with 3 red on a perfect table and an answer.md naming all
             31 sponsors the gold table says accept dollars. All three are
             false, and one of them, "leave every other column exactly as it
             is", is red on a file in which no other column was touched.
  menu p4    stopped with 4 red on a perfect table, all four false — "only 99
             data rows", "duplicated records", "duplicated blocks", none of
             which the file contains. Meanwhile answer.md says 14 rows have
             event DINNER where the table has 13, and the requirement that
             asks for exactly that number is GREEN.

The biographies show the milder form of the same thing: kg_6 ended holding a
chip that says the text "states an unsupported event date" for September 21,
1820, when the attachment gives that event a day of 21, a month of 9 and a
year of 1820. The counted chip beside it — 1,928 words against a 2,000 floor —
was right.

So on the tables the verdicts are wrong in both directions at once: correct
work held red, and the one wrong answer in the run let through. The mechanism
is not truncation — verifier.py deliberately shows the judge the whole
document and the whole attachment — it is that a 100-row, 20-column CSV read
twice over at the study's pinned reasoning effort is not something this judge
can hold. Requirements about a table are the ones to distrust; the counted
ones (a word budget) and the parsed ones (a function cap) held up every time
they were checked.

The rest of the shape. Tasks 1-2 have three unattended runs each, task 3 two,
tasks 4-6 one, so most of the first-attempt numbers are a single observation.
Extraction is stochastic and moves them more than anything else does: 652
extracted to 18, 19 and 18 requirements on three consecutive runs, and the run
that produced 19 ended with twelve violated while the run that produced 18
closed every one. Read the first-attempt column as "not zero, and for a reason
that survives checking" rather than as a magnitude.

Only the LongWeave file is large. It is streamed once and stopped as soon as
both picked rows are in hand; with CODE_FIXING gone that is 30 MB of 224.
"""

import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")
DATA = os.path.join(TASKS, "data")      # attachments — reachable by the agent
GOLD = os.path.join(TASKS, "gold")      # graders' answers — never attached

DCW_BASE = "https://raw.githubusercontent.com/LanLi2017/LLM4DC/main/"
CODEIF_URL = ("https://raw.githubusercontent.com/lin-rany/codeIF/master/"
              "data/question/final_release_1200.jsonl")
LONGWEAVE_URL = ("https://huggingface.co/datasets/zikaixiao1/LongWeave/"
                 "resolve/main/longweave.jsonl")

# The exact instances, pinned so a re-run reproduces the same six tasks.
# CodeIF carries a question_id, so its pair is pinned by that. LongWeave has no
# per-row id, so an instance is pinned by its ordinal within its (task, tier)
# group, counted in file order.
#
# The KG pair used to be two 81-triple rows, matched to each other. It is now
# 119 triples and 81, deliberately unmatched: the 81-triple row that used to be
# task 5 wrote a biography that was correct — right length, claims all
# traceable — and a task the agent finishes is a task the study cannot use.
# 119 facts in the same 2,000-word budget is the difference. Its first
# unattended run came in at 2,372 words.
CODEIF_IDS = [358, 1087]
KG_TASK = "longweave/KG_TO_TEXT/2k"
LONGWEAVE_PICKS = [
    (KG_TASK, [1, 6]),
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


# --------------------------------------------------------------- 1-2  CodeIF

# CodeIF is a question plus a generated list of instructions the answer has to
# obey, and the instructions are the point: they are grafted onto the question
# rather than drawn from it, so an answer that solves the problem still fails
# the list. That is what makes the pair steerable at a readable size — the
# artifact is thirty lines, not three hundred and fifty.
#
# Both instances are from the `hard` split, and both were picked by measuring,
# not reading. Every candidate had the real extractor run over its brief three
# times (the requirement count below is the median) and the real agent run over
# it unattended; what the docstring quotes is what those runs did.
#
# One instruction genre is deliberately avoided in this pair: a ban on a
# *variable property*. "Variable x should not be a global variable" and
# "variable x should not be a constant" have no property in the checker's list
# — there is no negated module_level, no negated assigned_once — and the
# extractor answers them with the positive property instead, so the checker
# reports the correct file as violating them. Four of the six candidates
# screened before this pair carried one, and in each the chip stayed red on
# code that obeyed the brief. Both instances here state their bans as
# constructs (`for`, `if`, `list`, `global`) and as library names, which the
# checker does have, and neither run produced an inverted verdict.
# One restatement, recorded the way the KG builder records its own. CodeIF 652
# ends its question with "Please write your code inside a markdown ```python```
# wrapper" — a format instruction aimed at a chat answer. Here the deliverable
# is a file, and the sentence arrives as a requirement: one screening run
# extracted "provide the solution inside a markdown python fence" and then had
# to grade it against solution.py. Dropped whole rather than rewritten, and the
# manifest says so.
CODEIF_DROP_SENTENCE = {
    652: ("Please write your code inside a markdown ```python``` wrapper.",
          "the deliverable is solution.py, so a markdown fence is a format the "
          "file cannot be in — the sentence would be graded against a file it "
          "does not describe"),
}

CODEIF_BRIEF = """{question}

Requirements:
{instructions}

Write the solution to solution.py. The file is the deliverable — the whole of
it is graded, so nothing belongs in the chat that belongs in the file.
"""

# What each instance is, for the picker's label and for the note that records
# why it survived the screen. The number the set is chosen on is how many
# requirements were violated the moment the agent first wrote solution.py —
# and, separately, how many of those were still violated after the draft was
# checked by hand against the brief. The second number is the one that counts.
CODEIF = {
    358: dict(
        n=1, label="CodeIF 1 — longest subarray not divisible by k, 10 constraints",
        requirements=14,
        note=("Finds the longest subarray whose sum is not divisible by k, "
              "with no for-loop, no list, at most one function, and a match "
              "statement standing in for the switch the brief asks for. It is "
              "here because the agent gets it WRONG: its unattended answer "
              "reports length 3 for [-5, 1, 0] with k=4, where the longest "
              "such subarray is [1, 0] and the answer is 2. The bug is one "
              "term — it drops a prefix by n - first_nonzero where the "
              "arithmetic needs n - first_nonzero - 1 — which is what a "
              "logic error in this benchmark looks like: right on most "
              "inputs, wrong on the ones that matter. It replaces CodeIF 652, "
              "whose prime-counting thread the agent got right in three runs "
              "out of three."),
        tested=True),
    1087: dict(
        n=2, label="CodeIF 2 — 2xN board tilings, 10 constraints",
        requirements=16,
        note=("Counts the tilings of a 2xN board with dominos and L-trominos "
              "under a no-if-statement rule and a pile of grafted naming "
              "constraints. It is here because the agent gets the count "
              "WRONG and keeps getting it wrong: one run returned 12, 28 and "
              "65 for N = 4, 5, 6 against answers of 11, 24 and 53, another "
              "returned 1, 5, 21, 89, which is not even the same sequence. "
              "The recurrence is easy to state and easy to mis-state, which "
              "is the whole of the difficulty — the constraints are wrapping. "
              "Its catch is that nothing in the app sees this: the run stops "
              "green, and the requirement that should notice is judged."),
        tested=True),
}


def build_codeif(meta, briefs):
    log("CodeIF      fetching 1,200 questions…")
    rows = {}
    for line in fetch(CODEIF_URL).decode("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["question_id"]] = r

    for qid in CODEIF_IDS:
        row = rows.get(qid)
        if row is None:
            raise RuntimeError(f"CodeIF question_id {qid} not found upstream")
        spec = CODEIF[qid]
        n = spec["n"]
        task = f"codeif_{n}"
        ins = row["instruction_list"]
        # Nothing is dropped from either list — the earlier pair needed three
        # instructions removed as unsatisfiable, and these two were screened
        # partly on not needing that. If a future upstream revision changes a
        # list, this count is what says so.
        if len(ins) != len(set(x["instruction"] for x in ins)):
            raise RuntimeError(f"{task}: the upstream instruction list repeats "
                               f"itself, which is not the row this was written "
                               f"against")
        question = row["question"].strip()
        dropped = []
        if qid in CODEIF_DROP_SENTENCE:
            sentence, reason = CODEIF_DROP_SENTENCE[qid]
            if question.count(sentence) != 1:
                raise RuntimeError(f"{task}: the sentence to drop appears "
                                   f"{question.count(sentence)} times upstream, "
                                   f"expected exactly 1")
            question = question.replace(sentence, "").strip()
            dropped.append(dict(step=f"dropped {sentence!r}", reason=reason))
            log(f"            dropped from {qid}: {reason}")

        briefs[task] = CODEIF_BRIEF.format(
            question=question,
            instructions="\n".join(f"{i}. {x['instruction']}"
                                    for i, x in enumerate(ins, start=1)))
        meta.append(dict(
            id=task, n=n, domain="Code generation", benchmark="CodeIF",
            attachments=[], label=spec["label"], dropped=dropped,
            source=(f"CodeIF (ACL 2025 Industry) question_id {qid}, "
                    f"{row['meta_info']['item_set']} split, "
                    f"{row['meta_info']['programming_language']}, "
                    f"{len(ins)} instructions"),
            grader=("the checker's code properties for every constraint that "
                    "names an identifier, a construct or a cap — which is most "
                    "of the list — and the judge for what the code has to "
                    "mean. CodeIF ships no test cases; upstream it is scored "
                    "by a model."),
            requirements=spec["requirements"],
            note=spec["note"],
            tested=spec["tested"]))
        log(f"            task {n}: question_id {qid}, {len(ins)} instructions, "
            f"{len(row['question'].split())} words of question")


# ------------------------------------------------------------- 3-4  the tables

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
# share one table. p13 sits on a table of its own, so any partner clears that
# bar; what picks the partner out of the rest is the requirement count, and
# that is set by how many rules the cleaning paragraph has to state, not by how
# many cells change. p28 was the partner first and measured at 25 — five dirty
# columns is five paragraphs of repair. p18 replaced it at a recorded 17 and
# then measured 21, 24 and 24 on three fresh runs of the extractor, so it went
# the same way: its repair states about ten rules across two columns.
#
# p15 was tried next and measured 23, 23 and 16: two dirty columns is still
# seven rules, and the extractor splits a named list of misspellings into one
# requirement per spelling as readily as into one. What decides the count is
# how many things the paragraph names, so the partner has to be the instance
# that names fewest.
#
# p4 is that instance. One dirty column, four stated rules, eight changed cells
# — and it pairs with p13 precisely because it is the same promise at a
# different scale: 1,992 cells to leave alone against p13's 1,923, and a fold
# list that stops where it stops. p13 folds every misspelling of DOLLARS it
# meets; p4 folds five spellings of DINNER and leaves BREAKFAST MENU alone,
# which is the same instruction read twice and obeyed differently.
MENU = [
    dict(
        n=3, pid=26, stem="menu_p26",
        label="AutoDCWorkflow 1 — menu dates, 100 x 21, 4 dirty columns",
        intro="The attached table menu_p26.csv holds a hundred historical menu "
              "records across twenty-one columns; the one the question depends "
              "on — the date each menu was published — is written four "
              "different ways, and three more columns carry padding.",
        requirements=11,
        clean="""In cleaned.csv's date column, write every date as
YYYY-MM-DDT00:00:00Z, so 01/17/1973 becomes 1973-01-17T00:00:00Z and
1900.01.25 becomes 1900-01-25T00:00:00Z. The source writes them four ways —
year first with hyphens, year first with dots, month first with hyphens, month
first with slashes — and in the year-first ones the day sometimes comes before
the month, so 1949-23-12 is the twenty-third of December and belongs at
1949-12-23T00:00:00Z. A number over twelve is the day. Blank dates stay blank.
In cleaned.csv's event, occasion and notes columns, trim the padded whitespace
and change nothing else about them: capitalisation, spelling and punctuation
stay exactly as they are. In cleaned.csv, leave every other column exactly as
it is.""",
        dropped=[]),
    dict(
        n=4, pid=18, stem="menu_p18",
        label="AutoDCWorkflow 2 — menu sponsors, 100 x 20, 2 dirty columns",
        intro="The attached table menu_p18.csv holds a hundred historical menu "
              "records across twenty columns; the two the question depends on "
              "— the sponsor who issued each menu and the meal it was served "
              "for — are dirty.",
        requirements=17,
        clean="""In cleaned.csv's sponsor column, turn every semicolon,
underscore and hyphen into a space, collapse runs of spaces into one, trim, and
put the result in uppercase, so HAMBURG-AMERIKA  LINIE becomes HAMBURG AMERIKA
LINIE and Toots _Shor becomes TOOTS SHOR. Three values are corrected as they
are folded: NORDDEUTSCHERRR LLOYD BREMEN becomes NORDDEUTSCHER LLOYD BREMEN,
and Adams' Restaurant and Adam's Restaurant both become ADAM'S RESTAURANT. Two
are emptied rather than folded: a lone ? and the placeholder [Restaurant name
and/or location not given] — and only that one, so [Restaurant And/Or Location
Not Given] is uppercased like any other value. In cleaned.csv's event column,
collapse the spaces and uppercase every value, then fold the four that name a
meal in more words than it needs: BREAKFAST MENU is BREAKFAST, DINNER (?) and
DINNER TO ABOVE are both DINNER, and LUNCHEON is LUNCH. In cleaned.csv, leave
every other column exactly as it is.""",
        dropped=[]),
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
            requirements=t["requirements"],
            note=(f"{dirty} cells change out of "
                  f"{(len(rows)-1) * len(rows[0]):,}; the other "
                  f"{(len(rows)-1) * len(rows[0]) - dirty:,} are the promise "
                  f"the freeze exists to keep, and table_checker now decides "
                  f"that promise by comparing the deliverable with its source "
                  f"instead of by reading it. The cleaning rule was checked "
                  f"against the gold table cell by cell before shipping. This "
                  f"pair is the one width of repair the agent does not get "
                  f"right: p13 left three cells of a frozen column changed, "
                  f"p18 uppercased a placeholder the brief empties. Narrower "
                  f"instances — p4, p9, p12 — were all repaired perfectly and "
                  f"are recorded in the docstring as ruled out."),
            tested=True))
        log(f"            task {n}: {t['stem']}.csv, {len(rows)-1} rows x "
            f"{len(rows[0])} cols, {dirty} cells dirty, purpose {t['pid']}")


# ---------------------------------------------------------- 5-6  KG_TO_TEXT

# The triples are reference material, so they are attached rather than pasted
# into the brief — the same rule the tables follow. That is also what keeps the
# requirement list short: the brief states four things and the eighty-odd facts
# the article has to carry sit in a file the requirement checker never sees.
#
# One restatement, because moving the facts makes the upstream wording false:
# "provided below" becomes "provided in the attached file". Nothing else in the
# task section is touched.
KG_BELOW = "provided below in Subject-Predicate-Object (Triple) format"
KG_ATTACHED = "provided in the attached file in Subject-Predicate-Object (Triple) format"

KG_BRIEF = """{head}

The facts are attached as {stem}.txt — {triples} triples. The attachment is
read-only, and the biography rests on it alone: every fact the biography states
comes from there, and it states nothing that is not there.

Write the biography to biography.md. For the target length, treat "around 2048
words" as between 2,000 and 2,100 words."""


def build_kg(meta, briefs, picked):
    for i, (ordinal, row) in enumerate(picked):
        n = 5 + i
        prompt = row["prompt"]
        marker = "**Input Facts (Triples):**"
        if prompt.count(marker) != 1:
            raise RuntimeError(f"kg_{n}: {marker!r} appears "
                               f"{prompt.count(marker)} times, expected 1")
        head = prompt[:prompt.index(marker)].strip()
        if head.count(KG_BELOW) != 1:
            raise RuntimeError(f"kg_{n}: the sentence to restate appears "
                               f"{head.count(KG_BELOW)} times, expected 1")
        head = head.replace(KG_BELOW, KG_ATTACHED)

        body = prompt[prompt.index(marker) + len(marker):]
        triples = [ln.strip()[2:] for ln in body.splitlines()
                   if ln.strip().startswith("- ")]
        if len(triples) < 10:
            raise RuntimeError(f"kg_{n}: found {len(triples)} triples, "
                               f"which is not the row this was written against")
        slug = prompt.split("slug '")[1].split("'")[0]
        stem = f"facts_{n}"
        with open(os.path.join(DATA, stem + ".txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(triples) + "\n")
        targets = row["metadata"]["target_sentences"]
        with open(os.path.join(GOLD, stem + ".json"), "w", encoding="utf-8") as fh:
            json.dump(targets, fh, indent=2, ensure_ascii=False)

        briefs[f"kg_{n}"] = KG_BRIEF.format(head=head, stem=stem,
                                            triples=len(triples))
        meta.append(dict(
            id=f"kg_{n}", n=n, domain="Writing", benchmark="LongWeave",
            attachments=[stem + ".txt"],
            label=f"KG_TO_TEXT {n-4} — {slug.split('_', 1)[1].replace('_', ' ')}, "
                  f"{len(triples)} facts",
            dropped=[dict(
                step="'provided below' -> 'provided in the attached file'",
                reason="the triples are attached rather than pasted into the "
                       "brief, so the upstream wording would point at text "
                       "that is not there")],
            source=f"LongWeave {KG_TASK} (arXiv:2510.24345), MIT, instance "
                   f"{ordinal} in file order, slug {slug}",
            gold=f"gold/{stem}.json",
            grader=f"the {len(targets)} target sentences in gold/{stem}.json — "
                   f"recall of the facts the biography had to carry, plus the "
                   f"word budget. No judge is involved in the recall half.",
            # measured separately: the 119-triple row extracts one more
            # requirement than the 81-triple one
            requirements=7 if n == 5 else 6,
            note=("Four stated constraints instead of fifty statements: 7 "
                  "extracted requirements against the AP-style pair's 46 and "
                  "72. The difficulty sits in the attachment, and the two "
                  "instances fail differently. #1 carries 119 triples into a "
                  "2,000-word budget and overruns it — 2,372 and 2,393 words "
                  "on two unattended runs, which is counted rather than "
                  "judged. #6 carries 81, lands in budget at 2,013, and comes "
                  "out as 31 short paragraphs that restate each other. The "
                  "81-triple instance that used to be task 5 wrote a correct "
                  "biography and had to go."),
            tested=True))
        log(f"            task {n}: {stem}.txt, {len(triples)} triples, "
            f"{len(targets)} gold sentences, slug {slug}")


# ----------------------------------------------------------- the long stream

def stream_longweave():
    """One pass over the 224 MB file, stopping as soon as every picked row is
    in hand. Only the lines whose task string matches are ever parsed."""
    want = {task: set(ix) for task, ix in LONGWEAVE_PICKS}
    seen = {task: -1 for task, _ in LONGWEAVE_PICKS}
    picked = {task: [] for task, _ in LONGWEAVE_PICKS}
    needed = sum(len(ix) for ix in want.values())

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
        build_menu(meta, briefs, purposes)
        build_codeif(meta, briefs)
        picked = stream_longweave()
        build_kg(meta, briefs, picked[KG_TASK])
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
        log(f"  Task {m['n']}  {m['domain']:<15} {m['words']:>5}w  "
            f"~{m.get('requirements', '?'):>2} reqs  {m['label']}")
    log("\nGold answers are in tasks/gold/ and are never attached to a session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
