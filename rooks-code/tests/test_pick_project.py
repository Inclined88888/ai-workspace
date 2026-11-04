"""rooks started in the home folder: pick a project by number or path; a wrong answer asks again instead of quitting."""

from pathlib import Path

from rooks_code import cli


def run(home: Path, answers: list[str], monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    said, it = [], iter(answers)
    picked = cli.pick_project(home, ask=lambda _p: next(it), say=said.append)
    return picked, "\n".join(said)


def make(home: Path) -> tuple[Path, Path]:
    a = home / "code" / "alpha"
    b = home / "beta"
    for p, mark in ((a, ".git"), (b, "pyproject.toml")):
        p.mkdir(parents=True)
        (p / mark).mkdir() if mark == ".git" else (p / mark).write_text("")
    (home / "Documents" / "secret").mkdir(parents=True)
    (home / "Documents" / "secret" / ".git").mkdir()
    return a, b


def test_projects_are_listed_and_picked_by_number(tmp_path, monkeypatch):
    a, b = make(tmp_path)
    picked, out = run(tmp_path, ["2"], monkeypatch)
    assert "~/code/alpha" in out and "~/beta" in out and "Documents" not in out  # no macOS permission prompt
    assert picked in (a, b)


def test_the_bug_typing_1_twice_no_longer_quits(tmp_path, monkeypatch):
    a, _ = make(tmp_path)
    picked, out = run(tmp_path, ["7", "nope", "~/code/alpha"], monkeypatch)  # out of range, bad path, then a good path
    assert picked == a and out.count("is not a folder") == 2


def test_enter_stays_and_q_quits(tmp_path, monkeypatch):
    make(tmp_path)
    assert run(tmp_path, [""], monkeypatch)[0] == tmp_path
    assert run(tmp_path, ["q"], monkeypatch)[0] is None


def test_projects_one_level_down_are_found_and_backups_skipped(tmp_path, monkeypatch):
    wt = tmp_path / "aiw-wt" / "finish"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere")  # a git worktree marks itself with a .git file
    old = tmp_path / "ai-workspace-data-backup-1"
    (old / ".rooks").mkdir(parents=True)
    picked, out = run(tmp_path, ["1"], monkeypatch)
    assert picked == wt and "backup" not in out


def test_first_run_offers_to_open_the_app_and_links_to_it(tmp_path, monkeypatch, capsys):
    calls, links = [], iter([{}, {}, {"url": "http://127.0.0.1:1", "key": "k"}])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/x/ai-workspace" if name == "ai-workspace" else None)
    monkeypatch.setattr(cli.config, "app_link", lambda: next(links))
    models = iter([False, False, True])  # the user connects a model in the app window meanwhile
    monkeypatch.setattr(cli.config, "app_has_models", lambda link: next(models))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda _p="": "")  # Enter = the recommended option
    assert cli.first_run_setup(tmp_path) is True
    assert calls == [["ai-workspace"]]
    out = capsys.readouterr().out
    assert "Connect a model in its window" in out and "Linked to AI Workspace" in out
