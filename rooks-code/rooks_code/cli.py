"""rooks: ai-workspace's terminal coding agent. Any model: OpenAI, Anthropic, OpenRouter, any OpenAI-compatible server,
or local Ollama / LM Studio (works fully offline).

  rooks                         interactive session in the current folder
  rooks "fix the failing test"  one prompt, then exit (prints the final answer; exit 1 if it did not finish)
  rooks -c                      continue the last session in this folder (--resume ID for an older one)
  rooks login                   connect a model: paste a key, or point at your AI Workspace server
  rooks undo                    put back the files changed by the last prompt
  rooks receipts [list|show|import --db PATH]
  rooks -P claude-code "task"   hand the job to your own signed-in Claude Code (your Claude plan); also -P codex
  rooks doctor                  check the provider, key and MCP servers, and say what to fix
  rooks init                    write an example config file
  rooks mcp                     list MCP servers and their tools

Every edit (shown as a diff) and every command is asked about first, unless the folder is trusted, --yes is given, or
you answer "a" (always, for this session). --dry-run shows what would change and changes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from pathlib import Path

from . import __version__, config, receipts
from . import theme as term_theme
from .undo import ChangeSets

SUBCOMMANDS = ("undo", "receipts", "doctor", "init", "mcp", "version", "login")


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

    def can_stream(self) -> bool:
        return self.out.isatty() if hasattr(self.out, "isatty") else False

    def todos(self, items: list[dict]) -> None:
        """The agent's checklist, shown whenever it changes."""
        marks = {"done": self.c("ok", "✓"), "doing": self.c("accent", "▸"), "pending": self.c("dim", "○")}
        self.p("")
        for i in items:
            text = i["text"] if i["status"] != "done" else self.c("dim", i["text"])
            self.p(f"  {marks[i['status']]} {text}")

    def stream(self, delta: str) -> None:
        self.out.write(delta)
        self.out.flush()

    def stream_end(self) -> None:
        self.out.write("\n")
        self.out.flush()

    def assistant(self, text: str, final: bool) -> None:
        self.p(("" if final else self.c("2", "· ")) + text.strip())

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
        cost = "" if totals["cost_unknown"] or s.provider == "workbench" else f" · {cost}"
        self.p(self.c("2", f"  {short_model(s)} · {totals['tokens_in'] + totals['tokens_out']:,} tokens{cost}"
                           f"{' · cap ' + ' / '.join(cap) if cap else ''}{' · /undo available' if undo else ''}"))


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
    ap.add_argument("--max-steps", type=int, help="model calls per prompt before stopping (default 200)")
    ap.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    ap.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue the last session in this folder")
    ap.add_argument("--resume", metavar="ID", help="continue an earlier session (ids: rooks receipts list)")
    ap.add_argument("--no-stream", action="store_true", help="print each answer when it is complete")
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
    overrides = {"provider": args.provider, "model": args.model, "base_url": args.base_url, "yes": args.yes,
                 "dry_run": args.dry_run, "max_usd": args.max_usd, "max_tokens": args.max_tokens, "max_steps": args.max_steps}
    interactive = not args.prompt and sys.stdin.isatty() and sys.stdout.isatty()
    try:
        s = config.resolve(overrides, root)
    except SystemExit:
        if not interactive or not first_run_setup(root):
            raise
        s = config.resolve(overrides, root)
    if args.no_mcp:
        s.mcp_servers = {}
    if args.no_stream:
        s.stream = False
    ui_class = UI
    if interactive and not os.environ.get("ROOKS_PLAIN"):
        from . import tui
        if tui.available():
            ui_class = tui.RichUI
    ui = ui_class(color=False if args.no_color else None, theme=getattr(args, "theme", None))
    if s.kind == "delegate":
        from . import delegate
        return delegate.main(s.provider, s, ui, " ".join(args.prompt) if args.prompt else None)
    if s.key_env and not s.api_key and not s.key_optional:
        if not (interactive and first_run_setup(root, f"{s.provider} needs a key, and none is saved yet.")):
            ui.error(f"{s.provider} needs an API key in the {s.key_env} environment variable (or use --provider ollama)")
            return 2
        s = config.resolve(overrides, root)
    from .agent import Agent
    history: list[dict] = []
    if args.cont or args.resume:
        history, note = load_session(root, args.resume)
        if note:
            ui.error(note) if not history else ui.p(ui.c("2", note))
            if not history:
                return 1
    agent = Agent(s, ui, messages=history)
    try:
        if args.prompt:
            answer = agent.run(" ".join(args.prompt))
            outcome = agent.log.rows[min(r for r in agent.log.rows if agent.log.rows[r]["kind"] == "turn")]["outcome"]
            return 0 if outcome in ("no_claim", "backed") else 1
        return repl(agent, ui)
    finally:
        agent.close()


HELP = """/undo        put back the files changed by the last prompt
/cost        tokens and cost so far
/dry-run     toggle dry run          /yes   toggle auto-approve
/model       list the provider's models; /model N or /model NAME switches
/effort      how hard a plan's model thinks: /effort low, medium, high or auto
/provider P  switch provider (openai, anthropic, nvidia, workbench, ...), keeping the conversation
/compact     summarise the conversation so far to free the context window
/receipt     path of this session's receipt
/image P [Q] ask about a picture (or mention @shot.png in a prompt)
/clear       forget the conversation /exit  quit (or Ctrl-D)"""


def load_session(root: Path, session: str | None) -> tuple[list[dict], str]:
    """(messages, note) of a saved session in this folder: the newest, or the one named."""
    import json

    runs = root / ".rooks" / "runs"
    files = sorted(runs.glob("*.messages.json")) if runs.is_dir() else []
    if session:
        files = [f for f in files if f.name == f"{session}.messages.json"]
    if not files:
        return [], (f"no saved session {session!r} in this folder" if session else "no earlier session in this folder to continue")
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"cannot read {files[-1]}: {e}"
    msgs = data.get("messages") or []
    last = msgs[-1] if msgs else {}
    if last.get("role") == "assistant" and last.get("tool_calls"):  # saved mid tool call: close it so the model accepts it
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "interrupted"} for c in last["tool_calls"]]
    prompts = sum(m["role"] == "user" for m in msgs)
    return msgs, f"continuing session {files[-1].name.removesuffix('.messages.json')} ({prompts} earlier prompt(s), last on {data.get('provider')}/{data.get('model')})"


# Models a provider lists that can't hold a conversation: embeddings, rerankers, safety filters, speech, image and
# vision-only models, reward models. Hidden from /model (still usable by name; /model all lists everything).
NON_CHAT = re.compile(r"embed|rerank|guard|safety|content-safety|topic-control|jailbreak|deplot|kosmos|\btts\b|whisper|"
                      r"\basr\b|parakeet|canary|\bclip\b|nvclip|retriev|\bbge\b|\bocr\b|paddle|flux|diffusion|"
                      r"sdxl|dall-e|gpt-image|imagen|reward|moderation|fuyu|neva|\bvila\b|riva|transcri|audio|realtime|"
                      r"\bsearch-preview\b|davinci|babbage", re.I)


def short_model(s) -> str:
    """What the status bar and banner call the current model: 'Auto', 'opus', 'nemotron-3-ultra' (no 3/ or org/)."""
    m = s.model or ""
    if s.provider == "workbench" and m in ("", "default", "auto"):
        name = "Auto"
    elif m.startswith("auto:"):
        name = "Auto · " + m.split(":", 1)[1]
    else:
        name = m.split("/", 1)[1] if m[:1].isdigit() and "/" in m else m
        name = name.rsplit("/", 1)[-1]
    return name + (f" · {s.effort}" if getattr(s, "effort", "") else "")


def set_model(agent, model: str, effort: str | None = None) -> None:
    """Switch the model (and thinking level) for the rest of the session, keep the conversation, and remember it."""
    s = agent.s
    s.model = model
    if effort is not None:
        s.effort = effort if effort in config.EFFORTS else ""
    agent.switch(s)  # a fresh client carries the thinking level
    config.save_choice(s.provider, s.model, getattr(s, "effort", ""))


def app_models(agent) -> list[dict]:
    """The app's models with its picker hints, or [] when the provider isn't the app."""
    if agent.s.provider != "workbench" or not hasattr(agent.model, "list_models_detailed"):
        return []
    return agent.model.list_models_detailed()


def effort_command(agent, ui: UI, rest: str) -> None:
    """/effort low|medium|high|auto: how hard a plan's model (Claude, ChatGPT, Copilot) thinks."""
    want = rest.strip().lower()
    if not want:
        ui.p(f"thinking level is {getattr(agent.s, 'effort', '') or 'auto'}; /effort low, medium, high or auto")
        return
    if want not in (*config.EFFORTS, "auto", "default"):
        ui.error("use /effort low, medium, high or auto")
        return
    set_model(agent, agent.s.model, "" if want in ("auto", "default") else want)
    ui.p(f"thinking level is now {agent.s.effort or 'auto'}" + ("" if agent.s.provider == "workbench" else
         " (it applies to subscription plans through the AI Workspace app)"))


def model_command(agent, ui: UI, rest: str) -> None:
    """/model: the chat models you can use, numbered (through the app: grouped by AI, newest few each);
    /model N or /model NAME switches; /model all lists everything."""
    s = agent.s
    choice = rest.strip()
    show_all = choice.lower() == "all"
    if show_all:
        choice = ""
    listed: list[str] = getattr(agent, "_listed_models", [])
    if choice.isdigit() and listed and 1 <= int(choice) <= len(listed):
        choice = listed[int(choice) - 1]
    if choice:
        set_model(agent, choice)
        ui.p(f"model is now {short_model(s)}")
        return
    try:
        rich = app_models(agent)
    except Exception:  # noqa: BLE001 - fall back to the plain list
        rich = []
    if rich:
        listed, groups = [], {}
        for m in rich:
            if show_all or m.get("pick", True):
                groups.setdefault(m.get("owned_by") or "", []).append(m)
        for owner, items in groups.items():
            ui.p(ui.c("bold", owner or "Other"))
            for m in items:
                listed.append(m["id"])
                ui.p(("→ " if m["id"] == s.model else "  ") + f"{len(listed):>3}  {m.get('label') or m['id']}")
        agent._listed_models = listed
        more = len(rich) - len(listed)
        if more > 0:
            ui.p(ui.c("2", f"  ({more} older or specialist models hidden; /model all shows them)"))
        ui.p(ui.c("2", "/model N to switch · /effort low|medium|high for how hard a plan thinks"))
        return
    try:
        listed = agent.model.list_models() if hasattr(agent.model, "list_models") else []
    except Exception as e:  # noqa: BLE001 - shown, never fatal
        ui.warn(f"could not list models: {e}")
        return
    if not listed:
        ui.p(f"{s.provider} lists no models; type /model NAME")
        return
    hidden = 0
    if not show_all:
        chat = [m for m in listed if not NON_CHAT.search(m)]
        hidden, listed = len(listed) - len(chat), chat or listed
    # the app's smart router (auto, auto:fast, auto:strong) is the easy choice, so it comes first
    listed = sorted(listed, key=lambda m: not m.startswith("auto"))  # stable: the rest keep their order
    agent._listed_models = listed
    for i, m in enumerate(listed[:200], 1):
        ui.p(("→ " if m == s.model else "  ") + f"{i:>3}  {m}")
    if len(listed) > 200:
        ui.p(f"  ... {len(listed) - 200} more; type /model NAME")
    if hidden:
        ui.p(ui.c("2", f"  ({hidden} more that can't chat are hidden: embeddings, safety filters, speech, images. /model all shows them)"))
    ui.p(ui.c("2", "/model N or /model NAME to switch"))


def provider_command(agent, ui: UI, rest: str) -> None:
    name = rest.strip()
    if not name:
        ui.p(f"provider is {agent.s.provider}; built in: {', '.join(config.PROVIDERS)}")
        return
    try:
        s = config.resolve({"provider": name}, agent.root)
    except SystemExit as e:
        ui.error(str(e).removeprefix("rooks: "))
        return
    if s.kind == "delegate":
        ui.error(f"{name} hands whole jobs to another CLI; start it with rooks -P {name} instead")
        return
    if s.key_env and not s.api_key and not s.key_optional:
        ui.error(f"{name} needs a key: set {s.key_env} or run rooks login")
        return
    for k in ("max_usd", "max_tokens", "max_steps", "auto_approve", "dry_run", "stream"):
        setattr(s, k, getattr(agent.s, k))
    s.mcp_servers = agent.s.mcp_servers
    agent.switch(s)
    ui.p(f"now on {s.provider}/{s.model}")


def first_run_setup(root: Path, why: str = "No AI service is connected yet.") -> bool:
    """At a terminal with nothing connected: walk through `rooks login` now instead of stopping with an error."""
    import argparse

    from . import login

    print(f"\nWelcome to Rooks. {why} Let's connect one (about a minute).\n")
    try:
        ok = login.main(argparse.Namespace(provider=None, url=None, model=None, key_stdin=False), root) == 0
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    if ok:
        print()
    return ok


def repl(agent, ui: UI) -> int:
    if type(ui).__name__ == "RichUI":
        from . import tui
        return tui.run(agent, ui)
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
                model_command(agent, ui, rest)
                s = agent.s
            elif cmd == "/effort":
                effort_command(agent, ui, rest)
                s = agent.s
            elif cmd == "/provider":
                provider_command(agent, ui, rest)
                s = agent.s
            elif cmd == "/compact":
                ui.p("" if agent.compact("asked with /compact") else "nothing to compact yet")
            elif cmd == "/receipt":
                ui.p(str(agent.log.path))
            elif cmd == "/image":
                from .images import image_command

                prompt, err = image_command(rest, agent.root)
                if err:
                    ui.p(err)
                else:
                    agent.run(prompt)
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
    if name == "login":
        ap.add_argument("-P", "--provider", help="skip the menu: the provider to connect")
        ap.add_argument("--url", help="AI Workspace server URL (provider workbench)")
        ap.add_argument("-m", "--model")
        ap.add_argument("--key-stdin", action="store_true", help="read the key from standard input (scripts)")
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
    elif name == "login":
        from . import login
        return login.main(a, root)
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


def diagnose(problem: str) -> int:
    """No usable model: list what was looked for and found, then the one thing to do."""
    print(f"problem: {problem.removeprefix('rooks: ').splitlines()[0]}")
    print("looked for:")
    link = config.app_link()
    print(f"  AI Workspace desktop app: {'open at ' + link['url'] if link else 'not open'}")
    docker = config.docker_workbench()
    has_wb_key = bool(os.environ.get("WORKBENCH_API_KEY") or config.stored_keys().get("workbench"))
    print(f"  AI Workspace server (Docker): {('running at ' + docker) if docker else 'not answering on ' + config.DOCKER_URL}"
          + (f", key {'set' if has_wb_key else 'NOT set'}" if docker else ""))
    found = [n for n in config.KEY_ORDER if os.environ.get(config.PROVIDERS[n]["key_env"])]
    stored = sorted(config.stored_keys())
    print(f"  API keys in the environment: {', '.join(config.PROVIDERS[n]['key_env'] for n in found) or 'none'}")
    print(f"  keys saved by rooks login: {', '.join(stored) or 'none'} ({config.keys_path()})")
    print(f"  Ollama: {'running' if config._ollama_up() else 'not running'}")
    print("fix: " + ("run `rooks login`, choose the AI Workspace server and paste the token from Settings > Terminal access"
                     if docker and not has_wb_key else "run `rooks login` and paste a key (free: NVIDIA, Groq, Gemini)"))
    return 1


def doctor(root: Path, a) -> int:
    from . import providers
    ok = True
    print(f"rooks {__version__}, Python {sys.version.split()[0]}, {sys.platform}")
    print(f"config: {config.config_path()} ({'found' if config.config_path().exists() else 'not found, using defaults'})")
    try:
        s = config.resolve({"provider": a.provider, "model": a.model, "base_url": a.base_url}, root)
    except SystemExit as e:
        return diagnose(str(e))
    if s.kind == "delegate":
        from . import delegate
        return delegate.doctor(s.provider)
    print(f"provider: {s.provider} ({s.kind}) at {s.base_url}, model {s.model}")
    if s.key_env:
        print(f"api key: {s.key_env} is {'set' if s.api_key else 'NOT set'}")
        ok &= bool(s.api_key) or s.key_optional
    try:
        model = providers.make(s)
        model.timeout = 60  # a check, not a task: a provider that hangs is reported instead of waited on
        r = model.complete("Reply with the single word: ready", [{"role": "user", "content": "ping"}], [])
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
