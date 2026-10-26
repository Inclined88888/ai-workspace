"""Stream each step as it happens and save a screenshot per step.

    python examples/live_steps.py "Find the latest release version of Python on python.org"
"""

import asyncio
import base64
import sys
from pathlib import Path

from browser_agent import StepEvent, run_task

OUT = Path("out/steps")


async def on_step(e: StepEvent) -> None:
    print(f"[{e.step}] {e.title or e.url}")
    if e.evaluation:
        print(f"     last step: {e.evaluation}")
    print(f"     next:      {e.next_goal}")
    for action in e.actions:
        print(f"     action:    {action}")
    if e.screenshot:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"step-{e.step:02d}.png").write_bytes(base64.b64decode(e.screenshot))


async def main(goal: str) -> None:
    result = await run_task(goal, max_steps=15, on_step=on_step, include_screenshots=True)
    print(f"\nAnswer: {result.final}\nScreenshots in {OUT}/")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Find the latest release version of Python on python.org"))
