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


def test_models_and_default(client, sdk):
    ids = [m.id for m in sdk.models.list().data]
    assert f"{client.pid}/fake-model" in ids
    SCRIPT[:] = [{"role": "assistant", "content": "plain answer"}]
    assert sdk.chat.completions.create(model="default", messages=[{"role": "user", "content": "hi"}]).choices[0].message.content == "plain answer"


def test_streaming_is_refused_plainly(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    r = client.post("/v1/chat/completions", json={"model": "default", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {BRIDGE_KEY}"})
    assert r.status_code == 400 and "Streaming" in r.json()["detail"]


def test_browse_tool_runs_the_browser_on_the_conversations_model(client, fake_url, monkeypatch):
    """R1 in the stack: the browser service is told to use Workbench (provider workbench) with this chat's model."""
    from conftest import BROWSER_JOBS, events, tool_call

    from app import config

    monkeypatch.setattr(config, "BROWSER_URL", fake_url.removesuffix("/v1"))
    monkeypatch.setattr(config, "BROWSER_API_KEY", BRIDGE_KEY)
    BROWSER_JOBS.clear()
    SCRIPT[:] = [tool_call("browse_web", {"goal": "find the opening hours", "max_steps": 5}), {"role": "assistant", "content": "Open 9 to 5."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "check the hours", "model": f"{client.pid}/fake-model"}))
    assert next(e for e in ev if e["type"] == "tool_result")["ok"]
    assert BROWSER_JOBS == [{"goal": "find the opening hours", "max_steps": 5, "provider": "workbench", "model": f"{client.pid}/fake-model"}]
