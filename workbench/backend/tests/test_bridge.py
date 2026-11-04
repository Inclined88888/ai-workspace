"""Item 103 core: OpenAI's own SDK, pointed at Workbench's /v1, reaches every backend through the adapter layer.

This is how the browser agent's browser-use library talks to models in the stack: structured output (strict json_schema)
and screenshots (image_url data URLs), with no provider key outside Workbench and every call in the run log.
"""

import json

import pytest
from conftest import NATIVE_CALLS, SCRIPT
from test_contract import BACKENDS, IDS, _connect  # noqa: F401 - shared backend definitions

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
SCHEMA = {"type": "object", "properties": {"next_goal": {"type": "string"}, "done": {"type": "boolean"}},
          "required": ["next_goal", "done"], "additionalProperties": False}
BRIDGE_KEY = "b" * 32


@pytest.fixture
def sdk(client, monkeypatch):
    from openai import OpenAI

    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    return OpenAI(base_url="http://testserver/v1", api_key=BRIDGE_KEY, http_client=client, max_retries=0)


@pytest.mark.parametrize("backend_def", [b for b in BACKENDS], ids=IDS)
def test_structured_output_with_a_screenshot_on_every_backend(client, fake_url, sdk, backend_def):
    from app import db

    auth_kind, protocol, path, secret = backend_def
    cid = _connect(client, fake_url, auth_kind, protocol, path, secret)
    NATIVE_CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": json.dumps({"next_goal": "click Search", "done": False})}]
    r = sdk.chat.completions.create(
        model=f"{cid}/vision-model",
        messages=[{"role": "system", "content": "You drive a browser."},
                  {"role": "user", "content": [{"type": "text", "text": "What next?"},
                                               {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}", "detail": "auto"}}]}],
        response_format={"type": "json_schema", "json_schema": {"name": "agent_output", "strict": True, "schema": SCHEMA}},
        temperature=0.2, frequency_penalty=0.3, max_completion_tokens=4096,
    )
    assert json.loads(r.choices[0].message.content) == {"next_goal": "click Search", "done": False}
    assert r.usage.prompt_tokens > 0
    if protocol != "chat_completions":  # the screenshot and the schema reached the provider in its own shape
        body = json.dumps(NATIVE_CALLS[-1]["body"])
        assert PNG in body
        marker = {"responses": '"text": {"format"', "anthropic_messages": '"tool_choice"', "ollama_native": '"format"'}[protocol]
        assert marker in body
    row = db.one("SELECT * FROM actions WHERE kind = 'model_call' ORDER BY id DESC")
    assert row["channel"] == "browser" and row["outcome"] == "ok" and row["model"] == f"{cid}/vision-model"
    db.run("DELETE FROM connections WHERE id = ?", cid)


def test_the_bridge_needs_its_key(client, monkeypatch):
    from app import config

    body = {"model": "default", "messages": [{"role": "user", "content": "hi"}]}
    monkeypatch.setattr(config, "BROWSER_API_KEY", "")
    assert client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer "}).status_code == 401
    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    assert client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    # the browser session cookie is not a bridge credential: a web page cannot drive /v1
    assert client.get("/v1/models").status_code == 401


def test_the_bridge_accepts_the_terminal_access_token(client, monkeypatch):
    """The wb_… token from Settings > Terminal access (setup.sh prints it for the rooks CLI) must drive /v1."""
    import hashlib

    from app import config, db

    monkeypatch.setattr(config, "BROWSER_API_KEY", "")
    monkeypatch.setattr(config, "DESK_API_KEY", "")
    token = "wb_" + "t" * 43
    db.set_setting("cli_token_hash", hashlib.sha256(token.encode()).hexdigest())
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    # a wrong wb_ token is refused
    bad = "wb_" + "x" * 43
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    # and the bridge keys still work alongside it
    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {BRIDGE_KEY}"}).status_code == 200


def test_models_and_default(client, sdk):
    ids = [m.id for m in sdk.models.list().data]
    assert f"{client.pid}/fake-model" in ids
    SCRIPT[:] = [{"role": "assistant", "content": "plain answer"}]
    assert sdk.chat.completions.create(model="default", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "plain answer"


def test_streaming_text_and_tool_calls(client, sdk):
    """stream: true speaks OpenAI's chunk format: text token by token, a tool call whole in one chunk, usage last."""
    from conftest import tool_call

    chunks = list(sdk.chat.completions.create(model="default", stream=True, messages=[{"role": "user", "content": "hi"}]))
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert chunks[-1].usage is not None and not chunks[-1].choices
    SCRIPT[:] = [tool_call("read_file", {"path": "a.py"}, "c1")]
    tools = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    chunks = list(sdk.chat.completions.create(model="default", stream=True, tools=tools, messages=[{"role": "user", "content": "read a.py"}]))
    calls = [tc for c in chunks if c.choices for tc in (c.choices[0].delta.tool_calls or [])]
    assert calls[0].function.name == "read_file" and json.loads(calls[0].function.arguments) == {"path": "a.py"}
    assert [c.choices[0].finish_reason for c in chunks if c.choices][-1] == "tool_calls"


def test_the_terminal_access_token_opens_the_bridge(client, monkeypatch):
    """setup.sh tells people to use Settings > Terminal access for the terminal; the rooks agent speaks /v1, so it works."""
    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    token = client.post("/api/cli-token").json()["token"]
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/v1/models", headers={"Authorization": "Bearer not-the-token"}).status_code == 401


def test_browse_tool_runs_the_browser_on_the_conversations_model(client, fake_url, monkeypatch):
    """R1 in the stack: the browser service is told to use Workbench (provider workbench) with this chat's model."""
    from conftest import BROWSER_JOBS, events, tool_call

    from app import config

    monkeypatch.setattr(config, "BROWSER_URL", fake_url.removesuffix("/v1"))
    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    BROWSER_JOBS.clear()
    SCRIPT[:] = [tool_call("browse_web", {"goal": "find the opening hours", "max_steps": 5}), {"role": "assistant", "content": "Open 9 to 5."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "check the hours", "model": f"{client.pid}/fake-model"}))
    held = next(e for e in ev if e["type"] == "approval_required")
    assert held["tool"] == "browse_web" and "browser" in held["reason"].lower()
    assert BROWSER_JOBS == []
    ok = client.post(f"/api/approvals/{held['id']}", json={"approve": True}).json()
    assert ok["status"] == "done"
    assert BROWSER_JOBS == [{"goal": "find the opening hours", "max_steps": 5, "provider": "workbench", "model": f"{client.pid}/fake-model"}]


def test_looking_at_a_site_asks_to_open_the_browser(client):
    """'Go to Xero' asks to open the browser. It does not tell the user to add a work site."""
    from conftest import events

    from app import assistant

    assert assistant.wants_a_look("go to xero see how many invoices are left for payment")
    assert assistant.wants_a_look("go search on google servicem8 for me")
    assert assistant.wants_a_look("open the browser and see how many orders we have")
    assert not assistant.wants_a_look("Catch me up on my mail")
    ev = events(client.post("/api/assistant/chat", json={"message": "go to xero see how many invoices are left for payment"}))
    held = next(e for e in ev if e["type"] == "approval_required")
    assert held["tool"] == "browse_web" and "browser" in held["reason"].lower()
    said = " ".join(e.get("content") or "" for e in ev if e["type"] == "message")
    assert "work site" not in said.lower() and "browser" in said.lower()


def test_rooks_code_gets_a_local_coder_model_when_ollama_only(monkeypatch):
    from app import llm

    monkeypatch.setattr(llm, "default_model_ref", lambda: "1/qwen2.5:7b")
    monkeypatch.setattr(llm, "_connection", lambda cid: {"id": cid, "auth_kind": "local_ollama"})
    monkeypatch.setattr(llm, "list_models_for", lambda cid, fresh=False: [
        {"ref": "1/qwen2.5:7b", "model": "qwen2.5:7b"}, {"ref": "1/qwen2.5-coder:7b", "model": "qwen2.5-coder:7b"}])
    assert llm.local_coder_ref() == "1/qwen2.5-coder:7b"
    monkeypatch.setattr(llm, "default_model_ref", lambda: "auto")
    assert llm.local_coder_ref() is None
    monkeypatch.setattr(llm, "default_model_ref", lambda: "1/qwen2.5:7b")
    monkeypatch.setattr(llm, "_connection", lambda cid: {"id": cid, "auth_kind": "api_key"})
    assert llm.local_coder_ref() is None
