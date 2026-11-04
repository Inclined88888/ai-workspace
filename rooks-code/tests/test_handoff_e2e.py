"""B4 hand-off, over real processes: a conversation started in the app continues in the rooks terminal with its history,
and the terminal's turn shows up back in the same app conversation. Codes are one-time and need the signed-in app."""

import pytest

from conftest import rooks
from fakes import FakeModel
from live_app import app, openai_reply, setup  # noqa: F401 - app is a fixture

pytest.importorskip("uvicorn")


def test_an_app_conversation_continues_in_the_terminal_and_comes_back(project, app):  # noqa: F811
    model = FakeModel([
        openai_reply("Noted: the deadline is Friday."),               # web turn
        lambda body: openai_reply("You said the deadline is Friday." if any(
            "deadline is Friday" in (m.get("content") or "") for m in body["messages"]) else "NO HISTORY"),  # terminal turn
    ])
    try:
        token = setup(app, model.url)
        ev = app.call_sse("/api/assistant/chat", {"message": "remember the deadline is Friday"})
        cid = next(e["conversation_id"] for e in ev if e.get("conversation_id"))

        code = app.call("POST", "/api/assistant/handoff", {"conversation_id": cid})
        assert code["command"] == f"rooks --handoff {code['code']}"

        env = {"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": token}
        r = rooks(project, "-P", "workbench", "--handoff", code["code"], "what is the deadline", env=env)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert f"Continuing app conversation #{cid}" in out
        assert "You said the deadline is Friday." in out  # the terminal turn saw the web history

        msgs = app.call("GET", f"/api/conversations/{cid}")["messages"]
        assert [m["content"] for m in msgs if m["role"] == "user"] == ["remember the deadline is Friday", "what is the deadline"]
        assert msgs[-1]["content"] == "You said the deadline is Friday."  # and the app shows the terminal's answer

        # one use only
        again = rooks(project, "-P", "workbench", "--handoff", code["code"], "again", env=env)
        assert again.returncode == 1 and "wrong, used or expired" in again.stdout + again.stderr
    finally:
        model.close()


def test_codes_need_the_signed_in_app_and_the_terminal_token(project, app):  # noqa: F811
    model = FakeModel([])
    try:
        token = setup(app, model.url)
        import urllib.error
        import urllib.request

        # the terminal token cannot mint codes (that needs the signed-in app)
        req = urllib.request.Request(app.url + "/api/assistant/handoff", method="POST", data=b'{"conversation_id": 1}',
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code in (401, 403)
        # claiming needs the terminal token
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(app.url + "/v1/rooks/handoff?code=x", timeout=10)
        assert e.value.code == 401
        # not on the workbench provider: refused, not silently a new local chat
        r = rooks(project, "-P", "ollama", "--handoff", "abc", "hi")
        assert r.returncode == 1 and "--provider workbench" in r.stdout + r.stderr
    finally:
        model.close()
