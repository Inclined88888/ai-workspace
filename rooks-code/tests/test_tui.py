"""Task 02b: the TUI's pure pieces, unit tested without a terminal. Plain-mode tests in test_e2e.py are unchanged."""

import time
from pathlib import Path

import pytest

from rooks_code import tui


def test_status_bar_text(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/night/k3\n")
    line = tui.status_bar("openai", "gpt-4.1-mini", 1200, 340, "$0.0021", "auto", tui.git_branch(tmp_path), 75,
                          context_used_pct=42.0, width=100)
    assert "openai/gpt-4.1-mini" in line
    assert "42% ctx" in line
    assert "1,540 tok" in line
    assert "$0.0021" in line
    assert "auto" in line
    assert "night/k3" in line
    assert "1:15" in line
    assert len(line) <= 100


def test_status_bar_without_optional_parts():
    line = tui.status_bar("ollama", "qwen2.5:7b", 0, 0, "", "", "", 3)
    assert "ollama/qwen2.5:7b" in line and "3s" in line
    assert "ctx" not in line and "·  ·" not in line


def test_git_branch_detached_and_missing(tmp_path):
    assert tui.git_branch(tmp_path) == ""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("abcdef1234567890\n")
    assert tui.git_branch(tmp_path) == "abcdef12"


def test_activity_text():
    assert tui.activity("read_file", {"path": "tools.py"}) == "reading tools.py"
    assert tui.activity("search", {"pattern": "foo"}, detail="1,240 files") == "searching 'foo' (1,240 files)"
    a = tui.activity("run_command", {"command": "pytest -q"}, elapsed_s=12)
    assert a.startswith("running pytest -q") and "12s" in a
    assert tui.activity("unknown_tool", {"x": 1}) == "unknown_tool"


def test_collapse_paste():
    assert tui.collapse_paste("one line") == "one line"
    assert tui.collapse_paste("two\nlines") == "two\nlines"
    big = "\n".join(f"line {i}" for i in range(50))
    assert tui.collapse_paste(big) == "[pasted 50 lines]"
    long_single = "x" * 300
    assert tui.collapse_paste(long_single) == "[pasted 1 lines]"


def test_completions_slash(tmp_path):
    assert "/undo" in tui.completions("/un", tmp_path)
    assert tui.completions("/", tmp_path) == list(tui.SLASH_COMMANDS)
    assert tui.completions("/zzz", tmp_path) == []


def test_completions_at_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("")
    (tmp_path / "src" / "util.py").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("")
    got = tui.completions("@src/m", tmp_path)
    assert got == ["@src/main.py"]
    assert all("node_modules" not in g for g in tui.completions("@", tmp_path))


def test_render_markdown_line():
    color = lambda code, text: f"<{code}>{text}</>"  # noqa: E731
    out, in_code = tui.render_markdown_line("# Title", False, color)
    assert "Title" in out and not in_code
    out, in_code = tui.render_markdown_line("```python", in_code, color)
    assert in_code
    out, in_code = tui.render_markdown_line("print(1)", in_code, color)
    assert in_code and out.startswith("<2>")
    out, in_code = tui.render_markdown_line("```", in_code, color)
    assert not in_code
