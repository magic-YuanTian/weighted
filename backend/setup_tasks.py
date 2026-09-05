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

Why these six (2026-09-05, sixth screen). Three domains, two instances each,
every list under twenty requirements.

The study opens the WEIGHTED condition on a recorded pre-run: an unattended
weighted agent ran the task once, stopped where the UI's auto-runner would
have stopped — its finish, a pause, a reply to the user — or at ten steps,
and that run is played back through the ordinary flow (agent/replay.py): the
participant sends the brief, the recorded extraction comes back as the list,
"start the agent" hands out the recorded steps one at a time with their files
and chips, and where the recording stopped the live agent takes over with the
same transcript. Recordings sit in tasks/traces/<task id>/ and are matched by
the brief's text. The two control conditions start live. The two CODE tasks
(restored on 2026-09-05 at the user's word — the earlier pair, 358 and 1087,
in place of 808 and 891) are recorded differently: the pre-run is a PREFIX of
a weighted run, cut before the agent's finish at an unfinished point — the
first five steps of one run, the first three of the other — with at least one
requirement red at the cut (the user: a prefix with every chip green is not
acceptable either) and the deliverable wrong; where the prefix ends the
auto-runner stops without a word in the chat (a dev-only trace marks the
seam), so the participant inherits a disputed draft the agent had not
declared done and starts the live agent themselves. The other four keep the
sixth screen's rule below. Three rules:

  incomplete    the recorded pre-run must stop with at least one requirement
                red and the deliverable wrong by ground truth — a finish with
                every chip green is not used, however wrong the answer — and
                the task must not be one the weighted agent completes inside
                ten steps as a rule. "Complete" is decided against ground
                truth: the file on disk is executed against an oracle, diffed
                against the gold table, or counted against the word budget
                and the facts file, and every chip on the rail is green.
  under twenty  the requirement count, over every extraction seen.
  from scratch  the plain agent (the controls' prompt and tools) still gets
                the task wrong often when it runs from the first message,
                measured over ten unattended steps — or the controls would
                have nothing to catch.

Nine weighted runs per task (six stopped by the auto-continue harness at ten
steps, three in recording mode, stopped at the first halt) and six plain runs,
on the repaired checker (screen_fixed.py / tally_fixed.py / perstep.py in the
session scratchpad; see the memory note). Completions at the first halt:

  1  CodeIF 358         restored 2026-09-05. Eighteen weighted recordings
                        (first halt or ten steps): finishes at steps 3-10 in
                        fifteen, pauses at 3-4 in three; MaxSubarrayLength
                        right at the first halt in 6 of 18, wrong in 12 —
                        mostly 16-17 of the oracle's 60 cases, twice all 60,
                        once a file that does not parse; the first miss is
                        almost always ([-5, 1, 0], k=4): 3, 1 or -1 for 2.
                        11-15 requirements, median 12. Recorded: the first 5
                        steps of a 10-step run (write, two edits, a rewrite,
                        an edit aimed at the judged correctness chip), cut
                        mid-run — that chip still red, 16 of 60 cases wrong.
  2  CodeIF 1087        restored 2026-09-05. Six weighted recordings: finishes
                        at steps 4-6 in three, pauses at step 3 in three;
                        num_tilings right at the halt in 1 of 6 — 8 to 10 of
                        the oracle's 10 cases wrong otherwise (the base case
                        or the recurrence; 12, 28, 65 for 11, 24, 53 in an
                        earlier screen). 12-17 requirements, median 15.
                        Recorded: the first 3 steps of a 6-step run (write,
                        an edit aimed at the judged correctness chip, a
                        read), cut before the edit that fixed it — the
                        judged "returns the number of tilings" chip red on a
                        recurrence that mis-states the gap transition, every
                        case wrong.
  3  menu p25           weighted 2 of 9; the other seven wrong in the
                        sponsor placeholders — [?] and the double-bracketed
                        'not given' notes, emptied where every other
                        bracketed value is unbracketed and uppercased — or
                        without an answer. Plain identical to gold 3 of 6.
                        11-18 requirements, median 14. Recorded: 4 steps,
                        paused with four chips red — currency folds and
                        trims not done, a lone ? left where [?] should be
                        emptied — and no answer yet.
  4  menu p14           weighted 3 of 9; the other six wrong in the same two
                        sponsor cells (rows 24 and 30, the placeholder
                        uppercased with NAME kept where the brief's example
                        drops it). Plain 0 of 6, the same two cells every
                        time. 10-13 requirements. Recorded: 10 steps, paused,
                        one sponsor cell left in mixed case with the
                        uppercase chip red, plus a judged chip.
  5  KG_TO_TEXT #8      weighted 0 of 9, plain 0 of 6: paused by the attempt
                        rule at steps 5-7 on the judged faithfulness chip,
                        6-16 of the 99 fact objects absent every time, the
                        word budget broken in about a third. 5-8
                        requirements. Recorded: 6 steps, paused, 2,120
                        words with the budget chip red, 16 objects absent.
  6  KG_TO_TEXT #11     weighted 0 of 9, plain 0 of 6: paused at steps 4-6,
                        2-5 of 81 objects absent. 6-8 requirements.
                        Recorded: 4 steps, paused, 2,182 words with the
                        budget chip red.

Restored after the sixth screen (2026-09-05). The user judged CodeIF 808 and
891 contrived and asked for the earlier code pair back — 358 and 1087, the
third screen's picks — with the pre-run cut at an unfinished point rather
than caught on a red-chip halt; the two rows above are those recordings.
Before that, a survey of natural-requirement code benchmarks (BigCodeBench-
Hard, ClassEval, NaturalCodeBench, CIFE, IFEvalCode; 68 weighted recordings)
found that fair single-function briefs are finished at step 3 with every chip
green, and the only red-chip halts were checker misfires; see README.

What the sixth screen dropped. CodeIF 137 (the nurses): 0 of 3 weighted in
the first round, then right at its finish in 3 of 3 recording runs — 3 of 9
at the first halt, 4 of 9 pushed through — and the recorded trace would have
been a coin toss. menu p18: complete at weighted steps 4-6 in 3 of 3, and
17-23 requirements. CodeIF 974 (the Fresnel integral) was tried and rejected:
the plain agent gets it wrong 6 of 6, but the weighted agent writes the
closed form at step 2 in 3 of 3, so a replayed pre-run would hand the
participant the answer.

What it ruled out. Code: 700 (5 of 9 weighted), 933 (2 of 9), 358 (2 of 6),
1087 (2 of 3), 1059 (never complete, but on a CamelCase-variables rule
beside a mandated lowercase parameter the checker does not exempt — a
contradiction, which the doable rule forbids). Tables: p28 3/3, p13 2/3,
p20 2/2, p24 1/3, p26 1/3, p27 1/3, p22 1/6 completed by the weighted agent;
p17 and p21 held only by false chips over tables identical to gold (a judge
saying placeholders remain when they are emptied; an extracted "change only
the event column" beside a brief that trims two more); p12's misses turned
on a brief naming a corrected value in its uppercased form when the source is
mixed-case, which the checker's value_map — matched against the source —
cannot see either. Beside the tasks: on p25 the extractor read "Deutsche
Marks, Italian Lire and Drachmas stay exactly as they are" as a value_map
folding the three onto the first, red over a table identical to gold, so the
shipped brief says "every other currency value stays exactly as it is"; and
the judge on 974 rejects a finite-interval quadrature as "not a calculation
of the improper integral" — right when the number is -0.1399, pedantic when
the closed form is returned beside it.

Fourth screen (2026-09-04), kept as the history of the first two rules. Three
rules, in the order they were applied, and the third gave way to the first
two:

  doable        every requirement in the brief can be met by one Python file
                or one CSV in this workspace, and none contradicts another.
                CodeIF grafts instructions onto its questions and some cannot
                be true here — a switch statement, a package, an interface
                naming convention, a CamelCase rule beside a required name
                like min_gelu — so codeif_instructions drops or restates
                them under a generic policy, each edit recorded in the
                manifest with its reason. One table brief (p26) described its
                own data wrongly and was corrected, then dropped.
  checked       every verdict a participant sees is one the checker can
                defend. Where the judge was watched failing correct work —
                trimming, folds, placeholders, uppercase, date formats, an
                attachment's name read as a deliverable's, a dunder read as
                a function name, a required name failed by a convention —
                the check was made deterministic or the routing fixed. What
                is left to the judge is what a judge is for: an answer file,
                a biography's faithfulness, its tone.
  first stop    the run's first halt should not be an accepted finish, and
                should leave a requirement red. With the first two rules
                enforced this holds for the biographies only. On a code or a
                table brief the agent can meet, with a checker that tells it
                the truth, it finishes — usually by step 5, usually right —
                and the earlier screens' non-finishes turn out to have been
                the contradictions and the false verdicts. The code and table
                pairs below are therefore chosen on what the agent gets WRONG
                at that finish, which is the second screen's rule, and the
                README says so.

The stuck pause counts attempts from the agent's own word: every file-changing
tool in the weighted condition carries `targets`, the requirement ids the
change is aimed at, and three changes aimed at one requirement that leave it
violated pause the run, the count restarting when it turns satisfied and when
the user presses continue.

What the fourth screen's unattended runs produced (first-stop rule, cap 24,
weighted only), three runs each on the repaired checker and the repaired
briefs — 137 has since been replaced by 808 and p18 by p25, see above:

  1  CodeIF 137         14 reqs (14, 14, 12). Finishes at steps 4, 3 and 3;
                        solve() right twice and, once, never returns. Across
                        the day's seven runs the answer was wrong in five
                        (45, 0, None, 100, 37 and a hang, for 53); the judge's
                        chip for "compute the minimum" was red over a wrong
                        answer three times.
  2  CodeIF 891         14 reqs (15, 13, 14). Finishes at step 3 three times;
                        min_gelu right twice, and once raises on a numpy
                        attribute that does not exist. Wrong in five of the
                        day's six runs before the list was repaired.
  3  menu p18           21 reqs (21, 21, 20). Finishes at steps 12 and 8 with
                        the table identical to gold once and wrong in one
                        cell once; paused once at step 10 on the judged
                        answer chip. Wrong in seven of nine runs before the
                        checker was repaired, in the placeholder cell.
  4  menu p14           11 reqs (12, 10, 11). Finishes at steps 6, 12 and 6;
                        identical to gold twice, wrong once in two sponsor
                        cells — the placeholder emptied and uppercased the
                        way p18 asks, where this brief keeps it.
  5  KG_TO_TEXT #8      6 reqs. Paused three times of three (steps 6, 10, 8)
                        on the judged faithfulness chip; 2,444 words once.
  6  KG_TO_TEXT #11     7 reqs. Paused three of three (steps 5, 4, 4) on the
                        same chip; 2,160 and 2,164 words against 2,000-2,100.

What the repaired checker changed, instance by instance. Under the attempt
rule with the OLD checker and briefs, 137, 891, 1194, 358, p18, p26 and p28
all stopped short of finishing — and every one of those stops was a
contradiction in the brief or a verdict that was wrong. With both repaired:
1194 finishes 3 of 3 (right twice), 358 finishes 3 of 3 (right twice), p26
finishes or is held only by false judge chips with a table identical to gold
3 of 3, p27 (written for this screen: six dirty columns) the same 3 of 3,
p28 the same. Six more CodeIF questions that state a worked example (316,
999, 165, 646, 703, 743) were run with the new `returns` check: 316 and 646
finish; 999, 165, 703 and 743 stop on further contradictions the generic
policy does not cover — a variable that "should be a constant" and is the
loop counter, "at most one class" beside two named classes — and on two
extractor slips since guarded (a line-count cap read as a line-length cap, a
parameter cap read as a function cap). 703's first draft failed the brief's
own example — compress_string returned 8 for 6 — and the `returns` chip said
so, which is the first time the app has been able to.

The shape of the numbers. A CodeIF instance finishes in three to five steps
and is right two times of three; the wrong third is a hang, a crash, or a
wrong integer that no chip sees. A table instance finishes in six to twelve
and is right two times of three, wrong in one or two cells. Extraction is
stochastic and moves the requirement counts more than anything else does —
p18 measured 15 to 21 across a day — so read a count as a band.

Only the LongWeave file is large. It is streamed once and stopped as soon as
both picked rows are in hand; KG_TO_TEXT/2k sits early, so that is about 30
MB of 224. Every edit to a benchmark brief is in the manifest under `dropped`.
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
CODEIF_URL = ("https://raw.githubusercontent.com/lin-rany/codeIF/master/"
              "data/question/final_release_1200.jsonl")
LONGWEAVE_URL = ("https://huggingface.co/datasets/zikaixiao1/LongWeave/"
                 "resolve/main/longweave.jsonl")

# The exact instances, pinned so a re-run reproduces the same six tasks.
# CodeIF carries a question_id, so its pair is pinned by that. LongWeave has no
# per-row id, so an instance is pinned by its ordinal within its (task, tier)
# group, counted in file order.
CODEIF_IDS = [358, 1087]
KG_TASK = "longweave/KG_TO_TEXT/2k"
LONGWEAVE_PICKS = [
    (KG_TASK, [8, 11]),
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
# Both instances were picked by measuring, not reading: the real extractor run
# over the brief three times, the real agent run over it unattended in the
# weighted condition until its first halt, and the file on disk at that halt
# executed against an oracle written from the question.
#
# One carries an instruction a single Python file cannot satisfy as written —
# 358 asks for a switch statement (808 did too, and 891 for a function named
# min_gelu under a CamelCase rule) —
# and the policy below drops or restates those, each edit recorded in the
# manifest. What is left is the benchmark's own text: the extractor's guard
# (extract._grounded) routes a construct or a negated property the parser has
# no answer for to the judge rather than letting the parser answer the
# opposite question, which is what used to fail correct code on four of every
# eleven CodeIF briefs.
#
# CODEIF_DROP_SENTENCE stays because it is the record of an edit policy: CodeIF
# 652 used to end with "Please write your code inside a markdown ```python```
# wrapper", a format instruction aimed at a chat answer, and here the
# deliverable is a file. 358 and 1087 carry no such sentence, and no repeated
# instruction; both were checked before pinning (as were 808 and 891).
CODEIF_DROP_SENTENCE = {}

# Instruction-level edits, under the same policy as the tables: an instruction
# may be DELETED with its reason recorded, or RESTATED, never added. What gets
# edited is what a single Python file in this workspace cannot satisfy as
# written, and a participant must not be handed a list that cannot be met:
#
#   switch      Python has no switch statement. A brief that demands one is
#               demanding a construct the language lacks.
#   package     The workspace is one flat directory of deliverable files and
#               a package is a directory; "organised in a package named X" is
#               impossible here. Where the same instruction also names
#               functions the package must contain, the functions are kept
#               and the package is dropped.
#   convention  "Function names in CamelCase" beside "a function named
#               min_gelu" cannot both be true of one file, and every reader of
#               the brief resolves it the same way: the convention is for the
#               names you choose. The checker reads it that way too — a name
#               the brief itself demands is exempt from the convention the same
#               brief states (verifier.mandated_names) — and the instruction is
#               restated to say so. An interface naming convention has nothing
#               to be true of at all: Python has no interfaces, and the brief's
#               own interface name is lowercase.
# The rules are generic — written as patterns, not per question — so that a
# screen over any CodeIF instance applies the same policy, and each edit is
# recorded in the manifest with its reason.
CODEIF_DROP_PATTERNS = [
    (re.compile(r"\bswitch statement\b", re.I),
     "Python has no switch statement"),
    (re.compile(r"\binterface names?\b.*\bnaming convention\b", re.I),
     "Python has no interfaces to name, and the interfaces such briefs name "
     "are lowercase"),
    (re.compile(r"\borganized in a package named\b", re.I),
     "the workspace is a single flat directory; a package is a directory"),
]
CODEIF_PACKAGE_FUNCTIONS = re.compile(
    r"^Your code should be organized in a package named \S+?,? (?:which should "
    r"contain|containing) these (functions|classes) (.*?)\.?$", re.I)
CODEIF_CONVENTION = re.compile(
    r"^The (function|class|variable) names in your code should follow the "
    r"(\S+?)\.? naming convention", re.I)
CODEIF_MANDATED = [
    (re.compile(r"\b(function|class|method)s? named [`'\"]?([A-Za-z_]\w*)", re.I), None),
    (re.compile(r"\bfunction name (?:is|as) [`'\"]?([A-Za-z_]\w*)", re.I), "function"),
    (re.compile(r"\bfunction signature is [`'\"]?([A-Za-z_]\w*)\s*\(", re.I), "function"),
    (re.compile(r"\bfunction [`]([A-Za-z_]\w*)[`]", re.I), "function"),
    (re.compile(r"\bdefine a python function [`]?([A-Za-z_]\w*)\s*\(", re.I), "function"),
    (re.compile(r"\binclude a function named [`'\"]?([A-Za-z_]\w*)", re.I), "function"),
    (re.compile(r"\bdefine a class named [`'\"]?([A-Za-z_]\w*)", re.I), "class"),
]


def _convention_rx(name):
    import code_checker
    key = name.lower().replace("-", "_")
    key = code_checker._CONVENTION_ALIASES.get(key, key)
    return code_checker.CONVENTIONS.get(key)


def _mandated(question, texts):
    """{kind: [names]} the question and instructions demand verbatim."""
    out = {"function": [], "class": [], "variable": []}
    for src in [question] + texts:
        for rx, kind in CODEIF_MANDATED:
            for m in rx.finditer(src):
                if kind is None:
                    k, name = m.group(1).lower(), m.group(2)
                    k = "function" if k == "method" else k
                else:
                    k, name = kind, m.group(1)
                if name not in out.setdefault(k, []):
                    out[k].append(name)
    return out


def codeif_instructions(qid, ins, question=""):
    """The instruction list after the policy above -> (texts, dropped notes)."""
    texts, dropped = [], []
    for x in ins:
        t = x["instruction"].strip()
        m = CODEIF_PACKAGE_FUNCTIONS.match(t)
        if m and m.group(2).strip(" []'\""):
            what = m.group(1).lower()
            names = ", ".join(n.strip(" []'\"") for n in m.group(2).split(","))
            new = f"Your code should define these {what}: {names}."
            texts.append(new)
            dropped.append(dict(step=f"restated {t!r} as {new!r}",
                                reason=f"the package is impossible in a flat "
                                       f"workspace; the {what} it was to "
                                       f"contain are kept"))
            continue
        why = next((r for rx, r in CODEIF_DROP_PATTERNS if rx.search(t)), None)
        if why:
            dropped.append(dict(step=f"dropped {t!r}", reason=why))
            continue
        texts.append(t)
    mandated = _mandated(question, texts)
    for i, t in enumerate(texts):
        m = CODEIF_CONVENTION.match(t)
        if not m:
            continue
        kind, conv = m.group(1).lower(), m.group(2)
        rx = _convention_rx(conv)
        clash = [n for n in mandated.get(kind, []) if rx is not None and not rx.match(n)]
        if clash:
            new = (t.rstrip(".") + f", apart from the names this task requires "
                   f"({', '.join(clash)}).")
            dropped.append(dict(step=f"restated {t!r} as {new!r}",
                                reason=f"the brief itself demands the {kind} name(s) "
                                       f"{', '.join(clash)}, which are not {conv}; "
                                       f"the convention is for the names the author "
                                       f"chooses, and the checker exempts the demanded "
                                       f"ones (verifier.mandated_names)"))
            texts[i] = new
    return texts, dropped


CODEIF_BRIEF = """{question}

Requirements:
{instructions}

Write the solution to solution.py. The file is the deliverable — the whole of
it is graded, so nothing belongs in the chat that belongs in the file.
"""

# What each instance is, for the picker's label and for the note that records
# why it survived the screen. `requirements` is the median of the fifth
# screen's nine extractions (six plain runs, three weighted) over the brief
# this script writes.
CODEIF = {
    358: dict(
        n=1, label="CodeIF 1 — longest subarray not divisible by k, 9 constraints",
        requirements=14,
        note=("Finds the longest subarray whose sum is not divisible by k, "
              "with no for-loop, no list, at most one function, and an "
              "interface named SubarrayInterface; the switch statement the "
              "brief asks for is dropped (Python has none) and the manifest "
              "records it. Picked in the third screen because the agent gets "
              "it WRONG: its unattended answer reported length 3 for "
              "[-5, 1, 0] with k=4, where the longest such subarray is [1, 0] "
              "and the answer is 2 — one term off in a prefix computation, "
              "which is what a logic error in this benchmark looks like: "
              "right on most inputs, wrong on the ones that matter. Restored "
              "on 2026-09-05 at the user's word, in place of 808; the "
              "pre-run is a recorded prefix, see the docstring."),
        tested=True),
    1087: dict(
        n=2, label="CodeIF 2 — tiling a 2 x N board with dominos and trominos, 10 constraints",
        requirements=16,
        note=("Counts the tilings of a 2xN board with dominos and L-trominos "
              "modulo 1e9+7, under a no-if-statement rule and a pile of grafted "
              "naming constraints (an interface remove_shipment, a class "
              "AnalyzeDwa with two properties, an object tys, numpy imported, "
              "a finally). Picked in the third screen because the agent gets "
              "the count WRONG and keeps getting it wrong: one run returned "
              "12, 28 and 65 for N = 4, 5, 6 against 11, 24 and 53, another "
              "1, 5, 21, 89, which is not even the same sequence. The "
              "recurrence is easy to state and easy to mis-state, which is "
              "the whole of the difficulty — the constraints are wrapping. "
              "Nothing in the app sees this: the run stops green, and the "
              "requirement that should notice is judged. Restored on "
              "2026-09-05 at the user's word, in place of 891; the pre-run is "
              "a recorded prefix, see the docstring."),
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
        if len(ins) != len(set(x["instruction"] for x in ins)):
            raise RuntimeError(f"{task}: the upstream instruction list repeats "
                               f"itself, which is not the row this was written "
                               f"against")
        question = row["question"].strip()
        texts, dropped = codeif_instructions(qid, ins, question)
        for d in dropped:
            log(f"            {qid}: {d['step'][:70]}")
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
            instructions="\n".join(f"{i}. {t}" for i, t in enumerate(texts, start=1)))
        meta.append(dict(
            id=task, n=n, domain="Code generation", benchmark="CodeIF",
            attachments=[], label=spec["label"], dropped=dropped,
            source=(f"CodeIF (ACL 2025 Industry) question_id {qid}, "
                    f"{row['meta_info']['item_set']} split, "
                    f"{row['meta_info']['programming_language']}, "
                    f"{len(ins)} instructions upstream, {len(texts)} kept"),
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
# share one table — p26 and p28 among them, so those two could never be a
# pair: a participant would be handed the same hundred rows twice. p18 sits
# on a table of its own.
#
# What picks the pair, on the repaired checker, is what is on disk when the
# run stops: p18 and p14 are the two instances whose table still comes out
# wrong in some runs (one cell, two cells), and their traps are mirror
# images of each other. p26 (its date sentence corrected), p27 and p28 were
# repaired identically to gold in every run; what held them, where anything
# did, was a judged chip that was wrong.
MENU = [
    dict(
        n=3, pid=25, stem="menu_p25",
        label="AutoDCWorkflow 1 — menu sponsors and currencies, 100 x 21, 2 dirty columns",
        intro="The attached table menu_p25.csv holds a hundred historical menu "
              "records across twenty-one columns; the two the question depends on "
              "— the sponsor who issued each menu and the currency it prices in — "
              "are dirty, and three more columns carry padding.",
        requirements=14,
        header_free=True,
        clean="""In cleaned.csv's sponsor column, drop the square brackets that enclose
most of the names and put every value in uppercase, so [battery park hotel]
becomes BATTERY PARK HOTEL and toots shor becomes TOOTS SHOR. Two names are
corrected as they are folded: [Adams' Restaurant] and [Adam's Restaurant] both
become ADAM'S RESTAURANT, and [NORDDEUTSCHERRR LLOYD BREMEN] becomes
NORDDEUTSCHER LLOYD BREMEN. Four values are emptied rather than folded, because
they are not sponsors: [?] and the three double-bracketed notes saying that a
restaurant name and/or location was not given. In cleaned.csv's currency column,
fold the three misspellings of the dollar — Dolars, Doller and Dollers — onto
Dollars, and empty the five values that are not currencies: None, n/a, N/A,
unknown and null. In
cleaned.csv's event, occasion and notes columns, trim the padded whitespace and
change nothing else about them: capitalisation, spelling and punctuation stay
exactly as they are. In cleaned.csv, leave every other column exactly as it
is.""",
        dropped=[],
        note=("Sixth screen (weighted ten-step pre-run): the traps are the "
              "sponsor placeholders — [?] and three double-bracketed 'not "
              "given' notes that are emptied where every other bracketed "
              "value is unbracketed and uppercased — and the currency "
              "column's five non-values. The weighted agent completed it at its "
              "first halt in 2 runs of 9 and left the placeholders wrong in "
              "six (2 to 4 cells; once the whole sponsor column untouched at "
              "a reply); the plain agent from scratch had the table identical "
              "to gold in 3 of 6. 11-18 requirements over nine extractions, "
              "median 14. Recorded pre-run: 4 steps, paused by the attempt rule "
              "with four chips red — currency folds and trims not done, a "
              "lone ? left where [?] should be emptied — and no answer yet. "
              "The brief says "
              "nothing about the currency values it does not change: "
              "'Deutsche Marks, Italian Lire and Drachmas stay exactly as "
              "they are' was extracted as a fold of the three onto the "
              "first, and 'every other currency value stays exactly as it "
              "is' as a freeze on every other COLUMN, red over the sponsor "
              "repairs the same brief asks for — both false chips over "
              "tables identical to gold, so the sentence is gone.")),
    dict(
        n=4, pid=14, stem="menu_p14",
        label="AutoDCWorkflow 2 — menu dish counts, 100 x 20, 2 dirty columns",
        intro="The attached table menu_p14.csv holds a hundred historical menu "
              "records across twenty columns; the two the question depends on "
              "— the sponsor who issued each menu and how many dishes it lists "
              "— are dirty.",
        requirements=12,
        clean="""In cleaned.csv's sponsor column, put every value in uppercase, so
Southern Pacific becomes SOUTHERN PACIFIC and the placeholder [Restaurant name
and/or location not given] becomes [RESTAURANT AND/OR LOCATION NOT GIVEN]. One
name is corrected as it is folded: Adams' Restaurant becomes ADAM'S RESTAURANT.
Nothing else about a name changes — a misspelling stays misspelled, only
uppercased. In cleaned.csv's dish_count column, drop the trailing .0 and keep
the whole number, so 22.0 becomes 22 and 546.0 becomes 546. In cleaned.csv,
leave every other column exactly as it is.""",
        dropped=[],
        note=("Fifth screen, ten-step budget: the plain agent finished at "
              "step 8 five times and ran to the cap once, and the table was "
              "wrong in the same two sponsor cells every time — rows 24 and "
              "30, where the placeholder is uppercased with the word NAME "
              "kept, [RESTAURANT NAME AND/OR LOCATION NOT GIVEN], and the "
              "brief's example drops it. The weighted agent, told by the "
              "checker, had it identical to gold once (right from step 4, "
              "finish at step 6) and was held at step 9 twice with the two "
              "cells wrong. The "
              "only menu instance the plain agent never completed. Sixth screen: "
              "weighted complete at the first halt in 3 of 9, the other six "
              "wrong in the same two cells. Recorded pre-run: 10 steps, paused, "
              "one sponsor cell left in mixed case with the uppercase chip "
              "red, plus a judged chip. "
              "Fourth screen: the trap is the opposite of p18's: here the bracketed "
              "placeholder is uppercased like any other value and the "
              "misspelt NORDDEUTSCHERRR LLOYD BREMEN stays misspelt, where "
              "p18 empties the one and corrects the other, and an agent that "
              "remembers the other table gets this one wrong. On the repaired "
              "checker the run finishes at its first stop in three runs of "
              "three (steps 6, 12, 6); the table was identical to gold twice "
              "and wrong once, in two sponsor cells — the placeholder written "
              "as p18's brief would have it. 119 cells change.")),
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
                  f"the freeze exists to keep, and table_checker decides that "
                  f"promise by comparing the deliverable with its source "
                  f"instead of by reading it. The cleaning rule was checked "
                  f"against the gold table cell by cell before shipping. "
                  + t["note"]),
            tested=True))
        log(f"            task {n}: {t['stem']}.csv, {len(rows)-1} rows x "
            f"{len(rows[0])} cols, {dirty} cells dirty, purpose {t['pid']}")


# ---------------------------------------------------------- 5-6  KG_TO_TEXT

# The triples are reference material, so they are attached rather than pasted
# into the brief — the same rule the tables follow. That is also what keeps the
# requirement list short: the brief states four things and the eighty-odd facts
# the biography has to carry sit in a file the requirement checker never sees.
#
# One restatement, because moving the facts makes the upstream wording false:
# "provided below" becomes "provided in the attached file". Nothing else in the
# task section is touched.
#
# The word budget is the one chip on this task a parser decides, and both
# instances are outside it at most first stops. What the attempt rule fires on
# is usually the judged chip beside it — every fact from the file, nothing that
# is not — which the agent aims at three times and cannot turn, and whether
# that verdict is right is for the participant to weigh.
KG_BELOW = "provided below in Subject-Predicate-Object (Triple) format"
KG_ATTACHED = "provided in the attached file in Subject-Predicate-Object (Triple) format"

KG_BRIEF = """{head}

The facts are attached as {stem}.txt — {triples} triples. The attachment is
read-only, and the biography rests on it alone: every fact the biography states
comes from there, and it states nothing that is not there.

Write the biography to biography.md. For the target length, treat "around 2048
words" as between 2,000 and 2,100 words."""

KG = {
    8: dict(
        n=5, requirements=6,
        note=("Fifth screen, ten-step budget: never completed in nine runs. "
              "The plain agent finished at steps 4 to 6 five times and ran "
              "to the cap once, with 6 to 15 of the 99 fact objects absent "
              "from the biography every time and the word count out of "
              "budget twice; the weighted agent paused at steps 4 to 5 "
              "three times of three, resumed to the cap, and was still "
              "missing 9 to 13 objects and out of budget every time. Sixth screen: "
              "0 of 9 weighted at the first halt, paused at steps 5-7. "
              "Recorded pre-run: 6 steps, paused, 2,120 words with the budget "
              "chip red, 16 objects absent. "
              "Fourth screen: paused by the attempt rule in three unattended runs of three "
              "on the repaired checker, at steps 6, 10 and 8, each time on "
              "the judged chip — state nothing the file does not — aimed at "
              "three times and still red; out of budget once (2,444 words) "
              "and in budget twice. Across nine runs over the day it paused "
              "nine times and was out of budget in five. 100 triples.")),
    11: dict(
        n=6, requirements=7,
        note=("Fifth screen, ten-step budget: never completed in nine runs. "
              "The plain agent finished at steps 4 to 9 with 2 to 18 of the "
              "81 fact objects absent every time and out of budget once; "
              "the weighted agent paused at steps 4 to 5 three times of "
              "three with 2 to 4 objects absent. Sixth screen: 0 of 9 weighted, "
              "paused at steps 4-6. Recorded pre-run: 4 steps, paused, 2,182 "
              "words with the budget chip red. "
              "Fourth screen: paused in three of three, at steps 5, 4 and 4, on the same "
              "judged chip, and out of budget in two of three: 2,160 and "
              "2,164 words against 2,000 to 2,100. Across nine runs over the "
              "day it paused nine times and was out of budget in six. 82 "
              "triples, the smallest file in the set.")),
}


def build_kg(meta, briefs, picked):
    for ordinal, row in picked:
        spec = KG[ordinal]
        n = spec["n"]
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
        if "around 2048 words" not in prompt:
            raise RuntimeError(f"kg_{n}: the upstream target is not 2048 words, "
                               f"which is not the row this was written against")

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
            grader=f"the word budget, counted — 2,000 to 2,100 words — plus "
                   f"the {len(targets)} target sentences in gold/{stem}.json "
                   f"for recall of the facts the biography had to carry. No "
                   f"judge is involved in either.",
            requirements=spec["requirements"],
            note=spec["note"],
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
