"""rooks: ai-workspace's terminal coding agent. Any model: OpenAI, Anthropic, OpenRouter, any OpenAI-compatible server,
or local Ollama / LM Studio (works fully offline).

  rooks                         interactive session in the current folder
  rooks "fix the failing test"  one prompt, then exit (prints the final answer; exit 1 if it did not finish)
  rooks undo                    put back the files changed by the last prompt
  rooks receipts [list|show|import --db PATH]
  rooks -P claude-code "task"   hand the job to your own signed-in Claude Code (your Claude plan); also -P codex
  rooks doctor                  check the provider, key and MCP servers
  rooks init                    write an example config file
  rooks mcp                     list MCP servers and their tools

Every edit (shown as a diff) and every command is asked about first, unless the folder is trusted, --yes is given, or
you answer "a" (always, for this session). --dry-run shows what would change and changes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__, config, receipts
from .undo import ChangeSets

SUBCOMMANDS = ("undo", "receipts", "doctor", "init", "mcp", "version")


class UI:
    def __init__(self, out=sys.stdout, err=sys.stderr, color: bool | None = None):
        self.out, self.err = out, err
        self.color = (out.isatty() and not os.environ.get("NO_COLOR")) if color is None else color
        self._spin: threading.Event | None = None
        if self.color and sys.platform == "win32":
            os.system("")  # enables ANSI escape handling in the Windows console

    def c(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def p(self, text: str = "", err: bool = False) -> None:
        print(text, file=self.err if err else self.out, flush=True)

    def warn(self, text: str) -> None:
        self.p(self.c("33", f"! {text}"), err=True)

    def error(self, text: str) -> None:
        self.p(self.c("31", f"✗ {text}"), err=True)

    def assistant(self, text: str, final: bool) -> None:
        self.p(("" if final else self.c("2", "· ")) + text.strip())

    def tool_note(self, text: str, read: bool = False) -> None:
        self.p(self.c("2", f"  {'→' if read else '·'} {text}"))

    def show_plan(self, plan) -> None:
        self.p(self.c("1", f"  ● {plan.summary}"))
        if plan.preview:
            for line in plan.preview.rstrip("\n").splitlines()[:400]:
                code = "32" if line.startswith("+") and not line.startswith("+++") else (
                    "31" if line.startswith("-") and not line.startswith("---") else "2")
                self.p("    " + self.c(code, line))

    def ask(self, plan) -> str:
        label = {"write": "Apply this change", "command": "Run this command", "external": "Call this tool"}[plan.effect]
        prompt = f"  {label}? [y]es / [n]o / [a]lways this session: "
        while True:
            self.out.write(self.c("36", prompt))
            self.out.flush()
            line = sys.stdin.readline()
            if not line:
                self.p("n (no input: denied)")
                return "n"
            if not sys.stdin.isatty():
                self.p(line.strip())  # piped answers are echoed so transcripts read naturally
            a = line.strip().lower()[:1]
            if a in ("y", "n", "a"):
                return a

    def tool_result(self, plan, result: str, outcome: str) -> None:
        if plan.effect == "command":
            lines = result.splitlines()
            shown = lines[:30]
            for l in shown:
                self.p(self.c("2", f"    │ {l}"))
            if len(lines) > 30:
                self.p(self.c("2", f"    │ ... {len(lines) - 30} more lines"))
        mark = {"verified": self.c("32", "✓ verified"), "ok": self.c("32", "✓ ok"), "tool_error": self.c("31", "✗ failed"),
                "check_failed": self.c("31", "✗ check failed")}.get(outcome, outcome)
        if plan.effect != "read":
            self.p(f"    {mark}" + (f"  {result.splitlines()[0]}" if plan.effect != "command" and result else ""))

    def thinking(self, on: bool) -> None:
        if not self.color:
            return
        if on:
            self._spin = threading.Event()
            stop = self._spin

            def spin():
                frames, i = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", 0
                while not stop.wait(0.1):
                    self.out.write(f"\r{frames[i % 10]} thinking"); self.out.flush(); i += 1
                self.out.write("\r" + " " * 12 + "\r"); self.out.flush()
            threading.Thread(target=spin, daemon=True).start()
        elif self._spin:
            self._spin.set(); time.sleep(0.12); self._spin = None

    def footer(self, totals: dict, s, undo: bool) -> None:
        cost = "cost unknown" if totals["cost_unknown"] else f"${totals['usd']:.4f}"
        cap = []
        if s.max_tokens:
            cap.append(f"{s.max_tokens:,} tokens")
        if s.max_usd:
            cap.append(f"${s.max_usd:.2f}")
        self.p(self.c("2", f"  [{s.provider}/{s.model} · {totals['tokens_in'] + totals['tokens_out']:,} tokens · {cost}"
                           f"{' · cap ' + ' / '.join(cap) if cap else ''}{' · /undo available' if undo else ''}]"))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rooks", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="*", help="one prompt to run, then exit; omit for an interactive session")
    ap.add_argument("-P", "--provider", help=f"one of {', '.join(config.PROVIDERS)} or a [providers.NAME] from the config")
    ap.add_argument("-m", "--model", help="model id, e.g. gpt-4.1-mini, claude-sonnet-4-5, qwen2.5:7b")
    ap.add_argument("--base-url", help="API base URL (for openai-compatible servers)")
    ap.add_argument("-C", "--cwd", help="project folder (default: current folder)")
    ap.add_argument("-y", "--yes", action="store_true", help="approve every edit and command without asking")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show edits and commands, apply nothing")
    ap.add_argument("--max-usd", type=float, help="hard cost cap for this session in USD (0 = none)")
    ap.add_argument("--max-tokens", type=int, help="hard token cap for this session (0 = none)")
    ap.add_argument("--max-steps", type=int, help="model calls per prompt before stopping (default 40)")
    ap.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--version", action="version", version=f"rooks {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]  (Windows consoles without UTF-8)
        except AttributeError:
            pass
    if argv and argv[0] in SUBCOMMANDS:
        return subcommand(argv[0], argv[1:])
    args = parser().parse_args(argv)
    root = Path(args.cwd or os.getcwd()).resolve()
    if not root.is_dir():
        sys.exit(f"rooks: {root} is not a folder")
    s = config.resolve({"provider": args.provider, "model": args.model, "base_url": args.base_url, "yes": args.yes,
                        "dry_run": args.dry_run, "max_usd": args.max_usd, "max_tokens": args.max_tokens,
                        "max_steps": args.max_steps}, root)
    if args.no_mcp:
        s.mcp_servers = {}
    ui = UI(color=False if args.no_color else None)
    if s.kind == "delegate":
        from . import delegate
        return delegate.main(s.provider, s, ui, " ".join(args.prompt) if args.prompt else None)
    if s.key_env and not s.api_key and not s.key_optional:
        ui.error(f"{s.provider} needs an API key in the {s.key_env} environment variable (or use --provider ollama)")
        return 2
    from .agent import Agent
    agent = Agent(s, ui)
    try:
        if args.prompt:
            answer = agent.run(" ".join(args.prompt))
            outcome = agent.log.rows[min(r for r in agent.log.rows if agent.log.rows[r]["kind"] == "turn")]["outcome"]
            return 0 if outcome in ("no_claim", "backed") else 1
        return repl(agent, ui)
    finally:
        agent.close()


HELP = """/undo     put back the files changed by the last prompt
/cost     tokens and cost so far
/dry-run  toggle dry run          /yes   toggle auto-approve
/model X  switch model (same provider)
/receipt  path of this session's receipt
/clear    forget the conversation /exit  quit (or Ctrl-D)"""


def repl(agent, ui: UI) -> int:
    s = agent.s
    mode = " · trusted folder" if s.trusted() else ""
    mode += " · DRY RUN" if s.dry_run else ""
    mode += " · auto-approve" if s.auto_approve else ""
    tools = len(agent.tool_specs())
    ui.p(ui.c("1", f"rooks {__version__}") + ui.c("2", f" · {s.provider}/{s.model} · {agent.root}{mode} · {tools} tools"))
    ui.p(ui.c("2", "Type a task, /help for commands, Ctrl-D to quit."))
    while True:
        try:
            if sys.stdin.isatty():
                line = input(ui.c("36", "› "))
            else:
                ui.out.write("› "); ui.out.flush()
                line = sys.stdin.readline()
                if not line:
                    raise EOFError
                line = line.rstrip("\n")
                ui.p(line)
        except EOFError:
            ui.p()
            return 0
        except KeyboardInterrupt:
            ui.p()
            continue
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            if cmd in ("/exit", "/quit"):
                return 0
            if cmd == "/help":
                ui.p(HELP)
            elif cmd == "/undo":
                ui.p(undo_text(agent.changes))
            elif cmd == "/cost":
                t = agent.log.totals()
                ui.p(f"{t['tokens_in']:,} in + {t['tokens_out']:,} out tokens, "
                     f"{'cost unknown' if t['cost_unknown'] else '$%.4f' % t['usd']}, {t['model_calls']} model calls")
            elif cmd == "/dry-run":
                s.dry_run = not s.dry_run
                ui.p(f"dry run {'on' if s.dry_run else 'off'}")
            elif cmd == "/yes":
                s.auto_approve = not s.auto_approve
                ui.p(f"auto-approve {'on' if s.auto_approve else 'off'}")
            elif cmd == "/model" and rest.strip():
                s.model = agent.model.model = agent.log.model = rest.strip()
                ui.p(f"model is now {s.model}")
            elif cmd == "/receipt":
                ui.p(str(agent.log.path))
            elif cmd == "/clear":
                agent.messages.clear()
                ui.p("conversation cleared")
            else:
                ui.p(f"unknown command {cmd}; /help lists them")
            continue
        agent.run(line)


def undo_text(changes: ChangeSets) -> str:
    r = changes.undo()
    if r is None:
        return "nothing to undo"
    label, files = r
    return f"undid the changes from {label[:80]!r}:\n" + "\n".join(f"  restored {f}" for f in files)


def subcommand(name: str, rest: list[str]) -> int:
    ap = argparse.ArgumentParser(prog=f"rooks {name}")
    ap.add_argument("-C", "--cwd", default=os.getcwd())
    if name == "receipts":
        ap.add_argument("action", nargs="?", default="list", choices=("list", "show", "import"))
        ap.add_argument("session", nargs="?", help="session id (default: newest)")
        ap.add_argument("--db", help="Workbench database for import (e.g. the workbench.db in its data volume)")
        ap.add_argument("--all", action="store_true", help="import every session")
    if name == "init":
        ap.add_argument("--force", action="store_true")
    if name == "doctor":
        ap.add_argument("-P", "--provider")
        ap.add_argument("-m", "--model")
        ap.add_argument("--base-url")
    a = ap.parse_args(rest)
    root = Path(a.cwd).resolve()
    if name == "version":
        print(f"rooks {__version__}")
    elif name == "undo":
        print(undo_text(ChangeSets(root)))
    elif name == "init":
        p = config.config_path()
        if p.exists() and not a.force:
            print(f"{p} already exists (use --force to overwrite)")
            return 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(config.EXAMPLE)
        print(f"wrote {p}")
    elif name == "receipts":
        files = receipts.sessions(root)
        if a.action == "list":
            if not files:
                print("no receipts in this folder yet")
            for f in files:
                rows = receipts.read(f)
                turns = [r for r in rows if r["kind"] == "turn"]
                calls = sum(r["kind"] == "tool_call" for r in rows)
                cost = [r["usd_cost"] for r in rows if r["kind"] == "model_call"]
                usd = "cost unknown" if any(c is None for c in cost) else f"${sum(cost):.4f}"
                print(f"{f.stem}  {len(turns)} prompt(s), {calls} tool call(s), {usd}  "
                      f"[{', '.join(t['outcome'] for t in turns)}]")
        else:
            chosen = [f for f in files if f.stem == a.session] if a.session else files[-1:]
            if a.action == "import" and a.all:
                chosen = files
            if not chosen:
                print("no such session")
                return 1
            if a.action == "show":
                for r in receipts.read(chosen[0]):
                    pad = "  " if r["parent_action_id"] else ""
                    v = {True: " ✓", False: " ✗"}.get(r["verified"], "")
                    print(f"{pad}#{r['id']} {r['kind']:<10} {r['outcome']:<13}{v} {r['tool'] or r['provider']} "
                          f"{(r['evidence'] or r['args'] or r['failure_reason'])[:100]}")
            else:
                if not a.db:
                    print("rooks receipts import needs --db PATH (Workbench's workbench.db)")
                    return 2
                n = receipts.import_into(Path(a.db), chosen)
                print(f"imported {n} row(s) into {a.db} (channel {receipts.CHANNEL})")
    elif name == "doctor":
        return doctor(root, a)
    elif name == "mcp":
        s = config.resolve({"provider": "ollama", "model": "x"}, root)
        if not s.mcp_servers:
            print(f"no MCP servers configured ([mcp.servers.NAME] in {config.config_path()} or .mcp.json in the project)")
            return 0
        from .mcp import MCPManager
        m = MCPManager(s.mcp_servers, str(root))
        try:
            for name_, srv in m.servers.items():
                print(f"{name_}{' (trusted)' if name_ in m.trusted else ''}: {len(srv.tools)} tools")
                for t in srv.tools:
                    print(f"  mcp__{name_}__{t['name']}  {t.get('description', '')[:80]}")
        finally:
            m.close()
    return 0


def doctor(root: Path, a) -> int:
    from . import providers
    ok = True
    print(f"rooks {__version__}, Python {sys.version.split()[0]}, {sys.platform}")
    print(f"config: {config.config_path()} ({'found' if config.config_path().exists() else 'not found, using defaults'})")
    s = config.resolve({"provider": a.provider, "model": a.model, "base_url": a.base_url}, root)
    if s.kind == "delegate":
        from . import delegate
        return delegate.doctor(s.provider)
    print(f"provider: {s.provider} ({s.kind}) at {s.base_url}, model {s.model}")
    if s.key_env:
        print(f"api key: {s.key_env} is {'set' if s.api_key else 'NOT set'}")
        ok &= bool(s.api_key) or s.key_optional
    try:
        r = providers.make(s).complete("Reply with the single word: ready", [{"role": "user", "content": "ping"}], [])
        print(f"model call: ok ({r.text.strip()[:40]!r}, {r.tokens_in}+{r.tokens_out} tokens)")
    except providers.ProviderError as e:
        ok = False
        print(f"model call: FAILED: {e}")
    print(f"trusted folder: {'yes' if s.trusted() else 'no'} ({root})")
    if s.mcp_servers:
        from .mcp import MCPManager
        m = MCPManager(s.mcp_servers, str(root), warn=lambda msg: print(msg))
        for n, srv in m.servers.items():
            print(f"mcp {n}: ok, {len(srv.tools)} tools")
        ok &= len(m.servers) == len([x for x in s.mcp_servers.values() if not x.get("disabled")])
        m.close()
    return 0 if ok else 1
