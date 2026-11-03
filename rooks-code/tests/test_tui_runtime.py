"""Exercise the interactive entry point without a provider or real user state."""
import ast
import io
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from rooks_code import tui
from rooks_code.cli import UI


@pytest.mark.parametrize("ending", ["/exit", "exit", "quit", "bye", EOFError()])
def test_interactive_loop_dispatches_prompt_and_exits(monkeypatch, tmp_path, ending):
    prompts = iter([KeyboardInterrupt(), "", "fix the failing test", ending])
    calls = []
    sessions = []

    class Session:
        def __init__(self, **kwargs):
            sessions.append(kwargs)

        def prompt(self, message):
            value = next(prompts)
            if isinstance(value, BaseException):
                raise value
            return value

    monkeypatch.setattr(tui, "PromptSession", Session)
    monkeypatch.setattr(tui, "banner", lambda *args: None)
    monkeypatch.setattr(tui, "history_path", lambda: tmp_path / "history")
    ui = tui.RichUI(out=io.StringIO(), color=False)
    agent = SimpleNamespace(run=calls.append, s=SimpleNamespace())

    assert tui.run(agent, ui) == 0
    assert calls == ["fix the failing test"]
    assert len(sessions) == 1
    assert ui.agent is agent
    assert "Bye." in ui.out.getvalue()


def test_init_command_has_a_real_prompt():
    calls = []
    agent = SimpleNamespace(run=calls.append, s=SimpleNamespace())
    tui.command(agent, tui.RichUI(out=io.StringIO(), color=False), "/init", "")
    assert len(calls) == 1
    assert "AGENTS.md" in calls[0]


def test_tui_has_no_shadowed_definitions():
    tree = ast.parse(Path(tui.__file__).read_text())
    for node in [tree, *(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))]:
        names = Counter(n.name for n in node.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)))
        assert not {name: count for name, count in names.items() if count > 1}


@pytest.mark.parametrize("ui_type", [tui.RichUI, UI])
@pytest.mark.parametrize("invalid", ["d", "s", "v", "yes please", "never", "approve", "absolutely not"])
def test_approval_rejects_unsupported_choices(monkeypatch, invalid, ui_type):
    answers = iter([invalid, "n"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    monkeypatch.setattr("sys.stdin", io.StringIO(invalid + "\nn\n"))
    ui = ui_type(out=io.StringIO(), color=False)
    assert ui.ask(SimpleNamespace(effect="write")) == "n"
