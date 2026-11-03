"""feat/mcp-client: Workbench as an MCP client.

A tiny in-test stdio MCP server (Python, one JSON-RPC message per line) and a tiny in-test HTTP MCP server
(Streamable HTTP and legacy SSE on uvicorn) stand in for real servers; nothing touches the network.
"""

import json
import os
import socket
import sys
import threading
import time

import pytest

from conftest import events, tool_call

STDIO_SERVER = r'''
import json, sys

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

print("fake-mcp banner, not json")  # servers that print banners must not break the client
sys.stdout.flush()
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    mid, method = msg.get("id"), msg.get("method", "")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {},
              "serverInfo": {"name": "fake-stdio", "version": "1.0"}}})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "Echo the text back", "annotations": {"readOnlyHint": True}, "inputSchema":
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
            {"name": "explode", "description": "Always fails", "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        args = msg["params"].get("arguments", {})
        if msg["params"].get("name") == "explode":
            send({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "echo:" + args.get("text", "")}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})
'''

TOOLS = {"tools": [{"name": "echo", "description": "Echo the text back", "inputSchema":
                    {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}


def _result(method, msg):
    if method == "initialize":
        return {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake-http", "version": "1.0"}}
    if method == "tools/list":
        return TOOLS
    if method == "tools/call":
        return {"content": [{"type": "text", "text": "echo:" + msg["params"].get("arguments", {}).get("text", "")}]}
    return {}


def _sse(msgs):
    return "".join(f"event: message\ndata: {json.dumps(m)}\n\n" for m in msgs)


def make_http_app(mode):
    """mode 'json': Streamable HTTP answering JSON; 'sse-answer': Streamable HTTP answering an SSE stream;
    mode 'legacy': the 2024-11-05 SSE transport (GET stream + endpoint event, POST per message)."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    app = FastAPI()
    if mode == "legacy":
        import queue

        bus: queue.Queue = queue.Queue()

        @app.get("/mcp")
        def stream():
            def gen():
                yield "event: endpoint\ndata: /mcp/messages\n\n"
                while True:
                    try:
                        yield bus.get(timeout=30)
                    except queue.Empty:
                        return

            return StreamingResponse(gen(), media_type="text/event-stream")

        @app.post("/mcp/messages")
        async def messages(request: Request):
            msg = await request.json()
            if "id" not in msg:  # a notification: accepted, no answer
                return Response(status_code=202)
            bus.put(_sse([{"jsonrpc": "2.0", "id": msg["id"], "result": _result(msg["method"], msg)}]))
            return Response(status_code=202)
        return app

    @app.post("/mcp")
    async def handle(request: Request):
        msg = await request.json()
        if "id" not in msg:
            return Response(status_code=202)
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": _result(msg["method"], msg)}
        if mode == "sse-answer":
            return Response(_sse([reply]), media_type="text/event-stream",
                            headers={"mcp-session-id": "sess-1"} if msg["method"] == "initialize" else {})
        return JSONResponse(reply, headers={"mcp-session-id": "sess-1"} if msg["method"] == "initialize" else {})

    return app


@pytest.fixture(scope="module")
def http_mcp():
    """One uvicorn serving /mcp in each of the three wire shapes, on its own ports."""
    import uvicorn

    urls, servers = {}, []
    for mode in ("json", "sse-answer", "legacy"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        server = uvicorn.Server(uvicorn.Config(make_http_app(mode), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        urls[mode] = (f"http://127.0.0.1:{port}/mcp", port)
    for _ in range(50):
        try:
            for _, port in urls.values():
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {mode: u for mode, (u, _) in urls.items()}
    for server in servers:
        server.should_exit = True


@pytest.fixture()
def stdio_script(tmp_path):
    p = tmp_path / "fake_mcp.py"
    p.write_text(STDIO_SERVER)
    return str(p)


def _add(client, name, **kw):
    r = client.post("/api/mcp/servers", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _del(client, sid):
    assert client.delete(f"/api/mcp/servers/{sid}").status_code == 200


def test_stdio_server_lists_and_calls_tools(client, stdio_script):
    from app import mcp_client

    srv = _add(client, "fakestdio", command=sys.executable, args=[stdio_script])
    try:
        tools = client.get("/api/mcp/tools").json()["servers"]
        mine = next(s for s in tools if s["name"] == "fakestdio")
        assert mine["status"] == "connected"
        assert mine["server_info"] == {"name": "fake-stdio", "version": "1.0"}
        assert [t["qualified"] for t in mine["tools"]] == ["mcp__fakestdio__echo", "mcp__fakestdio__explode"]

        ok, text = mcp_client.call("mcp__fakestdio__echo", {"text": "hi there"})
        assert ok and text == "echo:hi there"
        ok, text = mcp_client.call("mcp__fakestdio__explode", {})
        assert not ok and "boom" in text
    finally:
        _del(client, srv["id"])
        mcp_client.restart_all()


def test_mcp_tool_runs_in_the_assistant_loop_and_taints(client, stdio_script):
    from app import assistant

    srv = _add(client, "fakeloop", command=sys.executable, args=[stdio_script])
    try:
        ctx = assistant.Ctx(channel="web", conversation_id=1)
        names = [t.name for t in assistant.available_tools(ctx)]
        assert "mcp__fakeloop__echo" in names

        from conftest import SCRIPT
        SCRIPT[:] = [tool_call("mcp__fakeloop__echo", {"text": "from the model"}),
                     {"role": "assistant", "content": "The echo tool said it back."}]
        r = client.post("/api/assistant/chat", json={"message": "run the echo tool with 'from the model'"})
        assert r.status_code == 200
        evs = events(r)
        assert any("echo:from the model" in json.dumps(e) for e in evs), json.dumps(evs)[-2000:]
        # The tool output must reach the model marked untrusted (tainting the turn).
        from conftest import CALLS
        tool_msgs = [m for c in CALLS for m in c["messages"] if m["role"] == "tool"]
        assert any("Untrusted content" in m["content"] and "echo:from the model" in m["content"] for m in tool_msgs)
    finally:
        SCRIPT[:] = []
        _del(client, srv["id"])


def test_mcp_tools_without_read_only_hint_wait_for_approval(client, stdio_script):
    from app import assistant

    srv = _add(client, "fakegate", command=sys.executable, args=[stdio_script])
    try:
        ctx = assistant.Ctx(channel="web", conversation_id=1)
        tools = {t.name: t for t in assistant.available_tools(ctx)}
        assert assistant.hold_reason(tools["mcp__fakegate__echo"], ctx, {"text": "x"}) == ""
        assert assistant.hold_reason(tools["mcp__fakegate__explode"], ctx, {}) == assistant.MCP_ASK_REASON
        assert tools["mcp__fakegate__explode"].effect == "external"
    finally:
        _del(client, srv["id"])


def test_http_transports(client, http_mcp):
    from app import mcp_client

    ids = []
    try:
        for mode, transport in (("json", "streamable_http"), ("sse-answer", "streamable_http"), ("legacy", "sse")):
            srv = _add(client, f"fake{mode.replace('-', '')}", transport=transport, url=http_mcp[mode])
            ids.append(srv["id"])
            r = client.post(f"/api/mcp/servers/{srv['id']}/test")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "connected", (mode, r.json())
            assert r.json()["tools"][0]["name"] == "echo"
            ok, text = mcp_client.call(f"mcp__fake{mode.replace('-', '')}__echo", {"text": mode})
            assert ok and text == f"echo:{mode}"
    finally:
        for sid in ids:
            _del(client, sid)
        mcp_client.restart_all()


def test_http_egress_is_recorded(client, http_mcp):
    srv = _add(client, "egressmcp", transport="streamable_http", url=http_mcp["json"])
    try:
        assert client.post(f"/api/mcp/servers/{srv['id']}/test").json()["status"] == "connected"
        rows = client.get("/api/egress").json()["recent"]
        assert any(r["component"] == "mcp_client" and r["effect"] == "local" for r in rows)
    finally:
        _del(client, srv["id"])


def test_secrets_are_stored_encrypted_and_never_returned(client, stdio_script):
    srv = _add(client, "secretmcp", command=sys.executable, args=[stdio_script],
               env={"VISIBLE": "yes", "API_TOKEN": ""}, secret_env=["API_TOKEN"],
               secrets={"env": {"API_TOKEN": "sk-secret-value-123"}})
    try:
        listed = next(s for s in client.get("/api/mcp/servers").json()["servers"] if s["name"] == "secretmcp")
        assert listed["spec"]["env"] == {"VISIBLE": "yes"}
        assert listed["secrets"]["env"] == ["API_TOKEN"]
        assert listed["needs_secrets"] == {"env": [], "headers": []}
        assert "sk-secret-value-123" not in json.dumps(listed)

        from app import db, security
        row = db.one("SELECT * FROM mcp_servers WHERE id = ?", srv["id"])
        assert "sk-secret-value-123" not in row["spec_json"]
        assert json.loads(security.decrypt(row["secret_enc"]))["env"]["API_TOKEN"] == "sk-secret-value-123"

        # Updating without re-sending the secret keeps it; the resolved spec carries it into the process env.
        client.put(f"/api/mcp/servers/{srv['id']}", json={"name": "secretmcp", "command": sys.executable,
                                                          "args": [stdio_script], "env": {"VISIBLE": "yes", "API_TOKEN": ""},
                                                          "secret_env": ["API_TOKEN"]})
        from app import mcp_client
        row = db.one("SELECT * FROM mcp_servers WHERE id = ?", srv["id"])
        assert mcp_client._resolve(row)["env"]["API_TOKEN"] == "sk-secret-value-123"
    finally:
        _del(client, srv["id"])


def test_disable_remove_and_validation(client, stdio_script):
    srv = _add(client, "togglemcp", command=sys.executable, args=[stdio_script])
    client.get("/api/mcp/tools")  # connects
    r = client.post(f"/api/mcp/servers/{srv['id']}/toggle", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert all(s["name"] != "togglemcp" for s in client.get("/api/mcp/tools").json()["servers"])
    assert client.post(f"/api/mcp/servers/{srv['id']}/test").status_code == 400
    _del(client, srv["id"])
    assert all(s["name"] != "togglemcp" for s in client.get("/api/mcp/servers").json()["servers"])

    assert client.post("/api/mcp/servers", json={"name": "Bad Name!"}).status_code == 400
    assert client.post("/api/mcp/servers", json={"name": "nocommand"}).status_code == 400
    assert client.post("/api/mcp/servers", json={"name": "badurl", "transport": "sse", "url": "ftp://x"}).status_code == 400


def test_failed_server_reports_error_not_crash(client):
    srv = _add(client, "brokenmcp", command="definitely-not-a-real-command-xyz")
    try:
        mine = next(s for s in client.get("/api/mcp/tools").json()["servers"] if s["name"] == "brokenmcp")
        assert mine["status"] == "error" and "not found" in mine["last_error"]
        r = client.post(f"/api/mcp/servers/{srv['id']}/test")
        assert r.json()["status"] == "error" and "not found" in r.json()["error"]
    finally:
        _del(client, srv["id"])


def test_catalogue_lists_reference_servers(client):
    names = [c["name"] for c in client.get("/api/mcp/catalogue").json()["catalogue"]]
    assert names == ["filesystem", "github", "fetch", "memory", "sqlite", "playwright"]
    github = client.get("/api/mcp/catalogue").json()["catalogue"][1]
    assert github["command"] == "npx" and "GITHUB_PERSONAL_ACCESS_TOKEN" in github["secret_env"]
