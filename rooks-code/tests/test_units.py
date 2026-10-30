from pathlib import Path

import pytest

from rooks_code import config, providers
from rooks_code.tools import Toolbox, ToolError


def test_config_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("ROOKS_MODEL", raising=False)
    monkeypatch.delenv("ROOKS_BASE_URL", raising=False)
    file = {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.5", "max_usd": 3.0}
    monkeypatch.delenv("ROOKS_PROVIDER", raising=False)
    s = config.resolve({}, tmp_path, file)
    assert (s.provider, s.model, s.base_url, s.key_env, s.max_usd) == (
        "openrouter", "anthropic/claude-sonnet-4.5", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", 3.0)
    s = config.resolve({"provider": "ollama"}, tmp_path, file)
    assert s.model == "qwen2.5:7b" and s.local and s.base_url == "http://localhost:11434/v1"
    monkeypatch.setenv("ROOKS_MODEL", "llama3.2")
    assert config.resolve({"provider": "lmstudio"}, tmp_path, file).model == "llama3.2"
    assert config.resolve({"provider": "ollama", "max_usd": 0}, tmp_path, file).max_usd is None
    with pytest.raises(SystemExit):
        config.resolve({"provider": "nope"}, tmp_path, file)
    custom = {"providers": {"groq": {"kind": "openai", "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
                                     "model": "llama-3.3-70b"}}}
    assert config.resolve({"provider": "groq"}, tmp_path, custom).base_url == "https://api.groq.com/openai/v1"


def test_example_config_parses():
    import tomllib
    assert "provider" not in tomllib.loads(config.EXAMPLE)  # the default is chosen at run time (open app, else ollama)


def test_prices():
    assert providers.price("claude-sonnet-4-5-20250929", {}, False) == (3.0, 15.0)
    assert providers.price("openai/gpt-4.1-mini", {}, False) == (0.4, 1.6)
    assert providers.price("whatever", {}, True) == (0.0, 0.0)
    assert providers.price("mystery", {}, False) is None
    assert providers.cost("mystery", 10, 10, {"mystery": [1, 2]}, False) == 0.00003


def test_trusted_folders(tmp_path):
    s = config.Settings(root=tmp_path / "a" / "b", trusted_folders=[str(tmp_path / "a")])
    assert s.trusted()
    assert not config.Settings(root=tmp_path, trusted_folders=[str(tmp_path / "a")]).trusted()


def test_edit_requires_unique_match(tmp_path):
    (tmp_path / "f.txt").write_text("x\nx\n")
    tb = Toolbox(tmp_path)
    with pytest.raises(ToolError, match="2 times"):
        tb.prepare("edit_file", {"path": "f.txt", "old_text": "x", "new_text": "y"})
    plan = tb.prepare("edit_file", {"path": "f.txt", "old_text": "x", "new_text": "y", "replace_all": True})
    assert "-x" in plan.preview and (tmp_path / "f.txt").read_text() == "x\nx\n"  # nothing applied before approval
    assert plan.apply()[1] is True and (tmp_path / "f.txt").read_text() == "y\ny\n"
    with pytest.raises(ToolError, match="managed by rooks"):
        tb.prepare("write_file", {"path": ".git/config", "content": ""})


def test_search_and_list_skip_noise(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.js").write_text("needle")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.js").write_text("one\nneedle here\n")
    tb = Toolbox(tmp_path)
    assert tb.prepare("search", {"pattern": "needle"}).apply()[0] == "src/b.js:2: needle here"
    assert tb.prepare("list_files", {"pattern": "*.js"}).apply()[0] == "src/b.js"


def test_tool_calls_printed_as_text_are_recovered_only_when_valid():
    names = {"list_files", "read_file"}
    text = 'Let me look.\n\n```json\n{"name": "list_files", "arguments": {}}\n```\n\nThen I will read.'
    calls, rest = providers.parse_text_tool_calls(text, names)
    assert [(c.name, c.arguments, c.from_text) for c in calls] == [("list_files", {}, True)]
    assert rest == "Let me look.\n\n\n\nThen I will read."
    tagged = '<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>'
    assert providers.parse_text_tool_calls(tagged, names)[0][0].arguments == {"path": "a.py"}
    for not_a_call in ('```json\n{"name": "rm_rf", "arguments": {}}\n```', '```json\n{"a": 1}\n```', "just text {x}"):
        assert providers.parse_text_tool_calls(not_a_call, names) == ([], not_a_call)


def test_announced_step_detection():
    from rooks_code.agent import announces_step
    assert announces_step("Great. First, I'll run the tests using `python3 test_calc.py`.")
    assert announces_step("Let's execute the command:")
    assert not announces_step("Fixed add(): it subtracted. The test now passes.")
    assert not announces_step("Shall I run the tests?")
    assert announces_step("Next, I'll **search** in `test_calc.py`:\n\n1. **Search in `test_calc.py`** for failed tests:")
    assert not announces_step("Here is a summary: 1. Listed files. I'll be here if you need more. Let me know!")


def test_text_tool_calls_several_objects_in_one_fence():
    from rooks_code import providers

    text = ('Step 4\n```json\n{"name": "edit_file", "arguments": {"path": "a.py", "old_text": "x", "new_text": "y"}}\n'
            '{"name": "edit_file", "arguments": {"path": "a.py", "old_text": "p", "new_text": "q"}}\n```\nthen check')
    calls, rest = providers.parse_text_tool_calls(text, {"edit_file", "run_command"})
    assert [c.arguments["new_text"] for c in calls] == ["y", "q"]
    assert "edit_file" not in rest
