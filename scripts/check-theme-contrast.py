#!/usr/bin/env python3
"""Check WCAG AA contrast for every theme on every surface. Exit 1 on any failure.

  Workbench (desktop + web): works out each theme's effective CSS variables from the stylesheets the app loads
  (the same cascade the browser applies to <html data-theme=... data-accent="theme">), then checks text pairs.
  Terminal (Rooks Code, Rooks Browser): every role colour against black, white and the usual off-black/off-white.
  Phone: mobile/src/lib/theme.ts is checked by its own jest test (src/lib/__tests__/theme.test.ts).

Usage: python3 scripts/check-theme-contrast.py [-v]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ["styles.css", "appearance.css", "sunshine.css", "themes.css"]  # main.tsx import order (token files only)
SRC = ROOT / "workbench" / "frontend" / "src"
THEMES = {"sunshine": "sunshine", "paper": "paper", "cherry": "cherry", "neon": "neon", "night": "dark", "contrast": "contrast"}
DARK = {"neon", "night"}

TEXT_PAIRS = [  # (foreground, background) that must reach 4.5:1
    ("text", "paper"), ("text", "panel"), ("text", "panel-2"), ("text", "panel-3"),
    ("muted", "paper"), ("muted", "panel"), ("muted", "panel-2"),
    ("faint", "paper"), ("faint", "panel"),
    ("accent-text", "paper"), ("accent-text", "panel"), ("accent-text", "accent-soft"),
    ("on-accent", "accent"), ("on-ink", "ink-btn"),
    ("danger-text", "paper"), ("ok-text", "paper"), ("warn-text", "paper"),
    ("danger-text", "danger-soft"), ("ok-text", "ok-soft"), ("warn-text", "warn-soft"),
]
UI_PAIRS = [("accent-ring", "paper")]  # non-text (focus ring): 3:1. Borders are decorative in Sunshine (soft shadows carry the edges).


def lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)

    def ch(v: int) -> float:
        x = v / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def ratio(a: str, b: str) -> float:
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def rules():
    out = []
    order = 0
    for name in CSS:
        text = re.sub(r"/\*.*?\*/", "", (SRC / name).read_text(), flags=re.S)
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
            decls = dict((k.strip(), v.strip()) for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body))
            if not decls:
                continue
            for s in sel.split(","):
                m = re.fullmatch(r"\s*:root((?:\[[^\]]+\])*)\s*", s)
                if not m:
                    continue
                attrs = dict(re.findall(r'\[([\w-]+)="([^"]*)"\]', m.group(1)))
                out.append((len(attrs), order, attrs, decls))
                order += 1
    return out


def tokens(theme_attr: str, accent: str, mode: str) -> dict:
    root = {"data-theme": theme_attr, "data-accent": accent, "data-mode": mode}
    vals: dict = {}
    for _, _, attrs, decls in sorted((r for r in rules() if all(root.get(k) == v for k, v in r[2].items())), key=lambda r: (r[0], r[1])):
        vals.update(decls)
    return vals


def check_workbench(verbose: bool) -> list[str]:
    fails = []
    for tid, attr in THEMES.items():
        t = tokens(attr, "theme", "dark" if tid in DARK else "light")
        for pairs, need in ((TEXT_PAIRS, 4.5), (UI_PAIRS, 3.0)):
            for fg, bg in pairs:
                a, b = t.get(f"--{fg}"), t.get(f"--{bg}")
                if not (a and b and a.startswith("#") and b.startswith("#")):
                    fails.append(f"workbench {tid}: missing --{fg} or --{bg}")
                    continue
                r = ratio(a, b)
                if verbose:
                    print(f"  workbench {tid:<9} {fg:>12} on {bg:<11} {r:5.2f}")
                if r < need:
                    fails.append(f"workbench {tid}: --{fg} {a} on --{bg} {b} is {r:.2f}:1 (needs {need})")
    return fails


def check_terminal(verbose: bool) -> list[str]:
    sys.path.insert(0, str(ROOT / "rooks-code"))
    from rooks_code import theme as code_theme  # noqa: E402
    sys.path.insert(0, str(ROOT / "rooks-browser"))
    from rooks_browser import theme as browser_theme  # noqa: E402
    fails = []
    if code_theme.PALETTES != browser_theme.PALETTES:
        fails.append("terminal: rooks-code and rooks-browser palettes differ (copy rooks_code/theme.py over)")
    for tid, variants in code_theme.PALETTES.items():
        for bg_kind, backgrounds in (("dark", ("#000000", "#1e1e1e", "#282c34")), ("light", ("#ffffff", "#f5f5f5"))):
            for role, colour in variants[bg_kind].items():
                for bg in backgrounds:
                    r = ratio(colour, bg)
                    if verbose:
                        print(f"  terminal  {tid:<9} {bg_kind:<5} {role:>7} on {bg} {r:5.2f}")
                    if r < 4.5:
                        fails.append(f"terminal {tid}/{bg_kind}: {role} {colour} on {bg} is {r:.2f}:1")
    return fails


if __name__ == "__main__":
    v = "-v" in sys.argv
    fails = check_workbench(v) + check_terminal(v)
    if fails:
        print("Contrast failures:")
        for f in fails:
            print("  ✗ " + f)
        sys.exit(1)
    print(f"All themes pass AA: {len(THEMES)} workbench themes x {len(TEXT_PAIRS) + len(UI_PAIRS)} pairs, terminal palettes on dark and light terminals.")
