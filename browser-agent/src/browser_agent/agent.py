"""Run a plain English goal in a real browser with browser-use doing the step by step work."""

from __future__ import annotations

import inspect
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from .llm import make_llm


@dataclass
class StepEvent:
    step: int
    url: str
    title: str
    evaluation: str
    memory: str
    next_goal: str
    actions: list[dict[str, Any]]
    screenshot: str | None = None  # base64 PNG, only when include_screenshots=True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResult:
    goal: str
    provider: str
    model: str
    success: bool | None
    done: bool
    final: str | None
    steps: int
    urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


StepCallback = Callable[[StepEvent], Any] | Callable[[StepEvent], Awaitable[Any]]


def browser_options(headless: bool | None = None, allowed_domains: list[str] | None = None) -> dict[str, Any]:
    """BrowserProfile options, overridable by env for containers and custom Chrome installs."""
    opts: dict[str, Any] = {
        "headless": headless if headless is not None else os.getenv("BROWSER_HEADLESS", "true").lower() != "false",
    }
    if os.getenv("CHROME_PATH"):
        opts["executable_path"] = os.environ["CHROME_PATH"]
    if allowed_domains:
        opts["allowed_domains"] = allowed_domains
    # Service mode: keep the agent off IP address URLs and internal hostnames (other containers, LAN)
    if os.getenv("BROWSER_BLOCK_IP_ADDRESSES", "").lower() == "true":
        opts["block_ip_addresses"] = True
    blocked = [d.strip() for d in os.getenv("BROWSER_PROHIBITED_DOMAINS", "").split(",") if d.strip()]
    if blocked:
        opts["prohibited_domains"] = blocked
    return opts


async def run_task(
    goal: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    llm: Any = None,
    max_steps: int = 25,
    headless: bool | None = None,
    allowed_domains: list[str] | None = None,
    use_vision: bool | str = "auto",
    on_step: StepCallback | None = None,
    include_screenshots: bool = False,
) -> TaskResult:
    """Give the agent a goal and let it browse until it is done or runs out of steps.

    provider/model pick the LLM (see llm.PROVIDERS), or pass a ready browser-use chat model as `llm`.
    allowed_domains restricts navigation, e.g. ["*.pcpartpicker.com"]. max_steps caps cost.
    """
    from browser_use import Agent, Browser, BrowserProfile

    if llm is None:
        llm, provider_name, model_name = make_llm(provider, model)
    else:
        provider_name, model_name = provider or "custom", model or getattr(llm, "model", "custom")

    async def _step(state, output, n: int) -> None:
        if on_step is None:
            return
        actions = []
        for a in getattr(output, "action", None) or []:
            try:
                actions.append(a.model_dump(exclude_none=True))
            except Exception:  # noqa: BLE001 - never let reporting break the run
                actions.append({"action": str(a)})
        event = StepEvent(
            step=n,
            url=getattr(state, "url", ""),
            title=getattr(state, "title", ""),
            evaluation=getattr(output, "evaluation_previous_goal", None) or "",
            memory=getattr(output, "memory", None) or "",
            next_goal=getattr(output, "next_goal", None) or "",
            actions=actions,
            screenshot=getattr(state, "screenshot", None) if include_screenshots else None,
        )
        result = on_step(event)
        if inspect.isawaitable(result):
            await result

    browser = Browser(browser_profile=BrowserProfile(**browser_options(headless, allowed_domains)))
    agent = Agent(task=goal, llm=llm, browser=browser, use_vision=use_vision, register_new_step_callback=_step)
    started = time.monotonic()
    try:
        history = await agent.run(max_steps=max_steps)
    finally:
        # Agent closes the browser itself unless keep_alive is set; this covers early failures too.
        try:
            await browser.kill()
        except Exception:  # noqa: BLE001
            pass
    return TaskResult(
        goal=goal,
        provider=provider_name,
        model=model_name,
        success=history.is_successful(),
        done=history.is_done(),
        final=history.final_result(),
        steps=history.number_of_steps(),
        urls=[u for u in history.urls() if u],
        errors=[e for e in history.errors() if e],
        duration_s=round(time.monotonic() - started, 1),
    )
