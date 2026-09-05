# WeightText

A requirement-steered writing agent. The user states a task in chat; the system
extracts the task's requirements into an editable list, an agent works on the
task step by step, and every step is checked against the requirements — so the
user always sees what is met, what is not, and which step made it so.

## How it works

- **Extract** — the first chat message is parsed into a requirement list
  (length limits, banned/required phrases, structural rules, tone, process
  rules). Each requirement stays linked to the sentence it came from, and the
  list is fully editable: add, reword, delete. The run stops there and waits to
  be started — that first list is the thing the work will be measured against,
  and it should be read before there is work to read it against. Only there:
  every later message goes straight to an agent already working. (This is not
  the `#agent/review` condition, which is a separate screen before the session
  exists; this is the ordinary flow pausing once.)
- **Verify** — after every agent step, requirements are re-checked.
  Deterministic properties (word counts, phrases, structure) are checked by
  code; subjective ones (tone, content) are judged by the model. Every verdict
  carries evidence: a span in the document or a step in the log, one click away.
- **Steer** — highlight a requirement to have it repeated to the agent before
  every action; select text in the workspace to freeze it (edits that would
  remove it are rejected in the edit tool), or to ask for an anchored replace /
  insert. The finish gate holds the agent's "done" until everything checks out,
  and the loop pauses on its own when the agent has aimed three changes at the
  same requirement and it is still red — every change names the requirements
  it is for, so "tried three times and not managed" is counted from the
  agent's own word, and pressing continue starts the count over. At the pause
  the agent says, in one sentence, where it is stuck and what would unblock
  it; continue then acts at once — the model is told that the user has read
  the pause, so it does not answer the pause with a second message instead of
  a step (which is what the first press used to produce).

## Layout

    backend/
      server.py        Flask entry point (static build + API)
      llm_api.py       model access — the single place a model is wired in
      agent_routes.py  HTTP surface: /api/agent/*
      checker.py       deterministic text checks
      code_checker.py  deterministic checks on a Python deliverable
      table_checker.py deterministic checks on a CSV deliverable, read
                       against the source table it was made from
      setup_tasks.py   fetches the six benchmark tasks (see below)
      export_events.py flattens every run's event log into events.csv and
                       sessions.csv for analysis (see "Running the study")
      agent/           extraction, verification, tools, the step loop
      tasks/           the fetched tasks, their attachments and the
                       graders' gold answers (git-ignored)
      runs/            one directory per session: workspace, attachments,
                       and the event log (git-ignored)
    frontend/
      src/agent/       the three-pane UI: chat, workspace, requirements

## Setup

**Credentials.** Put your key in a `.env` at the repository root. It is
git-ignored; keep it that way.

    OPENAI_API_KEY=sk-...

`backend/llm_api.py` reads it from there and is the single place the model is
wired in. It targets `gpt-5.6-luna` by default; override with `WEIGHTTEXT_MODEL`
(and `OPENAI_BASE_URL` for an OpenAI-compatible gateway) rather than editing
the file. The **agent** — the model that plans and acts in the workspace — can
be put on a different model from the extractor and the judge with
`WEIGHTTEXT_AGENT_MODEL`; it defaults to the same one. (`gpt-5-nano` was tried
as a weaker agent and set aside: it needs `reasoning_effort` "low" to use its
tools at all and never names the requirements its edits are for.) Any client
satisfying the interface in that module's docstring works, but **the agent
model must support tool calling** — the agent has no other way to act.

**Backend** (Python 3.10+):

    uv venv --python 3.12 .venv                      # or: python -m venv .venv
    uv pip install --python .venv/bin/python -r backend/requirements.txt
    cd backend && ../.venv/bin/python server.py      # serves on :5091

**Frontend:**

    cd frontend
    npm install
    npm start                   # dev server on :3000, proxies /api to :5091

or `npm run build`, after which the backend serves the app on :5091 directly.

### The agent's terminal

The agent can run shell commands in its workspace, which is how the data tasks
get done properly: copy the attached source verbatim, then repair the copy one
column at a time with a real program, so the parts nobody asked it to change
stay byte-identical instead of being retyped from memory. Helper scripts belong
in `$SCRATCH` and the attachments are read-only at `$ATTACHMENTS` — every file
in the workspace itself is a deliverable and is checked as one.

A shell goes around the tool-level guarantees, so it does not get to keep what
it wrote unexamined. Every command is bracketed by a workspace snapshot: a file
that lost a protected phrase is put back — the same contract `edit_file` states
as a refusal, enforced one moment later because a shell cannot be asked first —
and whatever stands is verified by the step that ran it, so a `python3` script
that rewrites a table produces the same chips a hand edit would.

    WEIGHTTEXT_SHELL_TIMEOUT=120   # seconds one command may run (default)
    WEIGHTTEXT_SHELL=0             # withhold the tool entirely

On by default locally. Off by default as soon as `WEIGHTTEXT_PASSWORD` is set,
for the reason in the next section; `WEIGHTTEXT_SHELL=1` overrides that.

### Sharing it with other people

`server.py` serves the built frontend and the API from one port, so a single
process is the whole app. What it does not have is accounts: every run is a
long chain of large model calls billed to whoever owns `OPENAI_API_KEY`. Set a
shared password before the app is reachable from anywhere but this machine.

    export WEIGHTTEXT_PASSWORD='something-only-they-know'
    cd backend && ../.venv/bin/python server.py

Unset, there is no gate — local development behaves as it always did. Set, the
whole app answers 401 until the browser sends the password, over HTTP Basic,
so there is nothing to log into and no session to leak. Anyone with the
password can spend the key, so treat it as a key.

Setting it also withholds the agent's terminal, because a shell in the
workspace is a shell on this machine: reachable over a tunnel, with a password
shared among a few people, it is remote code execution for whoever holds that
password. The writing tasks do not need it. Turn it back on with
`WEIGHTTEXT_SHELL=1` only when you know who is on the other end.

On the same network that is already enough: the server binds `0.0.0.0`, so
others reach it at `http://<your-ip>:5091`. Leaving that network is the access
control.

For a link that works anywhere, put a tunnel in front of it rather than
opening a port on your router. The tunnel supplies HTTPS, which Basic auth
needs to not be sent in the clear:

    brew install cloudflared
    cloudflared tunnel --url http://localhost:5091

It prints a `https://<random>.trycloudflare.com` address. Send that and the
password. Stopping the tunnel withdraws the link immediately.

Two things this setup is not built for. Sessions live in `backend/runs/` on
local disk, so everyone shares one server and the work does not survive a
machine that goes to sleep. And a password shared among a few people is not a
substitute for per-user keys — for anything genuinely public, users need to
supply their own credentials and the API needs a rate limit.

### Running the study

Three settings, one screen, chosen by the URL and fixed for the life of a
session. The picker in the chat header numbers them and never names them;
the backend refuses any request a setting does not carry (409), so a stale
tab cannot hand a control participant half the treatment.

| URL | Setting | What the participant has |
|---|---|---|
| `#agent/s1` | 1 | WeightText: requirement list, verdicts with evidence, highlight, freeze, anchored replace/insert, direct editing, finish gate, stuck pause |
| `#agent/s2` | 2 | Chat baseline: the same agent and workspace, read-only; the message box is the only way to steer |
| `#agent/s3` | 3 | In-situ: the baseline plus anchored replace/insert and direct editing; no list, no verdicts, no freeze |

The three nest (2 ⊂ 3 ⊂ 1), so 1 − 3 measures the requirement object and
3 − 2 measures pointing at the text. On the server the same two questions
decide every branch: `session.verified` (is there a requirement layer) and
`session.steerable` (does the workspace answer to the user).

**Who and what.** Open `#agent/s1/p7` and the session records participant
`P7`; the token survives switching settings. Picking a benchmark task records
its id. Both land in `session.json` under `study`, so a run joins to a
questionnaire row without guessing from the brief.

**The clock and the hand-in.** The task begins at the first message; a clock
in the composer bar counts from the server's timestamp. *Hand in* (two
clicks) ends the task: the files as they stand are copied into a `submit`
event, and the server refuses every step, message, edit and steer after it.

    WEIGHTTEXT_SOFT_LIMIT=600    # seconds; the clock turns amber
    WEIGHTTEXT_HARD_LIMIT=720    # seconds; the client hands in on its own

Unset, there is a clock and a button and no limit.

**What is logged.** Every event carries a timestamp and lands in
`runs/<id>/session.json`: the agent's steps with their verdict chips, user
messages, anchored edits, hand edits, freezes, highlights, requirement
edits, clarification answers, requirement selections (and which pane they
came from), evidence jumps, step expansions, attachment previews, scrolling
per pane (counted, one event every three seconds), run/pause/continue, the
gate holding a finish, the stuck pause, and the hand-in. Then:

    cd backend && ../.venv/bin/python export_events.py

writes `backend/analysis/events.csv` (one row per event, with seconds since
the first message and the same as a fraction of the task) and
`sessions.csv` (one row per session with a count of every category) — the
inputs for a per-participant timeline and for actions-per-task tables.

### Benchmark tasks (optional)

The composer offers a picker of six evaluation tasks: three domains, two
instances each. They are not in this repository — two of the three source
benchmarks ship without a licence file, so nothing is redistributed here. Fetch
and assemble them once:

    cd backend && ../.venv/bin/python setup_tasks.py

| # | Task | Domain | Artifact | Reqs | Weighted agent, first halt or 10 steps (9 runs) | Plain agent from scratch (6 runs) | Recorded pre-run |
|---|------|--------|----------|------|------|------|------|
| 1 | CodeIF 358 | Code generation | longest subarray whose sum is not divisible by k (oracle: 60 cases) | 12 | right 6 of 18 recordings at the first halt; wrong in 12 (mostly 16–17 of 60 cases, always missing ([-5, 1, 0], k=4) → 2) | — | first **5 steps** of a 10-step run, cut mid-run: the judged correctness chip red, 16 of 60 cases wrong |
| 2 | CodeIF 1087 | Code generation | tilings of a 2 × N board with dominos and trominos, mod 1e9+7 (oracle: 10 cases) | 15 | right 1 of 6 recordings at the halt; 8–10 of 10 cases wrong otherwise | — | first **3 steps** of a 6-step run, cut before the fix: the judged correctness chip red, every case wrong |
| 3 | AutoDCWorkflow menu p25 | Data wrangling | 100 × 21 table, sponsor and currency dirty, three padded columns | 14 | complete 2 of 9; placeholders wrong or no answer in 7 | identical to gold 3 of 6 | 4 steps, **paused**: currency folds and trims not done, a lone ? left where [?] should be emptied, no answer yet; four chips red |
| 4 | AutoDCWorkflow menu p14 | Data wrangling | 100 × 20 table, sponsor and dish_count dirty | 12 | complete 3 of 9; the same two placeholder cells wrong in 6 | wrong in the same two cells 6 of 6 | 10 steps, **paused**: one sponsor cell left in mixed case with the uppercase chip red, plus a judged chip |
| 5 | LongWeave KG_TO_TEXT #8 | Writing | a 2,000-word biography from 100 triples | 6 | complete **0 of 9**: paused at 5–7, 6–16 of 99 fact objects absent | 0 of 6 | 6 steps, **paused**: 2,120 words with the budget chip red, 16 objects absent, a judged chip red |
| 6 | LongWeave KG_TO_TEXT #11 | Writing | a 2,000-word biography from 82 triples | 7 | complete **0 of 9**: paused at 4–6, 2–5 of 81 objects absent | 0 of 6 | 4 steps, **paused**: 2,182 words with the budget chip red, a judged chip red |

**Pre-run replay (sixth screen, 2026-09-05).** The weighted condition opens
on a recorded pre-run, played back through the ordinary flow: the participant
pastes the brief and sends it, the requirement list that was extracted at
recording time comes back as the extraction, "start the agent" hands out the
recorded steps one at a time at about the pace they were taken — each with
the files as they stood and the rail as it read afterwards — and the run
stops where the recording stopped: the agent's finish, a pause, or a reply.
From there the live agent continues, its transcript cut to the same point, so
it remembers exactly what the participant has just watched. Anything the
participant does that would have changed what the agent did — a message, a
steer, a hand edit, a change to the list, the gate, an answered question —
ends the replay early and hands the run to the live agent there; pausing only
waits. A session is matched to a recording by the brief's text
(`agent/replay.py`, hooked into `/message`, `/step` and `/recheck`), so the
picker, a paste from the printed sheet and a retyped brief all land on the
same recording, and the two controls, which have no recordings, start live.
The four table and writing recordings stop with at least one requirement red
and the deliverable wrong by ground truth — a run the agent had declared
finished with every chip green is not used, however wrong its answer — so the
participant inherits something the rail already disputes; all four stop on
the attempt-rule pause. The two code recordings (restored 2026-09-05, see
below) are prefixes instead: the first few steps of a weighted run, cut at an
unfinished point before the agent's finish; where the prefix ends the
auto-runner simply stops — nothing is said in the chat and the status stays
as recorded, so the seam is invisible and "Run" starts the live agent — and
at the cut the deliverable is wrong and a judged correctness chip is red in
both (a prefix with every chip green was ruled out as well). The rail shows
each requirement's wording whole, however long, rather than clamped to two
lines.
Recordings live in `backend/tasks/traces/<task id>/` — the run directory of
a weighted screening run made in recording mode (`trace.json`: the extraction
as committed and one group per agent turn with its events, files and rail;
`llm_messages.json`; `session.json`; `attachments/`; `scratch/`) plus a
`provenance.json` naming the run it came from — and, like the tasks, are not
committed. Three rules picked the six: the recorded pre-run
must not have completed the task and the weighted agent must not complete it
inside ten steps as a rule ("complete" by ground truth, with every chip
green); fewer than twenty requirements over every extraction seen; and the
plain agent, from scratch, must still get it wrong often. Nine weighted runs
and six plain runs per task, graded against oracle, gold table or word budget
plus a fact-object recall. The code pair and the biographies meet the first
rule outright. The tables do not, quite: with a terminal and the csv module a
hundred-row table with a handful of rules is a script, and every menu
instance screened (p12, p13, p17, p18, p20–p22, p24–p28) is completed by the
weighted agent in some runs — p25 and p14 least often, on the sponsor
placeholders the agent misreads — so for the tables the guarantee is the
recorded trace, chosen incomplete, and the rule holds two times in three.
Dropped this screen: CodeIF 137 (right at its finish in 3 of 3 recording
runs after 0 of 3 earlier), menu p18 (complete at weighted steps 4–6, 17–23
requirements); rejected: CodeIF 974, which the plain agent gets wrong 6 of 6
but the weighted agent writes in closed form at step 2.

**Code pair restored (2026-09-05).** The sixth screen's code tasks, CodeIF
808 and 891, were judged contrived (constraints like "define a class named
KmsDwa") and replaced, at the user's word, by the earlier pair from the third
screen, 358 and 1087. A survey of code benchmarks with natural requirements
came first — CIFE, IFEvalCode, NaturalCodeBench, BigCodeBench-Hard, ClassEval;
68 weighted recordings over 14 of their tasks — and found that on a fair
brief the weighted agent finishes a single-function task at step 3 with every
chip green, right or (on hidden test expectations) wrong; the only red-chip
halts were checker misfires (a default file name judged against the
workspace, doctest lines run without their setup). The rule for the code
pre-runs is therefore the prefix cut above, not the red-chip halt. The harness
grew `bcb:<n>` and `classeval:<k>` specs and test-suite graders along the way.

Three rules picked the pool the fifth screen drew from (fourth screen,
2026-09-04), in the order they were applied, and the third gave way to the
first two.

**Doable.** Every requirement in a brief can be met by one Python file or
one CSV in this workspace, and none contradicts another. CodeIF grafts its
instructions onto its questions, and some cannot be true here: a switch
statement, a package, an interface naming convention, a CamelCase rule beside
a required `min_gelu`. `setup_tasks.codeif_instructions` drops or restates
those under a generic policy — Python has no switch; the workspace is one flat
directory; the convention is for the names the author chooses — and every
edit is in the manifest with its reason. One table brief described its own
data wrongly (p26 put the day-before-month dates in the year-first format,
where the table has none) and was corrected; the "same nine cells wrong" that
had recommended it were the agent following the brief.

**Checked.** Every verdict a participant sees is one the checker can defend.
Where the judge was watched failing correct work, the check was made
deterministic or the routing fixed: `table_checker` now decides trimming
(`trimmed_copy`), folds and corrections and emptied placeholders
(`value_map`, matched against the source), collapsed spaces and padding,
banned characters and a column's format (`column_pattern`); the code checker
runs an example the brief states (`returns`), exempts dunders and the names
the brief itself demands from a naming convention, and no longer reads an
attachment's name as a deliverable's; the extractor no longer turns a
line-count cap into a line-length cap or a parameter cap into a function cap;
the judge is told that paraphrase, a date assembled from its fields, and
"married" for "spouse of" are not invented facts; and an extraction whose
reply is not JSON — one in six on the biographies, a stray glyph before the
closing brace — is asked again instead of leaving an empty rail.

**First stop.** The run's first halt should not be an accepted `finish`, and
should leave a requirement red. With the first two rules enforced this holds
for the biographies only. On a code or a table brief the agent can meet, with
a checker that tells it the truth, it finishes — by step 5 on the code, by
step 12 on the tables, and right two times in three. Every non-finish the
earlier screens had recorded on those domains turns out to have been a
contradiction in the brief or a verdict that was wrong: under the attempt rule
with the old checker and briefs, 137, 891, 1194, 358, p18, p26 and p28 all
stopped short, and repaired they all finish or are held only by false judge
chips over tables identical to gold. So the code and table pairs are chosen on
the second screen's rule instead — what the agent gets **wrong** at that
finish — and that is the honest description of what a participant meets
there: a run that has declared itself done, and a deliverable that is wrong
one time in three in a place no chip marks. The biographies keep the pause,
on a judged chip the agent aims at three times and cannot turn.

What the screen ruled out. 1194 and 358, once their contradictions were
removed, finish three times of three, right twice. p26, p27 (six dirty
columns, written for this screen) and p28 are repaired identically to gold in
every run and held, where they are held, by judged chips that are wrong. Six
CodeIF questions that state a worked example (316, 999, 165, 646, 703, 743)
were run with the new `returns` check: two finish, four stop on contradictions
the generic policy does not reach — a variable that "should be a constant" and
is the loop counter, "at most one class" beside two named classes. 703's first
draft failed the brief's own example, `compress_string` returning 8 for 6, and
the `returns` chip said so — the first time the app has been able to say what
a program returns.

The stuck pause counts attempts from the agent's own word: every change to a
file carries `targets`, the requirement ids it is aimed at, and three changes
aimed at one requirement that leave it violated pause the run, with the count
starting over when it turned satisfied in between and when the user presses
continue. It fires on the biographies; on the other four it has nothing to
count, because the chips go green.

**`backend/table_checker.py`** decides what a wrangling brief promises about its
source table by comparing the deliverable with the attachment: keep every
row, the order, the header; change only these columns; trim these and change
nothing else; fold these values onto that one; empty this placeholder;
uppercase, collapse, no semicolons left, every date in this shape; report this
count. It is the one check in the codebase that is handed source material,
which crosses the line `tools.Attachments` draws on purpose; a relational
promise is the only thing that cannot be checked on the other side of it. The
gold table is never involved. What is still the judge's on a table is the
answer file and the rules no property states — how an ambiguous date is read
— and the judge is still wrong about those often enough that a red chip on a
table should be read against the file before it is believed.

The code checker's naming rule exempts Python's own names (`__init__`,
`self`) and the identifiers the brief itself demands, so "function names in
CamelCase" beside "a function named min_gelu" is satisfiable and reads the
way every reader of the brief reads it. Its `returns` property runs one
example the brief states, in a scratch copy with a timeout, and is the only
check in the app that knows what a program returns; it runs only where the
agent's own shell runs. What is left is the largest open problem in the set:
nothing checks a program's answer to the task itself, and both code tasks
finish green on a wrong one a third of the time. The participant's terminal
is the reliable check.

Reference material is attached rather than pasted into the brief, so the
requirement checker never sees it — a table cannot be counted by a word limit,
and a hundred triples would be read as content by the extractor. Gold answers
land in `backend/tasks/gold/` and are never attached to a session — the agent
has no route to them through the tools, though its shell can still walk the
filesystem, so treat that directory as out of bounds rather than sealed.

This writes `backend/tasks/`, which is git-ignored. Instances are pinned — by
question id for CodeIF, by purpose id for AutoDCWorkflow, and for LongWeave by
ordinal within a (task, tier) group — so re-running reproduces the same six.
The 224 MB LongWeave file is streamed once and read only as far as the last row
needed; KG_TO_TEXT/2k sits early in it, so that is about 30 MB.

Skip this and the app still runs: `/api/agent/presets` returns an empty list
and the picker hides itself. The backend reads the directory per request, so
no restart is needed after running it — reload the page and the tasks are
there.

`setup_tasks.py`'s docstring carries the measurements these six were chosen on,
what each rejected candidate failed, and every edit made to a benchmark brief.

## Notes

- Sessions are held in memory and written to `backend/runs/<id>/session.json`
  as a full event log (steps, verdicts, user actions) for later analysis.
- `#dev` in the URL enables researcher tools: single-stepping, raw model
  observations, a context inspector, and session export.
- Reference material (a data table, a source document) is attached rather than
  pasted into the brief. Attachments live beside the workspace, not in it, so
  the requirement checker never sees them — a table cannot be counted by a word
  limit or searched by a banned-phrase rule. The agent reads them with
  `read_attachment`; the chat shows a file chip that opens the contents.
