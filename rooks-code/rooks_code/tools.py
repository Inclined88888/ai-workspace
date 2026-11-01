"""Built-in tools. Each one is prepared first (arguments checked, a preview such as a diff built) and applied only after
approval, so what you approve is exactly what runs. File tools are confined to the project folder."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
             ".rooks", "dist", "build", ".next", ".tox", "target"}
MAX_OUTPUT = 20_000
MAX_FILE_BYTES = 1_000_000       # skip files over 1 MB (usually generated, vendored or binary)
MAX_MATCHES = 200                # stop a search after this many hits
MAX_SCANNED = 5_000              # stop after opening this many files
SCAN_SECONDS = 10.0              # stop after this long, whatever the counts
MAX_LISTED = 500                 # stop list_files after this many rows


def is_home_or_root(root: Path) -> str:
    """'home' / 'root' when the folder is the user's home or a filesystem root, else ''."""
    try:
        r = root.resolve()
    except OSError:
        return ""
    if Path.home().resolve() == r:
        return "home"
    if r.parent == r:
        return "root"
    return ""


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


# --- gitignore -------------------------------------------------------------------------------------------------
# .gitignore semantics (gitignore(5)): patterns are relative to the folder the .gitignore sits in; a trailing / matches
# directories only; a leading / anchors to that folder; a middle slash anchors too; no slash matches the basename at any
# depth below it; ! negates and the last matching line wins. When a folder is ignored, everything under it is ignored.

def _match_one(rel: str, base: str, pattern: str) -> bool:
    """True when `rel` (path from the walk root) matches one gitignore `pattern` read from `base`/.gitignore.
    A pattern that names a folder also matches everything under that folder."""
    if base:
        if rel != base and not rel.startswith(base + "/"):
            return False
        rel = rel[len(base) + 1:]
    if rel == "":
        return False
    p = pattern.strip().rstrip("/")
    if not p or p.startswith("#") or p.startswith("!"):
        return False
    anchored = p.startswith("/") or "/" in p
    p = p.lstrip("/")
    if not p:
        return False
    if anchored:
        return rel == p or rel.startswith(p + "/") or fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(rel, p + "/*")
    name = rel.rsplit("/", 1)[-1]
    if fnmatch.fnmatchcase(name, p):
        return True
    return any(fnmatch.fnmatchcase(part, p) for part in rel.split("/")[:-1])


class Ignore:
    """The .gitignore files under a folder, consulted as git does: the nearest file's last matching line wins."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, list[tuple[str, bool, bool]]] = {}  # folder rel -> [(pattern, negated, dir_only)]

    def _rules(self, rel_dir: str) -> list[tuple[str, bool, bool]]:
        if rel_dir in self._cache:
            return self._cache[rel_dir]
        f = (self.root / rel_dir / ".gitignore") if rel_dir else (self.root / ".gitignore")
        rules: list[tuple[str, bool, bool]] = []
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                negated = s.startswith("!")
                if negated:
                    s = s[1:].strip()
                if s:
                    rules.append((s, negated, s.endswith("/")))
        except OSError:
            pass
        self._cache[rel_dir] = rules
        return rules

    def ignored(self, rel: str, is_dir: bool) -> bool:
        rel = rel.strip("/")
        if not rel:
            return False
        parts = rel.split("/")
        # an ignored parent folder ignores the whole subtree
        for i in range(1, len(parts)):
            if self._decide("/".join(parts[:i]), True):
                return True
        return self._decide(rel, is_dir)

    def _decide(self, rel: str, is_dir: bool) -> bool:
        ignored = False
        parts = rel.split("/")
        folders = [""] + ["/".join(parts[:i]) for i in range(1, len(parts))]
        for base in folders:
            for pattern, negated, dir_only in self._rules(base):
                if dir_only and not is_dir:
                    continue
                if _match_one(rel, base, pattern):
                    ignored = not negated
        return ignored


class Cancelled(Exception):
    """Raised by a tool's apply() when the user pressed Esc or Ctrl-C."""


def _rg_binary() -> str:
    return shutil.which("rg") or ""


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def diff(old: str, new: str, rel: str) -> str:
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 f"a/{rel}" if old else "/dev/null", f"b/{rel}")
    out = "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in lines)
    return out or "(no change)\n"


class Toolbox:
    def __init__(self, root: Path, command_timeout: int = 120, cancel: threading.Event | None = None):
        self.root = root.resolve()
        self.command_timeout = command_timeout
        self.cancel = cancel or threading.Event()

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

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

    def _walk(self, base: Path, cap: int) -> Iterator[Path]:
        """Files under base, SKIP_DIRS and .gitignore respected, at most `cap`, Cancelled on Esc/Ctrl-C."""
        if base.is_file():
            yield base
            return
        ig = Ignore(base)
        seen = 0
        for dirpath, dirnames, filenames in os.walk(base):
            self._check_cancel()
            rel_dir = Path(dirpath).relative_to(base).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            kept = []
            for d in sorted(dirnames):
                if d in SKIP_DIRS:
                    continue
                rel = f"{rel_dir}/{d}".strip("/")
                if not ig.ignored(rel, True):
                    kept.append(d)
            dirnames[:] = kept
            for f in sorted(filenames):
                if seen >= cap:
                    return
                rel = f"{rel_dir}/{f}".strip("/")
                if ig.ignored(rel, False):
                    continue
                seen += 1
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
            out, walked = [], 0
            for f in self._walk(base, MAX_SCANNED):
                walked += 1
                r = self.rel(f)
                if fnmatch.fnmatch(f.name, pattern) or fnmatch.fnmatch(r, pattern):
                    out.append(r)
                    if len(out) >= MAX_LISTED:
                        return "\n".join(out) + f"\n... stopped early: first {MAX_LISTED} listed", None, ""
            if walked >= MAX_SCANNED:
                out.append(f"... stopped early: walked the cap of {MAX_SCANNED:,} files")
            return "\n".join(out) or "(no files)", None, ""
        return Plan("read", f"list {self.rel(base)} {pattern}", "", run)

    def _search_py(self, rx, base: Path, glob: str | None):
        """Pure-Python walk used when ripgrep is not on PATH. Same caps as rg."""
        hits, scanned = [], 0
        started = time.monotonic()
        stopped = ""
        for f in self._walk(base, MAX_SCANNED * 4):  # walk further than we scan: big/binary files don't count
            self._check_cancel()
            if time.monotonic() - started > SCAN_SECONDS:
                stopped = f"stopped after {SCAN_SECONDS:.0f}s"
                break
            if glob and not fnmatch.fnmatch(f.name, glob) and not fnmatch.fnmatch(self.rel(f), glob):
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            scanned += 1
            if scanned > MAX_SCANNED:
                stopped = f"stopped after scanning {MAX_SCANNED:,} files"
                break
            try:
                with open(f, encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        self._check_cancel()
                        if rx.search(line):
                            hits.append(f"{self.rel(f)}:{i}: {line.rstrip()[:300]}")
                            if len(hits) >= MAX_MATCHES:
                                stopped = f"stopped after {MAX_MATCHES} matches"
                                break
            except (UnicodeDecodeError, OSError):
                continue
            if stopped:
                break
        body = "\n".join(hits) or "(no matches)"
        return (body + f"\n... ({stopped})") if stopped else body

    def _search_rg(self, rx, base: Path, glob: str | None):
        """ripgrep: --json streams matches so we can stop at MAX_MATCHES; .gitignore is native."""
        rg = _rg_binary()
        if not rg:
            return None
        cmd = [rg, "--json", "--no-require-git", "--max-count", "200", "--max-filesize", str(MAX_FILE_BYTES)]
        for d in sorted(SKIP_DIRS):
            cmd += ["--glob", f"!**/{d}/**", "--glob", f"!**/{d}"]
        cmd += ["--regexp", rx.pattern]
        if rx.flags & re.I:
            cmd.append("--ignore-case")
        if glob:
            cmd += ["--glob", glob]
        cmd.append(str(base))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", cwd=self.root)
        except OSError:
            return None
        hits, seen_files, stopped = [], set(), ""
        try:
            for line in proc.stdout or ():
                self._check_cancel()
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "match":
                    data = ev["data"]
                    path = data["path"]["text"]
                    try:
                        r = self.rel(Path(path).resolve())
                    except (OSError, ValueError):
                        r = path
                    text = data["lines"]["text"].rstrip("\n")[:300]
                    n = data["line_number"]
                    hits.append(f"{r}:{n}: {text}")
                    seen_files.add(path)
                    if len(hits) >= MAX_MATCHES:
                        stopped = f"stopped after {MAX_MATCHES} matches"
                        break
        finally:
            if stopped or self.cancel.is_set():
                proc.kill()
            proc.wait()
        body = "\n".join(hits) or "(no matches)"
        return (body + f"\n... ({stopped})") if stopped else body

    def _search(self, a: dict) -> Plan:
        try:
            rx = re.compile(str(a.get("pattern") or ""), re.I if a.get("ignore_case") else 0)
        except re.error as e:
            raise ToolError(f"bad regular expression: {e}") from None
        base, glob = self._path(a.get("path") or "."), a.get("glob")

        def run():
            result = self._search_rg(rx, base, glob)
            if result is None:
                result = self._search_py(rx, base, glob)
            return result, None, ""
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
