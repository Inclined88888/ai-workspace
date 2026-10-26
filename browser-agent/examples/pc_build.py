"""Pick parts for a custom PC on PCPartPicker. The agent is locked to that site with allowed_domains.

    python examples/pc_build.py "1440p gaming, AM5, 32GB RAM, under 1200 USD total"
"""

import asyncio
import sys

from browser_agent import run_task


async def main(brief: str) -> None:
    goal = f"""Use https://pcpartpicker.com to put together a compatible build for: {brief}.
Pick a CPU, motherboard, RAM, GPU, storage, PSU and case. Use PCPartPicker's compatibility filters.
Finish with a Markdown table: part type, product name, price, and a link to each part. Then the total price
and any compatibility warnings PCPartPicker shows. Do not create an account or buy anything."""
    result = await run_task(
        goal,
        max_steps=40,
        allowed_domains=["pcpartpicker.com", "*.pcpartpicker.com"],
        on_step=lambda e: print(f"  step {e.step}: {e.next_goal}  ({e.url})"),
    )
    print("\n" + (result.final or "No final answer; try more steps or a stronger model."))


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "1440p gaming, AM5, 32GB RAM, under 1200 USD total"))
