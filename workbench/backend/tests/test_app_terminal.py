"""#7: the app's own terminal. A real login shell on a pseudo terminal over a WebSocket, only for a signed-in page of the
same origin on this machine, without the app's secrets in its environment, and the whole session ends with the tab."""

import json
import os
import sys
import time

import pytest
from starlette.websockets import WebSocketDisconnect

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo terminals only")
WS = "/api/terminal/ws"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def local(monkeypatch):
    from app import terminal_pty

    monkeypatch.setattr(terminal_pty, "_is_local", lambda ws: True)  # TestClient's peer is "testclient", not loopback
    monkeypatch.setenv("SHELL", "/bin/sh")
    return terminal_pty


def read_until(ws, needle: str, limit: int = 200) -> str:
    seen = ""
    for _ in range(limit):
        seen += ws.receive_text()
        if needle in seen:
            return seen
    raise AssertionError(f"never saw {needle!r} in {seen[-500:]!r}")


def test_a_signed_in_page_gets_a_working_shell_without_the_apps_secrets(client, local, monkeypatch):
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", "sk-should-not-leak")
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.send_text(json.dumps({"type": "resize", "cols": 100, "rows": 30}))
        ws.send_text(json.dumps({"type": "input", "data": "echo sum-$((40+2)) size-$(stty size | tr ' ' x)\n"}))
        read_until(ws, "sum-42 size-30x100")  # the shell ran it, on a pty sized by the page
        ws.send_text(json.dumps({"type": "input", "data": "echo [${APP_SECRET}][${TOOLS_API_KEY}][${SOME_PROVIDER_API_KEY}][$AI_WORKSPACE_TERMINAL]\n"}))
        out = read_until(ws, "][1]")
        assert "[][][][1]" in out  # secrets stripped; the marker set
        assert "sk-should-not-leak" not in out.replace("${SOME_PROVIDER_API_KEY}", "")


def test_closing_the_tab_ends_the_shell_and_what_it_started(client, local, tmp_path):
    pidfile = tmp_path / "child.pid"
    with client.websocket_connect(WS, headers=ORIGIN) as ws:
        ws.send_text(json.dumps({"type": "input", "data": f"sleep 300 & echo $! > {pidfile}; echo started-$((0+1))bg\n"}))
        read_until(ws, "started-1bg")
        assert local._open
    for _ in range(50):
        if not local._open:
            break
        time.sleep(0.1)
    assert not local._open
    pid = int(pidfile.read_text())
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the background job outlived the terminal tab")


@pytest.mark.parametrize("case", ["signed_out", "other_origin", "remote", "switched_off"])
def test_who_may_not_open_a_terminal(client, monkeypatch, case):
    from app import db, terminal_pty

    monkeypatch.setattr(terminal_pty, "_is_local", lambda ws: case != "remote")
    headers = {"origin": "https://evil.example"} if case == "other_origin" else ORIGIN
    if case == "signed_out":
        monkeypatch.setattr(terminal_pty, "check_session", lambda cookie: False)
    if case == "switched_off":
        assert client.put("/api/settings", json={"app_terminal": False}).json()["app_terminal"] is False
    try:
        with client.websocket_connect(WS, headers=headers) as ws:
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
        assert e.value.code in (4401, 4403)
        assert not terminal_pty._open
    finally:
        db.set_setting("app_terminal", "1")


def test_status_needs_a_session(client):
    assert client.get("/api/terminal/status").json()["enabled"] is True
    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        assert client.get("/api/terminal/status").status_code == 401
    finally:
        client.cookies.update(saved)


def test_local_means_a_loopback_peer_and_a_loopback_page():
    from types import SimpleNamespace as NS

    from app.terminal_pty import _is_local

    def ws(peer, origin):
        return NS(client=NS(host=peer), headers={"origin": origin})

    assert _is_local(ws("127.0.0.1", "http://127.0.0.1:8123"))
    assert _is_local(ws("::1", "http://localhost:5173"))
    assert not _is_local(ws("192.168.1.20", "http://127.0.0.1:8123"))  # someone on the network
    assert not _is_local(ws("127.0.0.1", "http://rebind.evil.example:8123"))  # DNS rebinding to loopback
    assert not _is_local(ws("127.0.0.1", ""))
