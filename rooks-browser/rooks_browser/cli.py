"""rooks-browser: give it a task in plain English and watch it use a real browser.

  rooks-browser "find the cheapest USB-C hub on example-shop.com"   run a task (headed when a display is available)
  rooks-browser serve [--port 8765]          local HTTP API (Workbench's browser job API, plus approvals)
  rooks-browser mcp                          MCP server on stdio (Rooks Code, Claude Code, Codex, Cursor, ...)
  rooks-browser search "query"               one search, no model
  rooks-browser read URL                     one page's text, no model
  rooks-browser sites [list|allow|deny|remove HOST]   per-site lists, kept between runs
  rooks-browser receipts [list|show|import --db PATH] receipts in Workbench's run-log format
  rooks-browser doctor                       what is installed, which browsers and models are ready, install size
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
from pathlib import Path

from . import __version__, llm, receipts, runner, search
from .agent import StepEvent
from .policy import CATEGORIES, SitePolicy, parse_categories

SUBCOMMANDS = ("run", "serve", "mcp", "search", "read", "sites", "receipts", "doctor", "version")


def has_display() -> bool:
    if platform.system() in ("Darwin", "Windows"):
        return True
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def add_browser_args(p: argparse.ArgumentParser, headed_default: bool | None = None) -> None:
    g = p.add_argument_group("browser")
    mode = g.add_mutually_exclusive_group()
    mode.add_argument("--headed", dest="headed", action="store_true", default=None, help="show the browser window (watch it work)")
    mode.add_argument("--headless", dest="headed", action="store_false", help="no window")
    g.add_argument("--browser", dest="engine", default=os.getenv("ROOKS_BROWSER_ENGINE", "auto"),
                   choices=("auto", "chrome", "msedge", "chromium"), help="auto tries Chrome, then Edge, then Playwright's Chromium")
    g.add_argument("--cdp", dest="cdp_url", default=os.getenv("ROOKS_BROWSER_CDP_URL"),
                   help="attach to a Chrome you started with --remote-debugging-port, e.g. http://localhost:9222")
    g.add_argument("--allow", action="append", default=[], metavar="SITE", help="only these sites (repeatable, e.g. *.example.com)")
    g.add_argument("--deny", action="append", default=[], metavar="SITE", help="never these sites (repeatable)")
    g.add_argument("--slow-mo", type=int, default=0, metavar="MS", help="pause between browser operations (for watching)")


def add_model_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("model")
    g.add_argument("-P", "--provider", default=None, help=f"{', '.join(llm.PROVIDERS)} or auto")
    g.add_argument("-m", "--model", default=os.getenv("ROOKS_BROWSER_MODEL"))
    g.add_argument("--base-url", default=None, help="for openai-compatible, ollama or workbench")
    g.add_argument("--vision", choices=("auto", "on", "off"), default="auto", help="send screenshots to the model")


def settings_from(a, headed_default: bool) -> runner.Settings:
    headed = headed_default if a.headed is None else a.headed
    return runner.Settings(provider=getattr(a, "provider", None), model=getattr(a, "model", None), base_url=getattr(a, "base_url", None),
                           max_steps=getattr(a, "max_steps", 25), headless=not headed, engine=a.engine, cdp_url=a.cdp_url,
                           allow=a.allow, deny=a.deny, vision=getattr(a, "vision", "auto"),
                           pre_approved=parse_categories(getattr(a, "approve", None)), ask=parse_categories(getattr(a, "ask", None)),
                           start_url=getattr(a, "start_url", None), workbench_db=getattr(a, "workbench_db", None),
                           slow_mo=a.slow_mo, downloads=getattr(a, "downloads", None))


# --- run -------------------------------------------------------------------------------------------------------------
def _tty_approver(stream=sys.stderr):
    def ask(ap) -> bool:
        print(f"\n  ⚠  Needs your OK — {ap.description}\n     on {ap.url}", file=stream)
        if not sys.stdin or not sys.stdin.isatty():
            print("     (no terminal to ask: answered no)", file=stream)
            return False
        try:
            ans = input("     Allow? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        return ans in ("y", "yes")
    return ask


def cmd_run(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rooks-browser", description="Run a browser task.")
    p.add_argument("task", nargs="+")
    add_model_args(p)
    add_browser_args(p)
    p.add_argument("--max-steps", type=int, default=25)
    p.add_argument("--start-url", default=None, help="open this page first")
    p.add_argument("--approve", default=None, metavar="CATS",
                   help=f"pre-approve categories for this run ({', '.join(c for c in CATEGORIES if c != 'login')}, or all)")
    p.add_argument("--ask", default=None, metavar="CATS", help="also ask before these (download)")
    p.add_argument("--workbench-db", default=os.getenv("ROOKS_BROWSER_WORKBENCH_DB"), help="also write receipts into this Workbench database")
    p.add_argument("--downloads", default=None, help="folder for downloaded files")
    p.add_argument("--screenshots", default=None, metavar="DIR", help="save a screenshot after every step")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    a = p.parse_args(argv)
    goal = " ".join(a.task)
    try:
        s = settings_from(a, headed_default=has_display())
    except ValueError as e:
        p.error(str(e))
    out = sys.stderr if a.json else sys.stdout
    shots = Path(a.screenshots) if a.screenshots else None
    holder = {}

    async def on_event(ev: StepEvent):
        mark = {"ok": "✓", "denied": "✗", "blocked": "⛔", "error": "!", "bad_args": "?"}.get(ev.status, "·")
        print(f"  {ev.step:>2}. {ev.next_goal}", file=out)
        if ev.evaluation:
            print(f"      thinking: {ev.evaluation[:160]}", file=out)
        if ev.result and ev.result != "finished":
            print(f"      {mark} {ev.result.splitlines()[0][:160]}", file=out)
        out.flush()
        if shots and "agent" in holder:
            try:
                await holder["agent"].browser.screenshot(shots / f"step-{ev.step:02d}.png")
            except Exception:  # noqa: BLE001
                pass

    try:
        shown = llm.resolve(s.provider)
        shown = "AI Workspace app (smart router)" if shown == "workbench" else shown
    except ValueError as e:
        print(f"rooks-browser: {e}", file=sys.stderr)
        return 2
    print(f"Rooks Browser {__version__} · {shown} · {'headed' if not s.headless else 'headless'}", file=out)
    print(f"Task: {goal}", file=out)
    try:
        res = asyncio.run(runner.run_task(goal, s, approver=_tty_approver(out), on_event=on_event,
                                          on_agent=lambda ag: holder.update(agent=ag)))
    except (ValueError, RuntimeError) as e:
        print(f"rooks-browser: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        return 130
    if a.json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("", file=out)
        print("Answer (the agent's own claim; check it against the pages visited):" if res.done else "Not finished.", file=out)
        print(res.final or "; ".join(res.errors) or "(none)", file=out)
        print(f"\n{res.steps} steps · {res.duration_s}s · {len(res.urls)} pages · tokens {res.tokens_in}+{res.tokens_out}", file=out)
        for d in res.downloads:
            print(f"downloaded: {d['path']} ({d['bytes']} bytes)", file=out)
        for d in res.denied:
            print(f"you said no to: {d}", file=out)
        print(f"receipt: {res.receipt}", file=out)
    return 0 if res.done and res.success else 1


# --- one-offs --------------------------------------------------------------------------------------------------------
def cmd_search(argv):
    p = argparse.ArgumentParser(prog="rooks-browser search")
    p.add_argument("query", nargs="+")
    add_browser_args(p)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    res = asyncio.run(one_off_search(" ".join(a.query), settings_from(a, headed_default=False)))
    print(json.dumps(res, indent=2) if a.json else search.format_results(res["results"], res["source"], res.get("note", "")))
    return 0


async def one_off_search(query: str, s: runner.Settings) -> dict:
    from .browser import Browser
    log = receipts.RunLog(runner.home())
    base = search.searxng_url()
    if base:
        return {"source": "SearXNG", "results": search.searxng(base, query, log), "receipt": str(log.path)}
    policy = SitePolicy.load(runner.sites_file()).merged(s.allow, s.deny)
    async with Browser(policy=policy, log=log, headless=s.headless, engine=s.engine, cdp_url=s.cdp_url) as b:
        await b.open(search.fallback_url(query))
        results, note = await search.page_results(b.page, 8)
    return {"source": "browser search page", "results": results, "receipt": str(log.path)} | ({"note": note} if note else {})


async def one_off_read(url: str, s: runner.Settings, max_chars: int = 20000, screenshot: str | None = None) -> dict:
    from .browser import Browser
    log = receipts.RunLog(runner.home())
    policy = SitePolicy.load(runner.sites_file()).merged(s.allow, s.deny)
    async with Browser(policy=policy, log=log, headless=s.headless, engine=s.engine, cdp_url=s.cdp_url) as b:
        await b.open(url)
        snap = await b.snapshot()
        if screenshot:
            await b.screenshot(Path(screenshot))
    return {"url": snap.url, "title": snap.title, "text": snap.text.strip()[:max_chars],
            "links": [{"name": e["name"], "href": e["href"]} for e in snap.elements if e.get("href")][:60], "receipt": str(log.path)}


def cmd_read(argv):
    p = argparse.ArgumentParser(prog="rooks-browser read")
    p.add_argument("url")
    add_browser_args(p)
    p.add_argument("--screenshot", default=None, metavar="PNG")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    res = asyncio.run(one_off_read(a.url, settings_from(a, headed_default=False), screenshot=a.screenshot))
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else f"{res['title']}\n{res['url']}\n\n{res['text']}")
    return 0


def cmd_sites(argv):
    p = argparse.ArgumentParser(prog="rooks-browser sites")
    p.add_argument("action", nargs="?", default="list", choices=("list", "allow", "deny", "remove"))
    p.add_argument("site", nargs="?")
    a = p.parse_args(argv)
    path = runner.sites_file()
    pol = SitePolicy.load(path)
    if a.action != "list":
        if not a.site:
            p.error("give a site, e.g. example.com or *.example.com")
        pol.allow = [x for x in pol.allow if x != a.site]
        pol.deny = [x for x in pol.deny if x != a.site]
        if a.action == "allow":
            pol.allow.append(a.site)
        elif a.action == "deny":
            pol.deny.append(a.site)
        pol.save(path)
    print(f"allow: {', '.join(pol.allow) or '(any site not denied)'}\ndeny:  {', '.join(pol.deny) or '(none)'}\nfile:  {path}")
    return 0


def cmd_receipts(argv):
    p = argparse.ArgumentParser(prog="rooks-browser receipts")
    p.add_argument("action", nargs="?", default="list", choices=("list", "show", "import"))
    p.add_argument("session", nargs="?", help="session id (default: the latest)")
    p.add_argument("--db", help="Workbench database (workbench.db) for import")
    a = p.parse_args(argv)
    files = receipts.sessions(runner.home())
    if a.action == "list":
        if not files:
            print("no receipts yet")
        for f in files[-30:]:
            rows = receipts.read(f)
            turn = next((r for r in rows if r["kind"] == "turn"), {})
            pages = sum(1 for r in rows if r["kind"] == "egress" and r["tool"] == "browser.page")
            print(f"{f.stem}  {turn.get('outcome', '?'):<11} {pages:>3} pages  {turn.get('args', '')[:70]}")
        return 0
    chosen = [f for f in files if f.stem == a.session] if a.session else files[-1:]
    if not chosen:
        print("no such receipt")
        return 1
    if a.action == "show":
        for r in receipts.read(chosen[0]):
            print(f"{r['id']:>4} {r['kind']:<10} {r['outcome']:<12} {r['tool']:<18} {r['effect']:<8} {r['args'][:90]}")
        return 0
    if not a.db:
        p.error("import needs --db PATH (Workbench's workbench.db)")
    n = receipts.import_into(Path(a.db), chosen if a.session else files)
    print(f"imported {n} row(s) into {a.db} (channel {receipts.CHANNEL})")
    return 0


def cmd_doctor(argv):
    from .browser import installed_browsers
    print(f"Rooks Browser {__version__} on {platform.system()} {platform.machine()}, Python {platform.python_version()}")
    try:
        import importlib.metadata as md
        import playwright
        pw_dir = Path(playwright.__file__).parent
        size = sum(f.stat().st_size for f in pw_dir.rglob("*") if f.is_file())
        print(f"playwright {md.version('playwright')} ({size / 1e6:.0f} MB installed, includes its driver)")
    except ImportError:
        print("playwright: NOT INSTALLED (pip install playwright)")
    found = installed_browsers()
    for name in ("chrome", "msedge"):
        print(f"{name:<8} {found.get(name, 'not found')}")
    cache = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH") or (
        Path(os.getenv("LOCALAPPDATA", "")) / "ms-playwright" if platform.system() == "Windows" else
        Path.home() / ("Library/Caches/ms-playwright" if platform.system() == "Darwin" else ".cache/ms-playwright")))
    bundled = sorted(p.name for p in cache.glob("chromium*")) if cache.is_dir() else []
    print(f"chromium {'(Playwright) ' + ', '.join(bundled) if bundled else 'not downloaded (only needed without Chrome/Edge: python -m playwright install chromium)'}")
    if not found and not bundled:
        print("  -> no browser available: install Chrome or Edge, or run `python -m playwright install chromium`")
    print(f"display  {'yes (headed by default)' if has_display() else 'no (headless by default)'}")
    ready = llm.ready()
    for n, info in llm.PROVIDERS.items():
        if n == "scripted":
            continue
        print(f"model    {n:<18} {'ready' if n in ready else 'not set up'}" + (f"  ({' or '.join(info.key_env)})" if info.key_env and n not in ready else ""))
    sx = search.searxng_url()
    print(f"search   {'SearXNG at ' + sx if sx else 'browser search page (' + search.fallback_url('...').split('?')[0] + '); set SEARXNG_URL to use SearXNG'}")
    pol = SitePolicy.load(runner.sites_file())
    print(f"sites    allow {pol.allow or 'any'} · deny {pol.deny or 'none'}")
    print(f"data     {runner.home()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        print("\nRun options: rooks-browser run --help")
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(f"rooks-browser {__version__}")
        return 0
    cmd = argv[0] if argv[0] in SUBCOMMANDS else "run"
    rest = argv[1:] if argv[0] in SUBCOMMANDS else argv
    if cmd == "serve":
        from .server import main as serve
        return serve(rest)
    if cmd == "mcp":
        from .mcp_server import main as mcp
        return mcp(rest)
    return {"run": cmd_run, "search": cmd_search, "read": cmd_read, "sites": cmd_sites, "receipts": cmd_receipts,
            "doctor": cmd_doctor}[cmd](rest)


if __name__ == "__main__":
    raise SystemExit(main())
