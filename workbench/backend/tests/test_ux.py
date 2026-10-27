"""Getting-started checklist reports real state; model test only says ok after a real answer."""

from conftest import SCRIPT


def test_getting_started_reflects_real_data(client):
    before = client.get("/api/getting-started").json()
    assert before["providers"] >= 1 and isinstance(before["search_provider"], str)
    client.post("/api/tasks", json={"title": "Checklist task"})
    client.post("/api/docs", json={"kind": "note", "title": "Checklist note", "body": "x"})
    after = client.get("/api/getting-started").json()
    assert after["tasks"] == before["tasks"] + 1 and after["notes"] == before["notes"] + 1
    assert client.put("/api/getting-started", json={"dismissed": True}).json()["dismissed"] is True
    assert client.get("/api/getting-started").json()["dismissed"] is True
    client.put("/api/getting-started", json={"dismissed": False})


def test_model_test_reports_real_outcome(client):
    SCRIPT[:] = [{"role": "assistant", "content": "OK"}]
    r = client.post("/api/models/test", json={"model": f"{client.pid}/fake-model"}).json()
    assert r["ok"] is True and r["reply"] == "OK" and r["ms"] >= 0
    bad = client.post("/api/models/test", json={"model": "999/nope"}).json()
    assert bad["ok"] is False and "not found" in bad["error"]


def test_new_endpoints_need_sign_in(client):
    from fastapi.testclient import TestClient

    from app import main

    anon = TestClient(main.app)
    assert anon.get("/api/getting-started").status_code == 401
    assert anon.post("/api/models/test", json={}, headers={"X-Workbench": "1"}).status_code == 401
    assert anon.post("/api/models/test", json={}).status_code == 403  # CSRF header still required


def test_appearance_is_stored_per_workspace(client):
    assert client.get("/api/appearance").json() == {}
    body = {"theme": "forest", "font": "literata", "size": 16, "custom": [{"id": "c1", "name": "Mine", "mode": "dark", "bg": "#101010", "panel": "#181818", "text": "#eeeeee", "accent": "#ff8800"}]}
    assert client.put("/api/appearance", json=body).json()["theme"] == "forest"
    assert client.get("/api/appearance").json()["custom"][0]["accent"] == "#ff8800"
    assert client.put("/api/appearance", content=b"[1, 2]", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.put("/api/appearance", content=b"x" * 40000, headers={"Content-Type": "application/json"}).status_code == 413
