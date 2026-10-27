"""ROOKS runner: terminal commands and file access for the assistant, confined to one workspace folder.

It runs in its own container next to ROOKS. That container has only the workspace volume: no ROOKS database, no
APP_SECRET, no API keys. So a command the assistant runs, even a bad one, cannot read your credentials or your
ROOKS data. ROOKS decides what may run (permissions and approvals); this service only executes, inside the
workspace, and reports exactly what happened.

Endpoints (JSON, all need "Authorization: Bearer RUNNER_TOKEN" except /health):
  GET  /health                     status, workspace, self check for exposed secrets
  POST /exec   {id, command, cwd, timeout}     run with bash -c; exit code, stdout, stderr, duration
  POST /cancel {id}                stop a running command (its whole process group)
  POST /fs/list  {path}            entries of a folder
  POST /fs/read  {path, offset, limit}         numbered lines of a text file
  POST /fs/write {path, content, overwrite}    create a file, or replace it (the old one is kept as a version)
  POST /fs/edit  {path, old, new, replace_all} exact text replacement (the old file is kept as a version)

Settings: RUNNER_TOKEN (required, 24+ characters), WORKSPACE (default /workspace), RUNNER_PORT (default 8010),
RUNNER_MODE (container | local; shown to you so you know how isolated it is).
Standard library only; Python 3.9+.
"""

from __future__ import annotations

import hmac
import json
import os
import select
import shutil
import socket
import signal
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("RUNNER_TOKEN", "")
WS = os.path.realpath(os.environ.get("WORKSPACE", "/workspace"))
PORT = int(os.environ.get("RUNNER_PORT", "8010"))
MODE = os.environ.get("RUNNER_MODE", "local")
INTERNAL = ".rooks"                       # logs and versions; readable by the assistant, never written by its file tools
MAX_TIMEOUT = 1800
HEAD, TAIL = 4_000, 10_000                # characters of each stream returned (ROOKS shows at most 20,000); the full output is in the log
MAX_READ_LINES, MAX_READ_CHARS = 2_000, 120_000
MAX_WRITE = 5 * 1024 * 1024
MAX_LIST = 500
MAX_PARALLEL = 4
_running: dict[str, subprocess.Popen] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


class Refused(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


# Paths
def resolve(p: str, *, must_exist: bool = True, writing: bool = False) -> str:
    """A path inside the workspace, after following links. Anything that resolves outside is refused."""
    p = (p or ".").strip()
    if "\0" in p:
        raise Refused(400, "Invalid path.")
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(WS, p))
    if full != WS and os.path.commonpath([full, WS]) != WS:
        raise Refused(403, f"{p} is outside the workspace ({WS}). The assistant can only use files inside it.")
    if writing and (full == WS or os.path.relpath(full, WS).split(os.sep)[0] == INTERNAL):
        raise Refused(403, f"{p} is kept by ROOKS (logs and versions) and cannot be changed by the assistant.")
    if must_exist and not os.path.exists(full):
        raise Refused(404, f"{rel(full)} does not exist.")
    return full


def rel(full: str) -> str:
    r = os.path.relpath(full, WS)
    return "." if r == "." else r


def _version(full: str) -> str:
    """Keep the current file before it is replaced; returns where it was kept."""
    d = os.path.join(WS, INTERNAL, "versions", time.strftime("%Y%m%d"))
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}-{os.path.basename(full)}")
    shutil.copy2(full, dest)
    return rel(dest)


def _atomic_write(full: str, data: bytes) -> None:
    tmp = f"{full}.rooks-tmp-{uuid.uuid4().hex[:6]}"
    with open(tmp, "wb") as f:
        f.write(data)
    if os.path.exists(full):
        shutil.copymode(full, tmp)
    os.replace(tmp, full)


def _text(full: str) -> str:
    with open(full, "rb") as f:
        head = f.read(8192)
    if b"\0" in head:
        raise Refused(415, f"{rel(full)} is a binary file ({os.path.getsize(full):,} bytes); it cannot be shown as text.")
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read()


# Files
def fs_list(a: dict) -> dict:
    full = resolve(a.get("path", "."))
    if not os.path.isdir(full):
        raise Refused(400, f"{rel(full)} is a file, not a folder.")
    entries = []
    for name in sorted(os.listdir(full))[:MAX_LIST]:
        p = os.path.join(full, name)
        kind = "link" if os.path.islink(p) else "dir" if os.path.isdir(p) else "file"
        entries.append({"name": name, "type": kind, "size": os.path.getsize(p) if kind == "file" else None})
    return {"path": rel(full), "entries": entries, "truncated": len(os.listdir(full)) > MAX_LIST}


def fs_read(a: dict) -> dict:
    full = resolve(a.get("path", ""))
    if os.path.isdir(full):
        raise Refused(400, f"{rel(full)} is a folder; list it instead.")
    lines = _text(full).splitlines()
    offset = max(1, int(a.get("offset") or 1))
    limit = max(1, min(int(a.get("limit") or 400), MAX_READ_LINES))
    out, chars = [], 0
    for n in range(offset, min(len(lines), offset + limit - 1) + 1):
        line = lines[n - 1]
        if chars + len(line) > MAX_READ_CHARS:
            break
        out.append(f"{n:>6}\t{line}")
        chars += len(line)
    last = offset + len(out) - 1
    return {"path": rel(full), "total_lines": len(lines), "from": offset, "to": last, "content": "\n".join(out),
            "more": last < len(lines)}


def fs_write(a: dict) -> dict:
    content = a.get("content")
    if not isinstance(content, str):
        raise Refused(400, "content must be text.")
    data = content.encode()
    if len(data) > MAX_WRITE:
        raise Refused(413, f"That is {len(data):,} bytes; files written by the assistant can be up to {MAX_WRITE:,}.")
    full = resolve(a.get("path", ""), must_exist=False, writing=True)
    existed = os.path.exists(full)
    if existed and os.path.isdir(full):
        raise Refused(400, f"{rel(full)} is a folder.")
    if existed and not a.get("overwrite"):
        raise Refused(409, f"{rel(full)} already exists. Read it and use edit, or write again with overwrite true to replace it.")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    version = _version(full) if existed else ""
    _atomic_write(full, data)
    return {"path": rel(full), "bytes": len(data), "lines": content.count("\n") + (0 if content.endswith("\n") or not content else 1),
            "created": not existed, "previous_version": version}


def fs_edit(a: dict) -> dict:
    old, new = a.get("old"), a.get("new")
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        raise Refused(400, "old (the exact text to replace, not empty) and new are required.")
    full = resolve(a.get("path", ""), writing=True)
    text = _text(full)
    count = text.count(old)
    if count == 0:
        raise Refused(409, f"The text to replace was not found in {rel(full)}. Read the file again and copy the text exactly, "
                           "including spaces and line breaks.")
    if count > 1 and not a.get("replace_all"):
        raise Refused(409, f"The text to replace occurs {count} times in {rel(full)}. Include more surrounding lines so it is "
                           "unique, or set replace_all true.")
    updated = text.replace(old, new) if a.get("replace_all") else text.replace(old, new, 1)
    version = _version(full)
    _atomic_write(full, updated.encode())
    start = updated[:updated.find(new)].count("\n") + 1 if new else text[:text.find(old)].count("\n") + 1
    lines = updated.splitlines()
    lo, hi = max(1, start - 2), min(len(lines), start + new.count("\n") + 2)
    return {"path": rel(full), "replacements": count if a.get("replace_all") else 1, "previous_version": version,
            "snippet": "\n".join(f"{n:>6}\t{lines[n - 1]}" for n in range(lo, hi + 1))}


# Commands
# Set inside the command's own shell (preexec_fn is unsafe in a threaded server): no core dumps, no file over 4 GB
LIMITS = "ulimit -c 0 2>/dev/null; ulimit -f 4194304 2>/dev/null; "


def _env() -> dict:
    # A clean environment: nothing from the runner (in particular not RUNNER_TOKEN) reaches the command
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": WS, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "TERM": "dumb", "NO_COLOR": "1", "PYTHONUNBUFFERED": "1",
            # an edit made within the same second as the last run must not be hidden by stale bytecode
            "PYTHONDONTWRITEBYTECODE": "1", "PAGER": "cat", "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}


class _Stream:
    """Keeps the start and the end of a stream; everything also goes to the log."""

    def __init__(self):
        self.head, self.tail, self.total = "", "", 0

    def add(self, s: str) -> None:
        self.total += len(s)
        if len(self.head) < HEAD:
            take = HEAD - len(self.head)
            self.head += s[:take]
            s = s[take:]
        if s:
            self.tail = (self.tail + s)[-TAIL:]

    def text(self) -> tuple[str, bool]:
        cut = self.total > len(self.head) + len(self.tail)
        return (self.head + (f"\n… {self.total - len(self.head) - len(self.tail):,} characters not shown …\n" if cut else "") + self.tail), cut


def _gone(conn: socket.socket | None) -> bool:
    """ROOKS dropped the request (it restarted, or the turn was abandoned): nobody will read this result."""
    if conn is None:
        return False
    try:
        if select.select([conn], [], [], 0)[0]:
            return conn.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True
    return False


def run_exec(a: dict, conn: socket.socket | None = None) -> dict:
    command = a.get("command")
    if not isinstance(command, str) or not command.strip():
        raise Refused(400, "command is required.")
    if len(command) > 20_000:
        raise Refused(413, "That command is too long.")
    cid = str(a.get("id") or uuid.uuid4().hex[:12])
    if not all(c.isalnum() or c in "-_" for c in cid) or len(cid) > 64:
        raise Refused(400, "Invalid id.")
    cwd = resolve(a.get("cwd") or ".")
    if not os.path.isdir(cwd):
        raise Refused(400, f"{rel(cwd)} is not a folder.")
    timeout = max(1, min(int(a.get("timeout") or 120), MAX_TIMEOUT))
    if not _slots.acquire(timeout=5):
        raise Refused(429, f"{MAX_PARALLEL} commands are already running. Wait for one to finish.")
    logdir = os.path.join(WS, INTERNAL, "logs")
    os.makedirs(logdir, exist_ok=True)
    logpath = os.path.join(logdir, f"{time.strftime('%Y%m%d-%H%M%S')}-{cid}.log")
    shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
    out, err, wlock = _Stream(), _Stream(), threading.Lock()
    started = time.monotonic()
    try:
        with open(logpath, "w", encoding="utf-8") as log:
            log.write(f"$ {command}\n(in {rel(cwd)}, timeout {timeout}s)\n")
            proc = subprocess.Popen([shell, "-c", LIMITS + command], cwd=cwd, env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            with _lock:
                _running[cid] = proc

            def pump(pipe, stream):
                for raw in iter(lambda: pipe.read1(65536), b""):
                    s = raw.decode("utf-8", errors="replace")
                    stream.add(s)
                    with wlock:
                        log.write(s)
                pipe.close()

            threads = [threading.Thread(target=pump, args=(proc.stdout, out), daemon=True),
                       threading.Thread(target=pump, args=(proc.stderr, err), daemon=True)]
            for t in threads:
                t.start()
            timed_out = abandoned = False
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    timed_out = True
                    _kill(proc)
                    break
                if _gone(conn):
                    abandoned = True  # never leave a command running that ROOKS no longer waits for
                    _kill(proc)
                    break
                time.sleep(0.2)
            proc.wait()
            for t in threads:
                t.join(5)
            code = proc.returncode
            log.write(f"\n[exit {code}{', timed out' if timed_out else ''}{', stopped: ROOKS stopped waiting' if abandoned else ''}]\n")
    finally:
        with _lock:
            _running.pop(cid, None)
            cancelled = cid in _cancelled
            _cancelled.discard(cid)
        _slots.release()
    o, o_cut = out.text()
    e, e_cut = err.text()
    return {"id": cid, "exit_code": code, "timed_out": timed_out, "cancelled": cancelled, "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout": o, "stderr": e, "truncated": o_cut or e_cut, "stdout_chars": out.total, "stderr_chars": err.total,
            "log": rel(logpath), "cwd": rel(cwd)}


def _kill(proc: subprocess.Popen) -> None:
    """The whole process group: a command's children stop with it."""
    for sig, wait in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def run_cancel(a: dict) -> dict:
    cid = str(a.get("id") or "")
    with _lock:
        proc = _running.get(cid)
        if proc:
            _cancelled.add(cid)
    if not proc:
        return {"id": cid, "cancelled": False, "note": "Not running."}
    _kill(proc)
    return {"id": cid, "cancelled": True}


def health() -> dict:
    warnings = []
    if any(k in os.environ for k in ("APP_SECRET", "TOOLS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY")):
        warnings.append("ROOKS secrets are visible to the runner's environment.")
    if os.path.exists("/data/workbench.db"):
        warnings.append("The ROOKS database is visible to the runner.")
    return {"ok": bool(TOKEN), "workspace": WS, "mode": MODE, "uid": os.getuid(), "max_parallel": MAX_PARALLEL,
            "max_timeout": MAX_TIMEOUT, "warnings": warnings, "running": len(_running)}


ROUTES = {"/exec": run_exec, "/cancel": run_cancel, "/fs/list": fs_list, "/fs/read": fs_read, "/fs/write": fs_write, "/fs/edit": fs_edit}


class Handler(BaseHTTPRequestHandler):
    server_version = "rooks-runner"

    def log_message(self, *a):  # requests are logged by ROOKS without their content; nothing here
        pass

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, health())
        self._send(404, {"error": "Not found"})

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if len(TOKEN) < 24 or not hmac.compare_digest(auth.encode(), f"Bearer {TOKEN}".encode()):
            return self._send(401, {"error": "Not authorized."})
        fn = ROUTES.get(self.path)
        if not fn:
            return self._send(404, {"error": "Not found"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_WRITE * 2:
                raise Refused(413, "Request too large.")
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise Refused(400, "Expected a JSON object.")
            self._send(200, fn(body, self.connection) if fn is run_exec else fn(body))
        except Refused as e:
            self._send(e.status, {"error": str(e)})
        except (ValueError, TypeError) as e:
            self._send(400, {"error": f"Bad request: {e}"})
        except OSError as e:
            self._send(500, {"error": f"{e.__class__.__name__}: {e.strerror or e}"})


def main() -> None:
    if len(TOKEN) < 24:
        raise SystemExit("RUNNER_TOKEN must be set (24+ characters). Run ./setup.sh to generate one.")
    os.makedirs(WS, exist_ok=True)
    os.makedirs(os.path.join(WS, INTERNAL), exist_ok=True)
    host = os.environ.get("RUNNER_HOST", "0.0.0.0" if MODE == "container" else "127.0.0.1")
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
