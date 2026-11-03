"""Puts the pieces together for the CLI, the HTTP API and the MCP server: settings, policy, receipt, backend, kill
switch, overlay, model and agent."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import platform
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import llm
from .agent import Agent, Approver, Desk, EventFn, TaskResult, deny_all
from .backends import get_backend
from .killswitch import HOTKEY_LABEL, KillSwitch
from .policy import DeskPolicy
from .receipts import RunLog

MAX_ATTACH = 20 * 1024 * 1024


def home() -> Path:
    """Where receipts, screenshots, the trash and settings live. ROOKS_DESK_HOME overrides."""
    if os.getenv("ROOKS_DESK_HOME"):
        return Path(os.environ["ROOKS_DESK_HOME"]).expanduser()
    if platform.system() == "Windows":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData/Roaming") / "ai-workspace" / "rooks-desk"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/ai-workspace/rooks-desk"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share") / "ai-workspace" / "rooks-desk"


def policy_file() -> Path:
    return home() / "policy.json"


def mcp_file() -> Path:
    return home() / "mcp.json"


def stop_file() -> Path:
    return home() / "STOP"


def load_policy() -> DeskPolicy:
    p = DeskPolicy.load(policy_file())
    extra = os.getenv("ROOKS_DESK_FOLDERS")
    if extra:
        p.folders = [*p.folders, *[f for f in extra.split(os.pathsep) if f]]
    return p


def load_mcp_servers() -> dict:
    try:
        d = json.loads(mcp_file().read_text(encoding="utf-8"))
        return d.get("mcpServers") or d.get("servers") or {}
    except (OSError, ValueError):
        return {}


def workbench_mailer(link: dict | None = None):
    """Send through the person's own mail account in AI Workspace (POST /v1/desk/send_email). Files travel inside the
    request, so it works when the app runs in Docker or on another machine."""
    def send(msg: dict) -> str:
        lk = link or llm.app_link()
        if not lk:
            raise ValueError("email goes through AI Workspace: open the app and connect a mail account under Settings > Mail")
        atts = []
        total = 0
        for p in msg.get("attachments") or []:
            path = Path(os.path.expanduser(str(p)))
            data = path.read_bytes()
            total += len(data)
            if total > MAX_ATTACH:
                raise ValueError("attachments are over 20 MB together; most mail servers refuse that")
            atts.append({"filename": path.name, "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                         "content_b64": base64.b64encode(data).decode()})
        body = json.dumps({"to": msg.get("to"), "cc": msg.get("cc") or "", "subject": msg.get("subject") or "",
                           "body": msg.get("body") or "", "attachments": atts}).encode()
        req = urllib.request.Request(f"{lk['url']}/v1/desk/send_email", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {lk.get('key', '')}",
                                              "X-Workbench-Channel": "rooks-desk"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            raise ValueError(f"the app could not send it: {e.read().decode(errors='replace')[:300]}") from None
        return f"sent to {msg.get('to')} from {d.get('from', 'your account')}" + (f" with {len(atts)} attachment(s)" if atts else "")
    return send


@dataclass
class Settings:
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_steps: int = 40
    backend: str | None = None
    vision: str = "auto"
    pre_approved: set[str] = field(default_factory=set)
    dry_run: bool = False
    folders: list[str] = field(default_factory=list)  # added to the saved allow-list for this run
    allow_apps: list[str] = field(default_factory=list)
    overlay: bool = True
    killswitch: bool = True
    monitor: int = 1
    workbench_db: str | None = None
    mcp: bool = True
    settle: float = 0.6  # seconds to let the screen catch up after an action


def build_desk(s: Settings, log: RunLog, backend=None, mailer=None) -> Desk:
    h = home()
    policy = load_policy()
    if s.folders:
        policy.folders = [*policy.folders, *s.folders]
    if s.allow_apps:
        policy.allow_apps = [*policy.allow_apps, *s.allow_apps]
    b = backend or get_backend(s.backend)
    real = b.name != "fake"
    kill = KillSwitch(stop_file(), hotkey=s.killswitch and real, corner=real)
    mcp = None
    servers = load_mcp_servers() if s.mcp else {}
    if servers:
        from .mcp_client import MCPManager
        mcp = MCPManager(servers, str(Path.home()))
    return Desk(b, policy, log, home=h, kill=kill, dry_run=s.dry_run, mcp=mcp, mailer=mailer or workbench_mailer(), monitor=s.monitor)


def run_task(goal: str, s: Settings, *, approver: Approver = deny_all, on_event: EventFn | None = None,
             model: llm.Model | None = None, on_agent=None, backend=None, mailer=None) -> TaskResult:
    h = home()
    log = RunLog(h, workbench_db=Path(s.workbench_db) if s.workbench_db else None)
    m = model or llm.make(s.provider, s.model, base_url=s.base_url, api_key=s.api_key,
                          on_egress=lambda meth, url, outcome, ms, err: log.egress(meth, url, outcome, ms, None, tool="model", error=err))
    log.provider, log.model = m.provider, m.model
    desk = build_desk(s, log, backend, mailer)
    overlay = None
    if s.overlay and desk.b.name != "fake" and not s.dry_run:
        from .overlay import Overlay
        overlay = Overlay(desk.kill.trip, HOTKEY_LABEL)
    try:
        agent = Agent(m, desk, approver=approver, pre_approved=s.pre_approved, max_steps=s.max_steps,
                      vision=llm.sees(m.provider, m.model, s.vision), on_event=on_event, overlay=overlay,
                      settle=0.0 if desk.b.name == "fake" else s.settle)
        if on_agent:
            on_agent(agent)
        return agent.run(goal)
    finally:
        if overlay:
            overlay.close()
        desk.kill.close()
        if desk.mcp:
            desk.mcp.close()
