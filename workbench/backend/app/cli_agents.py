"""Coding agents you already have: hand a job to the user's own Claude Code (`claude`) or Codex (`codex`) CLI.

Why this shape. Anthropic does not let other apps offer Claude.ai sign-in or route requests through a Claude plan's
credentials, and apps may not collect or store Claude.ai tokens. It does allow a person to sign in to the unmodified
Claude Code binary with their own plan (https://code.claude.com/docs/en/legal-and-compliance). So this module never
signs anyone in and never touches a token: it finds the `claude` / `codex` program the user installed and signed in to
themselves, runs it on their computer in a folder they picked, and shows what it does. Sign-in, tokens and billing stay
inside the official CLI, under the user's own agreement with Anthropic or OpenAI.

Approvals. Claude Code runs in its Manual permission mode (or Plan / Accept edits, if the user picks those) and never
with a bypass flag. In print mode a step that needs approval is denied and listed in the result's `permission_denials`;
the run stops as "needs approval" and the user approves the exact steps here, which resumes the same session with only
those steps allowed (`--allowedTools`). Codex runs in its read-only sandbox until the user approves edits, then resumes
in `workspace-write`. Nothing runs with full access.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from . import actions, config, db

AGENTS: dict[str, dict[str, Any]] = {
    "claude": {
        "id": "claude", "name": "Claude Code", "bin": "claude", "env": "WORKBENCH_CLAUDE_BIN",
        "plan": "Claude Pro, Max, Team or Enterprise",
        "install": "npm install -g @anthropic-ai/claude-code", "login": "claude",
        "login_hint": "Run claude in a terminal and pick \"Claude account with subscription\". Anthropic's own sign-in opens in your browser.",
        "docs": "https://code.claude.com/docs/en/setup",
        "terms": "https://code.claude.com/docs/en/legal-and-compliance",
        # Keys the app may hold for its own Anthropic connection: kept out so the CLI uses the user's own sign-in.
        "strip_env": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"),
    },
    "codex": {
        "id": "codex", "name": "Codex", "bin": "codex", "env": "WORKBENCH_CODEX_BIN",
        "plan": "ChatGPT Plus, Pro, Business, Edu or Enterprise",
        "install": "npm install -g @openai/codex", "login": "codex login",
        "login_hint": "Run codex login in a terminal and choose Sign in with ChatGPT.",
        "docs": "https://developers.openai.com/codex/cli",
        "terms": "https://developers.openai.com/codex/noninteractive",
        "strip_env": ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"),
    },
}

# What the user picks per job. Never a bypass mode.
MODES = {
    "ask": {"label": "Ask before changes", "claude": "manual", "codex": "read-only"},
    "edits": {"label": "Edit files in this folder", "claude": "acceptEdits", "codex": "workspace-write"},
    "plan": {"label": "Read only (plan)", "claude": "plan", "codex": "read-only"},
}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MAX_EVENTS = 1500
MAX_TEXT = 6000
RUNS: dict[str, "Run"] = {}


# ---- Finding the program ----------------------------------------------------------------------------------------

def _extra_dirs() -> list[str]:
    """Where npm, the native installers and Homebrew put these CLIs, for apps started without a login shell's PATH."""
    home = Path.home()
    dirs = [home / ".local/bin", home / ".claude/local", home / ".npm-global/bin", home / ".volta/bin", home / ".bun/bin",
            Path("/opt/homebrew/bin"), Path("/usr/local/bin"), home / ".codex/bin"]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(Path(appdata) / "npm")
        dirs.append(home / "AppData/Local/Programs/claude")
    nvm = home / ".nvm/versions/node"
    if nvm.is_dir():
        dirs += sorted((p / "bin" for p in nvm.iterdir()), reverse=True)
    return [str(d) for d in dirs if d.is_dir()]


def find_binary(agent: str) -> str | None:
    a = AGENTS[agent]
    override = os.environ.get(a["env"], "").strip()
    if override:
        return override if Path(override).is_file() else None
    path = os.pathsep.join([os.environ.get("PATH", ""), *_extra_dirs()])
    return shutil.which(a["bin"], path=path)


def child_env(agent: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in AGENTS[agent]["strip_env"]}
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *_extra_dirs()])
    env.setdefault("NO_COLOR", "1")
    return env


def _run_quick(argv: list[str], agent: str, timeout: float = 15) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=child_env(agent), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout[:20000], p.stderr[:20000]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


_DETECT_CACHE: dict[str, tuple[float, dict]] = {}


def detect(agent: str, fresh: bool = False) -> dict:
    """Is the CLI installed and signed in? Reads only yes/no and the kind of sign-in, never identity or tokens."""
    hit = _DETECT_CACHE.get(agent)
    if hit and not fresh and time.time() - hit[0] < 20:
        return hit[1]
    a = AGENTS[agent]
    info = {k: a[k] for k in ("id", "name", "plan", "install", "login", "login_hint", "docs", "terms")}
    info.update(installed=False, path=None, version="", signed_in=False, auth="none", ready=False, detail="")
    path = find_binary(agent)
    if not path:
        info["detail"] = f"{a['name']} isn't installed on this computer."
        _DETECT_CACHE[agent] = (time.time(), info)
        return info
    info.update(installed=True, path=path)
    code, out, err = _run_quick([path, "--version"], agent)
    info["version"] = (out or err).strip().splitlines()[0][:80] if (out or err).strip() else ""
    if agent == "claude":
        code, out, _ = _run_quick([path, "auth", "status"], agent)
        try:
            st = json.loads(out)
        except ValueError:
            st = {}
        logged_in = bool(st.get("loggedIn"))
        method = str(st.get("authMethod") or "none")
        info["signed_in"] = logged_in
        info["auth"] = "subscription" if logged_in and method == "claude.ai" else ("api_key" if logged_in else "none")
    else:
        code, out, err = _run_quick([path, "login", "status"], agent)
        text = f"{out}\n{err}"
        info["signed_in"] = code == 0 and "Logged in" in text
        info["auth"] = "subscription" if "using ChatGPT" in text else ("api_key" if info["signed_in"] else "none")
    info["ready"] = info["signed_in"]
    if not info["signed_in"]:
        info["detail"] = f"Installed, but not signed in. {a['login_hint']}"
    elif info["auth"] == "api_key":
        info["detail"] = f"Signed in with an API key, so jobs bill that key rather than your {a['plan'].split(',')[0]} plan."
    else:
        info["detail"] = f"Signed in with your plan. Jobs use your own {a['name']} sign-in and its usage limits."
    _DETECT_CACHE[agent] = (time.time(), info)
    return info


_MANUAL_OK: dict[str, bool] = {}


def _claude_manual_mode(path: str) -> str:
    """Claude Code renamed its default permission mode to "manual" (v2.1.302); older versions call it "default"."""
    if path not in _MANUAL_OK:
        _, out, err = _run_quick([path, "--help"], "claude")
        _MANUAL_OK[path] = '"manual"' in (out + err)
    return "manual" if _MANUAL_OK[path] else "default"


# ---- A run --------------------------------------------------------------------------------------------------------

def _clip(s: Any, n: int = MAX_TEXT) -> str:
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"… ({len(s) - n:,} more characters)"


def _rule_for(tool: str, inp: dict) -> str:
    """The narrowest --allowedTools rule that lets exactly this step run."""
    if tool == "Bash" and inp.get("command"):
        return f"Bash({inp['command']})"
    return tool


def _describe(tool: str, inp: dict) -> str:
    if tool == "Bash":
        return f"Run: {inp.get('command', '')}"
    if tool in EDIT_TOOLS:
        return f"{'Create' if tool == 'Write' else 'Edit'} {inp.get('file_path') or inp.get('notebook_path') or 'a file'}"
    if tool == "WebFetch":
        return f"Open {inp.get('url', 'a web page')}"
    return f"Use {tool}"


class Run:
    def __init__(self, agent: str, task: str, folder: str, mode: str, source: str = "app"):
        self.id = uuid.uuid4().hex[:12]
        self.agent, self.task, self.folder, self.mode, self.source = agent, task, folder, mode, source
        self.status = "queued"            # queued | running | needs_approval | done | failed | stopped
        self.events: list[dict] = []
        self.session_id = ""
        self.allowed: list[str] = []
        self.pending: list[dict] = []
        self.result = ""
        self.error = ""
        self.cost_usd: float | None = None
        self.tokens_in = self.tokens_out = 0
        self.model = ""
        self.edits: dict[str, dict] = {}  # file -> {"before": str|None, "after": str|None} as told by the tool inputs
        self.diff = ""
        self.files: list[str] = []
        self.created_at = time.time()
        self.ended_at: float | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.rounds = 0
        self.git_base: str | None = None

    # -- public shape
    def summary(self) -> dict:
        return {"id": self.id, "agent": self.agent, "agent_name": AGENTS[self.agent]["name"], "task": self.task,
                "folder": self.folder, "mode": self.mode, "status": self.status, "created_at": self.created_at,
                "source": self.source}

    def full(self, after: int = 0) -> dict:
        return {**self.summary(), "events": self.events[after:], "event_count": len(self.events), "pending": self.pending,
                "result": self.result, "error": self.error, "cost_usd": self.cost_usd, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "model": self.model, "diff": self.diff, "files": self.files,
                "allowed": self.allowed, "ended_at": self.ended_at, "session": bool(self.session_id)}

    def add(self, kind: str, **kw: Any) -> None:
        if len(self.events) >= MAX_EVENTS:
            if self.events[-1].get("kind") != "note":
                self.events.append({"kind": "note", "text": "Later steps are not shown here (the run log keeps the totals).", "t": time.time()})
            return
        self.events.append({"kind": kind, "t": time.time(), **kw})

    # -- persistence: the run survives an app restart as a finished record
    def save(self) -> None:
        d = _runs_dir()
        data = self.full()
        data.update(session_id=self.session_id, edits_files=list(self.edits))
        tmp = d / f"{self.id}.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str))
        tmp.replace(d / f"{self.id}.json")


def _runs_dir() -> Path:
    d = config.DATA_DIR / "cli-runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_saved() -> None:
    for f in sorted(_runs_dir().glob("*.json"))[-200:]:
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if data.get("id") in RUNS or data.get("agent") not in AGENTS:
            continue
        r = Run(data["agent"], data.get("task", ""), data.get("folder", ""), data.get("mode", "ask"), data.get("source", "app"))
        r.id = data["id"]
        for k in ("events", "pending", "result", "error", "cost_usd", "tokens_in", "tokens_out", "model", "diff", "files",
                  "allowed", "created_at", "ended_at", "session_id"):
            if k in data:
                setattr(r, k, data[k])
        r.status = data.get("status", "done")
        if r.status in ("queued", "running"):
            r.status, r.error = "stopped", "The app closed while this job was running."
        RUNS[r.id] = r


# ---- Parsing each CLI's JSON lines ------------------------------------------------------------------------------

def _note_edit(run: Run, tool: str, inp: dict) -> None:
    path = inp.get("file_path") or inp.get("notebook_path")
    if not path:
        return
    rec = run.edits.setdefault(path, {"before": None, "after": None, "hunks": []})
    if tool == "Write":
        rec["after"] = inp.get("content", "")
    elif tool == "Edit":
        rec["hunks"].append((inp.get("old_string", ""), inp.get("new_string", "")))
    elif tool == "MultiEdit":
        for e in inp.get("edits") or []:
            rec["hunks"].append((e.get("old_string", ""), e.get("new_string", "")))


def parse_claude(run: Run, ev: dict) -> None:
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        run.session_id = ev.get("session_id") or run.session_id
        run.model = ev.get("model") or run.model
        label = f"{AGENTS['claude']['name']} {ev.get('claude_code_version', '')} started".strip() if run.rounds <= 1 else "Continuing the same session"
        run.add("start", text=label, model=run.model,
                mode=ev.get("permissionMode", ""))
    elif t == "system" and ev.get("subtype") == "api_retry":
        run.add("note", text=f"Retrying ({ev.get('error', 'error')}), attempt {ev.get('attempt')} of {ev.get('max_retries')}")
    elif t == "system" and ev.get("subtype") == "permission_denied":
        name = ev.get("tool_name") or "a step"
        run.add("denied", text=_clip(ev.get("message") or f"Paused: {name} needs your approval", 400))
    elif t == "assistant":
        msg = ev.get("message") or {}
        sub = ev.get("parent_tool_use_id") is not None
        for part in msg.get("content") or []:
            if part.get("type") == "text" and part.get("text", "").strip():
                run.add("text", text=_clip(part["text"]), sub=sub)
            elif part.get("type") == "thinking" and part.get("thinking", "").strip():
                run.add("thinking", text=_clip(part["thinking"], 1500), sub=sub)
            elif part.get("type") == "tool_use":
                name, inp = part.get("name", ""), part.get("input") or {}
                run.add("tool", id=part.get("id", ""), tool=name, label=_describe(name, inp), input=_clip(inp, 2000), sub=sub)
                if name in EDIT_TOOLS:
                    _note_edit(run, name, inp)
        if ev.get("error"):
            run.error = _clip((msg.get("content") or [{}])[0].get("text", "") or str(ev["error"]), 500)
    elif t == "user":
        for part in (ev.get("message") or {}).get("content") or []:
            if isinstance(part, dict) and part.get("type") == "tool_result":
                c = part.get("content")
                if isinstance(c, list):
                    c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                run.add("tool_result", id=part.get("tool_use_id", ""), text=_clip(c or "", 2500), error=bool(part.get("is_error")))
    elif t == "result":
        run.session_id = ev.get("session_id") or run.session_id
        if isinstance(ev.get("total_cost_usd"), (int, float)):
            run.cost_usd = float(ev["total_cost_usd"])
        u = ev.get("usage") or {}
        run.tokens_in += int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        run.tokens_out += int(u.get("output_tokens") or 0)
        run.result = _clip(ev.get("result") or "", 20000)
        if ev.get("is_error"):
            run.error = run.result or ev.get("subtype", "error")
        seen = set()
        run.pending = []
        for d in ev.get("permission_denials") or []:
            name, inp = d.get("tool_name", ""), d.get("tool_input") or {}
            rule = _rule_for(name, inp)
            if rule in seen or rule in run.allowed:
                continue
            seen.add(rule)
            run.pending.append({"id": d.get("tool_use_id") or uuid.uuid4().hex[:8], "tool": name, "rule": rule,
                                "label": _describe(name, inp), "input": _clip(inp, 2000)})
        run.add("result", text=run.result[:2000], error=bool(ev.get("is_error")), turns=ev.get("num_turns"),
                ms=ev.get("duration_ms"))


def parse_codex(run: Run, ev: dict) -> None:
    t = ev.get("type", "")
    if t == "thread.started":
        run.session_id = ev.get("thread_id") or run.session_id
        run.add("start", text="Codex started" if run.rounds <= 1 else "Continuing the same session", mode=MODES[run.mode]["codex"])
    elif t in ("item.started", "item.completed"):
        it = ev.get("item") or {}
        kind = it.get("type")
        if kind == "agent_message" and t == "item.completed":
            run.result = _clip(it.get("text", ""), 20000)
            run.add("text", text=_clip(it.get("text", "")))
        elif kind == "reasoning" and t == "item.completed" and it.get("text"):
            run.add("thinking", text=_clip(it["text"], 1500))
        elif kind == "command_execution":
            if t == "item.started":
                run.add("tool", id=it.get("id", ""), tool="Bash", label=f"Run: {it.get('command', '')}", input=it.get("command", ""))
            else:
                run.add("tool_result", id=it.get("id", ""), text=_clip(it.get("aggregated_output", ""), 2500),
                        error=(it.get("exit_code") not in (0, None)) or it.get("status") == "failed")
        elif kind == "file_change" and t == "item.completed":
            changes = it.get("changes") or []
            for c in changes:
                run.edits.setdefault(c.get("path", "?"), {"before": None, "after": None, "hunks": []})
            run.add("tool", id=it.get("id", ""), tool="Edit", label="Change " + ", ".join(f"{c.get('path')} ({c.get('kind')})" for c in changes), input="")
        elif kind in ("web_search", "mcp_tool_call") and t == "item.started":
            run.add("tool", id=it.get("id", ""), tool=kind, label=it.get("query") or it.get("tool") or kind, input="")
        elif kind == "error":
            run.add("note", text=_clip(it.get("message", ""), 500))
    elif t == "turn.completed":
        run.error = ""  # earlier "error" events were reconnects that recovered
        u = ev.get("usage") or {}
        run.tokens_in += int(u.get("input_tokens") or 0)
        run.tokens_out += int(u.get("output_tokens") or 0)
        run.add("result", text=run.result[:2000], error=False)
    elif t == "error":
        run.error = _clip(ev.get("message") or "Codex reported an error", 800)
        run.add("note", text=run.error[:300])
    elif t == "turn.failed":
        msg = (ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict) else ev.get("message")
        run.error = _clip(msg or "Codex stopped with an error", 800)
        run.add("result", text=run.error, error=True)


# ---- Diffs ---------------------------------------------------------------------------------------------------------

def _git(folder: str, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def git_snapshot(folder: str) -> str | None:
    """A tree id for the folder's current state (tracked + untracked), without touching the index or the working copy."""
    code, top = _git(folder, "rev-parse", "--show-toplevel")
    if code != 0:
        return None
    env = {**os.environ, "GIT_INDEX_FILE": str(_runs_dir() / f"idx-{uuid.uuid4().hex[:8]}")}
    try:
        subprocess.run(["git", "-C", folder, "read-tree", "HEAD"], env=env, capture_output=True, timeout=20)
        subprocess.run(["git", "-C", folder, "add", "-A", "."], env=env, capture_output=True, timeout=60)
        p = subprocess.run(["git", "-C", folder, "write-tree"], env=env, capture_output=True, text=True, timeout=20)
        return p.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.unlink(env["GIT_INDEX_FILE"])
        except OSError:
            pass


def compute_diff(run: Run) -> None:
    if run.git_base:
        after = git_snapshot(run.folder)
        if after and after != run.git_base:
            _, out = _git(run.folder, "diff", "--no-color", run.git_base, after, "--", ".")
            _, names = _git(run.folder, "diff", "--name-only", run.git_base, after, "--", ".")
            run.diff = _clip(out, 200_000)
            run.files = [n for n in names.splitlines() if n]
            return
        if after:
            run.diff, run.files = "", []
            return
    # Not a git folder: rebuild what we can from the edit steps the agent reported
    chunks, files = [], []
    for path, rec in run.edits.items():
        files.append(path)
        if rec.get("after") is not None and not rec.get("hunks"):
            chunks += difflib.unified_diff([], rec["after"].splitlines(True), "/dev/null", path)
        for old, new in rec.get("hunks", []):
            chunks += difflib.unified_diff(old.splitlines(True), new.splitlines(True), path, path)
    run.diff = _clip("".join(chunks), 200_000)
    run.files = files


# ---- Running -------------------------------------------------------------------------------------------------------

def build_argv(run: Run, path: str, resume_prompt: str | None) -> list[str]:
    if run.agent == "claude":
        mode = MODES[run.mode]["claude"]
        if mode == "manual":
            mode = _claude_manual_mode(path)
        argv = [path, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", mode]
        if run.allowed:
            argv += ["--allowedTools", ",".join(run.allowed)]
        if resume_prompt is not None and run.session_id:
            argv += ["--resume", run.session_id]
        return argv
    sandbox = "workspace-write" if (run.mode == "edits" or "codex:edits" in run.allowed) else "read-only"
    common = ["--json", "--skip-git-repo-check", "-c", f'sandbox_mode="{sandbox}"']
    if resume_prompt is not None and run.session_id:
        # `exec resume` has no --sandbox or --color flags (codex-cli 0.166); the -c override carries the sandbox
        return [path, "exec", "resume", *common, run.session_id, "-"]
    return [path, "exec", *common, "--color", "never", "--sandbox", sandbox, "-"]


async def _drive(run: Run, prompt: str, resume: bool) -> None:
    path = find_binary(run.agent)
    if not path:
        run.status, run.error = "failed", f"{AGENTS[run.agent]['name']} isn't installed. {AGENTS[run.agent]['install']}"
        run.add("result", text=run.error, error=True)
        return _finish(run)
    argv = build_argv(run, path, prompt if resume else None)
    run.status = "running"
    run.rounds += 1
    run.pending = []
    run.error = ""
    if run.rounds == 1:
        run.git_base = await asyncio.to_thread(git_snapshot, run.folder)
    kw: dict[str, Any] = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kw["start_new_session"] = True
    try:
        run.proc = await asyncio.create_subprocess_exec(*argv, cwd=run.folder, env=child_env(run.agent),
                                                         stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.PIPE, limit=8 * 1024 * 1024, **kw)
    except OSError as e:
        run.status, run.error = "failed", f"Couldn't start {AGENTS[run.agent]['name']}: {e}"
        run.add("result", text=run.error, error=True)
        return _finish(run)
    assert run.proc.stdin and run.proc.stdout and run.proc.stderr
    run.proc.stdin.write(prompt.encode())
    await run.proc.stdin.drain()
    run.proc.stdin.close()
    err_buf = bytearray()

    async def read_err() -> None:
        while chunk := await run.proc.stderr.read(4096):  # type: ignore[union-attr]
            if len(err_buf) < 16000:
                err_buf.extend(chunk)

    err_task = asyncio.create_task(read_err())
    parse = parse_claude if run.agent == "claude" else parse_codex
    last_save = 0.0
    while True:
        line = await run.proc.stdout.readline()
        if not line:
            break
        try:
            ev = json.loads(line)
        except ValueError:
            text = line.decode(errors="replace").strip()
            if text:
                run.add("note", text=_clip(text, 500))
            continue
        if isinstance(ev, dict):
            parse(run, ev)
        if time.time() - last_save > 2:
            run.save()
            last_save = time.time()
    code = await run.proc.wait()
    await err_task
    run.proc = None
    if run.status == "stopped":
        return _finish(run)
    if code != 0 and not run.error:
        tail = err_buf.decode(errors="replace").strip().splitlines()[-3:]
        run.error = _clip(" ".join(tail) or f"{AGENTS[run.agent]['name']} exited with code {code}", 800)
    if run.agent == "codex" and run.mode == "ask" and "codex:edits" not in run.allowed and not run.error:
        run.pending = [{"id": "codex-edits", "tool": "sandbox", "rule": "codex:edits",
                        "label": "Let Codex change files in this folder (workspace-write sandbox)", "input": ""}]
    if run.pending:
        run.status = "needs_approval"
    else:
        run.status = "failed" if run.error else "done"
    _finish(run)


def _finish(run: Run) -> None:
    try:
        compute_diff(run)
    except Exception as e:  # a diff is a nice-to-have; never lose the run over it
        run.add("note", text=f"Couldn't work out the changes: {e}")
    if run.status != "needs_approval":
        run.ended_at = time.time()
    run.save()
    _record(run)


def _record(run: Run) -> None:
    """One row in the run log per round, so Claude Code and Codex jobs sit next to every other agent action."""
    outcome = {"done": "no_claim", "needs_approval": "interrupted", "failed": "model_error", "stopped": "interrupted"}.get(run.status, "no_claim")
    try:
        actions.add("turn", outcome, trace={"channel": f"cli:{run.agent}"}, provider=AGENTS[run.agent]["name"], model=run.model,
                    tool=run.mode, effect="write", args=_clip(run.task, 300),
                    latency_ms=int(((run.ended_at or time.time()) - run.created_at) * 1000), tokens_in=run.tokens_in,
                    tokens_out=run.tokens_out, usd_cost=None if run.agent == "codex" else run.cost_usd,
                    failure_reason="Waiting for your approval" if run.status == "needs_approval" else _clip(run.error, 300),
                    detail=_clip(f"{len(run.files)} file(s) changed in {run.folder}", 300), request_id=f"cli-{run.id}-{run.rounds}")
    except Exception:
        pass


def start(agent: str, task: str, folder: str, mode: str, source: str = "app") -> Run:
    if agent not in AGENTS:
        raise ValueError("Unknown coding agent")
    if mode not in MODES:
        raise ValueError("Unknown mode")
    f = Path(os.path.expanduser(folder or "")).resolve()
    if not folder or not f.is_dir():
        raise ValueError("Pick a folder that exists on this computer.")
    if f == Path(f.anchor):
        raise ValueError("Pick a project folder, not the whole drive.")
    run = Run(agent, task.strip(), str(f), mode, source)
    RUNS[run.id] = run
    run.add("user", text=run.task)
    run.save()
    asyncio.get_running_loop().create_task(_drive(run, run.task, resume=False))
    return run


def approve(run: Run, rules: list[str] | None, note: str = "") -> None:
    if run.status != "needs_approval":
        raise ValueError("This job isn't waiting for approval.")
    chosen = [p for p in run.pending if rules is None or p["rule"] in rules]
    if not chosen:
        raise ValueError("Nothing to approve.")
    run.allowed += [p["rule"] for p in chosen]
    labels = "; ".join(p["label"] for p in chosen)
    run.add("approved", text=labels)
    prompt = (f"I approved these steps: {labels}. Go ahead with them and finish the task." + (f" {note}" if note else ""))
    asyncio.get_running_loop().create_task(_drive(run, prompt, resume=True))


def deny(run: Run) -> None:
    if run.status != "needs_approval":
        raise ValueError("This job isn't waiting for approval.")
    run.add("denied", text="You said no. Nothing else ran.")
    run.pending = []
    run.status = "done"
    run.ended_at = time.time()
    run.save()
    _record(run)


def follow_up(run: Run, text: str) -> None:
    if run.status in ("queued", "running"):
        raise ValueError("Wait for this job to finish first.")
    if not run.session_id:
        raise ValueError("This job can't be continued.")
    run.add("user", text=text.strip())
    run.ended_at = None
    asyncio.get_running_loop().create_task(_drive(run, text.strip(), resume=True))


def stop(run: Run) -> None:
    p = run.proc
    run.status = "stopped"
    run.add("note", text="Stopped by you.")
    if p and p.returncode is None:
        try:
            if sys.platform == "win32":
                p.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(p.pid, signal.SIGINT)  # SIGINT lets Claude Code end the turn cleanly
        except (ProcessLookupError, PermissionError, OSError):
            p.terminate()
