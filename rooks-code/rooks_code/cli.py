"""rooks: ai-workspace's terminal coding agent. Any model: OpenAI, Anthropic, OpenRouter, any OpenAI-compatible server,
or local Ollama / LM Studio (works fully offline).

  rooks                         interactive session in the current folder
  rooks "fix the failing test"  one prompt, then exit (prints the final answer; exit 1 if it did not finish)
  rooks undo                    put back the files changed by the last prompt
  rooks receipts [list|show|import --db PATH]
  rooks -P claude-code "task"   hand the job to your own signed-in Claude Code (your Claude plan); also -P codex
  rooks doctor                  check the provider, key and MCP servers
  rooks login                   set up a model: connect to Workbench, paste a key, or use Ollama
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
from . import theme as term_theme
from .undo import ChangeSets

SUBCOMMANDS = ("undo", "receipts", "doctor", "init", "login", "mcp", "models", "version")


class UI:
    def __init__(self, out=sys.stdout, err=sys.stderr, color: bool | None = None, theme: str | None = None):
        self.out, self.err = out, err
        self.color = (out.isatty() and not os.environ.get("NO_COLOR")) if color is None else color
        self.theme = term_theme.load(theme)  # follows the look chosen in AI Workspace; see theme.py
        self._spin: threading.Event | None = None
        if self.color and sys.platform == "win32":
            os.system("")  # enables ANSI escape handling in the Windows console

    def c(self, code: str, text: str) -> str:
        """Colour text by role ('accent', 'ok', 'error', 'warn', 'dim', 'prompt', 'bold'); old ANSI codes map onto roles."""
        if not self.color:
            return text
        role = term_theme.LEGACY_CODES.get(code, code)
        return f"\033[{self.theme.sgr(role)}m{text}\033[0m"

    def p(self, text: str = "", err: bool = False) -> None:
        print(text, file=self.err if err else self.out, flush=True)

    def warn(self, text: str) -> None:
        self.p(self.c("33", f"! {text}"), err=True)

    def error(self, text: str) -> None:
        self.p(self.c("31", f"✗ {text}"), err=True)

    def assistant(self, text: str, final: bool) -> None:
        self.p(("" if final else self.c("2", "· ")) + text.strip())

    def token(self, text: str, style: str) -> None:
        """A streamed delta: text plain, reasoning dimmed. Written raw; the agent's thinking spinner is off by now."""
        if style == "reasoning":
            self.out.write(self.c("2", text))
        else:
            self.out.write(text)
        self.out.flush()

    def stream_end(self) -> None:
        self.out.write("\n")
        self.out.flush()

    def tool_note(self, text: str, read: bool = False) -> None:
        self.p(self.c("2", f"  {'→' if read else '·'} {text}"))

    def show_plan(self, plan) -> None:
        self.p(self.c("accent", f"  ● {plan.summary}"))
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
    ap.add_argument("-c", "--continue", dest="continue_", action="store_true",
                    help="resume the newest session for this folder")
    ap.add_argument("--session", help="resume the session with this id")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--theme", choices=term_theme.THEMES, help="terminal colours (default: the theme chosen in AI Workspace, or $ROOKS_THEME)")
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
    ui = UI(color=False if args.no_color else None, theme=getattr(args, "theme", None))
    if s.kind == "delegate":
        from . import delegate
        return delegate.main(s.provider, s, ui, " ".join(args.prompt) if args.prompt else None)
    if s.key_env and not s.api_key and not s.key_optional:
        ui.error(f"{s.provider} needs an API key in the {s.key_env} environment variable (or use --provider ollama)")
        return 2
    from .agent import Agent
    from .tools import is_home_or_root
    where = is_home_or_root(root)
    if where == "home":
        ui.warn(f"you are in your home folder ({root}); search and list stop early here. "
                f"Run rooks -C <project> for the folder you are working on.")
    elif where == "root":
        ui.warn(f"you are in a filesystem root ({root}); search and list stop early here. "
                f"Run rooks -C <project> for the folder you are working on.")
    session = None
    if args.continue_ or args.session:
        from . import sessions as _sessions
        found = _sessions.newest_for(root) if args.continue_ else (
            (lambda x: (_sessions.load(args.session)[0], _sessions.load(args.session)[1]) if x else None)(
                _sessions.load(args.session)[0]))
        if found and found[0]:
            meta, _msgs = found
            session = _sessions.SessionStore.__new__(_sessions.SessionStore)
            session.id = meta["id"]
            session.path = _sessions.sessions_dir() / f"{meta['id']}.jsonl"
            ui.p(ui.c("2", f"  resumed session {meta['id']} ({meta.get('model', '')})"))
        else:
            ui.warn("no saved session for this folder" if args.continue_ else f"no session {args.session}")
    agent = Agent(s, ui, session=session)
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
/sessions list saved sessions   /resume ID  switch to one
/compact  compact the context now
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
            elif cmd == "/model":
                if rest.strip():
                    s.model = agent.model.model = agent.log.model = rest.strip()
                    ui.p(f"model is now {s.model}")
                else:
                    from . import providers as _p
                    ids = _p.list_models(s.base_url, s.api_key, s.headers)
                    if not ids:
                        ui.p("could not list models from this provider (is the key and base_url right?)")
                    else:
                        ui.p(f"{len(ids)} models; /model NAME switches. Current: {s.model}")
                        for i, m in enumerate(ids[:40], 1):
                            ui.p(f"  {i:>2}. {m}")
            elif cmd == "/provider" and rest.strip():
                from . import config as _cfg
                name = rest.strip()
                if name not in _cfg.PROVIDERS and name not in s.__dict__.get("_custom", {}):
                    cfg_file = _cfg.load_file()
                    if name not in cfg_file.get("providers", {}):
                        ui.p(f"unknown provider {name!r}; built in: {', '.join(_cfg.PROVIDERS)}")
                        continue
                try:
                    ns = _cfg.resolve({"provider": name}, agent.root)
                except SystemExit as e:
                    ui.p(str(e))
                    continue
                agent.s.provider, agent.s.model, agent.s.base_url, agent.s.key_env = ns.provider, ns.model, ns.base_url, ns.key_env
                from . import providers as _p
                agent.model = _p.make(ns, on_egress=agent._egress, on_token=agent._token)
                ui.p(f"provider {ns.provider}, model {ns.model} (conversation kept)")
            elif cmd == "/receipt":
                ui.p(str(agent.log.path))
            elif cmd == "/sessions":
                from . import sessions as _sessions
                rows = _sessions.list_all(agent.root)
                if not rows:
                    ui.p("no saved sessions for this folder")
                for r in rows[:20]:
                    import time as _t
                    age = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["created"])) if r["created"] else ""
                    ui.p(f"  {r['id']}  {r['model']}  {r['messages']} messages  {age}")
            elif cmd == "/resume" and rest.strip():
                from . import sessions as _sessions
                meta, msgs = _sessions.load(rest.strip())
                if not meta:
                    ui.p(f"no session {rest.strip()}")
                else:
                    agent.messages = msgs
                    ui.p(f"resumed {meta['id']}: {len(msgs)} messages")
            elif cmd == "/compact":
                from . import sessions as _sessions
                ui.p("compacted" if _sessions.compact(agent, ui.warn) else "nothing to compact (too short)")
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
    if name == "models":
        ap.add_argument("-P", "--provider")
        ap.add_argument("-m", "--model")
        ap.add_argument("--base-url")
    a = ap.parse_args(rest)
    root = Path(a.cwd).resolve()
    if name == "login":
        from . import login
        return login.main()
    if name == "models":
        from . import providers as _p
        try:
            s = config.resolve({"provider": a.provider, "model": a.model or "x", "base_url": a.base_url}, root)
        except SystemExit as e:
            print(e)
            return 1
        ids = _p.list_models(s.base_url, s.api_key, s.headers)
        if not ids:
            print(f"could not list models from {s.provider} at {s.base_url} (key set? base_url right?)")
            return 1
        for i, m in enumerate(ids, 1):
            print(f"{i:>3}. {m}")
        return 0
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
    """Every check is printed, with a one-line fix per failure. Never exits before the last check."""
    from . import providers
    ok = True
    print(f"rooks {__version__}, Python {sys.version.split()[0]}, {sys.platform}")
    cfg_file = config.config_path()
    found = cfg_file.exists()
    print(f"config file: {cfg_file} ({'found' if found else 'not found'})"
          + ("" if found else "  -> run `rooks login` or `rooks init`"))
    env_file = config._env_path()
    stored = config._stored_keys()
    if stored:
        print(f"stored keys: {len(stored)} ({'keychain' if _keyring_ok() else str(env_file)})")
    try:
        s = config.resolve({"provider": a.provider, "model": a.model, "base_url": a.base_url}, root)
    except SystemExit as e:
        print(f"provider: FAILED: {e}")
        print("  -> run `rooks login` to pick a model, or set WORKBENCH_URL for a Docker Workbench")
        s = None
        ok = False
    if s is not None:
        if s.kind == "delegate":
            from . import delegate
            return delegate.doctor(s.provider)
        print(f"provider: {s.provider} ({s.kind}) at {s.base_url}, model {s.model}")
        if s.key_env:
            have = bool(s.api_key)
            src = "environment" if os.environ.get(s.key_env) else "stored" if have else "NOT set"
            print(f"api key: {s.key_env} is {src}")
            if not have and not s.key_optional:
                ok = False
                print(f"  -> run `rooks login` and paste the key, or export {s.key_env}=...")
        if s.provider == "workbench":
            probe = config.workbench_probe(s.base_url.removesuffix("/v1"))
            print(f"workbench reachable: {'yes' if probe else 'NO'} ({s.base_url.removesuffix('/v1')})")
            if not probe:
                ok = False
                print("  -> start the app or the Docker Workbench, or set WORKBENCH_URL")
        try:
            r = providers.make(s).complete("Reply with the single word: ready", [{"role": "user", "content": "ping"}], [])
            print(f"model call: ok ({r.text.strip()[:40]!r}, {r.tokens_in}+{r.tokens_out} tokens)")
        except providers.ProviderError as e:
            ok = False
            print(f"model call: FAILED: {e}")
            print(f"  -> check the key and that {s.model} is available on {s.provider}; `rooks login` reconfigures")
    print(f"trusted folder: {'yes' if (s and s.trusted()) else 'no'} ({root})")
    if s and s.mcp_servers:
        from .mcp import MCPManager
        m = MCPManager(s.mcp_servers, str(root), warn=lambda msg: print(msg))
        for n, srv in m.servers.items():
            print(f"mcp {n}: ok, {len(srv.tools)} tools")
        ok &= len(m.servers) == len([x for x in s.mcp_servers.values() if not x.get("disabled")])
        m.close()
    elif s is not None:
        print("mcp: no servers configured")
    return 0 if ok else 1


def _keyring_ok() -> bool:
    try:
        import keyring
        keyring.get_keyring()
        return True
    except Exception:
        return False
