"""Puts the pieces together for the CLI, the HTTP API and the MCP server: settings, site policy, receipt, browser,
model and agent."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from . import llm
from .agent import Agent, Approver, EventFn, TaskResult, deny_all
from .browser import Browser
from .policy import SitePolicy
from .receipts import RunLog


def home() -> Path:
    """Where receipts, downloads and the per-site lists live. ROOKS_BROWSER_HOME overrides."""
    if os.getenv("ROOKS_BROWSER_HOME"):
        return Path(os.environ["ROOKS_BROWSER_HOME"]).expanduser()
    if platform.system() == "Windows":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData/Roaming") / "ai-workspace" / "rooks-browser"
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/ai-workspace/rooks-browser"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share") / "ai-workspace" / "rooks-browser"


def sites_file() -> Path:
    return home() / "sites.json"


@dataclass
class Settings:
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    max_steps: int = 25
    headless: bool = True
    engine: str = "auto"
    cdp_url: str | None = None
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    vision: str = "auto"
    pre_approved: set[str] = field(default_factory=set)
    ask: set[str] = field(default_factory=set)  # extra categories to ask about (download)
    start_url: str | None = None
    workbench_db: str | None = None
    slow_mo: int = 0
    screenshot_events: bool = False
    downloads: str | None = None


async def run_task(goal: str, s: Settings, *, approver: Approver = deny_all, on_event: EventFn | None = None,
                   model: llm.Model | None = None, on_agent=None) -> TaskResult:
    h = home()
    policy = SitePolicy.load(sites_file()).merged(s.allow, s.deny)
    log = RunLog(h, workbench_db=Path(s.workbench_db) if s.workbench_db else None)
    m = model or llm.make(s.provider, s.model, base_url=s.base_url, api_key=s.api_key,
                          on_egress=lambda meth, url, outcome, ms, err: log.egress(meth, url, outcome, ms, None, tool="model", error=err))
    log.provider, log.model = m.provider, m.model
    browser = Browser(policy=policy, log=log, headless=s.headless, engine=s.engine, cdp_url=s.cdp_url,
                      downloads=Path(s.downloads) if s.downloads else h / "downloads", slow_mo=s.slow_mo)
    async with browser:
        agent = Agent(m, browser, approver=approver, pre_approved=s.pre_approved, ask_extra=s.ask, max_steps=s.max_steps,
                      vision=llm.sees(m.provider, m.model, s.vision), on_event=on_event, start_url=s.start_url,
                      screenshot_events=s.screenshot_events)
        if on_agent:
            on_agent(agent)
        return await agent.run(goal)
