"""What Rooks Desk may do without asking, and what always needs a yes from the person.

Categories (each approval names one):
  shell       run a terminal command (read-only ones such as `ls`, `pwd`, `git status` do not ask)
  danger      a command that can destroy data or the system (rm -rf, sudo, mkfs, dd, format, curl | sh, ...). Always asks;
              cannot be pre-approved
  file_write  write, move or create a file outside the allowed folders (inside them it does not ask)
  file_read   read a file outside the allowed folders
  file_delete delete a file (always asks; deleted files go to Rooks Desk's trash, not oblivion)
  message     send an email or a message, or click Send / Post / Reply in an app
  purchase    Buy / Pay / Place order / Checkout, or typing into card fields
  login       Sign in / Log in, or anything touching passwords. Typing into a password box is refused outright: the
              person types it. Cannot be pre-approved
  app         open or control an app that is not on the allow-list, or that is on the deny-list (password managers,
              Keychain, banking by default)
  mcp         call a tool on another MCP server that is not marked trusted
  system      system-level hotkeys (log out, lock, task manager) and settings panes

`--approve shell,file_write` (or `approve: [...]` in a job) pre-approves categories for one run, except danger and login.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path

CATEGORIES = ("shell", "danger", "file_write", "file_read", "file_delete", "message", "purchase", "login", "app", "mcp", "system")
NEVER_PREAPPROVED = {"danger", "login"}

SAFE_COMMANDS = {"ls", "pwd", "echo", "cat", "head", "tail", "wc", "whoami", "date", "uname", "which", "where", "type", "file",
                 "stat", "du", "df", "hostname", "id", "uptime", "tree", "find", "grep", "rg", "sort", "uniq",
                 "dir", "Get-ChildItem", "Get-Location", "Get-Content", "Get-Date", "Get-Process", "ps", "top", "free", "lsb_release",
                 "sw_vers", "ver", "python3 --version", "node --version"}
# Read-only commands that print what is inside files. Outside the allowed folders they ask (file_read); the others
# (ls, df, du, stat, find, ...) only show names and sizes.
CONTENT_READERS = {"cat", "head", "tail", "wc", "grep", "rg", "sort", "uniq", "file", "Get-Content", "type", "git"}
SAFE_GIT = {"status", "log", "diff", "show", "branch", "remote", "rev-parse"}
DANGER = re.compile(
    r"(\brm\s+(-[a-z]*[rf][a-z]*\s+)+|\brm\s+-rf\b|\bsudo\b|\bdoas\b|\bsu\s+-|\bmkfs|\bdd\s+if=|\bformat\s+[a-z]:|\bdiskpart\b|"
    r"\bshutdown\b|\breboot\b|\bhalt\b|:\(\)\s*\{|\bchmod\s+-R\s+777|\bchown\s+-R|>\s*/dev/sd|\bcurl\b[^|]*\|\s*(ba|z)?sh|"
    r"\bwget\b[^|]*\|\s*(ba|z)?sh|\biwr\b.*\|\s*iex|Invoke-Expression|Remove-Item\b.*-Recurse|\bdel\s+/[sq]|\brmdir\s+/s|"
    r"\bgit\s+push\s+.*--force|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f|Set-ExecutionPolicy|\breg\s+delete|"
    r"\bcrontab\s+-r|\blaunchctl\s+(unload|remove)|\bsystemctl\s+(stop|disable|mask)|\bkill\s+-9\s+1\b|\bkillall\b|"
    r"\bsecurity\s+(find|dump)-.*password|\bnetsh\b|\biptables\b|\bufw\b|\bcsrutil\b|\bspctl\b)", re.I)
MESSAGE_WORDS = re.compile(r"\b(send|send now|post|publish|tweet|reply|reply all|forward|share|submit comment)\b", re.I)
PURCHASE_WORDS = re.compile(r"\b(buy|buy now|pay|pay now|place (your )?order|checkout|check out|purchase|subscribe|confirm (order|payment)|"
                            r"complete (order|purchase)|add payment)\b", re.I)
LOGIN_WORDS = re.compile(r"\b(sign in|log in|login|sign-in|log-in|authenticate|unlock|password|passcode|two-factor|2fa|verification code)\b", re.I)
CARD_WORDS = re.compile(r"\b(card number|credit card|cvv|cvc|security code|expir|iban|routing number|account number)\b", re.I)
TERMINAL_APPS = re.compile(r"(terminal|iterm|konsole|xterm|alacritty|kitty|wezterm|powershell|cmd\.exe|command prompt|windows terminal|"
                           r"xfce4-terminal|gnome-terminal|tilix|terminator|hyper|warp)", re.I)
DEFAULT_DENY_APPS = ["1Password", "Bitwarden", "KeePass", "KeePassXC", "LastPass", "Dashlane", "Keychain Access", "Passwords",
                     "seahorse", "Credential Manager"]
SYSTEM_HOTKEYS = {("ctrl", "alt", "delete"), ("cmd", "q"), ("cmd", "shift", "q"), ("ctrl", "cmd", "q"), ("cmd", "option", "esc"),
                  ("alt", "f4"), ("cmd", "alt", "esc"), ("ctrl", "shift", "esc"), ("cmd", "l")}


def home() -> Path:
    from .runner import home as h
    return h()


def default_folders() -> list[str]:
    hm = Path.home()
    return [str(hm / "Documents"), str(hm / "Desktop"), str(hm / "Downloads"), str(hm / "RooksDesk")]


@dataclass
class DeskPolicy:
    folders: list[str] = field(default_factory=default_folders)  # read/write here without asking
    allow_apps: list[str] = field(default_factory=list)  # empty = any app not denied
    deny_apps: list[str] = field(default_factory=lambda: list(DEFAULT_DENY_APPS))
    trusted_mcp: list[str] = field(default_factory=list)
    safe_commands: list[str] = field(default_factory=list)  # extra commands that run without asking

    # --- folders
    def resolved_folders(self) -> list[Path]:
        out = []
        for f in self.folders:
            try:
                out.append(Path(os.path.expandvars(f)).expanduser().resolve())
            except OSError:
                continue
        return out

    def inside(self, path: str | Path) -> bool:
        try:
            p = Path(os.path.expandvars(str(path))).expanduser().resolve()
        except OSError:
            return False
        return any(p == f or f in p.parents for f in self.resolved_folders())

    # --- apps
    def app_check(self, app: str) -> tuple[bool, str]:
        a = (app or "").lower()
        for d in self.deny_apps:
            if d.lower() in a:
                return False, f"{app} is on the deny list (password managers and similar)"
        if self.allow_apps and not any(x.lower() in a for x in self.allow_apps):
            return False, f"{app} is not on the allow list ({', '.join(self.allow_apps)})"
        return True, ""

    # --- shell
    def shell_category(self, command: str, cwd: str | None = None) -> str | None:
        """None: runs without asking (read-only, and only reads inside the allowed folders). `env`/`printenv` are not
        read-only here: the environment can hold keys."""
        c = (command or "").strip()
        if DANGER.search(c):
            return "danger"
        if self.read_only(c):
            return "file_read" if self._reads_contents(c) and self.reads_outside(c, cwd) else None
        return "shell"

    @staticmethod
    def _reads_contents(c: str) -> bool:
        try:
            words = shlex.split(c, posix=os.name != "nt")
        except ValueError:
            return True
        return bool(words) and os.path.basename(words[0]) in CONTENT_READERS

    def reads_outside(self, c: str, cwd: str | None = None) -> bool:
        """Whether a read-only command names a file or folder outside the allowed folders (`cat ~/.ssh/id_rsa`)."""
        try:
            words = shlex.split(c, posix=os.name != "nt")
        except ValueError:
            return True
        base = Path(cwd).expanduser() if cwd else None
        git = bool(words) and os.path.basename(words[0]) == "git"
        for w in words[1:]:
            if w.startswith("-") and not (os.name == "nt" and "\\" in w):
                continue
            pathy = w.startswith(("~", ".", "/", "\\", "$")) or "/" in w or "\\" in w or re.match(r"^[A-Za-z]:", w)
            if git and not pathy:
                continue  # git subcommands and refs
            p = Path(os.path.expandvars(w)).expanduser()
            if not p.is_absolute():
                if base is None:
                    if pathy:
                        return True
                    continue
                p = base / p
            if not self.inside(p):
                return True
        return False

    def read_only(self, c: str) -> bool:
        if re.search(r"[;&|`$<>]|\$\(", c.replace("2>&1", "")):
            return False  # chains, pipes, redirects and substitutions can do anything
        try:
            words = shlex.split(c, posix=os.name != "nt")
        except ValueError:
            return False
        if not words:
            return True
        w0 = os.path.basename(words[0])
        if w0 == "git":
            return len(words) > 1 and words[1] in SAFE_GIT
        if w0 in ("find",) and any(w in ("-delete", "-exec", "-execdir", "-ok") for w in words):
            return False
        return w0 in SAFE_COMMANDS or w0 in self.safe_commands or c in SAFE_COMMANDS

    # --- persistence
    @classmethod
    def load(cls, path: Path) -> "DeskPolicy":
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        p = cls()
        for k in ("folders", "allow_apps", "deny_apps", "trusted_mcp", "safe_commands"):
            if isinstance(d.get(k), list):
                setattr(p, k, [str(x) for x in d[k]])
        return p

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def to_dict(self) -> dict:
        return {"folders": self.folders, "allow_apps": self.allow_apps, "deny_apps": self.deny_apps, "trusted_mcp": self.trusted_mcp,
                "safe_commands": self.safe_commands}


@dataclass
class Approval:
    category: str
    action: str
    summary: str  # one sentence the person reads before saying yes
    detail: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    screenshot: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "category": self.category, "action": self.action, "summary": self.summary, "detail": self.detail,
                "screenshot": self.screenshot}


def classify_gui(action: str, args: dict, element: dict | None, app: str, policy: DeskPolicy) -> tuple[str | None, str]:
    """Category for a click/type/hotkey, from the element under it (accessibility tree), the model's own description of
    the target, and the app in front. Returns (category, reason)."""
    label = " ".join(str(x) for x in ((element or {}).get("name", ""), (element or {}).get("role", ""), args.get("target", "")) if x)
    if action in ("click", "double_click", "right_click"):
        if LOGIN_WORDS.search(label):
            return "login", f"clicking “{label.strip()}”"
        if PURCHASE_WORDS.search(label):
            return "purchase", f"clicking “{label.strip()}”"
        if MESSAGE_WORDS.search((element or {}).get("name", "") or args.get("target", "") or ""):
            return "message", f"clicking “{label.strip()}”"
    if action == "type":
        if (element or {}).get("secret") or re.search(r"password|passcode", label, re.I):
            return "login", "typing into a password field"
        if CARD_WORDS.search(label):
            return "purchase", "typing payment details"
        if TERMINAL_APPS.search(app or ""):
            text = str(args.get("text", ""))
            if "\n" in text or args.get("enter"):
                return ("danger" if DANGER.search(text) else "shell"), "typing a command into a terminal"
    if action == "hotkey":
        keys = tuple(sorted(args.get("keys_norm") or []))
        if keys in {tuple(sorted(k)) for k in SYSTEM_HOTKEYS}:
            return "system", f"pressing {'+'.join(args.get('keys_norm') or [])}"
        if TERMINAL_APPS.search(app or "") and "enter" in (args.get("keys_norm") or []):
            return "shell", "pressing Enter in a terminal runs whatever is typed there"
    return None, ""


def parse_categories(text: str | None) -> set[str]:
    out = set()
    for part in re.split(r"[,\s]+", text or ""):
        p = part.strip().lower().replace("-", "_")
        if not p:
            continue
        if p in ("all", "everything"):
            out |= set(CATEGORIES) - NEVER_PREAPPROVED
            continue
        if p in {"files", "write", "file"}:
            p = "file_write"
        if p in {"email", "messages", "send"}:
            p = "message"
        if p not in CATEGORIES:
            raise ValueError(f"unknown approval category {part!r} (choose from {', '.join(CATEGORIES)})")
        if p in NEVER_PREAPPROVED:
            raise ValueError(f"{p} can't be pre-approved: Rooks Desk always asks")
        out.add(p)
    return out
