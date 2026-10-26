"""Run the same goal on two providers at once and compare answers, steps and time.

    ANTHROPIC_API_KEY=... GEMINI_API_KEY=... python examples/compare_providers.py
"""

import asyncio

from browser_agent import run_task

GOAL = "Go to https://news.ycombinator.com and report the title and points of the current top 3 stories."


async def main() -> None:
    providers = ["anthropic", "gemini"]
    results = await asyncio.gather(*(run_task(GOAL, provider=p, max_steps=10) for p in providers), return_exceptions=True)
    for p, r in zip(providers, results):
        print(f"\n=== {p} ===")
        if isinstance(r, Exception):
            print(f"failed: {r}")
        else:
            print(f"{r.model}: {r.steps} steps, {r.duration_s}s, success={r.success}\n{r.final}")


if __name__ == "__main__":
    asyncio.run(main())
