"""The interactive terminal (tui.py): command menu, @ file picking, saved sessions, and the plain-mode fallback."""

import json

import pytest

pytest.importorskip("prompt_toolkit")
pytest.importorskip("rich")

from prompt_toolkit.document import Document  # noqa: E402

from rooks_code import tui  # noqa: E402


class FakeAgent:
    def __init__(self, root):
        self.root = root
        self.s = type("S", (), {"auto_approve": False, "dry_run": False})()
        self.messages = []
        self._listed_models = ["meta/llama-4", "nvidia/nemotron-3-ultra"]


def complete(agent, text):
    return [(c.text, c.display_meta_text) for c in tui.RooksCompleter(agent).get_completions(Document(text), None)]


def test_slash_opens_every_command_with_what_it_does(tmp_path):
    got = complete(FakeAgent(tmp_path), "/")
    assert [t for t, _ in got] == list(tui.COMMANDS)
    assert all(meta for _, meta in got)
    assert [t for t, _ in complete(FakeAgent(tmp_path), "/mo")] == ["/model"]


def test_model_names_complete_after_slash_model(tmp_path):
    assert [t for t, _ in complete(FakeAgent(tmp_path), "/model nemo")] == ["nvidia/nemotron-3-ultra"]


def test_at_lists_files_and_folders_but_not_hidden_or_junk(tmp_path):
    (tmp_path / "bank.csv").write_text("x")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.md").write_text("x")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "node_modules").mkdir()
    assert complete(FakeAgent(tmp_path), "sum up @") == [("@bank.csv", "file"), ("@docs/", "folder")]
    assert complete(FakeAgent(tmp_path), "read @docs/no") == [("@docs/notes.md", "file")]
    assert complete(FakeAgent(tmp_path), "@b") == [("@bank.csv", "file")]


def test_sessions_list_the_first_request_and_resume_completes_ids(tmp_path):
    runs = tmp_path / ".rooks" / "runs"
    runs.mkdir(parents=True)
    (runs / "20261102-a.messages.json").write_text(json.dumps({"messages": [{"role": "user", "content": "fix   the\ntests"}]}))
    (runs / "20261102-b.messages.json").write_text("not json")
    assert tui.sessions(tmp_path) == [("20261102-a", "fix the tests")]
    assert [t for t, _ in complete(FakeAgent(tmp_path), "/resume 2026")] == ["20261102-a"]


def test_not_a_terminal_means_plain_mode(monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert tui.available() is False


def test_new_clears_the_conversation_and_unknown_commands_suggest_one(tmp_path):
    import io

    agent = FakeAgent(tmp_path)
    agent.messages.append({"role": "user", "content": "hi"})
    out = io.StringIO()
    ui = tui.RichUI(out=out, color=False)
    tui.command(agent, ui, "/new", "")
    assert agent.messages == []
    tui.command(agent, ui, "/hlep", "")
    assert "Did you mean /help?" in out.getvalue()


def test_model_list_hides_models_that_cannot_chat_and_all_shows_them(tmp_path):
    import io

    from rooks_code import cli

    class Model:
        model = "a"

        def list_models(self):
            return ["meta/llama-4", "meta/llama-guard-4-12b", "nvidia/nv-embedqa-mistral-7b-v2", "whisper-1", "gpt-5.1"]

    agent = FakeAgent(tmp_path)
    agent.s = type("S", (), {"model": "gpt-5.1", "provider": "x"})()
    agent.model = Model()
    out = io.StringIO()
    ui = cli.UI(out=out, color=False)
    cli.model_command(agent, ui, "")
    text = out.getvalue()
    assert "meta/llama-4" in text and "gpt-5.1" in text and "llama-guard" not in text and "whisper" not in text
    assert "3 more that can't chat are hidden" in text
    assert agent._listed_models == ["meta/llama-4", "gpt-5.1"]
    out.truncate(0)
    cli.model_command(agent, ui, "all")
    assert "llama-guard" in out.getvalue() and len(agent._listed_models) == 5


def test_app_models_read_as_auto_and_auto_is_listed_first(tmp_path):
    import io

    from rooks_code import cli

    s = type("S", (), {"model": "default", "provider": "workbench"})()
    assert tui.model_label(s, "#999").startswith("Auto  ") and "AI Workspace app" in tui.model_label(s, "#999")

    class Model:
        model = "default"

        def list_models(self):
            return ["1/meta/llama-4", "2/sonnet", "auto", "auto:fast"]

    agent = FakeAgent(tmp_path)
    agent.s, agent.model = s, Model()
    cli.model_command(agent, cli.UI(out=io.StringIO(), color=False), "")
    assert agent._listed_models[:2] == ["auto", "auto:fast"]
