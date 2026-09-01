"""Fetch the six benchmark tasks and build them into backend/tasks/.

The tasks are not committed. Two of the three source benchmarks ship without a
licence file, so this repository does not redistribute them — it fetches them
from their own publishers and assembles the briefs locally. Run once:

    python setup_tasks.py

Afterwards the picker in the composer offers the six tasks. Without them the
/api/agent/presets endpoint returns an empty list and the picker hides itself,
so the app works either way.

Sources
    CodeIF          github.com/lin-rany/codeIF        (no licence file)
    AutoDCWorkflow  github.com/LanLi2017/LLM4DC       (no licence file)
    LongWeave       hf.co/datasets/zikaixiao1/LongWeave (MIT)

Only the LongWeave file is large, and it is streamed just as far as the rows
we need; everything else is a handful of small direct fetches.
"""

import csv
import io
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")
DATA = os.path.join(TASKS, "data")

CODEIF_URL = ("https://raw.githubusercontent.com/lin-rany/codeIF/master/"
              "data/question/final_release_1200.jsonl")
DCW_BASE = "https://raw.githubusercontent.com/LanLi2017/LLM4DC/main/"
LONGWEAVE_URL = ("https://huggingface.co/datasets/zikaixiao1/LongWeave/"
                 "resolve/main/longweave.jsonl")

# The exact instances. Pinned by id so a re-run reproduces the same six tasks.
CODEIF_IDS = [367, 738]

# CodeIF's instruction lists are generated, and question 367's came out
# self-defeating: two blanket naming conventions that the same list's explicit
# names cannot obey, and a call into a Flask function that does not exist. A run
# cannot pass them, and an agent that tries breaks the requirement it was
# obeying a moment ago -- in the pilot it renamed MsgProduct to MSG_PRODUCT to
# satisfy #14 and thereby failed #13. Nothing is rewritten; these are dropped
# whole, keyed by their 1-based position upstream, and the manifest records it.
CODEIF_DROP = {
    367: {
        11: "requires CAPITALIZED_WITH_UNDERSCORES function names, which #10's "
            "sort_event cannot have",
        14: "requires CAPITALIZED_WITH_UNDERSCORES class names, which #13's "
            "MsgProduct cannot have",
        16: "requires sort_event from Flask; Flask has no such function",
    },
}
LONGWEAVE_TASK = "longweave/AP_STYLE_WRITING/2k"

# LongWeave's briefs carry two kinds of instruction. The numbered statements
# under each category are the benchmark's recall half: rewrite this sentence,
# include it, keep its meaning. Around them sits prose -- a few lines of
# scoring criteria per category, and a pair of illustrating example lists --
# and that prose is what the runs die on.
#
# It extracts into whole-article judgements. "Use short, clear sentences",
# "maintain consistent style throughout the text" and "capitalize formal
# titles before a person's name" each become one requirement measured over the
# entire 2,000-word report, re-judged after every step, pointing at no sentence
# in particular. Both measured task 5 runs ended the same way: the gate
# rejected finish on exactly these, and the agent stopped to ask which
# sentences it was being failed for -- a question the verdict cannot answer.
#
# The example lists are worse. The agent reads them as required content and
# pastes the AP textbook into the news report: one run's article ends with
# "The person arrived at the site, and this is a technical topic." and
# "President Joe Biden visited in a comparison used by the editors" -- and the
# consistency judgement then fails the article for the editorial matter the
# brief's own examples put there.
#
# So the criteria and their examples are dropped whole and verbatim, and the
# manifest records each one. What is NOT dropped: the numbered statements, the
# per-category "Content Requirements for 'X'" headers that demand them, and
# every rule whose violations the statements themselves carry -- courtesy
# titles, gender-neutral terms, dates, capitalization. Recall is untouched;
# what goes is the holistic style judgement over the finished article, which
# no edit could ever settle.
LONGWEAVE_DROP = {
    5: [
        {"rule": "rewrite-and-include-all (umbrella)",
         "reason": "one requirement covering all 50 statements at once, "
                   "re-judged in bulk and unsettleable by any single edit; the "
                   "per-category headers keep the same demand statement by "
                   "statement",
         "blocks": [
             "**IMPORTANT: Each AP Style category includes example sentences "
             "that violate its rules. Rewrite and include all of them in your "
             "article, following AP Style and keeping their meaning. Missing "
             "or uncorrected items will reduce your score.**\n\n",
         ]},
        {"rule": "Clarity and Brevity — scoring criteria and examples",
         "reason": "brevity, readability and consistency judged over the whole "
                   "article; the examples are pasted into the report as content",
         "blocks": [
             "Scoring Criteria:\n"
             "- Brevity:\n"
             "  Avoid long or complex sentences.\n"
             "- Readability:\n"
             "  Use simple language suitable for general audiences.\n"
             "- Consistency:\n"
             "  Maintain consistent style throughout the text.\n"
             "\n"
             "Incorrect Examples:\n"
             "- 'The aforementioned individual arrived at the location.'\n"
             "- 'This is a highly technical subject matter.'\n"
             "\n"
             "Correct Examples:\n"
             "- 'The person arrived at the site.'\n"
             "- 'This is a technical topic.'\n"
             "\n",
         ]},
        {"rule": "Titles and Positions — formal-title capitalization",
         "reason": "judged over the whole article with no sentence to point "
                   "at; statements 31-40 still carry the violations to fix",
         "blocks": [
             "  Capitalize formal titles before a person's name; use lowercase "
             "after the name or when used alone.\n",
             "- 'President Joe Biden visited.'\n"
             "- 'Joe Biden, President, spoke.'\n",
             "- 'President Joe Biden visited.'\n"
             "- 'Joe Biden, the president, spoke.'\n",
         ]},
    ],
}


def drop_blocks(prompt, drops, n):
    """Remove the recorded blocks from one LongWeave prompt.

    Verbatim or not at all: a block that is missing, or that appears twice, is
    an upstream text this drop list was not written against, and quietly
    dropping the wrong span would change the benchmark without saying so.
    """
    records = []
    for drop in drops:
        for block in drop["blocks"]:
            hits = prompt.count(block)
            if hits != 1:
                raise RuntimeError(
                    f"longweave_{n}: the drop for {drop['rule']!r} matched "
                    f"{hits} times upstream, expected exactly 1")
            prompt = prompt.replace(block, "", 1)
        records.append({"rule": drop["rule"], "reason": drop["reason"],
                        "text": "".join(drop["blocks"]).strip()})
        log(f"            dropped {drop['rule']}")
    return prompt, records


# Appended to both CodeIF briefs. The benchmark's own constraints are additive
# and the agent satisfies them unattended; these interact — the eight-line cap
# fights the no-comprehension rule, and writing a loop out fights the line
# width — so holding one in focus while fixing another is what the highlight
# feature is for. Measured at reasoning "none": with this paragraph the agent
# churns (8 gate bounces, violations left); without it, it converges alone.
#
# A one-line-docstring rule was here and is gone. The sentence asked for "a
# one-line docstring that says what it takes (self aside) and what it
# returns", which extraction splits in two: docstring presence, which a parser
# settles, and "says what it takes and returns", which only a judge can. The
# judge never settled it — across two runs it went satisfied, violated and
# satisfied again over docstrings the step in between had not touched — so the
# requirement was measuring the judge rather than the agent, and the task lost
# the sentence instead of keeping an unsteerable chip on the rail.
CODEIF_HOUSE_STYLE = (
    "The file must also satisfy a house style. Keep every line under 80 "
    "characters. Keep every function body, the lines between the def line "
    "and the end of the function, to at most eight lines. Do not leave "
    "blank lines inside any function body, and do not use list "
    "comprehensions anywhere; write the loops out instead.")

# Per-question clarifiers appended after the benchmark constraints. 738's
# list pairs "snake_case variable names" with "max_freq should be a
# constant"; read strictly those collide (MAX_FREQ fails one, max_freq the
# other) and the agent ping-pongs — measured. The clarifier picks the one
# consistent reading instead of leaving the collision in the task.
CODEIF_CLARIFY = {
    367: "One clarification so no requirement is impossible: PyTorch's "
         "import name is torch, so `import torch` is how the pytorch "
         "import requirement is satisfied.",
    738: "One clarification so the naming rules cannot collide: 'a "
         "constant' here means max_freq is assigned exactly once and never "
         "reassigned; its name stays max_freq, in snake_case, not "
         "upper-case. In addition: max_frequency_component must reject bad "
         "input — a signal that is not a list, or an empty list, raises "
         "ValueError with the message 'signal must be a non-empty list' — "
         "and every function that returns a value must do so from exactly "
         "one return statement.",
}


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=600):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


# ----------------------------------------------------------------- the tasks

def build_codeif(meta, briefs):
    log("CodeIF      fetching 1,200 questions…")
    rows = {}
    for line in fetch(CODEIF_URL).decode("utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rows[r["question_id"]] = r

    for n, qid in enumerate(CODEIF_IDS, start=1):
        r = rows.get(qid)
        if r is None:
            raise RuntimeError(f"CodeIF question_id {qid} not found upstream")
        drop = CODEIF_DROP.get(qid, {})
        kept = [x for i, x in enumerate(r["instruction_list"], start=1)
                if i not in drop]
        # A brief that says "Requirements:" and numbers them tells participants
        # that these are the requirements and the rest of the text is not —
        # and hands them the tracking the tool is supposed to be doing. The
        # sentences are kept verbatim, woven into a paragraph.
        ins = " ".join(s if s.endswith((".", "!", "?")) else s + "."
                       for s in (x["instruction"].strip() for x in kept))
        for i in sorted(drop):
            log(f"            dropped #{i}: {drop[i]}")
        clarify = CODEIF_CLARIFY.get(qid)
        briefs[f"codeif_{qid}"] = (r["question"].strip() + "\n\n" + ins
                                   + (" " + clarify if clarify else "")
                                   + "\n\n" + CODEIF_HOUSE_STYLE
                                   + "\n\nWrite the solution to solution.py.")
        first = n == 1
        meta.append(dict(
            id=f"codeif_{qid}", n=n, domain="Code generation", benchmark="CodeIF",
            label=(f"CodeIF {n} — sentiment app, {len(kept)} constraints + house style"
                   if first else
                   f"CodeIF {n} — frequency analysis, {len(kept)} constraints + house style"),
            dropped=[{"index": i, "reason": drop[i],
                      "instruction": r["instruction_list"][i - 1]["instruction"]}
                     for i in sorted(drop)],
            source=f"CodeIF (ACL 2025 Industry) question_id {qid}, "
                   f"{r['meta_info']['item_set']} split, Python",
            note=("Benchmark constraints plus the house style. Measured at "
                  "reasoning none: the benchmark list alone converges "
                  "unattended in one turn; with the house style the run "
                  "sticks at the turn cap with 8 gate bounces." if first else
                  "Same treatment as the other CodeIF task, plus the "
                  "benchmark's own max_freq tension (snake_case vs constant) "
                  "— the pair that exercises requirement editing. Measured "
                  "at reasoning none: 8 bounces unattended, and "
                  "highlighting the violated requirements moved 4 "
                  "violations down to 1 in one sitting."),
            tested=True))
        log(f"            task {n}: question_id {qid}, {len(kept)} constraints"
            + (f" ({len(drop)} dropped as unsatisfiable)" if drop else ""))


# The two AutoDCWorkflow instances. The benchmark's unit is (dirty table,
# purpose) with a gold cleaned table and a gold OpenRefine recipe — a
# sequence of data transformations, which is what makes these data WRANGLING
# tasks rather than analysis. Edit policy for these briefs (2026-08-27,
# study design): benchmark requirements may be DELETED (reason recorded) or
# RESTATED, never added — no invented constraints, no extra deliverables.
# The cleaning instructions verbalize each instance's actual dirty->clean
# diff (verified against the gold tables, not the recipe JSON, whose
# mass-edit lists were recorded against other samples), and the only
# deliverables are the benchmark's own: the cleaned table, plus the answer
# to the instance's purpose question, quoted verbatim, with no format
# constraints of ours attached. One deletion, recorded here and in the
# manifest: the gold flights table renders arrival times through
# OpenRefine's toDate(), which stamps a fake date and blanks 8 cells it
# fails to parse; that step is dropped, and the surviving normalization is
# restated against the format the same table's departure columns already
# use, which loses nothing and keeps every cell checkable.
DCW = [
    dict(
        n=3, pid=148, stem="hos_data_p148",
        table="datasets/hospital/hos_data_p148.csv",
        label="AutoDCWorkflow 1 — hospital registry, 2 dirty columns",
        intro="The attached table hos_data_p148.csv lists twenty hospital "
              "records, and the two columns the question depends on are dirty.",
        clean="""In cleaned.csv's CountyName column, trim the
padded whitespace, put every value in uppercase, and fix the two corrupted
names: CHILTuN is CHILTON and COmFEE is COFFEE. In cleaned.csv's
EmergencyService column, trim and uppercase every value, then repair the
corrupted entries so the column holds only YES or NO — YsS, YEz, MES and rES
all mean YES. In cleaned.csv, leave every other column exactly as it is.""",
        note="Nothing here is added to the benchmark: the brief quotes the "
             "purpose verbatim and restates the gold dirty->clean diff in "
             "prose; the deliverables are the cleaned table and the answer "
             "to the purpose question, unstyled. Measured at reasoning none "
             "after the 2026-08-27 edit-policy revision: converges "
             "unattended, 2/2 runs, 2 turns, one to two minutes — the "
             "verification-showcase warm-up, not a churn task.",
        tested=True),
    dict(
        n=4, pid=117, stem="flights_data_p117",
        table="datasets/flights/dirty_tables/flights_data_p117.csv",
        label="AutoDCWorkflow 2 — flight times, 3 dirty columns",
        intro="The attached table flights_data_p117.csv records fifty flights' "
              "scheduled and actual times, collected from a tangle of travel "
              "sites; the source names and both arrival-time columns are dirty.",
        clean="""In cleaned.csv's src column, three names are special:
HLight becomes HELLOFLIGHT, USAT becomes USATODAY, and world-flight-tracker
becomes WORLD-FLIGHT-TRACKER, keeping its hyphens. Every other name loses
its underscore and dash decoration — turn them into spaces, trim, and put
the name in uppercase, so --co-- becomes CO and __flightaware__ becomes
FLIGHTAWARE. In cleaned.csv's two arrival columns, sched_arr_time and
act_arr_time, strip the same underscore and dash decoration and write every
value in the format the departure columns of the same table already use — a
lowercase time that keeps the :00 on whole hours, so 2:20-P.M. becomes
2:20 p.m. and __8:00_a.m.__ becomes 8:00 a.m. In cleaned.csv, leave the
departure columns and every other column untouched.""",
        dropped=[dict(
            step="OpenRefine toDate() on sched_arr_time and act_arr_time",
            reason="the gold table's toDate() stamps a fake date on every "
                   "arrival time and blanks 8 cells it fails to parse; the "
                   "step is deleted and the surviving normalization is "
                   "restated against the departure columns' own format")],
        note="The heavier half of the pair, and still pure benchmark: a "
             "name-folding pass whose three exceptions come from the gold "
             "table itself, the purpose question verbatim, no added "
             "constraints. One gold step deleted and recorded (toDate). "
             "Measured at reasoning none after the 2026-08-27 edit-policy "
             "revision: converges unattended, 2/2 runs, 1-3 turns, outputs "
             "spot-checked correct; its pre-memo ancestor stuck about one "
             "run in four on the folding exceptions, so a stick stays "
             "possible but cannot be counted on.",
        tested=True),
]

DCW_BRIEF = """{intro} The question, quoted from the benchmark: "{purpose}"

{clean}

Write the cleaned table to cleaned.csv, keeping the header, every row, every
column, and the original row order — the repairs above are the only changes.
Then write the answer to the quoted question to answer.md, worked from
cleaned.csv.

The data is attached as {stem}.csv."""


def build_dcw(meta, briefs):
    log("AutoDCWork  fetching purposes and dirty tables…")
    text = fetch(DCW_BASE + "purposes/all_purposes.csv").decode("utf-8")
    purposes = {int(r["ID"]): r["Purposes"].strip()
                for r in csv.DictReader(io.StringIO(text))}

    os.makedirs(DATA, exist_ok=True)
    for t in DCW:
        purpose = purposes.get(t["pid"])
        if not purpose:
            raise RuntimeError(f"AutoDCWorkflow purpose {t['pid']} not found upstream")
        table = fetch(DCW_BASE + t["table"]).decode("utf-8", "replace")
        with open(os.path.join(DATA, t["stem"] + ".csv"), "w", encoding="utf-8") as fh:
            fh.write(table)

        n = t["n"]
        briefs[f"dcw_{n}"] = DCW_BRIEF.format(intro=t["intro"], purpose=purpose,
                                              clean=t["clean"], stem=t["stem"])
        meta.append(dict(
            id=f"dcw_{n}", n=n, domain="Data wrangling", benchmark="AutoDCWorkflow",
            attachments=[t["stem"] + ".csv"],
            label=t["label"],
            dropped=t.get("dropped", []),
            source=f"AutoDCWorkflow (arXiv:2412.06724) purpose {t['pid']}, "
                   f"{len(table):,}-char dirty table, gold clean table and "
                   f"recipe in LanLi2017/LLM4DC",
            note=t["note"], tested=t["tested"]))
        log(f"            task {n}: {t['stem']}.csv, {len(table):,} chars, "
            f"purpose {t['pid']}")


def build_longweave(meta, briefs):
    log("LongWeave   streaming until the two AP-style rows are found…")
    found = []
    with urllib.request.urlopen(LONGWEAVE_URL, timeout=1800) as r:
        read = 0
        for line in r:
            read += len(line)
            if f'"{LONGWEAVE_TASK}"'.encode() not in line:
                continue
            found.append(json.loads(line))
            log(f"            found {len(found)}/2 after {read/1e6:.0f} MB")
            if len(found) == 2:
                break
    if len(found) < 2:
        raise RuntimeError("fewer than two LongWeave AP-style 2k rows found")

    for i, row in enumerate(found):
        n = 5 + i
        first = i == 0
        prompt, dropped = drop_blocks(row["prompt"], LONGWEAVE_DROP.get(n, []), n)
        title = prompt.split("titled '")[1].split("'")[0] if "titled '" in prompt else "untitled"
        # "Around 2048 words" made the extractor open every run with a
        # clarification question about what "around" means. The study session
        # should not start with a question the brief can answer itself.
        briefs[f"longweave_{n}"] = (
            prompt.strip() + "\n\nWrite the article to article.md. For the "
            "target word count, treat \"around 2048 words\" as between 2,000 "
            "and 2,048 words — text beyond 2,048 words is cut off before "
            "grading.")
        meta.append(dict(
            id=f"longweave_{n}", n=n, domain="Writing", benchmark="LongWeave",
            label=f"LongWeave {n-4} — {title[:44]}",
            dropped=dropped,
            source=f"LongWeave AP_STYLE_WRITING/2k (arXiv:2510.24345), MIT, "
                   f"{len(row['metadata']['statements'])} statements",
            note=("Overrun is discarded before scoring, so recall and the 2,048-word "
                  "budget genuinely compete. This instance produced the regression "
                  "cycle in the pilot." if first else
                  "Second instance at the same tier — same mechanics, different "
                  "content, so the pair isolates content from task design."),
            tested=first))
        log(f"            task {n}: {title[:50]}, "
            f"{len(row['metadata']['statements'])} statements"
            + (f" ({len(dropped)} style rule(s) dropped)" if dropped else ""))


def main():
    os.makedirs(TASKS, exist_ok=True)
    meta, briefs = [], {}
    try:
        build_codeif(meta, briefs)
        build_dcw(meta, briefs)
        build_longweave(meta, briefs)
    except Exception as e:                                   # noqa: BLE001
        print(f"\nfailed: {type(e).__name__}: {e}", file=sys.stderr)
        print("Nothing was written. The app still runs; the picker stays hidden.",
              file=sys.stderr)
        return 1

    for m in meta:
        text = briefs[m["id"]]
        m["chars"], m["words"] = len(text), len(text.split())
        with open(os.path.join(TASKS, m["id"] + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(TASKS, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    log(f"\nwrote {len(meta)} tasks to {os.path.relpath(TASKS, os.getcwd())}/")
    for m in meta:
        log(f"  Task {m['n']}  {m['benchmark']:11} {m['words']:>5}w  {m['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
