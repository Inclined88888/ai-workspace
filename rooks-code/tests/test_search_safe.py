"""Task 00: search and list must never hang, respect .gitignore, and stop early with a reason."""

import sys
import threading
import time
from pathlib import Path

import pytest

from rooks_code.tools import (MAX_LISTED, MAX_MATCHES, Cancelled, Ignore, Toolbox, _match_one, is_home_or_root)


def big_tree(root: Path, files: int = 20_000) -> Path:
    """A wide tree that hangs an unbounded walk. Every 997th file holds a needle."""
    for i in range(files):
        d = root / f"d{i // 500:03d}" / f"s{i // 50:04d}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"f{i:06d}.txt"
        f.write_text("needle on line 1\npadding\n" if i % 997 == 0 else "nothing here\n")
    return root


def test_search_stops_after_200_matches_in_a_20k_file_tree(tmp_path):
    big_tree(tmp_path / "proj", 20_000)
    tb = Toolbox(tmp_path / "proj")
    started = time.monotonic()
    out, _, _ = tb.prepare("search", {"pattern": "^nothing"}).apply()
    elapsed = time.monotonic() - started
    hits = [l for l in out.splitlines() if ": nothing" in l]
    assert len(hits) == MAX_MATCHES
    assert "stopped early" in out or "stopped after" in out
    assert f"{MAX_MATCHES} matches" in out
    assert elapsed < 60  # never "forever"; the old walk read all 20k files


def test_list_files_stops_early_and_says_why(tmp_path):
    big_tree(tmp_path / "proj", 20_000)
    tb = Toolbox(tmp_path / "proj")
    out, _, _ = tb.prepare("list_files", {}).apply()
    assert "stopped early" in out
    listed = [l for l in out.splitlines() if l and not l.startswith("...")]
    assert len(listed) == MAX_LISTED


def test_search_skips_files_over_1mb(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("needle\n" + "x" * 1_500_000)
    (tmp_path / "small.txt").write_text("needle in the small file\n")
    tb = Toolbox(tmp_path)
    out, _, _ = tb.prepare("search", {"pattern": "needle"}).apply()
    assert "small.txt" in out
    assert "big.txt" not in out


def test_search_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("secret/\n*.log\n!keep.log\n")
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "a.txt").write_text("needle\n")
    (tmp_path / "debug.log").write_text("needle\n")
    (tmp_path / "keep.log").write_text("needle\n")
    (tmp_path / "src.py").write_text("needle\n")
    tb = Toolbox(tmp_path)
    out, _, _ = tb.prepare("search", {"pattern": "needle"}).apply()
    files = {l.split(":")[0] for l in out.splitlines()}
    assert "src.py" in files and "keep.log" in files
    assert "secret/a.txt" not in files and "debug.log" not in files


def test_gitignore_dir_only_and_nested(tmp_path):
    (tmp_path / ".gitignore").write_text("build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.txt").write_text("needle\n")
    (tmp_path / "build.sh").write_text("needle\n")  # dir-only pattern must not match a file
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / ".gitignore").write_text("local.txt\n")
    (sub / "local.txt").write_text("needle\n")
    (sub / "shared.txt").write_text("needle\n")
    tb = Toolbox(tmp_path)
    out, _, _ = tb.prepare("search", {"pattern": "needle"}).apply()
    files = {l.split(":")[0] for l in out.splitlines()}
    assert "build.sh" in files and "pkg/shared.txt" in files
    assert "build/gen.txt" not in files and "pkg/local.txt" not in files


def test_match_one():
    # an anchored pattern (has a slash) matches the path from the .gitignore's folder only
    assert _match_one("c.txt", "", "/c.txt")
    assert not _match_one("a/c.txt", "", "/c.txt")
    assert _match_one("docs/anything/deep.txt", "", "docs/**")
    assert _match_one("build/gen.txt", "", "build")
    assert _match_one("build/gen.txt", "", "build/")
    assert not _match_one("build.sh", "", "build/")
    assert _match_one("pkg/local.txt", "pkg", "local.txt")
    assert not _match_one("pkg/shared.txt", "pkg", "local.txt")
    assert _match_one("deep/nested/x.log", "", "*.log")


def test_ignore_unit(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n/tmp/\n!x.log\ndocs/**\n")
    ig = Ignore(tmp_path)
    assert ig.ignored("a/b.log", False)
    assert ig.ignored("a.log", False)
    assert not ig.ignored("x.log", False)   # negated
    assert ig.ignored("tmp", True)          # anchored dir
    assert not ig.ignored("a/tmp", True)
    assert ig.ignored("docs/anything/deep.txt", False)


def test_cancel_event_stops_the_walk(tmp_path):
    big_tree(tmp_path / "proj", 20_000)
    cancel = threading.Event()
    tb = Toolbox(tmp_path / "proj", cancel=cancel)
    tb._search_rg = lambda *a, **k: None  # force the Python walk
    plan = tb.prepare("search", {"pattern": "needle"})

    original = tb._check_cancel
    calls = [0]

    def set_on_first_check():
        calls[0] += 1
        cancel.set()  # cancel as soon as the walk starts; deterministic, no sleep race
        original()

    tb._check_cancel = set_on_first_check
    with pytest.raises(Cancelled):
        plan.apply()
    assert calls[0] >= 1


def test_already_set_cancel_event_stops_immediately(tmp_path):
    (tmp_path / "a.txt").write_text("needle\n")
    cancel = threading.Event()
    cancel.set()
    tb = Toolbox(tmp_path, cancel=cancel)
    with pytest.raises(Cancelled):
        tb.prepare("list_files", {}).apply()


def test_home_and_root_detection(tmp_path):
    assert is_home_or_root(Path("/")) == "root"
    assert is_home_or_root(Path.home()) == "home"
    assert is_home_or_root(tmp_path) == ""
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert is_home_or_root(nested) == ""


def test_home_warning_on_start(tmp_path, monkeypatch):
    """Starting rooks in the home folder prints the one-line warning before the agent is built."""
    from rooks_code import cli
    import rooks_code.agent as agent_mod

    printed = []

    class FakeUI:
        def __init__(self, *a, **kw): pass
        def warn(self, text): printed.append(text)
        def error(self, text): printed.append(text)
        def p(self, *a, **kw): pass

    class FakeAgent:
        def __init__(self, s, ui): raise SystemExit(0)  # stop before any network/MCP

    monkeypatch.setattr(cli, "UI", FakeUI)
    monkeypatch.setattr(agent_mod, "Agent", FakeAgent)
    monkeypatch.setattr(cli, "Agent", FakeAgent, raising=False)
    monkeypatch.setattr(sys, "argv", ["rooks", "--no-mcp", "--no-color", "-P", "ollama", "-m", "x", "hi"])
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main()
    assert any("home folder" in t and "rooks -C" in t for t in printed)


def test_no_home_warning_in_a_project_folder(tmp_path, monkeypatch):
    from rooks_code import cli
    import rooks_code.agent as agent_mod

    printed = []

    class FakeUI:
        def __init__(self, *a, **kw): pass
        def warn(self, text): printed.append(text)
        def error(self, text): printed.append(text)
        def p(self, *a, **kw): pass

    class FakeAgent:
        def __init__(self, s, ui): raise SystemExit(0)

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(cli, "UI", FakeUI)
    monkeypatch.setattr(agent_mod, "Agent", FakeAgent)
    monkeypatch.setattr(sys, "argv", ["rooks", "--no-mcp", "--no-color", "-P", "ollama", "-m", "x", "hi"])
    monkeypatch.chdir(proj)
    with pytest.raises(SystemExit):
        cli.main()
    assert not any("home folder" in t for t in printed)
