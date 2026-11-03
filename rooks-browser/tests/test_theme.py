"""Rooks Browser's terminal output follows the AI Workspace theme and stays plain for NO_COLOR or pipes."""

import io
import json

from rooks_browser import cli, theme


def test_same_palettes_as_rooks_code():
    assert theme.THEMES == ("sunshine", "paper", "cherry", "neon", "night", "contrast")
    for name in theme.THEMES:
        assert set(theme.PALETTES[name]) == {"dark", "light"}


def test_painter_uses_the_app_theme(tmp_path, monkeypatch):
    f = tmp_path / "appearance.json"
    f.write_text(json.dumps({"theme": "cherry"}))
    monkeypatch.setenv("AI_WORKSPACE_APPEARANCE_FILE", str(f))
    monkeypatch.delenv("ROOKS_THEME", raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("ROOKS_TERMINAL", "light")
    paint = cli.painter(io.StringIO(), enabled=True)
    assert paint("accent", "x") == "\033[38;2;165;13;38mx\033[0m"  # cherry #a50d26 on a light terminal
    assert cli.painter(io.StringIO(), enabled=True, theme="neon")("ok", "x").startswith("\033[38;2;")


def test_plain_when_not_a_terminal_or_no_color(monkeypatch):
    assert cli.painter(io.StringIO())("accent", "x") == "x"
    tty = io.StringIO()
    tty.isatty = lambda: True
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli.painter(tty)("accent", "x") == "x"
