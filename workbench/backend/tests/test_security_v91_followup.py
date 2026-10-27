"""Follow-up to the v9.1 hardening: you still see what you approve, notices that can leave ROOKS carry no typed
content, the bundled Ollama counts as local and free, clients cannot fake their address, and ROOKS explains how to
connect Computer Use instead of claiming it cannot exist."""

import json
from pathlib import Path

from conftest import SCRIPT, tool_call
from test_computer import FakeCompanion, chat, paired  # noqa: F401  (fixture)

SECRET_TEXT = "Dear Sam, the gate code is 4471"


def test_you_see_what_you_approve_but_it_is_encrypted_at_rest(client, paired):  # noqa: F811
    from app import db

    client.put("/api/computer/settings", json={"mode": "ask"})
    with FakeCompanion(client, paired) as comp:
        SCRIPT[:] = [tool_call("computer_type", {"text": SECRET_TEXT, "app": "Notes"}, "t"), {"role": "assistant", "content": "Waiting."}]
        ev, _ = chat(client, "type the note")
        ap = next(e for e in ev if e["type"] == "approval")
        assert SECRET_TEXT in ap["args"]  # the live approval card shows what will be typed
        pending = next(a for a in client.get("/api/approvals").json() if a["id"] == ap["id"])
        assert SECRET_TEXT in pending["args"]
        assert SECRET_TEXT in json.dumps(client.get("/api/computer").json()["approvals"])
        raw = db.one("SELECT args FROM approvals WHERE id = ?", ap["id"])["args"]
        assert raw.startswith("enc:") and SECRET_TEXT not in raw  # never in plain text on disk
        assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).json()["status"] == "approved"
        assert comp.seen[-1]["args"]["text"] == SECRET_TEXT
    decided = next(a for a in client.get("/api/approvals", params={"status": "all"}).json() if a["id"] == ap["id"])
    assert decided["args"] == "{}"  # once decided, the content is no longer shown


def test_notices_that_can_leave_rooks_carry_no_typed_text(client):
    from app import assistant, db

    cid = assistant.new_conversation("mission")
    ctx = assistant.Ctx(channel="mission", conversation_id=cid)
    assistant.request_approval("computer_type", json.dumps({"text": SECRET_TEXT, "app": "Notes"}), ctx, "Typing needs approval.")
    body = db.one("SELECT body FROM inbox ORDER BY id DESC LIMIT 1")["body"]
    assert SECRET_TEXT not in body and "app=Notes" in body and f"text of {len(SECRET_TEXT)} characters" in body


def test_bundled_ollama_is_local_and_free():
    from app import llm

    assert llm._local_endpoint("http://ollama:11434/v1")  # the Docker Compose service setup.sh configures
    assert llm.is_free({"kind": "ollama", "base_url": "http://ollama:11434/v1"}, "qwen3:8b")
    assert llm._local_endpoint("http://10.0.0.5:11434/v1") and llm._local_endpoint("http://host.docker.internal:11434/v1")
    assert not llm._local_endpoint("https://ollama.example.com/v1")
    assert not llm.is_free({"kind": "ollama", "base_url": "https://ollama.example.com/v1"}, "qwen3:8b")


def test_clients_cannot_fake_their_address():
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert '"--forwarded-allow-ips", "*"' not in dockerfile and '"--forwarded-allow-ips", "127.0.0.1"' in dockerfile


def test_without_a_companion_rooks_explains_how_to_connect_one(client):
    from app import assistant

    cid = assistant.new_conversation("web")
    prompt = assistant.system_prompt(assistant.Ctx(channel="web", conversation_id=cid), "open my terminal")
    assert "no shell or terminal access" in prompt and "Computer page" in prompt
