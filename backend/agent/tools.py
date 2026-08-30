"""Workspace tools — the agent's only way to affect the world.

Tier-0 enforcement lives here (AGENT_UI_DESIGN.md §5): a `preserve` requirement
with ``enforce: true`` makes the edit tools *refuse* writes that destroy a
protected phrase. Nothing downstream has to trust the model about it — the tool
returns a rejection observation and the file is untouched.

`run_command` is the one tool that can write without going through that check —
a shell redirect goes straight round it — so it does not get to. Every command
is bracketed by a workspace snapshot and `absorb()` folds whatever it wrote back
into the same guarantee: a file that lost a protected phrase is put back. The
contract the edit tools state as "the file is unchanged" is enforced here one
moment later instead, which is the most a shell allows.
"""

import difflib
import os
import re
import subprocess

from . import requirements as R

MAX_READ = 20000
MAX_OUTPUT = 6000
SHELL_TIMEOUT = int(os.environ.get("WEIGHTTEXT_SHELL_TIMEOUT", "120"))


def shell_enabled():
    """A shell in the workspace is a shell on this machine. Local development
    gets it by default; the moment WEIGHTTEXT_PASSWORD turns the app into
    something reachable over a tunnel (see the README), the same tool is
    remote code execution for everyone holding the password, so it goes off
    unless the operator says otherwise in so many words."""
    setting = os.environ.get("WEIGHTTEXT_SHELL")
    if setting is not None:
        return setting.lower() not in ("0", "false", "no", "off")
    return not os.environ.get("WEIGHTTEXT_PASSWORD")


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the files in the workspace with their word counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_attachment",
            "description": ("Read a file the user attached (reference material — data "
                            "tables, source documents). Attachments are read-only and are "
                            "never part of the deliverable: the checker does not see them, "
                            "so nothing you read here counts towards a word limit."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "e.g. sales_2023.csv"},
                    "offset": {"type": "integer",
                               "description": "character offset, for a file too large to read at once"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": ("Read a workspace file. Lines come back numbered, so an "
                            "anchor for edit_file can be copied rather than recalled, "
                            "and insert_line can name a real place."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "e.g. cover_letter.md"},
                    "view_range": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": ("optional [start, end], 1-indexed; end -1 reads "
                                        "to the last line"),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": ("Create a file or replace its entire contents. Start every "
                            "deliverable with a '# Title' heading line."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": ("Replace one exact, unique passage of a file. Prefer this over "
                            "write_file for revisions: it cannot disturb text you did not name."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string", "description": "exact text to replace, must occur exactly once"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_file",
            "description": ("Insert text into a file at a line, without touching what is "
                            "already there. This is how you add: edit_file only replaces."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "insert_line": {
                        "type": "integer",
                        "description": ("the line number to insert AFTER; 0 puts the text "
                                        "at the top, the last line number appends"),
                    },
                    "insert_text": {"type": "string"},
                },
                "required": ["path", "insert_line", "insert_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command in the workspace directory. Reach for this when a "
                "program does the job better than retyping text does: transforming a "
                "data table, computing a figure you would otherwise estimate, running "
                "code you just wrote. python3 and the usual text tools are available; "
                "pandas is not, and for a small table you do not want it — the standard "
                "library's csv module leaves the columns you were told not to touch "
                "exactly as they were. $ATTACHMENTS holds the read-only attachments and "
                "$SCRATCH is where helper scripts and intermediates belong: every file "
                "in the workspace itself is a deliverable and is checked as one. Make "
                "one repair per command."),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "e.g. cp \"$ATTACHMENTS/sales.csv\" cleaned.csv"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_check",
            "description": ("Run the deterministic requirement checker over the workspace and "
                            "read the report. Costs nothing and never lies — run it before you finish."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": ("Declare the task complete. Rejected while a requirement is "
                            "violated, partially met, or stale."),
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "one sentence on what you delivered"}},
                "required": ["summary"],
            },
        },
    },
]


def schemas():
    """The tools offered for the next action. A disabled shell is withheld
    rather than offered and refused — a tool the model can see is a tool it
    plans around, and it should not plan around one that never works."""
    if shell_enabled():
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] != "run_command"]


SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class Workspace:
    """A flat directory of text files. Flat on purpose — the deliverables of a
    writing task are files, not a tree, and a flat namespace keeps scope
    resolution ('file: cover_letter.md') unambiguous."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, name):
        name = (name or "").strip().lstrip("./")
        if not SAFE_NAME.match(name):
            raise ValueError(f"invalid file name {name!r} (use a flat name like cover_letter.md)")
        return os.path.join(self.root, name)

    def list(self):
        # Dotfiles are never deliverables — a shell leaves them around and the
        # checker, the judge and the UI file list should all ignore them.
        return sorted(f for f in os.listdir(self.root)
                      if os.path.isfile(os.path.join(self.root, f))
                      and not f.startswith("."))

    def read(self, name):
        p = self.path(name)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()

    def write(self, name, content):
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(content)

    def snapshot(self):
        return {f: self.read(f) for f in self.list()}

    def make_writable(self):
        """Every file in here must stay ours to edit. A command can leave one
        that is not: `cp` gives the copy the source's permission bits, and the
        attachments are deliberately read-only, so the very first move of a
        wrangling task — copy the source, then repair the copy — otherwise
        lands a 0444 deliverable and every repair after it dies on
        PermissionError."""
        for name in self.list():
            p = self.path(name)
            mode = os.stat(p).st_mode & 0o777
            if not mode & 0o200:
                os.chmod(p, mode | 0o600)


class Attachments:
    """Read-only reference material, deliberately not the workspace.

    Kept in a separate directory because verifier.build_document() concatenates
    every workspace file into the document that requirements are checked
    against. A 19 KB data table living there would be counted by every global
    length check and searched by every banned-phrase check. The agent can read
    these; nothing else can see them."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def path(self, name):
        name = (name or "").strip().lstrip("./")
        if not SAFE_NAME.match(name):
            raise ValueError(f"invalid attachment name {name!r}")
        return os.path.join(self.root, name)

    def list(self):
        return sorted(f for f in os.listdir(self.root)
                      if os.path.isfile(os.path.join(self.root, f)))

    def read(self, name):
        p = self.path(name)
        if not os.path.exists(p):
            return None
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def add(self, name, content):
        p = self.path(name)
        if os.path.exists(p):
            os.chmod(p, 0o644)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(content)
        # Under a sandboxed engine "read-only" was a property of the sandbox.
        # With a shell running in the workspace next door it has to be a
        # property of the file: an agent that cleans the table in place rather
        # than copying it would otherwise destroy the only record of what the
        # source said, and every later check would compare clean against clean.
        os.chmod(p, 0o444)

    def meta(self):
        out = []
        for f in self.list():
            text = self.read(f) or ""
            lines = text.count("\n") + 1 if text else 0
            out.append({"name": f, "chars": len(text), "lines": lines,
                        "preview": text[:400]})
        return out


def _lines(text):
    """A file's lines, and whether it ended with a newline. Splitting on "\n"
    leaves a phantom empty last line for every well-formed file; numbering it
    invites an insert after a line that is not there."""
    lines = (text or "").split("\n")
    trailing = bool(lines) and lines[-1] == ""
    return (lines[:-1] if trailing else lines), trailing


def _numbered(lines, first=1):
    """Lines with their numbers, the way the Anthropic text editor tool returns
    a view. They do two jobs: insert_line has something real to name, and an
    edit anchor gets copied off a listing instead of recalled from memory --
    which is where most failed edits came from."""
    if not lines:
        return "(empty file)"
    width = len(str(first + len(lines) - 1))
    return "\n".join(f"{first + i:>{width}}: {line}" for i, line in enumerate(lines))


def _nearest(haystack, needle, window=320):
    """The passage in the file most like the one the agent asked for.

    A bare "not found" sends the agent back to re-read and guess again, which
    it does badly: it re-reads and then submits the same invented anchor. What
    it needs is the text that is actually there, so it can copy it.
    """
    m = difflib.SequenceMatcher(None, haystack, needle, autojunk=False)
    match = m.find_longest_match(0, len(haystack), 0, len(needle))
    if match.size < 12:
        head = "\n".join(haystack.split("\n")[:8])
        return ("Nothing in the file resembles it. The file begins:\n"
                f"---\n{head[:window]}\n---\n"
                "Copy an exact passage from the file, or use write_file.")
    start = max(0, match.a - 40)
    end = min(len(haystack), match.a + match.size + 40)
    return ("The closest passage actually in the file is:\n"
            f"---\n{haystack[start:end][:window]}\n---\n"
            "Copy it exactly, including punctuation and any curly quotes.")


def _occurrences(haystack, needle, limit=4, context=60):
    """Each place the ambiguous anchor matched, with its surroundings."""
    out, start, n = [], 0, 0
    while n < limit:
        i = haystack.find(needle, start)
        if i < 0:
            break
        a = max(0, i - context)
        b = min(len(haystack), i + len(needle) + context)
        out.append(f"  {n + 1}. …{haystack[a:b]}…".replace("\n", " "))
        start = i + 1
        n += 1
    return "\n".join(out)


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


def line_count(text):
    """The last line number insert_file will accept for this text — the same
    count the tool reports, from the same helper, so the digest and the error
    message can never disagree about where the end of the file is."""
    return len(_lines(text)[0])


def _diff_stat(before, after):
    b, a = (before or "").splitlines(), (after or "").splitlines()
    add = dele = 0
    for line in difflib.ndiff(b, a):
        if line.startswith("+ "):
            add += 1
        elif line.startswith("- "):
            dele += 1
    return add, dele


def _tier0_violations(session, before, after):
    """Protected phrases present before an edit must survive it."""
    lost = []
    for phrase in R.protected_phrases(session.requirements):
        if phrase and phrase in (before or "") and phrase not in (after or ""):
            lost.append(phrase)
    return lost


def absorb(session, before):
    """Fold whatever a command wrote into the guarantees an edit tool gives.

    `before` is a workspace snapshot taken just before the command ran. Every
    file that differs from it is either a write that stands or, if it dropped a
    protected phrase, a write that gets undone — the same contract edit_file
    states as a refusal, enforced one moment later because a shell cannot be
    asked first. This is the single funnel for shell writes, so a python script
    that rewrites a table is checked exactly like a patch would have been.

    Returns (changed, restored): changed is [(name, added, deleted)] for the
    writes that stood, restored is [(name, [lost phrases])] for the ones undone.
    """
    changed, restored = [], []
    session.workspace.make_writable()
    present = set(session.workspace.list())
    for rel in sorted(present | set(before)):
        after = session.workspace.read(rel) if rel in present else None
        prior = before.get(rel)
        if after == prior:
            continue
        lost = _tier0_violations(session, prior, after)
        if lost:
            if prior is None:
                try:
                    os.remove(session.workspace.path(rel))
                except OSError:
                    pass
            else:
                session.workspace.write(rel, prior)
            restored.append((rel, lost))
            continue
        add, dele = _diff_stat(prior, after)
        changed.append((rel, add, dele))
    return changed, restored


def _run_command(session, command):
    """Execute one command in the workspace and report what it did to it."""
    os.makedirs(session.scratch, exist_ok=True)
    # The server's own secrets are not the agent's to hold. OPENAI_API_KEY and
    # WEIGHTTEXT_PASSWORD are both in this process's environment, and a command
    # output goes into the observation, the event log and session.json — which
    # is the file the study data gets shared as. Nothing a command needs to do
    # here wants either of them.
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)}
    # Named, not spelled out in the prompt: a path the agent has to retype is a
    # path it eventually retypes wrong, and these two are in every command that
    # transforms an attached source.
    env["ATTACHMENTS"] = session.attachments.root
    env["SCRATCH"] = session.scratch
    before = session.workspace.snapshot()

    timed_out = False
    try:
        proc = subprocess.Popen(command, shell=True, executable="/bin/bash",
                                cwd=session.workspace.root, env=env,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return f"could not start the command: {e}", {"ok": False, "kind": "error"}

    # The /pause route kills through this handle, so ⏸ interrupts a command
    # that is going to run for its whole timeout instead of reading as a dead
    # button. Cleared in `finally` — a stale handle would have pause killing
    # whatever process id landed there next.
    session._proc = proc
    try:
        output = proc.communicate(timeout=SHELL_TIMEOUT)[0] or ""
        code = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        output = proc.communicate()[0] or ""
        code, timed_out = -1, True
    finally:
        session._proc = None

    changed, restored = absorb(session, before)

    lines = [f"$ {command}"]
    if timed_out:
        lines.append(f"(killed after {SHELL_TIMEOUT}s — it was still running)")
    else:
        lines.append(f"(exit {code})")
    output = output.strip()
    if len(output) > MAX_OUTPUT:
        # The head is where a traceback's message and a listing's shape are.
        # Saying how much was dropped stops the agent reading a cut-off line
        # as the end of the data.
        output = (output[:MAX_OUTPUT]
                  + f"\n[output cut off — {len(output) - MAX_OUTPUT} more characters. "
                    "Narrow the command, or write the result to a file and read it.]")
    lines.append(output if output else "(no output)")
    for rel, lost in restored:
        quoted = ", ".join(f'"{p}"' for p in lost)
        lines.append(f"REJECTED by the workspace: the command's change to {rel} "
                     f"would have removed protected text ({quoted}). "
                     f"{rel} was put back as it was.")
    for rel, add, dele in changed:
        text = session.workspace.read(rel)
        lines.append(f"deleted {rel}" if text is None else
                     f"wrote {rel} ({word_count(text)} words, +{add} −{dele})")
    if not changed and not restored:
        lines.append("(the workspace is unchanged)")

    meta = {"ok": code == 0 and not restored and not timed_out,
            "kind": "edit" if changed else "command"}
    if restored:
        meta["blocked"] = "tier0"
    if len(changed) == 1:
        meta["path"] = changed[0][0]
    return "\n".join(lines), meta


def execute(session, name, args):
    """Run one tool call. Returns (observation_text, meta).

    meta carries what the UI needs to render the step: {ok, path, add, del,
    kind, blocked}. `finish` never completes here — the loop owns the gate.
    """
    ws = session.workspace
    try:
        if name == "list_files":
            files = ws.list()
            if not files:
                att = session.attachments.list()
                note = ("workspace is empty"
                        + (" — attached (read-only): " + ", ".join(att) if att else ""))
                return note, {"ok": True, "kind": "read"}
            lines = [f"{f}  ({word_count(ws.read(f))} words)" for f in files]
            att = session.attachments.list()
            if att:
                lines.append("attached (read-only, not part of the deliverable): "
                             + ", ".join(att))
            return "\n".join(lines), {"ok": True, "kind": "read"}

        if name == "read_attachment":
            att = session.attachments
            wanted = args.get("name")
            text = att.read(wanted)
            if text is None:
                have = ", ".join(att.list()) or "none"
                return (f"no such attachment: {wanted}. Attached: {have}",
                        {"ok": False, "kind": "read"})
            offset = max(0, int(args.get("offset") or 0))
            chunk = text[offset:offset + MAX_READ]
            more = offset + len(chunk)
            tail = ("" if more >= len(text) else
                    f"\n\n[{more} of {len(text)} characters shown — "
                    f"call read_attachment again with offset={more} for the rest]")
            return chunk + tail, {"ok": True, "kind": "read", "path": wanted}

        if name == "read_file":
            path = args.get("path")
            text = ws.read(path)
            if text is None:
                return (f"no such file: {path}. Files: {', '.join(ws.list()) or 'none'}",
                        {"ok": False, "kind": "read", "reason": "missing"})
            lines, _ = _lines(text)
            first, rng = 1, args.get("view_range")
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                try:
                    a, b = int(rng[0]), int(rng[1])
                except (TypeError, ValueError):
                    return (f"view_range must be two integers, got {rng!r}.",
                            {"ok": False, "kind": "read", "path": path,
                             "reason": "range"})
                first = max(1, a)
                last = len(lines) if b == -1 else min(len(lines), b)
                if first > len(lines):
                    return (f"view_range starts at {first} but {path} has "
                            f"{len(lines)} lines.",
                            {"ok": False, "kind": "read", "path": path,
                             "reason": "range"})
                lines = lines[first - 1:last]
            return _numbered(lines, first)[:MAX_READ], {"ok": True, "kind": "read",
                                                        "path": path}

        if name in ("write_file", "edit_file", "insert_file"):
            path = args.get("path")
            before = ws.read(path) or ""
            if name == "write_file":
                after = args.get("content") or ""
            elif name == "insert_file":
                if ws.read(path) is None:
                    return (f"no such file: {path}. Files: {', '.join(ws.list()) or 'none'}. "
                            "Use write_file to create it.",
                            {"ok": False, "kind": "edit", "path": path})
                lines, trailing = _lines(before)
                try:
                    at = int(args.get("insert_line"))
                except (TypeError, ValueError):
                    return (f"insert_file needs insert_line, the line number to insert "
                            f"after. 0 puts the text at the top; {len(lines)} appends. "
                            f"{path} has {len(lines)} lines.",
                            {"ok": False, "kind": "edit", "path": path})
                if not 0 <= at <= len(lines):
                    return (f"insert_line {at} is outside {path}, which has "
                            f"{len(lines)} lines. Use 0 for the top and {len(lines)} "
                            "to append.",
                            {"ok": False, "kind": "edit", "path": path})
                added, _ = _lines(args.get("insert_text") or "")
                after = "\n".join(lines[:at] + added + lines[at:]) + ("\n" if trailing else "")
            else:
                old, new = args.get("old_str") or "", args.get("new_str") or ""
                hits = before.count(old)
                if not old:
                    n = len(_lines(before)[0])
                    return ("edit_file replaces text; it cannot add any, so old_str "
                            f"cannot be empty. To add text use insert_file: insert_line=0 "
                            f"for the top of {path}, {n} to append at the end.",
                            {"ok": False, "kind": "edit", "path": path})
                if hits == 0:
                    return (f"old_str not found in {path}.\n{_nearest(before, old)}",
                            {"ok": False, "kind": "edit", "path": path})
                if hits > 1:
                    return (f"old_str occurs {hits} times in {path}. "
                            f"Extend it so it is unique — the occurrences are:\n"
                            + _occurrences(before, old),
                            {"ok": False, "kind": "edit", "path": path})
                after = before.replace(old, new, 1)

            lost = _tier0_violations(session, before, after)
            if lost:
                quoted = ", ".join(f'"{p}"' for p in lost)
                return (f"REJECTED by the workspace: this edit would remove protected text "
                        f"({quoted}). The file is unchanged. Rewrite around it.",
                        {"ok": False, "kind": "edit", "path": path, "blocked": "tier0"})

            ws.write(path, after)
            add, dele = _diff_stat(before, after)
            return (f"wrote {path} ({word_count(after)} words, +{add} −{dele})",
                    {"ok": True, "kind": "edit", "path": path, "add": add, "del": dele})

        if name == "run_command":
            if not shell_enabled():
                return ("The shell is switched off on this server. Use write_file, "
                        "edit_file and insert_file instead.",
                        {"ok": False, "kind": "error"})
            command = (args.get("command") or "").strip()
            if not command:
                return ("run_command needs a command to run.",
                        {"ok": False, "kind": "error"})
            return _run_command(session, command)

        if name == "run_check":
            from . import verifier
            reports = verifier.verify(session, judge=False)
            R.apply_report(session.requirements, reports)
            return verifier.report_text(session.requirements), {"ok": True, "kind": "check"}

        if name == "finish":
            return (args.get("summary") or "").strip(), {"ok": True, "kind": "finish"}

        return f"unknown tool {name}", {"ok": False, "kind": "error"}

    except ValueError as e:
        return str(e), {"ok": False, "kind": "error"}
    except Exception as e:                                   # noqa: BLE001
        return f"tool error: {type(e).__name__}: {e}", {"ok": False, "kind": "error"}
