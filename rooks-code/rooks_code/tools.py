"""Built-in tools. Each one is prepared first (arguments checked, a preview such as a diff built) and applied only after
approval, so what you approve is exactly what runs. File tools are confined to the project folder."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
             ".rooks", "dist", "build", ".next", ".tox", "target"}
MAX_OUTPUT = 20_000


class ToolError(Exception):
    """Bad arguments or a tool that cannot run; the message goes back to the model so it can correct itself."""


@dataclass
class Plan:
    effect: str                       # read | write | command | external
    summary: str                      # one line for the approval prompt and the receipt
    preview: str                      # diff or command shown before approval
    apply: Callable[[], tuple[str, bool | None, str]]  # -> (result for the model, verified, evidence)
    path: Path | None = None          # file to snapshot before applying (writes)


SPECS = [
    {"name": "read_file", "description": "Read a text file in the project. Returns numbered lines.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Path relative to the project root"},
         "offset": {"type": "integer", "description": "First line to read, 1-based (default 1)"},
         "limit": {"type": "integer", "description": "Number of lines (default 400)"}}, "required": ["path"]}},
    {"name": "list_files", "description": "List files under a folder, optionally filtered by a glob such as '*.py'.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Folder relative to the project root (default '.')"},
         "pattern": {"type": "string", "description": "Glob on the file name or relative path (default all)"}}}},
    {"name": "search", "description": "Search file contents with a regular expression. Returns file:line: text.",
     "parameters": {"type": "object", "properties": {
         "pattern": {"type": "string", "description": "Python regular expression"},
         "path": {"type": "string", "description": "Folder or file to search (default '.')"},
         "glob": {"type": "string", "description": "Only files matching this glob, e.g. '*.ts'"},
         "ignore_case": {"type": "boolean"}}, "required": ["pattern"]}},
    {"name": "edit_file", "description": "Replace exact text in a file. old_text must appear exactly once unless "
                                         "replace_all is true. Read the file first so old_text matches exactly.",
     "parameters": {"type": "object", "properties": {
         "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
         "replace_all": {"type": "boolean"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "write_file", "description": "Create a file or overwrite it with the given content.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "run_command", "description": "Run a shell command in the project root and return its exit code and output. "
                                           "Use it to run tests, builds, git and other CLIs.",
     "parameters": {"type": "object", "properties": {
         "command": {"type": "string"}, "timeout": {"type": "integer", "description": "Seconds (default 120)"}},
         "required": ["command"]}},
]
NAMES = {s["name"] for s in SPECS}


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def diff(old: str, new: str, rel: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 f"a/{rel}" if old else "/dev/null", f"b/{rel}")
    out = "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in lines)
    return out or "(no change)\n"


class Toolbox:
    def __init__(self, root: Path, command_timeout: int = 120):
        self.root = root.resolve()
        self.command_timeout = command_timeout

    def _path(self, value, must_exist: bool = False, writing: bool = False) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ToolError("path is required")
        p = (self.root / os.path.expanduser(value)).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"{value} is outside the project folder {self.root}; only files inside it can be used")
        if writing and (p.relative_to(self.root).parts[:1] in ((".rooks",), (".git",))):
            raise ToolError(f"{value} is managed by rooks/git and cannot be written by the agent")
        if must_exist and not p.is_file():
            raise ToolError(f"{value} does not exist (or is not a file)")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix() or "."

    def _walk(self, base: Path):
        if base.is_file():
            yield base
            return
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for f in sorted(filenames):
                yield Path(dirpath) / f

    def prepare(self, name: str, a: dict) -> Plan:
        fn = getattr(self, f"_{name}", None)
        if name not in NAMES or fn is None:
            raise ToolError(f"unknown tool {name}")
        return fn(a)

    # read tools -----------------------------------------------------------------------------------------------
    def _read_file(self, a: dict) -> Plan:
        p = self._path(a.get("path"), must_exist=True)
        offset, limit = max(1, int(a.get("offset") or 1)), max(1, min(2000, int(a.get("limit") or 400)))

        def run():
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                raise ToolError(f"{a['path']} is not a UTF-8 text file") from None
            chunk = lines[offset - 1: offset - 1 + limit]
            body = "\n".join(f"{i:>5}  {l}" for i, l in enumerate(chunk, offset))
            more = f"\n... ({len(lines) - (offset - 1 + len(chunk))} more lines; use offset)" if offset - 1 + len(chunk) < len(lines) else ""
            return _clip(body + more) or "(empty file)", None, ""
        return Plan("read", f"read {self.rel(p)}", "", run)

    def _list_files(self, a: dict) -> Plan:
        base = self._path(a.get("path") or ".")
        pattern = a.get("pattern") or "*"

        def run():
            if not base.exists():
                raise ToolError(f"{a.get('path')} does not exist")
            out = []
            for f in self._walk(base):
                r = self.rel(f)
                if fnmatch.fnmatch(f.name, pattern) or fnmatch.fnmatch(r, pattern):
                    out.append(r)
                    if len(out) >= 500:
                        out.append("... (first 500 shown)")
                        break
            return "\n".join(out) or "(no files)", None, ""
        return Plan("read", f"list {self.rel(base)} {pattern}", "", run)

    def _search(self, a: dict) -> Plan:
        try:
            rx = re.compile(str(a.get("pattern") or ""), re.I if a.get("ignore_case") else 0)
        except re.error as e:
            raise ToolError(f"bad regular expression: {e}") from None
        base, glob = self._path(a.get("path") or "."), a.get("glob")

        def run():
            hits = []
            for f in self._walk(base):
                if glob and not fnmatch.fnmatch(f.name, glob) and not fnmatch.fnmatch(self.rel(f), glob):
                    continue
                try:
                    with open(f, encoding="utf-8") as fh:
                        for i, line in enumerate(fh, 1):
                            if rx.search(line):
                                hits.append(f"{self.rel(f)}:{i}: {line.rstrip()[:300]}")
                                if len(hits) >= 200:
                                    return "\n".join(hits) + "\n... (first 200 matches)", None, ""
                except (UnicodeDecodeError, OSError):
                    continue
            return "\n".join(hits) or "(no matches)", None, ""
        return Plan("read", f"search {rx.pattern!r} in {self.rel(base)}", "", run)

    # write tools ----------------------------------------------------------------------------------------------
    def _write_plan(self, p: Path, old: str, new: str, verb: str) -> Plan:
        rel = self.rel(p)

        def run():
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            back = p.read_text(encoding="utf-8")
            if back != new:
                return f"{rel} was written but reads back differently", False, f"{rel} content does not match"
            added = sum(1 for l in diff(old, new, rel).splitlines() if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff(old, new, rel).splitlines() if l.startswith("-") and not l.startswith("---"))
            ev = f"{rel} read back: {len(new.splitlines())} lines, sha256 {_sha(new)} (+{added} -{removed})"
            return f"{verb} {rel} (+{added} -{removed})", True, ev
        return Plan("write", f"{verb} {rel}", diff(old, new, rel), run, path=p)

    def _edit_file(self, a: dict) -> Plan:
        p = self._path(a.get("path"), must_exist=True, writing=True)
        old_text, new_text = a.get("old_text"), a.get("new_text")
        if not isinstance(old_text, str) or not isinstance(new_text, str) or not old_text:
            raise ToolError("old_text (non-empty) and new_text are required strings")
        text = p.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            raise ToolError(f"old_text was not found in {a['path']}; read the file and copy the text exactly")
        if count > 1 and not a.get("replace_all"):
            raise ToolError(f"old_text appears {count} times in {a['path']}; add surrounding lines or set replace_all")
        new = text.replace(old_text, new_text) if a.get("replace_all") else text.replace(old_text, new_text, 1)
        return self._write_plan(p, text, new, "edited")

    def _write_file(self, a: dict) -> Plan:
        p = self._path(a.get("path"), writing=True)
        content = a.get("content")
        if not isinstance(content, str):
            raise ToolError("content is required")
        if p.is_dir():
            raise ToolError(f"{a['path']} is a folder")
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        return self._write_plan(p, old, content, "wrote" if p.exists() else "created")

    def _run_command(self, a: dict) -> Plan:
        cmd = a.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            raise ToolError("command is required")
        timeout = max(1, min(3600, int(a.get("timeout") or self.command_timeout)))

        def run():
            try:
                r = subprocess.run(cmd, shell=True, cwd=self.root, capture_output=True, text=True, timeout=timeout,
                                   encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or "") + (e.stderr or "") if isinstance(e.stdout, str) else ""
                raise ToolError(f"timed out after {timeout}s\n{_clip(out)}") from None
            out = (r.stdout or "") + (("\n" if r.stdout and r.stderr else "") + r.stderr if r.stderr else "")
            return f"exit code {r.returncode}\n{_clip(out)}", None, f"exit code {r.returncode}"
        shell = "cmd.exe" if sys.platform == "win32" else "sh"
        return Plan("command", f"run: {cmd}", f"$ {cmd}    ({shell}, cwd {self.root}, timeout {timeout}s)", run)
