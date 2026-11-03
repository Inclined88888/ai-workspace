"""Terminal colours follow the AI Workspace theme, pick a light or dark set for the terminal, and stay off for NO_COLOR."""

import io
import json

from rooks_code import theme
from rooks_code.cli import UI, parser


def _lum(h):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def test_every_theme_has_both_variants_and_meets_aa():
    assert set(theme.PALETTES) == set(theme.THEMES)
    for name, variants in theme.PALETTES.items():
        for bg, against in (("dark", "#000000"), ("light", "#ffffff")):
            for role in theme.ROLES:
                c = variants[bg][role]
                assert _ratio(c, against) >= 4.5, (name, bg, role, c)
            # and on the usual off-black / off-white terminal defaults
            for role in theme.ROLES:
                other = "#1e1e1e" if bg == "dark" else "#f5f5f5"
                assert _ratio(variants[bg][role], other) >= 4.5, (name, bg, role, other)


def test_choice_order_flag_then_env_then_app_file(tmp_path):
    f = tmp_path / "appearance.json"
    f.write_text(json.dumps({"theme": "auto", "resolved": "night"}))
    env = {"AI_WORKSPACE_APPEARANCE_FILE": str(f)}
    import os
    old = dict(os.environ)
    try:
        os.environ.update(env)
        os.environ.pop("ROOKS_THEME", None)
        assert theme.load(env={}).name == "night"
        assert theme.load(env={"ROOKS_THEME": "neon"}).name == "neon"
        assert theme.load("contrast", env={"ROOKS_THEME": "neon"}).name == "contrast"
        f.write_text("{broken")
        assert theme.load(env={}).name == "sunshine"
        f.write_text(json.dumps({"theme": "purple"}))
        assert theme.load(env={}).name == "sunshine"
    finally:
        os.environ.clear()
        os.environ.update(old)


def test_terminal_background_detection():
    assert theme.terminal_background({}) == "dark"
    assert theme.terminal_background({"COLORFGBG": "0;15"}) == "light"
    assert theme.terminal_background({"COLORFGBG": "15;0"}) == "dark"
    assert theme.terminal_background({"COLORFGBG": "15;default;0"}) == "dark"
    assert theme.terminal_background({"ROOKS_TERMINAL": "light", "COLORFGBG": "15;0"}) == "light"


def test_truecolor_or_256():
    t = theme.TerminalTheme("neon", "dark", True)
    assert t.sgr("accent") == "38;2;212;255;58"
    t = theme.TerminalTheme("neon", "dark", False)
    assert t.sgr("accent").startswith("38;5;")
    assert 16 <= theme.to_256("#d4ff3a") <= 255
    assert theme.to_256("#000000") in (16, 232)
    assert theme.TerminalTheme("contrast", "light", True).sgr("error").startswith("1;"), "high contrast is bold"
    assert theme.TerminalTheme("sunshine", "dark", True).sgr("bold") == "1"


def test_ui_uses_theme_colours_and_respects_no_color(monkeypatch):
    monkeypatch.setenv("ROOKS_THEME", "neon")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("ROOKS_TERMINAL", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    ui = UI(out=io.StringIO(), err=io.StringIO(), color=True)
    assert ui.c("36", "x") == "\033[38;2;212;255;58mx\033[0m"  # the old cyan prompt is now the theme accent
    assert ui.c("accent", "x").startswith("\033[38;2;212;255;58m")
    off = UI(out=io.StringIO(), err=io.StringIO(), color=False)
    assert off.c("accent", "x") == "x"
    monkeypatch.setenv("NO_COLOR", "1")
    tty = io.StringIO()
    tty.isatty = lambda: True
    assert UI(out=tty, err=io.StringIO()).color is False


def test_theme_flag():
    assert parser().parse_args(["--theme", "neon", "hi"]).theme == "neon"
