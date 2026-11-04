"""B5 egress audit from the terminal, over real processes: the app's model call is recorded by egress.py and
`rooks egress` reports it per host, local vs external, with the same numbers as the app's own /api/egress."""

import json

import pytest

from conftest import rooks
from fakes import FakeModel
from live_app import app, openai_reply, setup  # noqa: F401 - app is a fixture

pytest.importorskip("uvicorn")


def test_rooks_egress_reports_what_the_app_sent_and_matches_the_app(project, app):  # noqa: F811
    model = FakeModel([openai_reply("hi")])
    try:
        token = setup(app, model.url)
        app.call_sse("/api/assistant/chat", {"message": "hello"})  # one model call to the fake provider on 127.0.0.1
        env = {"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": token}

        r = rooks(project, "egress", "--json", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        term = json.loads(r.stdout)
        web = app.call("GET", "/api/egress?hours=24")
        assert term["hosts"] == web["hosts"] and term["local_only"] == web["local_only"] is True
        host = next(h for h in term["hosts"] if h["host"].startswith("127.0.0.1"))
        assert host["effect"] == "local" and host["requests"] >= 1

        r = rooks(project, "egress", env=env)
        assert r.returncode == 0 and "local only: nothing left this machine" in r.stdout and "127.0.0.1" in r.stdout

        assert rooks(project, "egress", "--hours", "0", env=env).returncode == 2  # argparse refuses out-of-range windows
        bad = rooks(project, "egress", env={"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": "wrong"})
        assert bad.returncode == 1 and "Terminal access token" in bad.stderr
    finally:
        model.close()
