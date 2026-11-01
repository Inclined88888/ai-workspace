"""Image generation through connected providers, saved to Files and logged as model calls (Phase 4, media)."""

import base64
import json

from fastapi import Request

import conftest
from conftest import SCRIPT, tool_call

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SEEN: list[dict] = []


@conftest.fake.post("/v1/images/generations")
async def fake_images(request: Request):
    body = await request.json()
    SEEN.append({"body": body, "auth": request.headers.get("authorization")})
    if body["prompt"] == "fail please":
        from fastapi.responses import JSONResponse

        return JSONResponse({"error": {"message": "content policy sk-secret-123456789012345678901234"}}, status_code=400)
    return {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}


def _conn(client, fake_url, name="Images"):
    r = client.post("/api/providers", json={"name": name, "kind": "openai", "base_url": fake_url, "api_key": "img-key"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_needs_a_capable_provider_then_makes_and_saves_an_image(client, fake_url):
    from app import db

    cid = _conn(client, fake_url)
    assert all(p["connection_id"] != cid for p in client.get("/api/media/images/providers").json()["providers"])
    assert client.post("/api/media/images", json={"prompt": "a cat", "connection_id": cid}).status_code == 400

    client.put("/api/media/images/model", json={"connection_id": cid, "model": "flux-dev"})
    assert any(p["connection_id"] == cid and p["model"] == "flux-dev" for p in client.get("/api/media/images/providers").json()["providers"])
    SEEN.clear()
    r = client.post("/api/media/images", json={"prompt": "A cat in a hat!", "size": "portrait", "connection_id": cid})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "a-cat-in-a-hat.png" and out["model"] == "flux-dev" and out["size_bytes"] == len(PNG)
    assert SEEN[-1]["body"] == {"model": "flux-dev", "prompt": "A cat in a hat!", "n": 1, "size": "1024x1536", "response_format": "b64_json"}
    assert SEEN[-1]["auth"] == "Bearer img-key"
    f = db.one("SELECT * FROM work_files WHERE id = ?", out["file_id"])
    assert f["source"] == "output" and open(f["path"], "rb").read() == PNG
    call = db.one("SELECT * FROM actions WHERE kind = 'model_call' ORDER BY id DESC LIMIT 1")
    assert call["outcome"] == "ok" and call["model"] == f"{cid}/flux-dev" and call["channel"] == "media"

    assert client.post("/api/media/images", json={"prompt": "x", "size": "huge", "connection_id": cid}).status_code == 422


def test_provider_errors_are_redacted_and_logged(client, fake_url):
    from app import db

    cid = _conn(client, fake_url, "Images2")
    client.put("/api/media/images/model", json={"connection_id": cid, "model": "m"})
    r = client.post("/api/media/images", json={"prompt": "fail please", "connection_id": cid})
    assert r.status_code == 502 and "sk-secret-1234567890" not in r.text
    call = db.one("SELECT * FROM actions WHERE kind = 'model_call' ORDER BY id DESC LIMIT 1")
    assert call["outcome"] == "error" and "sk-secret-1234567890" not in call["failure_reason"]


def test_openrouter_style_chat_images(client, fake_url, monkeypatch):
    from app import media

    cid = _conn(client, fake_url, "OR-like")
    monkeypatch.setitem(media.KNOWN, "127.0.0.1", ("chat", "img-chat-model", False))
    SCRIPT[:] = [{"role": "assistant", "content": "", "images": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(PNG).decode()}}]}]
    r = client.post("/api/media/images", json={"prompt": "sunset", "connection_id": cid})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "img-chat-model" and r.json()["size_bytes"] == len(PNG)
    assert conftest.CALLS[-1]["modalities"] == ["image", "text"]


def test_the_assistant_can_make_an_image(client, fake_url):
    from app import db

    cid = _conn(client, fake_url, "Images3")
    client.put("/api/media/images/model", json={"connection_id": cid, "model": "flux"})
    before = db.one("SELECT COUNT(*) AS n FROM work_files")["n"]
    SCRIPT[:] = [tool_call("generate_image", {"prompt": "a lighthouse", "size": "landscape"}), {"role": "assistant", "content": "Saved it to Files."}]
    events = [json.loads(line[5:]) for line in client.post("/api/assistant/chat", json={"message": "draw a lighthouse"}).text.splitlines() if line.startswith("data:")]
    res = next(e for e in events if e.get("type") == "tool_result" and e["name"] == "generate_image")
    assert res["ok"], res
    assert db.one("SELECT COUNT(*) AS n FROM work_files")["n"] == before + 1
