"""rooks-desk: give it a task in plain English and watch it use your computer: apps, mouse, keyboard, terminal, files.

  rooks-desk "open Mousepad, write a haiku and save it as ~/Documents/haiku.txt"   run a task
  rooks-desk run --dry-run "..."             plan it and show every step without doing anything
  rooks-desk serve [--port 8766]             local HTTP API (the app's Computer page talks to this)
  rooks-desk mcp                             MCP server on stdio (Rooks Code, Claude Code, Codex, Cursor, ...)
  rooks-desk setup                           permissions walkthrough (macOS Screen Recording + Accessibility)
  rooks-desk doctor                          what works on this computer
  rooks-desk stop                            stop a running task from any terminal (same as Ctrl+Alt+Esc)
  rooks-desk folders [list|add|remove PATH]  folders it may read and write without asking
  rooks-desk apps [list|allow|deny|remove APP]   app allow and deny lists
  rooks-desk mcp-servers [list|add NAME -- CMD ARGS...|remove NAME|trust NAME]   other MCP servers it may use
  rooks-desk screenshot [FILE]               save a screenshot (and print the element list)
  rooks-desk receipts [list|show [SESSION]|import --db PATH]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, llm, receipts, runner
from .agent import StepEvent
from .killswitch import HOTKEY_LABEL
from .policy import CATEGORIES, NEVER_PREAPPROVED, DeskPolicy, parse_categories

SUBCOMMANDS = ("run", "serve", "mcp", "setup", "doctor", "stop", "folders", "apps", "mcp-servers", "screenshot", "receipts", "version")


def _tty_approver(stream=sys.stderr):
    def ask(ap) -> bool:
        print(f"\n  ⚠  Needs your OK ({ap.category}) — {ap.summary}", file=stream)
        if ap.detail and ap.category not in ("handoff",):
            print(f"     {ap.detail[:300]}", file=stream)
        if not sys.stdin or not sys.stdin.isatty():
            print("     (no terminal to ask: answered no)", file=stream)
            return False
        try:
            ans = input("     Allow? [y/N] " if ap.category != "handoff" else "     Done? [y = continue / N = stop] ").strip().lower()
        except EOFError:
            ans = ""
        return ans in ("y", "yes")
    return ask


def cmd_run(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rooks-desk", description="Run a task on this computer.")
    p.add_argument("task", nargs="+")
    p.add_argument("-P", "--provider", default=None, help=f"{', '.join(llm.PROVIDERS)} or auto")
    p.add_argument("-m", "--model", default=os.getenv("ROOKS_DESK_MODEL"))
    p.add_argument("--base-url", default=None)
    p.add_argument("--vision", choices=("auto", "on", "off"), default="auto", help="send screenshots to the model")
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--dry-run", action="store_true", help="show what it would do; touch nothing")
    p.add_argument("--approve", default=None, metavar="CATS",
                   help=f"pre-approve for this run: {', '.join(c for c in CATEGORIES if c not in NEVER_PREAPPROVED)} or all "
                        "(danger and login always ask)")
    p.add_argument("--folder", action="append", default=[], help="also allow this folder for this run (repeatable)")
    p.add_argument("--app", action="append", default=[], help="only these apps for this run (repeatable)")
    p.add_argument("--backend", default=os.getenv("ROOKS_DESK_BACKEND"), help="macos, windows, linux or fake")
    p.add_argument("--monitor", type=int, default=1, help="which screen to look at (1 = main)")
    p.add_argument("--no-overlay", action="store_true", help="no on-screen bar (not recommended)")
    p.add_argument("--workbench-db", default=os.getenv("ROOKS_DESK_WORKBENCH_DB"))
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        s = runner.Settings(provider=a.provider, model=a.model, base_url=a.base_url, max_steps=a.max_steps, backend=a.backend,
                            vision=a.vision, pre_approved=parse_categories(a.approve), dry_run=a.dry_run, folders=a.folder,
                            allow_apps=a.app, overlay=not a.no_overlay, monitor=a.monitor, workbench_db=a.workbench_db)
    except ValueError as e:
        p.error(str(e))
    out = sys.stderr if a.json else sys.stdout
    print(f"Rooks Desk {'(dry run) ' if a.dry_run else ''}· press {HOTKEY_LABEL} or move the mouse to the top-left corner to stop", file=out)

    def on_event(ev: StepEvent):
        mark = {"ok": "✓", "denied": "✗", "blocked": "⛔", "tool_error": "!", "unknown_tool": "?"}.get(ev.status, "·")
        print(f"  {ev.step:>2}. {ev.action} {json.dumps(ev.args, ensure_ascii=False)[:120]}", file=out)
        if ev.thought:
            print(f"      thinking: {ev.thought[:160]}", file=out)
        if ev.result and ev.result != "finished":
            print(f"      {mark} {ev.result.splitlines()[0][:200]}", file=out)
        out.flush()

    try:
        res = runner.run_task(" ".join(a.task), s, approver=_tty_approver(out), on_event=on_event)
    except KeyboardInterrupt:
        print("\nstopped (Ctrl+C)", file=out)
        return 130
    except ValueError as e:
        print(f"rooks-desk: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"\n{'✓ done' if res.success else ('■ stopped' if res.stopped else '✗ not finished')}: {res.final or '; '.join(res.errors)}")
        print(f"  receipt: {res.receipt}\n  screenshots: {res.screenshots}")
    return 0 if res.success else 1


def cmd_setup(argv) -> int:
    from .backends import get_backend
    b = get_backend(None)
    print(f"Rooks Desk setup ({b.name})\n")
    perms = b.permissions()
    for k, v in perms.items():
        print(f"  {'✓' if v['ok'] else '✗'} {k}{'' if v['ok'] else ': ' + v['how']}")
    if sys.platform == "darwin" and not all(v["ok"] for v in perms.values()):
        print("\nmacOS will now ask. Allow both, then quit and reopen the app you run rooks-desk from.")
        b.request_permissions()
        for which in ("screen", "input"):
            if not perms[which]["ok"]:
                b.open_settings(which)
    if sys.platform == "win32":
        from .backends.windows import UAC_NOTE
        print("\n  Note: " + UAC_NOTE)
    print(f"\nKill switch: {HOTKEY_LABEL}, or move the mouse to the top-left corner, or `rooks-desk stop`.")
    return 0


def cmd_doctor(argv) -> int:
    import platform as pf
    print(f"rooks-desk {__version__} on {pf.system()} {pf.release()} (Python {pf.python_version()})")
    print(f"  home: {runner.home()}")
    try:
        from .backends import get_backend
        b = get_backend(None)
        print(f"  backend: {b.name}")
        for m in b.monitors():
            if m.index:
                print(f"  screen {m.index}: {m.width}x{m.height} at ({m.left},{m.top}){' main' if m.primary else ''}")
        for k, v in b.permissions().items():
            print(f"  {'✓' if v['ok'] else '✗'} {k}{'' if v['ok'] else ': ' + v['how']}")
        ok, why = b.ax_available()
        print(f"  {'✓' if ok else '✗'} element lists: {why}")
        wins = b.windows()
        print(f"  windows: {len(wins)} open" + (f", in front: {wins[0].app} — {wins[0].title!r}" if wins else ""))
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ backend: {e}")
    try:
        import tkinter  # noqa: F401
        print("  ✓ on-screen bar (tkinter)")
    except Exception:  # noqa: BLE001
        print("  ✗ on-screen bar: tkinter is missing for this Python (the kill switch still works)")
    from . import clipboard
    try:
        clipboard._cmds()[0] or (_ for _ in ()).throw(RuntimeError("none"))
        print("  ✓ clipboard")
    except Exception:  # noqa: BLE001
        print("  ✗ clipboard: install xclip or xsel")
    ready = llm.ready()
    print(f"  models ready: {', '.join(ready) or 'none (open AI Workspace, or set GEMINI_API_KEY / GROQ_API_KEY)'}")
    pol = runner.load_policy()
    print(f"  allowed folders: {', '.join(pol.folders)}")
    print(f"  denied apps: {', '.join(pol.deny_apps)}")
    print(f"  MCP servers: {', '.join(runner.load_mcp_servers()) or 'none'}")
    print(f"  kill switch: {HOTKEY_LABEL} · top-left corner · rooks-desk stop")
    return 0


def cmd_stop(argv) -> int:
    f = runner.stop_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("stop", encoding="utf-8")
    import urllib.request
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{os.getenv('ROOKS_DESK_PORT', '8766')}/api/stop", data=b"",
                                                      method="POST"), timeout=2).close()
    except OSError:
        pass
    print("Stop sent. A running task stops before its next action.")
    return 0


def _edit_list(argv, key: str, label: str) -> int:
    p = argparse.ArgumentParser(prog=f"rooks-desk {label}")
    p.add_argument("op", nargs="?", default="list")
    p.add_argument("value", nargs="?")
    a = p.parse_args(argv)
    pol = DeskPolicy.load(runner.policy_file())
    if a.op != "list":
        if not a.value:
            p.error("give a value")
        if label == "folders":
            target, val = "folders", str(Path(a.value).expanduser().resolve())
            if a.op == "add":
                pol.folders.append(val)
            elif a.op == "remove":
                pol.folders = [f for f in pol.folders if str(Path(f).expanduser().resolve()) != val]
        else:
            if a.op == "allow":
                pol.allow_apps.append(a.value)
                pol.deny_apps = [x for x in pol.deny_apps if x.lower() != a.value.lower()]
            elif a.op == "deny":
                pol.deny_apps.append(a.value)
                pol.allow_apps = [x for x in pol.allow_apps if x.lower() != a.value.lower()]
            elif a.op == "remove":
                pol.allow_apps = [x for x in pol.allow_apps if x.lower() != a.value.lower()]
                pol.deny_apps = [x for x in pol.deny_apps if x.lower() != a.value.lower()]
            else:
                p.error("allow, deny or remove")
        pol.save(runner.policy_file())
    if label == "folders":
        for f in pol.folders:
            print(f"  {f}{'' if Path(f).expanduser().is_dir() else '  (does not exist yet)'}")
    else:
        print(f"  allow: {', '.join(pol.allow_apps) or '(any app not denied)'}\n  deny: {', '.join(pol.deny_apps)}")
    return 0


def cmd_mcp_servers(argv) -> int:
    f = runner.mcp_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"mcpServers": {}}
    servers = data.setdefault("mcpServers", {})
    op = argv[0] if argv else "list"
    if op == "add":
        if "--" not in argv or len(argv) < 4:
            print("usage: rooks-desk mcp-servers add NAME -- COMMAND [ARGS...]", file=sys.stderr)
            return 2
        name = argv[1]
        cmd = argv[argv.index("--") + 1:]
        servers[name] = {"command": cmd[0], "args": cmd[1:]}
    elif op == "remove" and len(argv) > 1:
        servers.pop(argv[1], None)
    elif op == "trust" and len(argv) > 1:
        pol = DeskPolicy.load(runner.policy_file())
        pol.trusted_mcp = sorted(set(pol.trusted_mcp) | {argv[1]})
        pol.save(runner.policy_file())
        print(f"{argv[1]}'s tools no longer ask before each call")
    if op in ("add", "remove"):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    trusted = set(DeskPolicy.load(runner.policy_file()).trusted_mcp)
    for n, s in servers.items():
        print(f"  {n}: {s.get('command')} {' '.join(s.get('args', []))}{'  (trusted)' if n in trusted else ''}")
    if not servers:
        print("  none. Example: rooks-desk mcp-servers add autocad -- uvx autocad-mcp-pro")
    return 0


def cmd_screenshot(argv) -> int:
    from .agent import Desk
    from .receipts import RunLog
    path = Path(argv[0] if argv else "rooks-desk-screenshot.png")
    d = Desk(__import__("rooks_desk.backends", fromlist=["get_backend"]).get_backend(None), runner.load_policy(),
             RunLog(runner.home()), home=runner.home())
    obs = d.observe()
    path.write_bytes(obs.png)
    print(obs.describe())
    print(f"\nsaved {path} ({obs.image.width}x{obs.image.height})")
    return 0


def cmd_receipts(argv) -> int:
    p = argparse.ArgumentParser(prog="rooks-desk receipts")
    p.add_argument("op", nargs="?", default="list", choices=("list", "show", "import"))
    p.add_argument("session", nargs="?")
    p.add_argument("--db")
    a = p.parse_args(argv)
    files = receipts.sessions(runner.home())
    if a.op == "list":
        for f in files[-30:]:
            rows = receipts.read(f)
            turn = next((r for r in rows if r["kind"] == "turn"), {})
            print(f"  {f.stem}  {turn.get('outcome', ''):<10} {str(turn.get('args', ''))[:70]}")
        return 0
    if a.op == "show":
        f = next((x for x in files if a.session and a.session in x.stem), files[-1] if files else None)
        if not f:
            print("no receipts yet")
            return 1
        for r in receipts.read(f):
            print(f"  {r['id']:>3} {r['kind']:<10} {r['outcome']:<10} {r['tool']:<20} {str(r['args'])[:80]}")
        print(f"\n  screenshots: {f.with_suffix('')}")
        return 0
    if not a.db:
        p.error("import needs --db PATH")
    print(f"imported {receipts.import_into(Path(a.db), files)} row(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("version", "--version"):
        print(__version__)
        return 0
    if cmd == "serve":
        from .server import main as serve_main
        return serve_main(rest)
    if cmd == "mcp":
        from .mcp_server import main as mcp_main
        return mcp_main(rest)
    handlers = {"setup": cmd_setup, "doctor": cmd_doctor, "stop": cmd_stop, "mcp-servers": cmd_mcp_servers,
                "screenshot": cmd_screenshot, "receipts": cmd_receipts,
                "folders": lambda r: _edit_list(r, "folders", "folders"), "apps": lambda r: _edit_list(r, "apps", "apps")}
    if cmd in handlers:
        return handlers[cmd](rest)
    if cmd == "run":
        return cmd_run(rest)
    return cmd_run(argv)


if __name__ == "__main__":
    sys.exit(main())
