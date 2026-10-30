"""The ai-workspace launcher (desktop/launcher/ai_workspace.py): the window gets a one-time sign-in link from the app
server (never the launch token, never a temporary file), and logs go to a known place."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ai_workspace_launcher", ROOT / "desktop" / "launcher" / "ai_workspace.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def _server(seen):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            seen["csrf"] = self.headers.get("X-Workbench")
            body = json.dumps({"code": "ONETIME", "expires_in": 120}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<!doctype html><div id="root"></div>')

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_window_gets_a_one_time_link_not_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_DATA_DIR", str(tmp_path))
    seen = {}
    srv = _server(seen)
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        link = launcher.handoff_url(url, "SECRET123")
        assert seen["auth"] == "Bearer SECRET123" and seen["csrf"] == "1"
        assert link == f"{url}/api/desktop/handoff?code=ONETIME"
        assert launcher.ui_ok(url)

        class FakePopen:
            def __init__(self, args, **kw):
                seen["args"] = args

        monkeypatch.setattr(launcher, "window_browser", lambda: "/usr/bin/chrome")
        monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
        launcher.open_window(link, tmp_path / "window")
        assert f"--app={link}" in seen["args"]
        assert "SECRET123" not in " ".join(seen["args"])
        assert not list(tmp_path.rglob("*.html"))  # no temporary page that could vanish before the browser reads it
    finally:
        srv.shutdown()


def test_launcher_writes_its_own_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "_LOG", [])
    launcher.say("hello from the launcher")
    launcher._LOG[0].close()
    assert "hello from the launcher" in (tmp_path / "logs" / "launcher.log").read_text()


def test_the_installer_shortcut_is_not_taken_for_the_native_app(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.Path, "home", lambda: tmp_path)
    shortcut = tmp_path / "Applications" / "AI Workspace.app" / "Contents" / "MacOS"
    shortcut.mkdir(parents=True)
    (shortcut / "ai-workspace").write_text("#!/bin/sh\n")
    if not Path("/Applications/AI Workspace.app").exists():
        assert launcher.native_app() is None
    (shortcut / "ai-workspace-desktop").write_text("")
    assert launcher.native_app() == ["open", "-a", str(tmp_path / "Applications" / "AI Workspace.app")]
