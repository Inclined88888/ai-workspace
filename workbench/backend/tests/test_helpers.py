"""The Files page's optional helper apps: says plainly what is missing, one Install button, failures are skipped."""

import subprocess


def test_missing_helpers_are_named_plainly_and_one_button_installs_them(client, monkeypatch):
    from app import helpers

    installed: set[str] = set()
    ran: list[list[str]] = []
    monkeypatch.setattr(helpers, "present", lambda k: k in installed)
    monkeypatch.setattr(helpers, "_system", lambda: "Darwin")
    monkeypatch.setattr(helpers.shutil, "which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)

    def fake_run(cmd, **kw):
        ran.append(cmd)
        if cmd[-1] == "ffmpeg":  # one helper fails: skipped, the others still install
            return subprocess.CompletedProcess(cmd, 1, "", "network down")
        installed.add(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(helpers.subprocess, "run", fake_run)
    s = client.get("/api/files/helpers").json()
    assert [m["label"] for m in s["missing"]] == ["Tesseract", "FFmpeg", "LibreOffice"] and s["can_install"]
    assert client.get("/api/files/capabilities").json()["helpers"]["missing"]
    helpers.install(wait=True)
    assert ["brew", "install", "--cask", "--quiet", "libreoffice"] in ran
    s = client.get("/api/files/helpers").json()
    assert [m["key"] for m in s["missing"]] == ["ffmpeg"] and not s["installing"]
    assert "FFmpeg could not be installed; skipped." in s["log"]


def test_without_brew_or_winget_it_says_how_instead_of_failing(client, monkeypatch):
    from app import helpers

    monkeypatch.setattr(helpers, "present", lambda k: False)
    monkeypatch.setattr(helpers, "_system", lambda: "Linux")
    r = client.post("/api/files/helpers/install").json()
    assert not r["installing"] and "sudo apt install tesseract-ocr ffmpeg libreoffice" in r["error"]
