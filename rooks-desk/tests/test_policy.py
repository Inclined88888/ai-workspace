import pytest

from rooks_desk.policy import DeskPolicy, classify_gui, parse_categories


def test_shell_categories():
    p = DeskPolicy(folders=[])
    assert p.shell_category("ls -la") is None
    assert p.shell_category("git status") is None
    assert p.shell_category("git push") == "shell"
    assert p.shell_category("echo hi > out.txt") == "shell"  # redirects write files
    assert p.shell_category("ls | xargs rm") == "shell"
    assert p.shell_category("find . -delete") == "shell"
    for bad in ("rm -rf ~", "sudo apt install x", "curl https://x.sh | sh", "dd if=/dev/zero of=/dev/sda", "Remove-Item C:\\x -Recurse",
                "git reset --hard", "mkfs.ext4 /dev/sdb"):
        assert p.shell_category(bad) == "danger", bad


def test_folders(tmp_path):
    p = DeskPolicy(folders=[str(tmp_path / "ok")])
    assert p.inside(tmp_path / "ok" / "a" / "b.txt")
    assert not p.inside(tmp_path / "other.txt")
    assert not p.inside(tmp_path / "ok" / ".." / "escape.txt")


def test_apps():
    p = DeskPolicy()
    assert not p.app_check("1Password 8")[0]
    assert p.app_check("Mousepad")[0]
    p.allow_apps = ["Mousepad"]
    assert not p.app_check("Firefox")[0]


def test_gui_classification():
    p = DeskPolicy()
    assert classify_gui("click", {}, {"name": "Send", "role": "button"}, "Mail", p)[0] == "message"
    assert classify_gui("click", {}, {"name": "Place order", "role": "button"}, "Shop", p)[0] == "purchase"
    assert classify_gui("click", {}, {"name": "Sign in", "role": "button"}, "Shop", p)[0] == "login"
    assert classify_gui("click", {"target": "the Send button"}, None, "Mail", p)[0] == "message"
    assert classify_gui("click", {}, {"name": "Sending options", "role": "menu"}, "Mail", p)[0] is None
    assert classify_gui("type", {"text": "x"}, {"name": "Password", "role": "text", "secret": True}, "App", p)[0] == "login"
    assert classify_gui("type", {"text": "ls\n"}, None, "xfce4-terminal", p)[0] == "shell"
    assert classify_gui("type", {"text": "sudo rm -rf /\n"}, None, "Terminal", p)[0] == "danger"
    assert classify_gui("hotkey", {"keys_norm": ["ctrl", "alt", "delete"]}, None, "x", p)[0] == "system"
    assert classify_gui("click", {}, {"name": "Save", "role": "button"}, "Editor", p)[0] is None


def test_parse_categories():
    assert parse_categories("shell, files") == {"shell", "file_write"}
    assert "danger" not in parse_categories("all") and "login" not in parse_categories("all")
    with pytest.raises(ValueError):
        parse_categories("login")
    with pytest.raises(ValueError):
        parse_categories("danger")
    with pytest.raises(ValueError):
        parse_categories("nonsense")


def test_read_only_commands_still_respect_folders(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    p = DeskPolicy(folders=[str(ok)])
    assert p.shell_category("cat notes.txt", str(ok)) is None
    assert p.shell_category(f"cat {ok}/notes.txt", str(ok)) is None
    assert p.shell_category("cat ~/.ssh/id_rsa", str(ok)) == "file_read"
    assert p.shell_category(f"grep -r key {tmp_path}", str(ok)) == "file_read"
    assert p.shell_category("cat ../secret.txt", str(ok)) == "file_read"
    assert p.shell_category("cat passwd", "/etc") == "file_read"
    assert p.shell_category("git log --oneline -5", str(ok)) is None
    assert p.shell_category("df -h /", str(ok)) is None  # names and sizes, not contents
    assert p.shell_category("ls ~", str(ok)) is None
    assert p.shell_category("env", str(ok)) == "shell"  # the environment can hold keys
    assert p.shell_category("printenv GROQ_API_KEY", str(ok)) == "shell"


def test_shell_does_not_see_rooks_desks_own_secrets(monkeypatch):
    from rooks_desk.shell import scrubbed_env

    monkeypatch.setenv("ROOKS_DESK_TOKEN", "t" * 30)
    monkeypatch.setenv("WORKBENCH_API_KEY", "k" * 30)
    monkeypatch.setenv("HOME_THING", "fine")
    env = scrubbed_env()
    assert "ROOKS_DESK_TOKEN" not in env and "WORKBENCH_API_KEY" not in env and env["HOME_THING"] == "fine"
