"""R1 contract: the same conversation, run through the agent loop, behaves the same on every backend.

One parametrized suite, five auth kinds, every protocol that each may speak. The fake endpoints (conftest) reject
requests that break each provider's documented wire format, so a pass means the adapter speaks it correctly.
"""

import json

import pytest
from conftest import CONTRACT_KEY, NATIVE_CALLS, OAUTH_TOKEN, SCRIPT, events, tool_call

BACKENDS = [  # (auth_kind, protocol, path under the fake server, secret)
    ("api_key", "chat_completions", "/v1", CONTRACT_KEY),
    ("api_key", "responses", "/v1", CONTRACT_KEY),
    ("api_key", "anthropic_messages", "/v1", CONTRACT_KEY),
    ("openai_compatible", "chat_completions", "/v1", ""),
    ("local_ollama", "ollama_native", "", ""),
    ("oauth_subscription", "responses", "/v1", OAUTH_TOKEN),
]
IDS = [f"{a}+{p}" for a, p, _, _ in BACKENDS]


def _connect(client, fake_url, auth_kind, protocol, path, secret) -> int:
    """API for normal kinds. Subscription connections only come from sign-in (item 101), so the test writes the token."""
    from app import db, llm
    from app.security import encrypt

    base = fake_url.removesuffix("/v1") + path
    if auth_kind == "oauth_subscription":
        tokens = json.dumps({"access_token": secret, "expires_at": 4102444800})  # 2100-01-01
        cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, secret_enc, models, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     "ChatGPT plan", auth_kind, protocol, base, encrypt(tokens), "gpt-plan", db.now())
    else:
        r = client.post("/api/providers", json={"name": f"{auth_kind} {protocol}", "auth_kind": auth_kind, "protocol": protocol,
                                                "base_url": base, "api_key": secret})
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
    llm._model_cache.pop(cid, None)
    return cid


@pytest.fixture(params=BACKENDS, ids=IDS)
def backend(request, client, fake_url):
    from app import db

    auth_kind, protocol, path, secret = request.param
    cid = _connect(client, fake_url, auth_kind, protocol, path, secret)
    yield {"id": cid, "auth_kind": auth_kind, "protocol": protocol}
    db.run("DELETE FROM connections WHERE id = ?", cid)


def test_lists_models(client, backend):
    models = [m["model"] for m in client.get("/api/models", params={"fresh": True}).json()["models"] if m["provider_id"] == backend["id"]]
    expected = {"local_ollama": ["llama3.2:3b", "qwen3:4b"], "oauth_subscription": ["fake-model", "gpt-plan"]}.get(backend["auth_kind"], ["fake-model"])
    assert models == expected


def test_agent_loop_runs_a_tool_and_answers(client, backend):
    from app import db

    NATIVE_CALLS.clear()
    SCRIPT[:] = [tool_call("create_task", {"title": f"Contract {backend['protocol']} {backend['id']}"}, "call1"),
                 {"role": "assistant", "content": "Task created successfully!"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add the contract task", "model": f"{backend['id']}/m"}))
    assert not [e for e in ev if e.get("type") == "error"], ev
    result = next(e for e in ev if e["type"] == "tool_result")
    assert result["ok"] and result["receipt"]["verified"] is True
    assert next(e for e in ev if e["type"] == "message")["content"] == "Task created successfully!"
    # the second request carried the tool's result back in this protocol's own shape
    if backend["protocol"] != "chat_completions":
        body = NATIVE_CALLS[-1]["body"]
        assert "Contract" in json.dumps(body)
        # the system prompt travels where each protocol defines it, never as user text
        system = {"anthropic_messages": lambda: body.get("system", ""), "responses": lambda: body.get("instructions", ""),
                  "ollama_native": lambda: body["messages"][0]["content"] if body["messages"][0]["role"] == "system" else ""}[backend["protocol"]]()
        assert "personal assistant" in system
        assert "personal assistant" not in json.dumps(body.get("messages") if backend["protocol"] != "ollama_native" else body["messages"][1:])
        assert "personal assistant" not in json.dumps(body.get("input", []))
    rows = db.all("SELECT auth_kind, protocol, outcome FROM actions WHERE kind = 'model_call' ORDER BY id DESC LIMIT 2")
    assert rows == [{"auth_kind": backend["auth_kind"], "protocol": backend["protocol"], "outcome": "ok"}] * 2


def test_streams_and_records_usage(client, backend):
    from app import db

    out = client.post("/api/ai/assist", json={"action": "fix", "text": "teh", "model": f"{backend['id']}/m"}).text
    chunks = [json.loads(line[5:]) for line in out.splitlines() if line.startswith("data:")]
    assert "".join(c.get("delta", "") for c in chunks) == "streamed" and chunks[-1].get("done")
    row = db.one("SELECT * FROM actions WHERE kind = 'model_call' AND channel = 'assist' ORDER BY id DESC")
    assert row["outcome"] == "ok" and row["tokens_in"] > 0 and row["tokens_out"] > 0 and row["protocol"] == backend["protocol"]


def test_provider_errors_are_surfaced_and_recorded(client, backend):
    from app import db

    ev = events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{backend['id']}/fail-model"}))
    err = next(e for e in ev if e.get("type") == "error")["error"]
    assert "500" in err and "upstream exploded" in err
    row = db.one("SELECT * FROM actions WHERE kind = 'model_call' AND model = ? ORDER BY id DESC", f"{backend['id']}/fail-model")
    assert row["outcome"] == "error" and "upstream exploded" in row["failure_reason"]


def test_an_expired_subscription_asks_for_sign_in(client, fake_url):
    from app import db
    from app.security import encrypt

    cid = _connect(client, fake_url, "oauth_subscription", "responses", "/v1", OAUTH_TOKEN)
    db.run("UPDATE connections SET secret_enc = ? WHERE id = ?", encrypt(json.dumps({"access_token": OAUTH_TOKEN, "expires_at": 1})), cid)
    ev = events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{cid}/gpt-plan"}))
    assert "sign in again" in next(e for e in ev if e.get("type") == "error")["error"]
    assert db.one("SELECT state FROM connections WHERE id = ?", cid)["state"] == "needs_auth"
    db.run("DELETE FROM connections WHERE id = ?", cid)


def test_subscription_connections_cannot_be_pasted_in(client):
    r = client.post("/api/providers", json={"name": "x", "auth_kind": "oauth_subscription", "protocol": "responses",
                                            "base_url": "https://api.openai.com/v1", "api_key": "copied-token"})
    assert r.status_code == 400 and "signing in" in r.json()["detail"]


def test_session_scraping_is_refused_everywhere_with_r5(client, fake_url):
    """D7: refusal-only backend; the user-visible error cites R5."""
    from app import db

    r = client.post("/api/providers", json={"name": "x", "auth_kind": "session_scraping_forbidden", "protocol": "chat_completions",
                                            "base_url": fake_url, "api_key": "cookie"})
    assert r.status_code == 400 and "R5" in r.json()["detail"]
    # even if such a row existed (hand-edited database), every use refuses and the model list survives
    cid = db.run("INSERT INTO connections(name, auth_kind, protocol, base_url, created_at) VALUES ('scraped', 'session_scraping_forbidden', "
                 "'chat_completions', ?, ?)", fake_url, db.now())
    ev = events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{cid}/any"}))
    assert "R5" in next(e for e in ev if e.get("type") == "error")["error"]
    models = client.get("/api/models", params={"fresh": True})
    assert models.status_code == 200 and not [m for m in models.json()["models"] if m["provider_id"] == cid]
    db.run("DELETE FROM connections WHERE id = ?", cid)


@pytest.mark.parametrize("pair", [("local_ollama", "chat_completions"), ("openai_compatible", "anthropic_messages"),
                                  ("oauth_subscription", "chat_completions"), ("api_key", "ollama_native"), ("nonsense", "responses")])
def test_impossible_pairs_are_rejected(client, pair):
    r = client.post("/api/providers", json={"name": "x", "auth_kind": pair[0], "protocol": pair[1], "base_url": "https://a.example/v1"})
    assert r.status_code == 400


def test_legacy_kind_still_works(client, fake_url):
    from app import db

    r = client.post("/api/providers", json={"name": "Old client", "kind": "ollama", "base_url": fake_url.removesuffix("/v1")})
    assert r.status_code == 200 and (r.json()["auth_kind"], r.json()["protocol"]) == ("local_ollama", "ollama_native")
    db.run("DELETE FROM connections WHERE id = ?", r.json()["id"])


def test_nothing_above_the_adapter_layer_branches_on_the_backend():
    """R1, checked in the source: only the adapter layer and the connection API/migration may look at auth_kind or protocol."""
    import pathlib
    import re

    # api and siwc: create/validate connections; db: migration; llm: records them; router: ranks connections by how they
    # are paid for (plan sign-in first, local last). None of them speak a wire protocol.
    allowed = {"adapters.py", "api.py", "db.py", "llm.py", "router.py", "siwc.py"}
    offenders = [f"{f.name}:{n}: {line.strip()}"
                 for f in sorted(pathlib.Path(__file__).resolve().parent.parent.joinpath("app").glob("*.py")) if f.name not in allowed
                 for n, line in enumerate(f.read_text().splitlines(), 1) if re.search(r"\bauth_kind\b|\bprotocol\b", line)]
    assert not offenders, offenders
