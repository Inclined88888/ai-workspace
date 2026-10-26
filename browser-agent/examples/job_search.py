"""Find three remote software engineer roles and return them as structured data.

    python examples/job_search.py                       # auto provider
    python examples/job_search.py --provider gemini
"""

import argparse
import asyncio
import json

from browser_agent import run_task

GOAL = """Go to https://weworkremotely.com/categories/remote-full-stack-programming-jobs and find three
remote software engineer roles posted most recently. For each give: title, company, region restrictions
(if listed) and the job URL. Reply with only a JSON array of objects with keys title, company, region, url."""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider")
    ap.add_argument("--model")
    args = ap.parse_args()

    result = await run_task(
        GOAL,
        provider=args.provider,
        model=args.model,
        max_steps=20,
        allowed_domains=["*.weworkremotely.com", "weworkremotely.com"],
        on_step=lambda e: print(f"  step {e.step}: {e.next_goal}"),
    )
    print(f"\n{result.provider}/{result.model}: {result.steps} steps in {result.duration_s}s")
    try:
        jobs = json.loads(result.final or "")
        for j in jobs:
            print(f"- {j['title']} at {j['company']} ({j.get('region') or 'anywhere'})\n  {j['url']}")
    except (ValueError, TypeError, KeyError):
        print(result.final)


if __name__ == "__main__":
    asyncio.run(main())
