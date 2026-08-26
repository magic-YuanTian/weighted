"""Workspace tools — the agent's only way to affect the world.

Tier-0 enforcement lives here (AGENT_UI_DESIGN.md §5): a `preserve` requirement
with ``enforce: true`` makes the edit tools *refuse* writes that destroy a
protected phrase. Nothing downstream has to trust the model about it — the tool
returns a rejection observation and the file is untouched.
"""

import difflib
import os
import re

from . import requirements as R

MAX_READ = 20000

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
            "description": "Read a workspace file in full.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "e.g. cover_letter.md"}},
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
        return sorted(f for f in os.listdir(self.root)
                      if os.path.isfile(os.path.join(self.root, f)))

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
        with open(self.path(name), "w", encoding="utf-8") as fh:
            fh.write(content)

    def meta(self):
        out = []
        for f in self.list():
            text = self.read(f) or ""
            lines = text.count("\n") + 1 if text else 0
            out.append({"name": f, "chars": len(text), "lines": lines,
                        "preview": text[:400]})
        return out


def word_count(text):
    return len(re.findall(r"\S+", text or ""))


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
            text = ws.read(args.get("path"))
            if text is None:
                return (f"no such file: {args.get('path')}. Files: {', '.join(ws.list()) or 'none'}",
                        {"ok": False, "kind": "read"})
            return text[:MAX_READ], {"ok": True, "kind": "read", "path": args.get("path")}

        if name in ("write_file", "edit_file"):
            path = args.get("path")
            before = ws.read(path) or ""
            if name == "write_file":
                after = args.get("content") or ""
            else:
                old, new = args.get("old_str") or "", args.get("new_str") or ""
                hits = before.count(old)
                if not old:
                    return "edit_file needs a non-empty old_str", {"ok": False, "kind": "edit", "path": path}
                if hits == 0:
                    return (f"old_str not found in {path} — read the file again and copy the "
                            "passage exactly", {"ok": False, "kind": "edit", "path": path})
                if hits > 1:
                    return (f"old_str occurs {hits} times in {path} — include more surrounding "
                            "text so it is unique", {"ok": False, "kind": "edit", "path": path})
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
