"""The interactive session at a real terminal: a welcome banner, a command menu that opens as you type /, @ to point at
files, history that survives restarts, a status bar, answers rendered as formatted text, and plain-language prompts.

Used only when stdin and stdout are a terminal and prompt_toolkit and rich are installed; pipes, scripts and tests keep
the plain line mode in cli.repl. Nothing here changes what the agent may do: every edit and command is still asked
about first (agent.py), and Ctrl+C stops the running task without leaving the session.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__, config
from .learning import local_path as learning_path
from .cli import UI, HELP, app_models, effort_command, load_session, model_command, provider_command, set_model, short_model, undo_text

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # the plain line mode still works; available() is False, so nothing below is used
    PromptSession = None  # type: ignore[assignment,misc]
    Completer = object  # type: ignore[assignment,misc]

LOGO = """\
██████   ██████   ██████  ██   ██ ███████
██   ██ ██    ██ ██    ██ ██  ██  ██
██████  ██    ██ ██    ██ █████   ███████
██   ██ ██    ██ ██    ██ ██  ██       ██
██   ██  ██████   ██████  ██   ██ ███████"""

# command -> (what it does, in plain words). Order is the order shown in the menu and in /help.
COMMANDS: dict[str, str] = {
    "/help": "what you can do here",
    "/new": "start a fresh conversation",
    "/model": "pick the AI, the model and how hard it thinks (arrow keys)",
    "/effort": "how hard a plan's model thinks: /effort low, medium, high or auto",
    "/provider": "switch the AI service: /provider NAME",
    "/login": "connect an AI service (paste a key, or use your AI Workspace app)",
    "/memory": "what Rooks remembers about you (shared with the AI Workspace app)",
    "/skills": "procedures Rooks learned; /skills add GITHUB-LINK checks a new one",
    "/undo": "put back the files changed by the last request",
    "/sessions": "earlier conversations in this folder",
    "/resume": "continue an earlier conversation: /resume ID",
    "/copy": "copy the last answer to the clipboard",
    "/compact": "summarise the conversation so far to make room",
    "/cost": "tokens used and cost so far",
    "/auto": "turn asking before changes off or on",
    "/dry-run": "show what would change without changing anything",
    "/doctor": "check that everything works and say what to fix",
    "/receipt": "where this session's record is saved",
    "/exit": "quit (or Ctrl+D)",
}
ALIASES = {"/clear": "/new", "/yes": "/auto", "/quit": "/exit", "/?": "/help"}


def available() -> bool:
    return PromptSession is not None and sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM") != "dumb"


def history_path() -> Path:
    return config.config_path().parent / "rooks-history"


class RichUI(UI):
    """UI that renders answers as formatted text and shows a live 'Working' line with the elapsed time."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.console = Console(highlight=False, soft_wrap=False, no_color=not self.color)
        self._started = 0.0

    def code_theme(self) -> str:
        return "ansi_light" if self.theme.background == "light" else "ansi_dark"

    def hexcolour(self, role: str) -> str:
        return self.theme.palette.get(role, "#999999")

    def assistant(self, text: str, final: bool) -> None:
        text = text.strip()
        if not text:
            return
        if not final:
            self.p(self.c("dim", "· " + text))
            return
        self.console.print()
        self.console.print(Padding(Markdown(text, code_theme=self.code_theme()), (0, 0, 0, 2)))

    def stream_end(self) -> None:
        self.out.write("\n")
        self.out.flush()

    def thinking(self, on: bool) -> None:
        if not self.color:
            return
        if on:
            self._started = self._started or time.monotonic()
            self._spin = threading.Event()
            stop, started = self._spin, self._started

            def spin():
                frames, i = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", 0
                while not stop.wait(0.1):
                    secs = int(time.monotonic() - started)
                    line = f"\r{self.c('accent', frames[i % 10])} Working {secs}s {self.c('dim', '· Ctrl+C to stop')}"
                    self.out.write(line); self.out.flush(); i += 1
                self.out.write("\r\033[2K"); self.out.flush()
            threading.Thread(target=spin, daemon=True).start()
        elif self._spin:
            self._spin.set(); time.sleep(0.12); self._spin = None

    def ask(self, plan) -> str:
        label = {"write": "Make this change", "command": "Run this command", "external": "Use this tool"}[plan.effect]
        self.p("")
        self.p("  " + self.c("bold", f"{label}?") + "  " + self.c("ok", "y") + " yes   " + self.c("error", "n") + " no   "
               + self.c("accent", "a") + " yes, and don't ask again this session")
        while True:
            try:
                a = input("  › ").strip().lower()[:1]
            except EOFError:
                return "n"
            except KeyboardInterrupt:
                self.p("")
                return "n"
            if a in ("y", "n", "a"):
                return a
            self.p(self.c("dim", "  Type y, n or a, then Enter."))

    def footer(self, totals: dict, s, undo: bool) -> None:
        self._started = 0.0
        tokens = totals["tokens_in"] + totals["tokens_out"]
        cost = "" if totals["cost_unknown"] else f" · ${totals['usd']:.4f}"
        self.p(self.c("dim", f"  {tokens:,} tokens{cost}{' · /undo puts the files back' if undo else ''}"))


class RooksCompleter(Completer):
    """Menu for / commands (with what each does), model and provider names after /model and /provider, @ for files."""

    def __init__(self, agent):
        self.agent = agent

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/") and " " not in text:
            for cmd, what in COMMANDS.items():
                if cmd.startswith(text.lower()):
                    yield Completion(cmd, start_position=-len(text), display_meta=what)
            return
        if text.startswith("/model "):
            part = text[len("/model "):]
            for m in getattr(self.agent, "_listed_models", [])[:300]:
                if part.lower() in m.lower():
                    yield Completion(m, start_position=-len(part))
            return
        if text.startswith("/provider "):
            part = text[len("/provider "):]
            for name, p in config.PROVIDERS.items():
                if name.startswith(part) and p["kind"] != "delegate":
                    yield Completion(name, start_position=-len(part))
            return
        if text.startswith("/resume "):
            part = text[len("/resume "):]
            for sid, _ in sessions(self.agent.root)[-30:]:
                if sid.startswith(part):
                    yield Completion(sid, start_position=-len(part))
            return
        word = text.split(" ")[-1]
        if word.startswith("@"):
            yield from self._files(word[1:])

    def _files(self, part: str):
        root = Path(self.agent.root)
        base, _, prefix = part.rpartition("/")
        folder = (root / base) if base else root
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        shown = 0
        for e in entries:
            if e.name.startswith(".") and not prefix.startswith("."):
                continue
            if e.name in ("node_modules", "__pycache__", ".git") or not e.name.lower().startswith(prefix.lower()):
                continue
            rel = f"{base}/{e.name}" if base else e.name
            yield Completion("@" + rel + ("/" if e.is_dir() else ""), start_position=-(len(part) + 1),
                             display_meta="folder" if e.is_dir() else "file")
            shown += 1
            if shown >= 60:
                return


def sessions(root) -> list[tuple[str, str]]:
    """(id, first request) of the saved conversations in this folder, oldest first."""
    import json

    runs = Path(root) / ".rooks" / "runs"
    out = []
    for f in sorted(runs.glob("*.messages.json")) if runs.is_dir() else []:
        try:
            msgs = json.loads(f.read_text(encoding="utf-8")).get("messages") or []
        except (OSError, ValueError):
            continue
        first = next((m.get("content") for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), str)), "")
        out.append((f.name.removesuffix(".messages.json"), " ".join((first or "").split())[:70]))
    return out


def model_label(s, dim: str) -> str:
    """'Auto, through your AI Workspace app' rather than 'default (workbench)'."""
    if s.provider == "workbench":
        return f"{short_model(s)}  [{dim}](through your AI Workspace app: same models, keys and plans · /model to change)[/]"
    return f"{short_model(s)}  [{dim}]({s.provider})[/]"


def _choose(message: str, options: list[tuple[str, str]], default: str | None = None) -> str | None:
    """An arrow-key menu (Enter picks, Esc or Ctrl+C cancels). None when cancelled or not available."""
    try:
        from prompt_toolkit.key_binding import KeyBindings as Keys
        from prompt_toolkit.shortcuts import choice
    except ImportError:
        return None
    keys = Keys()

    @keys.add("escape", eager=True)
    def _cancel(event):  # Esc backs out, as the hint says (choice() itself only knows Ctrl+C)
        event.app.exit(exception=KeyboardInterrupt())

    try:
        return choice(message=message, options=options, default=default if default in {v for v, _ in options} else None,
                      key_bindings=keys, bottom_toolbar=" ↑↓ move · Enter picks · Esc cancels ")
    except (KeyboardInterrupt, EOFError):
        return None


EFFORT_OPTIONS = [("", "Auto (the plan decides)"), ("low", "Low: quick answers"), ("medium", "Medium"),
                  ("high", "High: thinks longest, best for hard problems")]


def pick_model(agent, ui) -> bool:
    """/model with no name: pick the AI, then its model, then (for a plan) how hard it thinks. Short lists only.
    False when there is nothing to pick from here (another provider), so the plain list is shown instead."""
    try:
        rows = app_models(agent)
    except Exception:  # noqa: BLE001 - the plain list explains the error
        return False
    if not rows:
        return False
    groups: dict[str, list[dict]] = {}
    for m in rows:
        groups.setdefault(m.get("owned_by") or "Other", []).append(m)
    auto = groups.pop("Smart router", [])
    s = agent.s
    current_owner = next((m.get("owned_by") for m in rows if m["id"] == s.model), "")
    ais = ([("__auto__", "Auto: picks the right model for each job (recommended)")] if auto else []) + [
        (o, f"{o}  ({sum(1 for m in ms if m.get('pick', True))} models)") for o, ms in groups.items()]
    owner = _choose("Which AI?", ais, current_owner or "__auto__")
    if owner is None:
        ui.p(ui.c("dim", "  No change."))
        return True
    if owner == "__auto__":
        set_model(agent, "auto", "")
        ui.p(ui.c("ok", "  Now on Auto: each job goes to a fitting model."))
        return True
    items = groups[owner]
    picks = [m for m in items if m.get("pick", True)] or items
    options = [(m["id"], m.get("label") or m["id"]) for m in picks]
    if len(picks) < len(items):
        options.append(("__more__", f"More models ({len(items) - len(picks)} older or specialist)"))
    model = _choose(f"Which {owner} model?", options, s.model)
    if model == "__more__":
        model = _choose(f"All {owner} models", [(m["id"], m.get("label") or m["id"]) for m in items], s.model)
    if model is None:
        ui.p(ui.c("dim", "  No change."))
        return True
    effort = ""
    if any(m["id"] == model and m.get("effort") for m in items):
        picked = _choose("How hard should it think?", EFFORT_OPTIONS, getattr(s, "effort", ""))
        effort = picked or ""
    set_model(agent, model, effort)
    ui.p(ui.c("ok", f"  Now using {owner}: {short_model(agent.s)}"))
    return True


def banner(agent, ui: RichUI) -> None:
    s = agent.s
    accent, dim = ui.hexcolour("accent"), ui.hexcolour("dim")
    width = shutil.get_terminal_size((100, 30)).columns
    if width >= 60:
        ui.console.print(Text(LOGO, style=f"bold {accent}"))
    info = Table.grid(padding=(0, 2))
    info.add_column(style=dim, no_wrap=True)
    info.add_column()
    info.add_row("Model", model_label(s, dim))
    info.add_row("Folder", str(agent.root))
    mode = "asks before every change" if not (s.auto_approve or s.trusted()) else "changes files without asking"
    if s.dry_run:
        mode = "dry run: shows changes, makes none"
    info.add_row("Safety", mode)
    info.add_row("Tools", f"{len(agent.tool_specs())} (files, terminal, web{', your computer' if any('desk' in n for n in (s.mcp_servers or {})) else ''})")
    tips = Text.from_markup(
        f"Just type what you want, in plain words. For example: [italic]make a budget table from bank.csv[/]\n"
        f"[{accent}]/[/] shows every command   [{accent}]@[/] picks a file   "
        f"[{accent}]Esc Enter[/] new line   [{accent}]Ctrl+C[/] stops a task   [{accent}]Ctrl+D[/] quits")
    body = Table.grid()
    body.add_row(info)
    body.add_row("")
    body.add_row(tips)
    ui.console.print(Panel(body, title=f"[bold]Rooks[/] [{dim}]{__version__}[/]", title_align="left",
                           border_style=accent, padding=(1, 2), expand=False))


def help_table(ui: RichUI) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style=f"bold {ui.hexcolour('accent')}", no_wrap=True)
    t.add_column()
    for cmd, what in COMMANDS.items():
        t.add_row(cmd, what)
    ui.console.print(Padding(t, (1, 2)))
    ui.console.print(Padding(Text("Anything that is not a command is a request. Mention a file with @, for example: "
                                  "summarise @notes.md", style=ui.hexcolour("dim")), (0, 2, 1, 2)))


def copy_text(text: str) -> bool:
    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["clip"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def last_answer(agent) -> str:
    for m in reversed(agent.messages):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"]
    return ""


def relogin(agent, ui: RichUI) -> None:
    import argparse

    from . import login

    if login.main(argparse.Namespace(provider=None, url=None, model=None, key_stdin=False), agent.root) != 0:
        return
    try:
        fresh = config.resolve({}, agent.root)
    except SystemExit as e:
        ui.error(str(e).removeprefix("rooks: "))
        return
    provider_command(agent, ui, fresh.provider)


def run(agent, ui: RichUI) -> int:
    """The session loop. Returns the exit code."""
    agent.s.stream = False  # answers are shown formatted once complete; the Working line shows progress meanwhile
    banner(agent, ui)
    keys = KeyBindings()

    @keys.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    def toolbar():
        s = agent.s
        t = agent.log.totals()
        mode = "DRY RUN" if s.dry_run else ("auto" if (s.auto_approve or s.trusted()) else "asks first")
        shown = short_model(s)
        return FormattedText([("class:bar", f" {shown} "), ("class:barsep", "│"),
                              ("class:bar", f" {Path(agent.root).name or agent.root} "), ("class:barsep", "│"),
                              ("class:bar", f" {t['tokens_in'] + t['tokens_out']:,} tokens "), ("class:barsep", "│"),
                              ("class:bar", f" {mode} "), ("class:barsep", "│"),
                              ("class:bardim", " / commands  @ files  Ctrl+D quit ")])

    accent = ui.hexcolour("accent") if ui.color else ""
    style = Style.from_dict({
        "prompt": f"bold {accent}" if accent else "bold",
        "bottom-toolbar": "noreverse",
        "bar": f"bg:#2b2b2b #e6e6e6" if ui.theme.background != "light" else "bg:#ececec #222222",
        "barsep": "bg:#2b2b2b #666666" if ui.theme.background != "light" else "bg:#ececec #999999",
        "bardim": "bg:#2b2b2b #9a9a9a" if ui.theme.background != "light" else "bg:#ececec #666666",
        "completion-menu.completion": "bg:#333333 #e6e6e6",
        "completion-menu.completion.current": f"bg:{accent or '#555555'} #000000",
        "completion-menu.meta.completion": "bg:#262626 #a0a0a0",
        "completion-menu.meta.completion.current": "bg:#3a3a3a #ffffff",
    })
    history_path().parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(history=FileHistory(str(history_path())), completer=RooksCompleter(agent),
                            complete_while_typing=True, key_bindings=keys, bottom_toolbar=toolbar, style=style,
                            complete_style="column")
    warned = False
    while True:
        try:
            line = session.prompt(FormattedText([("class:prompt", "› ")]))
        except KeyboardInterrupt:
            if not warned:
                ui.p(ui.c("dim", "  (Ctrl+D or /exit to quit)"))
                warned = True
            continue
        except EOFError:
            ui.p(ui.c("dim", "Bye."))
            return 0
        line = line.strip()
        if not line:
            continue
        if not line.startswith("/") and ("/" + line.lower() in COMMANDS or line.lower() in ("exit", "quit", "bye")):
            # "model" or "exit" typed without the slash: do what was meant, not send it to the model as a request
            line = "/exit" if line.lower() in ("exit", "quit", "bye") else "/" + line.lower()
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            cmd = ALIASES.get(cmd.lower(), cmd.lower())
            if cmd == "/exit":
                ui.p(ui.c("dim", "Bye."))
                return 0
            command(agent, ui, cmd, rest.strip())
            continue
        agent.run(line)


def add_skill(agent, ui, url: str) -> None:
    """/skills add LINK: the app checks it (scans and a sandboxed trial); the user approves it on the Skills page."""
    learning = getattr(agent, "learning", None)
    if not url:
        ui.p("  /skills add https://github.com/OWNER/REPO (or a link to a skill folder in it)")
        return
    if not learning or not learning.app:
        ui.error("Skills from GitHub are checked and installed through the AI Workspace app. Open the app, then try again.")
        return
    ui.p(ui.c("dim", "  Checking it for safety (pinned version, code scan, prompt-injection scan, sandboxed trial)…"))
    r = learning.check_skill(url)
    for st in r.get("stages") or []:
        mark = {"done": "ok", "warn": "warn", "failed": "error", "blocked": "error"}.get(st["status"], "dim")
        ui.p("  " + ui.c(mark, f"{st['stage']}: {st['detail']}"))
    if r.get("choices"):
        ui.p("  That repository has several skills. Run /skills add with one of these links:")
        for c in r["choices"][:20]:
            ui.p(f"    {c['name']}  {c['url']}")
        return
    if not r.get("ok"):
        ui.error(f"Not added: {r.get('error') or 'the check did not finish'}")
        return
    sk = r.get("skill") or {}
    ui.p(ui.c("ok", f"  {sk.get('name', 'The skill')} passed the checks and is waiting for your OK."))
    ui.p(f"  Approve it on the Skills page: {learning.app_page('/skills')}")


def command(agent, ui: RichUI, cmd: str, rest: str) -> None:
    s = agent.s
    if cmd == "/help":
        help_table(ui)
    elif cmd == "/new":
        agent.messages.clear()
        ui.p(ui.c("ok", "  Fresh start: the conversation is cleared (files are not touched)."))
    elif cmd == "/model":
        if rest or not pick_model(agent, ui):
            model_command(agent, ui, rest)
    elif cmd == "/effort":
        effort_command(agent, ui, rest)
    elif cmd == "/provider":
        provider_command(agent, ui, rest)
    elif cmd == "/login":
        relogin(agent, ui)
    elif cmd == "/memory":
        learning = getattr(agent, "learning", None)
        facts = learning.facts() if learning else []
        if not learning:
            ui.p("  Learning is off (learn = false in rooks.toml).")
        elif not facts:
            ui.p("  Nothing remembered yet. Tell Rooks something lasting, like \"I use pnpm\", and it saves it.")
        for f in facts[:40]:
            ui.p(f"  • {f}")
        if facts:
            ui.p(ui.c("dim", "  Shared with the AI Workspace app (About you page)." if learning.app else
                      f"  Kept in {learning_path()}. Connect the app to share it with chat."))
    elif cmd == "/skills" and rest.split(" ", 1)[0].lower() in ("add", "install"):
        add_skill(agent, ui, rest.split(" ", 1)[1].strip() if " " in rest else "")
    elif cmd == "/skills":
        learning = getattr(agent, "learning", None)
        found = learning.skill_list() if learning else []
        if not found:
            ui.p("  No saved skills yet. After a task of several steps, the app saves the procedure so next time is faster."
                 if learning and learning.app else "  Skills are learned through the AI Workspace app; it isn't connected.")
        for sk in found:
            ui.p(f"  {ui.c('accent', sk['name'])}  {sk.get('description', '')}")
    elif cmd == "/undo":
        ui.p(undo_text(agent.changes))
    elif cmd == "/sessions":
        found = sessions(agent.root)
        if not found:
            ui.p("  No earlier conversations in this folder yet.")
        for sid, first in found[-15:]:
            ui.p(f"  {ui.c('accent', sid)}  {first}")
        if found:
            ui.p(ui.c("dim", "  /resume ID continues one (start typing the id for the list)."))
    elif cmd == "/resume":
        history, note = load_session(agent.root, rest or None)
        if history:
            agent.messages[:] = history
            ui.p(ui.c("ok", "  " + note))
        else:
            ui.error(note)
    elif cmd == "/copy":
        text = last_answer(agent)
        if not text:
            ui.p("  Nothing to copy yet.")
        else:
            ui.p(ui.c("ok", "  Copied the last answer.") if copy_text(text) else ui.c("warn", "  No clipboard tool found."))
    elif cmd == "/compact":
        ui.p("" if agent.compact("asked with /compact") else "  Nothing to compact yet.")
    elif cmd == "/cost":
        t = agent.log.totals()
        ui.p(f"  {t['tokens_in']:,} in + {t['tokens_out']:,} out tokens, "
             f"{'cost unknown' if t['cost_unknown'] else '$%.4f' % t['usd']}, {t['model_calls']} model calls")
    elif cmd == "/auto":
        s.auto_approve = not s.auto_approve
        ui.p(ui.c("warn", "  Changes now happen without asking. /auto again to turn asking back on.") if s.auto_approve
             else ui.c("ok", "  Rooks asks before every change again."))
    elif cmd == "/dry-run":
        s.dry_run = not s.dry_run
        ui.p(f"  Dry run {'on: nothing will be changed' if s.dry_run else 'off'}.")
    elif cmd == "/doctor":
        import argparse

        from .cli import doctor
        doctor(Path(agent.root), argparse.Namespace(provider=s.provider, model=s.model, base_url=None))
    elif cmd == "/receipt":
        ui.p(f"  {agent.log.path}")
    else:
        import difflib

        close = difflib.get_close_matches(cmd, list(COMMANDS) + list(ALIASES), n=1, cutoff=0.6)
        close = [ALIASES.get(c, c) for c in close]
        ui.p(f"  Unknown command {cmd}." + (f" Did you mean {close[0]}?" if close else "") + " /help lists them all.")


__all__ = ["available", "RichUI", "run", "COMMANDS", "HELP"]
