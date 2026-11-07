"""Professional workspaces from the terminal, over real processes: `rooks work` lists and chooses the same workspaces the
app's Your work page shows, and the terminal's own loop gets the chosen working style."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import rooks
from fakes import FakeModel
from live_app import app, setup  # noqa: F401 - app is a fixture
from rooks_code import config
from rooks_code.learning import Learning

pytest.importorskip("uvicorn")


def test_rooks_work_lists_and_chooses_the_same_workspaces_as_the_app(project, app, monkeypatch):  # noqa: F811
    model = FakeModel([])
    try:
        key = setup(app, model.url)
        env = {"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": key}
        r = rooks(project, "work", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        for wid in ("general", "trade", "legal", "study"):
            assert f" {wid} " in r.stdout
        assert "* general" in r.stdout and "in use" in r.stdout

        r = rooks(project, "work", "use", "study", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Now working as Study and research" in r.stdout and "tools are added" in r.stdout
        assert app.call("GET", "/api/workspaces/active")["id"] == "study"  # the app sees the terminal's choice
        assert {p["id"]: p for p in app.call("GET", "/api/packs")}["study"]["installed"] is True

        show = rooks(project, "work", "show", env=env)
        assert show.returncode == 0 and "Study and research (study)" in show.stdout and "Tools: added" in show.stdout

        s = config.Settings(root=project)
        s.provider, s.kind, s.base_url, s.model, s.key = "workbench", "openai", app.url + "/v1", "default", key
        assert "Professional workspace: Study and research." in Learning(s).context("help me revise")

        from types import SimpleNamespace

        from rooks_code import tui

        for k, v in env.items():
            monkeypatch.setenv(k, v)
        said: list[str] = []
        ui = SimpleNamespace(p=said.append)
        tui.command(SimpleNamespace(root=project, s=None), ui, "/work", "show")  # /work inside a session: same code
        assert "Study and research (study)" in said[-1]
        tui.command(SimpleNamespace(root=project, s=None), ui, "/work", "use general")
        assert "Now working as General" in said[-1] and app.call("GET", "/api/workspaces/active")["id"] == "general"
        assert "/work" in tui.COMMANDS
        tui.command(SimpleNamespace(root=project, s=None), ui, "/work", "use study")

        missing = rooks(project, "work", "use", "astronaut", env=env)
        assert missing.returncode == 1 and missing.stderr.startswith("rooks: ")
        assert app.call("GET", "/api/workspaces/active")["id"] == "study"
        assert rooks(project, "work", "use", env=env).returncode == 2  # needs an id
    finally:
        model.close()


def test_an_upgrade_answer_prints_its_plain_message(project, monkeypatch):
    """The app answers 402 with an upgrade card ({"error", "message"}); the terminal prints the message, not the dict."""

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.dumps({"detail": {"error": "upgrade_required", "message": "Trade is part of Pro."}}).encode()
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env = {"WORKBENCH_URL": f"http://127.0.0.1:{srv.server_port}", "WORKBENCH_API_KEY": "k" * 32}
        r = rooks(project, "work", "use", "trade", env=env)
        assert r.returncode == 1
        assert r.stderr.strip() == "rooks: Trade is part of Pro.", r.stderr
    finally:
        srv.shutdown()
