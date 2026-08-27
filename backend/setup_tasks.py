"""Fetch the six benchmark tasks and build them into backend/tasks/.

The tasks are not committed. Two of the three source benchmarks ship without a
licence file, so this repository does not redistribute them — it fetches them
from their own publishers and assembles the briefs locally. Run once:

    python setup_tasks.py

Afterwards the picker in the composer offers the six tasks. Without them the
/api/agent/presets endpoint returns an empty list and the picker hides itself,
so the app works either way.

Sources
    CodeIF       github.com/lin-rany/codeIF          (no licence file)
    T2R-bench    hf.co/datasets/Tele-AI/TeleTableBench (no licence file)
    LongWeave    hf.co/datasets/zikaixiao1/LongWeave   (MIT)

Two of the three archives are large. Neither is downloaded whole: the T2R
tables are pulled out of a 234 MB zip through range requests over its central
directory, and the LongWeave file is streamed only as far as the rows we need.
"""

import json
import os
import struct
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(HERE, "tasks")
DATA = os.path.join(TASKS, "data")

CODEIF_URL = ("https://raw.githubusercontent.com/lin-rany/codeIF/master/"
              "data/question/final_release_1200.jsonl")
T2R_BASE = "https://huggingface.co/datasets/Tele-AI/TeleTableBench/resolve/main/data/"
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
T2R_TABLES = ["metainputs_transmission_distribution",
              "2020_MTA_Metro_North_On_Time_Performance_Data"]
T2R_KEYPOINTS = [6, 10]          # which query to take for each table
LONGWEAVE_TASK = "longweave/AP_STYLE_WRITING/2k"


def log(msg):
    print(msg, flush=True)


def fetch(url, timeout=600):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def fetch_range(url, start, end, timeout=300):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def content_length(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=120) as r:
        return int(r.headers["Content-Length"])


# ----------------------------------------------------------------- zip by range

def zip_members(url, size):
    """Filename -> (offset, compressed size, method), read from the central
    directory alone. Avoids downloading a 234 MB archive for two small files."""
    tail = fetch_range(url, max(0, size - 70000), size - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise RuntimeError("zip end-of-central-directory not found")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    cd = fetch_range(url, cd_off, cd_off + cd_size - 1)
    out, p = {}, 0
    while p < len(cd) - 4 and cd[p:p + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", cd[p + 10:p + 12])
        comp, = struct.unpack("<I", cd[p + 20:p + 24])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        lho, = struct.unpack("<I", cd[p + 42:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        out[name] = (lho, comp, method)
        p += 46 + nlen + elen + clen
    return out


def zip_read(url, member):
    """One member's bytes, fetched by range and inflated locally."""
    import zlib
    offset, comp, method = member
    head = fetch_range(url, offset, offset + 29)
    nlen, elen = struct.unpack("<HH", head[26:30])
    body_at = offset + 30 + nlen + elen
    raw = fetch_range(url, body_at, body_at + comp - 1)
    if method == 0:
        return raw
    return zlib.decompress(raw, -zlib.MAX_WBITS)


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
        ins = "\n".join(f"{i+1}. {x['instruction']}" for i, x in enumerate(kept))
        for i in sorted(drop):
            log(f"            dropped #{i}: {drop[i]}")
        briefs[f"codeif_{qid}"] = (r["question"].strip() + "\n\nRequirements:\n" + ins
                                   + "\n\nWrite the solution to solution.py.")
        first = n == 1
        meta.append(dict(
            id=f"codeif_{qid}", n=n, domain="Code generation", benchmark="CodeIF",
            label=(f"CodeIF {n} — sentiment app, {len(kept)} constraints"
                   if first else
                   f"CodeIF {n} — frequency analysis, {len(kept)} constraints"),
            dropped=[{"index": i, "reason": drop[i],
                      "instruction": r["instruction_list"][i - 1]["instruction"]}
                     for i in sorted(drop)],
            source=f"CodeIF (ACL 2025 Industry) question_id {qid}, "
                   f"{r['meta_info']['item_set']} split, Python",
            note=("No for-loop but a while-loop is required, at most 2 classes, no "
                  "deque — satisfying one constraint tends to break another." if first else
                  "Same constraint count as the other CodeIF task but from a different "
                  "shipped split, so the pair separates difficulty from constraint count."),
            tested=first))
        log(f"            task {n}: question_id {qid}, {len(kept)} constraints"
            + (f" ({len(drop)} dropped as unsatisfiable)" if drop else ""))


T2R_BRIEF = """{query}

Write the report to report.md.

Report standards. Ground the analysis in the data provided. Support each
conclusion with data. Keep the structure logical, from problem definition
through to conclusions and recommendations. Keep it readable, and go beyond
review to give specific recommendations.

Requirements:
1. The report must be at least 1000 words.
2. Keep the content detailed, avoiding repetition and vague description.
3. If the data has gaps, adjust the analysis perspective rather than saying
   "insufficient data".

The data is attached as {name}.csv."""


def build_t2r(meta, briefs):
    log("T2R-bench   fetching queries…")
    rows = json.loads(fetch(T2R_BASE + "data_en.json").decode("utf-8"))

    zip_url = T2R_BASE + "table_en.zip"
    size = content_length(zip_url)
    log(f"            reading the {size/1e6:.0f} MB table archive by range…")
    members = zip_members(zip_url, size)

    os.makedirs(DATA, exist_ok=True)
    for i, stem in enumerate(T2R_TABLES):
        want_kp = T2R_KEYPOINTS[i]
        best = None
        for row in rows:
            path = row["file_path"]
            query, kp = row["question"], row["reference_key_points"]
            if os.path.basename(path.rstrip("/")) != stem:
                continue
            n_kp = len([x for x in kp.split("Key Point") if x.strip()])
            if best is None or abs(n_kp - want_kp) < abs(best[1] - want_kp):
                best = (query, n_kp)
        if best is None:
            raise RuntimeError(f"T2R table {stem} not found in data_en.json")
        query, n_kp = best

        name = next((m for m in members
                     if m.endswith(f"{stem}/{stem}.csv")), None)
        if name is None:
            raise RuntimeError(f"T2R table {stem}.csv not found in the archive")
        table = zip_read(zip_url, members[name]).decode("utf-8", "replace")
        with open(os.path.join(DATA, f"{stem}.csv"), "w", encoding="utf-8") as fh:
            fh.write(table)

        n = 3 + i
        first = i == 0
        briefs[f"t2r_{n}"] = T2R_BRIEF.format(query=query, name=stem)
        meta.append(dict(
            id=f"t2r_{n}", n=n, domain="Data wrangling", benchmark="T2R-bench",
            attachments=[f"{stem}.csv"],
            label=(f"T2R-bench {n-2} — transmission costs, {n_kp} keypoints" if first
                   else f"T2R-bench {n-2} — rail punctuality, {n_kp} keypoints"),
            source=f"T2R-bench (arXiv:2508.19813) {stem}, "
                   f"{len(table):,}-char table, {n_kp} keypoints",
            note=("'Never say insufficient data' bans the honest escape hatch; the "
                  "1000-word floor fights the no-padding rule on a 3.5 KB table."
                  if first else
                  "The largest table still inside the 20,000-character read limit, and "
                  "the most keypoints in the pool — coverage competes with the floor."),
            tested=first))
        log(f"            task {n}: {stem}.csv, {len(table):,} chars, {n_kp} keypoints")


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
        prompt = row["prompt"]
        title = prompt.split("titled '")[1].split("'")[0] if "titled '" in prompt else "untitled"
        briefs[f"longweave_{n}"] = prompt.strip() + "\n\nWrite the article to article.md."
        meta.append(dict(
            id=f"longweave_{n}", n=n, domain="Writing", benchmark="LongWeave",
            label=f"LongWeave {n-4} — {title[:44]}",
            source=f"LongWeave AP_STYLE_WRITING/2k (arXiv:2510.24345), MIT, "
                   f"{len(row['metadata']['statements'])} statements",
            note=("Overrun is discarded before scoring, so recall and the 2,048-word "
                  "budget genuinely compete. This instance produced the regression "
                  "cycle in the pilot." if first else
                  "Second instance at the same tier — same mechanics, different "
                  "content, so the pair isolates content from task design."),
            tested=first))
        log(f"            task {n}: {title[:50]}, "
            f"{len(row['metadata']['statements'])} statements")


def main():
    os.makedirs(TASKS, exist_ok=True)
    meta, briefs = [], {}
    try:
        build_codeif(meta, briefs)
        build_t2r(meta, briefs)
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
