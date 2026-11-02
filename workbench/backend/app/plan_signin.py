"""Install and sign in to Claude Code and Codex from the app, so a plan works without ever opening a terminal.

Both sign-ins are the vendors' own flows, driven exactly as a person would in a terminal:
- Claude: `claude auth login --claudeai` prints Anthropic's sign-in link; after signing in, Anthropic's page shows a code,
  which the user pastes back here and we type into the CLI. Status: `claude auth status --json` (loggedIn).
- Codex: `codex login --device-auth` prints OpenAI's device link and a one-time code; the CLI finishes by itself once the
  user approves. Status: `codex login status` (exit 0 when signed in).
The app never sees a password or a token: the CLI stores its own credentials where it always does.
Once signed in, connect() adds the plan as a chat model (the cli_plan connection, adapters.CliPlan).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import cli_agents, llm
from .security import require_user

router = APIRouter(prefix="/api/plans", dependencies=[Depends(require_user)])
PACKAGES = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
PRESET = {"claude": "claude-plan", "codex": "codex-plan"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
URL = re.compile(r"https://[^\s\"'<>]+")
DEVICE_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,6})\b")
SESSIONS: dict[str, "SignIn"] = {}
_install_lock = threading.Lock()


def _agent(name: str) -> str:
    if name not in PACKAGES:
        raise HTTPException(404, "Unknown plan: claude or codex")
    return name


def _run(args: list[str], timeout: float = 20, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin, env=_env())


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *cli_agents._extra_dirs()])
    env["NO_COLOR"] = "1"
    env["BROWSER"] = "true" if sys.platform != "win32" else ""  # the app opens the link itself; no second tab
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        env.pop(k, None)  # the plan sign-in must not be satisfied by the app's own API keys
    return env


def signed_in(agent: str) -> tuple[bool, str]:
    """(signed in with a plan, account) from the CLI itself. A missing CLI is simply not signed in."""
    exe = cli_agents.find_binary(agent)
    if not exe:
        return False, ""
    try:
        if agent == "claude":
            r = _run([exe, "auth", "status", "--json"])
            data = json.loads(r.stdout or "{}")
            return bool(data.get("loggedIn")) and data.get("authMethod") != "apiKey", data.get("email") or ""
        r = _run([exe, "login", "status"])
        text = (r.stdout + r.stderr).strip()
        return r.returncode == 0 and "chatgpt" in text.lower(), ""
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False, ""


def _connection(agent: str) -> dict | None:
    return llm.plan_connection(PRESET[agent], create=False)


def state() -> dict:
    out = {}
    for agent in PACKAGES:
        exe = cli_agents.find_binary(agent)
        ok, account = signed_in(agent) if exe else (False, "")
        conn = _connection(agent)
        out[agent] = {"installed": bool(exe), "signed_in": ok, "account": account, "can_install": bool(shutil.which("npm", path=_env()["PATH"])),
                      "connected": bool(conn and conn["enabled"]), "name": "Claude" if agent == "claude" else "ChatGPT",
                      "terms": cli_agents.AGENTS[agent]["terms"]}
    return out


def install(agent: str) -> dict:
    """npm install -g of the vendor's own package. Never sudo: a permission error is reported, not escalated."""
    npm = shutil.which("npm", path=_env()["PATH"])
    if not npm:
        raise HTTPException(400, "Node.js is needed to install it. Get it from nodejs.org, then press Install again.")
    if not _install_lock.acquire(blocking=False):
        raise HTTPException(409, "Already installing")
    try:
        r = subprocess.run([npm, "install", "-g", PACKAGES[agent]], capture_output=True, text=True, timeout=600, env=_env())
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "The install took more than 10 minutes. Check your connection and try again.") from None
    finally:
        _install_lock.release()
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        hint = " npm needs permission to install globally; see docs.npmjs.com/resolving-eacces-permissions-errors." if "EACCES" in r.stderr else ""
        raise HTTPException(400, f"Install failed: {' '.join(tail)[:400]}.{hint}")
    if not cli_agents.find_binary(agent):
        raise HTTPException(400, "Installed, but the program isn't on this computer's PATH yet. Restart the app and try again.")
    return state()[agent]


class SignIn:
    """One running sign-in: the CLI on a pseudo-terminal (it expects one), its output scanned for the link and code."""

    def __init__(self, agent: str, args: list[str]):
        self.id, self.agent, self.started = uuid.uuid4().hex, agent, time.time()
        self.text, self.url, self.code, self.error = "", "", "", ""
        self.status = "starting"  # starting | waiting | done | failed
        if sys.platform == "win32":
            self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env())
            self.fd = None
        else:
            import pty

            master, slave = pty.openpty()
            self.proc = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, env=_env(), close_fds=True, start_new_session=True)
            os.close(slave)
            self.fd = master
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        while True:
            try:
                chunk = os.read(self.fd, 4096) if self.fd is not None else self.proc.stdout.read1(4096)  # type: ignore[union-attr]
            except OSError:
                chunk = b""
            if not chunk:
                break
            self.text = (self.text + ANSI.sub("", chunk.decode("utf-8", "replace")))[-20000:]
            if not self.url:
                urls = [u.rstrip(".,)") for u in URL.findall(self.text)]
                pick = [u for u in urls if "authorize" in u or "device" in u] or urls
                if pick:
                    self.url = pick[0]
            if self.agent == "codex" and not self.code:
                m = DEVICE_CODE.search(self.text)
                if m:
                    self.code = m.group(1)
            if self.url and self.status == "starting":
                self.status = "waiting"
        self.proc.wait()
        self._finish()

    def _finish(self) -> None:
        ok, _ = signed_in(self.agent)
        if ok:
            self.status = "done"
        elif self.status != "failed":
            self.status = "failed"
            lines = [ln.strip() for ln in self.text.splitlines() if ln.strip() and "http" not in ln]
            self.error = (lines[-1] if lines else "Sign-in didn't finish.")[:300]

    def type_code(self, code: str) -> None:
        data = (code.strip() + "\r").encode()
        if self.fd is not None:
            os.write(self.fd, data)
        else:
            self.proc.stdin.write(data)  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()

    def out(self) -> dict:
        if self.status in ("starting", "waiting") and time.time() - self.started > 900:
            self.stop()
            self.status, self.error = "failed", "The sign-in expired after 15 minutes. Start again."
        return {"id": self.id, "agent": self.agent, "status": self.status, "url": self.url, "code": self.code,
                "needs_code": self.agent == "claude", "error": self.error}


def start(agent: str) -> dict:
    exe = cli_agents.find_binary(agent)
    if not exe:
        raise HTTPException(400, "Install it first")
    for s in list(SESSIONS.values()):
        if s.agent == agent:
            s.stop()
            SESSIONS.pop(s.id, None)
    args = [exe, "auth", "login", "--claudeai"] if agent == "claude" else [exe, "login", "--device-auth"]
    s = SignIn(agent, args)
    SESSIONS[s.id] = s
    for _ in range(150):  # the link appears within a second or two
        if s.url or s.status in ("done", "failed"):
            break
        time.sleep(0.1)
    if not s.url and s.status != "done":
        s.stop()
        raise HTTPException(502, "The sign-in didn't give a link. Try again in a moment.")
    return s.out()


def session(sid: str) -> SignIn:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "That sign-in has ended. Start again.")
    return s


def connect(agent: str) -> dict:
    """Add the signed-in plan as a chat model (or turn it back on)."""
    ok, _ = signed_in(agent)
    if not ok:
        raise HTTPException(400, "Not signed in yet")
    cid = llm.plan_connection(PRESET[agent], create=True)["id"]
    return {**state()[agent], "connection_id": cid}


class CodeIn(BaseModel):
    code: str = Field(..., min_length=4, max_length=400)


@router.get("")
def plans():
    return state()


@router.post("/{agent}/install")
def plans_install(agent: str):
    return install(_agent(agent))


@router.post("/{agent}/signin")
def plans_signin(agent: str):
    return start(_agent(agent))


@router.get("/signin/{sid}")
def plans_signin_status(sid: str):
    return session(sid).out()


@router.post("/signin/{sid}/code")
def plans_signin_code(sid: str, body: CodeIn):
    s = session(sid)
    if s.agent != "claude":
        raise HTTPException(400, "This sign-in finishes in the browser; no code to paste")
    s.type_code(body.code)
    for _ in range(300):
        if s.status in ("done", "failed"):
            break
        time.sleep(0.1)
    return s.out()


@router.delete("/signin/{sid}")
def plans_signin_cancel(sid: str):
    session(sid).stop()
    SESSIONS.pop(sid, None)
    return {"ok": True}


@router.post("/{agent}/connect")
def plans_connect(agent: str):
    return connect(_agent(agent))
