"""Rules the companion applies on your computer, whatever ROOKS sends. A second line of defence: even a compromised or
confused server cannot make the companion drive a shell, a password manager or a folder you did not allow."""

from __future__ import annotations

import os
import re

HARD_BLOCKED_APPS = {"terminal", "iterm", "iterm2", "warp", "alacritty", "kitty", "wezterm", "hyper", "powershell",
                     "command prompt", "cmd", "windows terminal", "xterm", "gnome-terminal", "konsole",
                     "keychain access", "passwords", "1password", "bitwarden", "lastpass", "dashlane", "keepassxc",
                     "system settings", "system preferences", "script editor", "automator", "shortcuts",
                     "activity monitor", "disk utility", "registry editor", "regedit"}
CONSEQUENTIAL_RE = re.compile(
    r"\b(send|submit|post|publish|share|tweet|reply all|buy|purchase|order|checkout|check out|pay|payment|transfer|donate|"
    r"subscribe|delete|remove|erase|empty trash|discard|overwrite|replace all|uninstall|install|format|reset|"
    r"sign out|log out|deactivate|close account|confirm|authorize|approve|grant|don't save|do not save|move to trash|replace)\b", re.I)
WRITE_KINDS = {"open_app", "focus", "click", "type_text", "key", "scroll", "set_value", "menu", "click_visual", "browser_open",
               "browser_click", "browser_fill", "browser_select", "browser_back", "files_move", "files_mkdir", "files_copy", "files_write",
               "files_undo", "sheet_update", "sheet_dedupe", "doc_write"}


class Refused(Exception):
    """The companion will not do this. The message says why, in plain words."""


def app_allowed(app: str, extra_blocked: list[str]) -> None:
    a = (app or "").strip().lower()
    if not a:
        return
    blocked = HARD_BLOCKED_APPS | {b.lower() for b in extra_blocked}
    if a in blocked or any(a.startswith(b + " ") for b in blocked):
        raise Refused(f"This computer does not let ROOKS control {app}.")


def check_element(el: dict, approved: bool, typing: bool = False) -> None:
    """Checks on the element actually found on screen, not on what the request claimed it was."""
    label = f"{el.get('label', '')} {el.get('description', '')}"
    if typing and el.get("secure"):
        raise Refused("That is a password field. ROOKS never types into password fields.")
    if not typing and CONSEQUENTIAL_RE.search(label) and not approved:
        raise Refused(f'"{label.strip()[:60]}" acts beyond the screen (send, buy, delete or similar) and was not approved.')


def label_matches(claimed: str, actual: str) -> bool:
    c, a = (claimed or "").strip().lower(), (actual or "").strip().lower()
    return bool(c) and bool(a) and (c in a or a in c)


def resolve(path: str, folders: list[str], must_exist: bool = True) -> str:
    """The real path of a file inside a folder you allowed. Links that lead outside are refused."""
    if not folders:
        raise Refused("No folders are allowed on this computer. Allow one with: rooks-companion allow-folder PATH")
    p = os.path.realpath(os.path.expanduser(path))
    for f in folders:
        root = os.path.realpath(os.path.expanduser(f))
        if p == root or p.startswith(root + os.sep):
            if must_exist and not os.path.exists(p):
                raise Refused(f"{path} does not exist.")
            return p
    raise Refused(f"{path} is outside the folders you allowed for ROOKS.")
