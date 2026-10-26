"""Browser agent: plain English goals in, browser actions out.

    import asyncio
    from browser_agent import run_task

    result = asyncio.run(run_task("Find three remote software engineer roles on a job site"))
    print(result.final)
"""

from .agent import StepEvent, TaskResult, run_task
from .llm import PROVIDERS, make_llm, resolve_provider

__all__ = ["run_task", "TaskResult", "StepEvent", "make_llm", "resolve_provider", "PROVIDERS"]
__version__ = "0.1.0"
