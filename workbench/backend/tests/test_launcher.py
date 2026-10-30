"""The ai-workspace launcher (desktop/launcher/ai_workspace.py): the sign-in token never goes on a command line."""

import importlib.util
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ai_workspace_launcher", ROOT / "desktop" / "launcher" / "ai_workspace.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_window_gets_a_private_forwarding_page_not_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_WORKSPACE_DATA_DIR", str(tmp_path))
    seen = {}

    class FakePopen:
        def __init__(self, args, **kw):
            seen["args"] = args

    monkeypatch.setattr(launcher, "window_browser", lambda: "/usr/bin/chrome")
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    launcher.open_window("http://127.0.0.1:1/api/desktop/session?token=SECRET123", tmp_path / "window")
    joined = " ".join(seen["args"])
    assert "SECRET123" not in joined
    app = next(a for a in seen["args"] if a.startswith("--app="))[6:]
    assert app.startswith("file:")
    page = Path(launcher.urllib.request.url2pathname(app[len("file:"):]))
    assert "SECRET123" in page.read_text()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(page).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(page.parent).st_mode) == 0o700
