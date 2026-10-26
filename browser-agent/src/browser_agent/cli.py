"""browser-agent command line.

    browser-agent run "Find three remote software engineer roles on weworkremotely.com"
    browser-agent open books.toscrape.com --screenshot out/books.png
    browser-agent click https://books.toscrape.com "text=Travel"
    browser-agent type https://quotes.toscrape.com/login "#username" "demo" --submit
    browser-agent screenshot https://news.ycombinator.com -o hn.png --full-page
    browser-agent text https://books.toscrape.com --selector h1
    browser-agent links https://books.toscrape.com
    browser-agent providers
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import actions
from .llm import PROVIDERS, available, resolve_provider


def _print(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            if v not in (None, "", []):
                print(f"{k}: {v}")
    else:
        print(data)


async def _run(args) -> int:
    from .agent import run_task

    def on_step(e) -> None:
        if not args.json:
            acts = ", ".join(next(iter(a)) for a in e.actions if a) or "thinking"
            print(f"[{e.step}] {e.next_goal or e.evaluation}  ->  {acts}  ({e.url})", file=sys.stderr)

    result = await run_task(
        args.goal, provider=args.provider, model=args.model, max_steps=args.max_steps,
        headless=not args.headed, allowed_domains=args.allow_domain or None, on_step=on_step,
    )
    if args.json:
        _print(result.to_dict(), True)
    else:
        print()
        print(result.final or "(no final answer)")
        print(f"\n{result.steps} steps · {result.duration_s}s · {result.provider}/{result.model} · "
              f"{'success' if result.success else 'not confirmed successful' if result.done else 'stopped before done'}",
              file=sys.stderr)
    return 0 if result.done else 2


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine readable output")
    common.add_argument("--headed", action="store_true", help="show the browser window")
    p = argparse.ArgumentParser(prog="browser-agent", description="AI browser agent and one-off browser actions.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, **kw) -> argparse.ArgumentParser:  # every subcommand accepts --json and --headed
        return sub.add_parser(name, parents=[common], **kw)

    r = add("run", help="give the agent a goal in plain English")
    r.add_argument("goal")
    r.add_argument("--provider", choices=["auto", *PROVIDERS], default=None, help="default: auto")
    r.add_argument("--model", help="model id for the provider")
    r.add_argument("--max-steps", type=int, default=25, help="hard cap on steps (and cost), default 25")
    r.add_argument("--allow-domain", action="append", help="restrict browsing, e.g. --allow-domain '*.pcpartpicker.com'")

    o = add("open", help="open a URL and print its title")
    o.add_argument("url")
    o.add_argument("--screenshot", help="also save a screenshot to this path")

    c = add("click", help="open a URL and click an element")
    c.add_argument("url")
    c.add_argument("selector", help="Playwright selector: CSS, text=..., role=..., xpath=...")
    c.add_argument("--screenshot", help="screenshot after the click")

    t = add("type", help="open a URL and type into a field")
    t.add_argument("url")
    t.add_argument("selector")
    t.add_argument("text")
    t.add_argument("--submit", action="store_true", help="press Enter afterwards")
    t.add_argument("--screenshot")

    s = add("screenshot", help="save a screenshot of a page or element")
    s.add_argument("url")
    s.add_argument("-o", "--output", default="screenshot.png")
    s.add_argument("--full-page", action="store_true")
    s.add_argument("--selector", help="only this element")

    x = add("text", help="print the visible text of a page or element")
    x.add_argument("url")
    x.add_argument("--selector")

    lk = add("links", help="list the links on a page")
    lk.add_argument("url")

    add("providers", help="show model providers and which have credentials")
    return p


async def _dispatch(args) -> int:
    headless = not args.headed
    if args.cmd == "run":
        return await _run(args)
    if args.cmd == "open":
        _print((await actions.open_url(args.url, screenshot=args.screenshot, headless=headless)).to_dict(), args.json)
    elif args.cmd == "click":
        _print((await actions.click(args.url, args.selector, screenshot=args.screenshot, headless=headless)).to_dict(), args.json)
    elif args.cmd == "type":
        info = await actions.type_text(args.url, args.selector, args.text, submit=args.submit, screenshot=args.screenshot, headless=headless)
        _print(info.to_dict(), args.json)
    elif args.cmd == "screenshot":
        info = await actions.screenshot(args.url, args.output, full_page=args.full_page, selector=args.selector, headless=headless)
        _print(info.to_dict(), args.json)
    elif args.cmd == "text":
        _print(await actions.page_text(args.url, selector=args.selector, headless=headless), args.json)
    elif args.cmd == "links":
        found = await actions.links(args.url, headless=headless)
        if args.json:
            _print(found, True)
        else:
            for item in found:
                print(f"{item['text'] or '(no text)'}\t{item['href']}")
    elif args.cmd == "providers":
        have = set(available())
        chosen = resolve_provider(None)
        rows = [{"provider": n, "ready": n in have, "default_model": p.default_model, "auto": n == chosen, "notes": p.notes}
                for n, p in PROVIDERS.items()]
        if args.json:
            _print(rows, True)
        else:
            for r in rows:
                mark = "*" if r["auto"] else " "
                print(f"{mark} {r['provider']:<18} {'ready' if r['ready'] else 'no key':<7} {r['default_model'] or '(pass --model)':<22} {r['notes']}")
            print("\n* = what 'auto' picks right now")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001 - CLI: one clear line instead of a traceback
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
