"""The interactive session at a real terminal: a welcome banner, a command menu that opens as you type /, @ to point at
files, history that survives restarts, a status bar, answers rendered as formatted text, and plain-language prompts.

Used only when stdin and stdout are a terminal and prompt_toolkit and rich are installed; pipes, scripts and tests keep
the plain line mode in cli.repl. Nothing here changes what the agent may do: every edit and command is still asked
about first (agent.py), and Ctrl+C stops the running task without leaving the session.
"""

from __future__ import annotations

import os
import re
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
    from rich.live import Live
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
    "/diff": "what changed in this folder (git), as a diff",
    "/jobs": "background jobs (servers, watchers): /jobs, /jobs stop ID",
    "/init": "write AGENTS.md: the project's rules and commands, so every session starts informed",
    "/context": "how full the conversation is, and which project instruction files are followed",
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
        from rich.theme import Theme

        accent = self.hexcolour("accent") if self.color else ""
        self.console = Console(highlight=False, soft_wrap=False, no_color=not self.color,
                               theme=Theme({"markdown.code": f"bold {accent}" if accent else "bold",  # inline `code`: coloured, no block
                                            "markdown.h1": "bold", "markdown.h2": "bold underline", "markdown.link": accent or "underline",
                                            "markdown.block_quote": "italic #9a9a9a"}))
        self._started = 0.0
        self.activity = "Thinking"  # what the agent is doing now, shown on the Working line

    def code_theme(self) -> str:
        return "ansi_light" if self.theme.background == "light" else "ansi_dark"

    def hexcolour(self, role: str) -> str:
        return self.theme.palette.get(role, "#999999")

    def assistant(self, text: str, final: bool) -> None:
        text = text.strip()
        if not text:
            return
        self.console.print()
        self.bullet(Markdown(_hide_markup(text), code_theme=self.code_theme()), "ok" if final else "text")

    def bullet(self, renderable, role: str = "ok") -> None:
        """'⏺ ' before the first line, the rest indented under it (how Claude Code and Hermes lay out answers)."""
        self.console.print(self._dotted(renderable, role if role != "text" else "dim"))

    def can_stream(self) -> bool:
        return bool(self.color)

    def stream(self, delta: str) -> None:
        """Live, formatted answer: the Markdown is re-rendered as text arrives (like the app's chat)."""
        if getattr(self, "_live", None) is None:
            self._buf = ""
            self.console.print()
            self._live = Live(console=self.console, refresh_per_second=12, vertical_overflow="visible", transient=False)
            self._live.start()
        self._buf += delta
        self._live.update(self._dotted(Markdown(_hide_markup(self._buf), code_theme=self.code_theme())))

    def _dotted(self, renderable, role: str = "ok"):
        g = Table.grid()
        g.add_column(width=2, no_wrap=True)
        g.add_column()
        g.add_row(Text("⏺", style=self.hexcolour(role)) if self.color else Text("*"), renderable)
        return g

    def stream_end(self) -> None:
        live = getattr(self, "_live", None)
        if live is not None:
            live.stop()
            self._live = None

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
                    what = self.activity[:70]
                    agent = getattr(self, "agent", None)
                    tok = ""
                    if agent is not None:
                        t = agent.log.totals()
                        tok = f" · ↓ {_k(t['tokens_in'] + t['tokens_out'])} tokens"
                    star = "✻✳✶✷✸✹"[(i // 3) % 6]
                    line = (f"\r\033[2K{self.c('accent', star)} {self.c('accent', what + '…')} "
                            f"{self.c('dim', f'({secs}s{tok} · Ctrl+C to stop)')}")
                    self.out.write(line); self.out.flush(); i += 1
                self.out.write("\r\033[2K"); self.out.flush()
            threading.Thread(target=spin, daemon=True).start()
        elif self._spin:
            self._spin.set(); time.sleep(0.12); self._spin = None

    QUIET = ("read-only", "auto-approved (--yes)", "trusted folder", "approved for this session", "dry run: not applied")

    def _head(self, verb: str, arg: str, role: str = "accent") -> None:
        t = Text()
        t.append("⏺ ", style=self.hexcolour(role))
        t.append(verb, style="bold")
        if arg:
            t.append(f"({arg})" if not arg.startswith(" ") else arg, style=self.hexcolour("dim") if verb in ("Thinking",) else "")
        self.console.print(t, overflow="ellipsis", no_wrap=True)

    def _sub(self, text: str, role: str = "dim") -> None:
        self.console.print(Text("  ⎿  ", style=self.hexcolour("dim")) + Text(text, style=self.hexcolour(role)),
                           overflow="ellipsis", no_wrap=True)

    def tool_note(self, text: str, read: bool = False) -> None:
        if text in self.QUIET:
            return
        if text.strip().startswith("↳"):  # a sub-agent's step
            self.console.print(Text(f"     {text.strip()}", style=self.hexcolour("dim")), overflow="ellipsis", no_wrap=True)
            return
        if not read:
            self._sub(text, "error" if text.startswith("failed") else "dim")
            return
        self.activity = text[:1].upper() + text[1:]
        if text.startswith("checklist"):
            return  # the checklist panel shows it
        verb, _, arg = text.partition(" ")
        names = {"read": "Read", "list": "List", "search": "Search", "remember:": "Remember", "load": "Skill",
                 "sub-agent:": "Task", "check": "Check", "search the web:": "Web search"}
        if text.startswith("search the web:"):
            verb, arg = "Web search", text.split(":", 1)[1].strip()
        elif verb == "read" and arg.startswith("http"):
            verb = "Fetch"
        else:
            verb = names.get(verb, verb.capitalize())
        self._head(verb, arg.strip())

    def show_plan(self, plan) -> None:
        self.activity = plan.summary[:1].upper() + plan.summary[1:]
        s = plan.summary
        if plan.effect == "command":
            cmd = s.split(": ", 1)[1] if ": " in s else s
            self._head("Bash" if not s.startswith(("start", "stop")) else ("Background" if s.startswith("start") else "Stop"), cmd)
        elif plan.effect == "write":
            verb, _, path = s.partition(" ")
            self._head({"edit": "Update", "create": "Write", "overwrite": "Write"}.get(verb, verb.capitalize()), path)
            if plan.preview:
                from rich.syntax import Syntax

                lines = plan.preview.rstrip("\n").splitlines()
                body = "\n".join(l for l in lines[:120] if not l.startswith(("---", "+++")))
                self.console.print(Padding(Panel(Syntax(body, "diff", theme=self.code_theme(), background_color="default", word_wrap=True),
                                                 border_style=self.hexcolour("dim"), padding=(0, 1), expand=True), (0, 0, 0, 2)))
                if len(lines) > 120:
                    self._sub(f"… {len(lines) - 120} more lines")
        else:
            self._head("Tool", s)

    def tool_result(self, plan, result: str, outcome: str) -> None:
        self.activity = "Thinking"
        lines = (result or "").splitlines()
        if outcome in ("tool_error", "check_failed"):
            self._sub(("check failed: " if outcome == "check_failed" else "failed: ") + (lines[0] if lines else ""), "error")
            for l in lines[1:8]:
                self.console.print(Text(f"     {l}", style=self.hexcolour("error")), overflow="ellipsis", no_wrap=True)
            return
        if plan.effect == "read":
            if plan.summary.startswith(("checklist", "remember", "sub-agent")):
                if plan.summary.startswith("sub-agent"):
                    self._sub(f"done · {len(lines)} lines of findings")
                return
            what = "lines" if plan.summary.startswith("read") and not plan.summary.startswith("read http") else "results"
            n = len([l for l in lines if l.strip()]) if what == "results" else len(lines)
            self._sub(f"{n} {what}" if n else "nothing found")
            return
        if plan.effect == "command":
            body = [l for l in lines if l.strip()]
            code = body[0] if body and body[0].startswith("exit code") else ""
            out = body[1:] if code else body
            for l in out[:10]:
                self.console.print(Text("  ⎿  " if l is out[0] else "     ", style=self.hexcolour("dim")) + Text(l), overflow="ellipsis", no_wrap=True)
            if len(out) > 10:
                self._sub(f"… +{len(out) - 10} lines")
            if code and code != "exit code 0":
                self._sub(code, "error")
            elif not out:
                self._sub(code or "done")
            return
        self._sub(("✓ " + (lines[0] if lines else "done")) if outcome in ("verified", "ok") else outcome, "ok")

    def todos(self, items: list[dict]) -> None:
        t = Table.grid(padding=(0, 1))
        t.add_column(no_wrap=True)
        t.add_column()
        marks = {"done": ("✓", "ok"), "doing": ("▸", "accent"), "pending": ("○", "dim")}
        for i in items:
            mark, role = marks[i["status"]]
            t.add_row(Text(mark, style=self.hexcolour(role)), Text(i["text"], style=self.hexcolour("dim") if i["status"] == "done" else ""))
        done = sum(i["status"] == "done" for i in items)
        self.console.print(Padding(Panel(t, title=f"Plan · {done}/{len(items)}", title_align="left", border_style=self.hexcolour("dim"),
                                         expand=False, padding=(0, 1)), (0, 0, 0, 2)))

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


_MARKUP_RE = re.compile(r"<(?:antml:)?function_calls>.*?(?:</(?:antml:)?function_calls>|\Z)|<tool_call>.*?(?:</tool_call>|\Z)", re.S)


def _hide_markup(text: str) -> str:
    """Tool-call markup a model printed as text never reaches the screen (the call itself is shown as a tool line)."""
    return _MARKUP_RE.sub("", text).rstrip()


def banner(agent, ui: RichUI) -> None:
    """A compact welcome, like Claude Code's: who, where, what it follows, and the few keys that matter."""
    s = agent.s
    accent, dim = ui.hexcolour("accent"), ui.hexcolour("dim")
    branch = git_branch(agent.root)
    home = str(Path.home())
    folder = str(agent.root).replace(home, "~", 1) if str(agent.root).startswith(home) else str(agent.root)
    files = getattr(agent, "instruction_files", [])
    head = Text.from_markup(f"[bold {accent}]♜ Rooks[/] [{dim}]{__version__}[/]   [bold]{short_model(s)}[/]"
                            + (f" [{dim}]· through your AI Workspace app[/]" if s.provider == "workbench" else f" [{dim}]· {s.provider}[/]"))
    where = Text.from_markup(f"[{dim}]{folder}[/]" + (f"  [{accent}]⎇ {branch}[/]" if branch else "")
                             + (f"  [{dim}]· follows {', '.join(Path(f).name for f in files)}[/]" if files else f"  [{dim}]· /init writes AGENTS.md[/]"))
    tools = len(agent.tool_specs())
    what = Text.from_markup(f"[{dim}]{tools} tools: files, terminal, web, background jobs, sub-agents"
                            + (", your computer" if any("desk" in n for n in (s.mcp_servers or {})) else "")
                            + " · remembers what you teach it[/]")
    body = Table.grid()
    for row in (head, where, what):
        body.add_row(row)
    ui.console.print(Panel(body, border_style=accent, padding=(0, 1), expand=False))
    ui.console.print(Text.from_markup(f"  [{dim}]Type a request in plain words.  [/][{accent}]/[/][{dim}] commands  [/][{accent}]@[/][{dim}] add a file  "
                                      f"[/][{accent}]⇧Tab[/][{dim}] mode  [/][{accent}]?[/][{dim}] shortcuts[/]"))
    recent = sessions(agent.root)[-3:]
    if recent:
        ui.console.print(Text.from_markup(f"  [{dim}]Recent: " + " · ".join(f"{t[:38]}" for _, t in reversed(recent)) + "   (/resume)[/]"),
                         overflow="ellipsis", no_wrap=True)
    ui.console.print()


SHORTCUTS = [("/", "commands, with what each does"), ("@", "add a file or folder to your request"),
             ("⇧Tab", "switch mode: asks first → auto-accept → plan only"), ("Esc Enter", "new line (paste keeps lines too)"),
             ("↑ ↓", "earlier requests"), ("Ctrl+R", "search earlier requests"), ("Ctrl+C", "stop the current task"),
             ("Ctrl+D", "quit"), ("model", "same as /model (plain words work for every command)")]

MODES = ["asks first", "auto-accept", "plan only"]


def mode_of(s) -> str:
    return "plan only" if s.dry_run else ("auto-accept" if (s.auto_approve or s.trusted()) else "asks first")


def set_mode(s, mode: str) -> None:
    s.dry_run = mode == "plan only"
    s.auto_approve = mode == "auto-accept"


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


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
    # answers stream in as they are written, formatted as they grow (RichUI.stream)
    banner(agent, ui)
    keys = KeyBindings()

    @keys.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    @keys.add("s-tab")
    def _mode(event):  # Shift+Tab: asks first -> auto-accept -> plan only, like Claude Code
        m = MODES[(MODES.index(mode_of(agent.s)) + 1) % len(MODES)]
        set_mode(agent.s, m)
        event.app.invalidate()

    def rule():
        return "─" * max(20, shutil.get_terminal_size((100, 30)).columns)

    def message():
        return FormattedText([("class:rule", rule() + "\n"), ("class:prompt", "❯ ")])

    def toolbar():
        s = agent.s
        t = agent.log.totals()
        mode = mode_of(s)
        branch = git_branch(agent.root)
        todos = getattr(getattr(agent, "extras", None), "todos", [])
        window = s.context_tokens or 0
        used = agent._estimate() if window else 0
        pct = min(100, used * 100 // window) if window else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        sep = ("class:sep", " · ")
        parts = [("class:model", f" {short_model(s)}"), sep,
                 ("class:dim", f"{Path(agent.root).name or agent.root}"), *([("class:branch", f" ⎇ {branch}")] if branch else []), sep,
                 ("class:dim", f"context [{bar}] {pct}%"), sep,
                 ("class:dim", f"{_k(t['tokens_in'] + t['tokens_out'])} tokens")]
        if todos:
            parts += [sep, ("class:dim", f"plan {sum(i['status'] == 'done' for i in todos)}/{len(todos)}")]
        style = {"asks first": "class:dim", "auto-accept": "class:warn", "plan only": "class:plan"}[mode]
        parts += [sep, (style, f"⏵⏵ {mode}"), ("class:hint", " ⇧Tab")]
        return FormattedText([("class:rule", rule() + "\n"), *parts])

    accent = ui.hexcolour("accent") if ui.color else ""
    dark = ui.theme.background != "light"
    style = Style.from_dict({
        "prompt": f"bold {accent}" if accent else "bold",
        "rule": "#555555" if dark else "#bbbbbb",
        "bottom-toolbar": "noreverse bg:default",
        "bottom-toolbar.text": "bg:default",
        "model": f"bold {accent}" if accent else "bold",
        "branch": accent or "",
        "dim": "#9a9a9a" if dark else "#666666",
        "sep": "#555555" if dark else "#aaaaaa",
        "hint": "#6f6f6f" if dark else "#999999",
        "warn": "bold #e5a50a",
        "plan": "bold #5fafff",
        "placeholder": "#6f6f6f italic" if dark else "#999999 italic",
        "completion-menu.completion": "bg:#333333 #e6e6e6",
        "completion-menu.completion.current": f"bg:{accent or '#555555'} #000000",
        "completion-menu.meta.completion": "bg:#262626 #a0a0a0",
        "completion-menu.meta.completion.current": "bg:#3a3a3a #ffffff",
    })
    history_path().parent.mkdir(parents=True, exist_ok=True)
    session = PromptSession(history=FileHistory(str(history_path())), completer=RooksCompleter(agent),
                            complete_while_typing=True, key_bindings=keys, bottom_toolbar=toolbar, style=style,
                            complete_style="column", reserve_space_for_menu=8,
                            placeholder=FormattedText([("class:placeholder", "Ask anything, or describe a job. / for commands, @ to add a file")]))
    ui.agent = agent  # the Working line shows live token use
    warned = False
    while True:
        try:
            line = session.prompt(message)
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
        if line == "?":
            t = Table.grid(padding=(0, 3))
            t.add_column(style=f"bold {ui.hexcolour('accent')}", no_wrap=True)
            t.add_column()
            for k, v in SHORTCUTS:
                t.add_row(k, v)
            ui.console.print(Padding(t, (0, 2, 1, 2)))
            continue
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            cmd = ALIASES.get(cmd.lower(), cmd.lower())
            if cmd == "/exit":
                ui.p(ui.c("dim", "Bye."))
                return 0
            command(agent, ui, cmd, rest.strip())
            continue
        agent.run(line)


INIT_PROMPT = ("Write AGENTS.md at the project root for coding agents working here: what the project is (2-3 lines), how "
               "it is laid out (main folders and what lives where), the exact commands to install, run, test, lint and "
               "build (check each one exists: read package.json, pyproject.toml, Makefile, scripts and CI files), code "
               "style rules you can see in the code, and pitfalls. Under 120 lines, facts only. If AGENTS.md exists, "
               "improve it instead of replacing what is right.")


def git_branch(root) -> str:
    head = Path(root)
    for _ in range(12):
        g = head / ".git"
        if g.is_file():  # a worktree: ".git" names the real git dir
            try:
                d = Path(g.read_text().split("gitdir:", 1)[1].strip())
                g = d if d.is_absolute() else head / d
            except (OSError, IndexError):
                return ""
        f = g / "HEAD"
        if f.is_file():
            try:
                ref = f.read_text().strip()
            except OSError:
                return ""
            return ref.split("refs/heads/", 1)[-1] if ref.startswith("ref:") else ref[:8]
        if head.parent == head:
            return ""
        head = head.parent
    return ""


def show_diff(agent, ui, rest: str) -> None:
    try:
        stat = subprocess.run(["git", "diff", "--stat", "HEAD", *rest.split()], cwd=agent.root, capture_output=True, text=True, timeout=20)
        diff = subprocess.run(["git", "diff", "HEAD", *rest.split()], cwd=agent.root, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        ui.error(f"git is not available here ({e})")
        return
    if stat.returncode != 0:
        ui.error((stat.stderr or "not a git repository").strip())
        return
    if not diff.stdout.strip():
        ui.p("  No changes since the last commit.")
        return
    from rich.syntax import Syntax

    ui.console.print(Padding(Text(stat.stdout.rstrip(), style=ui.hexcolour("dim")), (0, 0, 0, 2)))
    lines = diff.stdout.splitlines()
    ui.console.print(Syntax("\n".join(lines[:600]), "diff", theme=ui.code_theme(), background_color="default"))
    if len(lines) > 600:
        ui.p(ui.c("dim", f"  … {len(lines) - 600} more lines (git diff to see all)"))


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
    elif cmd == "/diff":
        show_diff(agent, ui, rest)
    elif cmd == "/jobs":
        ex = agent.extras
        try:
            parts = rest.split()
            out = ex.jobs.stop(parts[1]) if parts[:1] == ["stop"] and len(parts) > 1 else ex.jobs.check(rest.strip(), 30)
            ui.p("  " + out.replace("\n", "\n  "))
        except Exception as e:  # noqa: BLE001 - ToolError: say it
            ui.error(str(e))
    elif cmd == "/init":
        agent.run(INIT_PROMPT)
    elif cmd == "/context":
        used = agent._estimate()
        window = agent.s.context_tokens or 0
        ui.p(f"  About {used:,} tokens in use" + (f" of {window:,} ({used * 100 // window}%); older turns are summarised at 70%." if window else "."))
        files = getattr(agent, "instruction_files", [])
        ui.p("  Project instructions: " + (", ".join(files) if files else "none yet (/init writes AGENTS.md)"))
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
