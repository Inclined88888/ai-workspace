"""Terminal colours that follow the theme chosen in AI Workspace (Sunshine, Paper, Cherry & Ink, Neon, Dark, High contrast).

Which theme: --theme, then ROOKS_THEME, then the app's choice in ~/.ai-workspace/appearance.json (written by the desktop
app; AI_WORKSPACE_APPEARANCE_FILE overrides the path), then Sunshine.
Which variant: every theme has a set for dark terminals and one for light terminals, because a colour that reads on
black fails on white. ROOKS_TERMINAL=light|dark decides; otherwise COLORFGBG (set by many terminals); otherwise dark.
How: 24-bit colour when the terminal says it supports it (COLORTERM=truecolor/24bit, Windows Terminal, iTerm, VS Code,
WezTerm), else the nearest of the 256 xterm colours. NO_COLOR and --no-color still turn colour off (handled by the
caller). Every colour meets WCAG AA (4.5:1) against a black or white background (scripts/check-theme-contrast.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ROLES = ("accent", "ok", "error", "warn", "dim", "prompt")
THEMES = ("sunshine", "paper", "cherry", "neon", "night", "contrast")
ALIASES = {"dark": "night", "light": "sunshine", "linen": "paper", "gallery": "paper", "auto": "sunshine", "high-contrast": "contrast"}

# theme -> terminal background -> role -> colour
PALETTES: dict[str, dict[str, dict[str, str]]] = {
    "sunshine": {
        "dark": {"accent": "#ffc83d", "ok": "#7fd18b", "error": "#ff7a6e", "warn": "#ffd466", "dim": "#a39c8f", "prompt": "#ffc83d"},
        "light": {"accent": "#8a5a00", "ok": "#2f6b3a", "error": "#b42318", "warn": "#8a5a00", "dim": "#615a4e", "prompt": "#8a5a00"},
    },
    "paper": {
        "dark": {"accent": "#efeae0", "ok": "#8fc79a", "error": "#ff6b78", "warn": "#e2b15a", "dim": "#aca598", "prompt": "#efeae0"},
        "light": {"accent": "#141311", "ok": "#2d6236", "error": "#a61c1c", "warn": "#7a4c00", "dim": "#5a554c", "prompt": "#141311"},
    },
    "cherry": {
        "dark": {"accent": "#ff6b78", "ok": "#8fc79a", "error": "#ff8379", "warn": "#e2b15a", "dim": "#aca598", "prompt": "#ff6b78"},
        "light": {"accent": "#a50d26", "ok": "#2d6236", "error": "#a50d26", "warn": "#7a4c00", "dim": "#57524a", "prompt": "#a50d26"},
    },
    "neon": {
        "dark": {"accent": "#d4ff3a", "ok": "#5ef0a0", "error": "#ff7a86", "warn": "#ffc94d", "dim": "#a3adb8", "prompt": "#d4ff3a"},
        "light": {"accent": "#476000", "ok": "#006b3c", "error": "#b0122b", "warn": "#7a5200", "dim": "#56606b", "prompt": "#476000"},
    },
    "night": {
        "dark": {"accent": "#ff6b78", "ok": "#7fc08a", "error": "#ff8379", "warn": "#e2b15a", "dim": "#aca598", "prompt": "#ff6b78"},
        "light": {"accent": "#b5122b", "ok": "#2d6236", "error": "#a61c1c", "warn": "#7a4c00", "dim": "#5a554c", "prompt": "#b5122b"},
    },
    "contrast": {
        "dark": {"accent": "#8ab4ff", "ok": "#5ef08a", "error": "#ff9a9a", "warn": "#ffd75e", "dim": "#e0e0e0", "prompt": "#ffffff"},
        "light": {"accent": "#0033aa", "ok": "#005c1f", "error": "#a00000", "warn": "#6b4000", "dim": "#1f1f1f", "prompt": "#000000"},
    },
}
BOLD_THEMES = ("contrast",)  # high contrast also makes every coloured line bold


def appearance_file() -> Path:
    p = os.environ.get("AI_WORKSPACE_APPEARANCE_FILE")
    return Path(p) if p else Path.home() / ".ai-workspace" / "appearance.json"


def normalise(name: str | None) -> str | None:
    if not name:
        return None
    n = name.strip().lower()
    n = ALIASES.get(n, n)
    return n if n in THEMES else None


def theme_from_app() -> str | None:
    try:
        data = json.loads(appearance_file().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return normalise(data.get("resolved")) or normalise(data.get("theme"))


def terminal_background(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    forced = (env.get("ROOKS_TERMINAL") or "").strip().lower()
    if forced in ("light", "dark"):
        return forced
    fgbg = env.get("COLORFGBG", "")
    if fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return "light" if bg in (7, 15) or 9 < bg < 16 and bg != 8 else "dark"
        except ValueError:
            pass
    return "dark"


def truecolor(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if env.get("WT_SESSION"):
        return True
    return env.get("TERM_PROGRAM", "") in ("iTerm.app", "vscode", "WezTerm", "ghostty")


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_256(hex_colour: str) -> int:
    """Nearest xterm-256 colour (6x6x6 cube or the grey ramp)."""
    r, g, b = _rgb(hex_colour)
    steps = [0, 95, 135, 175, 215, 255]

    def idx(v: int) -> int:
        return min(range(6), key=lambda i: abs(steps[i] - v))

    ci = (idx(r), idx(g), idx(b))
    cube = (steps[ci[0]], steps[ci[1]], steps[ci[2]])
    grey_i = max(0, min(23, round((sum((r, g, b)) / 3 - 8) / 10)))
    grey = 8 + grey_i * 10
    d_cube = sum((a - c) ** 2 for a, c in zip((r, g, b), cube))
    d_grey = sum((a - grey) ** 2 for a in (r, g, b))
    return 232 + grey_i if d_grey < d_cube else 16 + 36 * ci[0] + 6 * ci[1] + ci[2]


@dataclass
class TerminalTheme:
    name: str
    background: str
    true_colour: bool

    @property
    def palette(self) -> dict[str, str]:
        return PALETTES[self.name][self.background]

    def sgr(self, role: str) -> str:
        """The SGR parameters (the part between ESC[ and m) for a role, or a plain style ('bold')."""
        if role == "bold":
            return "1"
        colour = self.palette.get(role)
        if colour is None:
            return "0"
        r, g, b = _rgb(colour)
        base = f"38;2;{r};{g};{b}" if self.true_colour else f"38;5;{to_256(colour)}"
        return f"1;{base}" if self.name in BOLD_THEMES else base


# What the older hard-coded ANSI codes meant, so callers can keep passing them.
LEGACY_CODES = {"31": "error", "32": "ok", "33": "warn", "36": "prompt", "2": "dim", "1": "bold", "35": "accent", "34": "accent"}


def load(name: str | None = None, env: dict | None = None) -> TerminalTheme:
    env = os.environ if env is None else env
    chosen = normalise(name) or normalise(env.get("ROOKS_THEME")) or theme_from_app() or "sunshine"
    return TerminalTheme(chosen, terminal_background(env), truecolor(env))
