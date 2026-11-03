"""ai-workspace --update: channels persist per install, stable only takes vX.Y.Z tags, no network on a plain launch."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("aiw_update", ROOT / "desktop" / "launcher" / "update.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)


def _fetch(tag):
    calls = []

    def f(url):
        calls.append(url)
        return {"tag_name": tag}
    f.calls = calls
    return f


def test_versions_order_prerelease_before_release():
    assert up.newer("v0.1.0", "0.1.0-rc1")
    assert up.newer("v0.2.0", "0.1.9")
    assert not up.newer("v0.1.0", "0.1.0")
    assert not up.newer("v0.1.0", "dev")


def test_stable_only_accepts_plain_semver_tags():
    assert up.latest_stable(fetch=_fetch("v0.1.0")) == "v0.1.0"
    for bad in ("v0.1.0-rc1", "desktop-v0.1.0", "v2026.7.20", ""):
        assert up.latest_stable(fetch=_fetch(bad)) is None


def test_channel_persists_and_survives_other_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    (tmp_path / "install.json").write_text(json.dumps({"src": "/x"}))
    assert up.get_channel() == "stable"
    up.set_channel("edge")
    d = json.loads((tmp_path / "install.json").read_text())
    assert d == {"src": "/x", "channel": "edge"}
    assert up.get_channel() == "edge"


def test_update_runs_installer_at_the_stable_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    ran = []
    rc = up.run_update("0.1.0-rc1", fetch=_fetch("v0.1.0"), runner=lambda c: ran.append(c) or 0, out=lambda *_: None)
    assert rc == 0 and len(ran) == 1
    assert "/v0.1.0/install.sh" in ran[0][-1] and "--no-start" in ran[0][-1]


def test_up_to_date_and_check_only_do_not_install(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    ran = []
    assert up.run_update("0.1.0", fetch=_fetch("v0.1.0"), runner=ran.append, out=lambda *_: None) == 0
    assert up.run_update("0.0.9", fetch=_fetch("v0.1.0"), check_only=True, runner=ran.append, out=lambda *_: None) == 0
    assert ran == []


def test_no_release_yet_is_reported_not_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    ran = []
    assert up.run_update("0.1.0-rc1", fetch=_fetch(""), runner=ran.append, out=lambda *_: None) == 1
    assert ran == []


def test_edge_follows_main_and_saves_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    ran = []
    f = _fetch("v9.9.9")
    up.run_update("0.1.0", channel="edge", fetch=f, runner=lambda c: ran.append(c) or 0, out=lambda *_: None)
    assert "/main/install.sh" in ran[0][-1] and f.calls == []
    assert up.get_channel() == "edge"


def test_windows_uses_install_ps1():
    cmd = up.install_command("v0.1.0", platform="win32")
    assert cmd[0] == "powershell" and "/v0.1.0/install.ps1" in cmd[-1]


def test_launch_notice_reads_cache_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_HOME", str(tmp_path))
    assert up.cached_notice("0.1.0") is None
    up.check("0.1.0", fetch=_fetch("v0.2.0"))
    monkeypatch.setattr(up, "_get_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert "v0.2.0" in up.cached_notice("0.1.0")
    assert up.cached_notice("0.2.0") is None


def test_launcher_exposes_update_flags():
    src = (ROOT / "desktop" / "launcher" / "ai_workspace.py").read_text()
    assert "--update" in src and "--channel" in src and "cached_notice" in src
