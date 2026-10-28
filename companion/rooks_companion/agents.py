"""Coding agents installed on this computer (OpenAI Codex CLI, Claude Code), run for ROOKS through their official
command line tools, in one folder you allowed, after you approved the task.

What this module never does: read, copy or send any agent's sign-in files or tokens; use a Claude subscription (Claude
Code runs only in --bare mode, where it accepts nothing but an API key); turn off an agent's own sandbox or permission
checks; run anything that was not approved for this one task.

  Codex:  codex exec --json --sandbox workspace-write -C <folder> --skip-git-repo-check <task>
          Signed in with ChatGPT, it uses your ChatGPT plan's Codex limits; signed in with an API key, OpenAI bills
          that key. ROOKS only reads which of the two `codex login status` reports. Before the first task, ROOKS
          checks on this computer that Codex's sandbox really keeps writes inside the folder, and refuses if not.
  Claude: claude --bare -p <task> --output-format stream-json --permission-mode acceptEdits --tools=... --disallowedTools=Bash
          Only when you turned it on here (allow-claude-api on) and ANTHROPIC_API_KEY is set for the companion.
          Anthropic bills that key per use. It gets no shell: --tools sets the tools that exist (in --bare mode Claude
          Code 2.1.312 then offers only Read and Edit), and Bash is denied as well. --allowedTools would only pre-approve
          tools, not remove any.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .policy import Refused, resolve

MAX_EVENTS = 400
MAX_TASK = 8000
RUN_SECONDS = 60 * 60
# Passed through to the agent; everything else in the companion's environment is left out
ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "SHELL", "SYSTEMROOT", "USERPROFILE",
            "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE")
CLAUDE_KEEP = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")
CLAUDE_TOOLS = "Read,Edit,Write,Glob,Grep"  # the tools that exist for the run; no Bash


def _env(extra: tuple[str, ...] = ()) -> dict:
    return {k: os.environ[k] for k in (*ENV_KEEP, *extra) if k in os.environ}


def _run(cmd: list[str], timeout: float = 20, cwd: str | None = None, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env or _env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _clean(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("WARNING: proceeding")).strip()


def codex_sign_in(status_text: str, code: int) -> str:
    """chatgpt, api_key or none, from `codex login status`. The key itself is never kept."""
    t = status_text.lower()
    if "not logged in" in t or code not in (0,):
        return "none"
    if "chatgpt" in t:
        return "chatgpt"
    if "api key" in t:
        return "api_key"
    return "unknown"


class Run:
    def __init__(self, agent: str, task: str, folder: str, proc: subprocess.Popen):
        self.id, self.agent, self.task, self.folder, self.proc = uuid.uuid4().hex[:12], agent, task, folder, proc
        self.events: list[dict] = []
        self.result = ""
        self.error = ""
        self.status = "running"
        self.started = time.time()
        self.lock = threading.Lock()

    def add(self, ev: dict) -> None:
        with self.lock:
            self.events.append(ev)
            del self.events[:-MAX_EVENTS]


class CodingAgents:
    def __init__(self, cfg: dict, home: str):
        self.cfg, self.home = cfg, home
        self.runs: dict[str, Run] = {}
        self._probe: dict[str, str] = {}

    def _path(self, name: str) -> str | None:
        return (self.cfg.get("coding_agents") or {}).get(name) or shutil.which(name)

    # Detection
    def sandbox_probe(self, codex: str) -> str:
        """enforced, not_enforced or unknown: ask Codex's own sandbox to write outside a folder, and look."""
        if codex in self._probe:
            return self._probe[codex]
        base = os.path.join(self.home, "probe")
        os.makedirs(base, mode=0o700, exist_ok=True)
        work, outside = tempfile.mkdtemp(dir=base), tempfile.mkdtemp(dir=base)
        target = os.path.join(outside, "escaped")
        code, out = _run([codex, "sandbox", "-c", "sandbox_mode=workspace-write", "--", "sh", "-c",
                          f"echo x > '{target}'; echo y > inside"], timeout=30, cwd=work)
        inside = os.path.exists(os.path.join(work, "inside"))
        result = "not_enforced" if os.path.exists(target) else "enforced" if inside else "unknown"
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
        if result != "unknown":
            self._probe[codex] = result
        return result

    def detect(self) -> dict:
        out = {}
        codex = self._path("codex")
        if codex:
            code, ver = _run([codex, "--version"])
            scode, status = _run([codex, "login", "status"])
            sign_in = codex_sign_in(_clean(status), scode)
            sandbox = self.sandbox_probe(codex) if code == 0 else "unknown"
            ready, why = True, ""
            if code != 0:
                ready, why = False, "codex --version failed: " + _clean(ver)[:200]
            elif sign_in == "none":
                ready, why = False, "Codex is not signed in. On this computer run: codex login"
            elif sandbox != "enforced":
                ready, why = False, ("Codex's sandbox did not keep a test write inside its folder on this computer, so ROOKS will not "
                                     "run Codex here." if sandbox == "not_enforced" else "ROOKS could not confirm Codex's sandbox works on this computer.")
            out["codex"] = {"installed": True, "version": _clean(ver)[:80], "sign_in": sign_in, "sandbox": sandbox, "ready": ready, "reason": why,
                            "billing": {"chatgpt": "Your ChatGPT plan's Codex usage limits", "api_key": "OpenAI bills the API key Codex is signed in with"}
                            .get(sign_in, "Unknown until Codex is signed in")}
        else:
            out["codex"] = {"installed": False, "ready": False, "reason": "Codex CLI is not installed on this computer (see openai.com/codex)."}
        claude = self._path("claude")
        if claude:
            code, ver = _run([claude, "--version"])
            allowed = bool(self.cfg.get("claude_api_billing"))
            has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
            ready, why = code == 0 and allowed and has_key, ""
            if code != 0:
                why = "claude --version failed."
            elif not allowed:
                why = ("ROOKS can run Claude Code only with an Anthropic API key, billed per use, never with a Claude subscription. "
                       "To allow it, run: python3 -m rooks_companion allow-claude-api on")
            elif not has_key:
                why = "Set ANTHROPIC_API_KEY for the companion (your own key from console.anthropic.com), then restart it."
            out["claude"] = {"installed": True, "version": _clean(ver)[:80], "sign_in": "api_key" if has_key else "none", "ready": ready,
                             "reason": why, "billing": "Anthropic bills the API key per use (not your Claude subscription)"}
        else:
            out["claude"] = {"installed": False, "ready": False, "reason": "Claude Code is not installed on this computer."}
        return out

    # Running
    def start(self, agent: str, task: str, folder: str, folders: list[str], approved: bool) -> dict:
        if not approved:
            raise Refused("A coding task runs only after you approve it in ROOKS.")
        task = (task or "").strip()
        if not task:
            raise Refused("Describe the coding task.")
        if len(task) > MAX_TASK:
            raise Refused(f"The task is longer than {MAX_TASK} characters.")
        path = resolve(folder, folders)
        if not os.path.isdir(path):
            raise Refused(f"{folder} is not a folder.")
        if any(r.status == "running" for r in self.runs.values()):
            raise Refused("A coding task is already running on this computer. Wait for it or stop it.")
        info = self.detect().get(agent)
        if not info:
            raise Refused(f"Unknown coding agent {agent}.")
        if not info["ready"]:
            raise Refused(info["reason"])
        if agent == "codex":
            cmd = [self._path("codex"), "exec", "--json", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", path, task]
            env = _env()
        else:
            cmd = [self._path("claude"), "--bare", "-p", task, "--output-format", "stream-json", "--verbose",
                   "--permission-mode", "acceptEdits", f"--tools={CLAUDE_TOOLS}", "--disallowedTools=Bash"]
            env = _env(CLAUDE_KEEP)
        proc = subprocess.Popen(cmd, cwd=path, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
        run = Run(agent, task, path, proc)
        self.runs[run.id] = run
        threading.Thread(target=self._read, args=(run,), daemon=True).start()
        return {"run": run.id, "folder": path, "agent": agent}

    def _read(self, run: Run) -> None:
        err: list[str] = []
        threading.Thread(target=lambda: err.extend(run.proc.stderr.read().splitlines()[-20:]), daemon=True).start()
        for line in run.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            for item in (parse_codex if run.agent == "codex" else parse_claude)(ev):
                if item["kind"] == "final":
                    run.result = item["text"]
                elif item["kind"] == "error":
                    run.error = item["text"]
                run.add(item)
        code = run.proc.wait()
        if run.status == "running":
            if code == 0 and not run.error:
                run.status = "done"
            else:
                run.status = "failed"
                run.error = run.error or _clean("\n".join(err))[-800:] or f"The agent exited with code {code}."

    def poll(self, rid: str, since: int = 0) -> dict:
        run = self.runs.get(rid)
        if not run:
            raise Refused("That coding task is not running on this computer (the companion may have restarted).")
        if run.status == "running" and time.time() - run.started > RUN_SECONDS:
            self.stop(rid)
            run.error = f"Stopped after {RUN_SECONDS // 60} minutes."
        with run.lock:
            events = run.events[since:]
            n = len(run.events)
        return {"run": rid, "status": run.status, "events": events, "next": n, "result": run.result[:24000], "error": run.error[:2000]}

    def stop(self, rid: str) -> dict:
        run = self.runs.get(rid)
        if not run:
            raise Refused("That coding task is not running on this computer.")
        if run.status == "running":
            run.status = "cancelled"
            run.proc.terminate()
            try:
                run.proc.wait(10)
            except subprocess.TimeoutExpired:
                run.proc.kill()
        return {"run": rid, "status": run.status}


def parse_codex(ev: dict) -> list[dict]:
    """Codex `exec --json` events (thread.started, item.started/completed, turn.completed) as short progress items."""
    t, item = ev.get("type"), ev.get("item") or {}
    it = item.get("type")
    if t == "item.started" and it == "command_execution":
        return [{"kind": "command", "text": str(item.get("command", ""))[:300]}]
    if t == "item.completed":
        if it == "command_execution":
            return [{"kind": "command_done", "text": str(item.get("command", ""))[:300], "exit_code": item.get("exit_code")}]
        if it == "file_change":
            return [{"kind": "files", "text": ", ".join(str(c.get("path", "")) for c in item.get("changes", []))[:300]}]
        if it == "agent_message":
            return [{"kind": "final", "text": str(item.get("text", ""))}]
        if it == "error":
            return [{"kind": "note", "text": str(item.get("message", ""))[:300]}]
    if t in ("turn.failed", "error"):
        msg = (ev.get("error") or {}).get("message") if isinstance(ev.get("error"), dict) else ev.get("message")
        return [{"kind": "error", "text": str(msg or "Codex reported an error.")[:800]}]
    return []


def parse_claude(ev: dict) -> list[dict]:
    """Claude Code `--output-format stream-json` messages as short progress items."""
    t = ev.get("type")
    if t == "assistant":
        out = []
        for c in (ev.get("message") or {}).get("content", []):
            if c.get("type") == "tool_use":
                inp = c.get("input") or {}
                out.append({"kind": "command", "text": f"{c.get('name')} {inp.get('file_path') or inp.get('pattern') or ''}".strip()[:300]})
        return out
    if t == "result":
        if ev.get("is_error"):
            return [{"kind": "error", "text": str(ev.get("result") or ev.get("subtype") or "Claude Code reported an error.")[:800]}]
        return [{"kind": "final", "text": str(ev.get("result") or "")},
                *([{"kind": "cost", "text": f"Reported API cost: ${ev['total_cost_usd']:.4f}"}] if isinstance(ev.get("total_cost_usd"), (int, float)) else [])]
    return []
